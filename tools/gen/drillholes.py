"""Synthetic drillhole set: collars, surveys, assays, lithology (CSV), reproducible by seed.

Volumes from ТЗ 5.3: up to 50 000 holes / 5 million samples. Values include missing data and
detection limits on purpose (`-999`, empty, `<0.01`) so that importers must report them.

`arrays` — the same kind of set as numpy arrays, without files (the benches of sample processing, ADR 0018
«Замеры»: 5 million samples of 10 fields in seconds), with what a real base has and a clean one does not:
samples of unequal length and with gaps, grades with ties at the detection limit, nuggets and unassayed runs,
a lithology whose contacts fall inside samples, holes without TD, without surveys, without a collar, without a
lithology, nearly flat holes, and infill drilling of the rich places (clustered collars).
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

import numpy as np

LITHO = ["ПЕС", "АЛВ", "ГРН", "КВЦ", "СЛН", "ДИО"]
GRADES = ("AU", "AG", "CU", "PB", "ZN", "FE", "S", "AS")  # numeric grades of `arrays`, in this order
LITHO_GRADE = (0.6, 0.8, 1.0, 4.0, 1.5, 1.0)  # the gold of a rock against the mean: quartz is the rich one
X0, Y0, GRID_M, SURVEY_M = 500_000.0, 7_000_000.0, 25.0, 50.0


def generate(out: Path, holes: int, samples_per_hole: int, seed: int = 1) -> dict[str, int]:
    rng = np.random.default_rng(seed)
    out.mkdir(parents=True, exist_ok=True)
    side = int(np.ceil(np.sqrt(holes)))
    n_samples = 0
    with (out / "collars.csv").open("w", newline="", encoding="utf-8") as fc, \
         (out / "surveys.csv").open("w", newline="", encoding="utf-8") as fs, \
         (out / "assays.csv").open("w", newline="", encoding="utf-8") as fa, \
         (out / "lithology.csv").open("w", newline="", encoding="utf-8") as fl:
        wc, ws, wa, wl = csv.writer(fc), csv.writer(fs), csv.writer(fa), csv.writer(fl)
        wc.writerow(["HOLE_ID", "EAST", "NORTH", "RL", "DEPTH"])
        ws.writerow(["HOLE_ID", "DEPTH", "AZIMUTH", "DIP"])
        wa.writerow(["HOLE_ID", "FROM", "TO", "AU", "AG", "CU"])
        wl.writerow(["HOLE_ID", "FROM", "TO", "ROCK"])
        for h in range(holes):
            name = f"СКВ-{h + 1:05d}"
            x = 500_000.0 + (h % side) * 25.0 + rng.normal(0, 2)
            y = 7_000_000.0 + (h // side) * 25.0 + rng.normal(0, 2)
            z = 300.0 + rng.normal(0, 5)
            depth = float(samples_per_hole)  # 1 m samples
            wc.writerow([name, f"{x:.3f}", f"{y:.3f}", f"{z:.3f}", f"{depth:.2f}"])
            az, dip = rng.uniform(0, 360), rng.uniform(-90, -55)
            for d in np.arange(0.0, depth + 1e-9, 50.0):
                ws.writerow([name, f"{d:.2f}", f"{(az + d * 0.02) % 360:.2f}", f"{min(-45.0, dip + d * 0.01):.2f}"])
            au = rng.lognormal(-1.0, 1.2, samples_per_hole)
            for i in range(samples_per_hole):
                r = rng.random()
                val = "-999" if r < 0.005 else ("" if r < 0.01 else ("<0.01" if au[i] < 0.01 else f"{au[i]:.3f}"))
                wa.writerow([name, f"{i:.2f}", f"{i + 1:.2f}", val, f"{au[i] * 3.1:.2f}", f"{au[i] * 0.04:.4f}"])
            n_samples += samples_per_hole
            pos = 0.0
            while pos < depth:
                step = min(depth - pos, float(rng.integers(5, 40)))
                wl.writerow([name, f"{pos:.2f}", f"{pos + step:.2f}", LITHO[int(rng.integers(len(LITHO)))]])
                pos += step
    return {"holes": holes, "samples": n_samples}


def arrays(
    holes: int, samples_per_hole: int, *, fields: int = 10, litho_per_hole: int = 20, seed: int = 1
) -> dict[str, Any]:
    """The set as arrays: `fields` fields of the samples — `fields − 2` grades (`GRADES`, then G9…), DENSITY
    and the text ROCK (the rock at the middle of the sample). Keys:
      names, x, y, z, td, depth, clustered   per hole (x, y, z NaN — no collar; td NaN — no TD);
      s_hole, s_depth, s_az, s_dip           survey stations every 50 m (hole — the index of the hole);
      a_hole, a_from, a_to, grades {name: float64}, density, rock (object: str | None)   samples, by hole and depth;
      l_hole, l_from, l_to, l_rock           lithology: `litho_per_hole` intervals a hole, contacts at 0.1 m."""
    if fields < 3 or samples_per_hole < 2 or litho_per_hole < 1 or 10 * samples_per_hole <= litho_per_hole:
        raise ValueError("fields >= 3, samples_per_hole >= 2, litho_per_hole < 10 * samples_per_hole")
    rng = np.random.default_rng(seed)
    h = np.arange(holes)
    side = int(np.ceil(np.sqrt(holes)))
    depth = np.full(holes, float(samples_per_hole))  # 1 m samples on average
    x = X0 + (h % side) * GRID_M + rng.normal(0, 2, holes)
    y = Y0 + (h // side) * GRID_M + rng.normal(0, 2, holes)
    # infill drilling: every fifth hole stands in a tight cluster, where the grades are three times the usual
    clustered = rng.random(holes) < 0.2
    n_clusters = max(1, holes // 500)
    cx = X0 + rng.uniform(0.1, 0.9, n_clusters) * side * GRID_M
    cy = Y0 + rng.uniform(0.1, 0.9, n_clusters) * side * GRID_M
    which = rng.integers(0, n_clusters, holes)
    x = np.where(clustered, cx[which] + rng.normal(0, 15, holes), x)
    y = np.where(clustered, cy[which] + rng.normal(0, 15, holes), y)
    z = 300.0 + 20.0 * np.sin((x - X0) / 400.0) + rng.normal(0, 5, holes)
    td = depth.copy()
    td[h % 53 == 7] += 3.0  # an unsampled tail
    td[h % 101 == 3] = np.nan  # no TD: the bottom of the last sample
    no_collar = h % 211 == 5
    x, y, z = (np.where(no_collar, np.nan, a) for a in (x, y, z))

    flat = h % 97 == 11
    az0 = rng.uniform(0, 360, holes)
    dip0 = np.where(flat, rng.uniform(-20, -5, holes), rng.uniform(-90, -55, holes))
    n_st = int(samples_per_hole // SURVEY_M) + 1
    s_hole = np.repeat(h[h % 50 != 9], n_st)  # every fiftieth hole has no survey
    s_depth = np.tile(np.arange(n_st) * SURVEY_M, len(s_hole) // n_st)
    s_az = (az0[s_hole] + s_depth * 0.02) % 360.0
    s_dip = np.minimum(np.where(flat[s_hole], -3.0, -45.0), dip0[s_hole] + s_depth * 0.01)

    k = litho_per_hole  # contacts: distinct multiples of 0.1 m inside the hole
    grid = 10 * samples_per_hole
    cuts = np.sort(rng.integers(1, grid - (k - 1), size=(holes, k - 1)), axis=1) + np.arange(k - 1)
    edges = np.concatenate([np.zeros((holes, 1)), cuts / 10.0, depth[:, None]], axis=1)
    l_code = rng.integers(0, len(LITHO), size=(holes, k))
    l_code[rng.random((holes, k)) < 0.02] = -1  # no rock written
    has_litho = h % 37 != 4
    rocks = np.array([*LITHO, None], dtype=object)

    n = holes * samples_per_hole
    a_hole = np.repeat(h, samples_per_hole)
    i = np.tile(np.arange(samples_per_hole), holes)
    jitter = rng.integers(-6, 7, n) * 0.05  # of the top of a sample; the first one starts at 0
    jitter[i == 0] = 0.0
    top = np.round(i + jitter, 2)
    a_to = np.round(np.where(i == samples_per_hole - 1, float(samples_per_hole), np.roll(top, -1)), 2)
    gap = (rng.random(n) < 0.02) & (i > 0)  # core not sampled between two samples
    a_from = np.round(top + np.where(gap, 0.25, 0.0), 2)
    mid = (a_from + a_to) / 2.0
    apart = 2.0 * samples_per_hole  # holes one after another on one axis: the bottoms of all the intervals ascend
    bottoms = (edges[:, 1:] + h[:, None] * apart).ravel()
    part = (np.searchsorted(bottoms, mid + a_hole * apart, side="right") - a_hole * k).clip(0, k - 1)
    code = np.where(has_litho[a_hole], l_code[a_hole, part], -1)
    richness = np.where(clustered[a_hole], 3.0, 1.0) * np.append(LITHO_GRADE, 1.0)[code]

    names = [*GRADES, *(f"G{j + 1}" for j in range(len(GRADES), fields - 2))][: fields - 2]
    unassayed = (rng.random(holes) < 0.01)[a_hole] & (i >= 40) & (i < 60)  # a run of 20 samples without assays
    grades: dict[str, np.ndarray] = {}
    for j, name in enumerate(names):
        g = rng.lognormal(-1.0 - 0.4 * j, 1.2 - 0.05 * j, n)
        if j == 0:
            g *= richness
            g[rng.random(n) < 0.0005] *= 50.0  # nuggets
        elif j == 1:
            g = grades[names[0]] * 3.1 * rng.lognormal(0.0, 0.3, n)
        g = np.round(g, 3)
        g[g < 0.01] = 0.005  # below the detection limit: half of it, thousands of equal values
        g[(rng.random(n) < 0.01) | unassayed] = np.nan
        grades[name] = g
    density = np.round(2.7 + 0.1 * (code % 3) + rng.normal(0, 0.15, n), 2)
    density[rng.random(n) < 0.03] = np.nan

    lh = np.repeat(h[has_litho], k)
    return {
        "names": [f"СКВ-{j + 1:05d}" for j in range(holes)],
        "x": x, "y": y, "z": z, "td": td, "depth": depth, "clustered": clustered,
        "s_hole": s_hole, "s_depth": s_depth, "s_az": s_az, "s_dip": s_dip,
        "a_hole": a_hole, "a_from": a_from, "a_to": a_to, "grades": grades, "density": density, "rock": rocks[code],
        "l_hole": lh, "l_from": edges[has_litho, :-1].ravel(), "l_to": edges[has_litho, 1:].ravel(),
        "l_rock": rocks[l_code[has_litho].ravel()],
    }  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate synthetic drillholes (CSV)")
    ap.add_argument("--holes", type=int, default=1000)
    ap.add_argument("--samples-per-hole", type=int, default=100)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    print(generate(a.out, a.holes, a.samples_per_hole, a.seed))


if __name__ == "__main__":
    main()
