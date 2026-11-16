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
from bf_core.projects.project import SUFFIX, read_manifest

from .container import ArchiveError, BadPassword, TooNew


def _job_error(exc: ArchiveError) -> JobError:
    if isinstance(exc, BadPassword):
        return JobError("bad_password", str(exc), "Проверьте пароль и повторите импорт.")
    if isinstance(exc, TooNew):
        return JobError("archive_too_new", str(exc), "Установите новую версию BedrockForge.")
    return JobError("archive_error", str(exc), "Проверьте файл: он мог быть повреждён при копировании.")


def _size_mb(params: dict[str, Any]) -> int:
    return 512


def _password(ctx: JobContext) -> str | None:
    """The secret is taken once: a retry after a worker crash has no password — never write a plain file then."""
    if ctx.params.get("encrypted") and not ctx.secret:
        raise JobError(
            "password_lost",
            "Пароль задания не сохраняется, а исполнитель был перезапущен.",
            "Запустите задание заново и введите пароль.",
        )
    return ctx.secret or None


@handler("export.project", "Экспорт проекта", queue="heavy", estimate_memory=_size_mb)
def export_job(ctx: JobContext) -> dict[str, Any]:
    from .export import export_project

    assert ctx.project_folder is not None
    out = Path(ctx.params["path"])
    password = _password(ctx)
    try:
        result = export_project(
            ctx.engine(),
            ctx.project_folder,
            out,
            password=password,
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


def _replaced_folder(root: Path, folder: str, uid: str) -> Path | None:
    """The project to replace, checked: a `.bfproj` folder right in the projects folder with that uid."""
    old = Path(folder)
    try:
        inside = old.resolve().parent == root.resolve()
    except OSError:
        return None
    manifest = read_manifest(old) if inside and old.name.endswith(SUFFIX) else None
    return old if manifest and manifest.get("uid") == uid else None


@handler("import.project", "Импорт проекта", queue="heavy", estimate_memory=_size_mb, instance=True)
def import_job(ctx: JobContext) -> dict[str, Any]:
    from .importer import find_imported, import_project

    p = ctx.params
    root = Path(p["root"])
    token = str(p["token"])
    result = find_imported(root, token)
    if result is not None:  # an earlier attempt finished the project and died before reporting
        ctx.log("Проект уже создан предыдущей попыткой")
    else:
        password = _password(ctx)
        try:
            result = import_project(
                Path(p["path"]),
                root,
                name=str(p["name"]),
                password=password,
                apply_view=bool(p.get("apply_view", True)),
                token=token,
                progress=lambda f, m: ctx.tick(f, m),
            )
        except ArchiveError as exc:
            raise _job_error(exc) from exc
    if p.get("replace_folder"):
        _replace(ctx, root, str(p["replace_folder"]), str(p.get("replace_uid", "")))
    rows = sum(result.get("rows", {}).values())
    ctx.log(f"Создан проект «{result['name']}»: строк {rows}, файлов {result.get('files', 0)}")
    return result


def _replace(ctx: JobContext, root: Path, folder: str, uid: str) -> None:
    """The old project goes only after a successful import; a failure here is a warning, not a new import."""
    old = _replaced_folder(root, folder, uid)
    if old is None:
        ctx.log("Заменяемый проект не найден: импорт создан рядом", "warn")
        return
    lock = ProjectLock(old / "project.lock")
    if not lock.acquire():
        ctx.log("Заменяемый проект открыт в другом окне: он оставлен, импорт создан рядом", "warn")
        return
    lock.release()
    try:
        trash = old.with_name(f".del-{uuid.uuid4().hex}{SUFFIX}")
        old.rename(trash)
    except OSError as exc:
        ctx.log(f"Заменяемый проект не удалён ({exc.strerror or exc}): удалите его в окне «Проекты»", "warn")
        return
    shutil.rmtree(trash, ignore_errors=True)
    ctx.log(f"Заменённый проект удалён: {old.name}")
