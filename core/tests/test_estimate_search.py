"""The neighbourhood search (ТЗ 8.8.2; ADR 0024 §4; Д-29; Д-30; Э18, 18.6): Т8.8-11; the selection equals a full
enumeration in NumPy (random sets, every mode: passes, hole and octant limits, the soft boundary, the rule of the high
samples, the exclusion of cross-validation); the answer does not depend on k_c; ties go by the number of the sample;
ТД29-1…3 at the level of the selection; the reasons of the inspector on a set of the T13 kind; threads; warm-up."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.geostat.estimate import search as se
from bf_core.geostat.estimate import search_kernel as sk
from bf_core.geostat.estimate.samples import D, RowSamples, S, prepare
from bf_core.geostat.estimate.spec_parts import Hy, Pass
from bf_core.geostat.rotation import anisotropy_matrix

R = se.REASONS


def samples_of(xyz, z=None, hole=None, dom=None, n_holes=1) -> RowSamples:
    """RowSamples from arrays, origin 0 (local = xyz); the canonical order is that of `prepare`."""
    xyz = np.asarray(xyz, dtype=np.float64)
    n = xyz.shape[0]
    z = np.ones(n) if z is None else np.asarray(z, dtype=np.float64)
    hole = np.zeros(n, np.int32) if hole is None else np.asarray(hole, dtype=np.int32)
    dom = np.zeros(n, np.int16) if dom is None else np.asarray(dom, dtype=np.int16)
    names = [f"h{i}" for i in range(max(int(hole.max()) + 1, n_holes))]
    return prepare(xyz, z, None, None, hole, names, dom, np.arange(n, dtype=np.int64), np.zeros((n, 2)),
                   origin=np.zeros(3))  # fmt: skip


def at(s: RowSamples, xyz) -> int:
    """The number (canonical position) of the sample at `xyz`."""
    hit = np.flatnonzero(np.all(s.xyz == np.asarray(xyz, dtype=np.float64), axis=1))
    assert hit.size == 1, xyz
    return int(hit[0])


def isotropic(r: float = 100.0, angles=(90.0, 0.0, 0.0)) -> se.Ellipsoid:
    """angles (90, 0, 0): the axes of the ellipsoid are the axes of the project (a1 = east, a2 = north, a3 = up)."""
    return se.Ellipsoid.of(angles, (r, r, r))


# --- the reference: a full enumeration, in NumPy and Python ----------------------------------------------------------


def ref_gather(s: RowSamples, ell: se.Ellipsoid, passes, soft: float, centres, k: int, exclude=None):
    """Per block: dict(idx, h, z, n, nh, nhy, noct, ok, ad, dmin) — the rules of 8.8.2 / Д-29 over every sample."""
    p = passes[k]
    rad = ell.radii[0] * p.factor
    hy = p.hy
    out = []
    for b, c in enumerate(np.asarray(centres, dtype=np.float64)):
        d = s.local - c
        h = ell.h(d)
        eu = np.sqrt(d[:, 0] * d[:, 0] + d[:, 1] * d[:, 1] + d[:, 2] * d[:, 2])
        dom_ok = (s.dom == D) | ((s.dom == S) & (soft > 0.0) & (eu <= soft))
        left = np.zeros(s.n, bool)
        if exclude is not None:
            left = (np.arange(s.n) == exclude[1][b]) if exclude[0] == "sample" else (s.hole == exclude[1][b])
        base = (h <= rad) & dom_ok & ~left
        touched = np.zeros(s.n, bool) if hy is None else base & (s.z > hy.t) & (h > hy.q * rad)
        zu = s.z.copy()
        cand = base
        if hy is not None:
            if hy.mode == "cap":
                zu[touched] = hy.t
            else:
                cand = base & ~touched
        ids = np.flatnonzero(cand)
        ids = ids[np.lexsort((ids, h[ids]))]
        octs = ell.octant(d)
        sel: list[int] = []
        holes: dict[int, int] = {}
        oc: dict[int, int] = {}
        for j in ids:
            if len(sel) >= p.n_max:
                break
            if p.n_hole and holes.get(int(s.hole[j]), 0) >= p.n_hole:
                continue
            if p.n_oct and oc.get(int(octs[j]), 0) >= p.n_oct:
                continue
            sel.append(int(j))
            holes[int(s.hole[j])] = holes.get(int(s.hole[j]), 0) + 1
            oc[int(octs[j])] = oc.get(int(octs[j]), 0) + 1
        n = len(sel)
        reach = h[sel[-1]] if n >= p.n_max and n else rad
        out.append({
            "idx": sel, "h": h[sel], "z": zu[sel], "n": n, "nh": len(holes), "noct": len(oc),
            "nhy": int(np.count_nonzero(touched & (h <= reach))),
            "ok": bool(n >= p.n_min and n > 0 and len(oc) >= p.k_oct and len(holes) >= p.n_holes_min),
            "ad": float(np.mean(eu[sel])) if n else np.nan, "dmin": float(np.min(eu[sel])) if n else np.nan,
        })  # fmt: skip
    return out


def assert_same(nb: se.Neighbours, ref: list[dict]) -> None:
    w = nb.idx.shape[1]
    for b, r in enumerate(ref):
        n = r["n"]
        assert int(nb.n[b]) == n, b
        assert list(nb.idx[b, :n]) == r["idx"], b
        assert (nb.idx[b, n:] == -1).all() and np.isnan(nb.h[b, n:]).all() and np.isnan(nb.z[b, n:]).all()
        np.testing.assert_array_equal(nb.h[b, :n], r["h"])
        np.testing.assert_array_equal(nb.z[b, :n], r["z"])
        assert int(nb.nh[b]) == r["nh"] and int(nb.noct[b]) == r["noct"] and int(nb.nhy[b]) == r["nhy"], b
        assert bool(nb.ok[b]) == r["ok"], b
        np.testing.assert_allclose(nb.ad[b], r["ad"], rtol=1e-13, equal_nan=True)
        np.testing.assert_allclose(nb.dmin[b], r["dmin"], rtol=1e-13, equal_nan=True)
    assert w >= max(r["n"] for r in ref)


def row_of(nb: se.Neighbours, b: int) -> se.Neighbours:
    return se.Neighbours(
        *(getattr(nb, f)[b : b + 1] for f in ("idx", "h", "z", "n", "nh", "nhy", "noct", "ok", "ad", "dmin"))
    )


def same_neighbours(a: se.Neighbours, b: se.Neighbours) -> None:
    for name in ("idx", "h", "z", "n", "nh", "nhy", "noct", "ok", "ad", "dmin"):
        np.testing.assert_array_equal(getattr(a, name), getattr(b, name), err_msg=name)


# --- random sets ---------------------------------------------------------------------------------------------------


def random_row(seed: int, n: int = 500, extent: float = 200.0):
    rng = np.random.default_rng(seed)
    xyz = rng.uniform(0.0, extent, (n, 3))
    z = rng.lognormal(0.0, 1.0, n)
    hole = rng.integers(0, 25, n)
    dom = rng.choice([0, 1, 2], n, p=[0.8, 0.14, 0.06])
    return samples_of(xyz, z, hole, dom, 25), rng


ELL = se.Ellipsoid.of((30.0, 20.0, 10.0), (60.0, 40.0, 25.0))
CONFIGS = {
    "plain": ((Pass(1.0, 4, 12),), 0.0),
    "limits": ((Pass(1.0, 3, 8, 2, 0, 0, 0), Pass(2.0, 2, 10, 0, 2, 2, 0), Pass(3.0, 1, 24, 3, 1, 4, 2)), 0.0),
    "soft": ((Pass(1.0, 4, 16, 3, 2, 2, 2), Pass(2.0, 2, 24)), 20.0),
    "hy_drop": ((Pass(1.0, 4, 16, hy=Hy(3.0, 0.4, "drop")), Pass(2.0, 3, 24, 4, hy=Hy(2.0, 0.5, "drop"))), 0.0),
    "hy_cap": ((Pass(1.0, 4, 16, 2, 2, 0, 0, Hy(3.0, 0.4, "cap")), Pass(2.5, 3, 24, hy=Hy(2.0, 0.3, "cap"))), 25.0),
    "n_max_128": ((Pass(1.0, 4, 128, 0, 0, 0, 0, Hy(4.0, 0.5, "cap")),), 10.0),
}


@pytest.mark.parametrize("name", list(CONFIGS))
@pytest.mark.parametrize("seed", [1, 2])
def test_selection_equals_full_enumeration(name, seed):
    s, rng = random_row(seed)
    passes, soft = CONFIGS[name]
    centres = rng.uniform(-30.0, 230.0, (120, 3))
    sr = se.Searcher(s, ELL, passes, soft)
    for k in range(len(passes)):
        assert_same(sr.gather(centres, k), ref_gather(s, ELL, passes, soft, centres, k))
    found = sr.search(centres)
    for b in range(centres.shape[0]):
        want = 0
        for k in range(len(passes)):
            r = ref_gather(s, ELL, passes, soft, centres[b : b + 1], k)[0]
            if r["ok"]:
                want = k + 1
                break
        assert int(found.pass_[b]) == want, b
        if want:
            assert_same(row_of(found.nb, b), [r])
        else:
            assert found.nb.n[b] == 0 and (found.nb.idx[b] == -1).all()
    assert (found.pass_ > 0).any()  # the set is not degenerate: some blocks hold


def test_selection_with_exclusion_of_a_sample_or_a_hole():
    s, rng = random_row(3, 400)
    passes, soft = CONFIGS["soft"]
    pick = rng.integers(0, s.n, 80)
    centres = s.local[pick]  # the centre is the left-out sample (xval)
    sr = se.Searcher(s, ELL, passes, soft)
    for k in range(len(passes)):
        for ex in (("sample", pick.astype(np.int64)), ("hole", s.hole[pick].astype(np.int64))):
            nb = sr.gather(centres, k, exclude=ex)
            assert_same(nb, ref_gather(s, ELL, passes, soft, centres, k, ex))
            for b in range(centres.shape[0]):
                got = nb.idx[b, : nb.n[b]]
                assert not (got == pick[b]).any()
                if ex[0] == "hole":
                    assert not (s.hole[got] == s.hole[pick[b]]).any()


@pytest.mark.parametrize("name", ["plain", "limits", "soft", "hy_cap"])
def test_equality_on_k_c(name):
    """A repeat with a larger k_c gives the answer of the full enumeration whatever the first k_c was."""
    s, rng = random_row(7)
    passes, soft = CONFIGS[name]
    centres = rng.uniform(-30.0, 230.0, (150, 3))
    base = se.Searcher(s, ELL, passes, soft)
    base_rounds = base.rounds
    ref = [base.gather(centres, k) for k in range(len(passes))]
    assert base.rounds >= base_rounds
    for kc in (1, 2, 5, 17, 64):
        sr = se.Searcher(s, ELL, passes, soft, k_c=kc)
        for k in range(len(passes)):
            same_neighbours(sr.gather(centres, k), ref[k])
        if kc <= 5:
            assert sr.rounds > 0  # the repeat did run
    big = se.Searcher(s, ELL, passes, soft, k_c=s.n)  # the whole row at once: no repeat at all
    for k in range(len(passes)):
        same_neighbours(big.gather(centres, k), ref[k])
    assert big.rounds == 0


def test_repeat_when_the_last_selected_is_the_k_c_th():
    """A selection stopped by n_max exactly at the k_c-th distance (a lattice: ties) is repeated, not trusted."""
    g = np.arange(-6, 7, dtype=np.float64)
    xyz = np.array([[x, y, z] for x in g for y in g for z in g])
    s = samples_of(xyz)
    ell = isotropic(100.0)
    p = (Pass(1.0, 1, 20),)
    centres = np.array([[0.0, 0.0, 0.0], [0.5, 0.0, 0.0], [0.2, 0.3, 0.1], [3.0, 3.0, 3.0]])
    ref = ref_gather(s, ell, p, 0.0, centres, 0)
    for kc in (3, 20, 21, 64):
        assert_same(se.Searcher(s, ell, p, 0.0, k_c=kc).gather(centres, 0), ref)


def test_ties_go_by_the_number_of_the_sample():
    """Equal h_s: the smaller canonical number first (№ 76); a permutation of the input changes nothing."""
    pts = np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1], [2, 0, 0], [0, 2, 0]], float)
    s = samples_of(pts)
    sr = se.Searcher(s, isotropic(10.0), (Pass(1.0, 1, 4),))
    nb = sr.gather(np.zeros((1, 3)), 0)
    assert list(nb.idx[0]) == [0, 1, 2, 3]  # the first four of the canonical order: all at h = 1
    assert (nb.h[0] == 1.0).all()
    perm = np.random.default_rng(5).permutation(len(pts))
    s2 = samples_of(pts[perm])
    nb2 = se.Searcher(s2, isotropic(10.0), (Pass(1.0, 1, 4),)).gather(np.zeros((1, 3)), 0)
    np.testing.assert_array_equal(s2.xyz[nb2.idx[0]], s.xyz[nb.idx[0]])


def test_ellipsoid_matrix_octant_and_distance():
    ell = se.Ellipsoid.of((30.0, 20.0, 10.0), (60.0, 40.0, 25.0))
    np.testing.assert_array_equal(ell.S, anisotropy_matrix(30.0, 20.0, 10.0, 60.0, 40.0, 25.0))
    d = np.random.default_rng(0).normal(size=(50, 3)) * 30.0
    np.testing.assert_allclose(ell.h(d), np.linalg.norm(d @ ell.S.T, axis=1), rtol=1e-14)
    # on the semi-axes (a1, a2, a3 are the rows of R): h_s = r1 · (distance / own range)
    rot = anisotropy_matrix(30.0, 20.0, 10.0, 1.0, 1.0, 1.0)
    for i, r in enumerate((60.0, 40.0, 25.0)):
        np.testing.assert_allclose(ell.h(rot[i] * r), 60.0, rtol=1e-13)
    # octants in the axes of the ellipsoid: an axis-aligned one is the project's frame
    e0 = isotropic(10.0)
    pts = np.array([[1, 1, 1], [-1, 1, 1], [1, -1, 1], [-1, -1, 1], [1, 1, -1], [-1, 1, -1], [1, -1, -1],
                    [-1, -1, -1], [0, 0, 0], [0, 1, -1]], float)  # fmt: skip
    np.testing.assert_array_equal(e0.octant(pts), [7, 6, 5, 4, 3, 2, 1, 0, 7, 3])
    s = np.array([[sk.octant(e0.S, *p) for p in pts]])
    np.testing.assert_array_equal(s[0], e0.octant(pts))


# --- Т8.8-11 -------------------------------------------------------------------------------------------------------


def ring(n: int, radius: float, hole0: int = 0, phase: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    a = phase + np.arange(n) * 2.0 * np.pi / n
    return np.c_[radius * np.cos(a), radius * np.sin(a), np.zeros(n)], np.full(n, hole0)


def test_t8_8_11_second_pass_and_limit_of_a_hole():
    r = 50.0
    ell = isotropic(r)
    near, h1 = ring(3, 0.9 * r)
    far, h2 = ring(10, 1.6 * r, 1, 0.1)
    s = samples_of(np.r_[near, far], hole=np.r_[h1, h2], n_holes=2)
    passes = (Pass(1.0, 4, 24), Pass(2.0, 4, 24))
    sr = se.Searcher(s, ell, passes, 0.0)
    got = sr.search(np.zeros((1, 3)))
    assert int(got.pass_[0]) == 2  # three samples in r < n_min = 4; thirteen in 2r
    assert int(got.nb.n[0]) == 13 and bool(got.nb.ok[0])
    assert not sr.gather(np.zeros((1, 3)), 0).ok[0] and int(sr.gather(np.zeros((1, 3)), 0).n[0]) == 3
    # n_hole = 2 with 3 holes of 10 samples each: NS = 6, NH = 3
    pts, hl = [], []
    for i in range(3):
        a, _ = ring(10, 20.0 + 5.0 * i, 0, 0.3 * i)
        pts.append(a)
        hl.append(np.full(10, i))
    s2 = samples_of(np.vstack(pts), hole=np.concatenate(hl), n_holes=3)
    nb = se.Searcher(s2, ell, (Pass(1.0, 4, 24, n_hole=2),), 0.0).gather(np.zeros((1, 3)), 0)
    assert (int(nb.n[0]), int(nb.nh[0])) == (6, 3) and bool(nb.ok[0])
    assert sorted(np.bincount(s2.hole[nb.idx[0, :6]]).tolist()) == [2, 2, 2]


# --- Д-29 ----------------------------------------------------------------------------------------------------------


def idw(z: np.ndarray, h: np.ndarray, power: float = 1.0) -> float:
    w = 1.0 / h**power
    return float(np.sum(w * z) / np.sum(w))


def td29_set():
    r1 = 100.0
    xyz = np.array([[0.2 * r1, 0, 0], [0, 0.2 * r1, 0], [0, 0, 0.2 * r1], [0.8 * r1, 0, 0]], float)
    return samples_of(xyz, [1.0, 1.0, 1.0, 50.0]), r1


def test_td29_1_drop_and_cap():
    s, r1 = td29_set()
    ell = isotropic(r1)
    c = np.zeros((1, 3))
    drop = se.Searcher(s, ell, (Pass(1.0, 1, 24, hy=Hy(5.0, 0.5, "drop")),), 0.0).gather(c, 0)
    assert int(drop.n[0]) == 3 and int(drop.nhy[0]) == 1
    assert idw(drop.z[0, :3], drop.h[0, :3]) == pytest.approx(1.0, abs=1e-12)  # оценка 1
    cap = se.Searcher(s, ell, (Pass(1.0, 1, 24, hy=Hy(5.0, 0.5, "cap")),), 0.0).gather(c, 0)
    assert int(cap.n[0]) == 4 and int(cap.nhy[0]) == 1
    big = int(np.argmax(s.z))
    assert big in cap.idx[0] and cap.z[0][list(cap.idx[0]).index(big)] == 5.0  # 50 → t
    z_used = np.where(s.z > 5.0, 5.0, s.z)
    want = idw(z_used[cap.idx[0, :4]], ell.h(s.local[cap.idx[0, :4]]))
    assert idw(cap.z[0, :4], cap.h[0, :4]) == pytest.approx(want, rel=1e-12)
    off = se.Searcher(s, ell, (Pass(1.0, 1, 24),), 0.0).gather(c, 0)
    assert int(off.n[0]) == 4 and int(off.nhy[0]) == 0 and 50.0 in off.z[0]  # the rule is off by default


@pytest.mark.parametrize("mode", ["drop", "cap"])
def test_td29_2_q_one_is_bitwise_the_search_without_the_rule(mode):
    s, rng = random_row(11, 300)
    ell = ELL
    centres = rng.uniform(0.0, 200.0, (100, 3))
    hy = Hy(1.0, 1.0, mode)
    with_rule = se.Searcher(s, ell, (Pass(1.0, 3, 16, hy=hy), Pass(2.0, 3, 24, hy=hy)), 0.0)
    plain = se.Searcher(s, ell, (Pass(1.0, 3, 16), Pass(2.0, 3, 24)), 0.0)
    for k in (0, 1):
        a, b = with_rule.gather(centres, k), plain.gather(centres, k)
        same_neighbours(a, b)
        assert (a.nhy == 0).all()


def test_td29_3_drop_below_n_min_goes_to_the_next_pass():
    s, r1 = td29_set()
    ell = isotropic(r1)
    c = np.zeros((1, 3))
    hy = Hy(5.0, 0.5, "drop")
    free = se.Searcher(s, ell, (Pass(1.0, 4, 24), Pass(2.0, 4, 24)), 0.0).search(c)
    assert int(free.pass_[0]) == 1  # four samples hold pass 1
    ruled = se.Searcher(s, ell, (Pass(1.0, 4, 24, hy=hy), Pass(2.0, 4, 24, hy=hy)), 0.0).search(c)
    assert int(ruled.pass_[0]) == 2  # three are left: PASS = 2
    assert int(ruled.nb.n[0]) == 4 and int(ruled.nb.nhy[0]) == 0  # in pass 2 the sample is within q·2·r1 = r1
    assert ruled.nb.z[0, :4].max() == 50.0


# --- the reasons of the inspector (Д-30), a set of the T13 kind ---------------------------------------------------


def t13():
    """Centre 0; the axes of the ellipsoid are the project's (angles 90, 0, 0); r = 100; passes ×1, ×1.5."""
    spec = {  # name: (xyz, hole, dom, z)
        "D2": ((-3, -4, 0), 3, D, 50.0), "C2": ((0, 0, -5), 2, D, 1.0),
        "A2": ((-10, 10, 10), 0, D, 1.0), "A3": ((10, -10, -10), 0, D, 1.0), "A1": ((10, 10, 10), 0, D, 1.0),
        "C1": ((0, 20, 20), 2, D, 1.0), "F1": ((-20, -20, -20), 5, S, 1.0), "B1": ((30, 30, 30), 1, D, 1.0),
        "B2": ((40, 40, 40), 1, D, 1.0), "B3": ((50, 50, 50), 1, D, 1.0), "D1": ((-60, 0, 0), 3, D, 50.0),
        "E1": ((5, 5, 5), 4, 2, 1.0), "F2": ((-40, -40, -40), 5, S, 1.0), "G1": ((150, 0, 0), 6, D, 1.0),
    }  # fmt: skip
    names = list(spec)
    xyz = [spec[k][0] for k in names]
    s = samples_of(xyz, [spec[k][3] for k in names], [spec[k][1] for k in names], [spec[k][2] for k in names], 7)
    return s, {k: at(s, spec[k][0]) for k in names}


def reasons_of(c: se.Candidates, ix: dict[str, int]) -> dict[str, str]:
    by = {int(i): R[int(r)] for i, r in zip(c.idx, c.reason, strict=True)}
    return {k: by[v] for k, v in ix.items() if v in by}  # beyond the largest radius — not a candidate


def test_explain_octants_boundary_hole_in_several_octants_and_far_high_sample():
    s, ix = t13()
    ell = isotropic(100.0)
    hy = Hy(10.0, 0.5, "drop")
    p1 = Pass(1.0, 2, 8, n_hole=2, n_oct=1, hy=hy)
    sr = se.Searcher(s, ell, (p1, Pass(1.5, 2, 8, hy=hy)), 40.0)
    c = sr.explain(np.zeros(3), 0)
    got = reasons_of(c, ix)
    assert got == {
        "D2": "selected", "C2": "selected", "A2": "selected", "A3": "selected",
        "A1": "hole_limit",      # the third of the hole A, which lies in three octants (7, 6, 1)
        "C1": "selected",        # x = 0: on the plane, the positive side — octant 7, the first of it
        "F1": "selected",        # soft: 34.6 m ≤ d_м = 40
        "B1": "octant_limit", "B2": "octant_limit", "B3": "octant_limit",  # octant 7 is taken by C1
        "D1": "hy_dropped",      # z = 50 > 10 at h_s = 60 > q·r = 50
        "E1": "other_domain", "F2": "soft_far", "G1": "outside",
    }  # fmt: skip
    octs = {k: int(c.octant[list(c.idx).index(v)]) for k, v in ix.items()}
    assert (octs["A1"], octs["A2"], octs["A3"], octs["C1"], octs["C2"], octs["D2"]) == (7, 6, 1, 7, 3, 4)
    # the order: (h_s, number): D2 and C2 are both at 5 m — D2 (X = −3) goes first
    assert list(c.idx[:2]) == [ix["D2"], ix["C2"]] and c.h[0] == c.h[1] == 5.0
    assert c.total == s.n and c.n == 6 and c.nh == 4 and c.noct == 6 and c.nhy == 1 and c.ok
    assert c.names()[0] == "selected" and np.all(np.diff(c.h) >= 0)
    # the same selection as the one of `gather`
    nb = sr.gather(np.zeros((1, 3)), 0)
    assert sorted(nb.idx[0, : nb.n[0]]) == sorted(i for i, r in zip(c.idx, c.reason, strict=True) if r == 0)
    assert list(nb.idx[0, : nb.n[0]]) == [i for i, r in zip(c.idx, c.reason, strict=True) if r == 0]
    assert (int(nb.n[0]), int(nb.nhy[0]), int(nb.noct[0]), int(nb.nh[0])) == (c.n, c.nhy, c.noct, c.nh)
    # the second pass: the radius is 150 — the sample at 150 m is no longer «outside» (it is beyond n_max = 8), and
    # the rule of Д-29 reaches q·150 = 75 m: the high sample at 60 m is not touched
    c2 = sr.explain(np.zeros(3), 1)
    got2 = reasons_of(c2, ix)
    assert c2.radius == 150.0 and got2["G1"] == "over_n_max" and got2["D1"] == "over_n_max" and c2.nhy == 0


def test_explain_n_max_and_capped():
    s, ix = t13()
    ell = isotropic(100.0)
    p = se.Searcher(s, ell, (Pass(1.0, 2, 3),), 0.0)
    got = reasons_of(p.explain(np.zeros(3), 0), ix)
    assert [k for k in ("D2", "C2", "A2") if got[k] == "selected"] == ["D2", "C2", "A2"]
    assert all(got[k] == "over_n_max" for k in ("A3", "A1", "C1", "B1", "B2", "B3", "D1"))
    assert got["F1"] == "other_domain" and got["F2"] == "other_domain"  # a hard boundary: S is another domain
    cap = se.Searcher(s, ell, (Pass(1.0, 2, 9, hy=Hy(10.0, 0.5, "cap")),), 40.0)
    c = cap.explain(np.zeros(3), 0)
    got = reasons_of(c, ix)
    assert got["D1"] == "hy_capped" and c.z[list(c.idx).index(ix["D1"])] == 10.0
    assert got["B2"] == "over_n_max" and c.n == 9 and c.nhy == 1  # D1 (60 m) is not beyond the reach (the ninth: 60 m)
    nb = cap.gather(np.zeros((1, 3)), 0)
    assert int(nb.nhy[0]) == 1 and ix["D1"] in nb.idx[0] and nb.z[0][list(nb.idx[0]).index(ix["D1"])] == 10.0


def test_explain_agrees_with_gather_on_random_blocks():
    s, rng = random_row(21, 300)
    for name in ("limits", "soft", "hy_drop", "hy_cap"):
        passes, soft = CONFIGS[name]
        sr = se.Searcher(s, ELL, passes, soft)
        centres = rng.uniform(0.0, 200.0, (25, 3))
        for k in range(len(passes)):
            nb = sr.gather(centres, k)
            for b in range(centres.shape[0]):
                c = sr.explain(centres[b], k)
                sel = c.idx[c.reason <= 1]
                assert list(sel) == list(nb.idx[b, : nb.n[b]]), (name, k, b)
                assert (c.n, c.nh, c.noct, c.nhy, c.ok) == (nb.n[b], nb.nh[b], nb.noct[b], nb.nhy[b], nb.ok[b])
                np.testing.assert_array_equal(c.z[c.reason <= 1], nb.z[b, : nb.n[b]])


def test_explain_cut_to_the_limit_and_pool_of_all_domains():
    s, _ = random_row(5, 800)
    sr = se.Searcher(s, ELL, (Pass(3.0, 4, 24),), 0.0)
    c = sr.explain(np.array([100.0, 100.0, 100.0]), 0, limit=50)
    assert c.idx.size == 50 and c.total > 50
    # a pool with another domain: the samples are given, the reason is «other_domain»
    pool = samples_of(np.array([[1.0, 0, 0], [2.0, 0, 0], [3.0, 0, 0]]), dom=[0, 2, 3])
    c2 = se.Searcher(pool, isotropic(10.0), (Pass(1.0, 1, 5),), 0.0).explain(np.zeros(3), 0, pool=pool)
    assert c2.names() == ["selected", "other_domain", "other_domain"]


# --- the edges ------------------------------------------------------------------------------------------------------


def test_edges_nan_todo_empty_and_errors():
    s, rng = random_row(4, 200)
    passes, soft = CONFIGS["plain"]
    sr = se.Searcher(s, ELL, passes, soft)
    centres = rng.uniform(0.0, 200.0, (6, 3))
    centres[1, 2] = np.nan
    todo = np.array([True, True, False, True, True, True])
    nb = sr.gather(centres, 0, todo=todo)
    assert not nb.ok[1] and not nb.ok[2] and nb.n[1] == 0 and nb.n[2] == 0 and np.isnan(nb.h[1]).all()
    ref = ref_gather(s, ELL, passes, soft, centres[[0, 3, 4, 5]], 0)
    full = sr.gather(centres[[0, 3, 4, 5]], 0)
    assert_same(full, ref)
    same = sr.gather(centres, 0)
    for src, dst in ((0, 0), (3, 3), (4, 4)):
        assert list(same.idx[dst]) == list(nb.idx[src])
    assert sr.gather(np.empty((0, 3)), 0).n.shape == (0,)
    # no sample of D at all: nothing holds, nothing is raised
    only_s = samples_of(np.array([[1.0, 0, 0], [2.0, 0, 0]]), dom=[1, 1])
    e = se.Searcher(only_s, isotropic(10.0), (Pass(1.0, 1, 5),), 0.0)
    assert not e.gather(np.zeros((1, 3)), 0).ok[0] and int(e.search(np.zeros((1, 3))).pass_[0]) == 0
    with pytest.raises(ApiError) as bad:
        se.Searcher(s, ELL, [Pass(float(i + 1)) for i in range(6)], 0.0)
    assert bad.value.code == "internal"
    with pytest.raises(ApiError):
        sr.explain(np.zeros(3), 3)


def test_samples_at_the_centre_and_on_the_boundary_of_the_ellipsoid():
    """h_s = 0 is a neighbour; a sample exactly at h_s = r1·factor is inside (≤)."""
    xyz = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [10.000001, 0.0, 0.0]])
    nb = se.Searcher(samples_of(xyz), isotropic(10.0), (Pass(1.0, 1, 5),), 0.0).gather(np.zeros((1, 3)), 0)
    assert list(nb.idx[0, :2]) == [0, 1] and int(nb.n[0]) == 2 and nb.h[0, 0] == 0.0 and nb.dmin[0] == 0.0


def test_the_same_result_on_1_2_4_8_threads_and_of_a_chunk_of_any_size():
    s, rng = random_row(9, 600)
    passes, soft = CONFIGS["limits"]
    centres = rng.uniform(-20.0, 220.0, (5000, 3))
    sr = se.Searcher(s, ELL, passes, soft)
    one = sr.search(centres)
    for workers in (2, 4, 8):
        parts = np.array_split(np.arange(centres.shape[0]), workers * 3)
        with ThreadPoolExecutor(workers) as ex:
            res = list(ex.map(lambda ix: sr.search(centres[ix]), parts))
        for ix, r in zip(parts, res, strict=True):
            np.testing.assert_array_equal(r.pass_, one.pass_[ix])
            np.testing.assert_array_equal(r.nb.idx, one.nb.idx[ix])
            np.testing.assert_array_equal(r.nb.z, one.nb.z[ix])
            np.testing.assert_array_equal(r.nb.nhy, one.nb.nhy[ix])


def test_long_candidate_lists_use_the_merge_order():
    """More than INSERTION_MAX candidates inside the radius: the same order (h_s, number) as the short path."""
    g = np.arange(-4, 5, dtype=np.float64)
    xyz = np.array([[x, y, z] for x in g for y in g for z in g])  # 729 points, many ties
    s = samples_of(xyz)
    ell = isotropic(100.0)
    p = (Pass(1.0, 1, 128),)
    centres = np.array([[0.0, 0.0, 0.0], [0.25, 0.5, 0.0]])
    assert_same(se.Searcher(s, ell, p, 0.0).gather(centres, 0), ref_gather(s, ell, p, 0.0, centres, 0))


# --- the warm-up, the speed -----------------------------------------------------------------------------------------


def test_read_only_inputs_do_not_add_a_signature_and_warm_runs():
    se.warm_search()
    before = (len(sk.gather_kernel.signatures), len(sk.classify_kernel.signatures))
    s, rng = random_row(13, 100)
    for a in (s.local, s.z, s.dom, s.hole):
        a.setflags(write=False)
    centres = rng.uniform(0.0, 200.0, (5, 3))
    centres.setflags(write=False)
    sr = se.Searcher(s, ELL, CONFIGS["soft"][0], 20.0)
    assert_same(sr.gather(centres, 0), ref_gather(s, ELL, CONFIGS["soft"][0], 20.0, centres, 0))
    sr.explain(centres[0], 0)
    assert (len(sk.gather_kernel.signatures), len(sk.classify_kernel.signatures)) == before


def test_speed_us_per_block(capsys):
    """A measurement, not a gate: 100 000 samples, n_max = 24, 3 passes, 20 000 blocks (it goes to the report)."""
    rng = np.random.default_rng(2)
    n = 100_000
    xyz = rng.uniform(0.0, 2000.0, (n, 3))
    s = samples_of(xyz, rng.lognormal(0.0, 1.0, n), rng.integers(0, 2000, n), n_holes=2000)
    ell = se.Ellipsoid.of((20.0, 10.0, 0.0), (120.0, 90.0, 50.0))
    passes = (Pass(1.0, 4, 24, 3, 2, 2, 2), Pass(2.0, 4, 24, 3), Pass(3.0, 2, 24))
    t0 = time.perf_counter()
    sr = se.Searcher(s, ell, passes, 0.0)
    t_build = time.perf_counter() - t0
    centres = rng.uniform(0.0, 2000.0, (20_000, 3))
    sr.search(centres[:200])
    t0 = time.perf_counter()
    found = sr.search(centres)
    dt = time.perf_counter() - t0
    with capsys.disabled():
        print(f"\n[search] build {t_build * 1e3:.0f} ms; {dt / centres.shape[0] * 1e6:.1f} µs/block (passes ok: "
              f"{np.bincount(found.pass_, minlength=4).tolist()}), repeats {sr.rounds}")  # fmt: skip
    assert dt / centres.shape[0] < 2e-3
