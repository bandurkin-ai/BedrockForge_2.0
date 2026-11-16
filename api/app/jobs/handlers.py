"""Job handlers. Each gets (ctx, params) and returns a JSON-able result."""

from __future__ import annotations

from sqlalchemy import delete, select

from app.importing.blockmodels import import_block_model
from app.importing.objects import ImportFailed, SectionPlacement, import_lines, import_rasters, import_wireframes
from app.importing.omf import attach_source, import_omf
from app.jobs.worker import JobContext, JobError, handler
from app.models import Dataset, Project, Upload
from app.storage import blobs, gc


def _uploads(ctx: JobContext, project_id: int, ids: list[str]) -> list[Upload]:
    from app.routers.uploads import part_path

    rows = {u.id: u for u in ctx.session.scalars(select(Upload).where(Upload.id.in_(ids)))}
    out = []
    for upload_id in ids:
        u = rows.get(upload_id)
        if u is None or u.project_id != project_id:
            raise JobError("Загруженный файл не найден: загрузите его ещё раз")
        if u.status != "complete":
            raise JobError(f"Файл «{u.filename}» загружен не полностью")
        if not part_path(u.id).is_file():
            raise JobError(f"Файл «{u.filename}» пропал из хранилища: загрузите его ещё раз")
        out.append(u)
    return out


@handler("import.objects")
def import_objects(ctx: JobContext, params: dict) -> dict:
    """params: project_id, kind (wireframes | lines | rasters | blocks | omf), uploads [id], folder, section."""
    from app.routers.uploads import part_path

    project_id = int(params["project_id"])
    kind = params["kind"]
    folder = params.get("folder") or ""
    uploads = _uploads(ctx, project_id, list(params["uploads"]))
    ctx.log(f"Файлов: {len(uploads)}, {sum(u.size for u in uploads) / 1e6:.1f} МБ")
    created: list[int] = []
    try:
        if kind == "rasters":
            section = SectionPlacement(**params["section"]) if params.get("section") else None
            files = {u.filename: part_path(u.id) for u in uploads}
            datasets = import_rasters(ctx.session, project_id, files, section=section, folder=folder,
                                      elevation=params.get("elevation"), z_shift=float(params.get("z_shift") or 0.0),
                                      progress=ctx.progress)
            created = [d.id for d in datasets]
        elif kind in ("wireframes", "lines", "blocks"):
            read = {"wireframes": import_wireframes, "lines": import_lines, "blocks": import_block_model}[kind]
            imported = []
            for i, u in enumerate(uploads):
                def progress(fraction: float, message: str, i=i, name=u.filename) -> None:
                    ctx.progress((i + fraction) / len(uploads), f"{name}: {message}")

                dataset = read(ctx.session, project_id, part_path(u.id), u.filename, folder=folder,
                               progress=progress, keep_source=False)
                imported.append((dataset, u))
                ctx.log(f"{u.filename}: прочитан ({dataset.meta.get('format')})")
            # Sources move into the store only now: after this nothing can fail or be cancelled,
            # so a cancelled job leaves the uploads in place for another attempt.
            for dataset, u in imported:
                dataset.blobs = {**(dataset.blobs or {}), "source": blobs.put_file(ctx.session, part_path(u.id))}
                created.append(dataset.id)
        elif kind == "omf":
            # One OMF file gives several datasets; they all refer to the file as their source.
            imported_omf = []
            for i, u in enumerate(uploads):
                def progress(fraction: float, message: str, i=i, name=u.filename) -> None:
                    ctx.progress((i + fraction) / len(uploads), f"{name}: {message}")

                datasets, warnings = import_omf(ctx.session, project_id, part_path(u.id), u.filename, folder=folder,
                                                progress=progress, keep_source=False)
                imported_omf.append((datasets, u))
                kinds = ", ".join(f"{d.kind} «{d.name}»" for d in datasets)
                ctx.log(f"{u.filename}: прочитан (OMF v1), наборов {len(datasets)}: {kinds}")
                for w in warnings:
                    ctx.log(f"{u.filename}: {w}", "warning")
            for datasets, u in imported_omf:
                attach_source(ctx.session, datasets, part_path(u.id))
                created.extend(d.id for d in datasets)
        else:
            raise JobError(f"Неизвестный тип объекта «{kind}»")
    except ImportFailed as exc:
        raise JobError(str(exc)) from exc
    ctx.session.execute(delete(Upload).where(Upload.id.in_([u.id for u in uploads])))
    return {"dataset_ids": created, "kind": kind}


@handler("export.omf")
def export_omf_file(ctx: JobContext, params: dict) -> dict:
    """params: project_id, dataset_ids [id], filename. The file lands in DATA_DIR/exports/<job id>.omf;
    GET /api/jobs/{id}/download gives it under `filename`."""
    import os

    from app.exporting.files import export_path
    from app.exporting.omf import ExportFailed, export_omf

    project_id = int(params["project_id"])
    project = ctx.session.get(Project, project_id)
    if project is None:
        raise JobError("Проект удалён")
    ids = [int(i) for i in params.get("dataset_ids") or []]
    found = {d.id: d for d in ctx.session.scalars(select(Dataset).where(Dataset.id.in_(ids), Dataset.project_id == project_id))}
    datasets = [found[i] for i in ids if i in found]
    missing = len(ids) - len(datasets)
    if not datasets:
        raise JobError("Выбранных наборов нет в проекте")
    if missing:
        ctx.log(f"Наборов удалено после запуска: {missing}", "warning")
    ctx.log(f"Наборов: {len(datasets)} — " + ", ".join(f"«{d.name}»" for d in datasets))
    tmp = blobs.tmp_path(".omf")
    try:
        written, warnings = export_omf(ctx.session, project.name, project.description or "", datasets, tmp,
                                       progress=lambda f, m: ctx.progress(f, m))
        ctx.check_cancel()
        dest = export_path(ctx.job_id, ".omf")
        os.replace(tmp, dest)
    except ExportFailed as exc:
        raise JobError(str(exc)) from exc
    finally:
        tmp.unlink(missing_ok=True)
    for w in warnings:
        ctx.log(w, "warning")
    size = dest.stat().st_size
    ctx.log(f"Записано элементов: {written}, {size / 1e6:.2f} МБ")
    return {"filename": params.get("filename") or f"{project.name}.omf", "size": size, "elements": written,
            "warnings": warnings, "download": f"/api/jobs/{ctx.job_id}/download"}


@handler("storage.gc")
def collect_garbage(ctx: JobContext, params: dict) -> dict:
    report = gc.collect(ctx.session)
    return report.__dict__


@handler("wireframes.derive")
def derive_wireframes(ctx: JobContext, params: dict) -> dict:
    """After import: normal orientation, self-intersections and levels of detail of every wireframe.
    The wireframe can be viewed meanwhile; results are added to its stats and display structure."""
    from sqlalchemy import update

    from app.geo.checks import orientation_conflicts, self_intersections
    from app.models import Wireframe
    from app.storage.geometry import build_lods, mesh_arrays

    ids = [int(i) for i in params.get("wireframe_ids", [])]
    done = []
    for k, wid in enumerate(ids):
        w = ctx.session.get(Wireframe, wid)
        if w is None:
            continue  # deleted meanwhile

        def step(fraction: float, message: str, k=k, name=w.name) -> None:
            ctx.progress((k + fraction) / len(ids), f"{name}: {message}")

        v, t = mesh_arrays(w.blobs)
        step(0.0, "согласованность нормалей")
        conflicts = orientation_conflicts(t, len(v))
        step(0.05, "самопересечения")
        crossings = self_intersections(v, t)
        refs, lods = build_lods(ctx.session, v, t, w.origin, progress=lambda f, m, step=step: step(0.4 + 0.6 * f, m))
        stats = {**(w.stats or {}), "orientation_conflicts": conflicts, "self_intersections": crossings, "checked": True}
        # A plain UPDATE: a wireframe deleted while this ran is simply skipped.
        ctx.session.execute(update(Wireframe).where(Wireframe.id == wid).values(
            stats=stats, blobs={**w.blobs, **refs}, render={**(w.render or {}), "lods": lods}))
        if crossings["pairs"]:
            ctx.log(f"{w.name}: самопересечений {crossings['pairs']}", "warning")
        if conflicts:
            ctx.log(f"{w.name}: рёбер с несогласованными нормалями {conflicts}", "warning")
        done.append(wid)
    return {"wireframe_ids": done}


@handler("geometry.operation")
def geometry_operation(ctx: JobContext, params: dict) -> dict:
    """params: project_id, op, params, name, folder — an operation on lines and wireframes
    (app/ops/operations.py). The result is a new object of the tree; sources are left as they are."""
    from app.ops.operations import OpError, run_operation

    try:
        result = run_operation(ctx.session, int(params["project_id"]), params["op"], params.get("params") or {},
                               name=params.get("name"), folder=params.get("folder"),
                               progress=lambda f, m="": ctx.progress(f, m or None))
    except OpError as exc:
        raise JobError(str(exc)) from exc
    for warning in result.get("warnings", []):
        ctx.log(warning, "warning")
    return result
