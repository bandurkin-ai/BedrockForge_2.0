"""Scatter plot (ТЗ 8.3; ADR 0019 §4): Т8.3-1 (МНК and RMA of y = 2x + 1), the density of a large cloud, the rows
inside a lasso against a direct count (Т8.3-3, the kernel), pairs with a missing value.

Tolerance — 1e−12 for a, b and r against the formulas counted directly (numpy, scipy, exact fractions); the
density and the rows of a lasso — exact."""

from __future__ import annotations

import json
import math
from fractions import Fraction

import numpy as np
import pytest
from scipy.stats import spearmanr

from bf_core.stats import scatter
from bf_core.stats.scatter import (
    CELLS,
    MAX_POINTS,
    density,
    grid_counts,
    pairs,
    regressions,
    rows_in_polygon,
    rows_in_rect,
    scatter_chart,
    spearman,
)
from bf_core.stats.weighted import StatsError

NAN = math.nan
REL = 1e-12


def refused(code: str, param: str | None, call) -> StatsError:  # type: ignore[no-untyped-def]
    with pytest.raises(StatsError) as e:
        call()
    assert e.value.code == code and e.value.param == param and e.value.message and e.value.hint
    return e.value


def direct(x: np.ndarray, y: np.ndarray, w: np.ndarray | None = None) -> dict[str, float]:
    """The formulas of ADR 0019 §4 counted directly."""
    w = np.ones(x.shape) if w is None else w
    total = w.sum()
    mx, my = np.sum(w * x) / total, np.sum(w * y) / total
    sxx, syy, sxy = np.sum(w * (x - mx) ** 2), np.sum(w * (y - my) ** 2), np.sum(w * (x - mx) * (y - my))
    r = sxy / math.sqrt(sxx * syy)
    b_ols, b_rma = sxy / sxx, math.copysign(math.sqrt(syy / sxx), r)
    return {"r": r, "b_ols": b_ols, "a_ols": my - b_ols * mx, "b_rma": b_rma, "a_rma": my - b_rma * mx,
            "mx": mx, "my": my, "sx": math.sqrt(sxx / total), "sy": math.sqrt(syy / total)}  # fmt: skip


def inside_exact(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    """The even-odd rule in exact fractions: the sides a ray to the right of the point crosses."""
    fx, fy = Fraction(x), Fraction(y)
    odd = False
    for t in range(len(poly)):
        (xt, yt), (xj, yj) = (Fraction(c) for c in poly[t]), (Fraction(c) for c in poly[t - 1])
        if (yt > fy) != (yj > fy) and fx < (xj - xt) * (fy - yt) / (yj - yt) + xt:
            odd = not odd
    return odd


def cloud(n: int = 2000, seed: int = 3):  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(seed)
    x = rng.lognormal(0.0, 1.0, n)
    y = x**0.8 * rng.lognormal(0.2, 0.5, n)
    return x, y, rng.choice([0.5, 1.0, 1.5, 3.0], n)


# --- acceptance of ТЗ 8.3 ----------------------------------------------------------------------------------


def test_t83_1_line_without_noise_gives_b_2_a_1() -> None:
    """Т8.3-1: y = 2x + 1 without noise — МНК and RMA give b = 2, a = 1 (1e−12)."""
    rng = np.random.default_rng(831)
    x = rng.uniform(0.0, 50.0, 5000)
    y = 2.0 * x + 1.0
    w = rng.choice([0.5, 1.0, 2.0], 5000)
    r = scatter_chart(x, y, w)
    s = r["stats"]
    for line in (s["ols"], s["rma"], s["weighted"]):
        assert line["b"] == pytest.approx(2.0, rel=REL) and line["a"] == pytest.approx(1.0, rel=REL)
    assert s["pearson"] == pytest.approx(1.0, abs=REL) and s["spearman"] == 1.0 and s["n"] == 5000
    assert s["weighted"]["pearson"] == pytest.approx(1.0, abs=REL)
    # a falling line: RMA takes the sign of r
    down = regressions(pairs(x, -2.0 * x + 1.0))
    assert down["rma"]["b"] == pytest.approx(-2.0, rel=REL) and down["rma"]["a"] == pytest.approx(1.0, rel=REL)
    assert down["ols"]["b"] == pytest.approx(-2.0, rel=REL) and down["pearson"] == pytest.approx(-1.0, abs=REL)
    # log axes: y = 3·x² is the line log10 y = log10 3 + 2·log10 x
    xs = rng.lognormal(0.0, 1.0, 3000)
    logs = scatter_chart(xs, 3.0 * xs**2, log_x=True, log_y=True)["stats"]
    for line in (logs["ols"], logs["rma"]):
        assert line["b"] == pytest.approx(2.0, rel=REL) and line["a"] == pytest.approx(math.log10(3.0), rel=1e-11)
    assert logs["weighted"] is None


# --- regressions, correlations -----------------------------------------------------------------------------


@pytest.mark.parametrize("log_x", [False, True])
@pytest.mark.parametrize("log_y", [False, True])
def test_regressions_equal_the_formulas(log_x: bool, log_y: bool) -> None:
    x, y, w = cloud()
    r = scatter_chart(x, y, w, log_x=log_x, log_y=log_y)
    u, v = (np.log10(x) if log_x else x), (np.log10(y) if log_y else y)
    plain, s = direct(u, v), r["stats"]
    for kind in ("ols", "rma"):
        assert s[kind]["b"] == pytest.approx(plain[f"b_{kind}"], rel=REL)
        assert s[kind]["a"] == pytest.approx(plain[f"a_{kind}"], rel=REL, abs=REL)
    assert s["pearson"] == pytest.approx(plain["r"], abs=REL)
    assert s["x"] == pytest.approx({"mean": plain["mx"], "std": plain["sx"]}, rel=REL)
    assert s["y"] == pytest.approx({"mean": plain["my"], "std": plain["sy"]}, rel=REL)
    assert s["ols"]["b"] == pytest.approx(np.polyfit(u, v, 1)[0], rel=1e-9)
    weighted = direct(u, v, w)
    assert s["weighted"]["b"] == pytest.approx(weighted["b_ols"], rel=REL)
    assert s["weighted"]["a"] == pytest.approx(weighted["a_ols"], rel=REL, abs=REL)
    assert s["weighted"]["pearson"] == pytest.approx(weighted["r"], abs=REL)
    assert s["weighted"]["x"]["mean"] == pytest.approx(weighted["mx"], rel=REL)
    assert s["weight_sum"] == pytest.approx(w.sum(), rel=REL) and s["n"] == 2000
    # ranks do not change under a logarithm
    assert s["spearman"] == pytest.approx(spearmanr(x, y).statistic, abs=REL)
    assert r["log_x"] is log_x and r["log_y"] is log_y and r["weighted"] is True and r["conventions"]
    json.dumps(r, allow_nan=False)


def test_integer_weights_equal_repeated_rows_and_ties_in_ranks() -> None:
    x = np.array([1.0, 2.0, 2.0, 4.0, 5.0, 5.0, 5.0, 8.0])
    y = np.array([2.0, 1.0, 4.0, 4.0, 3.0, 7.0, 7.0, 6.0])
    w = np.array([1.0, 3.0, 2.0, 1.0, 4.0, 1.0, 2.0, 3.0])
    rep = np.repeat(np.arange(8), w.astype(int))
    s = regressions(pairs(x, y, w))
    again = regressions(pairs(x[rep], y[rep]))
    assert s["weighted"]["b"] == pytest.approx(again["ols"]["b"], rel=REL)
    assert s["weighted"]["a"] == pytest.approx(again["ols"]["a"], rel=REL)
    assert s["weighted"]["pearson"] == pytest.approx(again["pearson"], abs=REL)
    assert s["ols"]["b"] == pytest.approx(direct(x, y)["b_ols"], rel=REL)
    assert spearman(pairs(x, y, w)) == pytest.approx(spearmanr(x, y).statistic, abs=REL)
    # two threads for large clouds: the same bits
    xs, ys, _ = cloud(5000, seed=8)
    p = pairs(np.round(xs, 1), ys)
    assert spearman(p) == spearman(p, parallel_from=10)
    assert spearman(p) == pytest.approx(spearmanr(np.round(xs, 1), ys).statistic, abs=REL)


def test_pair_with_a_missing_value_takes_no_part() -> None:
    x = np.array([1.0, NAN, 3.0, 4.0, np.inf, 6.0, 7.0, -2.0, 0.0, 10.0])
    y = np.array([2.0, 5.0, NAN, 8.0, 9.0, 13.0, 15.0, 3.0, 4.0, -np.inf])
    w = np.array([1.0, 1.0, 1.0, 0.0, 1.0, 2.0, NAN, 1.0, 3.0, 1.0])
    p = pairs(x, y)
    assert p.rows.tolist() == [0, 3, 5, 6, 7, 8] and p.counts == {"rows": 10, "missing": 4, "bad_weight": 0,
                                                                  "nonpositive": 0, "used": 6}  # fmt: skip
    assert p.w is None and np.array_equal(p.u, x[p.rows]) and np.array_equal(p.v, y[p.rows])
    s = regressions(p)
    assert s["ols"]["b"] == pytest.approx(direct(x[p.rows], y[p.rows])["b_ols"], rel=REL) and s["weighted"] is None
    pw = pairs(x, y, w)
    assert pw.rows.tolist() == [0, 5, 7, 8] and pw.counts == {"rows": 10, "missing": 4, "bad_weight": 2,
                                                              "nonpositive": 0, "used": 4}  # fmt: skip
    assert pw.w is not None and pw.w.tolist() == [1.0, 2.0, 1.0, 3.0]
    # a log axis: values ≤ 0 are counted and take no part; a missing value is never 0
    pl = pairs(x, y, w, log_x=True)
    assert pl.rows.tolist() == [0, 5] and pl.counts == {"rows": 10, "missing": 4, "bad_weight": 2, "nonpositive": 2,
                                                        "used": 2}  # fmt: skip
    assert pl.u.tolist() == [0.0, math.log10(6.0)] and pl.v.tolist() == [2.0, 13.0] and pl.log_x and not pl.log_y
    r = scatter_chart(x, y, w, log_x=True, row_ids=np.arange(100, 110), color=np.arange(10.0) * 2)
    assert r["points"] == {"x": [1.0, 6.0], "y": [2.0, 13.0], "row": [100, 105], "c": [0.0, 10.0]}
    assert r["mode"] == "points" and r["density"] is None and r["range"] == {"x": [1.0, 6.0], "y": [2.0, 13.0]}
    assert r["stats"]["ols"] == pytest.approx({"a": 2.0, "b": 11.0 / math.log10(6.0)}, rel=REL)
    # nothing left: the refusal says why
    e = refused("no_values", None, lambda: scatter_chart(x, np.full(10, NAN)))
    assert "без значения X или Y — 10" in e.hint
    e = refused("no_values", None, lambda: scatter_chart(-np.abs(x), y, log_x=True))
    assert "≤ 0 на логарифмической оси" in e.hint
    refused("no_values", None, lambda: scatter_chart([], []))
    refused("bad_param", "log_x", lambda: pairs(x, y, log_x=1))  # type: ignore[arg-type]


def test_points_carry_rows_and_colours() -> None:
    x, y, w = cloud(50)
    codes = np.arange(50, dtype=np.int32) % 3 - 1  # the codes of a text field: −1 — empty
    grades = np.where(np.arange(50) % 7 == 0, NAN, y)
    r = scatter_chart(x, y, color=codes)
    assert r["points"]["row"] == list(range(50)) and r["points"]["c"] == codes.tolist()
    assert r["points"]["x"] == x.tolist() and r["points"]["y"] == y.tolist()
    r = scatter_chart(x, y, color=grades, with_spearman=False)
    assert r["points"]["c"] == [None if i % 7 == 0 else float(y[i]) for i in range(50)]
    assert r["stats"]["spearman"] is None and scatter_chart(x, y)["points"]["c"] is None
    json.dumps(r, allow_nan=False)


def test_hard_data() -> None:
    rng = np.random.default_rng(12)
    # far from zero: the squares are summed about the means
    x = 1e9 + rng.normal(size=60)
    y = 5e8 + 0.25 * (x - 1e9) + 0.1 * rng.normal(size=60)
    fx, fy = [Fraction(float(v)) for v in x], [Fraction(float(v)) for v in y]
    mx, my = sum(fx) / 60, sum(fy) / 60
    sxx, sxy = sum((a - mx) ** 2 for a in fx), sum((a - mx) * (b - my) for a, b in zip(fx, fy, strict=True))
    syy = sum((b - my) ** 2 for b in fy)
    s = regressions(pairs(x, y))
    assert s["ols"]["b"] == pytest.approx(float(sxy / sxx), rel=1e-10)
    assert s["ols"]["a"] == pytest.approx(float(my - sxy / sxx * mx), rel=1e-10)
    assert s["pearson"] == pytest.approx(float(sxy) / math.sqrt(float(sxx) * float(syy)), abs=REL)
    assert s["rma"]["b"] == pytest.approx(math.sqrt(float(syy / sxx)), rel=1e-10)
    # no spread of x — no line; no spread of y — a flat line, no r
    flat_x = regressions(pairs(np.full(5, 3.0), np.arange(5.0)))
    assert flat_x["ols"] is None and flat_x["rma"] is None and flat_x["pearson"] is None
    assert flat_x["x"] == {"mean": 3.0, "std": 0.0}
    flat_y = regressions(pairs(np.arange(5.0), np.full(5, 3.0)))
    assert flat_y["ols"] == {"a": 3.0, "b": 0.0} and flat_y["rma"] == {"a": 3.0, "b": 0.0} and flat_y["pearson"] is None
    assert spearman(pairs(np.arange(5.0), np.full(5, 3.0))) is None
    # one pair, two pairs
    one = scatter_chart([2.0], [3.0])
    assert one["stats"]["ols"] is None and one["stats"]["spearman"] is None and one["stats"]["n"] == 1
    assert one["stats"]["x"] == {"mean": 2.0, "std": 0.0} and one["range"] == {"x": [2.0, 2.0], "y": [3.0, 3.0]}
    two = scatter_chart([1.0, 3.0], [1.0, 5.0])["stats"]
    assert two["ols"] == {"a": -1.0, "b": 2.0} and two["pearson"] == 1.0 and two["spearman"] == 1.0
    # numbers whose squares overflow: no line rather than a wrong one
    huge = regressions(pairs(np.array([1e200, 2e200, 4e200]), np.array([1.0, 2.0, 3.0])))
    assert huge["ols"] is None and huge["pearson"] is None
    json.dumps(scatter_chart(np.array([1e200, 2e200, 4e200]), np.array([1.0, 2.0, 3.0])), allow_nan=False)


# --- the density -------------------------------------------------------------------------------------------


def test_more_than_200_000_points_become_a_density_that_adds_up() -> None:
    assert MAX_POINTS == 200_000 and CELLS == 100
    n = MAX_POINTS + 202
    rng = np.random.default_rng(5)
    x = rng.lognormal(0.0, 1.0, n)
    y = x * rng.lognormal(0.0, 0.4, n)
    x[::1000] = NAN  # 201 pairs with a missing value: 200 001 are left
    r = scatter_chart(x, y, with_spearman=False)
    assert r["mode"] == "density" and r["points"] is None and r["counts"]["used"] == MAX_POINTS + 1
    d = r["density"]
    counts = np.array(d["counts"])
    used = n - int(np.isnan(x).sum())
    assert counts.shape == (100, 100) and counts.sum() == used == r["counts"]["used"] and d["max"] == counts.max()
    ok = np.isfinite(x)
    assert d["x_edges"][0] == x[ok].min() and d["x_edges"][-1] == x[ok].max() and len(d["x_edges"]) == 101
    assert d["y_edges"][0] == y[ok].min() and d["y_edges"][-1] == y[ok].max()
    assert r["range"] == {"x": [x[ok].min(), x[ok].max()], "y": [y[ok].min(), y[ok].max()]}
    ref, _, _ = np.histogram2d(y[ok], x[ok], bins=[np.array(d["y_edges"]), np.array(d["x_edges"])])
    assert np.abs(counts - ref).sum() <= 4  # numpy puts a value on an inner edge by its own rounding
    # exactly 200 000 pairs are still points
    full = scatter_chart(y[:MAX_POINTS], y[:MAX_POINTS], with_spearman=False)
    assert full["mode"] == "points" and len(full["points"]["x"]) == MAX_POINTS
    # log axes: the cells are equal in log10
    logs = scatter_chart(x, y, log_x=True, log_y=True, with_spearman=False)["density"]
    steps = np.diff(np.log10(logs["x_edges"]))
    assert np.allclose(steps, steps[0], rtol=1e-9) and np.array(logs["counts"]).sum() == used
    json.dumps(r, allow_nan=False)


def test_cells_of_the_density() -> None:
    x = np.array([0.0, 0.0, 9.999, 10.0, 5.0, 5.0, 2.5])
    y = np.array([0.0, 4.0, 3.999, 4.0, 2.0, 1.999, 4.0])
    d = density(pairs(x, y), cells=2)
    assert d == {"x_edges": [0.0, 5.0, 10.0], "y_edges": [0.0, 2.0, 4.0], "counts": [[1, 1], [2, 3]], "max": 3}
    # one value on an axis: a cell of its own
    one = density(pairs(np.full(4, 7.0), np.arange(4.0)), cells=4)
    assert one["x_edges"] == [6.5, 6.75, 7.0, 7.25, 7.5] and np.array(one["counts"]).sum() == 4
    assert np.array(one["counts"])[:, 2].tolist() == [1, 1, 1, 1]
    g = grid_counts([0.1, 0.9, 1.0, -5.0, 7.0], [0.1, 0.9, 1.0, 0.5, 0.5], 0.0, 1.0, 0.0, 1.0, 2, 2)
    assert g.tolist() == [[1, 0], [1, 3]] and g.dtype == np.int64  # what lies outside goes to the nearest cell
    with pytest.raises(StatsError):
        density(pairs([], []))


# --- the rows inside a frame, a lasso (Т8.3-3, the kernel) -------------------------------------------------


POLYGONS = {
    "triangle": [(0.2, 0.1), (0.9, 0.3), (0.4, 0.95)],
    "concave": [(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.5, 0.35), (0.1, 0.9)],
    "bow": [(0.1, 0.1), (0.9, 0.9), (0.9, 0.1), (0.1, 0.9)],  # crosses itself
    "star": [(0.5, 0.05), (0.78, 0.95), (0.05, 0.4), (0.95, 0.4), (0.22, 0.95)],  # the middle is wound twice
}


@pytest.mark.parametrize("name", list(POLYGONS))
def test_t83_3_rows_inside_a_lasso_equal_a_direct_count(name: str) -> None:
    """Т8.3-3 (kernel): the rows inside the polygon are exactly those a direct count by the even-odd rule finds."""
    poly = POLYGONS[name]
    rng = np.random.default_rng(833)
    x, y = rng.random(4000), rng.random(4000)
    x[::97], y[::89] = NAN, NAN
    p = pairs(x, y)
    got = rows_in_polygon(p, poly)
    want = [i for i in range(4000) if math.isfinite(x[i]) and math.isfinite(y[i]) and inside_exact(x[i], y[i], poly)]
    assert got.tolist() == want and got.dtype == np.int64 and 100 < len(want) < 3900
    # the order of the vertices and the vertex the lasso starts from do not matter
    assert rows_in_polygon(p, poly[::-1]).tolist() == want
    assert rows_in_polygon(p, poly[2:] + poly[:2]).tolist() == want
    if name == "star":
        assert not inside_exact(0.5, 0.5, poly) and inside_exact(0.5, 0.2, poly)  # even-odd: the middle is outside
    # log axes: the same lasso drawn on the logarithms
    lx, ly = 10.0**x, 10.0 ** (3.0 * y)
    logs = rows_in_polygon(pairs(lx, ly, log_x=True, log_y=True), [(10.0**a, 10.0 ** (3.0 * b)) for a, b in poly])
    u, v = np.log10(lx), np.log10(ly) / 3.0
    near = [i for i in set(logs.tolist()) ^ set(want) if abs(u[i] - x[i]) < 1e-12 and abs(v[i] - y[i]) < 1e-12]
    assert len(near) <= 2  # only points the rounding of the logarithm moved across a side


def test_points_on_the_border_and_frames() -> None:
    square = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]
    xs = np.array([0.0, 1.0, 2.0, 1.0, 1.0, 0.0, 2.0, 3.0, 1.0])
    ys = np.array([1.0, 0.0, 1.0, 2.0, 1.0, 0.0, 2.0, 1.0, NAN])
    p = pairs(xs, ys)
    # the left and the lower sides belong to the polygon, the right and the upper do not: two lassos side by
    # side never take a point twice
    assert rows_in_polygon(p, square).tolist() == [0, 1, 4, 5]
    right = [(2.0, 0.0), (4.0, 0.0), (4.0, 2.0), (2.0, 2.0)]
    assert rows_in_polygon(p, right).tolist() == [2, 7]
    # a frame takes its borders, the corners in any order; a missing value is never inside
    assert rows_in_rect(p, 0.0, 2.0, 0.0, 2.0).tolist() == [0, 1, 2, 3, 4, 5, 6]
    assert rows_in_rect(p, 2.0, 0.5, 1.5, 0.5).tolist() == [2, 4]
    assert rows_in_rect(p, 5.0, 6.0, 0.0, 1.0).tolist() == []
    # log axes: the frame in data units; a border ≤ 0 lies below every point
    pl = pairs(np.array([0.5, 1.0, 10.0, 100.0, -1.0]), np.array([1.0, 2.0, 3.0, 4.0, 5.0]), log_x=True)
    assert rows_in_rect(pl, 1.0, 10.0, 0.0, 10.0).tolist() == [1, 2]
    assert rows_in_rect(pl, -5.0, 1.0, 0.0, 10.0).tolist() == [0, 1]
    for bad in ([(0, 0), (1, 1)], [(0, 0), (1, NAN), (1, 0)], "lasso", [[0, 0, 0], [1, 1, 1], [2, 2, 2]], None):
        refused("bad_param", "polygon", lambda bad=bad: rows_in_polygon(p, bad))
    refused("bad_param", "polygon", lambda: rows_in_polygon(pl, [(0.0, 1.0), (5.0, 1.0), (5.0, 3.0)]))
    refused("bad_param", "polygon", lambda: rows_in_polygon(p, np.zeros((scatter.MAX_VERTICES + 1, 2))))
    refused("bad_param", "rect", lambda: rows_in_rect(p, NAN, 1.0, 0.0, 1.0))
    assert rows_in_polygon(pairs([], []), square).tolist() == []


def test_a_lasso_of_many_vertices_by_bands(monkeypatch: pytest.MonkeyPatch) -> None:
    """13.36: each pair is tested against the sides of its band of Y only — the rows are those of a direct count
    and of one band (every side); vertices on the borders of the bands and level sides change nothing; at most
    `MAX_VERTICES` (1000) vertices, the 1001st — 422 `bad_param` {param: polygon} with a hint."""
    rng = np.random.default_rng(1336)
    n = scatter.MAX_VERTICES
    angle = np.sort(rng.random(n)) * 2 * math.pi
    radius = 0.2 + 0.25 * rng.random(n)
    star = [(0.5 + r * math.cos(a), 0.5 + r * math.sin(a)) for a, r in zip(angle, radius, strict=True)]
    stairs = [(0.0, 0.0)] + [(0.1 * (k // 2 + k % 2), 0.1 * (k // 2)) for k in range(1, 20)] + [(0.0, 1.0)]
    x, y = rng.random(3000), rng.random(3000)
    x[:200], y[:200] = np.round(x[:200], 1), np.round(y[:200], 1)  # on the corners of the stairs
    p = pairs(x, y)
    for poly in (star, stairs):
        want = [i for i in range(3000) if inside_exact(x[i], y[i], poly)]
        assert 50 < len(want) < 2950
        for bands in (1, 10, 256):  # 10: the borders of the bands on the corners of the stairs
            monkeypatch.setattr(scatter, "BANDS", bands)
            assert rows_in_polygon(p, poly).tolist() == want, bands
    with pytest.raises(StatsError) as e:
        rows_in_polygon(p, star + [(0.5, 0.5)])
    assert e.value.code == "bad_param" and e.value.param == "polygon" and "1000" in (e.value.hint or "")


def test_tick_reports_and_cancels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scatter, "_BLOCK", 1024)
    monkeypatch.setattr(scatter, "_LASSO_BLOCK", 1024)
    x, y, w = cloud(6000)
    for limit in (MAX_POINTS, 1000):
        seen: list[float] = []
        scatter_chart(x, y, w, max_points=limit, tick=seen.append)
        assert seen == sorted(seen) and seen[-1] == 1.0 and len(seen) > 6

    class Stop(Exception):
        pass

    def stop(f: float) -> None:
        raise Stop

    with pytest.raises(Stop):
        scatter_chart(x, y, w, tick=stop)
    for threads in (1, 3):
        with pytest.raises(Stop):
            rows_in_polygon(pairs(x, y), POLYGONS["star"], tick=stop, threads=threads)
    p = pairs(x, y)
    lasso = [(0.5, 0.2), (3.0, 0.5), (2.0, 4.0), (0.3, 2.0)]
    assert rows_in_polygon(p, lasso, threads=1).tolist() == rows_in_polygon(p, lasso, threads=4).tolist()
    assert 100 < len(rows_in_polygon(p, lasso)) < 5900


def test_warm_compiles() -> None:
    scatter.warm()
