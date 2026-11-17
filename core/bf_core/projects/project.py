"""A project folder `<Имя>.bfproj` (ТЗ 6.5, ADR 0002 §5): manifest, project.sqlite, blobs, cache, backups, logs.

Opening: lock (else read-only) → integrity check → backup and migration if the schema is older → daily backup.
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import sqlalchemy as sa

import bf_core
from bf_core.api.errors import ApiError
from bf_core.db import dialect, migrate
from bf_core.db.schema import project_meta

from .lock import ProjectLock

MANIFEST = "project.bfproj"
DB_FILE = "project.sqlite"
SUFFIX = ".bfproj"
BACKUPS_KEPT = 5
BACKUP_EVERY = timedelta(hours=24)
MAX_PATH = 180


def damaged(detail: str) -> ApiError:
    return ApiError(
        409,
        "project_damaged",
        f"Файл проекта повреждён: {detail}",
        "Восстановите проект из автокопии (папка backups проекта) или обратитесь к разработчику.",
    )


def too_new() -> ApiError:
    return ApiError(
        409, "project_too_new", "Проект сохранён более новой версией программы.", "Обновите программу BedrockForge."
    )


def check_location(path: Path) -> None:
    """Only local disks (SQLite WAL needs shared memory) and paths ≤ 180 characters (ADR 0002 §5)."""
    text = str(path)
    if len(text) > MAX_PATH:
        raise ApiError(
            422,
            "path_too_long",
            f"Путь проекта длиннее {MAX_PATH} символов.",
            "Выберите папку проектов ближе к корню диска.",
        )
    if text.startswith(("\\\\", "//")) or _is_remote_drive(path):
        raise ApiError(
            422,
            "network_path",
            "Проекты не хранятся на сетевых дисках.",
            "Выберите папку на локальном диске: база проекта требует локальной файловой системы.",
        )


def make_dirs(path: Path) -> None:
    """mkdir -p with a user-facing error.

    Windows «Контролируемый доступ к папкам» (Defender) blocks unknown programs from writing to Documents
    and reports it as ERROR_FILE_NOT_FOUND / ERROR_ACCESS_DENIED even though the parent folder exists.
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        blocked = sys.platform == "win32" and isinstance(exc, (FileNotFoundError, PermissionError))
        hint = (
            "Вероятно, Windows блокирует запись в «Документы» (защита от программ-шантажистов). "
            "Разрешите программу: «Безопасность Windows» → «Защита от вирусов и угроз» → "
            "«Управление защитой от программ-шантажистов» → «Разрешить работу приложения через "
            "контролируемый доступ к папкам» → добавьте bf-core.exe из папки программы "
            "(resources\\bf-core). Или выберите другую папку: «Настройка → Общие → Папка данных проектов»."
            if blocked
            else "Проверьте права на папку и свободное место на диске или выберите другую папку: "
            "«Настройка → Общие → Папка данных проектов»."
        )
        raise ApiError(
            409,
            "folder_denied",
            f"Не удалось создать папку «{path}»: {exc.strerror or exc}.",
            hint,
        ) from exc


def _is_remote_drive(path: Path) -> bool:
    if sys.platform != "win32" or not path.drive:
        return False
    import ctypes

    get_type = ctypes.windll.kernel32.GetDriveTypeW  # type: ignore[attr-defined]
    get_type.argtypes = [ctypes.c_wchar_p]
    get_type.restype = ctypes.c_uint
    return bool(get_type(path.drive + "\\") == 4)  # DRIVE_REMOTE


_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def folder_name(name: str) -> str:
    """A safe folder name from the project name (Windows forbidden characters and trailing dots removed)."""
    base = _BAD_CHARS.sub("_", name).strip(" .") or "Проект"
    return base[:80]


@dataclass
class Project:
    folder: Path
    engine: sa.Engine
    uid: str
    read_only: bool
    lock: ProjectLock
    lock_owner: dict[str, object] = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.folder / DB_FILE

    def meta(self) -> dict[str, Any]:
        with dialect.read_tx(self.engine) as conn:
            row = conn.execute(sa.select(project_meta)).mappings().one()
        return dict(row)

    def close(self) -> None:
        if not self.read_only:
            try:
                dialect.checkpoint(self.engine)
            except sa.exc.DBAPIError:
                pass
        self.engine.dispose()
        self.lock.release()

    # --- backups ----------------------------------------------------------------------------

    def backup(self, label: str | None = None) -> Path:
        """`VACUUM INTO` a temporary file, then rename (a half-written copy never looks complete)."""
        folder = self.folder / "backups"
        folder.mkdir(exist_ok=True)
        stamp = dialect.utcnow().strftime("%Y%m%d-%H%M%S")
        target = folder / (f"{label}.sqlite" if label else f"project-{stamp}.sqlite")
        tmp = folder / f".tmp-{uuid.uuid4().hex}.sqlite"
        dialect.vacuum_into(self.engine, str(tmp))
        os.replace(tmp, target)
        if label is None:
            dated = sorted(folder.glob("project-*.sqlite"))
            for old in dated[:-BACKUPS_KEPT]:
                old.unlink(missing_ok=True)
        return target

    def backup_if_due(self) -> Path | None:
        if self.read_only:
            return None
        dated = sorted((self.folder / "backups").glob("project-*.sqlite"))
        if dated:
            age = dialect.utcnow().timestamp() - dated[-1].stat().st_mtime
            if age < BACKUP_EVERY.total_seconds():
                return None
        return self.backup()


def write_manifest(folder: Path, uid: str, name: str, **extra: Any) -> None:
    """Writes `project.bfproj`; fields it had besides the standard ones (import token) are kept."""
    old = read_manifest(folder) or {}
    data = {
        **old,
        **extra,
        "format": "bfproj",
        "version": 1,
        "uid": uid,
        "name": name,
        "app_version": bf_core.__version__,
    }
    tmp = folder / (MANIFEST + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, folder / MANIFEST)


def read_manifest(folder: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("format") == "bfproj" else None


def create_folder(root: Path, name: str, description: dict[str, str]) -> Path:
    """A new project folder with an empty database at the current schema. Returns the folder."""
    base = folder_name(name)
    folder = root / f"{base}{SUFFIX}"
    n = 2
    while folder.exists():
        folder = root / f"{base} ({n}){SUFFIX}"
        n += 1
    check_location(folder / "backups" / "pre-migrate-0000.sqlite")
    building = root / f".new-{uuid.uuid4().hex}{SUFFIX}"
    for sub in ("", "blobs", "cache", "backups", "logs"):
        make_dirs(building / sub)
    uid = uuid.uuid4().hex
    engine = dialect.create_engine(dialect.sqlite_url(str(building / DB_FILE)))
    try:
        migrate.upgrade(engine, "project")
        now = dialect.utcnow()
        with dialect.write_tx(engine) as conn:
            conn.execute(
                sa.insert(project_meta).values(
                    id=1,
                    uid=uuid.UUID(uid),
                    name=name,
                    name_key=dialect.name_key(name),
                    description=description,
                    app_version=bf_core.__version__,
                    created_at=now,
                    updated_at=now,
                    version=1,
                )
            )
        dialect.checkpoint(engine)
    finally:
        engine.dispose()
    write_manifest(building, uid, name)
    os.replace(building, folder)  # the folder appears complete or not at all
    return folder


def open_folder(folder: Path) -> Project:
    manifest = read_manifest(folder)
    if manifest is None or not (folder / DB_FILE).exists():
        raise ApiError(404, "project_not_found", "Папка не является проектом BedrockForge.", "Выберите другой проект.")
    lock = ProjectLock(folder / "project.lock")
    read_only = not lock.acquire()
    owner = lock.owner() if read_only else {}
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / DB_FILE), read_only=read_only))
    try:
        result = dialect.quick_check(engine)
        if result != "ok":
            raise damaged(result)
        st = migrate.state(engine, "project")
        if st == "newer":
            raise too_new()
        if st in ("old", "empty"):
            if read_only:
                raise ApiError(
                    423, "project_locked", "Проект открыт в другом окне и требует обновления.", "Закройте другое окно."
                )
            project = Project(folder, engine, str(manifest.get("uid", "")), read_only, lock, owner)
            if st == "old":
                with engine.connect() as conn:
                    rev = migrate.current(conn)
                project.backup(f"pre-migrate-{rev}")
            migrate.upgrade(engine, "project")
        project = Project(folder, engine, str(manifest.get("uid", "")), read_only, lock, owner)
        uid = project.meta()["uid"]
        project.uid = uid.hex if isinstance(uid, uuid.UUID) else str(uid)
        project.backup_if_due()
        return project
    except BaseException:
        engine.dispose()
        lock.release()
        raise
