"""The job `estimate.run` (подшаг 18.12; ТЗ 8.8; ADR 0024 §7, 8.0.2; e18-api.md §2.1–2.3, §6): the fields the kernel
gives (types, STATUS, roles), the rows of one prefix in one set of fields, a repeat of one row (№ 77), sub-blocks = the
parent, the restrictor, a cancel and a failure leave nothing, 409 / 422, the calculator over STATUS (ТД27-3), «устарел»
through the graph and «Перестроить», the same bits on 1, 2, 4 threads, a worker (`spawn`), the database (`-m db`)."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core import lineage
from bf_core.api.errors import ApiError
from bf_core.blocks import calc_job
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, dh_tables
from bf_core.geostat.estimate import job
from bf_core.geostat.estimate import job_run as jr
from bf_core.geostat.estimate import store as st
from bf_core.geostat.estimate import store_base as sb
from bf_core.jobs.context import JobCancelled
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests import estjob_util as u
from tests.estjob_util import JW
from tests.test_blocks_fields_store import add_fields
from tests.test_import_job import API, wait_job

STATUS, EST = "AU_OK_STATUS", "AU_OK_EST"


@pytest.fixture()
def jw(tmp_path: Path):  # type: ignore[no-untyped-def]
    w = u.world(tmp_path)
    yield w
    w.project.engine.dispose()


def run(w: JW, model: int | None = None, threads: int = 2, **over: Any) -> dict[str, Any]:
    params = {"expected_version": u.version_of(w, model), "replace": False, **over}
    return job.run_estimate(w.project.engine, w.project.folder, params, threads=threads)


def cols(w: JW, names: list[str], model: int | None = None) -> dict[str, np.ndarray]:
    return u.open_model(w, model).columns(names)


def same(a: np.ndarray, b: np.ndarray) -> bool:
    return a.shape == b.shape and bool(np.all((a == b) | (np.isnan(a) & np.isnan(b))))


def pair(w: JW) -> tuple[int, int, int]:
    """The rows «руда» / «порода» of one prefix and the plan that holds them."""
    a = u.make(w)
    b = u.make(w, label="порода", zone="перв")
    return a, b, u.plan(w, [a, b])


def merged_expected(w: JW, a: int, b: int, key: str, model: int | None = None) -> np.ndarray:
    dom = cols(w, ["DOM"], model)["DOM"]
    da, db = u.direct(w, a, model), u.direct(w, b, model)
    return np.where(dom == 0, da[key], db[key])


def left_over(w: JW) -> list[str]:
    tmp = BlobStore(w.project.folder).tmp_dir
    return sorted(p.name for p in tmp.iterdir()) if tmp.is_dir() else []


# --- the fields ----------------------------------------------------------------------------------------------


def test_a_plan_writes_the_fields_the_kernel_gives(jw: JW) -> None:
    a, b, plan = pair(jw)
    out = run(jw, plan_id=plan)
    assert out["fields"] == [f"AU_OK_{k}" for k in ("STATUS", "EST", "KV", "NS", "NH", "AD", "PASS", "SLOPE", "KE")]
    assert (out["plan_id"], out["model_id"], out["version"]) == (plan, jw.model, 2)
    assert out["merged"] == [{"prefix": "AU_OK_", "estimation_ids": [a, b]}]
    assert out["events"][0] == ["objects.changed", {"kind": "block_model", "id": jw.model}]
    assert out["events"][1:] == [["geostat.changed", {"kind": "estimation", "id": a}],
                                 ["geostat.changed", {"kind": "estimation", "id": b}]]  # fmt: skip
    m = u.open_model(jw)
    kinds = {n: (m.field(n)["dtype"], m.field(n).get("role"), m.field(n).get("unit")) for n in out["fields"]}
    assert kinds == {"AU_OK_STATUS": ("i1", None, None), "AU_OK_EST": ("f8", "grade", "г/т"),
                     "AU_OK_KV": ("f4", None, None), "AU_OK_NS": ("i2", None, None), "AU_OK_NH": ("i2", None, None),
                     "AU_OK_AD": ("f4", None, "м"), "AU_OK_PASS": ("i1", None, None),
                     "AU_OK_SLOPE": ("f4", None, None), "AU_OK_KE": ("f4", None, None)}  # fmt: skip
    assert "0 — оценён; 1 — вне домена; 2 — мало проб; 3 — вырожденная система" in m.field(STATUS)["description"]
    src = m.field(EST)["source"]
    assert (src["op"], src["algo"], src["params"]) == ("estimate", "est-1", {"estimation_ids": [a, b]})
    got = cols(jw, out["fields"])
    for key, name in (("EST", EST), ("KV", "AU_OK_KV"), ("NS", "AU_OK_NS"), ("PASS", "AU_OK_PASS")):
        want = merged_expected(jw, a, b, key)
        have = got[name] if key != "KV" else got[name].astype(np.float32).astype(np.float64)
        if key == "KV":
            want = want.astype(np.float32).astype(np.float64)
        assert same(have, want), key
    assert not np.isnan(got[STATUS]).any() and set(np.unique(got[STATUS])) <= {0.0, 1.0, 2.0, 3.0}
    # the counts: the rows of the model, by status; the samples as Э16 counts them
    for row in out["rows"]:
        c = row["counts"]
        assert c["targets"] == m.rows == sum(c["by_status"]) and len(c["by_pass"]) == len(c["singular"]) == 3
        s = c["samples"]
        assert s["rows"] == s["missing"] + s["no_position"] + s["outside_domain"] + s["used"]
    assert [r["label"] for r in out["rows"]] == ["руда", "порода"]
    # the rows: last run, current, the graph
    with dialect.read_tx(jw.project.engine) as conn:
        for ident in (a, b):
            g = st.get(conn, ident)
            last = g["meta"]["last_run"]
            assert (g["stale"], last["version"], last["counts"]["targets"], last["merged"]) == (False, 2, m.rows, 0)
            assert last["params"] == sb.params_hash(sb.load_spec(g["meta"]))
        node = lineage.Node("bm_field", jw.model, "au_ok_est")
        assert lineage.status(conn, [node])[node].state == "current"
        ins = sa.text("select src_kind, src_id from lineage where dst_kind = 'bm_field' and dst_sub = 'au_ok_est'")
        assert sorted(tuple(r) for r in conn.execute(ins)) == [("object:estimation", a), ("object:estimation", b)]


def test_a_row_alone_leaves_the_rest_out_of_the_domain_not_zero(jw: JW) -> None:
    a = u.make(jw)
    out = run(jw, estimation_ids=[a])
    assert out["merged"] == [] and out["plan_id"] is None
    got = cols(jw, [STATUS, EST, "AU_OK_KV", "AU_OK_NS", "AU_OK_PASS"])
    dom = cols(jw, ["DOM"])["DOM"]
    outside = dom == 1
    assert (got[STATUS][outside] == 1).all() and (got[STATUS][~outside] == 0).all()
    for n in (EST, "AU_OK_KV", "AU_OK_NS", "AU_OK_PASS"):
        assert np.isnan(got[n][outside]).all() and not np.isnan(got[n][~outside]).any(), n  # empty, never 0
    c = out["rows"][0]["counts"]
    assert c["by_status"] == [int((~outside).sum()), int(outside.sum()), 0, 0]


def test_the_restrictor_of_the_target_gives_status_1(jw: JW) -> None:
    ident = u.make(jw, target={"rules": {"expr": "DOM = 'руда'"}}, name="Ограничитель")
    run(jw, estimation_ids=[ident])
    got = cols(jw, [STATUS, EST])
    dom = cols(jw, ["DOM"])["DOM"]
    assert (got[STATUS][dom == 1] == 1).all() and np.isnan(got[EST][dom == 1]).all()
    assert same(got[EST][dom == 0], u.direct(jw, ident)["EST"][dom == 0])
    assert not np.isnan(got[EST][dom == 0]).any()


# --- a repeat of a part of the plan (№ 77) -------------------------------------------------------------------


def test_a_repeat_of_one_row_rewrites_its_domain_only(jw: JW) -> None:
    a, b, plan = pair(jw)
    first = run(jw, plan_id=plan)
    before = cols(jw, first["fields"])
    u.put(jw, a, search={"from": "model", "passes": [{"factor": 1, "n_min": 2, "n_max": 6}]})
    with pytest.raises(ApiError) as e:
        run(jw, estimation_ids=[a])
    assert (e.value.status, e.value.code, e.value.extra["field"]) == (409, "field_exists", "AU_OK_STATUS")
    out = run(jw, estimation_ids=[a], replace=True)
    assert out["fields"] == first["fields"] and out["version"] == first["version"] + 1
    after = cols(jw, first["fields"])
    dom = cols(jw, ["DOM"])["DOM"]
    for n in first["fields"]:
        assert same(after[n][dom == 1], before[n][dom == 1]), n  # the domain of the other row: carried
    assert not same(after[EST][dom == 0], before[EST][dom == 0])  # n_max 6 instead of 24: another estimate
    assert same(after[EST][dom == 0], u.direct(jw, a)["EST"][dom == 0])
    assert same(after[STATUS], before[STATUS]) and not np.isnan(after[STATUS]).any()
    assert [m["estimation_ids"] for m in out["merged"]] == []  # one row ran
    assert any("перенесены" in line for line in out["log"])
    with dialect.read_tx(jw.project.engine) as conn:  # the field still has both rows as inputs
        ins = sa.text("select src_id from lineage where dst_kind = 'bm_field' and dst_sub = 'au_ok_est'")
        assert sorted(r[0] for r in conn.execute(ins)) == [a, b]
        assert st.get(conn, b)["meta"]["last_run"]["version"] == first["version"]  # b did not run
        assert st.get(conn, a)["meta"]["last_run"]["version"] == out["version"]


def test_two_rows_of_one_prefix_with_one_domain_are_refused(jw: JW) -> None:
    a, b, plan = pair(jw)
    run(jw, plan_id=plan)
    u.put(jw, a, target={"domain_field": "DOM", "label": "порода"}, name="Теперь порода")  # same blocks as b
    with pytest.raises(ApiError) as e:
        run(jw, plan_id=plan, replace=True)
    assert (e.value.status, e.value.code) == (422, "bad_param")  # two rows, one domain: the plan is refused


# --- sub-blocks ----------------------------------------------------------------------------------------------


def test_sub_blocks_get_the_value_of_their_parent(jw: JW) -> None:
    ident = u.make(jw, model=jw.sub, name="Субблоки родитель")
    out = run(jw, model=jw.sub, estimation_ids=[ident])
    m = u.open_model(jw, jw.sub)
    assert m.sub and m.rows > m.parents and out["rows"][0]["counts"]["targets"] == m.rows
    got = m.columns([STATUS, EST, "DOM"])
    p = m.p()
    want = u.direct(jw, ident, jw.sub)["EST"]
    inside = got["DOM"] == 0
    assert inside.any() and (~inside).any() and (got[STATUS][~inside] == 1).all()
    assert same(got[EST][inside], want[inside]) and not np.isnan(got[EST][inside]).any()
    # sub-blocks of one parent in the domain share one value; a parent split into two domains has both of them
    for parent in np.unique(p[inside])[:50]:
        mine = (p == parent) & inside
        assert len(np.unique(got[EST][mine])) == 1
    split = [q for q in np.unique(p) if len(np.unique(got["DOM"][p == q])) == 2]
    assert split, "the world has a parent in two domains"


def test_own_sub_blocks_are_estimated_at_their_own_centre_and_size(jw: JW) -> None:
    ident = u.make(jw, model=jw.sub, sub="own", name="Субблоки свои")
    run(jw, model=jw.sub, estimation_ids=[ident])
    got = cols(jw, [STATUS, EST, "DOM"], jw.sub)
    inside = got["DOM"] == 0
    want = u.direct(jw, ident, jw.sub, own=True)["EST"]
    assert same(got[EST][inside], want[inside])
    parent = u.direct(jw, ident, jw.sub)["EST"]
    assert not same(got[EST][inside], parent[inside])  # another centre, another block: another value


def test_blocks_without_enough_samples_are_status_2_and_empty(jw: JW) -> None:
    ident = u.make(jw, search={"from": "manual", "r1": 3, "passes": [{"factor": 1, "n_min": 4, "n_max": 24}]})
    out = run(jw, estimation_ids=[ident])
    got = cols(jw, [STATUS, EST, "AU_OK_NS"])
    inside = cols(jw, ["DOM"])["DOM"] == 0
    assert (got[STATUS][inside] == 2).all() and (got[STATUS][~inside] == 1).all()
    assert np.isnan(got[EST]).all() and np.isnan(got["AU_OK_NS"]).all()  # empty, not 0
    assert out["rows"][0]["counts"]["by_status"] == [0, int((~inside).sum()), int(inside.sum()), 0]


def test_the_memory_limiter_cuts_the_pool_and_the_piece() -> None:
    from types import SimpleNamespace as NS

    def plan(n_max: int) -> Any:
        row = NS(spec=NS(search=NS(passes=[NS(n_max=n_max)])), samples=NS(n=100_000))
        return NS(rows=[row], read=["DOM"])

    assert job.limits(plan(24), 8, None) == (8, jr.PORTION)  # no budget, the default candidates: the full piece
    assert job.limits(plan(128), 8, None) == (8, 12288)  # 512 candidates a block: the piece is cut
    assert job.limits(plan(24), 8, 500)[0] == 1  # a budget that holds the samples and one thread
    assert 1 < job.limits(plan(24), 8, 1000)[0] < 8
    assert job.limits(plan(24), 3, 10_000) == (3, jr.PORTION)  # a big budget does not add threads
    small = job.memory_mb({"estimation_ids": [1], "samples": 10_000, "threads": 2})
    big = job.memory_mb({"estimation_ids": [1, 2, 3], "samples": 1_000_000, "threads": 8})
    assert 400 < small < 1000 < big and isinstance(small, int)


def test_importing_the_job_pulls_no_numba() -> None:
    """The scheduler of the core imports the handlers (BF-READY): the kernels come with the first job, not before."""
    heavy = ("numba", "pandas", "pyarrow", "duckdb", "scipy")
    code = ("import sys, importlib; importlib.import_module('bf_core.geostat.estimate.job');"
            f"print(','.join(m for m in {heavy!r} if m in sys.modules))")  # fmt: skip
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "", f"imported: {r.stdout.strip()}"


# --- nothing is left -----------------------------------------------------------------------------------------


def untouched(w: JW, version: int, fields: list[str], ident: int) -> None:
    m = u.open_model(w)
    assert m.version == version and [f["name"] for f in m.fields] == fields
    assert left_over(w) == []
    with dialect.read_tx(w.project.engine) as conn:
        assert st.get(conn, ident)["meta"]["last_run"] is None
        n = conn.execute(sa.text("select count(*) from lineage where dst_kind = 'bm_field'")).scalar()
        roles = conn.execute(sa.select(sa.func.count()).select_from(blob_owners).where(
            blob_owners.c.owner_id == w.model, blob_owners.c.role.like("fld:%"))).scalar()  # fmt: skip
    assert (n, roles) == (0, 0)


def test_a_cancel_leaves_nothing(jw: JW) -> None:
    ident = u.make(jw)
    v, names = u.version_of(jw), [f["name"] for f in u.open_model(jw).fields]
    seen = {"n": 0}

    def tick(_p: float | None = None, message: str | None = None) -> None:
        if message and message.startswith("Поиск"):
            seen["n"] += 1
            if seen["n"] >= 3:
                raise JobCancelled()

    params = {"estimation_ids": [ident], "expected_version": v, "replace": False}
    with pytest.raises(JobCancelled):
        job.run_estimate(jw.project.engine, jw.project.folder, params, tick, threads=2, chunk=700, portion=300)
    assert seen["n"] == 3
    untouched(jw, v, names, ident)
    assert run(jw, estimation_ids=[ident])["version"] == v + 1  # and it runs again afterwards


def test_a_failure_leaves_nothing(jw: JW, monkeypatch: pytest.MonkeyPatch) -> None:
    ident = u.make(jw)
    v, names = u.version_of(jw), [f["name"] for f in u.open_model(jw).fields]
    real, calls = jr.kn.estimate, {"n": 0}

    def boom(*a: Any, **k: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("сбой ядра")
        return real(*a, **k)

    monkeypatch.setattr(jr.kn, "estimate", boom)
    params = {"estimation_ids": [ident], "expected_version": v, "replace": False}
    with pytest.raises(RuntimeError):
        job.run_estimate(jw.project.engine, jw.project.folder, params, threads=2, chunk=700, portion=300)
    untouched(jw, v, names, ident)
    monkeypatch.undo()
    # a failure of the transaction after the file was written (the row went, the write is refused)
    monkeypatch.setattr(job.sto, "record_run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("запись")))
    with pytest.raises(RuntimeError):
        run(jw, estimation_ids=[ident])
    untouched(jw, v, names, ident)


def test_a_model_changed_during_the_run_is_409_and_nothing_is_left(jw: JW) -> None:
    ident = u.make(jw)
    v, names = u.version_of(jw), [f["name"] for f in u.open_model(jw).fields]
    done = {"n": 0}

    def tick(_p: float | None = None, message: str | None = None) -> None:
        if message == "Запись" and not done["n"]:
            done["n"] = 1
            add_fields(jw.project, jw.store, jw.model, [{"name": "X"}], {"X": np.zeros(u.open_model(jw).rows)})

    params = {"estimation_ids": [ident], "expected_version": v, "replace": False}
    with pytest.raises(ApiError) as e:
        job.run_estimate(jw.project.engine, jw.project.folder, params, tick, threads=1)
    assert (e.value.status, e.value.code, e.value.extra["version"]) == (409, "version_conflict", v + 1)
    assert [f["name"] for f in u.open_model(jw).fields] == [*names, "X"]
    assert left_over(jw) == []
    with dialect.read_tx(jw.project.engine) as conn:
        assert st.get(conn, ident)["meta"]["last_run"] is None


# --- the refusals before the work ----------------------------------------------------------------------------


def refused(w: JW, status: int, code: str, param: str | None = None, **over: Any) -> ApiError:
    with pytest.raises(ApiError) as e:
        run(w, **over)
    err = e.value
    assert (err.status, err.code) == (status, code), err.message
    assert err.message and err.hint and (param is None or err.extra.get("param") == param)
    return err


def test_the_refusals(jw: JW) -> None:
    a, b, plan = pair(jw)
    refused(jw, 422, "bad_param", "plan_id")  # nothing to run
    refused(jw, 422, "bad_param", "plan_id", plan_id=plan, estimation_ids=[a])
    refused(jw, 404, "estimation_not_found", "estimation_ids.1", estimation_ids=[a, b + 99])
    refused(jw, 404, "plan_not_found", plan_id=plan + 99)
    with pytest.raises(ApiError) as e:
        job.run_estimate(jw.project.engine, jw.project.folder, {"estimation_ids": [a], "expected_version": 99})
    assert (e.value.status, e.value.code, e.value.extra["version"]) == (409, "version_conflict", 1)
    run(jw, plan_id=plan)
    err = refused(jw, 409, "field_exists", estimation_ids=[a])
    assert err.extra["field"] == "AU_OK_STATUS"
    other = u.make(jw, model=jw.sub, name="Другая модель")
    refused(jw, 422, "bad_param", "estimation_ids.1", estimation_ids=[a, other], replace=True)
    with dialect.write_tx(jw.project.engine) as conn:  # the samples table is gone: «источник удалён»
        conn.execute(sa.update(dh_tables).where(dh_tables.c.id == jw.a).values(state="deleted"))
    refused(jw, 409, "source_lost", "estimation_ids.0", estimation_ids=[a], replace=True)


# --- the calculator over STATUS (ТД27-3), the graph, «Перестроить» --------------------------------------------


def test_the_calculator_reads_status_as_a_number(jw: JW) -> None:
    a = u.make(jw)
    run(jw, estimation_ids=[a])
    spec = {"model_id": jw.model, "expected_version": u.version_of(jw), "field": "ONLY", "dtype": "f8",
            "expr": "IF(AU_OK_STATUS = 0, AU_OK_EST, -1)"}  # fmt: skip
    out = calc_job.run_calc(jw.project.engine, jw.project.folder, spec)
    assert out["dtype"] == "f8"
    got = cols(jw, [STATUS, EST, "ONLY"])
    ok = got[STATUS] == 0
    assert ok.any() and (~ok).any()
    assert same(got["ONLY"][ok], got[EST][ok]) and (got["ONLY"][~ok] == -1).all()
    cond = calc_job.run_calc(jw.project.engine, jw.project.folder, {**spec, "expected_version": out["version"],
                                                                    "field": "OUT", "dtype": "i1",
                                                                    "expr": "AU_OK_STATUS = 1"})  # fmt: skip
    assert cond["dtype"] == "i1" and (cols(jw, ["OUT"])["OUT"] == (got[STATUS] == 1)).all()


def test_a_changed_table_makes_the_fields_stale_through_the_graph_and_they_are_rebuilt(jw: JW) -> None:
    from types import SimpleNamespace

    from bf_core.lineage import rebuild

    a, b, plan = pair(jw)
    run(jw, plan_id=plan)
    node = lineage.Node("bm_field", jw.model, "au_ok_est")

    def state() -> Any:
        with dialect.read_tx(jw.project.engine) as conn:
            return lineage.status(conn, [node])[node]

    assert state().state == "current"
    with dialect.write_tx(jw.project.engine) as conn:  # the table of the samples edited: rev + 1
        conn.execute(sa.update(dh_tables).where(dh_tables.c.id == jw.a).values(rev=dh_tables.c.rev + 1))
    s = state()
    assert s.state == "stale" and s.reason
    with dialect.read_tx(jw.project.engine) as conn:
        assert st.get(conn, a)["stale_reason"] == "изменена таблица «Пробы»"
        made = rebuild.make_plan(conn, node)
    owners = [(i.node.kind, i.owner.kind, i.owner.id) for i in made.items]
    assert owners[0] == ("bm_field", "object:estimation", a)
    svc = SimpleNamespace(require_current=lambda writable=False: jw.project)
    step = rebuild.prepare_step(svc, made.items[0].owner, {})
    assert step.job_kind == "estimate.run" and step.params["estimation_ids"] == [a]
    assert step.params["replace"] is True and step.params["expected_version"] == u.version_of(jw)
    job.run_estimate(jw.project.engine, jw.project.folder, step.params, threads=2)
    # the row is current by its own inputs now; what is left stale is above it: the model of the variogram
    with dialect.read_tx(jw.project.engine) as conn:
        assert st.get(conn, a)["stale"] is False
        row = lineage.status(conn, [lineage.Node("object:estimation", a)])[lineage.Node("object:estimation", a)]
        assert row.state == "stale" and row.reason.startswith("модель вариограммы")


# --- the bits ------------------------------------------------------------------------------------------------


def test_the_result_does_not_depend_on_threads_or_the_cut(jw: JW) -> None:
    a, b, plan = pair(jw)
    first = run(jw, plan_id=plan, threads=1)
    ref = cols(jw, first["fields"])
    for threads, chunk, portion in ((2, 700, 300), (4, 1 << 18, 129), (8, 333, 1000)):
        params = {"plan_id": plan, "expected_version": u.version_of(jw), "replace": True}
        job.run_estimate(jw.project.engine, jw.project.folder, params, threads=threads, chunk=chunk, portion=portion)
        got = cols(jw, first["fields"])
        assert all(same(got[n], ref[n]) for n in ref), (threads, chunk, portion)


def test_ik_writes_the_probabilities(jw: JW) -> None:
    ident = u.make(jw, method={"kind": "ik", "ik": {"thresholds": [1.0, 1.4], "models": "median"}},
                   name="Индикаторы")  # fmt: skip
    out = run(jw, estimation_ids=[ident])
    assert out["fields"] == [f"AU_IK_{k}" for k in ("STATUS", "NS", "PASS", "P1", "P2", "ETYPE")]
    got = cols(jw, out["fields"])
    inside = got["AU_IK_STATUS"] == 0
    p1, p2 = got["AU_IK_P1"][inside], got["AU_IK_P2"][inside]
    assert inside.any() and ((p1 >= 0) & (p2 <= 1) & (p1 <= p2 + 1e-6)).all()
    assert np.isnan(got["AU_IK_P1"][~inside]).all()


# --- a worker (`spawn`) and the database ---------------------------------------------------------------------


def test_a_worker_runs_the_job(monkeypatch: pytest.MonkeyPatch, pclient: Any) -> None:
    from bf_core.jobs.registry import HANDLER_MODULES
    from bf_core.jobs.service import jobs_of

    if job.__name__ not in HANDLER_MODULES:  # until the line of the orchestrator (jobs/registry.py) is in
        monkeypatch.setenv("BF_EXTRA_JOB_MODULES", job.__name__)
    importlib.import_module(job.__name__)
    assert pclient.post(API + "/projects", json={"name": "Оценка"}).status_code == 200
    project = projects_of(pclient.core).require_current()
    w = u.fill(project)
    ident = u.make(w)
    key, job_id = jobs_of(pclient.core).submit("estimate.run", {"estimation_ids": [ident],
                                                                "expected_version": u.version_of(w),
                                                                "replace": False}, title="Оценка")  # fmt: skip
    done = wait_job(pclient, key, job_id, timeout=240)
    assert done["status"] == "done", done
    res = done["result"]
    assert res["fields"][0] == STATUS and res["version"] == 2 and res["rows"][0]["estimation_id"] == ident
    assert same(cols(w, [EST])[EST], u.direct(w, ident)["EST"] * np.where(cols(w, ["DOM"])["DOM"] == 0, 1.0, np.nan))
    with dialect.read_tx(project.engine) as conn:
        assert st.get(conn, ident)["meta"]["last_run"]["job"] == job_id


@pytest.mark.db
def test_the_job_on_the_database(db_url: str, tmp_path: Path) -> None:
    """The write of the fields, `record_run` and the graph on SQLite and PostgreSQL (`-m db`; BF_TEST_PG)."""
    w = u.world(tmp_path, db_url)
    try:
        a, b, plan = pair(w)
        out = run(w, plan_id=plan)
        assert sum(out["rows"][0]["counts"]["by_status"]) == u.open_model(w).rows
        assert same(cols(w, [EST])[EST], merged_expected(w, a, b, "EST"))
        with dialect.read_tx(w.project.engine) as conn:
            node = lineage.Node("bm_field", w.model, "au_ok_est")
            assert lineage.status(conn, [node])[node].state == "current"
        u.put(w, a, disc=[1, 1, 1])
        run(w, estimation_ids=[a], replace=True)
        dom = cols(w, ["DOM"])["DOM"]
        assert same(cols(w, [EST])[EST][dom == 0], u.direct(w, a)["EST"][dom == 0])
        assert same(cols(w, [EST])[EST][dom == 1], u.direct(w, b)["EST"][dom == 1])
        assert left_over(w) == []
    finally:
        w.project.engine.dispose()
