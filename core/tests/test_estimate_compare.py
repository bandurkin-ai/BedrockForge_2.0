# ruff: noqa: F811 - the fixture `ra` is imported, and the tests take it by its name
"""«Сравнить оценки» — the job `estimate.compare` (подшаг 20.10; ТЗ 07-35 Д-37; ADR 0027 решения 25, 28, № 108):
**ТД37-1** (A = B → Δ = 0 in every block, the statistics and the domains zero), the field Δ (a gap in A or B is a gap
in Δ, never 0), the statistics, the histogram by blocks, the table by domains weighted by volume (empty FRAC — out of
the weights), the blocks whose STATUS changed, the graph (the inputs of Δ are A and B; a rewritten A makes Δ «устарел»),
a repeated comparison rewrites its field, 409 / 404 / 422 in Russian, the same bits with any portion, the route and the
job in a worker (`POST /blocks/{id}/compare`)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core import lineage
from bf_core.api.errors import ApiError
from bf_core.blocks import compare as cmp
from bf_core.blocks import compare_job as cj
from bf_core.blocks.model import Model
from bf_core.db import dialect
from bf_core.db.schema import blob_owners
from bf_core.jobs.context import JobCancelled
from bf_core.lineage import store as ls
from tests.estapi_util import finish
from tests.resapi_world import API, ra, read_only, refused  # noqa: F401 - the fixtures
from tests.test_blocks_fields_store import add_fields
from tests.test_blocks_report import put

NAN = np.nan
GRID = {"corner": [0.0, 0.0, 0.0], "counts": [4, 2, 1], "size": [10.0, 10.0, 10.0]}
FIELDS = [
    {"name": "DOM", "dtype": "cat", "labels": ["D1", "D2"], "role": "code"},
    {"name": "FRAC", "dtype": "f8", "role": "frac"},
    {"name": "AU", "dtype": "f8", "unit": "g/t", "role": "grade"},
]
DOM = np.array([0, 0, 0, 1, 1, 1, -1, -1], dtype=np.int32)
FRAC = np.array([1, 1, 0.5, 1, 1, 1, NAN, 1.0])
EST_A = np.array([1, 2, 3, NAN, 5, 6, 7, 8.0])
EST_B = np.array([1, 3, 3, 4, NAN, 4, 7, 10.0])
STATUS_A = np.array([0, 0, 0, 1, 1, 0, 0, 0.0])
STATUS_B = np.array([0, 0, 2, 1, 0, 0, 0, 0.0])
EST = {"op": "estimate", "params": {}, "inputs": [], "algo": "estimate-1"}
FIELDS_EST = [{"name": "OK_EST", "dtype": "f8", "unit": "g/t"}, {"name": "OK_STATUS", "dtype": "i1"},
              {"name": "ID_EST", "dtype": "f8", "unit": "g/t"}, {"name": "ID_STATUS", "dtype": "i1"}]  # fmt: skip


@pytest.fixture(autouse=True)
def _job_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """The job is in the workers (until the line of `jobs/registry.py`): before the core starts."""
    monkeypatch.setenv("BF_EXTRA_JOB_MODULES", "bf_core.blocks.compare_job")


@pytest.fixture()
def world(ra) -> Any:  # noqa: F811
    """A core and the model of eight blocks with two estimates (and their STATUS), a domain field and a FRAC."""
    p = np.arange(8, dtype=np.uint64)
    ra.mid = put(ra, GRID, FIELDS, p, {"DOM": DOM, "FRAC": FRAC, "AU": EST_A})
    add_fields(
        ra.project,
        ra.store,
        ra.mid,
        FIELDS_EST,
        {"OK_EST": EST_A, "OK_STATUS": STATUS_A, "ID_EST": EST_B, "ID_STATUS": STATUS_B},
        source=EST,
        step=3,
    )
    return ra


def model_of(c: Any) -> Model:
    with dialect.read_tx(c.project.engine) as conn:
        return Model.open(conn, c.store, c.mid)


def run(c: Any, **body: Any) -> dict[str, Any]:
    return cj.run_compare(c.project.engine, c.project.folder, {"model_id": c.mid, **body})


def column(c: Any, name: str) -> np.ndarray:
    return np.asarray(model_of(c).columns([name])[name])


def test_td37_1_a_equals_b_is_zero_in_every_block(world) -> None:
    out = run(world, field_a="OK_EST", field_b="OK_EST")
    d = column(world, "OK_EST_OK_EST_D")
    ok = np.isfinite(EST_A)
    assert np.array_equal(np.isnan(d), ~ok) and (d[ok] == 0.0).all()  # a gap stays a gap, the rest is exactly 0
    st = out["stats"]
    assert (st["n"], st["mean"], st["std"], st["min"], st["max"]) == (7, 0.0, 0.0, 0.0, 0.0)
    assert set(st["percentiles"].values()) == {0.0}
    assert out["status"]["changed"] == 0
    assert all(r["mean_delta"] in (0.0, None) and r["status_changed"] == 0 for r in out["domains"])
    # two different fields with the same values: the same
    add_fields(world.project, world.store, world.mid, [{"name": "COPY", "dtype": "f8"}], {"COPY": EST_A})
    run(world, field_a="OK_EST", field_b="COPY", out="Z")
    z = column(world, "Z")
    assert (z[ok] == 0.0).all() and np.isnan(z[~ok]).all()


def test_the_field_is_a_minus_b_with_gaps_and_the_statistics(world) -> None:
    out = run(world, field_a="OK_EST", field_b="ID_EST")
    d = column(world, "OK_EST_ID_EST_D")
    want = np.array([0, -1, 0, NAN, NAN, 2, 0, -2.0])
    assert np.array_equal(d, want, equal_nan=True)
    f = model_of(world).field("OK_EST_ID_EST_D")
    assert f["kind"] == "number" and f["unit"] == "g/t" and f["source"]["op"] == "compare"
    assert (f["source"]["params"]["a"], f["source"]["params"]["b"]) == ("OK_EST", "ID_EST")
    assert out["field"] == "OK_EST_ID_EST_D" and out["field_written"] is True and out["a"] == "OK_EST"
    good = want[np.isfinite(want)]
    st = out["stats"]
    assert st["n"] == 6 and st["min"] == -2.0 and st["max"] == 2.0
    assert abs(st["mean"] - good.mean()) < 1e-15 and abs(st["std"] - good.std()) < 1e-15
    assert st["percentiles"]["50"] == 0.0 and list(st["percentiles"]) == ["1", "5", "25", "50", "75", "95", "99"]
    hist = out["histogram"]  # by the number of blocks
    assert len(hist["edges"]) == cmp.HIST_BINS + 1 and sum(hist["counts"]) == 6
    assert hist["edges"][0] == -2.0 and hist["edges"][-1] == 2.0
    assert hist["counts"][0] == 1 and hist["counts"][-1] == 1 and hist["counts"][cmp.HIST_BINS // 2] == 3  # 0, 0, 0
    assert out["stages"]["total"] >= 0 and out["events"] == [["objects.changed", {"kind": "block_model",
                                                                                  "id": world.mid}]]  # fmt: skip


def test_the_table_by_domains_is_weighted_by_volume_and_counts_the_status_changes(world) -> None:
    out = run(world, field_a="OK_EST", field_b="ID_EST")
    by = {r["domain"]: r for r in out["domains"]}
    assert list(by) == ["D1", "D2", "(пусто)"]
    d1 = by["D1"]  # volumes 1000, 1000, 500 (FRAC 0,5): the means of A, B, Δ by volume (№ 108)
    assert (d1["blocks"], d1["missing"], d1["volume"], d1["status_changed"]) == (3, 0, 2500.0, 1)
    assert abs(d1["mean_a"] - 1.8) < 1e-12 and abs(d1["mean_b"] - 2.2) < 1e-12 and abs(d1["mean_delta"] + 0.4) < 1e-12
    d2 = by["D2"]  # two blocks have a gap in A or in B: they are counted as missing, not as 0
    assert (d2["blocks"], d2["missing"], d2["volume"], d2["mean_a"], d2["mean_b"], d2["mean_delta"]) == (
        1, 2, 1000.0, 6.0, 4.0, 2.0,
    )  # fmt: skip
    assert d2["status_changed"] == 1
    none = by["(пусто)"]  # no label; the block 6 has no volume (empty FRAC): a block, but not in the weights
    assert (none["blocks"], none["volume"], none["mean_delta"], none["status_changed"]) == (2, 1000.0, -2.0, 0)
    assert out["status"] == {"a": "OK_STATUS", "b": "ID_STATUS", "changed": 2}


def test_without_a_domain_field_or_a_status(world) -> None:
    add_fields(world.project, world.store, world.mid, [{"name": "AU2", "dtype": "f8"}], {"AU2": EST_B})
    out = run(world, field_a="AU", field_b="AU2", out="DELTA_AU", domain="DOM")
    assert [r["domain"] for r in out["domains"]] == ["D1", "D2", "(пусто)"] and out["status"] is None
    assert all(r["status_changed"] is None for r in out["domains"])
    # the model without a field with the role code / class: one row «Все»
    from tests.resapi_world import model

    other = model(world)  # AU and CLASS (role class)
    got = cj.run_compare(world.project.engine, world.project.folder,
                         {"model_id": other, "field_a": "AU", "field_b": "AU", "domain": None})  # fmt: skip
    assert [r["domain"] for r in got["domains"]] == ["Измеренные", "Выявленные", "Предполагаемые"]
    assert got["warnings"] == [] and got["stats"]["n"] == 8


def test_all_in_one_row_when_there_is_no_domain_field(ra) -> None:
    mid = put(ra, GRID, [FIELDS[2], {"name": "B2", "dtype": "f8", "unit": "g/t"}], np.arange(8, dtype=np.uint64),
              {"AU": EST_A, "B2": EST_B})  # fmt: skip
    out = cj.run_compare(ra.project.engine, ra.project.folder, {"model_id": mid, "field_a": "AU", "field_b": "B2"})
    assert [r["domain"] for r in out["domains"]] == ["Все"]
    only = out["domains"][0]
    assert only["blocks"] == 6 and only["missing"] == 2 and only["volume"] == 6000.0  # no FRAC: whole blocks


def test_the_graph_has_a_and_b_as_inputs_and_a_rewritten_a_makes_delta_stale(world) -> None:
    run(world, field_a="OK_EST", field_b="ID_EST")
    node = lineage.Node("bm_field", world.mid, "ok_est_id_est_d")
    with dialect.read_tx(world.project.engine) as conn:
        refs = ls.inputs_of(conn, [node])[node]
        got = lineage.status(conn, [node], store=world.store)[node]
    assert sorted(r.node.sub for r in refs) == ["id_est", "ok_est"]
    assert got.state == lineage.CURRENT
    f = model_of(world).field("OK_EST_ID_EST_D")
    assert f["source"]["inputs"] and all(len(s) == 64 for s in f["source"]["inputs"])
    add_fields(world.project, world.store, world.mid, [{"name": "OK_EST", "dtype": "f8", "unit": "g/t",
                                                        "replace": True}], {"OK_EST": EST_A + 1})  # fmt: skip
    with dialect.read_tx(world.project.engine) as conn:
        assert lineage.status(conn, [node], store=world.store)[node].state == lineage.STALE


def test_a_repeated_comparison_rewrites_its_field_but_not_a_foreign_one(world) -> None:
    first = run(world, field_a="OK_EST", field_b="ID_EST")
    again = run(world, field_a="OK_EST", field_b="ID_EST")
    assert again["version"] == first["version"] + 1
    names = [f["name"] for f in model_of(world).fields]
    assert names.count("OK_EST_ID_EST_D") == 1
    with pytest.raises(ApiError) as e:  # the name of a field that is not a comparison
        run(world, field_a="OK_EST", field_b="ID_EST", out="AU")
    assert (e.value.status, e.value.code, e.value.extra["field"]) == (409, "field_exists", "AU")


def test_the_refusals_are_in_russian_with_a_hint(world) -> None:
    def err(**body: Any) -> ApiError:
        with pytest.raises(ApiError) as e:
            run(world, **{"field_a": "OK_EST", "field_b": "ID_EST", **body})
        assert e.value.message and e.value.hint and any("а" <= ch.lower() <= "я" for ch in e.value.message)
        return e.value

    e = err(field_a="NOPE")
    assert (e.status, e.code, e.extra["param"]) == (404, "block_field_not_found", "field_a")
    assert err(field_b="nope").extra["param"] == "field_b"
    e = err(field_b="DOM")  # a category is not a number
    assert (e.status, e.code, e.extra["param"]) == (422, "bad_param", "field_b")
    e = err(domain="AU")
    assert (e.status, e.extra["param"]) == (422, "domain")
    assert err(domain="XX").status == 404
    e = err(out="ok_est")  # the name of A
    assert (e.status, e.extra["param"]) == (422, "out")
    assert err(out="Я" * 101).extra["param"] == "out"
    e = err(expected_version=99)
    assert (e.status, e.code) == (409, "version_conflict")
    assert err(expected_version=0).extra["param"] == "expected_version"
    assert err(field_a="").extra["param"] == "field_a"


def test_a_stale_version_a_cancel_and_a_failure_leave_nothing(world) -> None:
    def owners() -> int:
        with dialect.read_tx(world.project.engine) as conn:
            return int(conn.execute(sa.select(sa.func.count()).select_from(blob_owners)).scalar_one())

    before, version = owners(), model_of(world).version
    with pytest.raises(ApiError):
        run(world, field_a="OK_EST", field_b="ID_EST", expected_version=version + 1)

    def stop(progress: float | None, message: str | None) -> None:
        if message == "Запись":
            raise JobCancelled

    with pytest.raises(JobCancelled):
        cj.run_compare(world.project.engine, world.project.folder,
                       {"model_id": world.mid, "field_a": "OK_EST", "field_b": "ID_EST"}, tick=stop)  # fmt: skip
    assert owners() == before and model_of(world).version == version
    assert "OK_EST_ID_EST_D" not in [f["name"] for f in model_of(world).fields]
    assert not list((world.store.tmp_dir).glob("*"))


def test_the_same_bits_with_any_portion(world) -> None:
    ref = run(world, field_a="OK_EST", field_b="ID_EST", out="P_BIG")
    one = cj.run_compare(world.project.engine, world.project.folder,
                         {"model_id": world.mid, "field_a": "OK_EST", "field_b": "ID_EST", "out": "P_ONE"},
                         chunk=1)  # fmt: skip
    seven = cj.run_compare(world.project.engine, world.project.folder,
                           {"model_id": world.mid, "field_a": "OK_EST", "field_b": "ID_EST", "out": "P_SEVEN"},
                           chunk=3)  # fmt: skip
    for got in (one, seven):
        assert got["histogram"] == ref["histogram"] and got["stats"]["percentiles"] == ref["stats"]["percentiles"]
        assert abs(got["stats"]["mean"] - ref["stats"]["mean"]) < 1e-14
        assert [d["status_changed"] for d in got["domains"]] == [d["status_changed"] for d in ref["domains"]]
        for a, b in zip(got["domains"], ref["domains"], strict=True):
            assert a["volume"] == b["volume"] and abs((a["mean_delta"] or 0) - (b["mean_delta"] or 0)) < 1e-12
    base = column(world, "P_BIG")
    assert np.array_equal(column(world, "P_ONE"), base, equal_nan=True)
    assert np.array_equal(column(world, "P_SEVEN"), base, equal_nan=True)


def test_the_route_runs_the_job_and_answers_in_russian(world) -> None:
    url = f"{API}/blocks/{world.mid}/compare"
    started = world.post(url, json={"field_a": "OK_EST", "field_b": "ID_EST", "domain": "DOM"})
    assert started.status_code == 200, started.text
    job = finish(world, started.json(), timeout=120)
    assert job["status"] == "done", job.get("error") or job
    res = job["result"]
    assert res["field"] == "OK_EST_ID_EST_D" and res["stats"]["n"] == 6 and res["status"]["changed"] == 2
    assert [r["domain"] for r in res["domains"]] == ["D1", "D2", "(пусто)"]
    assert column(world, "OK_EST_ID_EST_D")[1] == -1.0
    refused(world.post(url, json={"field_a": "NOPE", "field_b": "ID_EST"}), 404, "block_field_not_found", "field_a")
    refused(world.post(url, json={"field_a": "DOM", "field_b": "ID_EST"}), 422, "bad_param", "field_a")
    refused(world.post(url, json={"field_a": "AU", "field_b": "ID_EST", "out": "FRAC"}), 409, "field_exists")
    refused(world.post(url, json={"field_a": "OK_EST", "field_b": "ID_EST", "expected_version": 1}), 409,
            "version_conflict")  # fmt: skip
    refused(world.post(url, json={"field_a": "OK_EST"}), 422, "bad_param", "field_b")
    refused(world.post(f"{API}/blocks/9999/compare", json={"field_a": "A", "field_b": "B"}), 404,
            "block_model_not_found")  # fmt: skip
    back = read_only(world)
    try:
        refused(world.post(url, json={"field_a": "OK_EST", "field_b": "ID_EST", "out": "X"}), 423,
                "project_read_only")  # fmt: skip
    finally:
        back()


def test_the_plan_finds_the_status_fields_of_an_estimate_only(world) -> None:
    m = model_of(world)
    from bf_core.resources.spec import parse_field_compare

    plan = cmp.plan_of(m, parse_field_compare(world.mid, {"field_a": "OK_EST", "field_b": "ID_EST"}))
    assert (plan.status_a, plan.status_b, plan.domain, plan.frac) == ("OK_STATUS", "ID_STATUS", "DOM", "FRAC")
    assert plan.unit == "g/t" and plan.warnings == ()
    plain = cmp.plan_of(m, parse_field_compare(world.mid, {"field_a": "AU", "field_b": "ID_EST"}))
    assert (plain.status_a, plain.status_b) == (None, None)


def test_the_job_gets_only_the_checked_fields_of_the_body(world, monkeypatch) -> None:
    """Any other key of the body (a client's own) does not go to the parameters of the job (review 20.18б, № 11)."""
    from bf_core.jobs.scheduler import Scheduler

    seen: list[dict[str, Any]] = []
    submit = Scheduler.submit

    def spy(self: Scheduler, kind: str, params: dict[str, Any], **kw: Any) -> tuple[str, int]:
        seen.append(dict(params))
        return submit(self, kind, params, **kw)

    monkeypatch.setattr(Scheduler, "submit", spy)
    body = {"field_a": "OK_EST", "field_b": "ID_EST", "domain": "DOM", "extra": "x", "model_id": 999,
            "rows": 1, "source": "instance", "report_id": 5}  # fmt: skip
    started = world.post(f"{API}/blocks/{world.mid}/compare", json=body)
    assert started.status_code == 200, started.text
    assert finish(world, started.json(), timeout=120)["status"] == "done"
    assert len(seen) == 1
    assert set(seen[0]) <= {"field_a", "field_b", "domain", "out", "model_id", "expected_version", "rows"}, seen[0]
    assert seen[0]["model_id"] == world.mid and seen[0]["rows"] == 8 and seen[0]["field_a"] == "OK_EST"
