"""The job `export.objects` (ТЗ 7.35 Д-17; ADR 0010 §1, ADR 0017 §5): every object of the project — or of the
chosen types, or the chosen objects — into files of the format chosen for its type.

`request` (the endpoint) checks the parameters and plans nothing on the disk; the job:

1. removes abandoned temporary folders of earlier exports in the chosen folder (`.‹имя›.bf-tmp`: their job is
   no more — its lock file is free — or, without a lock file, older than an hour);
2. names the new folder «‹Проект› — экспорт ГГГГ-ММ-ДД», « (2)» when such a name is there (without case), and
   makes the temporary `.‹имя›.bf-tmp` next to it, holding a lock file in it while it writes;
3. plans the files: the folder of the type (`registry.KINDS`), the folders of the user (`objects.folder`)
   inside, a stem per object free in its folder (`names.Folder`, the path limit measured on the temporary
   folder — it is the longer one); an object whose format is unavailable, that cannot be written so (no
   source file) or whose name does not fit goes to `skipped` with the reason;
4. writes the objects one by one (`Writer.write`), the progress by the bytes of their data, a cancel between
   the objects and inside the writers; an object its writer refuses (`FormatError`, `JobError`) goes to
   `skipped`, its files removed; a failure of the disk stops the job;
5. renames the temporary folder to the final one — one rename: a folder is there whole or not at all (Windows:
   up to 5 attempts 0.2 s apart on `PermissionError` of an antivirus or the indexer);
6. writes a row of `export_log` per file and returns the report: {folder, name, files [{path, size, object,
   object_id, kind, format}], skipped [{object, object_id, kind, reason, hint}], bytes, warnings}.

A cancel or a failure removes the temporary folder; a worker killed leaves it with its lock free, and the next
export removes it.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import export_log, objects
from bf_core.jobs.context import JobCancelled, JobContext, JobError
from bf_core.jobs.registry import handler

from . import names
from . import registry as reg

KIND = "export.objects"
TMP_SUFFIX = ".bf-tmp"
LOCK = ".bf-export.lock"
ABANDONED_S = 3600.0
RENAME_ATTEMPTS = 5
RENAME_PAUSE_S = 0.2
MAX_OBJECTS = 20_000


def _n(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def folder_name(project: str, day: str) -> str:
    return f"{project} — экспорт {day}"


# --- the request ----------------------------------------------------------------------------------------------


def request(
    conn: sa.Connection,
    *,
    folder: str,
    formats: dict[str, str],
    types: list[str] | None = None,
    ids: list[int] | None = None,
    project: str,
    oda: str | None = None,
    store: Any = None,
    today: str | None = None,
) -> dict[str, Any]:
    """Checked parameters of the job (for `POST /export/objects`); ApiError otherwise: 422 `path_not_absolute`,
    404 `folder_not_found`, 422 `bad_format` (a type or a format the registry does not know), 422
    `export_nothing` (no object of the chosen types can be written in the chosen formats)."""
    if not Path(folder).is_absolute():
        raise ApiError(422, "path_not_absolute", "Путь к папке должен быть полным.", "Выберите папку в окне выбора.")
    if not Path(folder).is_dir():
        raise ApiError(404, "folder_not_found", "Папка для экспорта не найдена.", "Выберите папку заново.")
    chosen = list(dict.fromkeys(types if types is not None else formats))
    for kind in chosen:
        fmt = formats.get(kind)
        if kind not in reg.KIND or fmt is None or reg.writer(kind, fmt) is None:
            title = reg.KIND[kind].title if kind in reg.KIND else kind
            raise ApiError(
                422, "bad_format", f"Для типа «{title}» не выбран известный формат экспорта.",
                "Выберите формат в таблице «тип → формат».",
            )  # fmt: skip
    env = reg.Env(store=store, oda=oda)
    items = reg.items(conn, chosen, ids)
    if len(items) > MAX_OBJECTS:
        raise ApiError(
            422, "too_many_objects", f"Объектов для экспорта больше {_n(MAX_OBJECTS)}.",
            "Экспортируйте типы по отдельности.",
        )  # fmt: skip
    writable, memory = 0, 256
    for item in items:
        w = reg.writer(item.kind, formats[item.kind])
        assert w is not None
        if reg.reason_of(w, env) is None and w.accepts(item) is None:
            writable += 1
            memory = max(memory, w.memory(item))
    if not writable:
        why = next(
            (r for k in chosen if (r := reg.reason_of(reg.writer(k, formats[k]), env))),  # type: ignore[arg-type]
            None,
        )
        raise ApiError(
            422, "export_nothing",
            "Нечего экспортировать: " + (why.message[0].lower() + why.message[1:] if why else
                                         "в выбранных типах нет объектов, которые записываются в выбранных форматах."),
            (why.hint if why and why.hint else "Выберите другие типы или форматы."),
        )  # fmt: skip
    return {
        "folder": folder, "formats": {k: formats[k] for k in chosen}, "types": chosen,
        "ids": None if ids is None else [int(i) for i in ids], "project": project, "oda": oda,
        "today": today or datetime.now().strftime("%Y-%m-%d"), "objects": writable, "memory": memory,
        "bytes": sum(i.size for i in items),
    }  # fmt: skip


def _memory(params: dict[str, Any]) -> int:
    return max(512, int(params.get("memory") or 256))


# --- temporary folders ----------------------------------------------------------------------------------------


def _abandoned(path: Path, now: float) -> bool:
    from bf_core.projects.lock import ProjectLock

    lock_path = path / LOCK
    if lock_path.is_file():
        lock = ProjectLock(lock_path)
        try:
            if lock.acquire():  # its job is gone: nobody holds the lock
                lock.release()
                return True
        except OSError:
            return False
        return False
    try:
        return now - path.stat().st_mtime > ABANDONED_S
    except OSError:
        return False


def sweep(folder: Path, now: float | None = None) -> list[str]:
    """Remove temporary folders of exports whose job is no more; their names."""
    removed: list[str] = []
    now = time.time() if now is None else now
    try:
        entries = list(folder.iterdir())
    except OSError:
        return removed
    for path in entries:
        if path.name.startswith(".") and path.name.endswith(TMP_SUFFIX) and path.is_dir() and not path.is_symlink():
            if _abandoned(path, now):
                shutil.rmtree(path, ignore_errors=True)
                if not path.exists():
                    removed.append(path.name)
    return removed


def _final_name(folder: Path, project: str, day: str) -> str:
    """The name of the new folder, free without case — also of the temporary folders of exports still running."""
    taken = names.Folder(len(str(folder)))
    for entry in _listing(folder):
        taken.take([entry.name])
        if entry.name.startswith(".") and entry.name.endswith(TMP_SUFFIX):
            taken.take([entry.name[1 : -len(TMP_SUFFIX)]])
    # the temporary folder is the longer one: «.» + name + «.bf-tmp»
    return taken.claim(folder_name(project, day), ("",), limit=names.MAX_PATH - 1 - len(TMP_SUFFIX))


def _rename(src: Path, dst: Path) -> None:
    for attempt in range(RENAME_ATTEMPTS):
        try:
            os.rename(src, dst)
            return
        except PermissionError:  # Windows: a handle of an antivirus or the indexer in the folder for a moment
            if attempt == RENAME_ATTEMPTS - 1:
                raise
            time.sleep(RENAME_PAUSE_S)


# --- the plan -------------------------------------------------------------------------------------------------


@dataclass
class Planned:
    item: reg.Item
    writer: reg.Writer
    path: Path  # in the temporary folder
    rel: Path  # the same under the export folder


@dataclass
class Plan:
    todo: list[Planned] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    def skip(self, item: reg.Item, reason: str, hint: str = "") -> None:
        self.skipped.append(
            {"object": item.name, "object_id": item.id, "kind": item.kind, "reason": reason, "hint": hint}
        )


def plan(items: list[reg.Item], formats: dict[str, str], env: reg.Env, tmp: Path) -> Plan:
    out = Plan()
    folders: dict[Path, names.Folder] = {}
    for item in items:
        w = reg.writer(item.kind, formats[item.kind])
        assert w is not None
        why = reg.reason_of(w, env) or w.accepts(item)
        if why is not None:
            out.skip(item, why.message, why.hint)
            continue
        rel_dir = Path(reg.KIND[item.kind].folder, *names.folder_parts(item.folder))
        book = folders.setdefault(rel_dir, names.Folder(len(str(tmp / rel_dir))))
        try:
            suffixes = w.suffixes(item, env)
            stem = book.claim(w.stem_of(item), suffixes)
        except names.ExportNameError as exc:
            out.skip(item, exc.message, exc.hint)
            continue
        except Exception as exc:  # noqa: BLE001 - a broken object is told, the others go on
            out.skip(item, _reason_of(exc)[0], _reason_of(exc)[1])
            continue
        rel = rel_dir / (stem + suffixes[0])
        out.todo.append(Planned(item, w, tmp / rel, rel))
    return out


def _reason_of(exc: BaseException) -> tuple[str, str]:
    message = getattr(exc, "message", None)
    if isinstance(message, str):
        return message, str(getattr(exc, "hint", "") or "")
    return f"Объект не записан: {exc}.", "Проверьте объект и повторите экспорт."


def _drop(paths: list[Path]) -> None:
    for p in paths:
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)


# --- the job --------------------------------------------------------------------------------------------------


@handler(KIND, "Экспорт объектов проекта", queue="heavy", estimate_memory=_memory)
def export_objects(ctx: JobContext) -> dict[str, Any]:
    from bf_core.formats.errors import FormatError
    from bf_core.lines.export import late_tick
    from bf_core.projects.lock import ProjectLock
    from bf_core.storage.blobs import BlobStore

    p = ctx.params
    if ctx.project_folder is None:
        raise RuntimeError("export.objects is a project job")
    folder = Path(p["folder"])
    engine = ctx.engine()
    env = reg.Env(engine=engine, store=BlobStore(ctx.project_folder), oda=p.get("oda"))
    formats = {str(k): str(v) for k, v in (p.get("formats") or {}).items()}
    ctx.tick(0.0, "План экспорта")
    if not folder.is_dir():
        raise JobError("folder_not_found", "Папка для экспорта не найдена.", "Выберите папку заново.")
    for name in sweep(folder):
        ctx.log(f"Удалена брошенная временная папка прежнего экспорта «{name}»")
    with dialect.read_tx(engine) as conn:
        items = reg.items(conn, p.get("types") or list(formats), p.get("ids"))
    try:
        final_name = _final_name(folder, str(p.get("project") or "Проект"), str(p.get("today")))
    except names.ExportNameError as exc:
        raise JobError(exc.code, exc.message, exc.hint) from exc
    final = folder / final_name
    tmp = folder / f".{final_name}{TMP_SUFFIX}"
    _drop([tmp])
    lock: ProjectLock | None = None
    done: list[tuple[Planned, reg.Written]] = []
    try:
        tmp.mkdir()
        lock = ProjectLock(tmp / LOCK)
        lock.acquire()
        work = plan(items, formats, env, tmp)
        total = max(1, sum(t.item.size for t in work.todo))
        passed = 0
        for t in work.todo:
            start, stop = passed / total, (passed + t.item.size) / total
            passed += t.item.size

            def tick(f: float = 0.0, message: str | None = None, *, a: float = start, b: float = stop) -> None:
                ctx.tick(0.98 * (a + (b - a) * max(0.0, min(1.0, f))), message)

            tick(0.0, f"«{t.item.name}» → {t.writer.title}")
            before = set(_listing(t.path.parent))
            try:
                t.path.parent.mkdir(parents=True, exist_ok=True)
                assert t.writer.write is not None
                written = t.writer.write(t.item, t.path, env, tick)
            except (FormatError, JobError, ApiError) as exc:
                _drop([q for q in _listing(t.path.parent) if q not in before])
                work.skip(t.item, *_reason_of(exc))
                continue
            done.append((t, written))
            for w in written.warnings:
                ctx.log(f"{t.item.name}: {w}", "warn")
            for note in written.notes:
                ctx.log(f"{t.item.name}: {note}")
        ctx.tick(0.98, "Перенос папки экспорта")
        if lock is not None:  # Windows does not rename a folder with an open file in it
            lock.release()
            (tmp / LOCK).unlink(missing_ok=True)
            lock = None
        files = [f for _t, w in done for f in w.files]
        if files:
            _rename(tmp, final)
        else:
            _drop([tmp])
    except JobCancelled:
        _close(lock, tmp)
        raise
    except names.ExportNameError as exc:
        _close(lock, tmp)
        raise JobError(exc.code, exc.message, exc.hint) from exc
    except OSError as exc:
        _close(lock, tmp)
        raise JobError(
            "file_unwritable",
            f"Папка экспорта не записана: {exc.strerror or exc}.",
            "Проверьте свободное место и доступ к папке, закройте файлы экспорта в других программах.",
        ) from exc
    except BaseException:
        _close(lock, tmp)
        raise
    return _report(ctx, engine, final, tmp, done, work.skipped, late_tick)


def _listing(folder: Path) -> list[Path]:
    try:
        return list(folder.iterdir())
    except OSError:
        return []


def _close(lock: Any, tmp: Path) -> None:
    if lock is not None:
        lock.release()
    _drop([tmp])


def _report(
    ctx: JobContext, engine: sa.Engine, final: Path, tmp: Path, done: list[tuple[Planned, reg.Written]],
    skipped: list[dict[str, Any]], late_tick: Any,
) -> dict[str, Any]:  # fmt: skip
    files: list[dict[str, Any]] = []
    for t, w in done:
        for f in w.files:
            path = final / f.relative_to(tmp)
            files.append({
                "path": str(path), "size": path.stat().st_size, "object": t.item.name, "object_id": t.item.id,
                "kind": t.item.kind, "format": t.writer.format,
            })  # fmt: skip
    for s in skipped:
        ctx.log(f"Не записан «{s['object']}»: {s['reason']}", "warn")
    total = sum(int(f["size"]) for f in files)
    if files:
        ctx.log(f"Папка «{final.name}»: файлов {_n(len(files))}, {_n(total)} байт; пропущено {_n(len(skipped))}")
        with dialect.write_tx(engine) as conn:
            ids = sorted({int(f["object_id"]) for f in files})
            alive = set(conn.execute(sa.select(objects.c.id).where(objects.c.id.in_(ids))).scalars())
            now = dialect.utcnow()
            conn.execute(
                sa.insert(export_log),
                [{"object_id": f["object_id"] if f["object_id"] in alive else None, "job_id": ctx.id,
                  "file_name": str(Path(f["path"]).relative_to(final.parent))[:400], "format": f["format"],
                  "settings": {"export": final.name, "kind": f["kind"]}, "counts": {"size": f["size"]},
                  "created_at": now} for f in files],
            )  # fmt: skip
    late_tick(ctx, 1.0, "Готово")
    return {
        "folder": str(final) if files else None, "name": final.name if files else None, "files": files,
        "skipped": skipped, "bytes": total,
        "warnings": [f"{t.item.name}: {w}" for t, written in done for w in written.warnings],
    }  # fmt: skip
