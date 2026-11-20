"""Block models of the job `import.objects` (ТЗ 7.24; ADR 0017 §2–3): the volumes of an OMF v1 file
(`formats.registry.ObjectsRead.volumes`). The job itself is `objects.py`.

* one at a time: `formats.omf_volume.read_volume` streams the occupied cells of a volume into a
  `blocks.store.CellsWriter` (temporary files of a volume with axes out of the order X, Y, Z — under
  `cache\\import\\<job>\\`, removed by the reader), then a short transaction writes the model as `building` under the
  id of the job (`blocks.store.commit_block_model`: a free name, «name (2)»); the file goes on to the next volume;
* `meta.source` {format, file, element}, `meta.warnings` — what the reader said (cells without values, a grid
  without data) and what the composition left out of the element (data on the nodes, …); the CRS — that of the
  project of the file, if BF2 wrote it; a file with several models puts them into a folder of the tree named
  after it, as its wireframes (`object_meshes.file_folder`);
* a volume that cannot be read (rotated, broken, a grid the store refuses — `ApiError` of `CellsWriter`) is a line
  of the journal and an entry of `skipped`; the others load. Not enough memory (`out_of_memory`) or disk
  (`disk_full`) fails the job: its two attempts (`jobs/registry.py`) start again from nothing;
* in the final transaction of the job (`log_blocks`) every model gets its row of `import_log`, which owns the
  source file (owner `import`, role `source`) — one blob for all the objects of the file.

Until the final transaction everything is `building`: a cancel or a failure leaves nothing (`jobs.store.drop_partial`;
the staged Parquet files and the temporary files of the reader are removed here and by the reader).
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import import_log, objects
from bf_core.formats.errors import FormatError
from bf_core.jobs.context import JobContext, JobError

from .object_files import MAX_NOTES, Group, Tick, n_text

__all__ = ["Blocks", "log_blocks", "memory", "write_volumes"]

BLOCK_MODEL = "block_model"  # `import_log.counts.kind` of a model; the kind of its object
BLOCK_MODELS = "block_models"  # the kind of a file entry of the result
TARGET = "objects"  # `import_log.target`
FATAL = ("out_of_memory", "disk_full")  # the job fails (and is tried again), not the volume


@dataclass
class Blocks:
    """The block models of a file written as `building`."""

    ids: list[int] = field(default_factory=list)
    counts: list[dict[str, Any]] = field(default_factory=list)  # `import_log.counts` of every model
    skipped: list[dict[str, str]] = field(default_factory=list)  # {name, code, message, hint}

    @property
    def cells(self) -> int:
        return sum(int(c["cells"]) for c in self.counts)


VERTEX_RATIO = 2  # inflated / compressed bytes of float64 vertices (Leapfrog, BF2: 1.05–1.55)
INDEX_RATIO = 8  # of int64 triangles (≈ 6)


def _mesh_counts(e: Any) -> tuple[int, int]:
    """(vertices, triangles) of a surface: from the JSON (a grid), else guessed from its compressed blocks."""
    from bf_core.formats.omf_file import MAX_MESH_TRIANGLES, MAX_MESH_VERTICES

    if e.points is not None and e.triangles is not None:
        return int(e.points), int(e.triangles)
    packed = e.packed or {}
    points = min(VERTEX_RATIO * int(packed.get("vertices", 0)) // 24, MAX_MESH_VERTICES)
    triangles = min(INDEX_RATIO * int(packed.get("triangles", 0)) // 24, MAX_MESH_TRIANGLES)
    return points, triangles


def memory(path: Any, size: int) -> int:
    """Bytes an OMF file needs (`objects._memory`), from its JSON: the biggest surface staged as a wireframe
    (about 12 × its arrays; a triangulated surface — by the sizes of its blocks) or the portions of the biggest
    volume; the file × 6 when the JSON cannot be read. The composition of the wizard is not read again
    (`omf_compose.described`)."""
    from bf_core.formats.omf_compose import described

    try:
        comp = described(path)
    except (FormatError, OSError):
        return size * 6
    meshes = [_mesh_counts(e) for e in comp.elements if e.kind == "surface" and e.loads]
    mesh = max((12 * (24 * p + 12 * t) for p, t in meshes), default=0)
    volume = max(((64 + 48 * len(e.data)) << 20 for e in comp.elements if e.kind == "volume" and e.loads), default=0)
    return max(mesh, volume, size)


def _element_notes(read: Any, key: str) -> list[str]:
    from bf_core.formats.registry import omf_note

    return [omf_note({}, x, own=True) for x in read.left_out if x["key"] == key]


def write_volumes(
    ctx: JobContext, engine: sa.Engine, store: Any, g: Group, read: Any, tick: Tick, folder: str | None
) -> Blocks:
    """The volumes of a read OMF file as `building` block models of this job (see the module docstring)."""
    from bf_core.blocks import store as bm
    from bf_core.drillholes.edit import author
    from bf_core.formats.omf_read import open_reader
    from bf_core.formats.omf_volume import read_volume
    from bf_core.formats.str_layers import scaled

    label, who = g.main.name, author()
    crs = read.crs.wkt if read.crs is not None else None
    keys = {e.key for e in read.volumes}
    total = max(sum(int(e.cells or 0) for e in read.volumes), 1)
    out = Blocks()
    assert ctx.project_folder is not None
    tmp = ctx.project_folder / "cache" / "import" / str(ctx.id)
    done = 0
    try:
        with open_reader(g.main) as reader:
            for vol in reader.volumes(keys):
                share = scaled(tick, done / total, (done + int(vol.entry.cells or 0)) / total)
                done += int(vol.entry.cells or 0)
                tmp.mkdir(parents=True, exist_ok=True)
                try:  # a cancel inside (its last report too) discards the staged cells there
                    vr = read_volume(reader.file, vol, lambda grid, fields: bm.CellsWriter(store, grid, fields),
                                     tmp_dir=tmp, progress=share)  # fmt: skip
                except (FormatError, ApiError) as exc:  # ApiError: `CellsWriter` refuses the grid or the fields
                    if exc.code in FATAL:
                        raise JobError(exc.code, f"«{label}»: {exc.message}", exc.hint) from exc
                    out.skipped.append({"name": vol.name, "code": exc.code, "message": exc.message, "hint": exc.hint})
                    ctx.log(f"{label}: объём «{vol.name}» не загружен: {exc.message} {exc.hint}".strip(), "error")
                    continue
                notes = [*vr.warnings, *_element_notes(read, vol.key)][:MAX_NOTES]
                try:
                    with dialect.write_tx(engine) as conn:
                        bm_id = bm.commit_block_model(
                            conn, store, vr.staged, name=vr.name, source=vr.source, warnings=notes, folder=folder,
                            state="building", job_id=ctx.id, crs=crs, author=who,
                        )  # fmt: skip
                        name = conn.execute(sa.select(objects.c.name).where(objects.c.id == bm_id)).scalar_one()
                finally:
                    vr.staged.discard()
                out.ids.append(bm_id)
                fields = [f["name"] for f in vr.fields]
                out.counts.append({"kind": BLOCK_MODEL, "name": name, "cells": vr.cells, "fields": len(fields)})
                for note in notes:
                    ctx.log(f"{label}: блочная модель «{name}»: {note}", "warn")
                grid = " × ".join(str(c) for c in vr.grid["counts"])
                named = f" ({', '.join(fields[:10])})" if fields else ""
                ctx.log(f"{label}: блочная модель «{name}» — сетка {grid}, ячеек {n_text(vr.cells)}, "
                        f"полей {len(fields)}{named}", "info")  # fmt: skip
    except MemoryError as exc:
        raise JobError("out_of_memory", f"«{label}»: не хватило памяти на блочную модель.",
                       "Закройте другие программы или разделите модель на части.") from exc  # fmt: skip
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if len(out.ids) > 1:
        from .object_meshes import file_folder

        place = file_folder(folder, label)
        with dialect.write_tx(engine) as conn:
            conn.execute(sa.update(objects).where(objects.c.id.in_(out.ids)).values(folder=place))
    return out


def entry(label: str, fmt: str, blocks: Blocks) -> dict[str, Any]:
    """The entry of the result of the job for the block models of a file."""
    return {
        "file": label, "format": fmt, "kind": BLOCK_MODELS, "object_id": blocks.ids[0], "object_ids": list(blocks.ids),
        "block_models": len(blocks.ids), "cells": blocks.cells, "skipped": len(blocks.skipped),
    }  # fmt: skip


def log_blocks(
    conn: sa.Connection,
    blocks: Blocks,
    *,
    label: str,
    fmt: str,
    settings: Mapping[str, Any],
    sha: str,
    job_id: int,
    now: Any,
) -> dict[str, Any]:
    """In the final transaction of the job: a row of `import_log` per model; each owns the source file `sha`
    (owner `import`). Returns the entry of the result."""
    from bf_core.storage.blobs import add_owners

    owners: list[tuple[str, str, int, str]] = []
    insert = sa.insert(import_log)
    for bm_id, counts in zip(blocks.ids, blocks.counts, strict=True):
        row = dict(
            object_id=bm_id, job_id=job_id, file_name=label, format=fmt, target=TARGET, mapping=dict(settings),
            counts=counts, sha256=sha, created_at=now,
        )  # fmt: skip
        log_id = int(conn.execute(insert, row).inserted_primary_key[0])  # type: ignore[index]
        owners.append((sha, "import", log_id, "source"))
    add_owners(conn, owners)
    return entry(label, fmt, blocks)
