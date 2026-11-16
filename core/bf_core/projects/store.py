"""Projects of the desktop (ТЗ 7.3): list, create, open, rename, describe, delete, search, composition.

The project folders are the truth; `instance.sqlite` keeps a cache of their `project_meta` (ADR 0003 «Последствия»).
One project is open at a time.
"""

from __future__ import annotations

import shutil
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect, migrate
from bf_core.db.schema import objects, project_meta, projects_cache

from .lock import ProjectLock
from .project import DB_FILE, SUFFIX, Project, check_location, create_folder, open_folder, read_manifest

#: Composition counters (ТЗ 7.3 «Сводка состава»). Later stages register theirs: name → fn(conn) → int.
SUMMARY_KEYS = (
    "sets",
    "holes",
    "surveys",
    "assays",
    "interval_rows",
    "wireframes",
    "lines",
    "rasters",
    "block_models",
    "charts",
    "chats",
)
#: Object kinds counted by the generic counter (the rest are registered by their modules).
OBJECT_KIND_KEYS = {
    "drillhole_set": "sets",
    "wireframe": "wireframes",
    "line_layer": "lines",
    "raster": "rasters",
    "block_model": "block_models",
    "chart": "charts",
}
COUNTERS: dict[str, Callable[[sa.Connection], int]] = {}

DESCRIPTION_FIELDS = ("deposit", "license", "stage")


def normalize_description(raw: Any) -> dict[str, str]:
    raw = raw if isinstance(raw, dict) else {}
    return {k: str(raw.get(k) or "").strip()[:500] for k in DESCRIPTION_FIELDS}


def summary(conn: sa.Connection) -> dict[str, int]:
    out = dict.fromkeys(SUMMARY_KEYS, 0)
    rows = conn.execute(
        sa.select(objects.c.kind, sa.func.count()).where(objects.c.state == "ready").group_by(objects.c.kind)
    ).all()
    for kind, n in rows:
        key = OBJECT_KIND_KEYS.get(kind)
        if key:
            out[key] += int(n)
    for key, fn in COUNTERS.items():
        out[key] = fn(conn)
    return out


def _db_mtime(folder: Path) -> float:
    m = 0.0
    for name in (DB_FILE, DB_FILE + "-wal"):
        try:
            m = max(m, (folder / name).stat().st_mtime)
        except OSError:
            pass
    return m


class ProjectService:
    def __init__(self, root: Path, instance_engine: sa.Engine) -> None:
        self.root = root
        self.instance = instance_engine
        self.current: Project | None = None
        self._lock = threading.RLock()
        #: Hooks of the job scheduler: a project opened; a project closing (True — its jobs run, keep it).
        self.on_open: list[Callable[[Project], None]] = []
        self.on_close: list[Callable[[Project], bool]] = []
        #: Projects closed by the user while their jobs still run (ADR 0002 §5 «Смена проекта»).
        self.detached: dict[str, Project] = {}
        self.is_released: Callable[[str], bool] = lambda _uid: True

    # --- listing ----------------------------------------------------------------------------

    def _folders(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return sorted(
            p for p in self.root.iterdir() if p.is_dir() and p.name.endswith(SUFFIX) and not p.name.startswith(".")
        )

    def _read_entry(self, folder: Path) -> dict[str, Any] | None:
        """project_meta + summary of one folder, via the cache when the database did not change."""
        manifest = read_manifest(folder)
        if manifest is None:
            return None
        uid = str(manifest.get("uid", ""))
        mtime = _db_mtime(folder)
        with dialect.read_tx(self.instance) as conn:
            cached = conn.execute(sa.select(projects_cache).where(projects_cache.c.uid == uid)).mappings().first()
        if cached and cached["mtime"] == mtime and cached["path"] == str(folder):
            entry = dict(cached["summary"])
        else:
            loaded = self._load_entry(folder, uid)
            if loaded is None:
                return None
            entry = loaded
            with dialect.write_tx(self.instance) as conn:
                conn.execute(sa.delete(projects_cache).where(projects_cache.c.uid == uid))
                conn.execute(
                    sa.insert(projects_cache).values(
                        uid=uid,
                        path=str(folder),
                        name=entry["name"],
                        name_key=dialect.name_key(entry["name"]),
                        summary=entry,
                        mtime=mtime,
                    )
                )
        entry["path"] = str(folder)
        entry["open"] = self.current is not None and self.current.folder == folder
        return entry

    def _load_entry(self, folder: Path, uid: str) -> dict[str, Any] | None:
        engine = dialect.create_engine(dialect.sqlite_url(str(folder / DB_FILE), read_only=True))
        try:
            if migrate.state(engine, "project") != "current":
                with engine.connect() as conn:
                    name = conn.execute(sa.select(project_meta.c.name)).scalar_one_or_none()
                return {"uid": uid, "name": name or folder.stem, "description": {}, "summary": {}, "outdated": True}
            with dialect.read_tx(engine) as conn:
                meta = conn.execute(sa.select(project_meta)).mappings().one()
                counts = summary(conn)
            return {
                "uid": uid,
                "name": meta["name"],
                "description": normalize_description(meta["description"]),
                "created_at": meta["created_at"].isoformat(),
                "updated_at": meta["updated_at"].isoformat(),
                "summary": counts,
            }
        except sa.exc.DBAPIError:
            return {"uid": uid, "name": folder.stem, "description": {}, "summary": {}, "damaged": True}
        finally:
            engine.dispose()

    def list(self, query: str = "") -> list[dict[str, Any]]:
        q = dialect.name_key(query)
        out = []
        for folder in self._folders():
            entry = self._read_entry(folder)
            if entry is None:
                continue
            if q:
                hay = dialect.name_key(" ".join([entry["name"], *entry.get("description", {}).values()]))
                if q not in hay:
                    continue
            out.append(entry)
        out.sort(key=lambda e: e.get("updated_at", ""), reverse=True)
        return out

    def find(self, uid: str) -> Path:
        for folder in self._folders():
            m = read_manifest(folder)
            if m and m.get("uid") == uid:
                return folder
        raise ApiError(404, "project_not_found", "Проект не найден.", "Обновите список проектов.")

    def _name_taken(self, name: str, except_uid: str | None = None) -> bool:
        key = dialect.name_key(name)
        return any(e["uid"] != except_uid and dialect.name_key(e["name"]) == key for e in self.list())

    @staticmethod
    def _clean_name(name: str) -> str:
        name = " ".join(str(name).split())
        if not name:
            raise ApiError(422, "name_empty", "Название проекта не может быть пустым.", "Введите название.")
        if len(name) > 200:
            raise ApiError(422, "name_too_long", "Название длиннее 200 символов.", "Сократите название.")
        return name

    def _taken(self, name: str) -> ApiError:
        return ApiError(409, "name_taken", f"Проект «{name}» уже есть.", "Выберите другое название.")

    # --- changes ----------------------------------------------------------------------------

    def create(self, name: str, description: Any = None) -> Project:
        name = self._clean_name(name)
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            check_location(self.root)
            if self._name_taken(name):
                raise self._taken(name)
            folder = create_folder(self.root, name, normalize_description(description))
            return self.open(read_manifest(folder)["uid"])  # type: ignore[index]

    def open(self, uid: str) -> Project:
        with self._lock:
            if self.current and self.current.uid == uid:
                return self.current
            self.close()
            if uid in self.detached:
                project = self.detached.pop(uid)
            else:
                project = open_folder(self.find(uid))
            self.current = project
            for hook in self.on_open:
                hook(project)
            with dialect.write_tx(self.instance) as conn:
                conn.execute(
                    sa.update(projects_cache).where(projects_cache.c.uid == uid).values(opened_at=dialect.utcnow())
                )
            return project

    def close(self) -> None:
        with self._lock:
            p, self.current = self.current, None
            if p is not None:
                keep = any([hook(p) for hook in self.on_close])
                if keep:
                    self.detached[p.uid] = p
                else:
                    p.close()
            self.reap()

    def reap(self) -> None:
        """Closes detached projects whose jobs have ended."""
        with self._lock:
            for uid in [u for u in self.detached if self.is_released(u)]:
                self.detached.pop(uid).close()

    def require_current(self, writable: bool = False) -> Project:
        p = self.current
        if p is None:
            raise ApiError(409, "no_project", "Проект не открыт.", "Откройте или создайте проект.")
        if writable and p.read_only:
            raise ApiError(
                423,
                "project_read_only",
                "Проект открыт только для чтения: он уже открыт в другом окне BedrockForge.",
                "Закройте проект в другом окне и откройте его заново.",
            )
        return p

    def update_meta(
        self, uid: str, *, name: str | None, description: Any, expected_version: int | None
    ) -> dict[str, Any]:
        """Rename and/or describe the open project (the folder name stays: it is not shown to the user)."""
        with self._lock:
            p = self.require_current(writable=True)
            if p.uid != uid:
                raise ApiError(409, "not_open", "Изменять можно только открытый проект.", "Откройте проект.")
            values: dict[str, Any] = {"updated_at": dialect.utcnow()}
            if name is not None:
                name = self._clean_name(name)
                if self._name_taken(name, except_uid=uid):
                    raise self._taken(name)
                values.update(name=name, name_key=dialect.name_key(name))
            if description is not None:
                values["description"] = normalize_description(description)
            with dialect.write_tx(p.engine) as conn:
                cur = conn.execute(sa.select(project_meta.c.version)).scalar_one()
                if expected_version is not None and expected_version != cur:
                    raise ApiError(
                        409,
                        "version_conflict",
                        "Описание проекта уже изменено в другом окне.",
                        "Обновите окно «Проекты» и повторите.",
                    )
                conn.execute(sa.update(project_meta).values(**values, version=cur + 1))
            if name is not None:
                from .project import write_manifest

                write_manifest(p.folder, p.uid, name)
            return p.meta()

    def delete(self, uid: str) -> None:
        """All the project's data: the folder goes away (a running job keeps it until it ends — Э2.4)."""
        with self._lock:
            folder = self.find(uid)
            if self.current and self.current.uid == uid:
                self.close()
            if uid in self.detached:
                raise ApiError(
                    409, "project_busy", "У проекта выполняются задания.", "Дождитесь их окончания или отмените их."
                )
            probe = ProjectLock(folder / "project.lock")  # refuses to delete a project open elsewhere
            if probe.acquire():
                probe.release()
            else:
                raise ApiError(
                    423,
                    "project_locked",
                    "Проект открыт в другом окне BedrockForge.",
                    "Закройте его там и повторите удаление.",
                )
            trash = folder.with_name(f".del-{uuid.uuid4().hex}{SUFFIX}")
            folder.rename(trash)  # disappears from the list at once, even if removal takes time
            shutil.rmtree(trash, ignore_errors=True)
            with dialect.write_tx(self.instance) as conn:
                conn.execute(sa.delete(projects_cache).where(projects_cache.c.uid == uid))
