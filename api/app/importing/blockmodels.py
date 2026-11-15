"""Import of a block model: read, describe the grid, check, store columns in the object store.

The model is a dataset of kind "blocks". Its columns are blobs (dataset.blobs):
- "centres" <f8 (n, 3), "sizes" <f4 (n, 3): exact geometry;
- "render" <f4 (n, 6): centre relative to meta.render_origin and size, for the viewer;
- "a0", "a1", ...: attributes, <f8 numbers or <i4 category codes (−1 empty).
meta holds the description: grid, attributes (name, kind, unit, labels, statistics), checks.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
from sqlalchemy.orm import Session

from app.blocks.shell import exposed_mask
from app.formats.blockmodel import (
    BlockModelError, BlockTable, check_model, describe_grid, guess_unit, read_block_model, stats_of,
)
from app.importing.objects import ImportFailed
from app.models import Dataset
from app.storage import blobs
from app.storage.geometry import extent_of, render_origin

Progress = Callable[[float, str], None]
DENSITY_NAMES = ("SG", "DENSITY", "DENS", "BD", "BULK_DENSITY", "RHO", "ПЛОТНОСТЬ")


def _no_progress(_f: float, _m: str) -> None:
    pass


def default_density(attributes: list[dict]) -> str | None:
    """A density field: by name, preferring one that varies (a constant 2.6 is usually a default)."""
    found = sorted((a for a in attributes if a["kind"] == "number" and a["name"].upper() in DENSITY_NAMES),
                   key=lambda a: DENSITY_NAMES.index(a["name"].upper()))
    varying = [a for a in found if a["stats"].get("count") and a["stats"].get("min") != a["stats"].get("max")]
    pick = (varying or found or [None])[0]
    return pick["key"] if pick else None


def default_grades(attributes: list[dict]) -> list[str]:
    """Grade fields: those with a unit; a "_CUT" (top-cut) variant is preferred over the raw field."""
    graded = [a for a in attributes if a["kind"] == "number" and a.get("unit") in ("g/t", "%")]
    names = {a["name"].upper() for a in graded}
    out = [a for a in graded if not (a["name"].upper() + "_CUT" in names)]
    return [a["key"] for a in out]


def store_block_model(session: Session, dataset: Dataset, t: BlockTable, progress: Progress = _no_progress) -> None:
    progress(0.70, "Сетка и проверки")
    grid, cells, inside = describe_grid(t)
    checks = check_model(t, grid, cells, inside)
    volume = np.prod(t.sizes, axis=1)
    progress(0.80, "Запись столбцов")
    refs = {
        "centres": blobs.put_array(session, t.centres, "<f8"),
        "sizes": blobs.put_array(session, t.sizes, "<f4"),
    }
    origin = render_origin(t.centres)
    render = np.empty((len(t.centres), 6), dtype="<f4")
    step = 1 << 20
    for a in range(0, len(t.centres), step):
        render[a:a + step, :3] = t.centres[a:a + step] - origin
        render[a:a + step, 3:] = t.sizes[a:a + step]
    refs["render"] = blobs.put_array(session, render, "<f4")
    progress(0.78, "Внешняя оболочка модели")
    refs["exposed"] = blobs.put_array(session, exposed_mask(t.centres, t.sizes, grid.origin, grid.parent, grid.counts), "<u1")
    attributes = []
    for i, c in enumerate(t.attributes):
        key = f"a{i}"
        refs[key] = blobs.put_array(session, c.values, "<f8" if c.kind == "number" else "<i4")
        a = {"key": key, "name": c.name, "kind": c.kind, "source": c.source, "stats": stats_of(c, volume)}
        if c.kind == "number":
            a["unit"] = guess_unit(c.name)
        else:
            a["labels"] = c.labels or []
        attributes.append(a)
        progress(0.80 + 0.18 * (i + 1) / max(len(t.attributes), 1), f"Поле {c.name}")
    lo = t.centres - t.sizes / 2
    hi = t.centres + t.sizes / 2
    extent = {"min": lo.min(axis=0).tolist(), "max": hi.max(axis=0).tolist()} if len(lo) else extent_of(lo)
    dataset.meta = {
        **(dataset.meta or {}),
        "format": t.format,
        "block_count": int(len(t.centres)),
        "volume": float(volume.sum()),
        "extent": extent,
        "render_origin": [float(v) for v in origin],
        "grid": {"origin": grid.origin, "parent": grid.parent, "counts": grid.counts, "subblocked": grid.subblocked,
                 "parents_used": grid.parents_used, "rotation": grid.rotation,
                 "from_file": t.grid_hint is not None, "aligned": True},
        "roles": t.roles,
        "attributes": attributes,
        "checks": checks,
        "notes": t.notes,
        "report": {"density": default_density(attributes), "grades": default_grades(attributes)},
    }
    dataset.blobs = {**(dataset.blobs or {}), **refs}


def import_block_model(session: Session, project_id: int, path: Path, filename: str, *, folder: str = "",
                       progress: Progress = _no_progress, keep_source: bool = True) -> Dataset:
    progress(0.02, "Чтение файла")
    try:
        t = read_block_model(path, filename, progress)
    except BlockModelError as exc:
        raise ImportFailed(str(exc)) from exc
    if not len(t.centres):
        raise ImportFailed("В файле нет блоков")
    dataset = Dataset(project_id=project_id, kind="blocks", name=Path(filename).name[:255], folder=folder,
                      meta={"filename": filename}, blobs={})
    session.add(dataset)
    session.flush()
    store_block_model(session, dataset, t, progress)
    if keep_source:
        dataset.blobs = {**dataset.blobs, "source": blobs.put_file(session, path)}
    session.flush()
    progress(1.0, f"Блоков: {len(t.centres)}")
    return dataset
