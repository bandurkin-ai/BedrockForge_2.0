"""Synthetic point clouds with the true label of every point (ADR 0014 «Тесты и приёмка», ТЗ 7.35 Д-22).

    plain(seed)     Т-З1: a flat field with «buildings», «trees» and single outliers
    funnel(seed)    Т-З2: a conical pit with a 50° wall and vegetation on 15 % of the wall
    benches(seed)   Т-З3: benches with vertical walls, bushes on the berms and a «building» with facades
    quarry(n, seed) the scene of the benchmark `ground_classify`: all of the above on one site

Everything comes from `np.random.default_rng(seed)`: the same seed gives the same bytes. Coordinates are
float64 project coordinates of UTM size (`ORIGIN`), the points are shuffled (no order to lean on) unless
`order="storage"` asks for the order a stored cloud has (cells of 1 m along a Morton curve).

`Scene.truth` holds ASPRS codes (2 ground, 5 vegetation, 6 building, 7 noise), `Scene.tag` says what the point
is (`T_*`), `Scene.ret` packs the return number (low 4 bits) and the number of returns (high 4 bits) the way
the cloud storage does. `Scene.ground_z` / `Scene.ground_cos` give the true surface and the cosine of its
slope at plan positions (for the deviation of a DTM along the normal).
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ORIGIN = (432_000.0, 6_180_000.0, 250.0)
GROUND, VEGETATION, BUILDING, NOISE = 2, 5, 6, 7
SIGMA = 0.02  # noise of a ground return along the normal, m

# what a point is
T_FLAT, T_SLOPE, T_BERM, T_WALL, T_TOP, T_FLOOR, T_RIM = 0, 1, 2, 3, 4, 5, 6
T_UNDER = 7  # ground under vegetation (thinned)
T_ROOF, T_FACADE, T_CROWN, T_TRUNK, T_BUSH, T_OUTLIER = 10, 11, 12, 13, 14, 15
GROUND_TAGS = (T_FLAT, T_SLOPE, T_BERM, T_WALL, T_TOP, T_FLOOR, T_RIM, T_UNDER)

Field = Callable[[np.ndarray, np.ndarray], np.ndarray]


@dataclass
class Scene:
    xyz: np.ndarray  # (n, 3) float64, project coordinates
    truth: np.ndarray  # (n,) uint8, ASPRS code
    tag: np.ndarray  # (n,) uint8, T_*
    ret: np.ndarray  # (n,) uint8: return number | number of returns << 4
    origin: tuple[float, float, float]
    ground_z: Field  # true ground height at project X, Y (the lower level at a vertical wall)
    ground_cos: Field  # cosine of the slope of the true ground there
    extent: tuple[float, float, float, float]  # x0, y0, x1, y1 of the site, project coordinates

    def __len__(self) -> int:
        return len(self.xyz)


class _Parts:
    """Points of a scene collected piece by piece in local coordinates."""

    def __init__(self) -> None:
        self.xyz: list[np.ndarray] = []
        self.truth: list[np.ndarray] = []
        self.tag: list[np.ndarray] = []
        self.ret: list[np.ndarray] = []

    def add(self, x, y, z, truth: int, tag, ret: int = 0x11) -> None:  # type: ignore[no-untyped-def]
        n = len(x)
        self.xyz.append(np.column_stack([x, y, z]).astype(np.float64))
        self.truth.append(np.full(n, truth, np.uint8))
        self.tag.append(np.broadcast_to(np.asarray(tag, np.uint8), (n,)).copy())
        self.ret.append(np.full(n, ret, np.uint8))

    def scene(self, rng, origin, ground_z: Field, ground_cos: Field, extent, order: str) -> Scene:  # type: ignore[no-untyped-def]
        xyz = np.vstack(self.xyz)
        truth, tag, ret = (np.concatenate(a) for a in (self.truth, self.tag, self.ret))
        if order == "storage":
            perm = _storage_order(xyz)
        else:
            perm = rng.permutation(len(xyz))
        xyz = xyz[perm] + np.asarray(origin)
        ox, oy, oz = origin
        return Scene(
            xyz=np.ascontiguousarray(xyz),
            truth=truth[perm],
            tag=tag[perm],
            ret=ret[perm],
            origin=origin,
            ground_z=lambda x, y: ground_z(np.asarray(x) - ox, np.asarray(y) - oy) + oz,
            ground_cos=lambda x, y: ground_cos(np.asarray(x) - ox, np.asarray(y) - oy),
            extent=(extent[0] + ox, extent[1] + oy, extent[2] + ox, extent[3] + oy),
        )


def _storage_order(xyz: np.ndarray) -> np.ndarray:
    """Cells of 1 m along a Morton curve — the order of a stored cloud (ADR 0014 §1), stable inside a cell."""
    ij = np.floor(xyz[:, :2] - xyz[:, :2].min(axis=0)).astype(np.uint64)
    code = np.zeros(len(xyz), np.uint64)
    for bit in range(16):
        one = np.uint64(1)
        code |= ((ij[:, 0] >> np.uint64(bit)) & one) << np.uint64(2 * bit)
        code |= ((ij[:, 1] >> np.uint64(bit)) & one) << np.uint64(2 * bit + 1)
    return np.argsort(code, kind="stable")


def _uniform(rng, x0: float, y0: float, x1: float, y1: float, density: float):  # type: ignore[no-untyped-def]
    n = int(round(density * (x1 - x0) * (y1 - y0)))
    return x0 + rng.random(n) * (x1 - x0), y0 + rng.random(n) * (y1 - y0)


def _discs(rng, count: int, x0, y0, x1, y1, r0: float, r1: float, taken: list, gap: float):  # type: ignore[no-untyped-def]
    """`count` discs (x, y, r) inside the rectangle that keep `gap` from each other and from `taken`."""
    out: list[tuple[float, float, float]] = []
    tries = 0
    while len(out) < count and tries < 200 * count:
        tries += 1
        r = r0 + rng.random() * (r1 - r0)
        x = x0 + r + rng.random() * (x1 - x0 - 2 * r)
        y = y0 + r + rng.random() * (y1 - y0 - 2 * r)
        if all((x - a) ** 2 + (y - b) ** 2 > (r + c + gap) ** 2 for a, b, c in [*taken, *out]):
            out.append((x, y, r))
    return out


def _in_discs(x, y, discs) -> np.ndarray:  # type: ignore[no-untyped-def]
    inside = np.zeros(len(x), bool)
    for a, b, r in discs:
        inside |= (x - a) ** 2 + (y - b) ** 2 <= r * r
    return inside


def _patches(rng, x, y, wall: np.ndarray, share: float, ring, r0: float, r1: float):  # type: ignore[no-untyped-def]
    """Discs centred on the ring (cx, cy, inner radius, outer radius), added until they cover `share` of the
    points marked by `wall`."""
    cx, cy, inner, outer = ring
    out: list[tuple[float, float, float]] = []
    covered = np.zeros(len(x), bool)
    total = max(int(wall.sum()), 1)
    while (covered & wall).sum() < share * total:
        a, d = rng.random() * 2 * np.pi, inner + rng.random() * (outer - inner)
        disc = (cx + d * np.cos(a), cy + d * np.sin(a), r0 + rng.random() * (r1 - r0))
        out.append(disc)
        covered |= (x - disc[0]) ** 2 + (y - disc[1]) ** 2 <= disc[2] ** 2
    return out


def _thin(rng, keep: np.ndarray, where: np.ndarray, share: float) -> np.ndarray:
    """Leaves `share` of the points marked by `where`."""
    return keep & (~where | (rng.random(len(keep)) < share))


def _trees(rng, parts: _Parts, discs, base: Field, density: float) -> None:  # type: ignore[no-untyped-def]
    """Crowns (ellipsoids 2–3.5 m above the ground and up) and trunks; `discs` are the crowns in plan."""
    for x, y, r in discs:
        low = 2.0 + 1.5 * rng.random()
        high = low + 4.0 + 6.0 * rng.random()
        n = int(round(1.5 * density * np.pi * r * r))
        u = rng.normal(size=(n, 3))
        u *= (rng.random(n) ** (1 / 3) / np.linalg.norm(u, axis=1))[:, None]  # uniform in the unit ball
        cx, cy = x + r * u[:, 0], y + r * u[:, 1]
        parts.add(cx, cy, base(cx, cy) + 0.5 * (low + high) + 0.5 * (high - low) * u[:, 2], VEGETATION, T_CROWN, 0x21)
        k = 15
        tx, ty = x + rng.normal(0, 0.07, k), y + rng.normal(0, 0.07, k)
        parts.add(tx, ty, base(tx, ty) + 0.3 + rng.random(k) * (low - 0.3), VEGETATION, T_TRUNK, 0x11)


def _bushes(rng, parts: _Parts, discs, base: Field, density: float, low=0.3, high=1.8) -> None:  # type: ignore[no-untyped-def]
    for x, y, r in discs:
        n = int(round(2.5 * density * np.pi * r * r))
        a, d = rng.random(n) * 2 * np.pi, r * np.sqrt(rng.random(n))
        bx, by = x + d * np.cos(a), y + d * np.sin(a)
        parts.add(bx, by, base(bx, by) + low + rng.random(n) * (high - low), VEGETATION, T_BUSH, 0x21)


def _building(rng, parts: _Parts, rect, base: float, height: float, density: float, facade: float) -> None:  # type: ignore[no-untyped-def]
    """A flat roof at `height` above `base` and four facades with `facade` points per m² of wall."""
    x0, y0, x1, y1 = rect
    rx, ry = _uniform(rng, x0, y0, x1, y1, density)
    parts.add(rx, ry, base + height + rng.normal(0, SIGMA, len(rx)), BUILDING, T_ROOF)
    for (ax, ay), (bx, by) in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
        length = float(np.hypot(bx - ax, by - ay))
        n = int(round(facade * length * height))
        s, e = rng.random(n), rng.normal(0, SIGMA, n)
        nx, ny = (by - ay) / length, -(bx - ax) / length  # outward normal of the facade
        parts.add(ax + s * (bx - ax) + e * nx, ay + s * (by - ay) + e * ny, base + rng.random(n) * height, BUILDING, T_FACADE)


def _outliers(rng, parts: _Parts, count: int, x0, y0, x1, y1, top: Field, bottom: Field) -> None:  # type: ignore[no-untyped-def]
    """Single points far above everything (20–60 m over `top`) and below the ground (5–30 m under `bottom`)."""
    pts: list[tuple[float, float, float]] = []
    off = np.array([0.0, 6.0, -6.0, 0.0, 0.0]), np.array([0.0, 0.0, 0.0, 6.0, -6.0])
    while len(pts) < count:
        x, y = x0 + rng.random() * (x1 - x0), y0 + rng.random() * (y1 - y0)
        if np.ptp(bottom(x + off[0], y + off[1])) > 8.0:
            continue  # next to a vertical wall a point is not alone
        if len(pts) % 3 == 2:
            z = float(bottom(np.array([x]), np.array([y]))[0]) - 5.0 - 25.0 * rng.random()
        else:
            z = float(top(np.array([x]), np.array([y]))[0]) + 20.0 + 40.0 * rng.random()
        if all((x - a) ** 2 + (y - b) ** 2 + (z - c) ** 2 > 36.0 for a, b, c in pts):
            pts.append((x, y, z))
    p = np.array(pts)
    parts.add(p[:, 0], p[:, 1], p[:, 2], NOISE, T_OUTLIER)


# --- Т-З1 ------------------------------------------------------------------------------------------------


def plain(seed: int, *, side: float = 200.0, density: float = 8.0, order: str = "random") -> Scene:
    """200 × 200 m, z = 0, σ = 0.02 m, 8 points/m²; 6 buildings 10×10…20×30 m, 5 m high, no ground under
    them; 150 trees with 30 % of the ground under the crown; 50 single outliers."""
    rng = np.random.default_rng(seed)
    parts = _Parts()
    sizes = [(10, 10), (12, 16), (15, 15), (14, 22), (18, 24), (20, 30)]
    blocks = _discs(rng, 6, 12, 12, side - 12, side - 12, 19.0, 19.0, [], 2.0)
    rects = [(x - w / 2, y - h / 2, x + w / 2, y + h / 2) for (x, y, _r), (w, h) in zip(blocks, sizes, strict=True)]
    trees = _discs(rng, 150, 2, 2, side - 2, side - 2, 2.0, 4.0, blocks, 0.5)

    def flat(x, y):  # type: ignore[no-untyped-def]
        return np.zeros(np.shape(x))

    gx, gy = _uniform(rng, 0, 0, side, side, density)
    keep = np.ones(len(gx), bool)
    for x0, y0, x1, y1 in rects:
        keep &= ~((gx >= x0) & (gx <= x1) & (gy >= y0) & (gy <= y1))
    under = _in_discs(gx, gy, trees)
    keep = _thin(rng, keep, under, 0.30)
    gz = rng.normal(0, SIGMA, len(gx))
    parts.add(gx[keep & ~under], gy[keep & ~under], gz[keep & ~under], GROUND, T_FLAT)
    parts.add(gx[keep & under], gy[keep & under], gz[keep & under], GROUND, T_UNDER, 0x22)
    for rect in rects:
        _building(rng, parts, rect, 0.0, 5.0, density, 1.0)
    _trees(rng, parts, trees, flat, density)
    _outliers(rng, parts, 50, 5, 5, side - 5, side - 5, flat, flat)
    return parts.scene(rng, ORIGIN, flat, lambda x, y: np.ones(np.shape(x)), (0, 0, side, side), order)


# --- Т-З2 ------------------------------------------------------------------------------------------------


def funnel(
    seed: int,
    *,
    depth: float = 300.0,
    slope_deg: float = 50.0,
    bottom: float = 15.0,
    rim: float = 30.0,
    density: float = 2.0,
    veg_share: float = 0.15,
    order: str = "random",
) -> Scene:
    """A conical pit: flat bottom of radius `bottom`, a wall at `slope_deg`, a flat rim `rim` wide; σ = 0.02 m
    along the normal. Vegetation 0.5–3 m above the wall (vertically) on `veg_share` of its area, with half of
    the ground under it."""
    rng = np.random.default_rng(seed)
    parts = _Parts()
    tan = float(np.tan(np.radians(slope_deg)))
    r0, r1 = bottom, bottom + depth / tan
    r2 = r1 + rim
    c = r2  # centre of the pit in local coordinates

    def height(x, y):  # type: ignore[no-untyped-def]
        r = np.hypot(np.asarray(x) - c, np.asarray(y) - c)
        return np.clip((r - r0) * tan, 0.0, depth) - depth

    def cosine(x, y):  # type: ignore[no-untyped-def]
        r = np.hypot(np.asarray(x) - c, np.asarray(y) - c)
        return np.where((r > r0) & (r < r1), 1.0 / np.hypot(1.0, tan), 1.0)

    gx, gy = _uniform(rng, 0, 0, 2 * r2, 2 * r2, density)
    r = np.hypot(gx - c, gy - c)
    inside = r <= r2
    gx, gy, r = gx[inside], gy[inside], r[inside]
    wall = (r > r0) & (r < r1)
    # patches of vegetation on the wall: discs added until they cover the share of its area
    patches = _patches(rng, gx, gy, wall, veg_share, (c, c, r0, r1), 10.0, 22.0)
    covered = _in_discs(gx, gy, patches) & wall
    keep = _thin(rng, np.ones(len(gx), bool), covered, 0.5)
    # noise along the normal of the wall: (-sin, cos) in the (r, z) plane
    e = rng.normal(0, SIGMA, len(gx))
    sin, cos = tan / np.hypot(1.0, tan), 1.0 / np.hypot(1.0, tan)
    ur = np.where(wall, -sin * e, 0.0) / np.maximum(r, 1e-9)
    px, py = gx + ur * (gx - c), gy + ur * (gy - c)
    pz = height(gx, gy) + np.where(wall, cos * e, e)
    tag = np.where(wall, T_SLOPE, np.where(r <= r0, T_FLOOR, T_RIM)).astype(np.uint8)
    tag[covered] = T_UNDER
    parts.add(px[keep], py[keep], pz[keep], GROUND, tag[keep])
    vx, vy = _uniform(rng, 0, 0, 2 * r2, 2 * r2, density)
    vr = np.hypot(vx - c, vy - c)
    on = _in_discs(vx, vy, patches) & (vr > r0) & (vr < r1)
    vx, vy = vx[on], vy[on]
    parts.add(vx, vy, height(vx, vy) + 0.5 + 2.5 * rng.random(len(vx)), VEGETATION, T_BUSH, 0x21)
    origin = (ORIGIN[0], ORIGIN[1], ORIGIN[2] + depth)
    return parts.scene(rng, origin, height, cosine, (0, 0, 2 * r2, 2 * r2), order)


# --- Т-З3 ------------------------------------------------------------------------------------------------


def benches(
    seed: int,
    *,
    count: int = 4,
    berm: float = 10.0,
    wall: float = 15.0,
    length: float = 150.0,
    top: float = 20.0,
    floor: float = 30.0,
    density: float = 8.0,
    order: str = "random",
) -> Scene:
    """`count` benches along X: a top platform, vertical walls `wall` high with berms `berm` wide between
    them, a floor; 8 points/m² on the flats and on the walls; bushes on the berms; a building 8 × 8 × 6 m with
    facades on the floor, 11 m from the foot of the last wall."""
    rng = np.random.default_rng(seed)
    parts = _Parts()
    walls = [top + k * berm for k in range(count)]  # X of the walls, from the top one down
    width = walls[-1] + floor

    def level(x):  # type: ignore[no-untyped-def]
        """Height of the flat at X; at a wall — its lower level."""
        return wall * (count - np.searchsorted(np.asarray(walls), np.asarray(x), side="right"))

    def height(x, y):  # type: ignore[no-untyped-def]
        return level(x).astype(np.float64)

    house = (walls[-1] + 11.0, length / 2 - 4.0, walls[-1] + 19.0, length / 2 + 4.0)
    bushes: list[tuple[float, float, float]] = []
    for k in range(count - 1):
        bushes += _discs(rng, 6, walls[k] + 0.5, 3, walls[k] + berm - 0.5, length - 3, 0.8, 1.5, bushes, 0.5)
    bushes += _discs(rng, 4, 2, 3, top - 2, length - 3, 0.8, 1.5, bushes, 0.5)
    gx, gy = _uniform(rng, 0, 0, width, length, density)
    keep = ~((gx >= house[0]) & (gx <= house[2]) & (gy >= house[1]) & (gy <= house[3]))
    under = _in_discs(gx, gy, bushes)
    keep = _thin(rng, keep, under, 0.5)
    gz = level(gx) + rng.normal(0, SIGMA, len(gx))
    tag = np.where(gx < top, T_TOP, np.where(gx >= walls[-1], T_FLOOR, T_BERM)).astype(np.uint8)
    tag[under] = T_UNDER
    parts.add(gx[keep], gy[keep], gz[keep], GROUND, tag[keep])
    for k, xw in enumerate(walls):
        n = int(round(density * wall * length))
        z0 = wall * (count - k - 1)
        parts.add(xw + rng.normal(0, SIGMA, n), rng.random(n) * length, z0 + rng.random(n) * wall, GROUND, T_WALL)
    _bushes(rng, parts, bushes, height, density)
    _building(rng, parts, house, 0.0, 6.0, density, density)
    return parts.scene(rng, ORIGIN, height, lambda x, y: np.ones(np.shape(x)), (0, 0, width, length), order)


# --- the site of the benchmark ---------------------------------------------------------------------------

SITE = 700.0  # side of one site, m


def quarry(n: int, seed: int, *, order: str = "storage") -> Scene:
    """About `n` points on sites of 700 × 700 m (one site up to 12 million points, then 2 × 2, 3 × 3 …):
    a conical pit 100 m deep with a 50° wall and vegetation, a rectangular pit of 4 benches with vertical
    walls 15 m high, a dump 25 m high with slopes of 35°, a rolling plain with trees, bushes and buildings,
    single outliers. The density follows `n` (1 million — 2 points/m², 10 million — 20)."""
    rng = np.random.default_rng(seed)
    tiles = int(np.ceil(np.sqrt(n / 12.5e6)))
    density = n / (tiles * tiles * SITE * SITE) / 1.07  # 7 % of the points are walls, vegetation, buildings
    parts = _Parts()
    fields = []
    for ty in range(tiles):
        for tx in range(tiles):
            fields.append(_site(rng, parts, tx * SITE, ty * SITE, density))

    def height(x, y):  # type: ignore[no-untyped-def]
        x, y = np.asarray(x), np.asarray(y)
        k = np.clip((y // SITE).astype(int), 0, tiles - 1) * tiles + np.clip((x // SITE).astype(int), 0, tiles - 1)
        out = np.empty(np.shape(x))
        for i, (z, _c) in enumerate(fields):
            m = k == i
            out[m] = z(x[m], y[m])
        return out

    def cosine(x, y):  # type: ignore[no-untyped-def]
        x, y = np.asarray(x), np.asarray(y)
        k = np.clip((y // SITE).astype(int), 0, tiles - 1) * tiles + np.clip((x // SITE).astype(int), 0, tiles - 1)
        out = np.empty(np.shape(x))
        for i, (_z, c) in enumerate(fields):
            m = k == i
            out[m] = c(x[m], y[m])
        return out

    return parts.scene(rng, ORIGIN, height, cosine, (0, 0, tiles * SITE, tiles * SITE), order)


def _site(rng, parts: _Parts, x0: float, y0: float, density: float):  # type: ignore[no-untyped-def]
    """One site at (x0, y0); returns its (height, cosine) fields in the coordinates of the scene."""
    tan50, tan35 = float(np.tan(np.radians(50.0))), float(np.tan(np.radians(35.0)))
    pit = (x0 + 185.0, y0 + 185.0, 15.0, 100.0)  # centre, radius of the bottom, depth
    pit_r1 = pit[2] + pit[3] / tan50
    dump = (x0 + 190.0, y0 + 520.0, 6.0, 25.0)  # centre, radius of the top, height
    dump_r1 = dump[2] + dump[3] / tan35
    cut = (x0 + 400.0, y0 + 90.0, x0 + 620.0, y0 + 310.0)  # outer rectangle of the bench pit
    steps, berm, wall = 4, 12.0, 15.0

    def rolling(x, y):  # type: ignore[no-untyped-def]
        return 1.5 * np.sin((x - x0) * (2 * np.pi / SITE)) * np.cos((y - y0) * (2 * np.pi / SITE))

    def depth_cut(x, y):  # type: ignore[no-untyped-def]
        """Number of benches below the surface at X, Y (0 outside the bench pit)."""
        d = np.minimum(np.minimum(x - cut[0], cut[2] - x), np.minimum(y - cut[1], cut[3] - y))
        return np.clip(np.floor(d / berm) + 1, 0, steps)

    def height(x, y):  # type: ignore[no-untyped-def]
        x, y = np.asarray(x, np.float64), np.asarray(y, np.float64)
        z = rolling(x, y)
        rp = np.hypot(x - pit[0], y - pit[1])
        z = z + np.clip((rp - pit[2]) * tan50, 0.0, pit[3]) - pit[3]
        rd = np.hypot(x - dump[0], y - dump[1])
        z = z + np.clip((dump_r1 - rd) * tan35, 0.0, dump[3])
        return z - wall * depth_cut(x, y)

    def cosine(x, y):  # type: ignore[no-untyped-def]
        x, y = np.asarray(x, np.float64), np.asarray(y, np.float64)
        rp = np.hypot(x - pit[0], y - pit[1])
        rd = np.hypot(x - dump[0], y - dump[1])
        t = np.where((rp > pit[2]) & (rp < pit_r1), tan50, np.where((rd > dump[2]) & (rd < dump_r1), tan35, 0.0))
        return 1.0 / np.hypot(1.0, t)

    def in_cut(x, y, pad=0.0):  # type: ignore[no-untyped-def]
        return (x > cut[0] - pad) & (x < cut[2] + pad) & (y > cut[1] - pad) & (y < cut[3] + pad)

    # objects of the plain: buildings and trees in the upper right quarter, bushes anywhere on the plain
    area = (x0 + 380.0, y0 + 380.0, x0 + SITE - 10.0, y0 + SITE - 10.0)
    blocks = _discs(rng, 4, *area, 12.0, 12.0, [], 4.0)
    rects = [(x - w / 2, y - h / 2, x + w / 2, y + h / 2) for (x, y, _r), (w, h) in zip(blocks, [(8, 8), (10, 14), (12, 12), (9, 16)], strict=True)]
    trees = _discs(rng, 120, *area, 2.0, 4.0, blocks, 0.5)
    shrubs = _discs(rng, 60, x0 + 10, y0 + 330, x0 + 370, y0 + SITE - 10, 0.8, 1.6, [(dump[0], dump[1], dump_r1)], 1.0)
    for k in range(steps - 1):  # bushes on the berms of the bench pit
        d = berm * (k + 0.5)
        shrubs += _discs(rng, 5, cut[0] + d - 1.5, cut[1] + 40, cut[0] + d + 1.5, cut[3] - 40, 0.8, 1.4, shrubs, 0.5)

    gx, gy = _uniform(rng, x0, y0, x0 + SITE, y0 + SITE, density)
    keep = np.ones(len(gx), bool)
    for a, b, c, d in rects:
        keep &= ~((gx >= a) & (gx <= c) & (gy >= b) & (gy <= d))
    rp = np.hypot(gx - pit[0], gy - pit[1])
    on_wall = (rp > pit[2]) & (rp < pit_r1)
    patches = _patches(rng, gx, gy, on_wall, 0.15, (pit[0], pit[1], pit[2], pit_r1), 8.0, 16.0)
    covered = _in_discs(gx, gy, patches) & on_wall
    under = _in_discs(gx, gy, trees)
    low = _in_discs(gx, gy, shrubs) | covered
    keep = _thin(rng, _thin(rng, keep, under, 0.30), low, 0.5)
    # noise along the normal (the slopes of the cones); the rest of the ground is close to flat
    e = rng.normal(0, SIGMA, len(gx))
    cos = cosine(gx, gy)
    sin = np.sqrt(1.0 - cos * cos)
    rd = np.hypot(gx - dump[0], gy - dump[1])
    ux = np.where(on_wall, -(gx - pit[0]) / np.maximum(rp, 1e-9), (gx - dump[0]) / np.maximum(rd, 1e-9))
    uy = np.where(on_wall, -(gy - pit[1]) / np.maximum(rp, 1e-9), (gy - dump[1]) / np.maximum(rd, 1e-9))
    gz = height(gx, gy) + cos * e
    gx, gy = gx + sin * e * ux, gy + sin * e * uy
    cutd = depth_cut(gx, gy)
    tag = np.where(on_wall | (cos < 0.99), T_SLOPE, T_FLAT).astype(np.uint8)
    tag[(cutd > 0) & (cutd < steps)] = T_BERM
    tag[cutd == steps] = T_FLOOR
    tag[under | low] = T_UNDER
    parts.add(gx[keep], gy[keep], gz[keep], GROUND, tag[keep])
    # vertical walls of the bench pit: rectangles shrinking by a berm, `density` points per m² of wall
    for k in range(steps):
        d = berm * k
        a, b, c, dd = cut[0] + d, cut[1] + d, cut[2] - d, cut[3] - d
        for (px, py), (qx, qy) in (((a, b), (c, b)), ((c, b), (c, dd)), ((c, dd), (a, dd)), ((a, dd), (a, b))):
            length = float(np.hypot(qx - px, qy - py))
            m = int(round(density * length * wall))
            s, en = rng.random(m), rng.normal(0, SIGMA, m)
            nx, ny = (qy - py) / length, -(qx - px) / length
            wx, wy = px + s * (qx - px), py + s * (qy - py)
            top = rolling(wx, wy) - wall * k
            parts.add(wx + en * nx, wy + en * ny, top - rng.random(m) * wall, GROUND, T_WALL)
    for rect in rects:
        mid = float(height(np.array([(rect[0] + rect[2]) / 2]), np.array([(rect[1] + rect[3]) / 2]))[0])
        _building(rng, parts, rect, mid, 6.0, density, density / 4)
    _trees(rng, parts, trees, height, density)
    _bushes(rng, parts, shrubs, height, density)
    vx, vy = _uniform(rng, pit[0] - pit_r1, pit[1] - pit_r1, pit[0] + pit_r1, pit[1] + pit_r1, density)
    vr = np.hypot(vx - pit[0], vy - pit[1])
    on = _in_discs(vx, vy, patches) & (vr > pit[2]) & (vr < pit_r1)
    parts.add(vx[on], vy[on], height(vx[on], vy[on]) + 0.5 + 2.5 * rng.random(int(on.sum())), VEGETATION, T_BUSH, 0x21)
    count = max(20, int(density * SITE * SITE / 20_000))

    def roofs(x, y):  # type: ignore[no-untyped-def]
        return height(x, y) + 15.0

    _outliers(rng, parts, count, x0 + 5, y0 + 5, x0 + SITE - 5, y0 + SITE - 5, roofs, height)
    return height, cosine


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate a synthetic point cloud with true labels (.npz)")
    ap.add_argument("--scene", choices=["plain", "funnel", "benches", "quarry"], default="quarry")
    ap.add_argument("--points", type=int, default=1_000_000, help="for the scene «quarry»")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    s = quarry(a.points, a.seed) if a.scene == "quarry" else {"plain": plain, "funnel": funnel, "benches": benches}[a.scene](a.seed)
    np.savez(a.out, xyz=s.xyz, truth=s.truth, tag=s.tag, ret=s.ret)
    print(f"{a.out}: {len(s)} points")


if __name__ == "__main__":
    main()
