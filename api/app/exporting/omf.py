"""Export of project datasets to one OMF v1 file.

Wireframes become surfaces, line layers become line sets (line attributes on their segments),
a regular block model becomes a dense volume grid. A sub-blocked model cannot be written in OMF v1
and is refused with a reason; so is a model whose dense grid would not fit in memory.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.formats.omf import OmfWriter
from app.models import Dataset, LineLayer, Wireframe
from app.storage import blobs
from app.storage.geometry import line_arrays, mesh_arrays

Progress = Callable[[float, str], None]
EXPORT_KINDS = ("wireframes", "lines", "blocks")
# A dense grid of this many cells takes 0.8 GB per numeric attribute while it is written.
MAX_DENSE_CELLS = 100_000_000


class ExportFailed(Exception):
    """The export cannot be made; the message is shown to the user."""


def _no_progress(_f: float, _m: str) -> None:
    pass


def block_grid_problem(d: Dataset) -> str | None:
    """Why a block model cannot be written as an OMF v1 volume, or None."""
    g = (d.meta or {}).get("grid") or {}
    if g.get("subblocked"):
        return "субблочная модель: в OMF v1 есть только регулярная сетка"
    if g.get("rotation"):
        return "модель повёрнута"
    counts = g.get("counts") or []
    if len(counts) != 3 or min(counts) <= 0:
        return "у модели нет описания сетки"
    total = int(np.prod(counts))
    if total > MAX_DENSE_CELLS:
        return f"сетка {counts[0]} × {counts[1]} × {counts[2]} = {total:,} ячеек больше предела {MAX_DENSE_CELLS:,}".replace(",", " ")
    return None


def _block_cells(d: Dataset) -> tuple[np.ndarray, list[float], list[float], list[int]]:
    """Fortran index of every block in its regular grid (u = X fastest)."""
    g = d.meta["grid"]
    origin = np.asarray(g["origin"], np.float64)
    parent = np.asarray(g["parent"], np.float64)
    counts = [int(c) for c in g["counts"]]
    centres = blobs.read_array(d.blobs["centres"], "<f8", 3)
    sizes = blobs.read_array(d.blobs["sizes"], "<f4", 3).astype(np.float64)
    if len(centres) and (np.abs(sizes - parent) > 1e-4 * np.maximum(parent, 1)).any():
        raise ExportFailed(f"«{d.name}»: размеры блоков отличаются от шага сетки — модель не регулярная")
    f = (centres - origin) / parent - 0.5
    ijk = np.rint(f).astype(np.int64)
    if len(centres) and (np.abs(f - ijk) > 1e-4).any():
        raise ExportFailed(f"«{d.name}»: центры блоков не на узлах сетки")
    if len(centres) and ((ijk < 0).any() or (ijk >= np.asarray(counts)).any()):
        raise ExportFailed(f"«{d.name}»: блоки за пределами сетки модели")
    cells = ijk[:, 0] + counts[0] * (ijk[:, 1] + counts[1] * ijk[:, 2])
    if len(np.unique(cells)) != len(cells):
        raise ExportFailed(f"«{d.name}»: в одной ячейке сетки несколько блоков")
    return cells, origin.tolist(), parent.tolist(), counts


def export_omf(session: Session, project_name: str, description: str, datasets: list[Dataset], path: Path,
               progress: Progress = _no_progress) -> tuple[int, list[str]]:
    """Writes the datasets to `path`; returns (elements written, warnings)."""
    warnings: list[str] = []
    with OmfWriter(path, project_name, description) as w:
        for n, d in enumerate(datasets):
            base, share = n / len(datasets), 1 / len(datasets)
            progress(base, f"{d.name}")
            if d.kind == "wireframes":
                items = list(session.scalars(select(Wireframe).where(Wireframe.dataset_id == d.id).order_by(Wireframe.id)))
                for k, m in enumerate(items):
                    progress(base + share * k / max(len(items), 1), f"Каркас {m.name}")
                    v, t = mesh_arrays(m.blobs)
                    w.add_surface(m.name, v, t, m.color)
                if not items:
                    warnings.append(f"«{d.name}»: в наборе нет каркасов")
            elif d.kind == "lines":
                layers = list(session.scalars(select(LineLayer).where(LineLayer.dataset_id == d.id).order_by(LineLayer.id)))
                for k, layer in enumerate(layers):
                    progress(base + share * k / max(len(layers), 1), f"Линии {layer.name}")
                    v, offsets, closed = line_arrays(layer.blobs)
                    lines = [(v[offsets[i]:offsets[i + 1]], bool(closed[i])) for i in range(len(offsets) - 1)]
                    warnings.extend(w.add_lines(layer.name, lines, list(layer.line_attributes or []), layer.color))
                if not layers:
                    warnings.append(f"«{d.name}»: в наборе нет слоёв линий")
            elif d.kind == "blocks":
                problem = block_grid_problem(d)
                if problem:
                    warnings.append(f"Блочная модель «{d.name}» не записана: {problem}")
                    continue
                try:
                    cells, corner, size, counts = _block_cells(d)
                except ExportFailed as exc:
                    warnings.append(f"Блочная модель не записана: {exc}")
                    continue
                columns = []
                for a in d.meta.get("attributes", []):
                    values = blobs.read_array(d.blobs[a["key"]], "<f8" if a["kind"] == "number" else "<i4")
                    columns.append((a["name"], a["kind"], np.asarray(values), a.get("labels"), a.get("unit")))
                w.add_volume(d.name, corner, size, counts, cells, columns, None)
            else:
                warnings.append(f"«{d.name}»: объекты вида «{d.kind}» в OMF не записываются")
        written = w.element_count
        if not written:
            raise ExportFailed("Нечего записать в OMF: " + ("; ".join(warnings) or "наборы пусты"))
    progress(1.0, f"Элементов: {written}")
    return written, warnings
