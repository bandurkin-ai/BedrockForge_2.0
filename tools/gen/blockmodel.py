"""Synthetic regular block model to Parquet (zstd), rows in Morton (IJK) order (ТЗ 6.5)."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np


def morton3(i: np.ndarray, j: np.ndarray, k: np.ndarray) -> np.ndarray:
    def spread(v: np.ndarray) -> np.ndarray:
        v = v.astype(np.uint64) & np.uint64(0x1FFFFF)
        v = (v | (v << np.uint64(32))) & np.uint64(0x1F00000000FFFF)
        v = (v | (v << np.uint64(16))) & np.uint64(0x1F0000FF0000FF)
        v = (v | (v << np.uint64(8))) & np.uint64(0x100F00F00F00F00F)
        v = (v | (v << np.uint64(4))) & np.uint64(0x10C30C30C30C30C3)
        v = (v | (v << np.uint64(2))) & np.uint64(0x1249249249249249)
        return v

    return spread(i) | (spread(j) << np.uint64(1)) | (spread(k) << np.uint64(2))


def generate(out: Path, nx: int, ny: int, nz: int, size=(10.0, 10.0, 5.0), seed: int = 1) -> int:
    import pyarrow as pa
    import pyarrow.parquet as pq

    rng = np.random.default_rng(seed)
    k, j, i = np.meshgrid(np.arange(nz), np.arange(ny), np.arange(nx), indexing="ij")
    i, j, k = i.ravel(), j.ravel(), k.ravel()
    order = np.argsort(morton3(i, j, k), kind="stable")
    i, j, k = i[order], j[order], k[order]
    n = i.size
    table = pa.table({
        "XC": 500_000.0 + (i + 0.5) * size[0],
        "YC": 7_000_000.0 + (j + 0.5) * size[1],
        "ZC": -200.0 + (k + 0.5) * size[2],
        "AU": rng.lognormal(-1.0, 1.0, n),
        "DENSITY": rng.normal(2.7, 0.05, n),
    })
    pq.write_table(table, out, compression="zstd", row_group_size=1_000_000)
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate a synthetic block model (Parquet)")
    ap.add_argument("--nx", type=int, default=100)
    ap.add_argument("--ny", type=int, default=100)
    ap.add_argument("--nz", type=int, default=50)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    print(f"{a.out}: {generate(a.out, a.nx, a.ny, a.nz)} blocks")


if __name__ == "__main__":
    main()
