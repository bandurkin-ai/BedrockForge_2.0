"""Synthetic block model (meta v2 files) and the time of reading its columns (Э17, 17.4; ADR 0023 «Замеры»).

    cd core && PYTHONPATH=. uv run python ../tools/gen/blockmodel_read.py --rows 20000000 --workdir /tmp/bm_read

Writes `cells` (idx uint64, AU f8, CU f4, DOM int16 category) and a file of fields `fld:` (CALC f8) in row groups of
2²⁰ rows, as the writers of 17.1/17.3 do, then times: `Reader.fill` of 4 columns (+ P), `Model.iter` over them,
`stats.blocks_cache.columns` (cold: Parquet; written to the disk cache; warm disk: the memory empty; warm memory).
No project: the files lie in a store of the work folder. Stand-alone — it does not touch `tools/gen/blockmodel.py`.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

GROUP = 1 << 20


def write(path: Path, rows: int, fld: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    rng = np.random.default_rng(1)
    cells = pa.schema([("idx", pa.uint64()), ("AU", pa.float64()), ("CU", pa.float32()), ("DOM", pa.int16())])
    extra = pa.schema([("CALC", pa.float64())])
    with pq.ParquetWriter(path, cells, compression="zstd") as a, pq.ParquetWriter(fld, extra, compression="zstd") as b:
        for lo in range(0, rows, GROUP):
            n = min(GROUP, rows - lo)
            au = rng.lognormal(0.0, 1.0, n)
            au[rng.random(n) < 0.05] = np.nan
            dom = pa.array(rng.integers(0, 5, n).astype(np.int16), mask=rng.random(n) < 0.02)
            a.write_table(
                pa.table(
                    [
                        pa.array(np.arange(lo, lo + n, dtype=np.uint64)),
                        pa.array(au),
                        pa.array(rng.random(n).astype(np.float32)),
                        dom,
                    ],
                    schema=cells,
                )
            )
            b.write_table(pa.table([pa.array(au * 2.0)], schema=extra))


def timed(label: str, call):  # type: ignore[no-untyped-def]
    t = time.perf_counter()
    out = call()
    print(f"{label:<44}{time.perf_counter() - t:8.2f} s")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Read time of the columns of a synthetic block model v2")
    ap.add_argument("--rows", type=int, default=20_000_000)
    ap.add_argument("--workdir", type=Path, required=True)
    a = ap.parse_args()
    from bf_core.blocks.model import Reader
    from bf_core.stats import blocks_cache as bc
    from bf_core.storage.blobs import BlobStore

    folder = a.workdir
    shutil.rmtree(folder, ignore_errors=True)
    store = BlobStore(folder)
    shas = {"cells": hashlib.sha256(b"cells").hexdigest(), "fld:aaaaaaaa": hashlib.sha256(b"fld").hexdigest()}
    for sha in shas.values():
        store.path_of(sha).parent.mkdir(parents=True, exist_ok=True)
    timed(
        f"write {a.rows:,} rows (cells + fld)",
        lambda: write(store.path_of(shas["cells"]), a.rows, store.path_of(shas["fld:aaaaaaaa"])),
    )
    fields = [{"name": "AU", "kind": "number", "dtype": "f8", "file": "cells"},
              {"name": "CU", "kind": "number", "dtype": "f4", "file": "cells"},
              {"name": "DOM", "kind": "category", "dtype": "cat", "file": "cells", "labels": list("abcde")},
              {"name": "CALC", "kind": "number", "dtype": "f8", "file": "fld:aaaaaaaa"}]  # fmt: skip
    names = [f["name"] for f in fields]
    reader = Reader(store, shas, fields, a.rows)
    got = timed("Reader.fill: 4 columns + P", lambda: reader.fill(names, p=True))
    del got

    def portions() -> int:
        n = 0
        for _off, k, *_rest in reader.pieces(names, p=True):
            n += k
        return n

    timed("Reader.pieces: the same by row groups", portions)
    side = int(round(a.rows ** (1 / 3))) + 1
    grid = {"corner": [0.0, 0.0, 0.0], "counts": [side, side, side], "size": [1.0, 1.0, 1.0]}
    model = bc.Model(1, "gen", grid, fields, shas["cells"], a.rows, shas)
    project = SimpleNamespace(uid="gen", folder=folder)
    bc.set_limit(1200)  # 20 million rows × 4 columns + idx: 640 МБ, over the default 600
    bc.MODELS.clear()
    timed("blocks_cache.columns cold (Parquet + disk write)", lambda: bc.columns(project, model, names, disk=True))
    timed("blocks_cache.columns memory", lambda: bc.columns(project, model, names, disk=True))
    bc.MODELS.clear()
    timed("blocks_cache.columns disk (memory empty)", lambda: bc.columns(project, model, names, disk=True))
    bc.MODELS.clear()
    timed("blocks_cache.columns Parquet again (no disk)", lambda: bc.columns(project, model, names, disk=False))
    print(f"cache {bc.MODELS.size / 2**20:.0f} MB, reads {bc.MODELS.reads}, loads {bc.MODELS.loads}")


if __name__ == "__main__":
    main()
