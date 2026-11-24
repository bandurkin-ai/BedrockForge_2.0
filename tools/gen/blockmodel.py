"""Synthetic regular block model to Parquet (zstd), rows in Morton (IJK) order (ТЗ 6.5)."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
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


def sub_model(
    n_rows: int, div: tuple[int, int, int] = (4, 4, 2), sub_share: float = 0.25, size=(10.0, 10.0, 5.0)
) -> dict:
    """The grid (meta v2 form) of a synthetic model of `n_rows` rows, `sub_share` of the parents split into 8
    octants (the rest whole); every parent is occupied, P = 0 … parents − 1."""
    parents = int(np.ceil(n_rows / (1.0 + 7.0 * sub_share)))
    nz = 50
    nxy = int(np.ceil(np.sqrt(parents / nz)))
    return {"corner": [500_000.0, 7_000_000.0, -200.0], "counts": [nxy, nxy, nz], "size": list(size),
            "order": "F", "rotation": 0.0, "div": list(div)}  # fmt: skip


def sub_chunks(
    n_rows: int,
    div: tuple[int, int, int] = (4, 4, 2),
    sub_share: float = 0.25,
    chunk_parents: int = 1 << 18,
    seed: int = 1,
    fields: int = 2,
) -> Iterator[tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, np.ndarray]]]:
    """Rows (P uint64, s uint8 n×3, w uint8 n×3, {field: float64}) of the model `sub_model` in file order
    (P, sz, sy, sx), by portions of about 4·`chunk_parents` rows, until `n_rows` rows are given. A split parent
    is eight octants (s ∈ {0, div/2}); the share of split parents is `sub_share`. Fields: AU, DENSITY, … ."""
    rng = np.random.default_rng(seed)
    half = np.asarray(div, dtype=np.uint8) // 2
    octant = np.array([[o & 1, (o >> 1) & 1, (o >> 2) & 1] for o in range(8)], dtype=np.uint8)
    whole_w = np.asarray(div, dtype=np.uint8)
    given = first = 0
    while given < n_rows:
        split = rng.random(chunk_parents) < sub_share
        count = np.where(split, 8, 1)
        total = int(count.sum())
        if given + total > n_rows:  # the last portion: whole parents only, up to n_rows rows
            keep = int(np.searchsorted(np.cumsum(count), n_rows - given, side="right"))
            split, count = split[:keep], count[:keep]
            total = int(count.sum())
            if not total:
                break
        p = np.repeat(np.arange(first, first + len(count), dtype=np.uint64), count)
        s = np.zeros((total, 3), dtype=np.uint8)
        w = np.broadcast_to(whole_w, (total, 3)).copy()
        rows = np.flatnonzero(np.repeat(split, count))
        k = np.arange(len(rows)) % 8
        s[rows] = octant[k] * half
        w[rows] = half
        values = {"AU": rng.lognormal(-1.0, 1.0, total), "DENSITY": rng.normal(2.7, 0.05, total)}
        for extra in range(2, fields):
            values[f"F{extra}"] = rng.random(total)
        first += len(count)
        given += total
        yield p, s, w, values


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
