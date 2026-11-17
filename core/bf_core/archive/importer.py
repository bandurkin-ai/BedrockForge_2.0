"""Import of a `.bfggis` (ТЗ 7.4): checked, renumbered and written into a new project folder that appears
only after success. A failure at any step leaves nothing; with «Заменить» the old project goes only after success.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

import sqlalchemy as sa

import bf_core
from bf_core.db import dialect, migrate
from bf_core.db.schema import blobs, project_md, project_meta
from bf_core.projects.lock import ProjectLock
from bf_core.projects.project import DB_FILE, SUFFIX, folder_name, make_dirs, read_manifest, write_manifest
from bf_core.storage.blobs import BlobStore, valid_sha

from .container import ArchiveError, Corrupted, TooNew, open_archive, read_header, read_json
from .rows import decode_row, read_lines
from .spec import APP, ID_COLUMNS, KIND_COLUMNS, RETURNED_MAPS, TABLES

Progress = Callable[[float, str], None]


class FromBF(ArchiveError):
    """A `.bfggis` of BedrockForge 1: its data tables are converted by the stages that bring those types."""


def _check_versions(manifest: dict[str, Any]) -> None:
    if manifest.get("format") != "bfggis":
        raise Corrupted("Файл повреждён: нет описания проекта")
    if manifest.get("app") != APP:
        return
    rev = str(manifest.get("schema_revision", ""))
    if rev not in migrate.known_revisions("project"):
        raise TooNew("Файл записан более новой версией BedrockForge: обновите программу")


def inspect(path: Path, password: str | None = None) -> dict[str, Any]:
    """Step 2 of the import: what is in the file. Reads the header and the manifest only."""
    with path.open("rb") as f:
        header, _ = read_header(f)
    info: dict[str, Any] = {
        "encrypted": bool(header.get("encrypted")),
        "app_version": header.get("app_version"),
        "schema_revision": header.get("schema_revision"),
        "from_bf": header.get("app") != APP,
        "size": path.stat().st_size,
    }
    if info["encrypted"] and not password:
        info["needs_password"] = True
        return info
    with open_archive(path, password) as (_, tar):
        member = tar.next()
        if member is None or member.name != "manifest.json":
            raise Corrupted("Файл повреждён: нет описания проекта")
        manifest = read_json(tar, member)
    _check_versions(manifest)
    project = manifest.get("project", {})
    info.update(
        needs_password=False,
        name=project.get("name", ""),
        description=project.get("description", {}),
        created_at=project.get("created_at"),
        exported_at=manifest.get("exported_at"),
        app_version=manifest.get("app_version"),
        schema_revision=manifest.get("schema_revision"),
        summary=manifest.get("summary") or {},
        tables=manifest.get("tables") or {},
        files=manifest.get("files", 0),
        file_bytes=manifest.get("file_bytes", 0),
        view_settings=bool(manifest.get("view_settings") or manifest.get("browser")),
    )
    return info


class _Hashing:
    """Reads a tar member while counting its SHA-256."""

    def __init__(self, f: IO[bytes]) -> None:
        self._f, self.h = f, hashlib.sha256()

    def __iter__(self) -> Any:
        for line in self._f:
            self.h.update(line)
            yield line

    def read(self, n: int = -1) -> bytes:
        data = self._f.read(n)
        self.h.update(data)
        return data


def import_project(
    path: Path,
    root: Path,
    *,
    name: str,
    password: str | None = None,
    apply_view: bool = True,
    token: str | None = None,
    progress: Progress = lambda _f, _m: None,
) -> dict[str, Any]:
    """Builds the project in `root/.imp-<uuid>.bfproj` and renames it into place. Returns the new uid, name,
    folder and the id maps. `token` marks the project (manifest) so that a retry after a crash finds it.
    The building folder is locked while it is filled: the maintenance removes only abandoned ones."""
    make_dirs(root)
    building = root / f".imp-{uuid.uuid4().hex}{SUFFIX}"
    for sub in ("", "blobs", "cache", "backups", "logs"):
        make_dirs(building / sub)
    lock = ProjectLock(building / "project.lock")
    lock.acquire()
    engine = dialect.create_engine(dialect.sqlite_url(str(building / DB_FILE)))
    store = BlobStore(building)
    size = max(1, path.stat().st_size)
    try:
        migrate.upgrade(engine, "project")
        with engine.connect() as conn:
            conn.info["bf_begin"] = "BEGIN IMMEDIATE"
            with conn.begin():  # one transaction for all rows (the database is new: nobody waits for it)
                result = _restore(
                    conn, path, password, store, name, apply_view, lambda pos, msg: progress(min(0.99, pos / size), msg)
                )
        dialect.checkpoint(engine)
    except BaseException:
        engine.dispose()
        lock.release()
        shutil.rmtree(building, ignore_errors=True)
        raise
    engine.dispose()
    if token:
        (building / "logs" / f"import-{token}.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8"
        )
    write_manifest(building, result["uid"], name, **({"import_token": token} if token else {}))
    lock.release()
    base = folder_name(name)
    folder = root / f"{base}{SUFFIX}"
    n = 2
    while folder.exists():
        folder = root / f"{base} ({n}){SUFFIX}"
        n += 1
    os.replace(building, folder)
    result["path"] = str(folder)
    return result


def find_imported(root: Path, token: str) -> dict[str, Any] | None:
    """A project an earlier attempt of the same import job already created (it died before reporting)."""
    if not root.is_dir():
        return None
    for folder in root.iterdir():
        if not folder.name.endswith(SUFFIX) or folder.name.startswith("."):
            continue
        manifest = read_manifest(folder)
        if manifest and manifest.get("import_token") == token:
            try:
                result = json.loads((folder / "logs" / f"import-{token}.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                result = {
                    "uid": manifest.get("uid"),
                    "name": manifest.get("name"),
                    "id_maps": {},
                    "rows": {},
                    "files": 0,
                }
            result["path"] = str(folder)
            return dict(result)
    return None


def _restore(
    conn: sa.Connection,
    path: Path,
    password: str | None,
    store: BlobStore,
    name: str,
    apply_view: bool,
    progress: Callable[[int, str], None],
) -> dict[str, Any]:
    maps: dict[str, dict[int, int]] = {}
    actual: dict[str, str] = {}
    staged: dict[str, tuple[Path, str, int]] = {}
    view: dict[str, Any] = {}
    index: dict[str, Any] = {}
    owners: list[dict[str, Any]] = []
    checksums: dict[str, str] | None = None
    manifest: dict[str, Any] | None = None
    pos = {"n": 0}
    by_name = {f"db/{t.name}.jsonl": t for t in TABLES}

    def on_read(n: int) -> None:  # progress and cancellation inside big members too (ТЗ 6.7: tick ≥ 1/s)
        pos["n"] = n
        progress(n, None)  # type: ignore[arg-type]

    with open_archive(path, password, on_read=on_read) as (_, tar):
        for member in tar:
            if not member.isfile():
                continue
            f = tar.extractfile(member)
            if f is None:
                raise Corrupted(f"Нет данных «{member.name}»")
            if member.name == "manifest.json":
                raw = f.read()
                actual[member.name] = hashlib.sha256(raw).hexdigest()
                manifest = json.loads(raw)
                _check_versions(manifest)
                if manifest.get("app") != APP:
                    raise FromBF(
                        "Файл создан программой BedrockForge первой версии: перенос его наборов, каркасов, линий и "
                        "растров включается вместе с этими данными на следующих этапах."
                    )
            elif member.name in by_name:
                spec = by_name[member.name]
                progress(pos["n"], f"Таблица {spec.name}")
                hf = _Hashing(f)
                if spec.view and not apply_view:
                    for _ in hf:
                        pass
                elif spec.name == "blob_owners":
                    owners = list(read_lines(hf))  # type: ignore[arg-type]  # inserted after the blobs rows (FK)
                else:
                    _insert_table(conn, spec.name, spec.renumber, read_lines(hf), maps)  # type: ignore[arg-type]
                actual[member.name] = hf.h.hexdigest()
                if spec.view:
                    view["applied"] = apply_view
            elif member.name == "blobs.json":
                raw = f.read()
                actual[member.name] = hashlib.sha256(raw).hexdigest()
                index = json.loads(raw)
            elif member.name.startswith("blobs/"):
                sha = member.name.split("/", 1)[1]
                if not valid_sha(sha):
                    raise Corrupted(f"Посторонний файл в архиве: {member.name}")
                progress(pos["n"], "Файлы хранилища")
                tmp = store.stage_stream(iter(lambda f=f: f.read(1 << 20), b""))  # type: ignore[misc]
                if tmp[1] != sha:
                    tmp[0].unlink(missing_ok=True)
                    raise Corrupted(f"Файл хранилища повреждён: {sha[:12]}…")
                staged[sha] = tmp
            elif member.name == "checksums.json":
                checksums = read_json(tar, member)
    if manifest is None or checksums is None:
        raise Corrupted("Файл обрезан: нет контрольных сумм")
    for member_name, sha in checksums.items():
        if actual.get(member_name) != sha:
            raise Corrupted(f"Контрольная сумма не совпала: {member_name}")
    # files: into place and registered (owners came with blob_owners)
    now = dialect.utcnow()
    for sha, tmp in staged.items():
        dst = store.path_of(sha)
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp[0], dst)
        media = str((index.get(sha) or {}).get("media_type") or "application/octet-stream")
        conn.execute(sa.insert(blobs).values(sha256=sha, size=tmp[2], media_type=media, created_at=now))
    known = set(staged) | {r[0] for r in conn.execute(sa.select(blobs.c.sha256))}
    _insert_table(conn, "blob_owners", False, (o for o in owners if o.get("sha256") in known), maps)
    uid = uuid.uuid4()
    project = manifest.get("project") or {}
    from bf_core.projects.store import normalize_description

    created = project.get("created_at")
    conn.execute(
        sa.insert(project_meta).values(
            id=1,
            uid=uid,
            name=name,
            name_key=dialect.name_key(name),
            description=normalize_description(project.get("description")),
            app_version=bf_core.__version__,
            created_at=dialect.utcnow() if not created else _dt(created),
            updated_at=now,
            version=1,
        )
    )
    return {
        "uid": uid.hex,
        "name": name,
        "id_maps": {k: {str(o): n for o, n in maps.get(k, {}).items()} for k in RETURNED_MAPS},
        "rows": {t: len(m) for t, m in maps.items()},
        "files": len(staged),
    }


def _dt(text: str) -> Any:
    from datetime import UTC, datetime

    d = datetime.fromisoformat(text)
    return d if d.tzinfo else d.replace(tzinfo=UTC)


_BATCH = 5_000


def _insert_table(conn: sa.Connection, name: str, renumber: bool, rows: Any, maps: dict[str, dict[int, int]]) -> None:
    table = project_md.tables[name]
    id_cols = {col: target for (t, col), target in ID_COLUMNS.items() if t == name}
    kind_cols = [(k, i, m) for (t, k, i), m in KIND_COLUMNS.items() if t == name]
    own = maps.setdefault(name, {}) if renumber else {}
    deferred: list[tuple[int, str, int]] = []  # (new id, column, old target id): self references
    # Rows nobody refers to (interval rows, survey readings — millions) go in portions, ids left to the database.
    batched = not renumber and not kind_cols and name not in id_cols.values() and "id" in table.c
    batch: list[dict[str, Any]] = []
    for raw in rows:
        row = decode_row(table, raw)
        for col, target in id_cols.items():
            old = row.get(col)
            if old is None:
                continue
            if target is None:
                row[col] = None
            elif target == name:
                row[col] = None  # filled once all rows of the table have their new ids
                deferred.append((-1, col, int(old)))
            elif int(old) in maps.get(target, {}):
                row[col] = maps[target][int(old)]
            else:
                raise Corrupted(f"Ссылка на отсутствующую запись: {name}.{col} = {old}")
        skip = False
        for kind_col, id_col, targets in kind_cols:
            target = targets.get(str(row.get(kind_col)))
            old = row.get(id_col)
            if target is None or old is None or int(old) not in maps.get(target, {}):
                skip = True  # an owner/scope that is not part of the archive (a job, a dropped row)
                break
            row[id_col] = maps[target][int(old)]
        if skip:
            continue
        if batched:
            row.pop("id", None)
            batch.append(row)
            if len(batch) >= _BATCH:
                conn.execute(sa.insert(table), batch)
                batch = []
            continue
        old_id = row.pop("id", None) if renumber else None
        res = conn.execute(sa.insert(table).values(**row))
        if renumber:
            key = res.inserted_primary_key
            assert key is not None and old_id is not None
            own[int(old_id)] = int(key[0])
            if deferred and deferred[-1][0] == -1:
                deferred[-1] = (int(key[0]), deferred[-1][1], deferred[-1][2])
    if batch:
        conn.execute(sa.insert(table), batch)
    for new_id, col, old_target in deferred:
        if old_target not in own:
            raise Corrupted(f"Ссылка на отсутствующую запись: {name}.{col} = {old_target}")
        conn.execute(sa.update(table).where(table.c.id == new_id).values({col: own[old_target]}))
