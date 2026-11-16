"""Synthetic drillhole set: collars, surveys, assays, lithology (CSV), reproducible by seed.

Volumes from ТЗ 5.3: up to 50 000 holes / 5 million samples. Values include missing data and
detection limits on purpose (`-999`, empty, `<0.01`) so that importers must report them.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np

LITHO = ["ПЕС", "АЛВ", "ГРН", "КВЦ", "СЛН", "ДИО"]


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
