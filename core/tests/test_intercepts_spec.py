"""Parameters of «Истинная мощность» and «Рудные пересечения» (ТЗ 8.5.4; ADR 0021 §4, §9–10; подшаг 15.9): the ranges
→ 422 `bad_param` {param}, an empty source → 422 `no_orientation`, the simple form of the command, the canonical body,
the hash without ids, `rebind`, 409 `column_exists`."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_tables, objects
from bf_core.drillholes import store as dh
from bf_core.intercepts import spec as sp
from bf_core.projects.service import projects_of

API = "/api/v1"


@pytest.fixture()
def demo(pclient):  # type: ignore[no-untyped-def]
    r = pclient.post(f"{API}/projects/demo")
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    with dialect.read_tx(project.engine) as conn:
        t = conn.execute(sa.select(dh_tables.c.id, dh_tables.c.set_id).where(dh_tables.c.name == "Пробы")).one()
        wf = conn.execute(
            sa.select(objects.c.id).where(objects.c.kind == "wireframe").order_by(objects.c.id)
        ).scalars().all()  # fmt: skip
    return {"engine": project.engine, "set_id": int(t.set_id), "table": str(t.id), "wf": [int(i) for i in wf]}


def parse(d: dict[str, Any], fn: Callable[..., Any], **body: Any) -> Any:
    with dialect.read_tx(d["engine"]) as conn:
        return fn(conn, {"set_id": d["set_id"], "table": d["table"], **body})


def refused(d: dict[str, Any], fn: Callable[..., Any], **body: Any) -> ApiError:
    with pytest.raises(ApiError) as e:
        parse(d, fn, **body)
    assert e.value.message and e.value.hint, e.value.message
    return e.value


GLOBAL = [{"source": "global", "alpha": 30, "dipdir": 120}]


def test_tt_global_defaults_and_canonical_body(demo) -> None:  # type: ignore[no-untyped-def]
    s = parse(demo, sp.parse_tt, orientation=GLOBAL)
    assert (s.eps, s.step, s.angles) == (0.1, 1.0, True)
    assert s.columns() == ["TT", "TT_FLAG", "TT_SRC", "ALPHA", "DIPDIR", "HOLE_THETA", "HOLE_AZ"]
    assert s.body == {"set_id": demo["set_id"], "table": demo["table"], "eps": 0.1, "step": 1.0, "angles": True,
                      "orientation": [{"source": "global", "alpha": 30.0, "dipdir": 120.0}]}  # fmt: skip
    assert s.tables() == [(int(demo["table"]), [])] and s.mesh_ids() == []
    assert parse(demo, sp.parse_tt, orientation=GLOBAL, angles=False).columns() == ["TT", "TT_FLAG", "TT_SRC"]
    assert parse(demo, sp.parse_tt, orientation=[{"source": "global", "alpha": "45,5", "dipdir": 360}]).body[
        "orientation"
    ][0] == {"source": "global", "alpha": 45.5, "dipdir": 0.0}


@pytest.mark.parametrize(
    ("key", "lo", "hi"),
    [("eps", 0.01, 0.5), ("step", 0.1, 10), ("l_min", 0, 100), ("waste_max", 0, 50)],
)
def test_ranges_of_8_5_4_top_level(demo, key: str, lo: float, hi: float) -> None:  # type: ignore[no-untyped-def]
    fn = sp.parse_tt if key in ("eps", "step") else sp.parse_run
    base: dict[str, Any] = {"orientation": GLOBAL} if fn is sp.parse_tt else {"field": "AU", "cutoff": 1}
    for ok in (lo, hi):
        got = parse(demo, fn, **base, **{key: ok})
        assert getattr(got, key) == ok
    for out in (lo - 0.001, hi + 0.001, "много", True):
        e = refused(demo, fn, **base, **{key: out})
        assert (e.status, e.code, e.extra["param"]) == (422, "bad_param", key), e.message
    assert "вне диапазона" in refused(demo, fn, **base, **{key: hi * 2 + 1}).message


@pytest.mark.parametrize(
    ("source", "key", "lo", "hi"),
    [("mesh", "d_max", 0, 1000), ("mesh", "r_n", 0, 200), ("core", "d_core", 0, 1000)],
)
def test_ranges_of_8_5_4_in_a_source(demo, source: str, key: str, lo: float, hi: float) -> None:  # type: ignore[no-untyped-def]
    src: dict[str, Any] = {"source": "mesh", "wireframe_ids": demo["wf"][:1]}
    if source == "core":
        src = {"source": "core", "table": "Пробы", "alpha": "AU", "beta": "CU"}
    for ok in (lo, hi):
        assert parse(demo, sp.parse_tt, orientation=[{**src, key: ok}]).body["orientation"][0][key] == ok
    for out in (lo - 0.5, hi + 0.5):
        e = refused(demo, sp.parse_tt, orientation=[{**src, key: out}])
        assert (e.code, e.extra["param"]) == ("bad_param", f"orientation.0.{key}")


@pytest.mark.parametrize(
    ("alpha", "dipdir", "param"),
    [(-1, 0, "alpha"), (90.5, 0, "alpha"), (30, -0.1, "dipdir"), (30, 361, "dipdir"), (None, 0, "alpha")],
)
def test_angles_out_of_range(demo, alpha: Any, dipdir: Any, param: str) -> None:  # type: ignore[no-untyped-def]
    e = refused(demo, sp.parse_tt, orientation=[{"source": "global", "alpha": alpha, "dipdir": dipdir}])
    assert (e.status, e.code, e.extra["param"]) == (422, "bad_param", f"orientation.0.{param}")
    att = [{"code": 1, "alpha": alpha, "dipdir": dipdir}]
    e = refused(demo, sp.parse_tt, orientation=[{"source": "domain", "field": "AU", "attitudes": att}])
    assert e.extra["param"] == f"orientation.0.attitudes.0.{param}"


def test_empty_sources_are_no_orientation(demo) -> None:  # type: ignore[no-untyped-def]
    cases: list[tuple[Any, str]] = [
        ([], "orientation"),
        ([{"source": "domain", "field": "AU", "attitudes": []}], "orientation.0.attitudes"),
        ([{"source": "domain", "field": "AU", "attitudes": ""}], "orientation.0.attitudes"),
        ([{"source": "mesh", "wireframe_ids": []}], "orientation.0.wireframe_ids"),
        ([{"source": "core", "table": "Пробы", "alpha": "AU"}], "orientation.0.beta"),
        ([{"source": "core", "table": ""}], "orientation.0.table"),
    ]
    for orientation, param in cases:
        e = refused(demo, sp.parse_tt, orientation=orientation)
        assert (e.status, e.code, e.extra["param"]) == (422, "no_orientation", param), (orientation, e.message)
    e = refused(demo, sp.parse_tt)  # no orientation at all: the simple form gives none either
    assert e.code == "no_orientation"
    got = parse(demo, sp.parse_run, field="AU", cutoff=1)  # the run goes without one: TT empty, TT_SRC «нет»
    assert got.orientation == [] and got.body["orientation"] == []


def test_bad_sources(demo) -> None:  # type: ignore[no-untyped-def]
    def param(orientation: Any) -> tuple[int, str, str]:
        e = refused(demo, sp.parse_tt, orientation=orientation)
        return e.status, e.code, str(e.extra.get("param"))

    assert param([{"source": "trend"}]) == (422, "bad_param", "orientation.0.source")
    assert param("global") == (422, "bad_param", "orientation")
    assert param(GLOBAL * 4) == (422, "bad_param", "orientation")
    assert param(GLOBAL * 2) == (422, "bad_param", "orientation.1.source")
    assert param([{"source": "domain", "attitudes": [{"code": 1, "alpha": 1, "dipdir": 1}]}]) == (
        422, "bad_param", "orientation.0.field")  # fmt: skip
    att = [{"code": "1", "alpha": 10, "dipdir": 20}]
    assert param([{"source": "domain", "field": "НЕТ", "attitudes": att}]) == (
        422, "field_not_found", "orientation.0.field")  # fmt: skip
    assert param([{"source": "domain", "field": "AU", "table": "Нет такой", "attitudes": att}]) == (
        404, "table_not_found", "orientation.0.table")  # fmt: skip
    twice = [*att, {"code": 1.0, "alpha": 1, "dipdir": 2}]
    assert param([{"source": "domain", "field": "AU", "attitudes": twice}]) == (
        422, "bad_param", "orientation.0.attitudes.1.code")  # fmt: skip
    assert param([{"source": "domain", "field": "AU", "attitudes": [{"code": "QZ", "alpha": 1, "dipdir": 2}]}]) == (
        422, "bad_param", "orientation.0.attitudes.0.code")  # fmt: skip
    many = [{"code": str(i), "alpha": 1, "dipdir": 1} for i in range(sp.ATTITUDES_MAX + 1)]
    assert param([{"source": "domain", "field": "AU", "attitudes": many}]) == (
        422, "bad_param", "orientation.0.attitudes")  # fmt: skip
    assert param([{"source": "mesh", "wireframe_ids": [999999]}]) == (
        422, "wireframe_not_found", "orientation.0.wireframe_ids")  # fmt: skip
    assert param([{"source": "mesh", "wireframe_ids": list(range(1, sp.MESH_MAX + 2))}]) == (
        422, "bad_param", "orientation.0.wireframe_ids")  # fmt: skip
    assert param([{"source": "core", "table": "Пробы", "alpha": "Номер пробы", "beta": "CU"}]) == (
        422, "bad_param", "orientation.0.alpha")  # fmt: skip
    assert param([{"source": "core", "table": "Пробы", "alpha": "AU", "beta": "CU", "line": "left"}]) == (
        422, "bad_param", "orientation.0.line")  # fmt: skip
    assert param([{"source": "core", "table": "Нет", "alpha": "AU", "beta": "CU"}]) == (
        404, "table_not_found", "orientation.0.table")  # fmt: skip


def test_domain_codes_and_the_simple_form_of_the_command(demo) -> None:  # type: ignore[no-untyped-def]
    s = parse(demo, sp.parse_tt, source="domain", domain="AU", attitudes="1=30/120; 2,5=45/0; 3 = 60 / 360",
              fallback_alpha=10, fallback_dipdir=90)  # fmt: skip
    dom, fb = s.body["orientation"]
    assert dom == {"source": "domain", "table": demo["table"], "field": "AU", "attitudes": [
        {"code": "1", "alpha": 30.0, "dipdir": 120.0}, {"code": "2.5", "alpha": 45.0, "dipdir": 0.0},
        {"code": "3", "alpha": 60.0, "dipdir": 0.0}]}  # fmt: skip
    assert fb == {"source": "global", "alpha": 10.0, "dipdir": 90.0}
    assert s.tables() == [(int(demo["table"]), ["AU"])]  # the domain field of the same table
    att = [{"code": " P-1 ", "alpha": 5, "dipdir": 6}]
    text = parse(demo, sp.parse_tt, orientation=[{"source": "domain", "field": "номер пробы", "attitudes": att}])
    assert text.body["orientation"][0]["field"] == "Номер пробы" and text.orientation[0].attitudes[0].code == "P-1"
    e = refused(demo, sp.parse_tt, source="plane", alpha=1, dipdir=1)
    assert (e.code, e.extra["param"]) == ("bad_param", "source")
    m = parse(demo, sp.parse_tt, source="mesh", wireframe_ids=",".join(map(str, demo["wf"][:2])), d_max=5)
    assert m.body["orientation"] == [{"source": "mesh", "wireframe_ids": demo["wf"][:2], "d_max": 5.0, "r_n": 0.0}]
    c = parse(demo, sp.parse_tt, source="core", core_table="Пробы", core_alpha="au", core_beta="CU", core_line="toh")
    assert c.body["orientation"] == [{"source": "core", "table": demo["table"], "alpha": "AU", "beta": "CU",
                                      "line": "toh", "d_core": 10.0}]  # fmt: skip


def test_hash_has_no_ids_and_rebind_maps_them(demo) -> None:  # type: ignore[no-untyped-def]
    orientation = [
        {"source": "mesh", "wireframe_ids": demo["wf"][:2]},
        {"source": "core", "table": "Пробы", "alpha": "AU", "beta": "CU"},
        {"source": "global", "alpha": 30, "dipdir": 120},
    ]
    s = parse(demo, sp.parse_tt, orientation=orientation)
    h = s.hash_params()
    text = repr(h)
    assert "wireframe_ids" not in text and "'table'" not in text and h["orientation"][1]["table_key"] == "пробы"
    assert len(h["orientation"][0]["wireframes"]) == 2 and s.mesh_ids() == demo["wf"][:2]
    assert s.tables() == [(int(demo["table"]), ["AU", "CU"])]  # the core table is the source: read once
    again = sp.rebind(s.body, [77], [901, 902])
    assert again["table"] == "77" and again["orientation"][1]["table"] == "77"
    assert again["orientation"][0]["wireframe_ids"] == [901, 902]
    assert again["orientation"][2] == s.body["orientation"][2]
    assert s.body["table"] == demo["table"]  # not changed in place


def test_run_parameters(demo) -> None:  # type: ignore[no-untyped-def]
    r = parse(demo, sp.parse_run, field="au", cutoff="0,5", include_cutoff=1.5, orientation=GLOBAL)
    assert (r.field, r.unit, r.cutoff, r.include_cutoff) == ("AU", "г/т", 0.5, 1.5)
    assert (r.l_min, r.min_by, r.waste_max, r.missing, r.name) == (2.0, "length", 2.0, "waste", "Рудные пересечения")
    assert r.means == ["AU", "CU"] and r.units == {"AU": "г/т", "CU": "%"} and r.read_fields() == ["AU", "CU"]
    assert r.body == {"set_id": demo["set_id"], "table": demo["table"], "field": "AU", "cutoff": 0.5,
                      "include_cutoff": 1.5, "l_min": 2.0, "min_by": "length", "waste_max": 2.0, "missing": "waste",
                      "density": None, "fields": ["CU"], "orientation": [{"source": "global", "alpha": 30.0,
                      "dipdir": 120.0}], "eps": 0.1, "step": 1.0, "name": "Рудные пересечения"}  # fmt: skip
    r = parse(demo, sp.parse_run, field="AU", cutoff=1, density="CU", fields=[], min_by="tt", missing="break",
              name="Пересечения", replace=True)  # fmt: skip
    assert (r.means, r.density, r.min_by, r.missing, r.name, r.replace) == (["AU"], "CU", "tt", "break", "Пересечения",
                                                                             True)  # fmt: skip
    cases: list[tuple[dict[str, Any], str, str]] = [
        ({"field": "AU"}, "bad_param", "cutoff"),
        ({"field": "", "cutoff": 1}, "bad_param", "field"),
        ({"field": "Номер пробы", "cutoff": 1}, "bad_param", "field"),
        ({"field": "НЕТ", "cutoff": 1}, "field_not_found", "field"),
        ({"field": "AU", "cutoff": 1, "include_cutoff": 1}, "bad_param", "include_cutoff"),
        ({"field": "AU", "cutoff": 1, "min_by": "mass"}, "bad_param", "min_by"),
        ({"field": "AU", "cutoff": 1, "missing": "zero"}, "bad_param", "missing"),
        ({"field": "AU", "cutoff": 1, "density": "Номер пробы"}, "bad_param", "density"),
        ({"field": "AU", "cutoff": 1, "fields": ["Номер пробы"]}, "bad_param", "fields"),
        ({"field": "AU", "cutoff": 1, "name": "пробы"}, "name_is_source", "name"),
        ({"field": "AU", "cutoff": 1, "name": "x" * 201}, "bad_param", "name"),
    ]
    for body, code, param in cases:
        e = refused(demo, sp.parse_run, **body)
        assert (e.status, e.code, e.extra["param"]) == (422, code, param), (body, e.message)
    e = refused(demo, sp.parse_run, table="Нет такой", field="AU", cutoff=1)
    assert (e.status, e.code, e.extra["param"]) == (404, "table_not_found", "table")
    with dialect.read_tx(demo["engine"]) as conn, pytest.raises(ApiError) as e2:
        sp.parse_run(conn, {"table": demo["table"], "field": "AU", "cutoff": 1})
    assert (e2.value.status, e2.value.code) == (404, "set_not_found")


def _noop(_p: float | None, _m: str | None) -> None:
    pass


def test_column_exists_unless_an_earlier_tt_wrote_it(demo) -> None:  # type: ignore[no-untyped-def]
    from bf_core.composites import results as res

    data = dh.TableData(name="Интервалы", kind="interval", holes=["A"], from_m=[0.0], to_m=[1.0],
                        columns=[dh.ColumnSpec("tt", "number", "м", "value")], values=[[0.5]])  # fmt: skip
    tid = dh.write_table(demo["engine"], demo["set_id"], data, job_id=None, tick=_noop)["table_id"]
    s = parse(demo, sp.parse_tt, table=str(tid), orientation=GLOBAL)
    with dialect.read_tx(demo["engine"]) as conn, pytest.raises(ApiError) as e:
        sp.check_columns(conn, s)
    assert (e.value.status, e.value.code, e.value.extra) == (409, "column_exists", {"param": "table", "name": "tt"})
    with dialect.write_tx(demo["engine"]) as conn:
        conn.execute(sa.insert(res.dh_results).values(
            set_id=demo["set_id"], table_id=tid, op="tt", name="Истинная мощность · Интервалы", params={}, inputs={},
            sha256="0", algo="tt-1", report={"written": [{"table": "интервалы", "columns": ["TT"]}]},
            created_at=dialect.utcnow()))  # fmt: skip
    with dialect.read_tx(demo["engine"]) as conn:
        sp.check_columns(conn, s)  # its own field: a repeat overwrites it
