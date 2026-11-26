"""The control of a resource report (подшаг 20.3; ТЗ 8.10 «Контроль», 7.35 Д-27; ADR 0027 decision 13).

Σ of the groups − the total (≤ 1e-12, and the check does find a break: a bit of the order of the sums, a lost group),
the blocks with no density (their volume, not a zero tonnage), the tonnes with `DENS_SRC` = 1, the blocks with no grade,
**ТД27-2** (the volume of the status 1 inside the restrictor = Σ of the volumes of those blocks, 1e-12; the share of the
volume of the restrictor; a partial share f of the restrictor), the volume of the restrictor against the wireframe
(a warning over 1 %, a wireframe off the model, the leaf by the centre and by the share), the cancel of the job."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks import restrict
from bf_core.blocks.model import Model
from bf_core.db import dialect
from bf_core.geostat.estimate.spec_parts import STATUS_LABELS
from bf_core.projects.service import projects_of
from bf_core.resources import compute as cp
from bf_core.resources import control as ct
from bf_core.resources import result as rr
from bf_core.resources.spec import parse_params
from bf_core.storage.blobs import BlobStore
from bf_core.wireframes import store as wf
from tests.test_blocks_report import put
from tests.test_fill_octree import box
from tests.test_resources_result import CLASS, close, random_case


@pytest.fixture()
def rc(pclient):  # type: ignore[no-untyped-def]
    assert pclient.post("/api/v1/projects", json={"name": "Ресурсы"}).status_code == 200
    pclient.project = projects_of(pclient.core).require_current()
    pclient.store = BlobStore(pclient.project.folder)
    return pclient


def run_control(c: Any, mid: int, body: dict[str, Any], **kw: Any) -> tuple[dict[str, Any], rr.Plan, cp.Sums]:
    """The pass and the control in one read transaction (the leaves of a wireframe read the project)."""
    store = BlobStore(c.project.folder)
    with dialect.read_tx(c.project.engine) as conn:
        model = Model.open(conn, store, mid)
        spec = parse_params({"model_id": mid, **body}).resolved(model.fields, rr.z_range_of(model))
        plan = rr.core_from_spec(model, spec)
        r = restrict.prepare(model, spec.restrict, conn=conn, store=store)
        sums = cp.run(model, plan.core, r)
        out = ct.build(model, plan, sums, restrictor=r, rules=spec.restrict, conn=conn, store=store, **kw)
    return out, plan, sums


GRID = {"corner": [0.0, 0.0, 0.0], "counts": [4, 3, 2], "size": [10.0, 10.0, 10.0]}
N = 24


def estimate_model(c: Any, seed: int = 11) -> tuple[int, dict[str, Any]]:
    """An estimated model: the grade AU_OK_EST (empty where the status is not 0), AU_OK_STATUS 0–3, a domain, the
    density with gaps and the flag DENS_SRC."""
    rng = np.random.Generator(np.random.PCG64(seed))
    status = rng.integers(0, 4, N).astype(np.int32)
    status[:6] = 1  # enough blocks of the status 1 in the domain
    est = np.where(status == 0, rng.lognormal(0.0, 0.6, N), np.nan)
    dom = np.array([0, 1] * (N // 2), dtype=np.int32)  # A, B, A, B, …
    sg = rng.uniform(2.0, 3.0, N)
    sg[[2, 9]] = np.nan
    src = (rng.random(N) < 0.3).astype(float)
    fields = [
        {"name": "AU_OK_EST", "dtype": "f8", "unit": "g/t", "source": {"op": "estimate"}},
        {"name": "AU_OK_STATUS", "dtype": "cat", "labels": list(STATUS_LABELS), "source": {"op": "estimate"}},
        {"name": "DOM", "dtype": "cat", "labels": ["A", "B"]},
        {"name": "SG", "dtype": "f8", "role": "density"},
        {"name": "DENS_SRC", "dtype": "i1"},
    ]
    cols = {"AU_OK_EST": est, "AU_OK_STATUS": status, "DOM": dom, "SG": sg, "DENS_SRC": src}
    mid = put(c, GRID, fields, np.arange(N, dtype=np.uint64), cols)
    return mid, {"est": est, "status": status, "dom": dom, "sg": sg, "src": src, "vol": 1000.0}


BODY = {"elements": ["AU_OK_EST"], "cutoffs": [None, 0.5], "density": "SG", "groups": ["DOM"], "curve": 0}


# --- Σ of the groups − the total --------------------------------------------------------------------------------------


def test_sum_of_the_groups_is_the_total_and_the_check_finds_a_break(rc) -> None:  # type: ignore[no-untyped-def]
    mid, body = random_case(rc, 5)
    ctl, plan, sums = run_control(rc, mid, body)
    sg = ctl["sum_groups"]
    assert sg["checked"] and sg["ok"] and sg["tonnes"]["rel"] <= ct.TOLERANCE and sg["volume"]["rel"] <= ct.TOLERANCE
    assert sg["tonnes"]["total"] > 0 and set(sg["metal"]) == {"AU", "CU"}
    assert not [w for w in ctl["warnings"] if w["code"] == "sum_mismatch"]
    # a break above the norm: the tonnes of one group off by 1e-9 → found, the warning is Russian with a hint
    broken = sums.sg.copy()
    sums.sg[np.argmax(sums.sg[:, 1]), 1] *= 1.0 + 1e-9
    sums.drop_memo()  # the tables of the sums are cached: a test that tampers with the accumulators says so
    bad = ct.sum_groups(sums, plan)
    assert not bad["ok"] and 1e-10 < bad["tonnes"]["rel"] < 1e-8
    sums.sg[:] = broken
    sums.drop_memo()
    # a lost group (its numbers gone): found; a drift under the norm (1e-14): not a break
    lost = int(np.argmax(sums.sg[:, 3]))
    saved = sums.sg[lost].copy(), sums.cg[lost].copy()
    sums.sg[lost] = 0.0
    sums.cg[lost] = 0.0
    sums.drop_memo()
    assert not ct.sum_groups(sums, plan)["ok"]
    sums.sg[lost], sums.cg[lost] = saved
    sums.sg[np.argmax(sums.sg[:, 1]), 1] *= 1.0 + 1e-14
    sums.drop_memo()
    assert ct.sum_groups(sums, plan)["ok"]


def test_a_broken_sum_is_a_warning_of_the_control(rc, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mid, body = random_case(rc, 2)
    real = ct.sum_groups

    def broken(sums: cp.Sums, plan: rr.Plan) -> dict[str, Any]:
        sums.sg[:, 1] *= 1.0 + 1e-6
        return real(sums, plan)

    monkeypatch.setattr(ct, "sum_groups", broken)
    ctl, _, _ = run_control(rc, mid, body)
    (w,) = [x for x in ctl["warnings"] if x["code"] == "sum_mismatch"]
    assert not ctl["sum_groups"]["ok"] and w["message"] and w["hint"]


def test_no_keys_nothing_to_compare(rc) -> None:  # type: ignore[no-untyped-def]
    mid, _ = estimate_model(rc)
    ctl, _, _ = run_control(rc, mid, {**BODY, "groups": []})
    assert ctl["sum_groups"] == {"checked": False, "tonnes": None, "volume": None, "metal": {"AU_OK_EST": None},
                                 "ok": True}  # fmt: skip


# --- no density, default density, no grade ----------------------------------------------------------------------------


def test_no_density_blocks_and_volume_not_zero_tonnes(rc) -> None:  # type: ignore[no-untyped-def]
    mid, m = estimate_model(rc)
    ctl, _, sums = run_control(rc, mid, BODY)
    assert ctl["no_density"] == {"blocks": 2, "volume": 2000.0}
    (w,) = [x for x in ctl["warnings"] if x["code"] == "no_density"]
    assert "2" in w["message"] and w["hint"]
    total = sums.s0[:, 1].sum() + sums.c0[:, 1].sum()
    assert close(total, float(np.nansum(m["sg"]) * 1000.0))  # the tonnage knows nothing of the two blocks
    # a number as the density: every block has one
    ctl2, _, _ = run_control(rc, mid, {**BODY, "density": 2.5})
    assert ctl2["no_density"] == {"blocks": 0, "volume": 0.0} and not ctl2["warnings"]


def test_default_density_tonnes_by_dens_src(rc) -> None:  # type: ignore[no-untyped-def]
    mid, m = estimate_model(rc)
    ctl, _, _ = run_control(rc, mid, BODY)
    sel = (m["src"] == 1) & np.isfinite(m["sg"])
    assert ctl["default_density"]["blocks"] == int(sel.sum()) > 0
    assert close(ctl["default_density"]["tonnes"], float(np.sum(m["sg"][sel]) * 1000.0))
    # no field — null (the model of the property has no DENS_SRC)
    mid2, body2 = random_case(rc, 8)
    ctl2, _, _ = run_control(rc, mid2, body2)
    assert ctl2["default_density"] is None and ctl2["status"] == {} and ctl2["wireframes"] == []


def test_no_grade_per_element(rc) -> None:  # type: ignore[no-untyped-def]
    mid, m = estimate_model(rc)
    ctl, _, _ = run_control(rc, mid, BODY)
    miss = np.isnan(m["est"]) & np.isfinite(m["sg"])
    ng = ctl["no_grade"]["AU_OK_EST"]
    assert ng["blocks"] == int(miss.sum()) and close(ng["tonnes"], float(np.sum(m["sg"][miss]) * 1000.0))


# --- ТД27-2: the status of the estimate -------------------------------------------------------------------------------


def status_expected(m: dict[str, Any], f: np.ndarray) -> dict[int, tuple[int, float]]:
    return {s: (int(((m["status"] == s) & (f > 0)).sum()), float(np.sum(1000.0 * f[(m["status"] == s) & (f > 0)])))
            for s in (1, 2, 3)}  # fmt: skip


def test_td27_2_volume_of_the_status_1_inside_the_restrictor(rc) -> None:  # type: ignore[no-untyped-def]
    mid, m = estimate_model(rc)
    # no restrictor: the whole model
    ctl, _, _ = run_control(rc, mid, BODY)
    st = ctl["status"]["AU_OK_EST"]
    assert st["prefix"] == "AU_OK_" and [i["code"] for i in st["items"]] == [1, 2, 3]
    assert [i["label"] for i in st["items"]] == ["вне домена", "мало проб", "вырожденная система"]
    want = status_expected(m, np.ones(N))
    for item in st["items"]:
        assert item["blocks"] == want[item["code"]][0] and close(item["volume"], want[item["code"]][1], 1e-12)
        assert close(item["share"], item["volume"] / (N * 1000.0))
    assert want[1][0] >= 6
    # the restrictor: the domain A only — the status 1 of the blocks of A (the volume of V·f of the blocks)
    rules = {"cond": {"field": "DOM", "op": "=", "value": "A"}}
    ctl, _, _ = run_control(rc, mid, {**BODY, "restrict": rules})
    f = (m["dom"] == 0).astype(float)
    got = {i["code"]: i for i in ctl["status"]["AU_OK_EST"]["items"]}
    want = status_expected(m, f)
    assert got[1]["blocks"] == want[1][0] > 0 and close(got[1]["volume"], want[1][1], 1e-12)
    assert close(got[1]["share"], want[1][1] / float(f.sum() * 1000.0), 1e-12)  # of the volume of the restrictor
    # a partial share of the restrictor (a slab by the share: half of the height of every layer): V·f
    slab = {"slab": {"axis": "z", "from": 5.0, "to": 15.0, "by": "share"}}
    both = {"op": "and", "items": [rules, slab]}
    ctl, _, _ = run_control(rc, mid, {**BODY, "restrict": both})
    z = np.arange(N) // 12  # the layer of a block (4 × 3 blocks in a layer)
    f = (m["dom"] == 0) * 0.5 * np.ones(N)
    assert set(z) == {0, 1}
    got = {i["code"]: i for i in ctl["status"]["AU_OK_EST"]["items"]}
    want = status_expected(m, f)
    assert close(got[1]["volume"], want[1][1], 1e-12) and got[1]["blocks"] == want[1][0]
    assert close(got[2]["volume"], want[2][1], 1e-12) and close(got[3]["volume"], want[3][1], 1e-12)


def test_status_only_for_the_elements_of_an_estimate(rc) -> None:  # type: ignore[no-untyped-def]
    """The element that is not a field of an estimate has no status; two elements of one prefix share the numbers."""
    mid, m = estimate_model(rc)
    fields = [
        {"name": "AU", "dtype": "f8", "unit": "g/t"},
        {"name": "AU_OK_EST", "dtype": "f8", "unit": "g/t", "source": {"op": "estimate"}},
        {"name": "AU_OK_KV", "dtype": "f8", "source": {"op": "estimate"}},
        {"name": "AU_OK_STATUS", "dtype": "cat", "labels": list(STATUS_LABELS), "source": {"op": "estimate"}},
        {"name": "SG", "dtype": "f8", "role": "density"},
    ]
    cols = {"AU": np.full(N, 1.0), "AU_OK_EST": m["est"], "AU_OK_KV": np.full(N, 0.5), "AU_OK_STATUS": m["status"],
            "SG": np.full(N, 2.5)}  # fmt: skip
    mid2 = put(rc, GRID, fields, np.arange(N, dtype=np.uint64), cols, name="Две")
    ctl, _, _ = run_control(rc, mid2, {"elements": ["AU", "AU_OK_EST", "AU_OK_KV"], "cutoffs": [None],
                                       "density": "SG", "curve": 0})  # fmt: skip
    assert set(ctl["status"]) == {"AU_OK_EST", "AU_OK_KV"}
    assert ctl["status"]["AU_OK_EST"]["items"] == ctl["status"]["AU_OK_KV"]["items"]


# --- the volume of the restrictor against the wireframe ---------------------------------------------------------------


def plain_model(rc_: Any) -> int:
    n = 4 * 4 * 4
    fields = [{"name": "AU", "dtype": "f8", "unit": "g/t"}, CLASS]
    grid = {"corner": [0.0, 0.0, 0.0], "counts": [4, 4, 4], "size": [10.0, 10.0, 10.0]}
    cols = {"AU": np.full(n, 1.5), "CLASS": np.zeros(n, dtype=np.int32)}
    return put(rc_, grid, fields, np.arange(n, dtype=np.uint64), cols)


def add_box(c: Any, lo: tuple[float, ...], hi: tuple[float, ...], name: str) -> int:
    v, t = box(lo, hi)
    with dialect.write_tx(c.project.engine) as conn:
        return wf.put_wireframe(conn, c.store, v, t, name=name)


def wire_rules(oid: int, **kw: Any) -> dict[str, Any]:
    return {"wireframe": {"id": oid, "side": "inside", "by": "frac", "m": 4, **kw}}


WBODY = {"elements": ["AU"], "cutoffs": [None], "density": 2.5, "curve": 0}


def test_wireframe_volume_matches_with_the_boundary_in_a_block(rc) -> None:  # type: ignore[no-untyped-def]
    mid = plain_model(rc)
    oid = add_box(rc, (0.0, 0.0, 0.0), (25.0, 20.0, 20.0), "Руда")  # 25 m: the boundary cuts the third block in half
    ctl, _, _ = run_control(rc, mid, {**WBODY, "restrict": wire_rules(oid)})
    (w,) = ctl["wireframes"]
    assert w["wireframe_id"] == oid and w["name"] == "Руда" and close(w["volume_wireframe"], 25 * 20 * 20.0)
    assert close(w["volume_restrictor"], 10_000.0) and w["diff_pct"] < 1e-6
    assert not w["warning"] and not w["outside_extent"] and not ctl["warnings"]


def test_wireframe_by_the_centre_differs_over_one_percent_is_a_warning(rc) -> None:  # type: ignore[no-untyped-def]
    mid = plain_model(rc)
    oid = add_box(rc, (0.0, 0.0, 0.0), (25.0, 20.0, 20.0), "Руда")
    ctl, _, _ = run_control(rc, mid, {**WBODY, "restrict": wire_rules(oid, by="centre")})
    (w,) = ctl["wireframes"]
    # the centre of the third block (x = 25) is on the boundary: the blocks whose centre is inside are 2 × 2 × 2
    assert w["volume_restrictor"] in (8000.0, 12_000.0)
    assert close(w["diff_pct"], abs(w["volume_restrictor"] - 10_000.0) / 100.0) and w["warning"]
    (warn,) = [x for x in ctl["warnings"] if x["code"] == "wireframe_volume"]
    assert "Руда" in warn["message"] and "20" in warn["message"] and warn["hint"]


def test_wireframe_off_the_model_and_in_a_tree_with_other_rules(rc) -> None:  # type: ignore[no-untyped-def]
    mid = plain_model(rc)
    far = add_box(rc, (500.0, 500.0, 0.0), (520.0, 520.0, 20.0), "Далеко")
    near = add_box(rc, (0.0, 0.0, 0.0), (20.0, 20.0, 20.0), "Ближе")
    ors = {"op": "or", "items": [wire_rules(far), wire_rules(near), wire_rules(near)]}
    tree = {"op": "and", "items": [{"cond": {"field": "AU", "op": ">", "value": 1.0}}, ors]}
    ctl, _, _ = run_control(rc, mid, {**WBODY, "restrict": tree})
    by = {w["wireframe_id"]: w for w in ctl["wireframes"]}
    assert set(by) == {far, near}  # the same wireframe twice is checked once
    assert by[far]["outside_extent"] and by[far]["volume_restrictor"] == 0.0 and by[far]["warning"]
    assert not by[near]["outside_extent"] and by[near]["diff_pct"] < 1e-6
    codes = [x["code"] for x in ctl["warnings"]]
    assert "wireframe_outside" in codes and codes.count("wireframe_volume") == 1
    # without the connection the wireframes are not looked at
    assert ct.wire_leaves(tree)[0]["id"] == far and len(ct.wire_leaves(tree)) == 2
    assert ct.wire_leaves(None) == [] and ct.wire_leaves({"cond": {"field": "AU", "op": ">", "value": 1}}) == []


# --- the job: cancel, progress ----------------------------------------------------------------------------------------


def test_cancel_stops_the_extra_pass_and_progress_is_reported(rc) -> None:  # type: ignore[no-untyped-def]
    mid, _ = estimate_model(rc)
    seen: list[float | None] = []
    run_control(rc, mid, BODY, tick=lambda p, _m: seen.append(p))
    assert seen and all(0.0 <= (p or 0.0) <= 1.0 for p in seen)

    def cancel() -> None:
        raise ApiError(409, "cancelled", "Задание отменено.", "Запустите расчёт снова.")

    with pytest.raises(ApiError) as e:
        run_control(rc, mid, BODY, cancel=cancel)
    assert e.value.code == "cancelled"
