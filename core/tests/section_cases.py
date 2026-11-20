"""Inputs of the shared answers `tests/data/sections/series_cases.json` (ADR 0016 §2) and their computation.

The file is read by pytest (`test_section_geometry.py`, `test_section_polyline.py`, `test_section_names.py`) and
by vitest (`ui/test/viz-section-plane.test.ts`). The answers are computed by the core; the tests check them
against hand-made expectations and the file against the core. Rewrite the file after a deliberate change:
`BF_REGEN_SECTION_CASES=1 uv run pytest -q tests/test_section_geometry.py tests/test_section_polyline.py`.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from bf_core.sections.geometry import SeriesGeometry, series_geometry
from bf_core.sections.names import section_names
from bf_core.sections.polyline import PolylineSection

DATA = Path(__file__).parent / "data" / "sections" / "series_cases.json"
REGEN = os.environ.get("BF_REGEN_SECTION_CASES") == "1"
TOL = {"rel": 1e-12, "abs": 1e-12}

RULES = [
    "Axes: s = (sin A, cos A, 0); v = (-cos A*sin phi, sin A*sin phi, -cos phi); w = (-cos A*cos phi, sin A*cos phi,"
    " sin phi); sin/cos of degrees exact at multiples of 90 (sinCosDeg), otherwise of d*(PI/180); -0 written 0.",
    "A from P1->P2: exact 0/90/180/270 when dx or dy is 0, otherwise atan2(dx, dy)*(180/PI), +360 below zero.",
    "vertical: phi = 90 and P1.z taken as 0 (w = Z); plan: phi = 0, A = 90 unless given; P1.z = top level.",
    "K = floor((s1 - s0)/step + 1e-9) + 1; s_k = s0 + k*step; O_k = P1 + s_k*v per component.",
    "d = p - P1 first; dot = 0.0 + terms x, y, z in order, a term with a zero axis component skipped;"
    " u = d.s, w = d.w, tau = d.v, t = tau - s_k.",
    "Window k: lo_k <= tau < hi_k, lo_k = s_k - towards_k, hi_k = s_k + away_k; neighbours abut when"
    " |away_k + towards_(k+1) - step| <= 1e-9*step, then hi_k = lo_(k+1) = min(s_k + away_k, s_(k+1) - towards_(k+1)).",
    "Section of a point: the window holding tau; several (gaps/overlaps) - the nearest plane |t|, smaller k on a"
    " tie; none or NaN - -1. A line: the window holding all its vertices [tau_min, tau_max]; several - the plane"
    " nearest to (tau_min + tau_max)/2.",
    "Polyline: L_j = sqrt(dx*dx + dy*dy), s_j = (dx/L, dy/L, 0), v_j = (-s_y, s_x, 0), w = (0, 0, 1), start U_j ="
    " L_0 + ... + L_(j-1) in order; segment of a point - the nearest in plan (squared distance: the smaller of the ends"
    " and, for 0 < d.s_j < L_j, (d.v_j)^2), smaller j on a tie, -1 beyond the corridor (closed); u = U_j + d.s_j,"
    " w = Z, t = d.v_j with d = p - P_j; half-spaces n.p + c >= 0: faces +-v_j at the corridor, the start (s_0 at P_0,"
    " else the bisector (s_(j-1) + s_j)/sqrt(nx*nx + ny*ny) at P_j) and the end (-s at P_m, else minus the next"
    " bisector at P_(j+1)), c = -n.P.",
    "Names (core only): {k} from 1, {s}, {X}, {Y}, {Z} of O_k with a number format; the default of 8.1.2 with the"
    " precision grown until the names differ.",
    "null in a coordinate is an empty value (NaN); null in an answer - NaN.",
    "Compare numbers: |a - b| <= abs + rel*|b|; cases with exact = true - bit for bit.",
]


def _n(x: float) -> float | None:
    return None if not math.isfinite(x) else float(x)


def _vec(a: Any) -> list[float | None]:
    return [_n(float(x)) for x in np.asarray(a, dtype=np.float64).reshape(-1)]


def _arr(points: list[list[float | None]]) -> np.ndarray:
    return np.array([[math.nan if c is None else c for c in p] for p in points], dtype=np.float64).reshape(-1, 3)


def at(g: SeriesGeometry, k: int, u: float, w: float, t: float) -> list[float]:
    """The point with (u, w) on section k at the distance t from its plane."""
    return [float(x) for x in g.p1 + u * g.s + w * g.w + (float(g.offs[k]) + t) * g.v]


def _edges(g: SeriesGeometry) -> list[list[float]]:
    """Depths on the faces of the windows of a series with P1 = 0 and v = (1, 0, 0): x = tau exactly."""
    out = []
    for k in (0, 1, 7, g.count - 2, g.count - 1):
        lo, hi, s = float(g.lo[k]), float(g.hi[k]), float(g.offs[k])
        for tau in (lo, math.nextafter(lo, -math.inf), s + float(g.away[k]), math.nextafter(hi, -math.inf), hi):
            out.append([tau, 3.0, -2.0])
    return out


VERTICAL_AZ0 = {"type": "vertical", "p1": [0.0, 0.0, 0.0], "az": 0.0, "s0": 0.0, "s1": 200.0, "step": 50.0,
                "towards": 25.0, "away": 25.0}  # fmt: skip

SERIES: list[dict[str, Any]] = [
    {
        "id": "t8_1_1",
        "title": "Т8.1-1: A = 90°, P1 = (1000; 5000; 0), s0 = 0, s1 = 100, Δ = 25: K = 5, O_k.y = 5000 + 25k",
        "exact": True,
        "params": {
            "type": "vertical",
            "p1": [1000.0, 5000.0, 0.0],
            "az": 90.0,
            "s0": 0.0,
            "s1": 100.0,
            "step": 25.0,
            "towards": 12.5,
            "away": 12.5,
        },
        "points": [
            [1000.0, 5012.5, 7.0],
            [1000.0, 4987.5, 0.0],
            [1000.0, 4987.499, 0.0],
            [1003.0, 5050.0, None],
            [1000.0, 5112.5, 0.0],
            [1000.0, 5112.499, 3.0],
        ],
        "lines": [
            [[1000.0, 5040.0, 0.0], [1010.0, 5055.0, 5.0], [1020.0, 5062.4, -3.0]],
            [[1000.0, 5040.0, 0.0], [1000.0, 5062.5, 0.0]],
            [[1003.0, 5050.0, None], [1004.0, 5051.0, 2.0]],
            [],
        ],
    },
    {
        "id": "t8_1_5",
        "title": "Т8.1-5: наклонная φ = 60°, A = 30°, s0 = 0: P1 + 3ŝ + 2ŵ → (u; w) = (3; 2), t = 0",
        "exact": False,
        "params": {
            "type": "inclined",
            "p1": [1000.0, 2000.0, 300.0],
            "az": 30.0,
            "phi": 60.0,
            "s0": 0.0,
            "s1": 100.0,
            "step": 20.0,
            "towards": 10.0,
            "away": 10.0,
        },
        "points": lambda g: [
            at(g, 0, 3, 2, 0),
            at(g, 3, -7.5, 11, 4),
            at(g, 5, 0, 0, 9.99),
            at(g, 5, 0, 0, 10.5),
            [1000.0, 2000.0, None],
        ],
        "lines": lambda g: [
            [at(g, 2, -5, -5, 0), at(g, 2, 5, -5, 0), at(g, 2, 5, 5, 0), at(g, 2, -5, 5, 0)],
            [at(g, 2, 0, 0, -9), at(g, 2, 1, 1, 9)],
            [at(g, 2, 0, 0, -9), at(g, 3, 0, 0, 0)],
        ],
    },
    {
        "id": "plan",
        "title": "План: верх 400, низ 300, Δ = 10 — z_k = 400 − 10k, ŵ — север",
        "exact": True,
        "params": {
            "type": "plan",
            "p1": [500.0, 600.0, 400.0],
            "s0": 0.0,
            "s1": 100.0,
            "step": 10.0,
            "towards": 5.0,
            "away": 5.0,
        },
        "points": [
            [510.0, 620.0, 395.0],
            [500.0, 600.0, 400.0],
            [500.0, 600.0, 305.0],
            [500.0, 600.0, 295.0],
            [500.0, 600.0, 405.0],
            [501.0, 602.0, 405.001],
        ],
        "lines": [[[400.0, 500.0, 350.0], [450.0, 520.0, 350.0], [430.0, 560.0, 350.0]]],
    },
    {
        "id": "inclined_flat",
        "title": "Наклонная φ = 0° (горизонтальная), A = 45°",
        "exact": False,
        "params": {
            "type": "inclined",
            "p1": [100.0, 200.0, 50.0],
            "az": 45.0,
            "phi": 0.0,
            "s0": -20.0,
            "s1": 20.0,
            "step": 10.0,
            "towards": 5.0,
            "away": 5.0,
        },
        "points": lambda g: [at(g, 0, 1, 2, 0), at(g, 4, -3, 4, -4.5), [110.0, 210.0, 50.0]],
        "lines": [],
    },
    {
        "id": "vertical_az270",
        "title": "Вертикальная A = 270°: взгляд на юг",
        "exact": True,
        "params": {
            "type": "vertical",
            "p1": [0.0, 0.0, 15.0],
            "az": 270.0,
            "s0": 0.0,
            "s1": 40.0,
            "step": 20.0,
            "towards": 10.0,
            "away": 10.0,
        },
        "points": [[5.0, -10.0, 3.0], [-7.0, -29.0, -1.0], [0.0, 10.0, 0.0], [0.0, 10.5, 0.0]],
        "lines": [],
    },
    {
        "id": "abut_hard",
        "title": "Т8.1-2 на гранях: Δ = 0,3 = 0,1 + 0,2 (в float64 не равно), s0 = −1234,5; x = τ точно",
        "exact": True,
        "params": {
            "type": "vertical",
            "p1": [0.0, 0.0, 0.0],
            "az": 180.0,
            "s0": -1234.5,
            "s1": -1228.5,
            "step": 0.3,
            "towards": 0.1,
            "away": 0.2,
        },
        "points": _edges,
        "lines": [],
    },
    {
        "id": "utm_p2",
        "title": "UTM, P2 вместо азимута, Δ = 0,3, K = 4001",
        "exact": False,
        "params": {
            "type": "vertical",
            "p1": [512345.678, 7012345.123, 0.0],
            "p2": [512845.1, 7012045.9],
            "s0": -1234.5,
            "s1": -34.5,
            "step": 0.3,
            "towards": 0.1,
            "away": 0.2,
        },
        "sections": [0, 1, 2, 2000, 3999, 4000],
        "points": lambda g: (
            [at(g, k, 25.5, 310.25, t) for k in (0, 2000, 4000) for t in (-0.05, 0.0, 0.15)] + [at(g, 4000, 0, 0, 0.25)]
        ),
        "lines": lambda g: [[at(g, 2000, 0, 0, -0.05), at(g, 2000, 40, -10, 0.1)]],
    },
    {
        "id": "overrides_abut",
        "title": "Свои окна, соседи смыкаются: w_от(1) = 40, w_к(2) = 10",
        "exact": True,
        "params": {**VERTICAL_AZ0, "overrides": {"1": {"away": 40.0}, "2": {"towards": 10.0}}},
        "points": [[-89.0, 1.0, 0.0], [-90.0, 1.0, 0.0], [-60.0, 1.0, 0.0], [-225.0, 0.0, 0.0]],
        "lines": [],
    },
    {
        "id": "overrides_gap_overlap",
        "title": "Свои окна: перекрытие 0 и 1 (ближайшая плоскость, при равенстве — меньший k), зазор 3–4",
        "exact": True,
        "params": {**VERTICAL_AZ0, "overrides": {"0": {"away": 60.0}, "3": {"away": 10.0}}},
        "points": [[-30.0, 0.0, 0.0], [-25.0, 0.0, 0.0], [-55.0, 0.0, 0.0], [-165.0, 0.0, 0.0], [-59.999, 0.0, 0.0]],
        "lines": [[[-20.0, 0.0, 0.0], [-30.0, 5.0, 0.0]], [[-26.0, 0.0, 0.0], [-30.0, 5.0, 0.0]]],
    },
]


def _resolve(spec: Any, g: SeriesGeometry) -> Any:
    return spec(g) if callable(spec) else spec


def series_answer(case: dict[str, Any]) -> dict[str, Any]:
    g = series_geometry(case["params"])
    assert isinstance(g, SeriesGeometry)
    ks = case.get("sections", range(g.count))
    origins = g.origins()
    names = section_names(case["params"])
    points = _resolve(case["points"], g)
    p = _arr(points)
    local, sec = g.local(p), g.section_of(p)
    pts = []
    for i, q in enumerate(points):
        k = int(sec[i])
        t = float(local[i, 2] - g.offs[k]) if k >= 0 else math.nan
        pts.append({"p": [_n(c) if c is not None else None for c in q], "tau": _n(local[i, 2]),
                    "u": _n(local[i, 0]), "w": _n(local[i, 1]), "section": k, "t": _n(t)})  # fmt: skip
    lines = _resolve(case["lines"], g)
    flat = [v for line in lines for v in line]
    offsets = np.cumsum([0] + [len(line) for line in lines])
    line_sec = g.line_sections(_arr(flat), offsets) if lines else np.zeros(0, dtype=np.int64)
    return {
        "id": case["id"],
        "title": case["title"],
        "exact": case["exact"],
        "params": case["params"],
        "az": g.az,
        "phi": g.phi,
        "axes": {"s": _vec(g.s), "v": _vec(g.v), "w": _vec(g.w)},
        "count": g.count,
        "partition": g.partition,
        "sections": [
            {
                "k": int(k),
                "name": names[k],
                "s": float(g.offs[k]),
                "origin": _vec(origins[k]),
                "towards": float(g.towards[k]),
                "away": float(g.away[k]),
                "lo": float(g.lo[k]),
                "hi": float(g.hi[k]),
            }
            for k in ks
        ],
        "points": pts,
        "lines": [{"xyz": line, "section": int(line_sec[i])} for i, line in enumerate(lines)],
    }


def on(pl: PolylineSection, j: int, a: float, t: float, z: float | None = 0.0) -> list[float | None]:
    """The point at `a` along segment j of a polyline section, `t` across it, at the level z."""
    q = pl.vertex(j) + a * pl.s_axes[j] + t * pl.v_axes[j]
    return [float(q[0]), float(q[1]), z]


def wedge(pl: PolylineSection, j: int, r: float) -> list[float | None]:
    """The point at the distance r from the bend P_j in its outer wedge (of a bend to the left), Z empty."""
    n = pl.v_axes[j - 1] + pl.v_axes[j]
    q = pl.vertex(j) - r * n / math.sqrt(n[0] * n[0] + n[1] * n[1])
    return [float(q[0]), float(q[1]), None]


POLYLINES: list[dict[str, Any]] = [
    {
        "id": "t8_1_7",
        "title": "Т8.1-7: ломаная из 3 сегментов на UTM, длины 500 + 520 + 340 — развёртка 1360",
        "exact": False,
        "params": {
            "type": "polyline",
            "polyline": [[500000.0, 7000000.0], [500300.0, 7000400.0], [500500.0, 7000880.0], [500660.0, 7000580.0]],
            "corridor": 25.0,
        },
        "points": lambda pl: [
            [500000.0, 7000000.0, 10.0],
            [500300.0, 7000400.0, 11.0],
            [500500.0, 7000880.0, 12.0],
            [500660.0, 7000580.0, 13.0],
            on(pl, 1, 260.0, 10.0, -50.0),
            on(pl, 2, 350.0, 0.0),
            on(pl, 0, 250.0, 30.0),
            wedge(pl, 1, 5.0),
            on(pl, 1, 2.0, 6.0),
        ],
        "lines": lambda pl: [
            [on(pl, 0, 10.0, 1.0), on(pl, 1, 100.0, -2.0), on(pl, 2, 300.0, 24.0)],
            [on(pl, 0, 10.0, 1.0), on(pl, 0, 20.0, 26.0)],
        ],  # fmt: skip
    },
    {
        "id": "straight_axes",
        "title": "Ломаная восток → север: излом, внешний клин, концы коридора",
        "exact": True,
        "params": {"type": "polyline", "polyline": [[0.0, 0.0], [100.0, 0.0], [100.0, 50.0]], "corridor": 10.0},
        "points": [
            [100.0, 0.0, 0.0],
            [105.0, -5.0, 1.0],
            [95.0, 5.0, 2.0],
            [97.0, 8.0, 3.0],
            [50.0, 10.0, 4.0],
            [50.0, 10.001, 5.0],
            [50.0, -10.0, 6.0],
            [-10.0, 0.0, 7.0],
            [-7.1, 7.1, 8.0],
            [100.0, 60.0, 9.0],
            [50.0, 3.0, None],
        ],
        "lines": [[[10.0, 2.0, 0.0], [90.0, -3.0, 5.0], [100.0, 40.0, 1.0]], [[10.0, 2.0, 0.0], [50.0, 20.0, 0.0]], []],
    },
    {
        "id": "zigzag_utm",
        "title": "Зигзаг из 5 сегментов на UTM, коридор 7,5",
        "exact": False,
        "params": {
            "type": "polyline",
            "polyline": [
                [512001.25, 7012003.5],
                [512101.75, 7012043.125],
                [512140.5, 7011980.0],
                [512240.0, 7012001.0],
                [512250.125, 7012101.0],
                [512180.0, 7012180.5],
            ],  # fmt: skip
            "corridor": 7.5,
        },
        "points": lambda pl: (
            [on(pl, j, a * pl.lengths[j], t, 100.0 + j) for j in range(5) for a, t in ((0.4, -7.0), (0.6, 3.0))]
            + [on(pl, 4, 30.0, 7.6)]
        ),
        "lines": [],
    },
]


def _resolve_pl(spec: Any, pl: PolylineSection) -> Any:
    return spec(pl) if callable(spec) else spec


def polyline_answer(case: dict[str, Any]) -> dict[str, Any]:
    pl = series_geometry(case["params"])
    assert isinstance(pl, PolylineSection)
    points = _resolve_pl(case["points"], pl)
    p = _arr(points)
    seg, local = pl.segment_of(p), pl.local(p)
    lines = _resolve_pl(case["lines"], pl)
    flat = [v for line in lines for v in line]
    offsets = np.cumsum([0] + [len(line) for line in lines])
    line_sec = pl.line_sections(_arr(flat), offsets) if lines else np.zeros(0, dtype=np.int64)
    return {
        "id": case["id"],
        "title": case["title"],
        "exact": case["exact"],
        "params": case["params"],
        "count": 1,
        "name": section_names(case["params"])[0],
        "total": pl.total,
        "segments": [
            {
                "j": j,
                "start": float(pl.starts[j]),
                "length": float(pl.lengths[j]),
                "s": _vec(pl.s_axes[j]),
                "v": _vec(pl.v_axes[j]),
                "half_spaces": [_vec(r) for r in pl.half_spaces(j)],
            }
            for j in range(pl.segments)
        ],
        "points": [
            {"p": q, "segment": int(seg[i]), "u": _n(local[i, 0]), "w": _n(local[i, 1]), "t": _n(local[i, 2])}
            for i, q in enumerate(points)
        ],
        "lines": [{"xyz": line, "section": int(line_sec[i])} for i, line in enumerate(lines)],
    }


def load() -> dict[str, Any]:
    return json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {}


def store(key: str, value: Any, extra: Callable[[dict[str, Any]], None] | None = None) -> None:
    """Rewrite one part of the file (the others as they are)."""
    data = load()
    data.update(
        about="Общие эталоны серий разрезов (ADR 0016 §2): ядро — tests/section_cases.py, окно — viz/section/*.ts",
        rules=RULES,
        tolerance=TOL,
    )
    data[key] = value
    if extra is not None:
        extra(data)
    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def close(a: Any, b: Any, exact: bool, path: str = "") -> None:
    """Assert that the JSON values a and b agree: bit for bit when exact, else within TOL."""
    if isinstance(b, dict):
        assert isinstance(a, dict) and set(a) == set(b), path
        for key in b:
            close(a[key], b[key], exact, f"{path}.{key}")
    elif isinstance(b, list):
        assert isinstance(a, list) and len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            close(x, y, exact, f"{path}[{i}]")
    elif isinstance(b, float) and not isinstance(a, bool) and isinstance(a, int | float):
        if exact:
            assert float(a) == b, f"{path}: {a!r} != {b!r}"
        else:
            assert abs(float(a) - b) <= TOL["abs"] + TOL["rel"] * abs(b), f"{path}: {a!r} != {b!r}"
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"
