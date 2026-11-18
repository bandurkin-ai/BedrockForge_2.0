"""Synthetic wireframes for the benchmarks of Э8 (ADR 0013 «Замеры»; `bench/scenarios/wireframe_*.py`,
`hole_intersections.py`, `mesh_formats.py`), reproducible, float64 on UTM-sized coordinates (float32 cannot hold
them: its step is 0.5 m on the northings).

    pit_surface(n)        an open surface of about n triangles: a relief as a survey gives it (hills with noise
                          of centimetres — nothing in it is flat) cut by open pits with level berms and plane
                          faces, turned 17° to the grid — half of the cells lie in the plane of their
                          neighbours, the kind of surface the levels of detail are hard on (ADR 0013, 8.12)
    Surface.height(x, y)  the height of the triangles of the surface over a point: the reference of a section,
                          a pick and a crossing
    ore_body(n, …)        a closed solid (an ellipsoid turned and plunged), normals outward
    fault(surface, n)     a steep wavy sheet that cuts through the surface (self-intersections of a wireframe
                          that holds both)
    scene(n)              about n triangles as the wireframes of one file: a large surface, a fault, three ore
                          bodies and forty lenses — with names, colours and attributes
    drillholes(surface, n) collars and survey stations of n holes drilled from the surface: every fifth collar
                          is a vertex of the surface (as a DTM built through the collars has it) — its hole
                          goes into the ground there or, from a crest, out over the face; the others stand
                          above it

The triangles are in the order a file has them (row by row), not in the spatial order of the store.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from tools.gen.mesh import uv_sphere

ORIGIN = (512_345.678, 6_234_567.123, 412.345)
STEP = 2.0  # metres between the nodes of a surface
PITS_EVERY = 1200.0  # about one pit a square of this side
PIT_TURN = 17.0  # degrees between the sides of a pit and the grid
BENCH, BERM, RISE = 20.0, 8.0, 15.0  # a bench in plan: a level berm of 8 m and a face of 12 m that drops 15 m
RIM = 72.0  # the level of the first berm: just above the relief
SNAP = 1e-8  # of a cell: a point this near to a node is the node


@dataclass(frozen=True, eq=False)
class Surface:
    """A surface z = f(x, y) on a grid of nx × ny cells of two triangles (normals up)."""

    nx: int
    ny: int
    step: float
    origin: tuple[float, float, float]
    z: np.ndarray  # (ny + 1, nx + 1): the heights of the nodes, project coordinates
    period: float  # the side of the square of one pit
    bottom: float  # `inward` from which a pit is its level bottom

    @property
    def triangles(self) -> int:
        return 2 * self.nx * self.ny

    @property
    def side(self) -> tuple[float, float]:
        return self.nx * self.step, self.ny * self.step

    def mesh(self) -> tuple[np.ndarray, np.ndarray]:
        """(xyz float64 n×3, tri int32 m×3): the nodes row by row, the two triangles of a cell together."""
        nx, ny = self.nx, self.ny
        x = self.origin[0] + np.arange(nx + 1) * self.step
        y = self.origin[1] + np.arange(ny + 1) * self.step
        xyz = np.empty(((nx + 1) * (ny + 1), 3), dtype=np.float64)
        xyz[:, 0] = np.tile(x, ny + 1)
        xyz[:, 1] = np.repeat(y, nx + 1)
        xyz[:, 2] = self.z.reshape(-1)
        i = (np.arange(ny, dtype=np.int64)[:, None] * (nx + 1) + np.arange(nx, dtype=np.int64)[None, :]).reshape(-1)
        tri = np.empty((len(i), 2, 3), dtype=np.int32)
        tri[:, 0] = np.column_stack([i, i + 1, i + nx + 2])
        tri[:, 1] = np.column_stack([i, i + nx + 2, i + nx + 1])
        return xyz, tri.reshape(-1, 3)

    def cells(self, x: Any, y: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """(inside, i, j, u, w): the cell of every point and its place in it, 0…1 (a point on a node is the node)."""
        fx = (np.asarray(x, dtype=np.float64) - self.origin[0]) / self.step
        fy = (np.asarray(y, dtype=np.float64) - self.origin[1]) / self.step
        fx, fy = np.broadcast_arrays(fx, fy)
        with np.errstate(invalid="ignore"):
            inside = (fx >= -SNAP) & (fx <= self.nx + SNAP) & (fy >= -SNAP) & (fy <= self.ny + SNAP)
        out = []
        for f, n in ((fx, self.nx), (fy, self.ny)):
            f = np.where(inside, f, 0.0)
            near = np.rint(f)
            f = np.where(np.abs(f - near) <= SNAP, near, f)
            k = np.clip(np.floor(f), 0, n - 1)
            out += [k.astype(np.int64), f - k]
        return inside, out[0], out[2], out[1], out[3]

    def height(self, x: Any, y: Any) -> np.ndarray:
        """The height of the surface over (x, y) — of the plane of the triangle over the point; exact at the
        nodes; NaN outside the surface."""
        inside, i, j, u, w = self.cells(x, y)
        z00, z10, z11, z01 = self.z[j, i], self.z[j, i + 1], self.z[j + 1, i + 1], self.z[j + 1, i]
        low = (1.0 - u) * z00 + (u - w) * z10 + w * z11  # the triangle under the diagonal: nodes 00, 10, 11
        high = (1.0 - w) * z00 + u * z11 + (w - u) * z01  # … and over it: 00, 11, 01
        return np.where(inside, np.where(w <= u, low, high), np.nan)

    def inward(self, x: Any, y: Any) -> np.ndarray:
        """How far inside its pit a point is, in plan, metres (negative — outside every pit)."""
        lx = np.mod(np.asarray(x, dtype=np.float64) - self.origin[0], self.period) - self.period / 2.0
        ly = np.mod(np.asarray(y, dtype=np.float64) - self.origin[1], self.period) - self.period / 2.0
        c, s = math.cos(math.radians(PIT_TURN)), math.sin(math.radians(PIT_TURN))
        return 0.35 * self.period - np.maximum(np.abs(lx * c + ly * s), np.abs(ly * c - lx * s))

    def level(self, x: Any, y: Any, margin: float) -> np.ndarray:
        """The level of the berm or of the pit bottom a point stands on, at least `margin` metres (in plan)
        from its edges; NaN — the point is on a face, near an edge, or the place may be the relief."""
        t = self.inward(x, y)
        k = np.floor(t / BENCH)
        r = t - k * BENCH
        berm = (t < self.bottom - margin) & (r > margin) & (r < BERM - margin)
        level = np.where(t > self.bottom + margin, _pit_level(np.full_like(t, self.bottom)), _pit_level(t))
        ok = ((t > self.bottom + margin) | berm) & (level < RELIEF_FLOOR - 1.0)
        return np.where(ok, self.origin[2] + level, np.nan)

    def save(self, path: Path) -> None:
        np.savez(path, grid=[self.nx, self.ny], numbers=[self.step, *self.origin, self.period, self.bottom], z=self.z)

    @classmethod
    def load(cls, path: Path) -> Surface:
        with np.load(path) as f:
            (nx, ny), (step, ox, oy, oz, period, bottom) = f["grid"].tolist(), f["numbers"].tolist()
            return cls(int(nx), int(ny), step, (ox, oy, oz), f["z"], period, bottom)


RELIEF_FLOOR = 49.0  # the relief never goes below this (relative to ORIGIN): a pit level under it is the pit


def _pit_level(inward: np.ndarray) -> np.ndarray:
    k = np.floor(inward / BENCH)
    r = inward - k * BENCH
    return RIM - RISE * (k + np.clip((r - BERM) / (BENCH - BERM), 0.0, 1.0))


def pit_surface(n_triangles: int, *, seed: int = 8, origin: tuple[float, float, float] = ORIGIN) -> Surface:
    """A square surface of about `n_triangles` triangles (module docstring)."""
    n = max(4, round(math.sqrt(n_triangles / 2.0)))
    side = n * STEP
    period = side / max(1, round(side / PITS_EVERY))
    rng = np.random.default_rng(seed)
    x, y = np.meshgrid(np.arange(n + 1) * STEP, np.arange(n + 1) * STEP)
    relief = 60.0 + 8.0 * np.sin(x / 310.0) * np.cos(y / 270.0) + 2.0 * np.sin(x / 37.0 + y / 53.0)
    relief += np.clip(rng.normal(0.0, 0.05, relief.shape), -0.3, 0.3)  # a survey: nothing is flat
    assert float(relief.min()) > RELIEF_FLOOR
    blank = Surface(n, n, STEP, origin, relief, period, 0.25 * period)
    inward = blank.inward(origin[0] + x, origin[1] + y)
    pit = _pit_level(np.clip(inward, 0.0, blank.bottom))
    z = origin[2] + np.where(inward > 0.0, np.minimum(relief, pit), relief)
    return Surface(n, n, STEP, origin, z, period, blank.bottom)


def _turn(azimuth: float, plunge: float) -> np.ndarray:
    """Rows: the long axis (along `azimuth`, plunging `plunge` degrees down), the level cross axis, the third."""
    a, p = math.radians(azimuth), math.radians(plunge)
    along = np.array([math.sin(a) * math.cos(p), math.cos(a) * math.cos(p), -math.sin(p)])
    across = np.array([math.cos(a), -math.sin(a), 0.0])
    return np.array([along, across, np.cross(along, across)])


def ore_body(
    n_triangles: int,
    *,
    centre: tuple[float, float, float],
    radii: tuple[float, float, float],
    azimuth: float = 0.0,
    plunge: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """A closed ellipsoid of about `n_triangles` triangles with its normals outward: `radii` along its long
    axis (azimuth, plunge in degrees), across it and the third way."""
    v, t = uv_sphere(max(n_triangles, 16), radius=1.0, centre=(0.0, 0.0, 0.0))
    xyz = (v * np.asarray(radii, dtype=np.float64)) @ _turn(azimuth, plunge) + np.asarray(centre, dtype=np.float64)
    tri = t.astype(np.int32)
    a, b, c = (xyz[tri[:, k]] for k in range(3))
    if float(np.einsum("ij,ij->", a - xyz.mean(axis=0), np.cross(b - a, c - a))) < 0.0:
        tri = np.ascontiguousarray(tri[:, ::-1])
    return xyz, tri


def body_distance(points: Any, centre: Any, radii: Any, azimuth: float, plunge: float) -> np.ndarray:
    """Where points are against the ellipsoid of `ore_body`: 1 on it, below 1 inside."""
    local = (np.asarray(points, dtype=np.float64) - np.asarray(centre, dtype=np.float64)) @ _turn(azimuth, plunge).T
    return np.sqrt(np.einsum("ij,ij->i", local / np.asarray(radii), local / np.asarray(radii)))


def fault(
    surface: Surface, n_triangles: int, *, azimuth: float = 63.0, dip: float = 75.0
) -> tuple[np.ndarray, np.ndarray]:
    """A sheet of about `n_triangles` triangles through the middle of the surface, striking along `azimuth`
    and dipping `dip` degrees, a little wavy: it goes from above the highest node to below the lowest."""
    sx, sy = surface.side
    top, low = float(surface.z.max()) + 20.0, float(surface.z.min()) - 20.0
    length, width = 0.7 * min(sx, sy), (top - low) / math.sin(math.radians(dip))
    cell = math.sqrt(2.0 * length * width / max(n_triangles, 2))
    ns, nw = max(1, round(length / cell)), max(1, round(width / cell))
    s, w = np.meshgrid(np.linspace(-length / 2.0, length / 2.0, ns + 1), np.linspace(0.0, width, nw + 1))
    a, d = math.radians(azimuth), math.radians(dip)
    strike = np.array([math.sin(a), math.cos(a), 0.0])
    down = np.array([math.cos(a) * math.cos(d), -math.sin(a) * math.cos(d), -math.sin(d)])
    normal = np.cross(strike, down)
    middle = np.array([surface.origin[0] + sx / 2.0, surface.origin[1] + sy / 2.0, top])
    middle[:2] -= down[:2] * width / 2.0  # the middle of the sheet is under the middle of the surface
    wave = 1.5 * np.sin(s / 90.0) * np.cos(w / 70.0)
    xyz = middle + s.reshape(-1, 1) * strike + w.reshape(-1, 1) * down + wave.reshape(-1, 1) * normal
    i = (np.arange(nw, dtype=np.int64)[:, None] * (ns + 1) + np.arange(ns, dtype=np.int64)[None, :]).reshape(-1)
    tri = np.concatenate([np.column_stack([i, i + 1, i + ns + 2]), np.column_stack([i, i + ns + 2, i + ns + 1])])
    return xyz, tri.astype(np.int32)


BODIES = (  # the place in the surface (shares of its side), the depth of the centre, radii, azimuth, plunge
    ((0.50, 0.50), 330.0, (0.16, 0.07, 0.035), 40.0, 25.0),
    ((0.30, 0.66), 250.0, (0.10, 0.05, 0.030), 115.0, 10.0),
    ((0.70, 0.30), 420.0, (0.12, 0.04, 0.045), 310.0, 40.0),
)


def body_of(surface: Surface, k: int) -> dict[str, Any]:
    """The arguments of `ore_body` for the body `k` of a surface: under it, sized by its side."""
    (fx, fy), depth, radii, azimuth, plunge = BODIES[k]
    sx, sy = surface.side
    size = min(sx, sy, 1400.0)  # a larger surface has the bodies of a 1.4 km one: they stay under its pits
    centre = (surface.origin[0] + fx * sx, surface.origin[1] + fy * sy, surface.origin[2] - depth * size / 1400.0)
    return {"centre": centre, "radii": tuple(r * size for r in radii), "azimuth": azimuth, "plunge": plunge}


def scene(n_triangles: int, *, seed: int = 8) -> list[dict[str, Any]]:
    """About `n_triangles` triangles as the wireframes of one file: [{name, xyz, tri, color, attrs}]."""
    surface = pit_surface(round(0.62 * n_triangles), seed=seed)
    out = [_mesh("Рельеф карьера", surface.mesh(), "#8a99a8", [("Тип", "Поверхность"), ("Съёмка", "2026-10-01")])]
    out.append(_mesh("Разлом Северный", fault(surface, round(0.05 * n_triangles)), "#d04a3a", [("Тип", "Разлом")]))
    for k in range(len(BODIES)):
        body = ore_body(round(0.09 * n_triangles), **body_of(surface, k))
        attrs = [("Тип", "Руда"), ("Категория", ("C1", "C2", "P1")[k]), ("Содержание Au, г/т", 1.25 + 0.5 * k)]
        out.append(_mesh(f"Рудное тело {k + 1}", body, ("#e0a030", "#c07020", "#f0c040")[k], attrs))
    rng = np.random.default_rng(seed + 1)
    sx, sy = surface.side
    for k in range(40):
        centre = (
            surface.origin[0] + float(rng.uniform(0.1, 0.9)) * sx,
            surface.origin[1] + float(rng.uniform(0.1, 0.9)) * sy,
            surface.origin[2] - float(rng.uniform(150.0, 500.0)),
        )
        radii = (float(rng.uniform(8.0, 25.0)), float(rng.uniform(4.0, 10.0)), float(rng.uniform(1.0, 4.0)))
        lens = ore_body(max(44, round(0.06 * n_triangles / 40)), centre=centre, radii=radii,
                        azimuth=float(rng.uniform(0.0, 360.0)), plunge=float(rng.uniform(0.0, 60.0)))  # fmt: skip
        out.append(_mesh(f"Линза {k + 1:02d}", lens, "#b08050", [("Тип", "Линза"), ("Номер", k + 1)]))
    return out


def _mesh(name: str, mesh: tuple[np.ndarray, np.ndarray], color: str, attrs: list[tuple[str, Any]]) -> dict[str, Any]:
    return {"name": name, "xyz": mesh[0], "tri": mesh[1], "color": color,
            "attrs": [{"name": k, "value": v} for k, v in attrs]}  # fmt: skip


def drillholes(surface: Surface, holes: int, *, stations: int = 20, seed: int = 7) -> dict[str, np.ndarray]:
    """Collars and survey stations of `holes` holes drilled from the surface: {x, y, z, td, s_hole, s_depth,
    s_az, s_dip}. Every fifth collar is a node of the surface, the others stand 0.5–30 m above it; every hole
    starts at 45–90° — so one collared on a crest and aimed out of the pit wall, flatter than its face (51°),
    starts in the air. The holes are 150–400 m long and wander (the stations come in any order)."""
    rng = np.random.default_rng(seed)
    sx, sy = surface.side
    x = surface.origin[0] + rng.uniform(0.08, 0.92, holes) * sx
    y = surface.origin[1] + rng.uniform(0.08, 0.92, holes) * sy
    on_node = np.arange(holes) % 5 == 0
    x[on_node] = surface.origin[0] + np.rint((x[on_node] - surface.origin[0]) / surface.step) * surface.step
    y[on_node] = surface.origin[1] + np.rint((y[on_node] - surface.origin[1]) / surface.step) * surface.step
    z = surface.height(x, y) + np.where(on_node, 0.0, rng.uniform(0.5, 30.0, holes))
    td = rng.uniform(150.0, 400.0, holes)
    s_hole = np.repeat(np.arange(holes, dtype=np.int64), stations)
    s_depth = (np.tile(np.arange(stations), holes) * np.repeat(td / stations, stations)).round(1)
    drift = rng.normal(0.0, 2.0, (holes, stations)).cumsum(axis=1).ravel()
    s_az = (np.repeat(rng.uniform(0.0, 360.0, holes), stations) + drift) % 360.0
    drift = rng.normal(0.0, 1.0, (holes, stations)).cumsum(axis=1).ravel()
    dip = rng.uniform(-90.0, -45.0, holes)
    s_dip = np.clip(np.repeat(dip, stations) + drift, -90.0, -30.0)
    order = rng.permutation(holes * stations)
    return {"x": x, "y": y, "z": z, "td": td, "s_hole": s_hole[order], "s_depth": s_depth[order],
            "s_az": s_az[order], "s_dip": s_dip[order]}  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the wireframes of a synthetic deposit (.npz a wireframe)")
    ap.add_argument("--triangles", type=int, default=1_000_000)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    for k, m in enumerate(scene(a.triangles)):
        np.savez(a.out / f"{k:02d}.npz", vertices=m["xyz"], triangles=m["tri"])
        print(f"{k:02d} {m['name']}: {len(m['xyz'])} vertices, {len(m['tri'])} triangles")


if __name__ == "__main__":
    main()
