"""Import of an OMF v1 file: one file gives several datasets.

Every surface goes into one wireframes dataset, every line set is a layer of one lines dataset,
every volume is a block model dataset of its own. What has no counterpart in BedrockForge yet
(points, textures, data on vertices and faces) is listed in `meta.warnings` of the datasets and in
the job journal; nothing is dropped silently.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from sqlalchemy.orm import Session

from app.formats.omf import FORMAT_NAME, OmfFormatError, read_omf
from app.formats.wireframes import mesh_stats
from app.importing.blockmodels import store_block_model
from app.importing.objects import PALETTE, ImportFailed, add_line_layer, add_wireframe, queue_derive, used_colors
from app.models import Dataset
from app.storage import blobs

Progress = Callable[[float, str], None]


def _no_progress(_f: float, _m: str) -> None:
    pass


def import_omf(session: Session, project_id: int, path: Path, filename: str, *, folder: str = "",
               progress: Progress = _no_progress, keep_source: bool = True) -> tuple[list[Dataset], list[str]]:
    """Returns the created datasets (not committed) and the warnings of the reader."""
    progress(0.02, "Чтение файла OMF")
    try:
        content = read_omf(path, progress=lambda f, m: progress(0.02 + 0.38 * f, m))
    except OmfFormatError as exc:
        raise ImportFailed(str(exc)) from exc
    if not (content.meshes or content.line_layers or content.blocks):
        reasons = "; ".join(content.warnings[:5])
        raise ImportFailed("В файле OMF нет объектов, которые можно загрузить" + (f": {reasons}" if reasons else ""))

    stem = Path(filename).name
    meta = {"format": FORMAT_NAME, "filename": filename, "omf_project": content.project, "warnings": content.warnings}
    if content.description:
        meta["omf_description"] = content.description
    created: list[Dataset] = []
    used = used_colors(session, project_id)

    if content.meshes:
        d = Dataset(project_id=project_id, kind="wireframes", name=stem[:255], folder=folder, meta=dict(meta), blobs={})
        session.add(d)
        session.flush()
        total = sum(len(m.triangles) for m in content.meshes) or 1
        done = 0
        for i, mesh in enumerate(content.meshes):
            base = 0.4 + 0.25 * done / total
            share = 0.25 * len(mesh.triangles) / total
            progress(base, f"Каркас {i + 1} из {len(content.meshes)}: {mesh.name}")
            stats = mesh_stats(mesh.vertices, mesh.triangles)
            if mesh.attributes:
                stats["attributes"] = mesh.attributes

            def step(fraction: float, message: str, base=base, share=share, name=mesh.name) -> None:
                progress(base + share * fraction, f"{name}: {message}")

            add_wireframe(session, d.id, mesh.name, mesh.color or PALETTE[(used + i) % len(PALETTE)],
                          mesh.vertices, mesh.triangles, stats, progress=step)
            done += len(mesh.triangles)
        used += len(content.meshes)
        session.flush()
        queue_derive(session, project_id, d)
        created.append(d)

    if content.line_layers:
        progress(0.66, "Линии")
        d = Dataset(project_id=project_id, kind="lines", name=stem[:255], folder=folder, meta=dict(meta), blobs={})
        session.add(d)
        session.flush()
        for i, layer in enumerate(content.line_layers):
            if layer.lines:
                add_line_layer(session, d.id, layer.name, layer.color or PALETTE[(used + i) % len(PALETTE)], layer.lines)
        session.flush()
        created.append(d)

    for k, (name, table, _color, units) in enumerate(content.blocks):
        lo = 0.7 + 0.28 * k / len(content.blocks)
        span = 0.28 / len(content.blocks)
        title = name if len(content.blocks) == 1 and name == Path(filename).stem else f"{Path(filename).stem}: {name}"
        d = Dataset(project_id=project_id, kind="blocks", name=title[:255], folder=folder,
                    meta={**meta, "omf_element": name}, blobs={})
        session.add(d)
        session.flush()
        # store_block_model reports 0.7–0.98 of its own work.
        store_block_model(session, d, table, lambda f, m, lo=lo, span=span: progress(lo + span * max(0.0, f - 0.7) / 0.28, m))
        if units:
            attrs = [dict(a, unit=units[a["name"]]) if a["name"] in units and a["kind"] == "number" else a
                     for a in d.meta["attributes"]]
            d.meta = {**d.meta, "attributes": attrs}
        created.append(d)

    session.flush()
    if keep_source:
        attach_source(session, created, path)
    progress(1.0, f"Наборов: {len(created)}")
    return created, content.warnings


def attach_source(session: Session, datasets: list[Dataset], path: Path) -> None:
    """The file goes into the store once (it is moved, so this is the last step) and every dataset refers to it."""
    sha = blobs.put_file(session, path)
    for d in datasets:
        d.blobs = {**(d.blobs or {}), "source": sha}
    session.flush()
