"""A block file (Micromine DAT, CSV, CSV Micromine; Datamine — `formats/datamine_blocks.py` through the same contract)
imported as a block model of the job `import.objects` (ТЗ 7.31, 8.7.2; ADR 0023 decisions 5–7; Д-13).

`inspect_blockfile` — what the wizard shows: the roles of the columns, the grid, the check of three centres, the
warnings (the first `INSPECT_ROWS` blocks of a big file). `write_blockfile` / `import_model` — the import, in portions
and cancellable (`tick` between them; a long sort ticks every half second):

1. **Чтение** — the source (`formats.blocks_text`) gives portions; the columns go to a spool of raw files in the order
   of the file (`cache\\import\\<job>\\`), the centres and sizes also to `GridScan` (the codes of «пусто» → NaN, a text
   field → int32 codes into labels built over the whole file);
2. the grid (`GridScan.finish` + the hint of the file and the wizard) and the rows (P, s, w) of every block
   (`InferredGrid.rows_of`); blocks the grid cannot hold → 422 `blocks_unaligned`; the check of three centres → 422
   `rotation_mismatch` for a turned model;
3. **Сортировка** — `schema.sort_rows` (P, sz, sy, sx); a repeated row (the same P, s, w) is dropped and counted — the
   first stays; the same (P, s) with another size cannot be stored twice: the first stays, the rest are counted as
   overlaps (their rows are in the examples);
4. **Запись** — the fields of every portion of the sorted rows are taken from the spool (one pass over each file per
   portion), `CellsWriterV2` writes them, `ModelChecker` checks them; the checks go to `meta.checks`; then a short
   transaction commits the model as `building` under the id of the job (`commit_block_model`).

Row numbers in the checks and in the errors are 0-based rows of the data of the file (the hints show them from 1).
Memory: about 60 bytes a row of the sort and the keys plus a portion of 2²⁰ rows of the fields; disk: the spool
(8 bytes a number) and the file written. A file the grid cannot hold is a `FormatError` (a line of the journal, the
others load); out of memory or disk is a `JobError` (the job fails and is tried again).
"""

from __future__ import annotations

import contextlib
import errno
import shutil
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import checks as bcheck
from bf_core.blocks import geometry as geo
from bf_core.blocks import schema as bs
from bf_core.blocks.grid_infer import OK, GridScan, Unaligned
from bf_core.formats import blocks_roles as br
from bf_core.formats.blocks_text import CHUNK_ROWS, BlockSource, TextColumn, empty_to_nan, text_is_empty
from bf_core.formats.errors import FormatError
from bf_core.jobs.context import JobError

from .blockfile_parts import BlockOptions as BlockOptions  # re-exported: the answers of the wizard
from .blockfile_parts import (
    FieldPlan,
    Resolved,
    Spool,
    Tick,
    _err,
    _n,
    busy,
    drop_wrong_fractions,
    plan_fields,
    resolve,
)
from .object_blocks import Blocks

__all__ = [
    "BlockFile", "BlockOptions", "Imported", "import_model", "inspect_blockfile", "inspect_entry", "is_blockfile",
    "memory", "open_source", "write_blockfile",
]  # fmt: skip

PORTION = bs.ROW_GROUP_V2  # rows written and taken from the spool at a time
INSPECT_ROWS = 2_000_000
MAX_NOTES = 40
DATAMINE_SUFFIXES = (".dm", ".fbm")


@dataclass
class Imported:
    """What `import_model` made: the model written (`building` or `ready`) and what to tell about it."""

    model_id: int
    name: str
    rows: int
    parents: int
    sub: bool
    fields: list[str]
    checks: dict[str, Any]
    warnings: list[str]
    grid: dict[str, Any]


@dataclass
class BlockFile(Blocks):
    """`Blocks` of one file, with the format the journal names (dat | csv | csv_micromine | dm | fbm)."""

    format: str = ""
    warnings: list[str] = field(default_factory=list)


# --- reading -----------------------------------------------------------------------------------------------------


def _read(source: BlockSource, res: Resolved, plans: list[FieldPlan], opts: Any, spool: Spool, scan: GridScan,
          tick: Tick, notes: list[str]) -> int:  # fmt: skip
    """Pass 1: the portions of the file into the spool and the scan of the grid; the rows read."""
    from bf_core.formats.numbers import number_text

    geometry = {k: c for k, c in res.geometry.items() if c}
    want = [*geometry.values(), *[p.column for p in plans]]
    rows = 0
    for chunk in source.chunks(want, CHUNK_ROWS, lambda f, m: tick(0.45 * f, m)):
        c = np.column_stack([chunk.numbers[geometry[a]] for a in "xyz"])
        d = np.column_stack([chunk.numbers[geometry["d" + a]] for a in "xyz"]) if res.sized else None
        scan.add(c, d)
        for k, a in enumerate("xyz"):
            spool.put(a, c[:, k])
            if d is not None:
                spool.put("d" + a, d[:, k])
        for p in plans:
            if p.text:
                tc = chunk.texts[p.column]
                spool.put(p.key, p.labels.map(tc, text_is_empty(tc.labels, opts.empty)))  # type: ignore[union-attr]
                continue
            values = chunk.numbers[p.column]
            p.emptied += empty_to_nan(values, opts.empty)
            if p.kind == "number":
                if p.role == "frac":
                    known = values[np.isfinite(values)]
                    if len(known):
                        p.lo, p.hi = min(p.lo, float(known.min())), max(p.hi, float(known.max()))
                spool.put(p.key, values)
                continue
            ok = np.isfinite(values)  # a number field taken as a code or a class: its labels are the numbers
            uniq, inverse = np.unique(values[ok], return_inverse=True)
            codes = np.full(len(values), -1, dtype=np.int32)
            tc = TextColumn(inverse.astype(np.int32), [number_text(v) for v in uniq])
            codes[ok] = p.labels.map(tc, np.zeros(len(uniq), dtype=bool))[: len(inverse)]  # type: ignore[union-attr]
            spool.put(p.key, codes)
        rows += chunk.rows
    notes.extend(source.notes)
    for p in plans:
        if p.emptied:
            shown = ", ".join(f"{v:g}" for v in opts.empty)
            notes.append(f"Поле «{p.name}»: значений с кодом пустого ({shown}) — {_n(p.emptied)}; они пустые, не нули.")
    return rows


def _free_check(folder: Path, rows: int, columns: int) -> None:
    """The disk the spool and the file written will need (the estimate: 8 bytes a value, once for each)."""
    if not rows:
        return
    need = int(rows * columns * 8 * 1.6)
    probe = folder
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    if free < need:
        raise JobError("disk_full", f"Для импорта нужно около {_n(need >> 20)} МБ на диске, "
                       f"свободно {_n(free >> 20)} МБ.",
                       "Освободите место на диске с проектом и повторите импорт.")  # fmt: skip


@dataclass
class Cells:
    """Pass 2: the rows (P, s, w) of the blocks in the order of the file; `valid` — the raw rows that are blocks."""

    p: np.ndarray
    s: np.ndarray | None
    w: np.ndarray | None
    valid: np.ndarray  # the raw row numbers (ascending) of the rows that are blocks
    invalid: int
    check3: Any


def _cells(inferred: Any, spool: Spool, rows: int, res: Resolved, tick: Tick) -> Cells:
    sub = inferred.sub
    p = np.empty(rows, dtype=np.uint64)
    bad = np.empty(rows, dtype=np.int8)
    s = np.empty((rows, 3), dtype=np.uint8) if sub else None
    w = np.empty((rows, 3), dtype=np.uint8) if sub else None
    lost = Unaligned()
    names = ("x", "y", "z", "dx", "dy", "dz") if res.sized else ("x", "y", "z")

    def geometry(a: int, b: int) -> tuple[np.ndarray, np.ndarray | None]:
        c = np.column_stack([spool.read(k, a, b) for k in names[:3]])
        return c, (np.column_stack([spool.read(k, a, b) for k in names[3:]]) if res.sized else None)

    for a in range(0, rows, PORTION):
        b = min(rows, a + PORTION)
        c, d = geometry(a, b)
        cells = inferred.rows_of(c, d)
        lost.add(a, cells)
        p[a:b], bad[a:b] = cells.p, cells.bad
        if s is not None and w is not None and cells.s is not None and cells.w is not None:
            s[a:b], w[a:b] = cells.s, cells.w
        tick(0.45 + 0.05 * b / rows, "Сетка и ячейки")
    lost.raise_if()
    valid = np.flatnonzero(bad == OK)
    if not len(valid):
        raise _err("block_model_invalid", "В файле нет ни одного блока с центром и размером.",
                   "Проверьте столбцы центров и размеров.")  # fmt: skip
    three = sorted({int(valid[0]), int(valid[len(valid) // 2]), int(valid[-1])})
    rows3 = [geometry(r, r + 1) for r in three]
    centres3 = np.vstack([c for c, _ in rows3])  # type: ignore[misc]
    sizes3 = np.vstack([d for _, d in rows3]) if res.sized else None  # type: ignore[misc]
    check3 = inferred.check3(centres3, sizes3, three)
    if len(valid) != rows:  # rows without a centre or a size are not blocks
        p, s, w = p[valid], None if s is None else s[valid], None if w is None else w[valid]
    return Cells(p, s, w, valid, lost.invalid, check3)


# --- sorting, repeated rows ---------------------------------------------------------------------------------------


@dataclass
class Arranged:
    """The blocks in the order of the model: `sel` — their raw rows; the repeated ones are left out and counted."""

    sel: np.ndarray
    p: np.ndarray
    s: np.ndarray | None
    w: np.ndarray | None
    counts: dict[str, int]
    examples: dict[str, list[dict[str, Any]]]


def _arrange(cells: Cells, rows: int, tick: Tick) -> Arranged:
    p, s, w = cells.p, cells.s, cells.w
    order = busy(lambda: bs.sort_rows(p, s), tick, 0.5, "Сортировка")
    sel = order if len(cells.valid) == rows else cells.valid[order]
    p, s, w = p[order], None if s is None else s[order], None if w is None else w[order]
    del order
    key = geo.row_keys(p, s)
    same = np.zeros(len(key), dtype=bool)
    same[1:] = key[1:] == key[:-1]
    del key
    counts = {"duplicate": 0, "overlap": 0}
    examples: dict[str, list[dict[str, Any]]] = {"duplicate": [], "overlap": []}
    if same.any():
        head = np.maximum.accumulate(np.where(same, 0, np.arange(len(same))))
        exact = same if w is None else same & (w == w[head]).all(axis=1)
        for kind, mask in (("duplicate", exact), ("overlap", same & ~exact)):
            at = np.flatnonzero(mask)
            counts[kind] = len(at)
            for i in at[: bs.MAX_EXAMPLES]:
                sub = None if s is None or w is None else [*map(int, s[i]), *map(int, w[i])]
                examples[kind].append({"check": kind, "row": int(sel[i]), "p": int(p[i]),
                                       "other_row": int(sel[head[i]]), "sub": sub})  # fmt: skip
        keep = ~same
        sel, p, s, w = sel[keep], p[keep], None if s is None else s[keep], None if w is None else w[keep]
    return Arranged(sel, p, s, w, counts, examples)


# --- the import --------------------------------------------------------------------------------------------------


def import_model(
    source: BlockSource,
    opts: BlockOptions,
    *,
    engine: sa.Engine,
    store: Any,
    workdir: Path,
    name: str,
    label: str,
    tick: Tick,
    log: Callable[[str, str], None] | None = None,
    folder: str | None = None,
    state: str = "ready",
    job_id: int | None = None,
    author: str | None = None,
) -> Imported:
    """The whole import of a source as a block model of the project (see the module docstring). `workdir` — the
    folder of the spool (removed at the end), `name` — the wanted name of the model (a free one is taken)."""
    from bf_core.blocks import store as bm

    res = resolve(source.names, source.kinds, opts)
    plans = plan_fields(res, source.kinds, opts)
    notes: list[str] = []
    scan = GridScan(opts.hint(source.hint), centres=opts.centres)
    spool = Spool(workdir / "spool")
    writer: bm.CellsWriterV2 | None = None
    staged: bm.StagedCells | None = None
    try:
        _free_check(workdir, source.rows_estimate, len(plans) + (6 if res.sized else 3))
        tick(0.0, "Чтение")
        rows = _read(source, res, plans, opts, spool, scan, tick, notes)
        spool.flush()
        inferred = scan.finish()
        notes = [*inferred.warnings, *notes, *drop_wrong_fractions(plans)]
        cells = _cells(inferred, spool, rows, res, tick)
        if not cells.check3.ok:
            notes.append(f"Центры трёх блоков (строки {', '.join(str(r + 1) for r in cells.check3.rows)}) расходятся "
                         f"с вычисленными на {cells.check3.dist:.3g} м.")  # fmt: skip
        if cells.invalid:
            notes.append(f"Строк без центра или с размером ≤ 0: {_n(cells.invalid)} — пропущены.")
        arr = _arrange(cells, rows, tick)
        del cells
        if arr.counts["overlap"]:
            notes.append(f"Блоков с тем же положением в родителе, но другим размером: {_n(arr.counts['overlap'])}. "
                         "Оставлена первая строка; остальные — перекрытия в находках.")  # fmt: skip
        # --- write and check
        tick(0.55, "Запись")
        grid, sub = inferred.grid, inferred.sub
        origin: dict[str, Any] = {"op": "import", "inputs": [], "algo": None, "versions": {},
                  "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}  # fmt: skip
        specs = [pl.spec({**origin, "params": {"format": source.format, "file": label, "column": pl.column,
                                               "empty": list(opts.empty)}}) for pl in plans]  # fmt: skip
        frac = next((pl.name for pl in plans if pl.role == "frac"), None)
        writer = bm.CellsWriterV2(store, grid, specs, sub=sub)
        chk = bcheck.ModelChecker(grid, [{"name": pl.name, "kind": pl.kind} for pl in plans], frac)
        for kind in ("duplicate", "overlap"):  # found while sorting: counted here, their rows are file rows
            chk.count[kind].add(np.empty(0, dtype=np.int64), chk.limit, arr.counts[kind])
        n_out = len(arr.sel)
        for a in range(0, n_out, PORTION):
            b = min(n_out, a + PORTION)
            at = np.argsort(arr.sel[a:b], kind="stable")
            ordered = arr.sel[a:b][at]  # ascending: one pass over each file of the spool
            values = {pl.name: spool.take(pl.key, rows, ordered, at) for pl in plans}
            ps, ss, ws = arr.p[a:b], None if arr.s is None else arr.s[a:b], None if arr.w is None else arr.w[a:b]
            writer.write(ps, values, ss, ws)
            chk.feed(ps, ss, ws, values)
            tick(0.55 + 0.4 * b / n_out, "Запись")
        staged = writer.finish()
        report = chk.finish()
        if report.finding("outside") or report.finding("crossing"):
            raise bs.bad_model("Блоки не укладываются в сетку модели.", "Проверьте начало и размер родителя в мастере.")
        checks = report.checks

        def to_file(ex: dict[str, Any]) -> dict[str, Any]:  # a row of the sorted model → the row of the file
            out = {**ex, "row": int(arr.sel[ex["row"]])}
            if "other_row" in ex:
                out["other_row"] = int(arr.sel[ex["other_row"]])
            return out

        mine = [*arr.examples["duplicate"], *arr.examples["overlap"]]  # found while sorting: file rows already
        checks["examples"] = [*mine, *map(to_file, checks["examples"])][: bs.MAX_EXAMPLES]
        staged.v2 = {**(staged.v2 or {}), "checks": checks}
        for f in report.findings:
            if f["severity"] in (bcheck.ERROR, bcheck.WARNING):
                notes.append(f["message"])
                if log is not None:
                    log(f"«{label}»: {f['message']} {f['hint']}".strip(), "warn")
        tick(0.97, "Запись в проект")
        from bf_core.db import dialect
        from bf_core.db.schema import objects

        with dialect.write_tx(engine) as conn:
            bm_id = bm.commit_block_model(
                conn, store, staged, name=name, source={"format": source.format, "file": label},
                warnings=notes[:MAX_NOTES], folder=folder, state=state, job_id=job_id, author=author,
            )  # fmt: skip
            final = conn.execute(sa.select(objects.c.name).where(objects.c.id == bm_id)).scalar_one()
        return Imported(bm_id, str(final), n_out, int(staged.v2["parents"]), sub, [pl.name for pl in plans], checks,
                        notes[:MAX_NOTES], grid.to_meta())  # fmt: skip
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise JobError("disk_full", f"«{label}»: на диске кончилось место.",
                           "Освободите место на диске с проектом и повторите импорт.") from exc  # fmt: skip
        raise
    except MemoryError as exc:
        raise JobError("out_of_memory", f"«{label}»: не хватило памяти на блочную модель.",
                       "Закройте другие программы или разделите модель на части.") from exc  # fmt: skip
    finally:
        if staged is not None:
            staged.discard()
        if writer is not None:
            writer.discard()
        spool.flush()
        shutil.rmtree(workdir, ignore_errors=True)
        with contextlib.suppress(OSError):
            workdir.parent.rmdir()  # `cache\\import\\<job>` when it holds nothing else
        source.close()


# --- the job -------------------------------------------------------------------------------------------------------


def open_source(path: Path, opts: BlockOptions) -> BlockSource:
    """The reader of a block file by its kind: Datamine (`.dm`, `.fbm`) — `formats.datamine_blocks.open_blocks`
    (Э17 17.14), a table (DAT, CSV) — `formats.blocks_text`."""
    from bf_core.formats.blocks_text import open_block_file

    if path.suffix.lower() in DATAMINE_SUFFIXES:
        try:
            from bf_core.formats.datamine_blocks import open_blocks  # type: ignore[import-not-found,unused-ignore]
        except ImportError as exc:
            raise FormatError("unsupported", "Блочные модели Datamine в этой версии не загружаются.",
                              "Сохраните модель в DAT или CSV.") from exc  # fmt: skip
        return open_blocks(path, encoding=opts.encoding)  # type: ignore[no-any-return,unused-ignore]
    return open_block_file(path, encoding=opts.encoding, delimiter=opts.delimiter)


def is_blockfile(path: str | Path, chosen: Mapping[str, Any] | None = None) -> bool:
    """A file of the selection is a block model: the wizard asked for it (`blocks` in its options), or its header has
    the centre and the size of a block (ТЗ 7.31); a table of points (X, Y, Z, values) does not. A Datamine `.dm`
    is told by the registry (`DmHeader.kind == block_model`); a `.fbm` always is one."""
    from bf_core.formats.blocks_text import peek_names

    path = Path(path)
    if (chosen or {}).get("blocks") is not None or path.suffix.lower() == ".fbm":
        return True
    if path.suffix.lower() == ".dm":  # told by its fields: the registry sends a block model of Datamine here
        from bf_core.formats.datamine_blocks import is_block_model

        return is_block_model(path, (chosen or {}).get("encoding"))
    peek = peek_names(path, (chosen or {}).get("encoding"))
    if peek is None:
        return False
    roles = br.classify(peek[1])
    return roles.has_centre and roles.has_size and br.looks_like_block_model(peek[1])


def memory(path: Path, size: int) -> int:
    """MB the job needs: the keys and rows of the sort (≈ 70 bytes a block) and the portions of the writing."""
    from bf_core.formats.blocks_text import peek_names

    rows = size // 40  # a text row of a block file
    try:
        if path.suffix.lower() not in DATAMINE_SUFFIXES and (peek_names(path) or ("", []))[0] == "dat":
            from bf_core.formats.micromine_header import read_header

            h = read_header(path)
            rows = max(0, size - h.data_offset) // max(h.record_size, 1)
    except (FormatError, OSError):
        pass
    return int(1024 + rows * 70 / 2**20)


def write_blockfile(
    ctx: Any, engine: sa.Engine, store: Any, g: Any, chosen: Mapping[str, Any] | None, tick: Tick, folder: str | None
) -> BlockFile:
    """The block file of a group `g` (the main file `g.main`, the options of the file `chosen`) as a `building` model
    of the job `ctx`; the shares of the progress are the caller's (`tick`). A file the grid cannot hold or that is not
    readable → `FormatError` (a line of the journal, the other files load); no memory or disk → `JobError`."""
    from bf_core.drillholes.edit import author

    path: Path = g.main
    opts = BlockOptions.of((chosen or {}).get("blocks"), encoding=(chosen or {}).get("encoding"),
                           name=(chosen or {}).get("name"))  # fmt: skip
    assert ctx.project_folder is not None
    work = ctx.project_folder / "cache" / "import" / str(ctx.id) / f"blocks-{uuid.uuid4().hex[:8]}"
    try:
        source = open_source(path, opts)
        done = import_model(source, opts, engine=engine, store=store, workdir=work, name=opts.name or path.stem,
                            label=path.name, tick=tick, log=ctx.log, folder=folder, state="building", job_id=ctx.id,
                            author=author())  # fmt: skip
    except ApiError as exc:  # the grid, the fields or the writer refused the file
        if exc.code in ("disk_full", "out_of_memory"):
            raise JobError(exc.code, exc.message, exc.hint) from exc
        raise FormatError(exc.code, exc.message, exc.hint) from exc
    out = BlockFile(format=source.format, warnings=done.warnings)
    out.ids.append(done.model_id)
    out.counts.append({"kind": "block_model", "name": done.name, "cells": done.rows, "fields": len(done.fields),
                       "parents": done.parents, "sub": done.sub, "duplicates": done.checks["duplicates"],
                       "overlaps": done.checks["overlaps"]["n"]})  # fmt: skip
    grid = " × ".join(str(c) for c in done.grid["counts"])
    ctx.log(f"{path.name}: блочная модель «{done.name}» — сетка {grid}, блоков {_n(done.rows)}, родителей "
            f"{_n(done.parents)}, полей {len(done.fields)}", "info")  # fmt: skip
    return out


# --- what the wizard shows -----------------------------------------------------------------------------------------


def inspect_blockfile(
    path: str | Path, chosen: Mapping[str, Any] | None = None, tick: Tick | None = None, *, sample: int = INSPECT_ROWS
) -> dict[str, Any]:
    """`files[].blocks` of the inspection (docs/progress/e17-api.md §5): format, rows, columns with their roles, the
    grid inferred, `check3`, the empty codes that occur, the units, the warnings, `error`. The grid is read from the
    first `sample` blocks of a big file (a warning says so; the import reads all of them). A file that cannot be read
    is a `FormatError`; a model that cannot be built has `error` {code, message, hint}."""
    path = Path(path)
    opts = BlockOptions.of((chosen or {}).get("blocks"), encoding=(chosen or {}).get("encoding"),
                           name=(chosen or {}).get("name"))  # fmt: skip
    source = open_source(path, opts)
    try:
        return _inspect(source, opts, tick, sample)
    finally:
        source.close()


def inspect_entry(
    path: str | Path, chosen: Mapping[str, Any] | None = None, tick: Tick | None = None
) -> dict[str, Any]:
    """The entry of `files[]` of `inspect_files` for a block file (the form of `object_files.summarize`; the answer
    of the wizard — `blocks`). A model the file cannot make is still an entry (`blocks.error`), not a failed file."""
    path = Path(path)
    info = inspect_blockfile(path, chosen, tick)
    rows = int(info["rows"])
    names = [f["name"] for f in info["fields"]]
    warnings = list(info["warnings"])[:MAX_NOTES]
    if info["error"] is not None:
        warnings.insert(0, f"{info['error']['message']} {info['error']['hint']}".strip())
    totals = dict.fromkeys(("layers", "lines", "points", "vertices", "rasters", "wireframes", "triangles"), 0)
    return {
        "path": str(path), "file": path.name, "ok": True, "format": info["format"], "label": info["label"],
        "kind": "block_model", "encoding": info["encoding"], "crs": None, "sidecars": [], "layers": [],
        "rasters": [], "wireframes": [],
        "block_models": [{"name": path.stem, "counts": (info["grid"] or {}).get("counts"), "cells": rows,
                          "fields": names}],  # fmt: skip
        "totals": {**totals, "block_models": 1 if info["error"] is None else 0, "cells": rows},
        "unread": 0, "preliminary": False, "choices": {}, "warnings": warnings, "left_out": [], "blocks": info,
    }  # fmt: skip


@dataclass
class _Stat:
    lo: float = np.inf
    hi: float = -np.inf
    empty: set[float] = field(default_factory=set)


def _inspect(source: BlockSource, opts: BlockOptions, tick: Tick | None, sample: int) -> dict[str, Any]:
    found = br.classify(source.names)
    out: dict[str, Any] = {
        "format": source.format, "label": source.label, "rows": source.rows_estimate,
        "rows_exact": source.rows is not None,
        "columns": [], "roles": {a: found.geometry[a] for a in br.GEOMETRY}, "grid": None, "sub": False, "parents": 0,
        "empty_candidates": [], "units": {}, "fields": [], "check3": None, "warnings": [], "error": None,
        "encoding": source.encoding,
    }  # fmt: skip
    try:
        res = resolve(source.names, source.kinds, opts)
    except ApiError as exc:
        out["columns"] = [{"name": n, "type": source.kinds[n], "role": None} for n in source.names]
        out["error"] = {"code": exc.code, "message": exc.message, "hint": exc.hint}
        return out
    by_role = {c: r for r, c in res.geometry.items() if c}
    out["roles"] = dict(res.geometry)
    out["columns"] = [{"name": n, "type": source.kinds[n], "role": by_role.get(n)} for n in source.names]
    geometry = {k: c for k, c in res.geometry.items() if c}
    plans = plan_fields(res, source.kinds, opts)
    scan = GridScan(opts.hint(source.hint), centres=opts.centres)
    cs: list[np.ndarray] = []
    ds: list[np.ndarray] = []
    stats = {p.column: _Stat() for p in plans if not p.text}
    read = 0
    sample = sample if source.format in ("dat", "dm", "fbm") else sample // 4  # text is parsed slower
    for chunk in source.chunks([*geometry.values(), *[p.column for p in plans]], CHUNK_ROWS, tick, limit=sample):
        c = np.column_stack([chunk.numbers[geometry[a]] for a in "xyz"])
        d = np.column_stack([chunk.numbers[geometry["d" + a]] for a in "xyz"]) if res.sized else None
        scan.add(c, d)
        cs.append(c)
        if d is not None:
            ds.append(d)
        for column, st in stats.items():
            v = chunk.numbers[column]
            st.empty.update(br.empty_candidates(v))
            fin = v[np.isfinite(v)]
            if len(fin):
                st.lo, st.hi = min(st.lo, float(fin.min())), max(st.hi, float(fin.max()))
        read += chunk.rows
    notes = list(source.notes)
    out["warnings"] = notes
    if source.rows is None and read < sample:
        out["rows"], out["rows_exact"] = read, True  # the whole file was read
    if read >= sample and (source.rows is None or source.rows > read):
        notes.append(f"Сетка выведена по первым {_n(read)} блокам; при импорте файл читается весь.")
    try:
        inferred = scan.finish()
        notes[:0] = inferred.warnings
        c_all = np.concatenate(cs)
        d_all = np.concatenate(ds) if ds else None
        cells = inferred.rows_of(c_all, d_all)
        lost = Unaligned()
        lost.add(0, cells)
        lost.raise_if()
        valid = np.flatnonzero(cells.bad == OK)
        three = sorted({int(valid[0]), int(valid[len(valid) // 2]), int(valid[-1])})
        check = inferred.check3(c_all[three], None if d_all is None else d_all[three], three)
    except ApiError as exc:
        out["error"] = {"code": exc.code, "message": exc.message, "hint": exc.hint, **exc.extra}
        return out
    out["grid"] = inferred.grid.to_meta()
    out["sub"] = inferred.sub
    out["parents"] = int(np.unique(cells.p[valid]).size)
    out["check3"] = check.as_dict()
    if lost.invalid:
        notes.append(f"Строк без центра или с размером ≤ 0: {_n(lost.invalid)} — будут пропущены.")
    out["empty_candidates"] = sorted({c for st in stats.values() for c in st.empty})
    for p in plans:
        stat = stats.get(p.column)
        varying = stat is None or stat.lo != stat.hi
        role = p.role if p.role in ("grade", "density", "frac", "code", "class") else None
        if stat is not None and not varying and role in ("density", "grade"):
            role = None  # a constant «DENSITY» is no density
        out["fields"].append({"name": p.name, "column": p.column, "kind": p.kind, "unit": p.unit, "role": role,
                              "constant": not varying, "empty": sorted(stat.empty) if stat else []})  # fmt: skip
        if p.unit:
            out["units"][p.name] = p.unit
    return out
