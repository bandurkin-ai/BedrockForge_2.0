"""The common set of the benches of sample processing (ADR 0018 «Замеры», решения 16 и 27; ТЗ 8.2.3: «50 тыс.
скважин, 5 млн проб, 10 полей»): `composites`, `composite_bench`, `composite_merge`, `topcut_analysis`, `declus`,
`composite_preview`, `composite_compile`.

    meta = build(work, params)        # in `prepare`: the project, the set, the tables, their snapshots
    meta, engine, folder = opened(work)

The data — `tools/gen/drillholes.py::arrays` (reproducible; what a real base has: samples of unequal length and
with gaps, ties at the detection limit, nuggets, unassayed runs, holes without TD, without surveys, without a
collar or a lithology, flat holes, infill drilling) written as the import writes them (`drillholes.store`):
  «Скважины»  `holes` holes — collars, a survey station every 50 m;
  «Пробы»     `holes × per_hole` samples, `fields` fields: the grades AU… (г/т, %), DENSITY and the text ROCK;
  «Литология» `holes × litho_per_hole` intervals with ROCK, its contacts inside the samples (ci — 500 тыс. +
              100 тыс., full — 5 млн + 1 млн: the two tables of `composite_merge`).
The targets of ТЗ 8.2.3 are counted «при готовом снимке таблицы» (решение 16): `build` opens every table once, as
the «Таблица» panel does (`drillholes.snapshot.build_rows` → `cache/tables/<id>-<token>-<rev>.parquet`; a table
under 100 000 rows has no file and is read from `dh_rows`, as in the program). With `cold=True` the read of the
chosen columns of «Пробы» without the snapshot is timed first (`meta["cold_read_s"]`: the line of the report
without a budget).

A scenario calls the function the handler of the job calls (`composites.jobs.run`, `run_tables`,
`jobs_prep.run_prep`) or the route (the preview, the analysis of the top cut), with clocks around the functions
of its stages:

    with stages((source, "read", "read"), (compose, "compute", "compute")) as took:
        out = cj.run(engine, folder, {"body": body, "rows": rows}, job_id=1, tick=ticks)
    took["read"]      seconds inside every call of `source.read`;   took["@read"] — when the last one returned

The clock replaces the attribute of the module for the time of the call (the handler looks it up there), so a
renamed stage fails loudly instead of measuring nothing. Every job starts from the project as `build` left it
(`fresh`): the database and the snapshots are put aside in `build` and copied back before a job that follows
another one — a repeated run (`--repeat`) does the work of the first one, a job never sees the table of another.
`warm` loads the kernels outside the timing: the same handlers on a set of 24 holes in a project of its own.
`ref` of the meta — what the results are checked against (computed from the arrays, not by the kernels).
"""

from __future__ import annotations

import contextlib
import gc
import json
import shutil
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT = "Замер обработки проб"
DB, KEPT, USED = "project.sqlite", "kept.sqlite", "used"
SET, SAMPLES, LITHO = "Скважины", "Пробы", "Литология"
UNITS = {"AU": "г/т", "AG": "г/т", "AS": "г/т"}  # the other grades — %
DENSITY, ROCK = "DENSITY", "ROCK"
WARM = {"holes": 24, "per_hole": 60, "litho_per_hole": 5}


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


# --- the data -------------------------------------------------------------------------------------------------


def _short_stretches(d: dict[str, Any], span: Any, least: float) -> float:
    """Metres of the stretches of one rock shorter than `least` (f_min·L: they get no composite, ТЗ 8.2.2).
    Consecutive intervals of one rock are one stretch; an interval without a rock and the hole below its
    lithology have none and are stretches too."""
    import numpy as np

    rocks = {r: k for k, r in enumerate(sorted({r for r in d["l_rock"].tolist() if r is not None}))}
    code = np.array([rocks.get(r, -1) for r in d["l_rock"].tolist()], dtype=np.int64)
    h, a, b = d["l_hole"], d["l_from"], d["l_to"]
    last = np.flatnonzero(np.append(h[1:] != h[:-1], True))  # the last interval of every hole
    tail = last[span[h[last]] > b[last] + 1e-9]
    at = tail + 1  # the stretch from the lithology to the bottom of the hole goes after it
    h, code = np.insert(h, at, h[tail]), np.insert(code, at, -1)
    a, b = np.insert(a, at, b[tail]), np.insert(b, at, span[d["l_hole"][tail]])
    first = np.flatnonzero(np.append(True, (h[1:] != h[:-1]) | (code[1:] != code[:-1])))
    length = b[np.append(first[1:] - 1, len(h) - 1)] - a[first]
    return float(length[length < least * (1.0 - 1e-9)].sum())


def _reference(d: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """What the scenarios check their results against, from the arrays of the generator."""
    import numpy as np

    holes = len(d["names"])
    ln = d["a_to"] - d["a_from"]
    bottom = np.zeros(holes)
    np.maximum.at(bottom, d["a_hole"], d["a_to"])
    collar = ~np.isnan(d["x"])
    td = np.where(collar, d["td"], np.nan)  # a hole met only in the samples has no TD
    span = np.fmax(td, bottom)  # the stretch composites cover: to TD, or to the bottom of the last sample
    with_litho = np.zeros(holes, dtype=bool)
    with_litho[d["l_hole"]] = True
    au = d["grades"]["AU"]
    have = ~np.isnan(au)
    both = with_litho[d["a_hole"]]
    return {
        "span_m": float(span.sum()),
        "span_collar_m": float(span[collar].sum()),
        "short_m": _short_stretches(d, span, 0.5 * float(params.get("length", 2.0))),  # f_min = 0.5, as given
        "no_collar": int(holes - collar.sum()),
        "sampled_m": float(ln.sum()),
        "merge_m": float(ln[both].sum()),  # samples of the holes the lithology has: it covers them whole
        "merge_au": float((ln * au)[both & have].sum()),
        "au_n": int(have.sum()),
        "au_mean": float((ln * au)[have].sum() / ln[have].sum()),
        "au_max": float(np.nanmax(au)),
    }


def write_set(
    engine: Any, folder: Path, name: str, d: dict[str, Any], params: dict[str, Any], *, cold: bool = False
) -> dict[str, Any]:
    """The set of `arrays` in the project: collars, surveys, «Пробы», «Литология», the snapshots."""
    import numpy as np

    from bf_core.db import dialect
    from bf_core.drillholes import store

    names = d["names"]
    collars = [
        store.CollarRow(names[i], x, y, z, td)
        for i, (x, y, z, td) in enumerate(zip(d["x"].tolist(), d["y"].tolist(), d["z"].tolist(), d["td"].tolist(),
                                              strict=True))
        if x == x  # no coordinates — no collar: the hole comes with its samples
    ]  # fmt: skip
    surveys = [
        (names[h], depth, az, dip)
        for h, depth, az, dip in zip(d["s_hole"].tolist(), d["s_depth"].tolist(), d["s_az"].tolist(),
                                     d["s_dip"].tolist(), strict=True)
    ]  # fmt: skip
    with dialect.write_tx(engine) as conn:
        set_id = store.create_set(conn, name)
        store.replace_collars(conn, set_id, collars)
        store.replace_surveys_in(conn, set_id, surveys, _noop)
    hole_names = np.array(names, dtype=object)
    grades = d["grades"]
    cols = [store.ColumnSpec(n, "number", UNITS.get(n, "%"), "component") for n in grades]
    cols += [store.ColumnSpec(DENSITY, "number", "т/м³"), store.ColumnSpec(ROCK, "text")]
    tables = {
        "samples": store.TableData(SAMPLES, "assay", hole_names[d["a_hole"]].tolist(), d["a_from"], d["a_to"], cols,
                                   [*grades.values(), d["density"], d["rock"]]),
        "litho": store.TableData(LITHO, "interval", hole_names[d["l_hole"]].tolist(), d["l_from"], d["l_to"],
                                 [store.ColumnSpec(ROCK, "text")], [d["l_rock"]]),
    }  # fmt: skip
    out: dict[str, Any] = {"set_id": set_id, "holes": len(names), "rows": len(d["a_hole"]),
                           "litho_rows": len(d["l_hole"]), "numbers": [*grades, DENSITY], "grades": list(grades),
                           "ref": _reference(d, params)}  # fmt: skip
    for key, data in tables.items():
        out[key] = int(store.write_table(engine, set_id, data, job_id=None, tick=_noop)["table_id"])
    del tables, d
    gc.collect()
    if cold:
        from bf_core.composites import source

        t0 = time.perf_counter()
        got = source.read(engine, set_id, out["samples"], [*out["numbers"], ROCK], cache_dir=None)
        out["cold_read_s"] = round(time.perf_counter() - t0, 3)
        assert got.source == "rows" and got.rows == out["rows"]
        del got
        gc.collect()
    snapshot(engine, folder, set_id, [out["samples"], out["litho"]])
    return out


def snapshot(engine: Any, folder: Path, set_id: int, table_ids: list[int]) -> None:
    """«Таблица хотя бы раз открывалась»: the Parquet snapshot of the current revision of every table (≥ 100 000
    rows), when it is not there."""
    from bf_core.db import dialect
    from bf_core.drillholes.snapshot import PARQUET_FROM, build_rows, table_key

    cache = folder / "cache" / "tables"
    with dialect.read_tx(engine) as conn:
        for tid in table_ids:
            key = table_key(conn, "bench", set_id, str(tid))
            assert key is not None, tid
            if not (cache / f"{tid}-{key[-2]}-{key[-1]}.parquet").is_file():
                snap = build_rows(conn, set_id, tid, key, cache)
                assert len(snap.ids) < PARQUET_FROM or (cache / f"{tid}-{key[-2]}-{key[-1]}.parquet").is_file()
                del snap
    gc.collect()


def _project(root: Path, params: dict[str, Any], seed: int, *, cold: bool = False) -> dict[str, Any]:
    from tools.gen.drillholes import arrays

    from bf_core.db import dialect
    from bf_core.projects.project import create_folder

    folder = create_folder(root, PROJECT, {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    try:
        d = arrays(int(params["holes"]), int(params["per_hole"]), fields=int(params.get("fields", 10)),
                   litho_per_hole=int(params.get("litho_per_hole", 20)), seed=seed)  # fmt: skip
        return {"folder": str(folder), **write_set(engine, folder, SET, d, params, cold=cold)}
    finally:
        engine.dispose()


def build(work: Path, params: dict[str, Any], *, cold: bool = False) -> dict[str, Any]:
    """The project of a scenario in `work` (`prepare`); the meta — also in `work/samples.json`. The database
    (its journal written into it) and the names of the snapshots are kept for `fresh`."""
    import sqlite3

    meta = _project(work, params, 12, cold=cold)
    folder = Path(meta["folder"])
    con = sqlite3.connect(folder / DB)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()
    shutil.copyfile(folder / DB, work / KEPT)
    cache = folder / "cache" / "tables"
    meta["snapshots"] = sorted(f.name for f in cache.iterdir()) if cache.is_dir() else []
    (work / "samples.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def opened(work: Path) -> tuple[dict[str, Any], Any, Path]:
    """(meta, engine, folder) of the project `build` made; the caller disposes the engine."""
    from bf_core.db import dialect

    meta = json.loads((work / "samples.json").read_text(encoding="utf-8"))
    folder = Path(meta["folder"])
    return meta, dialect.create_engine(dialect.sqlite_url(str(folder / DB))), folder


def fresh(work: Path) -> tuple[dict[str, Any], Any, Path, float]:
    """`opened` for a job that writes: the project as `build` left it — after another job (of this run or of an
    earlier one of `--repeat`) the database is copied back and the snapshots of results go; the trajectories a
    job of this process cached are forgotten, as in a worker that has just started. The last item — the
    seconds it took (0 before the first job). No engine of the project may be open."""
    meta = json.loads((work / "samples.json").read_text(encoding="utf-8"))
    folder = Path(meta["folder"])
    t0 = time.perf_counter()
    if (work / USED).exists():
        for tail in ("-wal", "-shm"):
            (folder / (DB + tail)).unlink(missing_ok=True)
        shutil.copyfile(work / KEPT, folder / DB)
        cache = folder / "cache" / "tables"
        for f in cache.iterdir() if cache.is_dir() else ():
            if f.name not in meta["snapshots"]:
                f.unlink()
        from bf_core.drillholes import trajectory

        with trajectory._lock:
            trajectory._cache.clear()
    (work / USED).touch()
    took = time.perf_counter() - t0
    return (*opened(work), took)


def service(engine: Any, folder: Path) -> Any:
    """What a route takes as `svc`: the current project (`require_current`)."""
    project = SimpleNamespace(engine=engine, folder=folder, uid="bench")
    return SimpleNamespace(require_current=lambda writable=False: project)


def warm(job: Callable[[dict[str, Any], Any, Path], Any], fields: int = 10) -> None:
    """`job(meta, engine, folder)` on a set of 24 holes in a project of its own, removed at once: the kernels of
    the scenario compiled into NUMBA_CACHE_DIR or loaded from it, the modules imported (`load`, not timed)."""
    import tempfile

    from bf_core.db import dialect

    with tempfile.TemporaryDirectory(prefix="bf-bench-warm-") as tmp:
        meta = _project(Path(tmp), {**WARM, "fields": fields}, 13)
        folder = Path(meta["folder"])
        engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
        try:
            job(meta, engine, folder)
        finally:
            engine.dispose()
    gc.collect()


# --- a run ----------------------------------------------------------------------------------------------------


@contextlib.contextmanager
def stages(*spots: tuple[Any, str, str], note: Callable[[str, Any], None] | None = None) -> Iterator[dict[str, float]]:
    """Clocks around functions: `(module, attribute, key)` — the seconds inside every call are added to
    `took[key]`, `took["@" + key]` is `time.perf_counter()` at the last return; `note(key, result)` sees what a
    call returned. The attributes are put back at the end."""
    took: dict[str, float] = {}
    saved: list[tuple[Any, str, Any]] = []

    def clock(fn: Any, key: str) -> Any:
        def timed(*args: Any, **kw: Any) -> Any:
            t0 = time.perf_counter()
            try:
                out = fn(*args, **kw)
            finally:
                t1 = time.perf_counter()
                took[key] = took.get(key, 0.0) + t1 - t0
                took["@" + key] = t1
            if note is not None:
                note(key, out)
            return out

        return timed

    try:
        for owner, name, key in spots:
            fn = getattr(owner, name)  # AttributeError: the handler has another stage now — fix the scenario
            saved.append((owner, name, fn))
            setattr(owner, name, clock(fn, key))
        yield took
    finally:
        for owner, name, fn in reversed(saved):
            setattr(owner, name, fn)


class Ticks:
    """The `tick` of a job: counts the calls and keeps the longest wait between two of them — how long a cancel
    may stay unanswered while the job can still be cancelled."""

    def __init__(self) -> None:
        self.n = 0
        self.gap = 0.0
        self._last = time.perf_counter()

    def __call__(self, _p: float | None = None, _m: str | None = None) -> None:
        now = time.perf_counter()
        self.gap = max(self.gap, now - self._last)
        self._last = now
        self.n += 1


def report_of(engine: Any, result_id: int) -> dict[str, Any]:
    """`dh_results.report` of a result."""
    import sqlalchemy as sa

    from bf_core.db import dialect
    from bf_core.db.schema import dh_results

    with dialect.read_tx(engine) as conn:
        rep = conn.execute(sa.select(dh_results.c.report).where(dh_results.c.id == result_id)).scalar_one()
    return dict(rep or {})


def read_from(rows: int) -> str:
    """Where `source.read` takes a table of that many rows from, once it was opened: its snapshot — "parquet" —
    or, under 100 000 rows (no snapshot is kept), `dh_rows` — "rows"."""
    from bf_core.drillholes.snapshot import PARQUET_FROM

    return "parquet" if rows >= PARQUET_FROM else "rows"


def peak_mb() -> float | None:
    from bench.run import peak_rss_mb

    got = peak_rss_mb()
    return None if got is None else round(got, 1)


def close(a: float, b: float, rel: float) -> bool:
    return abs(a - b) <= rel * max(abs(a), abs(b), 1.0)


def timings(took: dict[str, float], keys: dict[str, tuple[str, ...]], prefix: str = "") -> dict[str, float]:
    """Info keys `<prefix><stage>_s` — sums of the clocks of `stages`: {"read": ("read",), "write": ("table",
    "commit")}; a clock that never ran counts as 0."""
    return {f"{prefix}{name}_s": round(sum(took.get(k, 0.0) for k in parts), 3) for name, parts in keys.items()}
