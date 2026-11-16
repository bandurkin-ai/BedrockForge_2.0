"""Jobs `export.project` and `import.project` (ТЗ 7.4). The password travels as the job secret: the scheduler
clears it from the table when a worker takes the job; it is never in the job state or logs."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from bf_core.jobs.context import JobContext, JobError
from bf_core.jobs.registry import handler
from bf_core.projects.lock import ProjectLock

from .container import ArchiveError, BadPassword, TooNew


def _job_error(exc: ArchiveError) -> JobError:
    if isinstance(exc, BadPassword):
        return JobError("bad_password", str(exc), "Проверьте пароль и повторите импорт.")
    if isinstance(exc, TooNew):
        return JobError("archive_too_new", str(exc), "Установите новую версию BedrockForge.")
    return JobError("archive_error", str(exc), "Проверьте файл: он мог быть повреждён при копировании.")


def _size_mb(params: dict[str, Any]) -> int:
    return 512


@handler("export.project", "Экспорт проекта", queue="heavy", estimate_memory=_size_mb)
def export_job(ctx: JobContext) -> dict[str, Any]:
    from .export import export_project

    assert ctx.project_folder is not None
    out = Path(ctx.params["path"])
    try:
        result = export_project(
            ctx.engine(),
            ctx.project_folder,
            out,
            password=ctx.secret or None,
            include_view=bool(ctx.params.get("include_view", True)),
            progress=lambda f, m: ctx.tick(f, m),
            log=lambda t, lvl: ctx.log(t, lvl),
        )
    except ArchiveError as exc:
        raise _job_error(exc) from exc
    except OSError as exc:
        raise JobError(
            "write_failed", f"Не удалось записать файл: {exc.strerror or exc}", "Выберите другую папку."
        ) from exc
    ctx.log(f"Записан файл {out.name}: {result['bytes'] / 2**20:.1f} МБ")
    return result


@handler("import.project", "Импорт проекта", queue="heavy", estimate_memory=_size_mb, instance=True)
def import_job(ctx: JobContext) -> dict[str, Any]:
    from .importer import import_project

    p = ctx.params
    root = Path(p["root"])
    try:
        result = import_project(
            Path(p["path"]),
            root,
            name=str(p["name"]),
            password=ctx.secret or None,
            apply_view=bool(p.get("apply_view", True)),
            progress=lambda f, m: ctx.tick(f, m),
        )
    except ArchiveError as exc:
        raise _job_error(exc) from exc
    replaced = p.get("replace_folder")
    if replaced:
        old = Path(replaced)
        lock = ProjectLock(old / "project.lock")
        if lock.acquire():
            lock.release()
            trash = old.with_name(f".del-{uuid.uuid4().hex}.bfproj")
            old.rename(trash)
            shutil.rmtree(trash, ignore_errors=True)
            ctx.log(f"Заменённый проект удалён: {old.name}")
        else:
            ctx.log("Заменяемый проект открыт в другом окне: он оставлен, импорт создан рядом", "warn")
    ctx.log(f"Создан проект «{result['name']}»: строк {sum(result['rows'].values())}, файлов {result['files']}")
    return result
