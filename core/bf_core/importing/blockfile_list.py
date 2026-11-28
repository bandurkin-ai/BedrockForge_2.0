"""The import of a block file as a LIST of blocks (`blocks.layout = "list"`; ADR 0030 decisions 8, 12, 14; Д-45 А6): the
model that lies in no grid — the sizes of its blocks do not divide a parent, the offsets have no common step — is kept
as the centre and the size of every block (`blocks/list_layout.py`, meta v3). The user chooses it in the wizard after
422 `blocks_unaligned`; nothing here is automatic.

`write_list` is pass 2 and the write of `object_blockfile.import_model` when the options say `list`: the centres of the
spool go to the local axes of the enclosing grid, the rows are sorted by (idx, cz, cy, cx) with the repeated ones
(the same centre and size) left out and counted, the portions of the sorted rows are taken from the spool and written
by `ListWriter`, `ListChecker` finds the overlaps of the boxes. `summary` is what the wizard shows after the choice:
«Список блоков: N, охват …». Rows without a centre or a size ≤ 0 are not blocks: dropped and said, never zeros.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import numpy as np
import sqlalchemy as sa

from bf_core.blocks import checks as bcheck
from bf_core.blocks import list_layout as ll

from .blockfile_cells import PORTION
from .blockfile_parts import FieldPlan, Resolved, Spool, Tick, _n, busy

if TYPE_CHECKING:
    from .object_blockfile import BlockOptions, Imported

MAX_NOTES = 40


def project_box(frame: ll.ListFrame) -> list[float]:
    """[xmin, ymin, zmin, xmax, ymax, zmax] of the boxes in the axes of the project (a turn is about Z)."""
    lo, hi = frame.lo, frame.hi
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    pts = frame.grid.to_project(corners)
    return [float(v) for v in (*pts.min(axis=0), *pts.max(axis=0))]


def summary(frame: ll.ListFrame, parents: int) -> dict[str, Any]:
    """The line of the wizard: how many blocks, the extent, the sizes, the enclosing grid."""
    return {"rows": int(frame.rows), "extent": project_box(frame), "size_min": [float(v) for v in frame.smin],
            "size_max": [float(v) for v in frame.smax], "cells": list(frame.grid.counts),
            "cell": [float(v) for v in frame.grid.regular()], "parents": int(parents)}  # fmt: skip


def list_view(frame: ll.ListFrame, c: np.ndarray, d: np.ndarray | None, read: int, whole: bool) -> dict[str, Any]:
    """The part of the inspection of a list of blocks that differs (`object_blockfile._inspect`): the enclosing grid,
    no sub-blocks, the occupied cells, no refusal of a block, the summary «Список блоков: N, охват …»; `_invalid` —
    the rows without a centre or a size (the import drops them)."""
    assert d is not None
    good = np.isfinite(c).all(axis=1) & np.isfinite(d).all(axis=1) & (d > 0).all(axis=1)
    parents = int(np.unique(ll.cells_of(frame.grid, frame.local(c[good]))).size)
    three = sorted({0, max(read // 2, 0), max(read - 1, 0)})
    return {"layout": "list", "grid": frame.grid.to_meta(), "sub": False, "parents": parents,
            "check3": {"ok": True, "dist": 0.0, "rows": three}, "list": {**summary(frame, parents), "whole": whole},
            "_invalid": int(len(c) - good.sum())}  # fmt: skip


def write_list(
    source: Any,
    res: Resolved,
    plans: list[FieldPlan],
    opts: BlockOptions,
    spool: Spool,
    scan: ll.ListScan,
    rows: int,
    notes: list[str],
    *,
    engine: sa.Engine,
    store: Any,
    name: str,
    label: str,
    tick: Tick,
    log: Callable[[str, str], None] | None,
    folder: str | None,
    state: str,
    job_id: int | None,
    author: str | None,
) -> Imported:
    """Pass 2 and the write of a list of blocks (module docstring); returns what `import_model` returns."""
    from bf_core.blocks import store as bm

    from .object_blockfile import Imported

    frame = scan.finish()
    notes[:0] = frame.warnings
    tick(0.45, "Сетка и ячейки")
    c = np.column_stack([spool.read(k, 0, rows) for k in "xyz"])
    d = np.column_stack([spool.read(k, 0, rows) for k in ("dx", "dy", "dz")])
    good = np.isfinite(c).all(axis=1) & np.isfinite(d).all(axis=1) & (d > 0).all(axis=1)
    raw = np.flatnonzero(good)
    if len(raw) != rows:
        notes.append(f"Строк без центра или с размером ≤ 0: {_n(rows - len(raw))} — пропущены.")
        c, d = c[good], d[good]
    lc = frame.local(c)
    del c
    arr = busy(lambda: ll.arrange(frame.grid, lc, d), tick, 0.5, "Сортировка")
    if arr.dups:
        notes.append(f"Повторяющиеся блоки (тот же центр и размер): {_n(arr.dups)}. Оставлена первая строка.")
    lc, ld, sel = lc[arr.order], d[arr.order], raw[arr.order]
    n_out = len(sel)
    tick(0.55, "Запись")
    origin: dict[str, Any] = {"op": "import", "inputs": [], "algo": None, "versions": {},
                              "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}  # fmt: skip
    specs = [pl.spec({**origin, "params": {"format": source.format, "file": label, "column": pl.column,
                                           "empty": list(opts.empty)}}) for pl in plans]  # fmt: skip
    frac = next((pl.name for pl in plans if pl.role == "frac"), None)
    chk = ll.ListChecker([{"name": pl.name, "kind": pl.kind} for pl in plans], frac)
    with ll.ListWriter(store, frame.grid, specs) as writer:
        for a in range(0, n_out, PORTION):
            b = min(n_out, a + PORTION)
            at = np.argsort(sel[a:b], kind="stable")
            ordered = sel[a:b][at]  # ascending: one pass over each file of the spool
            values = {pl.name: spool.take(pl.key, rows, ordered, at) for pl in plans}
            writer.put(arr.idx[a:b], lc[a:b], ld[a:b], values)
            chk.feed(ld[a:b], values)
            tick(0.55 + 0.35 * b / n_out, "Запись")
        tick(0.9, "Проверка перекрытий")
        staged = writer.finish()
    try:
        examples = [{**e, "row": int(raw[e["row"]]), "other_row": int(raw[e["other_row"]])} for e in arr.examples]
        report = chk.finish(arr.idx, lc, ld, dups=arr.dups, dup_examples=examples, rows_of=lambda i: int(sel[i]))
        staged.v2 = {**(staged.v2 or {}), "checks": report.checks}
        parents = int(staged.v2["parents"])
        notes.append(f"Модель импортирована списком блоков: {_n(n_out)} блоков, охватывающая сетка "
                     f"{' × '.join(str(v) for v in frame.grid.counts)} ячеек.")  # fmt: skip
        for f in report.findings:
            if f["severity"] in (bcheck.ERROR, bcheck.WARNING):
                notes.append(f["message"])
                if log is not None:
                    log(f"«{label}»: {f['message']} {f['hint']}".strip(), "warn")
        tick(0.97, "Запись в проект")
        from bf_core.db import dialect
        from bf_core.db.schema import objects

        with dialect.write_tx(engine) as conn:
            bm_id = bm.commit_block_model(conn, store, staged, name=name,
                                          source={"format": source.format, "file": label},
                                          warnings=notes[:MAX_NOTES], folder=folder, state=state, job_id=job_id,
                                          author=author)  # fmt: skip
            final = conn.execute(sa.select(objects.c.name).where(objects.c.id == bm_id)).scalar_one()
    finally:
        staged.discard()
    return Imported(bm_id, str(final), n_out, parents, False, [pl.name for pl in plans], report.checks,
                    notes[:MAX_NOTES], frame.grid.to_meta())  # fmt: skip
