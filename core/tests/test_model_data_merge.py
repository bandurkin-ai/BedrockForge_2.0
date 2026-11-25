"""The merge of coincident points (review 19.29б): the pairs of a k-d tree give the bits of the former scan along x
(the reference below is that scan) and a fan of vertical holes (one x, y) is no longer quadratic."""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pytest

from bf_core.model.data import canonical_order, merge_coincident

TOL = 1e-9


def reference(xyz: Any, f: Any, tol: float = TOL, *, segment: Any = None) -> tuple[np.ndarray, int, int]:
    """The scan along x that `merge_coincident` was before the review: pairs (i, i + k) within tol in x."""
    p, v = np.asarray(xyz, dtype=np.float64), np.asarray(f, dtype=np.float64)
    n = len(v)
    seg = None if segment is None else np.asarray(segment)

    def close(k: int, idx: np.ndarray) -> np.ndarray:
        ok = p[idx + k, 0] - p[idx, 0] <= tol
        if seg is not None:
            ok &= seg[idx + k] == seg[idx]
        return ok

    ii: list[np.ndarray] = []
    jj: list[np.ndarray] = []
    if n > 1:
        idx = np.flatnonzero(close(1, np.arange(n - 1)))
        k = 1
        while idx.shape[0]:
            j = idx + k
            near = ((p[idx] - p[j]) ** 2).sum(axis=1) <= tol * tol
            if near.any():
                ii.append(idx[near])
                jj.append(j[near])
            k += 1
            idx = idx[idx + k < n]
            idx = idx[close(k, idx)]
    drop = np.zeros(n, dtype=bool)
    conflicts = 0
    if ii:
        pi, pj = np.concatenate(ii), np.concatenate(jj)
        same = v[pi] == v[pj]
        order = np.argsort(pj[same], kind="stable")
        for a, b in zip(pi[same][order].tolist(), pj[same][order].tolist(), strict=True):
            if not drop[a]:
                drop[b] = True
        other = ~same
        conflicts = int((~drop[pi[other]] & ~drop[pj[other]]).sum())
    return np.flatnonzero(~drop), int(drop.sum()), conflicts


@pytest.mark.parametrize("seed", range(6))
def test_same_rows_as_the_scan_along_x_on_random_sets(seed: int) -> None:
    rng = np.random.default_rng(seed)
    n = 3000
    base = rng.integers(0, 12, (n, 3)).astype(np.float64)  # a coarse lattice: many exact and near duplicates
    xyz = base + rng.choice([0.0, 4e-10, -7e-10, 2e-9, 1e-9], (n, 3))
    f = rng.integers(0, 3, n).astype(np.float64)
    seg = np.sort(rng.integers(0, 3, n))
    for segment in (None, seg):
        # the rows are in the canonical order: by x (inside a segment when there are segments)
        keys = (f, xyz[:, 2], xyz[:, 1], xyz[:, 0]) + (() if segment is None else (seg,))
        o = np.lexsort(keys)
        xyz, f, seg = xyz[o], f[o], seg[o]
        segment = None if segment is None else seg
        stats: dict[str, int] = {}
        keep = merge_coincident(xyz, f, segment=segment, stats=stats)
        ref, merged, conflicts = reference(xyz, f, segment=segment)
        assert keep.tobytes() == ref.tobytes()
        assert stats == {"merged": merged, "conflicts": conflicts}
        assert merged > 0 and conflicts > 0


def test_a_non_finite_row_neither_merges_nor_breaks_the_tree() -> None:
    xyz = np.array([[0.0, 0, 0], [0.0, 0, 0], [np.nan, 0, 0], [np.nan, 0, 0], [1.0, np.inf, 0]])
    keep = merge_coincident(xyz, np.zeros(5))
    assert keep.tolist() == [0, 2, 3, 4]


def test_tolerance_edge_is_the_same_exact_test() -> None:
    xyz = np.array([[0.0, 0, 0], [TOL, 0, 0], [2 * TOL, 0, 0], [3.0, 0, 0], [3.0, 0, 1.0000001e-9]])
    f = np.zeros(5)
    ref, merged, _ = reference(xyz, f)
    keep = merge_coincident(xyz, f)
    assert keep.tolist() == ref.tolist() and merged == len(xyz) - len(keep)


def test_a_fan_of_vertical_holes_is_not_quadratic_and_ticks() -> None:
    """200 holes × 4000 points on common (x, y): the old scan took 93 s; the tree takes seconds."""
    rng = np.random.default_rng(0)
    holes, per = 200, 4000
    x = np.repeat(rng.uniform(0, 5000, holes), per)
    y = np.repeat(rng.uniform(0, 5000, holes), per)
    z = np.tile(-np.arange(per) * 0.5, holes)
    p = np.column_stack([x, y, z])
    f = rng.normal(size=len(p))
    o = canonical_order(p, f, np.zeros(len(p), np.int8))
    p, f = p[o], f[o]
    beats: list[int] = []
    t0 = time.perf_counter()
    keep = merge_coincident(p, f, segment=np.zeros(len(p), np.int64), tick=lambda *_: beats.append(1))
    assert time.perf_counter() - t0 < 5.0
    assert len(keep) == len(p) and beats


def test_a_cancel_from_the_tick_stops_the_merge() -> None:
    class Cancel(Exception):
        pass

    def stop(*_: Any) -> None:
        raise Cancel

    xyz = np.array([[0.0, 0, 0], [1.0, 0, 0]])
    with pytest.raises(Cancel):
        merge_coincident(xyz, np.zeros(2), segment=np.array([0, 0]), tick=stop)
