"""The cross-validation of a row of the estimation plan (ТЗ 8.8.1; ADR 0024 §8; e18-api.md §8.1; Э18, 18.14).

    t = cross_validate(spec, samples, by="hole", threads=None, tick=None)   # XvalTable: one row per sample of D
    s = summary(true, est, kv)                                              # the figures of ТЗ 8.8.1
    out = run_xval(engine, folder, params, tick)                            # the job `estimate.xval`: the answer
    page = read_page(conn, store, estimation_id, page, size)                # `GET /estimations/{id}/xval`

The same kernel as the estimation (`kernel.estimate` with the mask of the exclusion), the same `origin`, the same
passes; the target is the position of the sample, in a point (`disc` does not apply — the estimate of a point; a
sample is a point). `by="sample"` leaves out the sample itself, `by="hole"` (the default) — all the samples of its hole
(also those of S, the soft boundary): the bits are those of the row estimated at the same place from `samples.take`
of the rest (Т8.8-12; the kernel guarantees it, the tests prove it). The targets are the samples of the domain D;
`true` is the value the estimate works with (the top cut applied, the coincident samples averaged — the number of
the sample `i` is its position in the canonical order, № 76); `est` — EST (NN, IDW, SK, OK) or ETYPE (IK); KV is that
of the kernel, clipped from below at 0 (`kernel.neg_kv`): the ratio error²/KV does not take a row with KV = 0 (no
division: the row is counted in `n_kv_zero`); NN, IDW, IK have no KV — `kv`, `z_error`, `mean_ratio` are empty (null).
A row whose STATUS is not 0 is in the table (the estimate is empty) and out of the summary.

The table is the blob `xval` of the row (Parquet, the columns `COLUMNS`; the summary, the histogram, `by`, `at` and the
hash of the parameters are in the metadata of the file): written last, whole, in one transaction that drops the blob
of the previous calculation; a cancel or a failure before it leaves nothing (the old table stays).

The summary (n — the rows that took part, E = est − true; the divisor of the dispersion is n):
    mean_error = mean E;  std_error = sqrt(mean (E − mean E)²);  mae = mean |E|;  r — Pearson of (true, est);
    slope = cov(true, est) / var(est) — the regression of the true on the estimate (1 — no conditional bias);
    mean_ratio = mean E²/KV over the rows with KV > 0.
An empty value (n = 0; a constant sample: r, slope; no KV) is null, never 0 (8.0.1).
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import blob_owners
from bf_core.jobs.context import JobContext, JobError
from bf_core.jobs.registry import handler
from bf_core.storage.blobs import BlobStore

from . import store_base as sb
from .samples import D, RowSamples
from .spec import EstimationSpec
from .spec_parts import ALLOWED, Outputs
from .store_check import freshness

if TYPE_CHECKING:  # numba stays out of the process of the API, which only registers the handler (№ 18.16)
    from . import kernel

KIND = "estimate.xval"
TITLE = "Перекрёстная проверка"
STAGE_SAMPLES, STAGE_XVAL, STAGE_WRITE = "Пробы", "Перекрёстная проверка", "Запись"
BY = ("hole", "sample")
CHUNK = 4096  # targets per portion of the kernel (the cancel is checked between them)
ROW_GROUP = 32768
PAGE_MAX = 5000
BINS = 40
META_KEY = b"bf.xval"
PARQUET_MEDIA = "application/vnd.apache.parquet"
COLUMNS = ("i", "row_id", "hole", "from", "to", "x", "y", "z", "true", "est", "error", "kv", "z_error", "pass", "ns",
           "status")  # fmt: skip
_INT = {"i": "int64", "row_id": "int64", "pass": "int16", "ns": "int16", "status": "int8"}
Tick = Callable[[float | None, str | None], None]


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    return None


def _bad(param: str, message: str, hint: str) -> ApiError:
    return ApiError(422, "bad_param", message, hint, {"param": param})


# --- the summary ------------------------------------------------------------------------------------------------------


def _num(x: float) -> float | None:
    return float(x) if math.isfinite(x) else None


def summary(true: np.ndarray, est: np.ndarray, kv: np.ndarray | None = None) -> dict[str, Any]:
    """The figures of the module docstring from the arrays of the table (NaN — no value: such a row does not take
    part). `n_skipped` — the rows left out, `n_ratio` — the rows of `mean_ratio`, `n_kv_zero` — the rows with a KV that
    is 0 (the ratio is not defined; a negative one is clipped to 0 by the kernel)."""
    t = np.asarray(true, dtype=np.float64).ravel()
    e = np.asarray(est, dtype=np.float64).ravel()
    ok = np.isfinite(t) & np.isfinite(e)
    n = int(ok.sum())
    out: dict[str, Any] = {"n": n, "mean_error": None, "std_error": None, "mae": None, "r": None, "slope": None,
                           "mean_ratio": None, "n_ratio": 0, "n_kv_zero": 0, "n_skipped": int(t.size - n)}  # fmt: skip
    if n == 0:
        return out
    t, e = t[ok], e[ok]
    err = e - t
    mean = float(err.mean())
    out["mean_error"] = _num(mean)
    out["std_error"] = _num(math.sqrt(float(np.mean((err - mean) ** 2))))
    out["mae"] = _num(float(np.abs(err).mean()))
    dt, de = t - t.mean(), e - e.mean()
    var_t, var_e, cov = float(np.mean(dt * dt)), float(np.mean(de * de)), float(np.mean(dt * de))
    if var_e > 0.0:
        out["slope"] = _num(cov / var_e)
        if var_t > 0.0 and n > 1:
            out["r"] = _num(cov / math.sqrt(var_t * var_e))
    if kv is not None:
        k = np.asarray(kv, dtype=np.float64).ravel()[ok]
        pos = np.isfinite(k) & (k > 0.0)
        out["n_kv_zero"] = int((np.isfinite(k) & (k <= 0.0)).sum())
        out["n_ratio"] = int(pos.sum())
        if pos.any():
            out["mean_ratio"] = _num(float(np.mean(err[pos] ** 2 / k[pos])))
    return out


def histogram(error: np.ndarray, bins: int = BINS) -> dict[str, list[float] | list[int]]:
    """{edges [b + 1], counts [b]} of the finite errors (empty when there are none); a constant error — one bin of
    unit width around it."""
    e = np.asarray(error, dtype=np.float64)
    e = e[np.isfinite(e)]
    if e.size == 0:
        return {"edges": [], "counts": []}
    lo, hi = float(e.min()), float(e.max())
    if lo == hi:
        lo, hi = lo - 0.5, hi + 0.5
    counts, edges = np.histogram(e, bins=bins, range=(lo, hi))
    return {"edges": [float(v) for v in edges], "counts": [int(c) for c in counts]}


# --- the calculation --------------------------------------------------------------------------------------------------


@dataclass
class XvalTable:
    """One row per sample of D, in the canonical order of the samples; NaN — none."""

    by: str
    i: np.ndarray  # i8: the number of the sample (its position in `RowSamples`)
    row_id: np.ndarray  # i8
    hole: np.ndarray  # i4: dense index into `holes`
    holes: list[str]
    span: np.ndarray  # f8 [n, 2]: FROM, TO
    xyz: np.ndarray  # f8 [n, 3]: the project's coordinates
    true: np.ndarray
    est: np.ndarray
    kv: np.ndarray
    ns: np.ndarray  # f8: the samples of the pass that estimated
    pass_: np.ndarray  # f8: the pass, from 1
    status: np.ndarray  # i1: 0 — estimated; 1–3 — Д-27
    neg_kv: int = 0

    @property
    def n(self) -> int:
        return int(self.i.shape[0])

    @property
    def error(self) -> np.ndarray:
        return self.est - self.true

    @property
    def z_error(self) -> np.ndarray:
        """error / sqrt(KV); NaN where KV is not above 0 (no division by 0)."""
        out = np.full(self.n, math.nan)
        pos = np.isfinite(self.kv) & (self.kv > 0.0)
        out[pos] = self.error[pos] / np.sqrt(self.kv[pos])
        return out

    def summary(self) -> dict[str, Any]:
        ok = self.status == 0
        s = summary(self.true[ok], self.est[ok], self.kv[ok])
        s["by_status"] = [int((self.status == c).sum()) for c in range(4)]
        return s


def xval_spec(spec: EstimationSpec) -> EstimationSpec:
    """The row for the cross-validation: a point (`disc` 1×1×1) and the outputs the table needs (EST or ETYPE, KV,
    NS, PASS, whatever the user chose to write; the method gives what it has)."""
    allowed = ALLOWED[spec.method.kind]
    need = tuple(k for k in ("STATUS", "EST", "ETYPE", "KV", "NS", "PASS") if k in allowed)
    return replace(spec, disc=(1, 1, 1), outputs=Outputs(spec.outputs.prefix, need))


def _col(res: kernel.ChunkResult, key: str, m: int) -> np.ndarray:
    got = res.values.get(key)
    return np.full(m, math.nan) if got is None else got


def cross_validate(spec: EstimationSpec, samples: RowSamples, by: str = "hole", *, threads: int | None = None,
                   chunk: int | None = None, tick: Tick | None = None,
                   k_c: int | None = None) -> XvalTable:  # fmt: skip
    """The table of the cross-validation of a row (module docstring). `spec` — the row as stored (the search
    resolved); `tick(None, None)` is the check of the cancel (raises `JobCancelled`), `tick(p, text)` — the progress
    of the stage; `k_c` — the first number of candidates of the tree (tests)."""
    from bf_core.composites import strips

    from . import kernel

    if by not in BY:
        raise _bad("by", f"Перекрёстная проверка: неизвестный режим «{str(by)[:20]}».", "Режимы: hole, sample.")
    beat = tick or _noop
    rk = kernel.compile_row(xval_spec(spec), samples, k_c=k_c)
    target = np.flatnonzero(samples.dom == D).astype(np.int64)
    n = int(target.size)
    if by == "hole":
        key: tuple[str, np.ndarray] = ("hole", samples.hole.astype(np.int64))
    else:
        key = ("sample", np.arange(samples.n, dtype=np.int64))
    est = np.full(n, math.nan)
    kv = np.full(n, math.nan)
    ns = np.full(n, math.nan)
    pass_ = np.full(n, math.nan)
    status = np.full(n, kernel.ST_OUT, dtype=np.int8)
    ik = rk.kind == "ik"
    size = int(chunk or CHUNK)
    parts = [target[a : a + size] for a in range(0, n, size)]
    portions = (kernel.Portion(samples.local[p], exclude=(key[0], key[1][p])) for p in parts)
    neg = 0
    at = 0
    for p, res in zip(parts, kernel.run(rk, portions, threads=strips.default_threads() if threads is None else threads,
                                        tick=beat, local=True), strict=False):  # fmt: skip
        m = p.size
        sl = slice(at, at + m)
        est[sl] = _col(res, "ETYPE" if ik else "EST", m)
        kv[sl] = _col(res, "KV", m)
        ns[sl] = _col(res, "NS", m)
        pass_[sl] = _col(res, "PASS", m)
        status[sl] = res.status
        neg += res.neg_kv
        at += m
        beat(at / max(n, 1), STAGE_XVAL)
    return XvalTable(by, target, samples.row_id[target], samples.hole[target], list(samples.holes),
                     samples.span[target], samples.xyz[target], samples.z[target], est, kv, ns, pass_, status,
                     neg)  # fmt: skip


# --- the file ---------------------------------------------------------------------------------------------------------


def _arrow(t: XvalTable) -> Any:
    import pyarrow as pa

    names = np.asarray(t.holes, dtype=object)

    def floats(a: np.ndarray) -> Any:
        return pa.array(a, type=pa.float64(), mask=~np.isfinite(a))

    def ints(a: np.ndarray, kind: str) -> Any:
        ok = np.isfinite(a)
        return pa.array(np.where(ok, a, 0).astype(kind), type=getattr(pa, kind)(), mask=~ok)

    cols = {
        "i": pa.array(t.i, type=pa.int64()), "row_id": pa.array(t.row_id, type=pa.int64()),
        "hole": pa.array(names[t.hole].tolist() if t.n else [], type=pa.string()),
        "from": floats(t.span[:, 0]), "to": floats(t.span[:, 1]),
        "x": floats(t.xyz[:, 0]), "y": floats(t.xyz[:, 1]), "z": floats(t.xyz[:, 2]),
        "true": floats(t.true), "est": floats(t.est), "error": floats(t.error), "kv": floats(t.kv),
        "z_error": floats(t.z_error), "pass": ints(t.pass_, "int16"), "ns": ints(t.ns, "int16"),
        "status": pa.array(t.status, type=pa.int8()),
    }  # fmt: skip
    return pa.table(cols)


def to_parquet(t: XvalTable, meta: dict[str, Any]) -> bytes:
    """The Parquet file of the table; `meta` (JSON) is in the metadata of the schema under `META_KEY`."""
    import io

    import pyarrow.parquet as pq

    table = _arrow(t)
    text = json.dumps(meta, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    table = table.replace_schema_metadata({META_KEY: text})
    out = io.BytesIO()
    pq.write_table(table, out, row_group_size=ROW_GROUP, compression="zstd")
    return out.getvalue()


def _find_blob(conn: sa.Connection, estimation_id: int) -> str | None:
    q = sa.select(blob_owners.c.sha256).where(blob_owners.c.owner_kind == sb.OWNER,
                                              blob_owners.c.owner_id == estimation_id, blob_owners.c.role == sb.XVAL)
    sha = conn.execute(q).scalar()
    return None if sha is None else str(sha)


def _not_found() -> ApiError:
    return ApiError(404, "xval_not_found", "Перекрёстной проверки для этих параметров нет.",
                    "Выполните перекрёстную проверку.")  # fmt: skip


def _jsonable(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def read_page(conn: sa.Connection, store: BlobStore, estimation_id: int, page: int = 1,
              size: int = 500) -> dict[str, Any]:  # fmt: skip
    """`GET /estimations/{id}/xval` (e18-api §8.1): `{estimation_id, by, at, stale, total, page, pages, size, rows,
    summary, histogram}`; `rows` — the dicts of `COLUMNS` (null — none). 404 `estimation_not_found`, 404
    `xval_not_found` (no calculation), 422 `bad_param {page | size}`. `stale` — the parameters or the inputs of the
    row are not those of the calculation."""
    import pyarrow.parquet as pq

    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise _bad("page", "Номер страницы — целое число от 1.", "Откройте первую страницу.")
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= PAGE_MAX:
        raise _bad("size", f"Размер страницы — от 1 до {PAGE_MAX}.", "Уменьшите размер страницы.")
    row = sb.stored(conn, sb.KIND, estimation_id)
    sha = _find_blob(conn, estimation_id)
    if sha is None or not store.exists(sha):
        raise _not_found()
    pf = pq.ParquetFile(store.path_of(sha))
    raw = (pf.schema_arrow.metadata or {}).get(META_KEY)
    info: dict[str, Any] = json.loads(raw) if raw else {}
    total = int(pf.metadata.num_rows)
    pages = max(1, -(-total // size))
    lo = min((page - 1) * size, total)
    hi = min(lo + size, total)
    got: list[dict[str, Any]] = []
    first = 0
    for g in range(pf.metadata.num_row_groups):
        cnt = int(pf.metadata.row_group(g).num_rows)
        if first + cnt > lo and first < hi:
            sub = pf.read_row_group(g).slice(max(lo - first, 0), min(hi, first + cnt) - max(lo, first))
            cols = {name: sub.column(name).to_pylist() for name in sub.column_names}
            got += [{name: _jsonable(cols[name][k]) for name in sub.column_names} for k in range(sub.num_rows)]
        first += cnt
    meta = sb.lenient(row.meta or {})
    try:
        spec_now = sb.load_spec(meta)
        stale = sb.params_hash(spec_now) != info.get("params") or bool(freshness(conn, row.meta or {})["stale"])
    except ApiError:
        stale = True
    return {"estimation_id": estimation_id, "by": info.get("by"), "at": info.get("at"), "stale": stale,
            "total": total, "page": page, "pages": pages, "size": size, "rows": got,
            "summary": info.get("summary"),
            "histogram": info.get("histogram") or {"edges": [], "counts": []}}  # fmt: skip


# --- the job ----------------------------------------------------------------------------------------------------------


def _parse(params: dict[str, Any]) -> tuple[int, str]:
    ident = params.get("estimation_id")
    if isinstance(ident, bool) or not isinstance(ident, int) or ident < 1:
        raise _bad("estimation_id", "Не указаны параметры оценки для проверки.",
                   "Выберите строку «Параметры оценки».")  # fmt: skip
    by = params.get("by")
    by = "hole" if by is None or by == "" else by
    if by not in BY:
        raise _bad("by", f"Перекрёстная проверка: неизвестный режим «{str(by)[:20]}».", "Режимы: hole, sample.")
    return int(ident), str(by)


def memory_mb(params: dict[str, Any]) -> int:
    """The samples with the trajectories (≈ 40 numbers a row), the tree, the table (16 columns) and the kernel."""
    rows = int(params.get("rows") or 0)
    return max(256, int(rows * 80 * 8 / 2**20) + 192)


def run_xval(engine: sa.Engine, folder: Any, params: dict[str, Any], tick: Tick = _noop,
             threads: int | None = None) -> dict[str, Any]:  # fmt: skip
    """The body of the job `estimate.xval` (the handler, the tests and the bench in the process): `params`
    `{estimation_id, by?}` → `{estimation_id, by, n, summary, events}` (e18-api §8.1); the table is the blob `xval` of
    the row. 404 `estimation_not_found`, 409 `source_lost`, 422 `bad_param`, `no_samples`. Cancel — `JobCancelled`
    out of `tick(None, None)` — leaves the old table as it was."""
    from .samples import load

    folder = Path(folder)
    ident, by = _parse(params)
    beat = tick
    with dialect.read_tx(engine) as conn:
        row = sb.stored(conn, sb.KIND, ident)
        meta = dict(row.meta or {})
        if freshness(conn, meta)["source_lost"]:
            raise ApiError(409, "source_lost", "Источник параметров оценки удалён.",
                           "Восстановите таблицу, модель вариограммы или блочную модель либо удалите строку.",
                           {"param": "estimation_id"})  # fmt: skip
        spec = sb.load_spec(meta)
    if not spec.resolved_search:
        raise ApiError(422, "search_unresolved", "Эллипсоид поиска не определён.",
                       "Откройте параметры оценки и сохраните их заново.", {"param": "search"})  # fmt: skip
    origin = meta.get("origin")
    org = np.asarray(origin, dtype=np.float64) if isinstance(origin, list) and len(origin) == 3 else None
    beat(0.0, STAGE_SAMPLES)
    project = SimpleNamespace(engine=engine, uid=f"job:{folder}", folder=folder)
    samples = load(project, spec, origin=org, cancel=lambda: beat(None, None))
    beat(0.05, STAGE_XVAL)
    table = cross_validate(spec, samples, by, threads=threads,
                           tick=lambda p, m: beat(None if p is None else 0.05 + 0.9 * p, m))  # fmt: skip
    beat(None, None)
    result = table.summary()
    at = dialect.utcnow().isoformat()
    info = {"v": 1, "estimation_id": ident, "by": by, "at": at, "version": int(row.version),
            "params": sb.params_hash(spec), "origin": samples.origin.tolist(), "summary": result,
            "histogram": histogram(table.error[table.status == 0])}  # fmt: skip
    beat(0.97, STAGE_WRITE)
    data = to_parquet(table, info)
    # no cancel from here on: the table goes whole, in one transaction, and the old one with it
    store = BlobStore(folder)
    staged = store.stage_bytes(data)
    try:
        with dialect.write_tx(engine) as conn:
            sb.stored(conn, sb.KIND, ident)
            conn.execute(sa.delete(blob_owners).where(blob_owners.c.owner_kind == sb.OWNER,
                                                      blob_owners.c.owner_id == ident,
                                                      blob_owners.c.role == sb.XVAL))  # fmt: skip
            store.commit_staged(conn, staged, media_type=PARQUET_MEDIA, owner_kind=sb.OWNER, owner_id=ident,
                                role=sb.XVAL)  # fmt: skip
    except BaseException:
        staged[0].unlink(missing_ok=True)
        raise
    return {"estimation_id": ident, "by": by, "n": table.n, "summary": result,
            "events": [["geostat.changed", {"kind": "estimation", "id": ident}]]}  # fmt: skip


@handler(KIND, TITLE, queue="heavy", max_attempts=1, estimate_memory=memory_mb)
def xval_job(ctx: JobContext) -> dict[str, Any]:
    assert ctx.project_folder is not None
    try:
        return run_xval(ctx.engine(), ctx.project_folder, ctx.params, ctx.tick)
    except ApiError as exc:
        raise JobError(exc.code, exc.message, exc.hint, retry=False) from None
