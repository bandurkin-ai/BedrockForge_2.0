"""The samples of a row of the estimation plan (ТЗ 8.8.2; Т8.8-9; Э18, 18.5): a gap is not a zero, the top cut, the
weights, the domains (D, S, the others), the averaging of coincident samples, the canonical order (a permutation of
the input — bitwise the same samples; a shift — 1e−9), the declustered mean, the errors, 100 thousand samples."""

from __future__ import annotations

import math
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.db import dialect, migrate
from bf_core.drillholes import store as dh
from bf_core.geostat.estimate import samples as sm
from bf_core.geostat.estimate.spec_parts import parse_samples
from tests.dbutil import make_engine


def noop(_p: float | None, _m: str | None) -> None:
    pass


# --- a small project -------------------------------------------------------------------------------------------

COLLARS = [("H1", 0.0, 0.0), ("H2", 10.0, 0.0), ("H3", 0.0, 10.0), ("H4", 10.0, 10.0)]  # vertical, Z = 100
NAN = None
ASSAY = [  # hole, from, to, AU, AU_CUT, DECL_W, ZONE, ZN
    ("H1", 0.0, 1.0, 1.0, 1.0, 0.5, "A", 1.0),
    ("H1", 1.0, 2.0, 0.0, 0.0, 1.0, "A", 1.0),  # a real zero: it stays
    ("H1", 2.0, 3.0, NAN, NAN, 1.0, "A", 1.0),  # a gap: not a zero, not used
    ("H2", 0.0, 1.0, 40.0, 30.0, 2.0, "A", 1.0),  # cut 40 → 30
    ("H2", 1.0, 2.0, 3.0, 3.0, NAN, "B", 2.0),  # no weight; domain B (soft)
    ("H3", 0.0, 1.0, 5.0, 5.0, 0.0, "C", 3.0),  # weight 0 is no weight; domain C (another)
    ("H3", 1.0, 2.0, 2.0, 2.0, 1.5, "A", 1.0),
    ("H4", 0.0, 1.0, 4.0, 4.0, 1.0, None, 1.0),  # no domain
    ("H5", 0.0, 1.0, 9.0, 9.0, 1.0, "A", 1.0),  # no collar: no position
]
ZONES = [  # an interval table of domains: hole, from, to, ZONE2
    ("H1", 0.0, 3.0, "окисл"),
    ("H2", 0.0, 1.0, "окисл"),
    ("H2", 1.0, 2.0, "перех"),
    ("H3", 0.0, 2.0, "перв"),
]


def table(name: str, kind: str, rows: list[Any], cols: list[tuple[str, str]]) -> dh.TableData:
    return dh.TableData(
        name=name, kind=kind, holes=[r[0] for r in rows], from_m=[r[1] for r in rows], to_m=[r[2] for r in rows],
        columns=[dh.ColumnSpec(n, k, None, "component" if k == "number" else "attribute") for n, k in cols],
        values=[[r[3 + i] for r in rows] for i in range(len(cols))],
    )  # fmt: skip


@pytest.fixture()
def data(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = make_engine("sqlite", tmp_path)
    migrate.upgrade(engine, "project")
    with dialect.write_tx(engine) as conn:
        set_id = dh.create_set(conn, "Скважины")
        dh.replace_collars(conn, set_id, [dh.CollarRow(h, x=x, y=y, z=100.0, td=10.0, azimuth=0.0, dip=-90.0)
                                          for h, x, y in COLLARS])  # fmt: skip
    cols = [("AU", "number"), ("AU_CUT", "number"), ("DECL_W", "number"), ("ZONE", "text"), ("ZN", "number")]
    dh.write_table(engine, set_id, table("Пробы", "assay", ASSAY, cols), job_id=None, tick=noop)
    dh.write_table(engine, set_id, table("Зоны", "interval", ZONES, [("ZONE2", "text")]), job_id=None, tick=noop)
    project = SimpleNamespace(engine=engine, uid=f"est-samples:{tmp_path}", folder=tmp_path)
    yield project, set_id
    engine.dispose()


def sel(set_id: int, **over: Any) -> Any:
    body: dict[str, Any] = {"set_id": set_id, "table": "пробы", "field": "AU", "weights": False}
    body.update(over)
    return parse_samples(body)


# --- a gap is not a zero; the counts ------------------------------------------------------------------------------


def test_gap_is_not_zero_and_counted(data) -> None:  # type: ignore[no-untyped-def]
    project, set_id = data
    s = sm.load(project, sel(set_id))
    # 9 rows: one gap (H1 2–3), no position (H5), no domain filter: 7 samples
    assert s.n == 7 and s.counts["rows"] == 9 and s.counts["missing"] == 1 and s.counts["no_position"] == 1
    assert s.counts["outside_domain"] == 0 and s.counts["selected"] == 7 and s.counts["used"] == 7
    assert 0.0 in s.z.tolist() and not np.isnan(s.z).any()  # the real zero stays, the gap is gone
    assert sorted(s.z.tolist()) == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 40.0] and (s.dom == sm.D).all()
    assert s.holes == ["H1", "H2", "H3", "H4"] and s.w is None and s.means["declus"] is None
    assert s.means["naive"] == pytest.approx(55.0 / 7)
    # the middle of the interval on the vertical hole; the origin — the centre of the extent
    i = int(np.flatnonzero((s.xyz[:, 0] == 10.0) & (abs(s.xyz[:, 1]) < 1e-9) & (s.z == 40.0))[0])
    assert np.allclose(s.xyz[i], [10.0, 0.0, 99.5], rtol=0, atol=1e-12) and s.span[i].tolist() == [0.0, 1.0]
    assert np.allclose(s.origin, [5.0, 5.0, 99.0], rtol=0, atol=1e-12) and np.array_equal(s.local, s.xyz - s.origin)
    assert s.row_id.dtype == np.int64 and len(set(s.row_id.tolist())) == 7 and (s.merged == 1).all()
    kinds = [d["kind"] for d in s.inputs]
    assert kinds == ["table", "trajectories"] and s.inputs[0]["name_key"] == "пробы"


def test_all_gaps_is_an_error(data) -> None:  # type: ignore[no-untyped-def]
    project, set_id = data
    with pytest.raises(ApiError) as e:
        sm.load(project, sel(set_id, domain={"field": "ZONE", "values": ["нет такой"]}))
    assert e.value.status == 422 and e.value.code == "no_samples"
    assert e.value.extra["counts"]["outside_domain"] == 7 and "Проверьте" in e.value.hint
    with pytest.raises(ApiError) as e:  # the domain of another table: H3 is «перв» but its samples have no value > …
        sm.load(project, sel(set_id, domain={"table": "зоны", "field": "ZONE2", "values": ["нет"]}))
    assert e.value.code == "no_samples" and e.value.extra["param"] == "samples"


def test_errors_name_the_param(data) -> None:  # type: ignore[no-untyped-def]
    project, set_id = data
    for body, code, param in [
        ({"field": "PB"}, "field_not_found", "samples.field"),
        ({"field": "ZONE"}, "field_not_number", "samples.field"),
        ({"weights": "nope"}, "field_not_found", "samples.weights"),
        ({"topcut": {"field": "nope"}}, "field_not_found", "samples.topcut.field"),
        ({"domain": {"field": "nope", "values": ["A"]}}, "field_not_found", "samples.domain.field"),
    ]:
        with pytest.raises(ApiError) as e:
            sm.load(project, sel(set_id, **body))
        assert e.value.status == 422 and e.value.code == code and e.value.extra["param"] == param, body
    with pytest.raises(ApiError) as e:
        sm.load(project, sel(set_id, table="керн"))
    assert e.value.status == 404 and e.value.code == "table_not_found"


# --- the top cut; the weights ----------------------------------------------------------------------------------


def test_topcut_value_and_field(data) -> None:  # type: ignore[no-untyped-def]
    project, set_id = data
    by_value = sm.load(project, sel(set_id, topcut={"value": 30}))
    assert by_value.z.max() == 30.0 and by_value.raw.max() == 40.0 and by_value.counts["capped"] == 1
    by_field = sm.load(project, sel(set_id, topcut={"field": "au_cut"}))
    assert np.array_equal(by_field.z, by_value.z) and np.array_equal(by_field.raw, by_value.raw)
    assert by_field.counts["capped"] == 1 and by_field.counts["missing"] == 1
    none = sm.load(project, sel(set_id))
    assert none.z.max() == 40.0 and none.counts["capped"] == 0
    # a threshold above everything cuts nothing; a gap stays a gap
    high = sm.load(project, sel(set_id, topcut={"value": 1e6}))
    assert np.array_equal(high.z, none.z) and high.counts["capped"] == 0 and high.counts["missing"] == 1


def test_weights_decl_w_by_default(data) -> None:  # type: ignore[no-untyped-def]
    project, set_id = data
    default = sm.load(project, sel(set_id, weights=None))  # DECL_W of the table
    assert default.w is not None
    named = sm.load(project, sel(set_id, weights="decl_w"))
    assert np.array_equal(default.w, named.w, equal_nan=True)
    assert sm.load(project, sel(set_id, weights=False)).w is None
    # 7 samples: weights 0.5, 1, 2, NaN, NaN (0 is none), 1.5, 1 → two without a weight
    assert default.counts["no_weight"] == 2 and int(np.isnan(default.w).sum()) == 2
    ok = np.isfinite(default.w)
    expect = float(np.sum(default.w[ok] * default.z[ok]) / np.sum(default.w[ok]))
    assert default.means["declus"] == pytest.approx(expect, rel=1e-15)
    assert sm.declus_mean(default.z, default.w) == pytest.approx(expect, rel=1e-15)
    assert sm.declus_mean(np.array([1.0, 2.0]), np.array([np.nan, 0.0])) is None
    assert sm.declus_mean(np.ones(2), None) is None
    # the cut value goes into the declustered mean
    cut = sm.load(project, sel(set_id, weights=None, topcut={"value": 30}))
    assert cut.means["declus"] < default.means["declus"]


# --- the domains -----------------------------------------------------------------------------------------------


def test_domain_text_soft_and_others(data) -> None:  # type: ignore[no-untyped-def]
    project, set_id = data
    s = sm.load(project, sel(set_id, domain={"field": "ZONE", "values": ["A"]}))
    assert s.n == 4 and (s.dom == sm.D).all() and s.counts["outside_domain"] == 3  # B, C, none
    soft = sm.load(project, sel(set_id, domain={"field": "ZONE", "values": ["A"]},
                                soft={"domains": ["B"], "dist": 5}))  # fmt: skip
    assert soft.n == 5 and soft.counts["d"] == 4 and soft.counts["s"] == 1 and soft.counts["outside_domain"] == 2
    assert soft.z[soft.dom == sm.S].tolist() == [3.0]
    assert soft.means["naive"] == pytest.approx((1.0 + 0.0 + 40.0 + 2.0) / 4)  # S is not in the mean of D
    everything = sm.load(project, sel(set_id, domain={"field": "ZONE", "values": ["A"]},
                                      soft={"domains": ["B"], "dist": 5}), all_domains=True)  # fmt: skip
    assert everything.n == 6 and everything.dom_labels == ["C"] and everything.counts["others"] == {"C": 1}
    assert everything.z[everything.dom == 2].tolist() == [5.0] and everything.counts["outside_domain"] == 1


def test_domain_number_and_other_table(data) -> None:  # type: ignore[no-untyped-def]
    project, set_id = data
    s = sm.load(project, sel(set_id, domain={"field": "ZN", "values": ["1"]}))  # numeric field: format(x, ".15g")
    assert s.n == 5 and (s.dom == sm.D).all()
    s = sm.load(project, sel(set_id, domain={"table": "зоны", "field": "ZONE2", "values": ["окисл"]}))
    # the middle of the sample in the interval of the other table: H1 0–1, 1–2 (2–3 is a gap), H2 0–1
    assert s.n == 3 and sorted(s.z.tolist()) == [0.0, 1.0, 40.0] and s.counts["outside_domain"] == 4
    assert [d["kind"] for d in s.inputs] == ["table", "table", "trajectories"]
    soft = sm.load(project, sel(set_id, domain={"table": "зоны", "field": "ZONE2", "values": ["окисл"]},
                                soft={"domains": ["перех"], "dist": 2}))  # fmt: skip
    assert soft.n == 4 and soft.z[soft.dom == sm.S].tolist() == [3.0]


def test_origin_given_and_cancel(data) -> None:  # type: ignore[no-untyped-def]
    project, set_id = data
    base = sm.load(project, sel(set_id))
    fixed = sm.load(project, sel(set_id), origin=np.array([1.0, 2.0, 3.0]))
    assert fixed.origin.tolist() == [1.0, 2.0, 3.0] and np.array_equal(fixed.xyz, base.xyz)
    assert np.array_equal(fixed.local, base.xyz - fixed.origin)
    calls: list[int] = []

    def cancel() -> None:
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError):
        sm.load(project, sel(set_id), cancel=cancel)
    assert len(calls) == 3
    sub = base.take(np.array([0, 2, 5]))
    assert sub.n == 3 and sub.means == base.means and sub.origin is base.origin
    assert sub.z.tolist() == base.z[[0, 2, 5]].tolist()


# --- arrays: order, averaging, Т8.8-9 --------------------------------------------------------------------------


def arrays(n: int, seed: int = 7, dup: int = 0) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    xyz = rng.uniform(0.0, 500.0, (n, 3))
    z = np.round(rng.lognormal(0.0, 1.0, n), 3)
    hole = rng.integers(0, 12, n).astype(np.int32)
    dom = (rng.random(n) < 0.2).astype(np.int16)
    if dup:  # coincident samples of one domain (a copy 3e−7 m away), and of another one (a copy of another domain)
        src = rng.choice(n, dup, replace=False)
        xyz = np.vstack((xyz, xyz[src] + 3e-7))
        z = np.concatenate((z, z[src] + 1.0))
        hole = np.concatenate((hole, (hole[src] + 1) % 12))
        dom = np.concatenate((dom, dom[src]))
        n += dup
    return {"xyz": xyz, "z": z, "raw": z + 0.5, "w": rng.uniform(0.2, 2.0, n), "hole": hole,
            "holes": [f"H{k:02d}" for k in range(12)], "dom": dom, "row_id": np.arange(1000, 1000 + n, dtype=np.int64),
            "span": np.column_stack((np.arange(n), np.arange(n) + 1.0)).astype(np.float64)}  # fmt: skip


def run(a: dict[str, Any], order: np.ndarray | None = None, shift: Any = None) -> sm.RowSamples:
    o = np.arange(a["z"].shape[0]) if order is None else order
    xyz = a["xyz"][o] + (0.0 if shift is None else np.asarray(shift))
    return sm.prepare(xyz, a["z"][o], a["raw"][o], a["w"][o], a["hole"][o], a["holes"], a["dom"][o],
                      a["row_id"][o], a["span"][o])  # fmt: skip


def same(a: sm.RowSamples, b: sm.RowSamples) -> bool:
    for name in ("xyz", "local", "z", "raw", "w", "hole", "dom", "row_id", "span", "merged", "origin"):
        x, y = getattr(a, name), getattr(b, name)
        if x.dtype != y.dtype or x.tobytes() != y.tobytes():
            return False
    return a.holes == b.holes and a.means == b.means and a.counts == b.counts


def test_t8_8_9_permutation_bitwise_and_shift() -> None:
    a = arrays(400, dup=30)
    base = run(a)
    rng = np.random.default_rng(1)
    for _ in range(8):
        assert same(base, run(a, rng.permutation(a["z"].shape[0])))
    assert base.counts["merged_groups"] == 30 and base.n == 400  # every copy joined its original
    # the shift of Т8.8-9: the origin follows the extent, the local coordinates agree to 1e−9
    moved = run(a, shift=[5e5, 7e6, 0.0])
    assert np.array_equal(moved.z, base.z) and np.array_equal(moved.dom, base.dom)
    assert np.array_equal(moved.row_id, base.row_id) and np.array_equal(moved.hole, base.hole)
    assert np.allclose(moved.local, base.local, rtol=0.0, atol=1e-9)
    assert np.allclose(moved.origin - base.origin, [5e5, 7e6, 0.0], rtol=0.0, atol=1e-9)
    assert moved.means["naive"] == base.means["naive"]
    assert moved.means["declus"] == pytest.approx(base.means["declus"], rel=1e-9)  # type: ignore[arg-type]


def test_t8_8_9_four_samples_permutation() -> None:
    xyz = np.array([[10.0, 10.0, 0.0], [-10.0, 10.0, 0.0], [10.0, -10.0, 0.0], [-10.0, -10.0, 0.0]])
    z = np.array([1.0, 2.0, 3.0, 4.0])
    kw: dict[str, Any] = {"w": None, "hole": np.arange(4, dtype=np.int32), "holes": list("abcd"),
                          "dom": np.zeros(4, np.int16), "row_id": np.arange(4), "span": np.zeros((4, 2))}  # fmt: skip
    ref = sm.prepare(xyz, z, None, **kw)
    # the canonical order: X, then Y, then Z: (−10, −10), (−10, 10), (10, −10), (10, 10)
    assert ref.xyz[:, :2].tolist() == [[-10, -10], [-10, 10], [10, -10], [10, 10]] and ref.z.tolist() == [4, 2, 3, 1]
    import itertools

    for perm in itertools.permutations(range(4)):
        p = list(perm)
        got = sm.prepare(xyz[p], z[p], None, **{**kw, "hole": kw["hole"][p], "row_id": kw["row_id"][p]})
        assert got.z.tobytes() == ref.z.tobytes() and got.xyz.tobytes() == ref.xyz.tobytes()
        assert got.hole.tolist() == ref.hole.tolist()


def test_equal_xyz_ordered_by_value_then_domain() -> None:
    xyz = np.zeros((3, 3))
    xyz[:, 0] = [1.0, 1.0, 0.0]
    z = np.array([5.0, 2.0, 9.0])
    order = sm.canonical_order(xyz, z)
    assert order.tolist() == [2, 1, 0]
    assert sm.canonical_order(xyz, np.array([1.0, 1.0, 1.0]), np.array([1, 0, 0])).tolist() == [2, 1, 0]


def test_averaging_of_coincident_samples() -> None:
    xyz = np.array(
        [
            [0.0, 0.0, 0.0],
            [5e-7, 0.0, 0.0],
            [2e-6, 0.0, 0.0],  # 0–1 joined; 2 is 1.5e−6 from 1
            [0.0, 0.0, 0.0],
            [100.0, 0.0, 0.0],
        ]
    )  # 3: another domain at the point of 0 — not joined
    z = np.array([2.0, 4.0, 6.0, 8.0, 1.0])
    w = np.array([1.0, 3.0, 0.5, 2.0, np.nan])
    dom = np.array([0, 0, 0, 1, 0], dtype=np.int16)
    s = sm.prepare(xyz, z, z * 2, w, np.array([0, 1, 1, 2, 3], np.int32), list("abcd"), dom,
                   np.array([10, 11, 12, 13, 14]), np.arange(10.0).reshape(5, 2))  # fmt: skip
    assert s.n == 4 and s.counts["merged_groups"] == 1 and s.counts["merged_rows"] == 2 and s.counts["used"] == 4
    assert len(s.notes) == 1 and "2 в 1 точках" in s.notes[0]
    k = int(np.flatnonzero((s.dom == 0) & (s.xyz[:, 0] == 0.0))[0])
    assert s.z[k] == 3.0 and s.raw[k] == 6.0 and s.w[k] == 4.0 and s.merged[k] == 2  # type: ignore[index]
    assert s.row_id[k] == 10 and s.span[k].tolist() == [0.0, 1.0] and s.hole[k] == 0  # the first of the group
    assert sorted(s.merged.tolist()) == [1, 1, 1, 2]
    assert s.w is not None and np.isnan(s.w[s.row_id == 14]).all()  # no weight stays NaN
    far = sm.prepare(xyz[[0, 2]], z[[0, 2]], None, None, np.zeros(2, np.int32), ["a"], np.zeros(2, np.int16),
                     np.arange(2), np.zeros((2, 2)))  # fmt: skip
    assert far.n == 2 and far.counts["merged_groups"] == 0 and far.notes == []  # 2e−6 m apart: two samples
    chain = sm.prepare(xyz[:3], z[:3], None, None, np.zeros(3, np.int32), ["a"], np.zeros(3, np.int16),
                       np.arange(3), np.zeros((3, 2)), tol=1e-6)  # fmt: skip
    assert chain.n == 2 and chain.merged.tolist() == [2, 1]  # 0–1 joined, 2 is 1.5e−6 m from 1: its own


def test_chain_and_keep_groups() -> None:
    xyz = np.array([[0.0, 0.0, 0.0], [8e-7, 0.0, 0.0], [1.6e-6, 0.0, 0.0], [50.0, 0.0, 0.0]])
    keep, group = sm.merge_coincident(xyz, np.zeros(4, np.int16))
    assert keep.tolist() == [0, 3] and group.tolist() == [0, 0, 0, 1]  # a chain of three is one sample
    keep, group = sm.merge_coincident(xyz, np.array([0, 1, 0, 0], np.int16))  # 1 is another domain: it joins none
    assert keep.tolist() == [0, 1, 2, 3] and group.tolist() == [0, 1, 2, 3]  # 0 and 2 are 1.6e−6 m apart
    k1, g1 = sm.merge_coincident(xyz[:1], np.zeros(1, np.int16))
    assert k1.tolist() == [0] and g1.tolist() == [0]


def test_means_and_empty_declus() -> None:
    z = np.array([1.0, 3.0, 8.0])
    assert sm.declus_mean(z, np.array([1.0, 1.0, 2.0])) == pytest.approx((1 + 3 + 16) / 4.0)
    assert sm.declus_mean(z, np.array([np.nan, -1.0, 0.0])) is None
    ind = (z <= 3.0).astype(np.float64)  # the m_k of IK: the declustered mean of an indicator
    assert sm.declus_mean(ind, np.array([1.0, 1.0, 2.0])) == pytest.approx(0.5)
    assert math.isnan(sm.origin_of(np.array([[np.nan, 0.0, 0.0]]))[0])


# --- 100 thousand samples -----------------------------------------------------------------------------------------


def test_100k_samples_in_two_seconds() -> None:
    a = arrays(100_000, dup=200)
    sm.prepare(a["xyz"][:1000], a["z"][:1000], a["raw"][:1000], a["w"][:1000], a["hole"][:1000], a["holes"],
               a["dom"][:1000], a["row_id"][:1000], a["span"][:1000])  # fmt: skip  # imports, first calls
    best = math.inf
    for _ in range(3):
        t = time.perf_counter()
        s = run(a)
        best = min(best, time.perf_counter() - t)
    assert s.n == 100_000 and s.counts["merged_groups"] == 200
    assert best <= 2.0, f"{best:.2f} s"
