"""The warm-up of the kernels of the charts and the statistics of Э13 (13.8; ADR 0019 «Замеры»): `stats.warm`
compiles the kernels of the charts (track Г), calls `warm()` of the modules of swath (track С), goes on after a
module that fails, and leaves nothing to compile for the read-only columns the routes give the kernels."""

from __future__ import annotations

import numpy as np
import pytest

from bf_core.stats import contact
from bf_core.stats.select import SampleFilter

LABELS = ["GRANITE", "ORE", "SCHIST"]


def table(n_holes: int, rows: int, seed: int = 0):  # type: ignore[no-untyped-def]
    """Samples of `n_holes` holes sorted by (hole, FROM), as `stats.source` reads a table: hole, frm, to, codes."""
    rng = np.random.default_rng(seed)
    to = np.cumsum(rng.choice([0.5, 1.0], (n_holes, rows)), axis=1)
    frm = np.concatenate((np.zeros((n_holes, 1)), to[:, :-1]), axis=1)
    hole = np.repeat(np.arange(n_holes), rows)
    return hole, frm.reshape(-1), to.reshape(-1), rng.integers(-1, 3, n_holes * rows).astype(np.int32)


def test_warm_compiles_the_kernels_and_calls_every_module(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    from types import ModuleType

    from bf_core.stats import warm as stats_warm

    contact.warm()
    for kernel in (contact._domain_strip, contact._bin, contact._crossing_rows):
        assert kernel.signatures, kernel
    # `warm()` goes through the modules of the charts (Г) and of swath and the tab «Статистика» (С)
    names = stats_warm.MODULES
    assert {"select", "describe", "histogram", "qq", "dendrogram", "scatter", "boxplot", "probplot", "domain_stats",
            "ternary", "contact", "swath", "swath_blocks"} <= set(names)  # fmt: skip
    called: list[str] = []
    for name in names:
        full = f"bf_core.stats.{name}"
        try:
            module = __import__(full, fromlist=["warm"])
        except ModuleNotFoundError:  # a module of another track that is not merged yet: `warm` skips it
            module = ModuleType(full)
            monkeypatch.setitem(sys.modules, full, module)
        monkeypatch.setattr(module, "warm", lambda name=name: called.append(name), raising=False)
    monkeypatch.setattr(stats_warm, "columns", lambda: called.append("columns"))
    took = stats_warm.warm()
    assert called == [*names, "columns"] and set(took) == {*names, "columns"}


def test_warm_goes_on_after_a_module_that_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from bf_core.stats import histogram
    from bf_core.stats import warm as stats_warm

    def broken() -> None:
        raise RuntimeError("no kernel")

    monkeypatch.setattr(histogram, "warm", broken)
    monkeypatch.setattr(stats_warm, "MODULES", ("histogram", "no_such_module", "contact"))
    took = stats_warm.warm()
    assert set(took) == {"contact", "columns"} and "histogram" in caplog.text


def test_warm_leaves_nothing_to_compile_for_the_columns_of_the_api() -> None:
    """After `warm()` the charts on the read-only columns of `stats.source` (numba compiles a kernel anew for a
    read-only array) compile nothing: the number of specializations of every kernel stays as it was."""
    import sys
    from types import SimpleNamespace

    from bf_core.stats import boxplot, correlation, dendrogram, domain_stats, scatter, select, swath, ternary
    from bf_core.stats import swath_frame as sf
    from bf_core.stats import warm as stats_warm

    assert set(stats_warm.warm()) >= {"select", "contact", "swath", "columns"}

    def compiled() -> dict[str, int]:
        out = {}
        for name, module in list(sys.modules.items()):
            if name.startswith("bf_core."):
                for key, obj in vars(module).items():
                    if hasattr(obj, "signatures") and hasattr(obj, "py_func"):
                        out[f"{name}.{key}"] = len(obj.signatures)
        return out

    before = compiled()
    assert len(before) > 30 and before["bf_core.stats.select._take"] >= 3  # writable, read-only, read-only + weights
    rng = np.random.default_rng(0)
    frozen = stats_warm.frozen
    hole, frm, to, codes = table(40, 300)
    frm, to, codes = frozen(frm), frozen(to), frozen(codes)
    n = len(hole)
    values, other, third = (frozen(rng.lognormal(0.0, 1.0, n)) for _ in range(3))
    lengths, field, row_ids = frozen(to - frm), frozen(rng.uniform(1.0, 3.0, n)), frozen(np.arange(n))
    hits = SimpleNamespace(  # every hole goes into a solid at 50 m and out of it at 120 m
        hole=np.repeat(np.arange(40), 2), depth=np.tile([50.0, 120.0], 40),
        event=np.tile(np.array([contact.ENTER, contact.LEAVE], dtype=np.uint8), 40), side=np.tile([-1, 1], 40),
    )  # fmt: skip
    for weight in select.WEIGHTS:
        flt = SampleFilter(weight=weight, weight_field="W" if "field" in weight else None)
        sel = select.select_sample(values, flt, length=lengths, weight_values=field)
        boxplot.box_chart(sel, codes, LABELS)
        domain_stats.domain_stats(sel, codes, LABELS, length=lengths, decl_weights=field)
        r = contact.contact_chart(hole.astype(np.int32), frm, to, codes, LABELS, "ORE", "SCHIST", values, flt,
                                  length=lengths, weight_values=field)  # fmt: skip
        assert r["contacts"] > 100
        r = contact.contact_chart_wireframe(hole, frm, to, hits, values, flt, length=lengths, weight_values=field)
        assert r["contacts"] == 80 and r["counts"]["in_steps"] > 1000
        w = select.sample_weights(weight, lengths, field)
        for method in correlation.METHODS:
            dendrogram.dendrogram_chart(["A", "B", "C"], [values, other, third], w, correlation.CorrSpec(method=method))
        for color in (None, field, codes):
            scatter.scatter_chart(np.array(values), np.array(other), w, color=color, row_ids=row_ids)
        scatter.scatter_chart(np.array(values), np.array(other), w, row_ids=row_ids, max_points=1000)
    ternary.shares(values, other, third)
    # a swath of samples: their middles come read-only from the cache
    points = frozen(rng.uniform(0.0, 50.0, (n, 3)))
    f = sf.frame(30.0, -10.0, 5.0)
    swath.sample_series(points, sel, f, swath.cuboid(f, samples=[swath.sample_bounds(f, points, sel)], width=2.0))
    blocks(rng, points, sel, f)
    after = compiled()
    assert {k: (before.get(k), v) for k, v in after.items() if before.get(k) != v} == {}


def blocks(rng, points, sel, f) -> None:  # type: ignore[no-untyped-def]
    """The swath of a block model and of samples «внутри блоков» as `routes_swath` makes them: the columns of
    `stats.blocks_cache` (read-only; `idx` uint32 below 2³² cells, else uint64), `keep` of the route (writable);
    a model of several groups and a tail (`GROUP` made small), with a selection and without, every weight."""
    from bf_core.stats import swath
    from bf_core.stats import swath_blocks as sb
    from bf_core.stats import warm as stats_warm

    frozen = stats_warm.frozen
    grid = {"corner": [0.0, 0.0, 0.0], "counts": [10, 10, 10], "size": [5.0, 5.0, 5.0], "order": "F"}
    for dtype in (np.uint32, np.uint64):
        idx = frozen(np.sort(rng.choice(1000, 700, replace=False)).astype(dtype))
        m = idx.shape[0]
        values, density = frozen(rng.lognormal(0.0, 1.0, m)), frozen(rng.uniform(2.0, 3.0, m))
        codes, keep = frozen(rng.integers(-1, 3, m).astype(np.int32)), np.asarray(sb.keep_of(LABELS, ["ORE"]))
        for group in (sb.GROUP, 128):
            old, sb.GROUP = sb.GROUP, group
            try:
                ext = [sb.block_bounds(f, grid, idx), sb.block_bounds(f, grid, idx, codes=codes, keep=keep)]
                c = swath.cuboid(f, models=ext, samples=[swath.sample_bounds(f, points, sel)], width=10.0)
                for weight in sb.WEIGHTS:
                    dens = density if weight == "tonnage" else None
                    r = sb.block_series(grid, idx, values, f, c, weight=weight, density=dens)
                    assert r.counts["rows"] == m and r.total["count"] > 0
                    sb.block_series(grid, idx, values, f, c, weight=weight, density=dens, codes=codes, keep=keep)
            finally:
                sb.GROUP = old
        for cells in (swath.cells_of(grid, idx), swath.cells_of(grid, sb.selected_idx(idx, codes, keep))):
            swath.sample_series(points, sel, f, c, cells=cells)
            swath.points_in_cells(points, cells, method="search")
            swath.points_in_cells(points, cells, method="bits")
