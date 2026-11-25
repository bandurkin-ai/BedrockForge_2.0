"""The realizations of a simulation: the blob `real` (ADR 0024 §2; подшаг 18.32; docs/progress/e18-api.md §9).

A Parquet file (zstd), a row — a target (P, D) of the regular model in the order of the model (P ascending): `p` u64 —
the parent, `dom` i2 — the code of the domain of the target (0 — the domain of the row of the simulation; the codes of
more domains are for the later kinds), `r001 … rNNN` — the realizations: f4 (SGS, co-SGS, SIS with thresholds) or u1
(SIS categories, the codes of the labels). The row groups hold `GROUP` rows, so a reader takes a part of the nodes
without the whole file; NaN is never written (a node always has a value).

    staged = write(store, p, dom, real [n_real, n] f8, dtype="f4")        (path, sha256, size): the file is sealed
    r = Reals(store, sha)    r.n, r.n_real, r.columns
    r.p(), r.dom()           the whole columns
    r.rows(lo, hi)           f8 [n_real, hi − lo] of the nodes [lo, hi) (reads the row groups it meets)
    summary(r)               per realization: n, mean, std, min, max (`Chan` merge over the row groups)
    column_names(n_real)     ["p", "dom", "r001", …]

The numbers of the realizations are stored as f4 (the contract): `post` and the summary read them back as f8.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from bf_core.api.errors import ApiError

if TYPE_CHECKING:
    from bf_core.storage.blobs import BlobStore

GROUP = 1 << 17  # rows of a row group


def column_names(n_real: int) -> list[str]:
    return ["p", "dom", *[f"r{k + 1:03d}" for k in range(int(n_real))]]


def broken(what: str = "Файл реализаций повреждён или не найден.") -> ApiError:
    return ApiError(409, "real_broken", what, "Запустите моделирование заново.")


def write(store: BlobStore, p: np.ndarray, dom: np.ndarray, real: np.ndarray, dtype: str = "f4",
          group: int = GROUP) -> tuple[Path, str, int]:  # fmt: skip
    """The file of the realizations `real` [n_real, n] (f8, the nodes in the order of `p`) → (temp path, SHA-256, size).
    The caller commits it in its transaction (`store.commit_staged`) or deletes it. A value that the type cannot hold
    (non-finite after the cast, a code off 0–255) is refused: 500 `sim_nan`."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from bf_core.blocks.fields_store import seal

    n_real, n = int(real.shape[0]), int(real.shape[1])
    if p.shape[0] != n or dom.shape[0] != n:
        raise ApiError(500, "sim_rows", "Число строк реализаций не совпадает с числом узлов.", "Сообщите разработчику.")
    kind = np.float32 if dtype == "f4" else np.uint8
    atype = pa.float32() if dtype == "f4" else pa.uint8()
    names = column_names(n_real)
    schema = pa.schema([("p", pa.uint64()), ("dom", pa.int16()), *[(c, atype) for c in names[2:]]])
    store.tmp_dir.mkdir(parents=True, exist_ok=True)
    path = store.tmp_dir / uuid.uuid4().hex
    writer = pq.ParquetWriter(path, schema, compression="zstd")
    try:
        for a in range(0, max(n, 1), group):
            b = min(a + group, n)
            part = np.ascontiguousarray(real[:, a:b], dtype=np.float64)
            with np.errstate(over="ignore", invalid="ignore"):
                cast = part.astype(kind)
            if dtype == "f4" and not bool(np.isfinite(cast).all()):
                raise ApiError(500, "sim_nan", "Реализация содержит значение вне диапазона f4.",
                               "Сообщите разработчику.")  # fmt: skip
            arrays = [pa.array(p[a:b].astype(np.uint64), type=pa.uint64()), pa.array(dom[a:b].astype(np.int16))]
            arrays += [pa.array(cast[k], type=atype) for k in range(n_real)]
            writer.write_table(pa.Table.from_arrays(arrays, schema=schema))
        writer.close()
        sha, size = seal(path)
    except BaseException:
        writer.close()
        path.unlink(missing_ok=True)
        raise
    return path, sha, size


class Reals:
    """A file of realizations opened for reading by row groups. Light: nothing is read before it is asked."""

    def __init__(self, store: BlobStore, sha: str) -> None:
        import pyarrow.parquet as pq

        self.store, self.sha = store, sha
        path = store.path_of(sha)
        if not path.is_file():
            raise broken()
        self._path = path
        meta = pq.ParquetFile(path).metadata
        self.columns = [meta.schema.column(i).name for i in range(meta.num_columns)]
        if self.columns[:2] != ["p", "dom"] or len(self.columns) < 3:
            raise broken()
        self.n_real = len(self.columns) - 2
        self.n = int(meta.num_rows)
        sizes = [int(meta.row_group(g).num_rows) for g in range(meta.num_row_groups)]
        self.starts = np.concatenate(([0], np.cumsum(sizes))).astype(np.int64)
        self._cache: tuple[int, np.ndarray] | None = None

    def _file(self) -> Any:
        import pyarrow.parquet as pq

        return pq.ParquetFile(self._path)

    def column(self, name: str) -> np.ndarray:
        import pyarrow.parquet as pq

        return np.asarray(pq.read_table(self._path, columns=[name]).column(0).to_numpy())

    def p(self) -> np.ndarray:
        return self.column("p").astype(np.uint64, copy=False)

    def dom(self) -> np.ndarray:
        return self.column("dom").astype(np.int16, copy=False)

    def group(self, g: int) -> np.ndarray:
        """The realizations of the row group `g`: f8 [n_real, rows] (the last one is cached: a chunk of the model
        asks the groups it meets one after another)."""
        if self._cache is not None and self._cache[0] == g:
            return self._cache[1]
        t = self._file().read_row_group(g, columns=self.columns[2:])
        out = np.empty((self.n_real, t.num_rows), dtype=np.float64)
        for k in range(self.n_real):
            out[k] = t.column(k).to_numpy()
        self._cache = (g, out)
        return out

    def rows(self, lo: int, hi: int) -> np.ndarray:
        """f8 [n_real, hi − lo]: the nodes [lo, hi) of every realization."""
        lo, hi = max(int(lo), 0), min(int(hi), self.n)
        if hi <= lo:
            return np.empty((self.n_real, 0), dtype=np.float64)
        first = int(np.searchsorted(self.starts, lo, side="right")) - 1
        parts = []
        g = first
        while g < len(self.starts) - 1 and self.starts[g] < hi:
            a, b = int(self.starts[g]), int(self.starts[g + 1])
            parts.append(self.group(g)[:, max(lo - a, 0) : min(hi, b) - a])
            g += 1
        return parts[0] if len(parts) == 1 else np.concatenate(parts, axis=1)


def summary(reals: Reals) -> dict[str, Any]:
    """Per realization `{n, mean, std (ddof 0), min, max}` and the same over all of them, from the row groups (the mean
    and the variance of the parts are merged by the formula of Chan, so the memory is a row group)."""
    k = reals.n_real
    n = np.zeros(k)
    mean = np.zeros(k)
    m2 = np.zeros(k)
    lo = np.full(k, np.inf)
    hi = np.full(k, -np.inf)
    for g in range(len(reals.starts) - 1):
        x = reals.group(g)
        m = x.shape[1]
        if m == 0:
            continue
        gm = x.mean(axis=1)
        g2 = ((x - gm[:, None]) ** 2).sum(axis=1)
        tot = n + m
        delta = gm - mean
        m2 = m2 + g2 + delta * delta * n * m / tot
        mean = mean + delta * m / tot
        n = tot
        lo = np.minimum(lo, x.min(axis=1))
        hi = np.maximum(hi, x.max(axis=1))
    return _pack(n, mean, np.sqrt(m2 / np.maximum(n, 1.0)), lo, hi)


def stats_of(real: np.ndarray) -> dict[str, Any]:
    """The same summary of the realizations in the memory `[n_real, n]` (what the job keeps in `last_run.summary`)."""
    k, n = int(real.shape[0]), int(real.shape[1])
    if n == 0:
        return _pack(np.zeros(k), np.zeros(k), np.zeros(k), np.zeros(k), np.zeros(k))
    return _pack(np.full(k, float(n)), real.mean(axis=1), real.std(axis=1), real.min(axis=1), real.max(axis=1))


def _pack(n: np.ndarray, mean: np.ndarray, std: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> dict[str, Any]:
    per = [{"n": int(n[r]), "mean": float(mean[r]), "std": float(std[r]), "min": float(lo[r]), "max": float(hi[r])}
           for r in range(int(n.shape[0]))]  # fmt: skip
    if not float(n.sum()):
        return {"per": per, "all": None}
    return {"per": per, "all": {"mean": float(mean.mean()), "std": float(std.mean()), "min": float(lo.min()),
                                "max": float(hi.max())}}  # fmt: skip
