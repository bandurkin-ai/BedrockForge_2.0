"""The jobs `blocks.density` and `blocks.classify` — the fields of a block model by a rule (подшаг 17.29; ТЗ 8.7.5; ADR
0023 §9, решения 13, 22; docs/progress/e17-api.md §9, §15).

The fields written with their roles, units and sources, the values equal to the kernels of 17.26, the restrictor, the
default of the domain, «устарел» by the inputs (a changed or deleted field, a wireframe of a category, the filter), a
cancel at any tick leaves nothing, 409 on a stale version or a name in use, the same on PostgreSQL (`-m db`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import categories as cat
from bf_core.blocks import density as dens
from bf_core.blocks import fields_store as fst
from bf_core.blocks import jobs_fields as jf
from bf_core.blocks.model import Model
from bf_core.db import dialect, migrate
from bf_core.db.schema import objects
from bf_core.jobs.context import JobCancelled
from tests.dbutil import make_engine
from tests.test_blocks_fields_store import make_model, sample
from tests.test_blocks_fill_jobs import P, body, plain_model, swap_geometry, tmp_clean
from tests.test_blocks_ops import stages_of, ticker

VALUES = sample()[1]
AU, N, DOM = VALUES["AU"], VALUES["N"], VALUES["DOM"]
CU = VALUES["CU"].astype(np.float32).astype(np.float64)  # a field f4: the values come back widened


@pytest.fixture()
def pm(tmp_path: Path):  # type: ignore[no-untyped-def]
    """A project with a model of 350 rows: AU, CU, N and the category DOM (руда, порода, дайка)."""
    engine = make_engine("sqlite", tmp_path)
    migrate.upgrade(engine, "project")
    p = P(engine, tmp_path)
    p.model_id = make_model(p, p.store)  # type: ignore[attr-defined]
    yield p
    engine.dispose()


def opened(p: Any, mid: int | None = None) -> Model:
    with dialect.read_tx(p.engine) as conn:
        return Model.open(conn, p.store, mid or p.model_id)


def col(p: Any, name: str, mid: int | None = None) -> np.ndarray:
    return opened(p, mid).columns([name])[name]


def same(a: np.ndarray, b: np.ndarray) -> bool:
    return bool(np.array_equal(a, b, equal_nan=True))


def dens_params(p: Any, **kw: Any) -> dict[str, Any]:
    m = opened(p)
    return {"model_id": m.id, "expected_version": kw.pop("expected_version", m.version), "method": "constant",
            "value": 2.7, **kw}  # fmt: skip


def class_params(p: Any, **kw: Any) -> dict[str, Any]:
    m = opened(p)
    rules = [{"label": "A", "expr": "AU > 2"}, {"label": "B", "expr": "AU > 1"}, {"label": "C", "expr": "AU >= 0"}]
    return {"model_id": m.id, "expected_version": kw.pop("expected_version", m.version), "rules": rules, **kw}


def stale(p: Any, name: str, mid: int | None = None) -> bool:
    with dialect.read_tx(p.engine) as conn:
        m = Model.open(conn, p.store, mid or p.model_id)
        return jf.is_stale(conn, p.store, m, m.field(name))


def replace_values(p: Any, name: str, values: np.ndarray, dtype: str = "f8") -> int:
    """The field `name` written again (a new file): the files its dependants read changed."""
    m = opened(p)
    with fst.FieldsWriter(p.store, m, m.version) as w:  # type: ignore[arg-type]
        w.add(name, dtype, replace=True)
        w.write({name: values})
        staged = w.finish()
    with dialect.write_tx(p.engine) as conn:
        return fst.commit_fields(conn, p.store, staged, source={"op": "calc", "params": {"expr": name}})


# --- the density ------------------------------------------------------------------------------------------------------


def test_a_constant_density_writes_two_fields_with_roles_and_source(pm: P) -> None:
    seen: list[Any] = []
    out = jf.run_density(pm.engine, pm.folder, dens_params(pm), tick=ticker(seen))
    assert stages_of(seen) == ["Чтение", "Расчёт", "Запись"] and set(out["stages"]) == {
        "read",
        "calc",
        "write",
        "total",
    }
    assert (out["field"], out["fields"], out["version"], out["rows"]) == ("DENS", ["DENS", "DENS_SRC"], 2, 350)
    assert (out["passed"], out["defaulted"], out["empty"]) == (350, 0, 0)
    assert out["events"] == [["objects.changed", {"kind": "block_model", "id": pm.model_id}]]
    m = opened(pm)
    d, s = m.field("DENS"), m.field("DENS_SRC")
    assert (d["dtype"], d["role"], d["unit"], d["file"]) == ("f8", "density", "т/м³", s["file"])
    assert (s["dtype"], s["role"]) == ("i1", None) and d["file"] != "cells"
    assert np.all(col(pm, "DENS") == 2.7) and np.all(col(pm, "DENS_SRC") == 0.0)
    assert d["stats"]["min"] == d["stats"]["max"] == 2.7 and out["stats"] == d["stats"]
    src = d["source"]
    assert src["op"] == "density" and src["algo"] == dens.ALGO and src["inputs"] == []
    assert src["params"]["method"] == "constant" and src["params"]["filter_id"] is None
    assert src["params"]["restrict"] is None and src["params"]["out"] == "DENS" and s["source"] == src
    assert not stale(pm, "DENS") and tmp_clean(pm)


def test_a_density_from_a_field_the_default_of_the_domain_and_the_restrictor(pm: P) -> None:
    out = jf.run_density(pm.engine, pm.folder, dens_params(pm, method="field", field="CU", default=2.5))
    gap = np.isnan(CU)
    assert out["defaulted"] == int(gap.sum()) and out["passed"] == 350 and out["warnings"] == []
    assert same(col(pm, "DENS"), np.where(gap, 2.5, CU))
    assert same(col(pm, "DENS_SRC"), gap.astype(np.float64))
    assert opened(pm).field("DENS")["unit"] == "%"  # the unit of the field the values are copied from, as they are
    # without a default the gap stays empty (never 0), and the restrictor leaves the blocks outside empty
    out = jf.run_density(pm.engine, pm.folder, dens_params(pm, method="field", field="CU", out="D2", src_field="S2",
                                                           restrict={"expr": "N > 10"}))  # fmt: skip
    inside = N > 10
    assert out["passed"] == int(inside.sum()) and out["empty"] == int((inside & gap).sum())
    assert "Значение по умолчанию не задано: блоки без плотности останутся пустыми." in out["warnings"]
    assert same(col(pm, "D2"), np.where(inside, CU, np.nan))
    assert same(col(pm, "S2"), np.where(inside, gap.astype(np.float64), np.nan))
    params = opened(pm).field("D2")["source"]["params"]
    assert params["restrict"] == {"expr": "N > 10"} and params["field"] == "CU"


def test_a_density_by_a_table_a_formula_and_a_field_that_is_not_a_density(pm: P) -> None:
    table = {"field": "DOM", "values": [{"value": "руда", "density": 3.1}, {"value": "порода", "density": 2.6}]}
    out = jf.run_density(pm.engine, pm.folder, dens_params(pm, method="table", table=table, default=2.0))
    want = np.select([DOM == 0, DOM == 1], [3.1, 2.6], 2.0)  # дайка and the empty — the default
    assert same(col(pm, "DENS"), want) and out["defaulted"] == int(((DOM == 2) | (DOM < 0)).sum())
    out = jf.run_density(pm.engine, pm.folder, dens_params(pm, method="formula", out="DF", src_field="SF",
                                                           expr="IF(AU > 1, 3.1, 2.7)", default=2.0))  # fmt: skip
    assert same(col(pm, "DF"), np.where(np.isnan(AU), 2.0, np.where(AU > 1, 3.1, 2.7)))
    assert out["field"] == "DF" and opened(pm).field("DF")["source"]["params"]["expr"] == "IF(AU > 1, 3.1, 2.7)"
    with pytest.raises(ApiError) as e:  # a zero of the field is a refusal, not a silent zero tonnage
        jf.run_density(pm.engine, pm.folder, dens_params(pm, method="field", field="N", out="D3", src_field="S3"))
    assert (e.value.status, e.value.code) == (422, "density_invalid")
    for kw, code in (({"method": "field", "field": "NOPE"}, "density_field_missing"),
                     ({"method": "field", "field": "DOM"}, "bad_param"),
                     ({"method": "formula", "expr": "AU +"}, "expr_syntax")):  # fmt: skip
        with pytest.raises(ApiError) as e2:
            jf.run_density(pm.engine, pm.folder, dens_params(pm, out="D4", src_field="S4", **kw))
        assert (e2.value.status, e2.value.code) == (422, code), kw
    assert [f["name"] for f in opened(pm).fields][-4:] == ["DENS", "DENS_SRC", "DF", "SF"] and tmp_clean(pm)


def test_names_in_use_versions_and_replacement(pm: P) -> None:
    jf.run_density(pm.engine, pm.folder, dens_params(pm))
    for kw in ({}, {"out": "dens"}, {"out": "X1", "src_field": "dens_src"}):
        with pytest.raises(ApiError) as e:
            jf.run_density(pm.engine, pm.folder, dens_params(pm, **kw))
        assert (e.value.status, e.value.code) == (409, "field_exists"), kw
        assert e.value.extra["field"].casefold() in ("dens", "dens_src")
    with pytest.raises(ApiError) as e2:  # the version moved
        jf.run_density(pm.engine, pm.folder, dens_params(pm, expected_version=1, out="X2", src_field="X3"))
    assert (e2.value.status, e2.value.code, e2.value.extra) == (409, "version_conflict", {"version": 2})
    with pytest.raises(ApiError) as e3:
        jf.run_density(pm.engine, pm.folder, dens_params(pm, out="Y", src_field="y"))
    assert (e3.value.status, e3.value.code, e3.value.extra["param"]) == (422, "bad_param", "src_field")
    out = jf.run_density(pm.engine, pm.folder, dens_params(pm, value=3.0, replace=True, unit="г/см³",
                                                           description="Плотность руды"))  # fmt: skip
    m = opened(pm)
    assert out["version"] == 3 and np.all(col(pm, "DENS") == 3.0)
    assert (m.field("DENS")["unit"], m.field("DENS")["description"]) == ("г/см³", "Плотность руды")
    assert [f["name"] for f in m.fields].count("DENS") == 1 and tmp_clean(pm)
    assert [f["file"] for f in m.fields if f["name"] in ("DENS", "DENS_SRC")] == [m.field("DENS")["file"]] * 2


def test_a_density_is_stale_when_its_inputs_change_or_go(pm: P) -> None:
    kw = {"method": "field", "field": "CU", "default": 2.5, "out": "D", "src_field": "S",
          "restrict": {"expr": "N > 10"}}  # fmt: skip
    jf.run_density(pm.engine, pm.folder, dens_params(pm, **kw))
    src = opened(pm).field("D")["source"]
    assert len(src["inputs"]) == 2 and not stale(pm, "D")  # the file of CU and N (one) and the tree
    replace_values(pm, "CU", np.where(np.isnan(CU), np.nan, CU + 1.0))  # a field it reads written again
    assert stale(pm, "D")
    jf.run_density(pm.engine, pm.folder, dens_params(pm, replace=True, **kw))
    assert not stale(pm, "D")
    m = opened(pm)
    with dialect.write_tx(pm.engine) as conn:
        fst.delete_fields(conn, pm.model_id, m.version, ["CU"])  # an input is gone
    assert stale(pm, "D")
    assert not stale(pm, "AU")  # a field of the import is never stale


def test_a_cancel_of_a_density_leaves_nothing(pm: P) -> None:
    total: list[Any] = []
    jf.run_density(pm.engine, pm.folder, dens_params(pm, out="A", src_field="B"), tick=ticker(total), chunk=64)
    assert len(total) >= 4
    v = opened(pm).version
    for at in (1, 2, 3, len(total) - 1, len(total)):
        with pytest.raises(JobCancelled):
            jf.run_density(pm.engine, pm.folder, dens_params(pm, expected_version=v, out="C", src_field="D"),
                           tick=ticker([], at), chunk=64)  # fmt: skip
        assert opened(pm).version == v and "C" not in {f["name"] for f in opened(pm).fields} and tmp_clean(pm), at


def test_the_portions_do_not_change_the_density(pm: P) -> None:
    kw = {"method": "formula", "expr": "IF(AU > 1, 3.1, 2.7)", "default": 2.0}
    jf.run_density(pm.engine, pm.folder, dens_params(pm, out="A", src_field="B", **kw), chunk=64)
    jf.run_density(pm.engine, pm.folder, dens_params(pm, out="C", src_field="D", **kw))
    assert same(col(pm, "A"), col(pm, "C")) and same(col(pm, "B"), col(pm, "D"))


# --- the categories ---------------------------------------------------------------------------------------------------


def test_classify_writes_a_category_with_the_labels_of_the_rules(pm: P) -> None:
    seen: list[Any] = []
    out = jf.run_classify(pm.engine, pm.folder, class_params(pm), tick=ticker(seen))
    assert stages_of(seen) == ["Чтение", "Расчёт", "Запись"] and "Мажоритарный фильтр" not in seen
    assert (out["field"], out["fields"], out["version"], out["rows"]) == ("CLASS", ["CLASS"], 2, 350)
    f = opened(pm).field("CLASS")
    assert (f["kind"], f["dtype"], f["role"], f["labels"]) == ("category", "cat", "class", ["A", "B", "C"])
    code = np.full(350, -1, dtype=np.int32)
    code[AU >= 0] = 2
    code[AU > 1] = 1
    code[AU > 2] = 0
    assert np.array_equal(col(pm, "CLASS"), code)  # a gap in the field: no category
    assert out["counts"] == {s: int((code == i).sum()) for i, s in enumerate("ABC")}
    assert out["unclassified"] == int((code < 0).sum()) == int(np.isnan(AU).sum())
    assert out["passed"] == 350 and out["events"] == [["objects.changed", {"kind": "block_model", "id": pm.model_id}]]
    src = f["source"]
    assert src["op"] == "classify" and src["algo"] == cat.ALGO and len(src["inputs"]) == 1
    assert src["params"]["rules"][0] == {"label": "A", "expr": "AU > 2"} and src["params"]["scheme"] is None
    assert src["params"]["majority"] == 0 and src["params"]["out"] == "CLASS"
    assert not stale(pm, "CLASS") and tmp_clean(pm)


def test_classify_estimate_restrictor_and_the_majority_filter(pm: P) -> None:
    seen: list[Any] = []
    kw = {"estimate": "N", "majority": 2, "restrict": {"expr": "CU > 0.2"}, "out": "K"}
    out = jf.run_classify(pm.engine, pm.folder, class_params(pm, **kw), tick=ticker(seen))
    assert stages_of(seen) == ["Чтение", "Расчёт", "Мажоритарный фильтр", "Запись"]
    assert "majority" in out["stages"]
    # the same as the kernels give (the job adds nothing, takes nothing)
    m = opened(pm)
    spec = cat.parse({"rules": class_params(pm)["rules"], "estimate": "N", "majority": 2, "out": "K"})
    plan = cat.prepare(m, spec)

    class Cut:  # the restrictor of the test: CU > 0.2 as the job's `restrict.prepare` makes it
        fields = ["CU"]

        def weights(self, ch: Any) -> np.ndarray:
            return (ch.cols["CU"] > 0.2).astype(np.float64)

    ref = cat.classify(m, plan, Cut())
    assert np.array_equal(col(pm, "K"), ref.codes.astype(np.int32))
    assert out["counts"] == dict(zip("ABC", ref.counts, strict=True)) and out["no_estimate"] == ref.no_estimate
    assert out["unclassified"] == ref.unclassified and out["passed"] == ref.passed
    assert opened(pm).field("K")["source"]["params"]["majority"] == 2


def test_classify_refusals_before_anything_is_written(pm: P) -> None:
    v = opened(pm).version
    bad_rules = [{"label": "A", "expr": "NOPE > 1"}]
    for kw, status, code in (
        ({"rules": bad_rules}, 422, "classify_field_missing"),
        ({"rules": [{"label": "A", "expr": "AU"}, {"label": "B", "expr": "'x'"}]}, 422, "expr_type"),
        ({"estimate": "NOPE"}, 422, "classify_field_missing"),
        ({"restrict": {"cond": {"field": "NOPE", "op": ">", "value": 1}}}, 422, "filter_field_missing"),
        ({"filter_id": 999}, 404, "filter_not_found"),
        ({"expected_version": v + 1}, 409, "version_conflict"),
    ):  # fmt: skip
        with pytest.raises(ApiError) as e:
            jf.run_classify(pm.engine, pm.folder, class_params(pm, **kw))
        assert (e.value.status, e.value.code) == (status, code), kw
    jf.run_classify(pm.engine, pm.folder, class_params(pm))
    with pytest.raises(ApiError) as e2:
        jf.run_classify(pm.engine, pm.folder, class_params(pm, expected_version=1, out="Z"))
    assert (e2.value.status, e2.value.code, e2.value.extra) == (409, "version_conflict", {"version": 2})
    with pytest.raises(ApiError) as e3:
        jf.run_classify(pm.engine, pm.folder, class_params(pm))
    assert (e3.value.status, e3.value.code, e3.value.extra) == (409, "field_exists", {"field": "CLASS"})
    out = jf.run_classify(pm.engine, pm.folder, class_params(pm, expected_version=2, replace=True,
                                                             description="Мои классы"))  # fmt: skip
    assert out["version"] == 3 and opened(pm).field("CLASS")["description"] == "Мои классы" and tmp_clean(pm)


def test_a_cancel_of_classify_leaves_nothing(pm: P) -> None:
    total: list[Any] = []
    jf.run_classify(pm.engine, pm.folder, class_params(pm, out="A", majority=1), tick=ticker(total), chunk=64)
    v = opened(pm).version
    for at in (1, 2, 3, len(total) // 2, len(total)):
        with pytest.raises(JobCancelled):
            jf.run_classify(pm.engine, pm.folder, class_params(pm, expected_version=v, out="C", majority=1),
                            tick=ticker([], at), chunk=64)  # fmt: skip
        assert opened(pm).version == v and tmp_clean(pm), at


def test_the_wireframe_of_a_category_overrides_the_rule_and_makes_the_field_stale_when_edited(tmp_path: Path) -> None:
    engine = make_engine("sqlite", tmp_path)
    migrate.upgrade(engine, "project")
    p = P(engine, tmp_path)
    try:
        mid = plain_model(p)  # 6 × 5 × 4 parents of 10 m, AU = the number of the parent (a gap at every seventh)
        inside = body(p, (0.0, 0.0, 0.0), (30.0, 20.0, 40.0), name="Зона")
        donor = body(p, (30.0, 20.0, 0.0), (60.0, 50.0, 40.0), name="Донор")
        rules = [{"label": "низ", "expr": "AU < 60"}, {"label": "верх", "expr": "AU >= 60"}]
        params = {"model_id": mid, "expected_version": 1, "rules": rules, "estimate": "AU",
                  "overrides": [{"wireframe_id": inside, "label": "верх"}], "out": "K"}  # fmt: skip
        out = jf.run_classify(engine, p.folder, params)
        m = opened(p, mid)
        au = m.columns(["AU"])["AU"]
        i, j = np.arange(120) % 6, (np.arange(120) // 6) % 5
        zone = (i < 3) & (j < 2)  # the centres of the parents in the box
        want = np.where(au < 60, 0, 1)
        want = np.where(zone, 1, want)
        want = np.where(np.isnan(au), -1, want)  # no estimate: no category, whatever the wireframe says
        assert np.array_equal(col(p, "K", mid), want.astype(np.int32)) and out["no_estimate"] == int(np.isnan(au).sum())
        assert len(m.field("K")["source"]["params"]["overrides"]) == 1
        assert not stale(p, "K", mid)
        swap_geometry(p, inside, donor)  # the wireframe is edited in place
        assert stale(p, "K", mid)
        with dialect.write_tx(engine) as conn:
            conn.execute(sa.delete(objects).where(objects.c.id == inside))
        assert stale(p, "K", mid)  # a wireframe that is gone
        with pytest.raises(ApiError) as e:
            jf.check_inputs_alive(*_alive(p, mid, "K"))
        assert e.value.code == "wireframe_not_found"
    finally:
        engine.dispose()


def _alive(p: Any, mid: int, name: str) -> tuple[Any, ...]:
    conn = p.engine.connect()
    m = Model.open(conn, p.store, mid)
    return conn, p.store, m, m.field(name)


@pytest.mark.db
def test_the_field_jobs_on_both_databases(db_url: str, tmp_path: Path) -> None:
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project")
    p = P(engine, tmp_path / "blobs")
    p.store.tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        p.model_id = make_model(p, p.store)  # type: ignore[attr-defined]
        out = jf.run_density(engine, p.folder, dens_params(p, method="field", field="CU", default=2.5))
        assert out["version"] == 2 and same(col(p, "DENS"), np.where(np.isnan(CU), 2.5, CU))
        out = jf.run_classify(engine, p.folder, class_params(p, majority=1))
        assert out["version"] == 3 and out["rows"] == 350
        assert not stale(p, "DENS") and not stale(p, "CLASS")
        replace_values(p, "AU", AU + 1.0)
        assert not stale(p, "DENS") and stale(p, "CLASS")
    finally:
        engine.dispose()
