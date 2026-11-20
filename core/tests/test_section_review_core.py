"""Э10, 10.28 — the review of the core of the series of sections, without the API:

* (1) the cache of contours keeps to `CACHE_BYTES` counting every array it holds (the copy of the vertices of a
  series of several layers, the layers a series refers to), whatever the number of versions;
* (4) `trim` spares a folder a job is writing into; a commit into a folder that went skips the file; a job that
  got past its last tick after a cancel does not put an older or a narrower window over a newer one;
* (5) a file of the cache is written section by section: the memory of the job is one section, not all of them
  and their copies; the estimate of its memory grows with the triangles and the sections;
* (7) the section of a depth range: a big own window of section 0 or windows ≫ Δ cost no walk over the series,
  and the answers are the brute force ones; the links of 1 500 × 1 500 contours are found in bounded buffers, the
  same as in one.
"""

from __future__ import annotations

import os
import shutil
import time
import tracemalloc
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.geo.section import Section
from bf_core.sections import cache_files, cache_job, links
from bf_core.sections import contours as sc_mod
from bf_core.sections.geometry import SeriesGeometry, series_geometry

KEY, WF = "a" * 32, "f" * 32
P1 = [512_000.0, 6_789_000.0, 0.0]


def vertical(**more: Any) -> SeriesGeometry:
    g = series_geometry({"type": "vertical", "p1": P1, "az": 90, "s0": 0, "s1": 100, "step": 25, **more})
    assert isinstance(g, SeriesGeometry)
    return g


# --- (1) the cache of contours -----------------------------------------------------------------------------------


def _root(a: np.ndarray) -> np.ndarray:
    while isinstance(a.base, np.ndarray):
        a = a.base
    return a


def held_bytes() -> int:
    """The bytes of every array the two caches hold, each buffer once."""
    seen: dict[int, int] = {}

    def add(*arrays: np.ndarray) -> None:
        for a in arrays:
            r = _root(a)
            seen[id(r)] = r.nbytes

    def lines(v: sc_mod.LayerLines) -> None:
        add(v.xyz, v.offsets, v.lid)

    for v in sc_mod._lines.values():
        lines(v)
    for s in sc_mod._series.values():
        add(s.xyz, s.offsets, s.layer, s.lid, s.section, s.off)
        for v in s.layers:
            lines(v)
    return sum(seen.values())


def ring_layer(g: SeriesGeometry, layer_id: int, rev: int, n: int, k: int = 2) -> sc_mod.LayerLines:
    a = np.linspace(0.0, 2 * np.pi, n, endpoint=False)
    xyz = g.origin(k)[None, :] + 50.0 * np.cos(a)[:, None] * g.s[None, :] + (100.0 + 50.0 * np.sin(a))[:, None] * g.w
    return sc_mod.LayerLines(layer_id, f"Слой {layer_id}", rev, np.ascontiguousarray(xyz), np.array([0, n]),
                             np.array([0]))  # fmt: skip


def test_the_cache_of_contours_keeps_to_its_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    sc_mod.clear_caches()
    limit = 40 << 20
    monkeypatch.setattr(sc_mod, "CACHE_BYTES", limit)
    g = vertical()
    try:
        for version in range(64):  # 64 versions of a series of two layers of 6 MB each
            layers = [ring_layer(g, i, version + 1, 250_000) for i in (1, 2)]
            for layer in layers:
                sc_mod._remember(sc_mod._lines, ("u", layer.layer_id, layer.rev), layer)
            sc = sc_mod.series_contours(g, layers)
            assert sc.count == 2 and sc.section.tolist() == [2, 2]
            sc_mod._remember(sc_mod._series, ("u", 7, version, "[]"), sc)
            assert held_bytes() <= limit, version
        assert len(sc_mod._series) >= 1  # the newest stays
        one = ring_layer(g, 3, 1, 1000)
        assert np.shares_memory(sc_mod.series_contours(g, [one]).xyz, one.xyz)  # one layer: no copy
    finally:
        sc_mod.clear_caches()


# --- (4) the files of the cache: races ----------------------------------------------------------------------------


def cuts(n: int, points: int = 4) -> list[Section]:
    rng = np.random.default_rng(n)
    return [Section(rng.random((points, 3)), np.array([0, points]), np.array([True])) for _ in range(n)]


def put(folder: Path, first: int, count: int, tag: int, total: int = 600) -> cache_files.Staged:
    st = cache_files.Staged(folder, KEY, tag)
    st.write(WF, "k", first, total, iter(cuts(count)))
    return st


def tmps(folder: Path) -> list[Path]:
    return sorted(cache_files.root(folder).rglob("*.tmp"))


def test_trim_spares_a_folder_being_written(tmp_path: Path) -> None:
    other = cache_files.series_dir(tmp_path, "b" * 32)
    other.mkdir(parents=True)
    (other / f"{WF}.bfa").write_bytes(b"\0" * 1000)
    os.utime(other, (1000.0, 1000.0))
    st = put(tmp_path, 0, 101, 1)
    os.utime(cache_files.series_dir(tmp_path, KEY), (10.0, 10.0))  # the oldest: it would go first
    assert cache_files.trim(tmp_path, limit=0) == ["b" * 32]
    st.commit()
    assert cache_files.coverage(tmp_path, KEY, WF) == (0, 101) and tmps(tmp_path) == []


def test_a_commit_into_a_folder_that_went_skips_it(tmp_path: Path) -> None:
    st = put(tmp_path, 0, 101, 1)
    shutil.rmtree(cache_files.series_dir(tmp_path, KEY))  # trimmed by another job meanwhile
    assert st.commit() == [WF]  # the job is not failed: the sections are just not cached
    assert cache_files.coverage(tmp_path, KEY, WF) is None
    st = put(tmp_path, 0, 101, 2)
    shutil.rmtree(cache_files.series_dir(tmp_path, KEY))
    st.write("e" * 32, "k", 0, 600, iter(cuts(101)))  # the folder made again
    assert st.commit() == [WF]
    assert cache_files.coverage(tmp_path, KEY, WF) is None and cache_files.coverage(tmp_path, KEY, "e" * 32) == (0, 101)
    assert tmps(tmp_path) == []


def test_a_late_commit_keeps_the_newer_window(tmp_path: Path) -> None:
    older = put(tmp_path, 0, 101, 1)  # cancelled after its last tick: it still commits
    time.sleep(0.01)
    newer = put(tmp_path, 250, 101, 2)
    newer.commit()
    older.commit()
    assert cache_files.coverage(tmp_path, KEY, WF) == (250, 101) and tmps(tmp_path) == []
    # the other order: the older one first, then the newer one replaces it
    older, newer = put(tmp_path, 0, 101, 3), None
    time.sleep(0.01)
    newer = put(tmp_path, 400, 101, 4)
    older.commit()
    newer.commit()
    assert cache_files.coverage(tmp_path, KEY, WF) == (400, 101)


def test_a_narrower_window_does_not_replace_a_wider_one(tmp_path: Path) -> None:
    put(tmp_path, 500, 100, 1).commit()
    os.utime(cache_files.file_path(tmp_path, KEY, WF), (1000.0, 1000.0))
    put(tmp_path, 549, 51, 2).commit()  # k ± 50 at the end of the series: inside the file there
    assert cache_files.coverage(tmp_path, KEY, WF) == (500, 100)
    put(tmp_path, 300, 101, 3).commit()  # another window: replaced
    assert cache_files.coverage(tmp_path, KEY, WF) == (300, 101) and tmps(tmp_path) == []


# --- (5) the memory of writing a file -------------------------------------------------------------------------


def lazy_cuts(n: int, points: int) -> Iterator[Section]:
    for i in range(n):
        xyz = np.full((points, 3), float(i))
        yield Section(xyz, np.array([0, points // 2, points]), np.array([True, False]))


def test_a_file_is_written_section_by_section(tmp_path: Path) -> None:
    n, points = 100, 20_000  # 48 MB of points in all, 0.5 MB a section
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        base = tracemalloc.get_traced_memory()[0]
        st = cache_files.Staged(tmp_path, KEY, 1)
        st.write(WF, "k", 0, n, lazy_cuts(n, points))
        peak = tracemalloc.get_traced_memory()[1] - base
    finally:
        tracemalloc.stop()
    assert peak < 4 * points * 24, peak  # a few sections, not 48 MB
    st.commit()
    assert cache_files.coverage(tmp_path, KEY, WF) == (0, n)
    for k in (0, 57, n - 1):
        s = cache_files.read_slot(tmp_path, KEY, WF, k)
        assert s is not None and s.offsets.tolist() == [0, points // 2, points] and s.closed.tolist() == [True, False]
        assert (s.xyz == float(k)).all() and s.xyz.shape == (points, 3)


def test_encode_file_makes_one_copy() -> None:
    data = cuts(100, 20_000)
    size = 100 * 20_000 * 24
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        base = tracemalloc.get_traced_memory()[0]
        out = cache_files.encode_file(KEY, WF, "k", 0, 100, data)
        peak = tracemalloc.get_traced_memory()[1] - base
    finally:
        tracemalloc.stop()
    assert len(out) >= size and peak < 1.3 * size, peak / size


def test_the_estimate_of_memory_follows_the_input() -> None:
    small = cache_job._memory({"triangles": 1_000, "count": 5})
    big = cache_job._memory({"triangles": 10_000_000, "count": 101})
    assert 128 <= small <= 300 and small < big <= 1024


# --- (7) the search of the section of a depth range ------------------------------------------------------------


def brute(g: SeriesGeometry, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """The rule of `kernels.find_sections` without its shortcuts: the windows that hold [a, b], the nearest plane
    to (a + b)/2, the smaller k on a tie."""
    out = np.full(len(a), -1, dtype=np.int64)
    lo_min, hi_max = float(g.lo.min()), float(g.hi.max())
    for i in range(len(a)):
        x, y = float(a[i]), float(b[i])
        if not (x >= lo_min and y < hi_max and x <= y):
            continue
        hold = np.flatnonzero((g.lo <= x) & (y < g.hi))
        if len(hold):
            m = 0.5 * x + 0.5 * y
            d = np.abs(m - g.offs[hold])
            out[i] = hold[int(np.argmin(d))]  # argmin: the first of the smallest
    return out


SERIES_7 = {"p1": [0.0, 0.0, 0.0], "s0": 0, "s1": 4999, "step": 1}
GEOMETRIES_7 = [
    {"towards": 0.5, "away": 0.5, "overrides": {"0": {"towards": 5000}, "4999": {"away": 5000}}},  # a partition
    {"towards": 0.5, "away": 0.5, "overrides": {"2": {"away": 0.8}, "3": {"towards": 0.2}, "0": {"towards": 900}}},
    {"towards": 3000, "away": 3000},  # every window ≫ Δ
    {"towards": 0.3, "away": 0.4},  # gaps
    {"towards": 0.5, "away": 0.5, "overrides": {"7": {"towards": 2000, "away": 0.25}}},  # one big window, a gap
    {"towards": 1.5, "away": 0.25, "overrides": {"10": {"away": 4000}}},
]


@pytest.mark.parametrize("params", GEOMETRIES_7)
def test_the_section_of_a_range_is_the_brute_force_one(params: dict[str, Any]) -> None:
    g = vertical(**SERIES_7, **params)
    rng = np.random.default_rng(7)
    tau = np.concatenate([
        rng.uniform(-6000, 11000, 1500), rng.uniform(-2, 30, 1500), rng.uniform(4970, 5002, 500),
        g.lo[:40], g.hi[:40], g.offs[:40], np.nextafter(g.hi[:40], -np.inf), g.lo[-20:], g.hi[-20:], [np.nan],
    ])  # fmt: skip
    from bf_core.sections import kernels

    assert (g.section_of_depth(tau) == brute(g, tau, tau)).all()
    a = rng.uniform(-50, 5050, 3000)
    b = a + np.concatenate([rng.uniform(0, 0.6, 1500), rng.uniform(0, 8000, 1500)])
    out = np.empty(len(a), dtype=np.int64)
    kernels.find_sections(a, b, *g._args(), out)
    assert (out == brute(g, a, b)).all()


def _time(fn: Any) -> float:
    fn()
    best = np.inf
    for _ in range(3):
        t = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t)
    return float(best)


@pytest.mark.parametrize(
    ("big", "plain"),
    [
        ({"towards": 0.5, "away": 0.5, "overrides": {"0": {"towards": 5000}}}, {"towards": 0.5, "away": 0.5}),
        ({"towards": 3000, "away": 3000}, {"towards": 0.3, "away": 0.4}),
    ],
)
def test_a_big_window_costs_no_walk_over_the_series(big: dict[str, Any], plain: dict[str, Any]) -> None:
    tau = np.random.default_rng(3).uniform(0, 40, 100_000)
    gb, gp = vertical(**SERIES_7, **big), vertical(**SERIES_7, **plain)
    assert gb.partition == gp.partition
    tb = _time(lambda: gb.section_of_depth(tau))
    tp = _time(lambda: gp.section_of_depth(tau))
    assert tb <= 10 * tp + 5e-3, (tb, tp)


# --- (7) the links ---------------------------------------------------------------------------------------------


def squares(n: int, k: int, shift: float) -> list[np.ndarray]:
    return [np.array([[u, w], [u + 1, w], [u + 1, w + 1], [u, w + 1]]) + shift
            for u, w in ((3.0 * (i % 40), 3.0 * (i // 40)) for i in range(n))]  # fmt: skip


def series_of(polys: list[np.ndarray], ks: list[int]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    uw = np.vstack(polys)
    offsets = np.concatenate([[0], np.cumsum([len(p) for p in polys])]).astype(np.int64)
    return uw, offsets, np.array(ks, dtype=np.int64)


def test_links_in_bounded_buffers_are_the_same(monkeypatch: pytest.MonkeyPatch) -> None:
    n = 1500
    polys = squares(n, 0, 0.0) + squares(n, 1, 0.5) + squares(7, 2, 0.2)
    uw, offsets, section = series_of(polys, [0] * n + [1] * n + [2] * 7)
    real = links._kernels()
    sizes: list[int] = []

    class Spy:
        def __getattr__(self, name: str) -> Any:
            return getattr(real, name)

        def section_pair(self, uw, offsets, lower, upper, *rest):  # type: ignore[no-untyped-def]
            sizes.append(len(rest[-3]))
            return real.section_pair(uw, offsets, lower, upper, *rest)

    monkeypatch.setattr(links, "_kernels", lambda: Spy())
    pairs, how = links.auto_links(uw, offsets, section, 2.0)
    assert sizes and max(sizes) <= (1 << 20) + n, max(sizes)
    monkeypatch.setattr(links, "PAIR_BUFFER", 1 << 40, raising=False)
    whole, whole_how = links.auto_links(uw, offsets, section, 2.0)
    monkeypatch.setattr(links, "PAIR_BUFFER", 1000, raising=False)
    small, small_how = links.auto_links(uw, offsets, section, 2.0)
    assert len(pairs) > n
    assert (pairs == whole).all() and (how == whole_how).all() and (pairs == small).all() and (how == small_how).all()


def test_the_pairs_of_sections_are_found_once(monkeypatch: pytest.MonkeyPatch) -> None:
    polys = [p for k in range(300) for p in squares(1, k, 0.1 * k)]
    uw, offsets, section = series_of(polys, list(range(0, 600, 2))[:150] + list(range(1, 151)))
    calls = [0]

    def counted(*args: Any) -> set[Any]:
        calls[0] += 1
        return set(*args)

    monkeypatch.setattr(links, "set", counted, raising=False)
    pairs, _how = links.auto_links(uw, offsets, section, 2.0)
    assert calls[0] <= 1 and len(pairs) > 0
    ks = section[pairs]
    assert (ks[:, 1] == ks[:, 0] + 1).all()
