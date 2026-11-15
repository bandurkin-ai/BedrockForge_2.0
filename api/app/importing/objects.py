"""Import of project objects from files: wireframes, line layers, rasters.

The same functions serve the background import job (app/jobs/handlers.py) and the small-file
endpoints of routers/datasets.py. They add rows to `session` and leave the commit to the caller.
Errors meant for the user are `ImportFailed`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.formats.lines import LineFormatError, line_stats, pack, read_lines
from app.formats.rasters import (
    Georef, RasterFormatError, corners, corners3d, is_vertical, pair_files, pixel_size, plane_azimuth,
    read_raster, rotation_deg, vertical_georef,
)
from app.formats.wireframes import WireframeFormatError, mesh_stats, read_wireframes
from app.models import Dataset, LineLayer, Raster, Wireframe
from app.storage import blobs, geometry

# Distinct colours for objects that carry none in the file.
PALETTE = ["#e8873a", "#4f9dde", "#5fbf6a", "#c06fd8", "#e0c040", "#e05d6f", "#46c7c0", "#9a8f7a"]

Progress = Callable[[float, str], None]


class ImportFailed(Exception):
    """The file cannot be imported; the message is shown to the user."""


def _no_progress(_fraction: float, _message: str) -> None:
    pass


def used_colors(session: Session, project_id: int) -> int:
    lines = session.scalar(select(func.count()).select_from(LineLayer).join(Dataset).where(Dataset.project_id == project_id)) or 0
    meshes = session.scalar(select(func.count()).select_from(Wireframe).join(Dataset).where(Dataset.project_id == project_id)) or 0
    return lines + meshes


def _source_blob(session: Session, path: Path, keep_source: bool) -> dict:
    """Keeps the imported file itself with the object (re-import, audit trail). The file is
    moved into the store, not copied: `path` must be in the store's file system (blobs.tmp_dir(),
    the uploads folder) and is gone afterwards."""
    if not keep_source:
        return {}
    return {"source": blobs.put_file(session, path)}


def jsonable(attributes: dict) -> dict:
    out = {}
    for k, v in attributes.items():
        if isinstance(v, float) and not np.isfinite(v):
            v = None
        out[str(k)] = v if isinstance(v, (str, int, float, bool)) or v is None else str(v)
    return out


def add_wireframe(session: Session, dataset_id: int, name: str, color: str, vertices: np.ndarray,
                  triangles: np.ndarray, stats: dict | None = None, progress: Progress = _no_progress) -> Wireframe:
    v = np.ascontiguousarray(vertices, dtype="<f8").reshape(-1, 3)
    t = np.ascontiguousarray(triangles, dtype="<i4").reshape(-1, 3)
    if stats is None:
        stats = mesh_stats(v, t)
    stored = geometry.store_mesh(session, v, t, derived=True, progress=progress)
    w = Wireframe(
        dataset_id=dataset_id, name=name[:255], color=color, point_count=len(v), triangle_count=len(t),
        extent=stored.extent, origin=stored.origin, blobs=stored.blobs, render=stored.render or {}, stats=stats,
    )
    session.add(w)
    return w


def import_wireframes(session: Session, project_id: int, path: Path, filename: str, *,
                      folder: str = "", progress: Progress = _no_progress, keep_source: bool = True) -> Dataset:
    suffix = Path(filename).suffix.lower()
    progress(0.05, "Чтение файла")
    try:
        fmt, meshes = read_wireframes(path, suffix)
    except WireframeFormatError as exc:
        raise ImportFailed(str(exc)) from exc
    dataset = Dataset(project_id=project_id, kind="wireframes", name=Path(filename).name[:255], folder=folder,
                      meta={"format": fmt, "filename": filename}, blobs={})
    session.add(dataset)
    session.flush()
    used = used_colors(session, project_id)
    total = sum(len(m.triangles) for m in meshes) or 1
    done = 0
    for i, mesh in enumerate(meshes):
        share = 0.55 * len(mesh.triangles) / total
        base = 0.4 + 0.55 * done / total
        progress(base, f"Каркас {i + 1} из {len(meshes)}: {mesh.name}, проверка")
        stats = mesh_stats(mesh.vertices, mesh.triangles)
        if mesh.attributes:
            stats["attributes"] = mesh.attributes

        def step(fraction: float, message: str, base=base, share=share, name=mesh.name) -> None:
            progress(base + share * (0.2 + 0.8 * fraction), f"{name}: {message}")

        add_wireframe(session, dataset.id, mesh.name, mesh.color or PALETTE[(used + i) % len(PALETTE)],
                      mesh.vertices, mesh.triangles, stats, progress=step)
        done += len(mesh.triangles)
    session.flush()
    queue_derive(session, project_id, dataset)
    # Last: once moved, the source is gone from the upload folder, so nothing may fail after it.
    dataset.blobs = _source_blob(session, path, keep_source)
    session.flush()
    return dataset


def queue_derive(session: Session, project_id: int, dataset: Dataset) -> None:
    """Checks and levels of detail run as their own job once the import is committed."""
    from app.jobs import queue

    ids = list(session.scalars(select(Wireframe.id).where(Wireframe.dataset_id == dataset.id).order_by(Wireframe.id)))
    if ids:
        queue.enqueue(session, "wireframes.derive", {"wireframe_ids": ids}, project_id=project_id,
                      title=f"Проверка и копии для показа: {dataset.name}"[:255], max_attempts=2)


def add_line_layer(session: Session, dataset_id: int, name: str, color: str, lines: list) -> LineLayer:
    vertices, offsets, closed = pack(lines)
    stored = geometry.store_lines(session, vertices, offsets, closed)
    layer = LineLayer(
        dataset_id=dataset_id, name=name[:255], color=color, line_count=len(lines), point_count=len(vertices),
        extent=stored.extent, origin=stored.origin, blobs=stored.blobs, stats=line_stats(lines),
        line_attributes=[jsonable(line.attributes) for line in lines],
    )
    session.add(layer)
    return layer


def import_lines(session: Session, project_id: int, path: Path, filename: str, *,
                 folder: str = "", progress: Progress = _no_progress, keep_source: bool = True) -> Dataset:
    suffix = Path(filename).suffix.lower()
    progress(0.05, "Чтение файла")
    try:
        fmt, layers = read_lines(path, suffix)
    except LineFormatError as exc:
        raise ImportFailed(str(exc)) from exc
    dataset = Dataset(project_id=project_id, kind="lines", name=Path(filename).name[:255], folder=folder,
                      meta={"format": fmt, "filename": filename}, blobs={})
    session.add(dataset)
    session.flush()
    used = used_colors(session, project_id)
    for i, layer in enumerate(layers):
        progress(0.5 + 0.45 * i / max(len(layers), 1), f"Слой {i + 1} из {len(layers)}: {layer.name}")
        add_line_layer(session, dataset.id, layer.name, layer.color or PALETTE[(used + i) % len(PALETTE)], layer.lines)
    dataset.blobs = _source_blob(session, path, keep_source)
    session.flush()
    return dataset


# ---- rasters


def raster_placement(g: Georef, width: int, height: int, checks: list, notes: list) -> tuple[dict, dict]:
    """The stored georeference (plan or vertical) and the plan extent of a raster."""
    pts = corners(g.affine, width, height)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    vertical = is_vertical(g, width, height)
    georef = {
        "affine": g.affine, "affine_z": g.affine_z if vertical else None, "source": g.source, "crs": g.crs,
        "placement": "vertical" if vertical else "plan",
        "pixel_size": round(pixel_size(g.affine, g.affine_z if vertical else None), 4),
        "rotation": round(rotation_deg(g.affine), 4),
        "corners": [[round(x, 3), round(y, 3)] for x, y in pts],
        "checks": checks, "notes": notes,
    }
    extent = {"min": [round(min(xs), 2), round(min(ys), 2)], "max": [round(max(xs), 2), round(max(ys), 2)]}
    if vertical:
        c3 = corners3d(g, width, height)
        zs = [p[2] for p in c3]
        georef["corners3d"] = [[round(v, 3) for v in p] for p in c3]
        georef["azimuth"] = round(plane_azimuth(g.affine), 3)
        extent = {"min": [*extent["min"], round(min(zs), 2)], "max": [*extent["max"], round(max(zs), 2)]}
    return georef, extent


@dataclass(frozen=True)
class SectionPlacement:
    """A raster standing in a vertical plane: left edge (x1, y1), right edge (x2, y2), z_top down to z_bottom."""

    x1: float
    y1: float
    x2: float
    y2: float
    z_top: float
    z_bottom: float


def shift_raster_z(georef: dict, extent: dict, elevation: float | None, dz: float) -> tuple[dict, dict, float | None]:
    """The raster raised (dz > 0) or lowered by dz metres: its plan elevation, or every corner of a vertical one."""
    if not dz:
        return georef, extent, elevation
    georef = dict(georef)
    extent = {k: list(v) for k, v in extent.items()}
    if georef.get("corners3d"):
        georef["corners3d"] = [[p[0], p[1], round(p[2] + dz, 3)] for p in georef["corners3d"]]
        if georef.get("affine_z"):
            g, h, i = georef["affine_z"]
            georef["affine_z"] = [g, h, i + dz]
        for k in ("min", "max"):
            if len(extent.get(k, [])) == 3:
                extent[k][2] = round(extent[k][2] + dz, 2)
        return georef, extent, elevation
    return georef, extent, None if elevation is None else round(elevation + dz, 3)


def section_fallback(section: SectionPlacement | None):
    if section is None:
        return None
    s = section
    return lambda w, h: vertical_georef(w, h, s.x1, s.y1, s.x2, s.y2, s.z_top, s.z_bottom)


def check_raster_names(names: list[str]) -> dict[str, list[str]]:
    groups, unmatched = pair_files(names)
    if not groups:
        raise ImportFailed("Среди файлов нет изображения (jpg, png, tif, bmp)")
    if unmatched:
        raise ImportFailed(f"Файлы привязки без изображения с тем же именем: {', '.join(unmatched)}")
    return groups


def _decode_sidecar(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1251", errors="replace")


def import_rasters(session: Session, project_id: int, files: dict[str, Path], *,
                   section: SectionPlacement | None = None, folder: str = "",
                   elevation: float | None = None, z_shift: float = 0.0,
                   progress: Progress = _no_progress) -> list[Dataset]:
    """`files`: original file name -> path. Images pair with their georeference files by name.

    Z level: `elevation` sets the height of plan images (instead of the one in the georeference file);
    `z_shift` then raises or lowers every image, vertical ones too."""
    groups = check_raster_names(list(files))
    fallback = section_fallback(section)
    created: list[Dataset] = []
    for n, (image_name, sidecar_names) in enumerate(groups.items()):
        progress(0.05 + 0.9 * n / len(groups), f"Изображение {image_name}")
        sidecars = {Path(s).suffix.lower(): _decode_sidecar(files[s].read_bytes()) for s in sidecar_names}
        try:
            r = read_raster(files[image_name], image_name, sidecars, fallback)
        except RasterFormatError as exc:
            raise ImportFailed(str(exc)) from exc
        if r.georef.source == "Вручную по разрезу":
            r.notes.append("Файла привязки нет: изображение поставлено в плоскость линии разреза, уточните края и высоты в свойствах")
        georef, extent = raster_placement(r.georef, r.width, r.height, r.checks, r.notes)
        z = r.georef.elevation
        if elevation is not None and not georef.get("corners3d"):
            if z is not None and abs(z - elevation) > 1e-9:
                r.notes.append(f"Высота из файла привязки {z:g} м заменена на {elevation:g} м")
            z = elevation
        if z_shift:
            if z is None and not georef.get("corners3d"):
                r.notes.append("Сдвиг по Z не применён: у растра нет высоты (он ляжет под самый низкий объект); задайте высоту")
            else:
                georef, extent, z = shift_raster_z(georef, extent, z, z_shift)
                r.notes.append(f"Сдвиг по Z: {z_shift:+g} м")
        georef["notes"] = r.notes
        dataset = Dataset(project_id=project_id, kind="rasters", name=Path(image_name).name[:255], folder=folder,
                          meta={"format": Path(image_name).suffix.lstrip(".").upper(), "filename": image_name,
                                "sidecars": sidecar_names})
        session.add(dataset)
        session.flush()
        session.add(Raster(
            dataset_id=dataset.id, name=Path(image_name).stem[:255], width=r.width, height=r.height,
            georef=georef, extent=extent, elevation=z, opacity=1.0,
            blobs={"image": blobs.put_bytes(session, r.image, media_type=r.mime)}, mime=r.mime,
        ))
        created.append(dataset)
    session.flush()
    return created
