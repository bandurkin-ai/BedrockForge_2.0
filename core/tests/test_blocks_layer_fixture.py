"""The answers of the core that the layer of blocks (`ui/src/viz/blocks/*`, подшаг 17.30; ADR 0023 §11, decisions
16–19; docs/progress/e17-api.md §12) is read against: BFA1 `blocks` written from the real kernels (`lod.view`,
`shell_merge.scale_class` …) in the form of the contract, with the float64 faces and the rays the viewer must
answer — computed here by a plain loop over the faces, with no part of the viewer in it.

`tests/data/blocks/layer_fixture.json` is read by vitest (`ui/test/blocks-layer.test.ts`). Until the routes of
17.27 exist the encoding below is the one the route will do (`origin` — the rounded centre of the local extent,
`c`, `h` float32 from it); 17.27/17.31 re-check the layer against the real answer of the route.
Regenerate: `BF_REGEN_BLOCKS_LAYER=1 uv run pytest -q tests/test_blocks_layer_fixture.py`.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.blocks.lod import View, view
from bf_core.blocks.schema import GridV2
from bf_core.blocks.shell_merge import NO_CLASS, break_bins, break_class, scale_bins, scale_class
from bf_core.viz import binary

DATA = Path(__file__).parent / "data" / "blocks" / "layer_fixture.json"
REGEN = os.environ.get("BF_REGEN_BLOCKS_LAYER") == "1"
CORNER = [512120.4, 7100010.6, 255.2]  # UTM-sized: float32 of these loses centimetres


def grid_of(counts: Any, size: Any, div: Any, rotation: float, corner: Any = CORNER) -> GridV2:
    return GridV2.of({"corner": list(corner), "counts": list(counts), "size": list(size), "div": list(div),
                      "rotation": rotation, "order": "F"})  # fmt: skip


def split_model(seed: int, grid: GridV2) -> tuple[Any, Any, Any]:
    """Parents of random depth: whole, split into sub-blocks of 1…div along random axes, or missing."""
    rng = np.random.default_rng(seed)
    div = grid.sub.div
    items: list[tuple[int, tuple[int, ...], tuple[int, ...]]] = []

    def cut(p: int, s: tuple[int, ...], w: tuple[int, ...], depth: int) -> None:
        axes = [a for a in range(3) if w[a] > 1]
        if not axes or depth == 0 or rng.random() < 0.3:
            items.append((p, s, w))
            return
        a = int(rng.choice(axes))
        half = w[a] // 2
        w1 = tuple(half if i == a else w[i] for i in range(3))
        s2 = tuple(s[i] + half if i == a else s[i] for i in range(3))
        cut(p, s, w1, depth - 1)
        cut(p, s2, w1, depth - 1)

    for p in range(grid.counts[0] * grid.counts[1] * grid.counts[2]):
        kind = rng.random()
        if kind < 0.12:
            continue
        if kind < 0.6:
            items.append((p, (0, 0, 0), tuple(div)))
        else:
            cut(p, (0, 0, 0), tuple(div), 3)
    items.sort(key=lambda t: (t[0], t[1][2], t[1][1], t[1][0]))
    return (np.array([t[0] for t in items], dtype=np.uint64), np.array([t[1] for t in items], dtype=np.uint8),
            np.array([t[2] for t in items], dtype=np.uint8))  # fmt: skip


def local_extent(f: Any) -> tuple[np.ndarray, np.ndarray]:
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    for a in range(3):
        sel = (f.a >> 1) == a
        if not sel.any():
            continue
        u, v = (a + 1) % 3, (a + 2) % 3
        for ax, size in ((a, np.zeros(int(sel.sum()))), (u, f.h[sel, 0]), (v, f.h[sel, 1])):
            lo[ax] = min(lo[ax], float((f.c[sel, ax] - size).min()))
            hi[ax] = max(hi[ax], float((f.c[sel, ax] + size).max()))
    return lo, hi


def corners(grid: GridV2, f: Any) -> list[list[list[float]]]:
    """The four corners of every face in the project axes (float64)."""
    out = []
    for i in range(len(f)):
        a = int(f.a[i]) >> 1
        u, v = (a + 1) % 3, (a + 2) % 3
        q = []
        for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            x = f.c[i].copy()
            x[u] += su * f.h[i, 0]
            x[v] += sv * f.h[i, 1]
            q.append(x)
        out.append(grid.to_project(np.array(q)).tolist())
    return out


def hit_of(f: Any, o: np.ndarray, d: np.ndarray) -> dict[str, Any] | None:
    """The nearest face on the ray (local, float64) by a plain loop; None — a miss."""
    best: tuple[float, int] | None = None
    for i in range(len(f)):
        a = int(f.a[i]) >> 1
        u, v = (a + 1) % 3, (a + 2) % 3
        if abs(d[a]) < 1e-12:
            continue
        t = (f.c[i, a] - o[a]) / d[a]
        if t <= 0 or (best is not None and t >= best[0]):
            continue
        pu, pv = o[u] + t * d[u], o[v] + t * d[v]
        if abs(pu - f.c[i, u]) <= f.h[i, 0] and abs(pv - f.c[i, v]) <= f.h[i, 1]:
            best = (t, i)
    if best is None:
        return None
    t, i = best
    return {"face": i, "t": t, "point": (o + t * d).tolist()}


def rays_of(grid: GridV2, f: Any, seed: int, n: int = 8) -> list[dict[str, Any]]:
    """Rays from outside to points inside the extent (and one that misses), local and project, float64."""
    rng = np.random.default_rng(seed)
    lo, hi = local_extent(f)
    mid, span = (lo + hi) / 2, hi - lo
    out = []
    for j in range(n):
        target = lo + rng.random(3) * span
        dirn = rng.normal(size=3)
        dirn /= np.linalg.norm(dirn)
        o = target - dirn * float(np.linalg.norm(span)) * 1.5
        out.append(ray_entry(grid, f, o, dirn, f"in{j}"))
    far = mid + np.array([span[0] * 3, span[1] * 3, 0.0])
    out.append(ray_entry(grid, f, far, np.array([0.0, 0.0, 1.0]), "miss"))
    return out


def ray_entry(grid: GridV2, f: Any, o: np.ndarray, d: np.ndarray, name: str) -> dict[str, Any]:
    s, c = grid.sin_cos()
    op = grid.to_project(o.reshape(1, 3))[0]
    dp = np.array([c * d[0] - s * d[1], s * d[0] + c * d[1], d[2]])
    return {"name": name, "local": {"o": o.tolist(), "d": d.tolist()}, "project": {"o": op.tolist(), "d": dp.tolist()},
            "hit": hit_of(f, o, d)}  # fmt: skip


def encode(
    grid: GridV2,
    v: View,
    *,
    mode: str,
    rows: int,
    passed: int,
    field: str | None,
    colour: dict[str, Any],
    classes: int,
    bins: Any,
    labels: Any,
) -> tuple[bytes, np.ndarray]:
    f = v.faces
    lo, hi = local_extent(f)
    origin = np.round((lo + hi) / 2)
    header = {"kind": "blocks", "format": 1, "model": 12, "version": 3, "mode": mode, "corner": list(grid.corner),
              "rotation": grid.rotation, "origin": origin.tolist(), "faces": len(f), "faces_raw": v.faces_raw,
              "simplified": v.simplified, "lod": v.lod, "rows": rows, "passed": passed, "empty": rows - passed,
              "field": field, "colour": colour, "classes": classes, "bins": bins, "labels": labels}  # fmt: skip
    arrays = [("c", f.c - origin, "<f4"), ("h", f.h, "<f4"), ("a", f.a, "<u1"), ("k", f.k, "<u2")]
    return binary.encode(header, arrays), origin


def case(name: str, grid: GridV2, v: View, seed: int, **meta: Any) -> dict[str, Any]:
    raw, origin = encode(grid, v, **meta)
    f = v.faces
    return {
        "name": name,
        "bfa1": base64.b64encode(raw).decode("ascii"),
        "grid": {"corner": list(grid.corner), "rotation": grid.rotation, "counts": list(grid.counts)},
        "origin": origin.tolist(),
        "faces": {"c": f.c.tolist(), "h": f.h.tolist(), "a": f.a.tolist(), "k": f.k.tolist()},
        "corners_project": corners(grid, f),
        "rays": rays_of(grid, f, seed),
        "faces_raw": v.faces_raw, "simplified": v.simplified, "lod": v.lod,
    }  # fmt: skip


def build() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []

    # 1. turned model with sub-blocks, a grade scale (log, 256 buckets), some blocks without a value
    grid = grid_of((6, 5, 4), (10.0, 10.0, 5.0), (2, 2, 2), 30.0)
    p, s, w = split_model(5, grid)
    rng = np.random.default_rng(7)
    values = np.exp(rng.normal(0.0, 1.2, len(p)))
    values[rng.random(len(p)) < 0.08] = np.nan
    passed = rng.random(len(p)) < 0.9
    k = scale_class(values, 0.1, 10.0, log=True)
    v = view(grid, p, s, w, passed, k)
    cases.append(case("scale_turned", grid, v, 1, mode="shell", rows=len(p), passed=int(passed.sum()), field="AU",
                      colour={"kind": "scale", "from": 0.1, "to": 10.0, "log": True}, classes=256,
                      bins=scale_bins(0.1, 10.0, log=True), labels=None))  # fmt: skip

    # 2. classes by breaks, no turn
    grid = grid_of((5, 5, 3), (20.0, 20.0, 10.0), (1, 1, 1), 0.0)
    n = grid.counts[0] * grid.counts[1] * grid.counts[2]
    p = np.arange(n, dtype=np.uint64)
    values = np.random.default_rng(3).uniform(0, 4, n)
    breaks = [0.5, 1.0, 2.0]
    v = view(grid, p, None, None, np.ones(n, dtype=bool), break_class(values, breaks))
    cases.append(case("classes_plain", grid, v, 2, mode="shell", rows=n, passed=n, field="AU",
                      colour={"kind": "classes", "breaks": breaks}, classes=4, bins=break_bins(breaks),
                      labels=None))  # fmt: skip

    # 3. categories, codes beyond 255 (the legend of 256 × H), a code without a label
    grid = grid_of((4, 4, 4), (10.0, 10.0, 10.0), (1, 1, 1), -20.0)
    n = 64
    p = np.arange(n, dtype=np.uint64)
    codes = (np.arange(n) * 7 % 5).astype(np.uint16)
    codes[3] = 300
    codes[9] = NO_CLASS
    v = view(grid, p, None, None, np.ones(n, dtype=bool), codes)
    cases.append(case("categories", grid, v, 3, mode="shell", rows=n, passed=n, field="ROCK",
                      colour={"kind": "categories", "hidden": []}, classes=301, bins=None,
                      labels=["руда", "порода", "окисленная", "дайка", "вода"]))  # fmt: skip

    # 4. LOD: a cube of 16³ over the limit, simplified
    grid = grid_of((16, 16, 16), (5.0, 5.0, 5.0), (1, 1, 1), 10.0)
    n = 16**3
    p = np.arange(n, dtype=np.uint64)
    q = np.arange(n)
    klass = ((q % 16 + q // 16 % 16 + q // 256) // 6 % 4).astype(np.uint16)  # P = i + 16 (j + 16 k)
    v = view(grid, p, None, None, np.ones(n, dtype=bool), klass, limit=300, kind="mode")
    assert v.simplified and v.lod > 0
    cases.append(case("lod", grid, v, 4, mode="shell", rows=n, passed=n, field="ROCK",
                      colour={"kind": "categories", "hidden": []}, classes=4, bins=None,
                      labels=["a", "b", "c", "d"]))  # fmt: skip

    # 5. all blocks (no hiding), no colouring
    grid = grid_of((3, 3, 3), (10.0, 10.0, 10.0), (1, 1, 1), 0.0)
    p = np.array([q for q in range(27) if q != 5], dtype=np.uint64)
    v = view(grid, p, None, None, np.ones(len(p), dtype=bool), None, shell=False)
    cases.append(case("all_blocks", grid, v, 5, mode="all", rows=len(p), passed=len(p), field=None,
                      colour={"kind": "none"}, classes=0, bins=None, labels=None))  # fmt: skip

    # 6. Т8.7-9: 100³ of one class — six rectangles
    grid = grid_of((100, 100, 100), (1.0, 1.0, 1.0), (1, 1, 1), 0.0)
    n = 100**3
    v = view(grid, np.arange(n, dtype=np.uint64), None, None, np.ones(n, dtype=bool), np.zeros(n, np.uint16))
    assert len(v.faces) == 6 and v.faces_raw == 60_000
    cases.append(case("cube_100", grid, v, 6, mode="shell", rows=n, passed=n, field=None, colour={"kind": "none"},
                      classes=0, bins=None, labels=None))  # fmt: skip
    return {"note": "BF_REGEN_BLOCKS_LAYER=1 uv run pytest -q tests/test_blocks_layer_fixture.py", "cases": cases}


def test_fixture_matches_the_kernels() -> None:
    fresh = build()
    for c in fresh["cases"]:
        assert c["faces"]["a"], c["name"]
        head, arrays = binary.decode(base64.b64decode(c["bfa1"]))
        assert head["kind"] == "blocks" and head["faces"] == len(c["faces"]["a"])
        assert np.array_equal(arrays["a"], np.array(c["faces"]["a"], dtype=np.uint8))
        got = arrays["c"].reshape(-1, 3).astype(np.float64) + np.array(c["origin"])
        assert np.abs(got - np.array(c["faces"]["c"])).max() < 1e-3  # float32 from the integer origin
    if REGEN:
        DATA.parent.mkdir(parents=True, exist_ok=True)
        DATA.write_text(json.dumps(fresh, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        return
    if not DATA.exists():
        pytest.fail("нет tests/data/blocks/layer_fixture.json: BF_REGEN_BLOCKS_LAYER=1, см. модуль")
    kept = json.loads(DATA.read_text(encoding="utf-8"))
    assert [c["name"] for c in kept["cases"]] == [c["name"] for c in fresh["cases"]]
    for a, b in zip(kept["cases"], fresh["cases"], strict=True):
        assert a["bfa1"] == b["bfa1"], a["name"]  # the kernels give the bytes the viewer was read against
        assert a["faces"] == b["faces"] and a["rays"] == b["rays"], a["name"]
