"""The properties of the fields of a block model: the list with «устарел» and «Где используется», the edit of the
unit, the description and the role, the deletion, «Перестроить» of a field (подшаг 17.29; ТЗ 8.7.2, 8.7.5; ADR 0023
§2, §7, §9; docs/progress/e17-api.md §4, §7).

A field of the calculator, the density and the categories is «устарел» when its inputs change or go (a field written
again, a filter or a wireframe deleted); the list tells where a field is used (the fields made from it, the project view
and the saved views: the colouring, the quick restrictor, a filter of the library); the required field (the volume) and
a used one are not deleted (422); a rename is refused with a reason; «Перестроить» makes the field again from
`source.params` (409 `source_lost` when an input is gone), the same on PostgreSQL (`-m db`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import fields_ops as fo
from bf_core.blocks import fields_store as fst
from bf_core.blocks.model import Model
from bf_core.db import dialect, migrate
from bf_core.db.schema import blob_owners, objects, settings
from tests.dbutil import make_engine
from tests.test_blocks_fields_store import make_model, sample
from tests.test_blocks_fill_jobs import P, body
from tests.test_blocks_ops_api import API, BL, done, opened, refused, setup_core

AU = sample()[1]["AU"]


@pytest.fixture()
def oc(pclient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    return setup_core(pclient, monkeypatch, "Поля")


def fields_of(c: Any, mid: int | None = None) -> dict[str, dict[str, Any]]:
    r = c.get(f"{BL}/{mid or c.mid}/fields")
    assert r.status_code == 200, r.text
    return {f["name"]: f for f in r.json()["fields"]}


def version(c: Any) -> int:
    return opened(c, c.mid).version


def calc(c: Any, expr: str, field: str, **kw: Any) -> dict[str, Any]:
    r = c.post(f"{BL}/{c.mid}/calc", json={"expected_version": version(c), "expr": expr, "field": field, **kw})
    return done(c, r)


def write_again(c: Any, name: str, values: np.ndarray) -> None:
    m = opened(c, c.mid)
    with fst.FieldsWriter(c.p.store, m, m.version) as w:  # type: ignore[arg-type]
        w.add(name, "f8", replace=True)
        w.write({name: values})
        staged = w.finish()
    with dialect.write_tx(c.p.engine) as conn:
        fst.commit_fields(conn, c.p.store, staged, source={"op": "calc", "params": {"expr": name}})


def patch(c: Any, field: str, **kw: Any) -> Any:
    return c.patch(f"{BL}/{c.mid}/fields/{field}", json={"expected_version": version(c), **kw})


def cells_sha(c: Any) -> str:
    q = sa.select(blob_owners.c.sha256).where(blob_owners.c.owner_id == c.mid, blob_owners.c.role == "cells")
    with dialect.read_tx(c.p.engine) as conn:
        return str(conn.execute(q).scalar_one())


# --- the list ---------------------------------------------------------------------------------------------------------


def test_the_list_of_the_fields_with_the_makers_and_stale(oc) -> None:  # type: ignore[no-untyped-def]
    f = fields_of(oc)
    assert list(f) == ["AU", "CU", "N", "DOM"] and not any(x["stale"] or x["rebuildable"] for x in f.values())
    assert f["AU"]["source"] is None and f["AU"]["deletable"] and f["AU"]["usage"] == [] and not f["AU"]["required"]
    assert f["DOM"]["labels_count"] == 3 and f["DOM"]["kind"] == "category" and f["AU"]["stats"]["count"] > 0
    calc(oc, "AU * 2", "AU2", unit="г/т", filter_id=None)
    done(oc, oc.post(f"{BL}/{oc.mid}/density", json={"expected_version": version(oc), "method": "field", "field": "CU",
                                                       "default": 2.5}))  # fmt: skip
    done(oc, oc.post(f"{BL}/{oc.mid}/classify", json={"expected_version": version(oc), "out": "K",
                                                        "rules": [{"label": "A", "expr": "N > 20"}]}))  # fmt: skip
    r = oc.get(f"{BL}/{oc.mid}/fields")
    assert r.json()["stale"] is False and r.json()["stale_reason"] is None and r.json()["version"] == 4
    f = fields_of(oc)
    for name, op in (("AU2", "calc"), ("DENS", "density"), ("DENS_SRC", "density"), ("K", "classify")):
        assert f[name]["source"]["op"] == op and f[name]["rebuildable"] and not f[name]["stale"], name
    assert f["AU2"]["source"]["params"]["expr"] == "AU * 2" and f["AU2"]["unit"] == "г/т"
    # who reads what: AU, CU and N are in use now, DOM is free
    assert [u["name"] for u in f["AU"]["usage"]] == ["AU2"] and f["AU"]["usage"][0]["role"] == "source"
    assert [u["name"] for u in f["CU"]["usage"]] == ["DENS", "DENS_SRC"] and not f["CU"]["deletable"]
    assert [u["name"] for u in f["N"]["usage"]] == ["K"] and f["DOM"]["deletable"] and f["AU2"]["deletable"]
    # a field written again: everything that read it is «устарел»
    write_again(oc, "CU", np.where(np.isnan(AU), np.nan, 0.5))
    f = fields_of(oc)
    assert (f["DENS"]["stale"], f["DENS_SRC"]["stale"], f["K"]["stale"], f["AU2"]["stale"]) == (
        True,
        True,
        False,
        False,
    )
    assert f["DENS"]["stale_reason"] and f["AU2"]["stale_reason"] is None
    write_again(oc, "AU", AU + 1.0)
    f = fields_of(oc)
    assert f["AU2"]["stale"] and "Изменились" in f["AU2"]["stale_reason"]
    refused(oc.get(f"{BL}/777777/fields"), 404, "block_model_not_found")


def test_a_field_of_a_fill_is_stale_with_the_model(oc) -> None:  # type: ignore[no-untyped-def]
    from bf_core.blocks.fill import jobs as fj
    from tests.test_blocks_fill_jobs import params, prototype, request
    from tests.test_blocks_ops import ORE

    proto = prototype(oc.p)
    wid = body(oc.p, *ORE, name="Руда")
    mid = int(fj.run_fill(oc.p.engine, oc.p.folder, params(request(proto, wid)))["model_id"])
    f = fields_of(oc, mid)
    assert not f["CODE"]["stale"] and f["CODE"]["source"]["op"] == "fill" and not f["CODE"]["rebuildable"]
    assert f["FRAC"]["required"] and not f["FRAC"]["deletable"]
    with dialect.write_tx(oc.p.engine) as conn:
        conn.execute(sa.delete(objects).where(objects.c.id == wid))
    r = oc.get(f"{BL}/{mid}/fields").json()
    assert r["stale"] and r["stale_reason"] == "удалён каркас «Руда»"
    f = {x["name"]: x for x in r["fields"]}
    assert f["CODE"]["stale"] and f["CODE"]["stale_reason"] == "удалён каркас «Руда»" and f["FRAC"]["stale"]


# --- the edit ---------------------------------------------------------------------------------------------------------


def test_the_unit_the_description_and_the_role_are_edited(oc) -> None:  # type: ignore[no-untyped-def]
    before = cells_sha(oc)
    r = patch(oc, "AU", unit="  г/т  ", description=" Золото,  по пробам ", role=None)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["version"] == 2 and out["field"]["unit"] == "г/т" and out["field"]["role"] is None
    assert out["field"]["description"] == "Золото, по пробам"
    m = opened(oc, oc.mid)
    assert (m.field("AU")["role"], m.field("AU")["unit"], m.version) == (None, "г/т", 2)
    assert cells_sha(oc) == before  # the files do not change: the fields stay actual
    assert patch(oc, "au", unit=None, description="").json()["field"]["unit"] is None  # null and "" clear
    assert opened(oc, oc.mid).field("AU")["description"] is None
    # the roles inside a kind
    assert patch(oc, "CU", role="frac").json()["field"]["role"] == "frac"  # the values are in 0…1
    assert patch(oc, "DOM", role="class").json()["field"]["role"] == "class"
    assert patch(oc, "DOM", role="code").json()["field"]["role"] == "code"
    assert patch(oc, "CU", role="grade").json()["field"]["role"] == "grade"
    assert patch(oc, "AU", role="density").json()["field"]["role"] == "density"  # the values are above 0
    assert fields_of(oc)["AU"]["required"] is False and oc.get(f"{BL}/{oc.mid}").json()["version"] == version(oc)


def test_the_edit_refusals(oc) -> None:  # type: ignore[no-untyped-def]
    v = version(oc)
    cases = [
        (patch(oc, "AU", role="code"), 422, "role_invalid", "fields.0.role"),
        (patch(oc, "DOM", role="grade"), 422, "role_invalid", "fields.0.role"),
        (patch(oc, "AU", role="mine"), 422, "role_invalid", "fields.0.role"),
        (patch(oc, "AU", role="frac"), 422, "role_invalid", "fields.0.role"),  # the values go over 1
        (patch(oc, "N", role="density"), 422, "role_invalid", "fields.0.role"),  # a zero is among them
        (patch(oc, "AU", unit="x" * 41), 422, "bad_param", "fields.0.unit"),
        (patch(oc, "AU", description="x" * 501), 422, "bad_param", "fields.0.description"),
        (patch(oc, "AU", unit=5), 422, "bad_param", "fields.0.unit"),
        (patch(oc, "AU"), 422, "bad_param", "fields"),
        (patch(oc, "NOPE", unit="x"), 404, "block_field_not_found", "fields.0.name"),
        (oc.patch(f"{BL}/{oc.mid}/fields/AU", json={"unit": "x"}), 422, "bad_param", "expected_version"),
        (oc.patch(f"{BL}/{oc.mid}/fields/AU", json={"expected_version": v + 3, "unit": "x"}), 409,
         "version_conflict", None),
        (oc.patch(f"{BL}/777777/fields/AU", json={"expected_version": 1, "unit": "x"}), 404,
         "block_model_not_found", None),
        (oc.patch(f"{BL}/{oc.mid}/fields/AU", json=[1]), 422, "bad_request", None),
    ]  # fmt: skip
    for r, status, code, param in cases:
        refused(r, status, code, param)
    err = refused(patch(oc, "AU", name="Gold"), 422, "field_rename_unsupported", "fields.0.name")
    assert err["field"] == "AU" and "калькулятором" in err["hint"]
    assert patch(oc, "AU", name="AU", unit="г/т").status_code == 200  # the same name is not a rename
    assert patch(oc, "CU", role="frac").status_code == 200
    err = refused(patch(oc, "AU", role="frac"), 422, "role_invalid")  # the values of AU are not shares
    done_ = calc(oc, "IF(AU > 1, 1, 0.5)", "SH")
    assert done_["field"] == "SH"
    err = refused(patch(oc, "SH", role="frac"), 422, "frac_exists", "role")
    assert err["field"] == "CU" and opened(oc, oc.mid).field("SH")["role"] is None


# --- the deletion -----------------------------------------------------------------------------------------------------


def delete(c: Any, name: str, v: int | None = None) -> Any:
    return c.delete(f"{BL}/{c.mid}/fields/{name}", params={"expected_version": v or version(c)})


def put_styles(c: Any, styles: dict[str, Any]) -> None:
    with dialect.write_tx(c.p.engine) as conn:
        conn.execute(sa.insert(settings).values(scope="view", key="block_styles", value=styles, version=1))


def test_the_deletion_refuses_a_required_and_a_used_field_and_lists_where(oc) -> None:  # type: ignore[no-untyped-def]
    calc(oc, "AU * 2", "AU2")
    err = refused(delete(oc, "AU"), 422, "field_in_use")
    assert err["field"] == "AU" and err["usage"] == [{"kind": "field", "id": None, "name": "AU2", "role": "source"}]
    assert "«AU2»" in err["message"]
    assert patch(oc, "CU", role="frac").status_code == 200
    refused(delete(oc, "CU"), 422, "field_required")
    # the views: the colouring, the quick restrictor and a filter of the library
    flt = oc.post(f"{API}/filters", json={"name": "Домен", "expr": "DOM = 'руда' AND N > 5"})
    assert flt.status_code == 200, flt.text
    quick = {"items": [{"field": "CU", "op": ">", "value": 1}]}
    put_styles(oc, {str(oc.mid): {"colour": {"field": "n"}, "quick": quick, "filter_id": flt.json()["id"]}})
    now = {u["role"] for u in fields_of(oc)["N"]["usage"]}
    assert now == {"colour", "filter"} and {u["role"] for u in fields_of(oc)["DOM"]["usage"]} == {"filter"}
    err = refused(delete(oc, "DOM"), 422, "field_in_use")
    assert err["usage"] == [{"kind": "view", "id": oc.mid, "name": "Вид проекта · Модель", "role": "filter"}]
    saved = {"keys": {"block_styles": {str(oc.mid): {"colour": {"field": "DOM"}}}}}
    with dialect.write_tx(oc.p.engine) as conn:
        now_ = dialect.utcnow()
        conn.execute(sa.insert(objects).values(kind="view", name="Мой вид", name_key="мой вид", meta=saved,
                                               sort_key=0.0, state="ready", version=1, created_at=now_,
                                               updated_at=now_))  # fmt: skip
    assert {u["name"] for u in fields_of(oc)["DOM"]["usage"]} == {"Вид проекта · Модель", "Мой вид"}
    # nothing is used any more: the deletion goes
    with dialect.write_tx(oc.p.engine) as conn:
        conn.execute(sa.delete(settings).where(settings.c.key == "block_styles"))
        conn.execute(sa.delete(objects).where(objects.c.kind == "view"))
    v = version(oc)
    out = delete(oc, "DOM").json()
    assert out == {"version": v + 1, "deleted": "DOM"} and "DOM" not in fields_of(oc)
    # the field made from AU goes first, then AU itself
    assert delete(oc, "AU2").status_code == 200
    assert delete(oc, "AU").status_code == 200 and list(fields_of(oc)) == ["CU", "N"]


def test_the_deletion_refusals(oc) -> None:  # type: ignore[no-untyped-def]
    v = version(oc)
    refused(delete(oc, "NOPE"), 404, "block_field_not_found")
    refused(delete(oc, "AU", v + 4), 409, "version_conflict")
    refused(oc.delete(f"{BL}/{oc.mid}/fields/AU"), 422, "bad_param", "expected_version")
    refused(oc.delete(f"{BL}/777777/fields/AU", params={"expected_version": 1}), 404, "block_model_not_found")
    assert version(oc) == v and list(fields_of(oc)) == ["AU", "CU", "N", "DOM"]


# --- «Перестроить» of a field -----------------------------------------------------------------------------------------


def test_rebuild_of_the_fields_of_the_calculator_the_density_and_the_categories(oc) -> None:  # type: ignore[no-untyped-def]
    calc(oc, "AU * 2", "AU2", unit="г/т")
    assert patch(oc, "AU2", description="Удвоенное").status_code == 200
    done(oc, oc.post(f"{BL}/{oc.mid}/density", json={"expected_version": version(oc), "method": "field", "field": "CU",
                                                       "default": 2.5, "unit": "г/см³"}))  # fmt: skip
    done(oc, oc.post(f"{BL}/{oc.mid}/classify", json={"expected_version": version(oc), "out": "K", "majority": 1,
                                                        "rules": [{"label": "A", "expr": "AU2 > 2"}]}))  # fmt: skip
    new_au = AU * 3.0
    write_again(oc, "AU", new_au)
    f = fields_of(oc)
    assert f["AU2"]["stale"] and not f["DENS"]["stale"]
    out = done(oc, oc.post(f"{BL}/{oc.mid}/fields/AU2/rebuild", json={"expected_version": version(oc)}))
    assert out["field"] == "AU2" and np.allclose(opened(oc, oc.mid).columns(["AU2"])["AU2"], new_au * 2, equal_nan=True)
    f = fields_of(oc)
    assert not f["AU2"]["stale"] and (f["AU2"]["unit"], f["AU2"]["description"]) == ("г/т", "Удвоенное")
    assert list(f) == ["AU", "CU", "N", "DOM", "AU2", "DENS", "DENS_SRC", "K"]  # a field written again keeps its place
    assert f["K"]["stale"]  # the categories read AU2, which was written again
    out = done(oc, oc.post(f"{BL}/{oc.mid}/fields/K/rebuild", json={"expected_version": version(oc)}))
    assert out["field"] == "K" and not fields_of(oc)["K"]["stale"]
    assert fields_of(oc)["K"]["source"]["params"]["majority"] == 1
    # the density from either of its two fields; the unit it has is kept
    out = done(oc, oc.post(f"{BL}/{oc.mid}/fields/DENS_SRC/rebuild", json={"expected_version": version(oc)}))
    assert out["fields"] == ["DENS", "DENS_SRC"] and fields_of(oc)["DENS"]["unit"] == "г/см³"
    refused(oc.post(f"{BL}/{oc.mid}/fields/N/rebuild", json={"expected_version": version(oc)}), 422, "not_rebuildable")
    refused(oc.post(f"{BL}/{oc.mid}/fields/AU2/rebuild", json={"expected_version": 1}), 409, "version_conflict")
    refused(oc.post(f"{BL}/{oc.mid}/fields/AU2/rebuild", json={}), 422, "bad_param", "expected_version")
    refused(oc.post(f"{BL}/{oc.mid}/fields/NOPE/rebuild", json={"expected_version": version(oc)}), 404,
            "block_field_not_found")  # fmt: skip


def test_rebuild_without_its_inputs_is_source_lost(oc) -> None:  # type: ignore[no-untyped-def]
    flt = oc.post(f"{API}/filters", json={"name": "Сорт", "expr": "N > 5"}).json()["id"]
    calc(oc, "AU * 2", "AU2", filter_id=flt)
    wid = body(oc.p, (0.0, 0.0, 0.0), (1000.0, 1000.0, 1000.0), name="Зона")
    over = [{"wireframe_id": wid, "label": "A"}]
    rules = [{"label": "A", "expr": "AU > 1"}]
    body_ = {"expected_version": version(oc), "out": "K", "rules": rules, "overrides": over}
    done(oc, oc.post(f"{BL}/{oc.mid}/classify", json=body_))
    f = fields_of(oc)
    assert not f["AU2"]["stale"] and not f["K"]["stale"]
    assert oc.delete(f"{API}/filters/{flt}").status_code == 200
    with dialect.write_tx(oc.p.engine) as conn:
        conn.execute(sa.delete(objects).where(objects.c.id == wid))
    f = fields_of(oc)
    assert f["AU2"]["stale"] and "Нет входов" in f["AU2"]["stale_reason"] and f["K"]["stale"]
    assert "Нет входов" in f["K"]["stale_reason"]
    for name in ("AU2", "K"):
        err = refused(oc.post(f"{BL}/{oc.mid}/fields/{name}/rebuild", json={"expected_version": version(oc)}), 409,
                      "source_lost")  # fmt: skip
        assert "Исходных данных больше нет" in err["message"]
    assert tuple(fields_of(oc)) == ("AU", "CU", "N", "DOM", "AU2", "K")  # nothing was written


def test_a_field_name_with_a_slash_is_edited_deleted_and_rebuilt(oc) -> None:  # type: ignore[no-untyped-def]
    from urllib.parse import quote

    name = "Au g/t"
    calc(oc, "AU * 2", name, unit="г/т")
    assert name in fields_of(oc)
    for enc in (quote(name, safe=""), name):  # the client sends %2F; a raw slash must work as well
        r = oc.patch(f"{BL}/{oc.mid}/fields/{enc}", json={"expected_version": version(oc), "unit": "ppm"})
        assert r.status_code == 200, r.text
        assert r.json()["field"]["name"] == name and r.json()["field"]["unit"] == "ppm"
    r = oc.post(f"{BL}/{oc.mid}/fields/{quote(name, safe='')}/rebuild", json={"expected_version": version(oc)})
    assert done(oc, r)["field"] == name
    r = oc.delete(f"{BL}/{oc.mid}/fields/{quote(name, safe='')}", params={"expected_version": version(oc)})
    assert r.status_code == 200 and r.json()["deleted"] == name
    assert name not in fields_of(oc)


def test_a_read_only_project_lists_but_does_not_edit(oc) -> None:  # type: ignore[no-untyped-def]
    calc(oc, "AU * 2", "AU2")
    v = version(oc)
    oc.project.read_only = True
    try:
        assert oc.get(f"{BL}/{oc.mid}/fields").status_code == 200
        refused(
            oc.patch(f"{BL}/{oc.mid}/fields/AU", json={"expected_version": v, "unit": "x"}), 423, "project_read_only"
        )
        refused(oc.delete(f"{BL}/{oc.mid}/fields/AU2", params={"expected_version": v}), 423, "project_read_only")
        refused(oc.post(f"{BL}/{oc.mid}/fields/AU2/rebuild", json={"expected_version": v}), 423, "project_read_only")
    finally:
        oc.project.read_only = False
    assert version(oc) == v


def test_the_functions_without_the_api(tmp_path: Path) -> None:
    engine = make_engine("sqlite", tmp_path)
    migrate.upgrade(engine, "project")
    p = P(engine, tmp_path)
    try:
        mid = make_model(p, p.store)
        with dialect.write_tx(engine) as conn:
            version, changed = fo.edit_fields(
                conn, mid, 1, [{"name": "AU", "unit": "г/т"}, {"name": "cu", "role": "frac"}]
            )
        assert version == 2 and [f["name"] for f in changed] == ["AU", "CU"]
        with dialect.write_tx(engine) as conn:
            with pytest.raises(ApiError) as e:
                fo.edit_fields(conn, mid, 2, [])
            assert e.value.code == "bad_param"
            with pytest.raises(ApiError) as e2:
                fo.edit_fields(conn, mid, 1, [{"name": "AU", "unit": "x"}])
            assert (e2.value.status, e2.value.extra) == (409, {"version": 2})
        with dialect.read_tx(engine) as conn:
            m = Model.open(conn, p.store, mid)
            rows = fo.describe(conn, p.store, m)
        assert (
            [r["name"] for r in rows] == ["AU", "CU", "N", "DOM"] and rows[1]["required"] and not rows[1]["deletable"]
        )
    finally:
        engine.dispose()


@pytest.mark.db
def test_the_properties_on_both_databases(db_url: str, tmp_path: Path) -> None:
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project")
    p = P(engine, tmp_path / "blobs")
    p.store.tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        mid = make_model(p, p.store)
        from bf_core.blocks import calc_job

        out = calc_job.run_calc(engine, p.folder, {"model_id": mid, "expected_version": 1, "expr": "AU * 2",
                                                    "field": "AU2"})  # fmt: skip
        assert out["version"] == 2
        with dialect.write_tx(engine) as conn:
            version, _ = fo.edit_fields(conn, mid, 2, [{"name": "AU2", "unit": "г/т", "description": "Удвоенное"}])
        with dialect.write_tx(engine) as conn:
            with pytest.raises(ApiError) as e:
                fo.remove_field(conn, p.store, mid, version, "AU")
            assert e.value.code == "field_in_use" and e.value.extra["usage"][0]["name"] == "AU2"
        with dialect.write_tx(engine) as conn:
            assert fo.remove_field(conn, p.store, mid, version, "AU2") == version + 1
        with dialect.read_tx(engine) as conn:
            m = Model.open(conn, p.store, mid)
            assert [f["name"] for f in m.fields] == ["AU", "CU", "N", "DOM"]
            assert all(not r["stale"] and r["deletable"] for r in fo.describe(conn, p.store, m))
    finally:
        engine.dispose()
