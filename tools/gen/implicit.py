"""Synthetic drillholes and strings for implicit modelling (Э19, ADR 0026; ТЗ 8.9.2), reproducible by seed 12345.

`generate(out)` writes a small scene as CSV (the committed fixtures `tests/fixtures/implicit/`): 100 straight holes
through folded layers (ALV above a wavy surface, SS, SH below it, GR at the bottom), a spherical intrusion INT and a
steep slab VN (a vein). The contacts are exact — found by bisection on the analytical geology — and then written with
two decimals like any table of a project.

    collars.csv     HOLE,X,Y,Z,TD,AZIMUTH,DIP           straight holes (azimuth and dip of the collar, no surveys)
    lithology.csv   HOLE,FROM,TO,CODE                    the rock of every stretch, the bounds are the contacts
    assays.csv      HOLE,FROM,TO,AU                      5 m samples, AU by rock (INT and VN are rich), some missing
    strings.csv     LAYER,LINE,CLOSED,X,Y,Z              hand data: the contact of SS/SH in three sections, a closed
                                                         ring in the intrusion, points outside the intrusion

`holes_arrays(holes, seed)` — a big set of arrays without files for the measures (50 000 holes of ~100 m, 6 codes, runs
of 5–25 m, a few gaps in the code), the same way `tools/gen/drillholes.arrays` does it for samples.

    python tools/gen/implicit.py --out tests/fixtures/implicit
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

import numpy as np

SEED = 12345
CODES = ("ALV", "SS", "SH", "GR", "INT", "VN")
SPHERE_CENTRE = (200.0, 200.0, 200.0)
SPHERE_RADIUS = 55.0
SLAB_POINT = (320.0, 200.0, 200.0)  # a point of the mid-plane of the vein
SLAB_NORMAL = (0.8, 0.0, 0.6)  # unit normal of the vein
SLAB_HALF = 1.5  # half thickness, m
GRID, SPACING, TOP = 10, 40.0, 300.0
X0, Y0 = 20.0, 20.0
SAMPLE = 5.0  # m: the length of an assay sample
DEPTH_STEP = 0.25  # m: the step of the search for a change of the rock along a hole
FOLD = (260.0, 15.0, 300.0, 400.0, 40.0)  # z1 = a + b·sin(2πx/λx)·cos(2πy/λy) and the thickness of SS and SH


def surface(x: Any, y: Any) -> Any:
    """The upper surface of the layers SS (a fold): z1(x, y)."""
    a, b, lx, ly, _t = FOLD
    return a + b * np.sin(2.0 * np.pi * np.asarray(x) / lx) * np.cos(2.0 * np.pi * np.asarray(y) / ly)


def rock(x: Any, y: Any, z: Any) -> Any:
    """The code index (`CODES`) at points: the layers, over them the intrusion, over it the vein."""
    x, y, z = (np.asarray(v, dtype=np.float64) for v in (x, y, z))
    z1 = surface(x, y)
    t = FOLD[4]
    code = np.where(z >= z1, 0, np.where(z >= z1 - t, 1, np.where(z >= z1 - 2.0 * t, 2, 3)))
    inside = (x - SPHERE_CENTRE[0]) ** 2 + (y - SPHERE_CENTRE[1]) ** 2 + (z - SPHERE_CENTRE[2]) ** 2 <= SPHERE_RADIUS**2
    code = np.where(inside, 4, code)
    n = np.asarray(SLAB_NORMAL)
    d = (x - SLAB_POINT[0]) * n[0] + (y - SLAB_POINT[1]) * n[1] + (z - SLAB_POINT[2]) * n[2]
    return np.where((np.abs(d) <= SLAB_HALF) & (y > 100.0) & (y < 300.0), 5, code)


def _direction(azimuth: np.ndarray, dip: np.ndarray) -> np.ndarray:
    """Unit vectors of holes: azimuth from north clockwise, dip negative downwards (the convention of the project)."""
    az, dp = np.radians(azimuth), np.radians(dip)
    return np.column_stack([np.sin(az) * np.cos(dp), np.cos(az) * np.cos(dp), np.sin(dp)])


def scene(holes: int = 100, seed: int = SEED) -> dict[str, Any]:
    """The collars, the stretches of every rock and the samples of the scene (arrays; `generate` writes them)."""
    rng = np.random.default_rng(seed)
    side = max(1, int(round(holes**0.5)))
    k = np.arange(holes)
    x = X0 + (k % side) * SPACING + rng.uniform(-6.0, 6.0, holes)
    y = Y0 + (k // side) * SPACING + rng.uniform(-6.0, 6.0, holes)
    z = TOP + rng.uniform(-4.0, 4.0, holes)
    az = rng.uniform(0.0, 360.0, holes).round(1)
    dip = -rng.uniform(62.0, 90.0, holes).round(1)
    td = rng.uniform(105.0, 135.0, holes).round(1)
    d = _direction(az, dip)
    names = [f"H{h + 1:03d}" for h in range(holes)]

    def at(h: np.ndarray, depth: np.ndarray) -> np.ndarray:
        return rock(x[h] + d[h, 0] * depth, y[h] + d[h, 1] * depth, z[h] + d[h, 2] * depth)  # type: ignore[no-any-return]

    grid = np.arange(0.0, td.max() + DEPTH_STEP, DEPTH_STEP)
    rows: list[tuple[int, float, float, int]] = []
    for h in range(holes):
        depth = grid[grid <= td[h]]
        if depth[-1] < td[h]:
            depth = np.append(depth, td[h])
        code = at(np.full(depth.shape, h), depth)
        change = np.flatnonzero(code[1:] != code[:-1])
        lo, hi = depth[change].copy(), depth[change + 1].copy()
        c_lo = code[change]
        for _ in range(40):  # bisection: the contact between the two samples
            mid = 0.5 * (lo + hi)
            same = at(np.full(mid.shape, h), mid) == c_lo
            lo, hi = np.where(same, mid, lo), np.where(same, hi, mid)
        cuts = np.concatenate([np.round(0.5 * (lo + hi), 2), [td[h]]])
        codes = np.concatenate([code[:1], code[change + 1]])
        prev = 0.0
        for b, c in zip(cuts, codes, strict=True):
            if b <= prev:  # a stretch shorter than 1 cm is lost
                continue
            if rows and rows[-1][0] == h and rows[-1][3] == int(c):
                rows[-1] = (h, rows[-1][1], float(b), int(c))
            else:
                rows.append((h, prev, float(b), int(c)))
            prev = float(b)
    litho = np.array(rows, dtype=[("hole", "i8"), ("from", "f8"), ("to", "f8"), ("code", "i8")])
    samples = []
    for h in range(holes):
        for a in np.arange(0.0, np.floor(td[h] / SAMPLE) * SAMPLE, SAMPLE):
            m = float(at(np.array([h]), np.array([a + 0.5 * SAMPLE]))[0])
            mean = (0.05, 0.3, 0.1, 0.02, 1.5, 6.0)[int(m)]
            v = float(np.exp(rng.normal(np.log(mean), 0.6)))
            samples.append((h, a, a + SAMPLE, v if rng.random() > 0.04 else np.nan))
    assays = np.array(samples, dtype=[("hole", "i8"), ("from", "f8"), ("to", "f8"), ("au", "f8")])
    return {"names": names, "x": x, "y": y, "z": z, "td": td, "azimuth": az, "dip": dip, "litho": litho,
            "assays": assays}  # fmt: skip


def strings() -> list[tuple[str, int, int, np.ndarray]]:
    """Hand data: (layer, line, closed, vertices) — the contact SS/SH on three sections, a ring in the intrusion,
    points outside of it."""
    out: list[tuple[str, int, int, np.ndarray]] = []
    xs = np.linspace(20.0, 380.0, 13)
    for k, yy in enumerate((100.0, 200.0, 300.0)):
        out.append(
            (
                "Контакт SS-SH",
                k,
                0,
                np.column_stack([xs, np.full(xs.shape, yy), surface(xs, np.full(xs.shape, yy)) - FOLD[4]]),
            )
        )
    a = np.linspace(0.0, 2.0 * np.pi, 13)[:-1]
    ring = np.column_stack(
        [SPHERE_CENTRE[0] + 30.0 * np.cos(a), SPHERE_CENTRE[1] + 30.0 * np.sin(a), np.full(a.shape, SPHERE_CENTRE[2])]
    )
    out.append(("Внутри INT", 0, 1, np.vstack([ring, ring[:1]])))
    far = np.array([[20.0, 20.0, 150.0], [380.0, 20.0, 150.0], [20.0, 380.0, 150.0], [380.0, 380.0, 150.0]])
    for k, p in enumerate(far):
        out.append(("Снаружи INT", k, 0, p[None, :]))
    return out


def _write(path: Path, header: list[str], rows: Any) -> int:
    n = 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        for r in rows:
            w.writerow(r)
            n += 1
    return n


def generate(out: Path, holes: int = 100, seed: int = SEED) -> dict[str, int]:
    """Writes the CSV files of the scene into `out`; returns the numbers of rows."""
    s = scene(holes, seed)
    out.mkdir(parents=True, exist_ok=True)
    names = s["names"]
    collars = (
        [names[h], f"{s['x'][h]:.2f}", f"{s['y'][h]:.2f}", f"{s['z'][h]:.2f}", f"{s['td'][h]:.1f}",
         f"{s['azimuth'][h]:.1f}", f"{s['dip'][h]:.1f}"]
        for h in range(holes)
    )  # fmt: skip
    litho = ([names[r["hole"]], f"{r['from']:.2f}", f"{r['to']:.2f}", CODES[r["code"]]] for r in s["litho"])
    assays = (
        [names[r["hole"]], f"{r['from']:.2f}", f"{r['to']:.2f}", "" if np.isnan(r["au"]) else f"{r['au']:.3f}"]
        for r in s["assays"]
    )
    rows = (
        [name, line, closed, f"{v[0]:.2f}", f"{v[1]:.2f}", f"{v[2]:.2f}"]
        for name, line, closed, xyz in strings()
        for v in xyz
    )
    return {
        "collars": _write(out / "collars.csv", ["HOLE", "X", "Y", "Z", "TD", "AZIMUTH", "DIP"], collars),
        "lithology": _write(out / "lithology.csv", ["HOLE", "FROM", "TO", "CODE"], litho),
        "assays": _write(out / "assays.csv", ["HOLE", "FROM", "TO", "AU"], assays),
        "strings": _write(out / "strings.csv", ["LAYER", "LINE", "CLOSED", "X", "Y", "Z"], rows),
    }


def holes_arrays(holes: int, seed: int = SEED) -> dict[str, np.ndarray]:
    """A big set without files for the measures: straight holes of 80–120 m, intervals of 5–25 m with one of six codes
    (no equal codes in a row), now and then a gap in the code (−1). Returns the collars (x, y, z, td, azimuth, dip),
    the rows (hole, from, to, code) ordered by (hole, from) and `starts` of the holes."""
    rng = np.random.default_rng(seed)
    side = int(np.ceil(np.sqrt(holes)))
    k = np.arange(holes)
    x = 500_000.0 + (k % side) * 25.0 + rng.uniform(-3.0, 3.0, holes)
    y = 7_000_000.0 + (k // side) * 25.0 + rng.uniform(-3.0, 3.0, holes)
    z = 300.0 + rng.uniform(-5.0, 5.0, holes)
    td = rng.uniform(80.0, 120.0, holes).round(1)
    az, dip = rng.uniform(0.0, 360.0, holes), -rng.uniform(55.0, 90.0, holes)
    per = np.ceil(td / 15.0).astype(np.int64) + 2  # intervals per hole: enough to cover; trimmed to td below
    hole = np.repeat(k, per)
    length = rng.uniform(5.0, 25.0, hole.shape[0])
    ends = np.cumsum(length)
    first = np.cumsum(per) - per
    ends = ends - np.repeat(ends[first] - length[first], per)  # depth of the end of every row, from 0 in each hole
    frm = ends - length
    keep = frm < td[hole]
    hole, frm, ends = hole[keep], frm[keep], np.minimum(ends[keep], td[hole[keep]])
    code = rng.integers(0, len(CODES), hole.shape[0]).astype(np.int64)
    same = np.concatenate([[False], (code[1:] == code[:-1]) & (hole[1:] == hole[:-1])])
    code[same] = (code[same] + 1) % len(CODES)
    code[rng.random(code.shape[0]) < 0.01] = -1
    starts = np.concatenate([[0], np.cumsum(np.bincount(hole, minlength=holes))]).astype(np.int64)
    return {"x": x, "y": y, "z": z, "td": td, "azimuth": az, "dip": dip, "hole": hole, "from": frm, "to": ends,
            "code": code, "starts": starts}  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--holes", type=int, default=100)
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()
    for name, n in generate(args.out, args.holes, args.seed).items():
        print(f"{name}: {n}")


if __name__ == "__main__":
    main()
