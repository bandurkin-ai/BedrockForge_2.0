"""Restoring a project from a `.bfggis` file as a new project of this database.

Ids are new: every table goes through a staging copy and an old -> new map kept in the database
(temporary tables), so millions of rows never pass through Python. Ids written inside JSON
(operation sources, the raster of a terrain model, the tables and styles of a chart, the rows of the
edit log) are translated afterwards. The whole import is one transaction: on any error nothing is
left behind, and with mode "replace" the old project is removed only when the new one is complete.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.archive import versions
from app.archive.container import Corrupted, TooNew, open_archive, read_json
from app.archive.spec import RETURNED_MAPS, TABLES, Table
from app.storage import blobs

Progress = Callable[[float, str], None]
MAX_NAME = 200
_SHA = re.compile(r"^[0-9a-f]{64}$")


class ImportFailed(Exception):
    pass


def work_root() -> Path:
    """Extracted files of running imports: same file system as the store, so files are renamed into
    it, and outside tmp/, whose collector would remove files older than an hour."""
    d = blobs.root() / "archive-work"
    d.mkdir(parents=True, exist_ok=True)
    return d


def free_name(session: Session, name: str) -> str:
    """`name`, or "name (2)", "name (3)", ... if it is taken."""
    name = name.strip()[:MAX_NAME] or "Проект"
    taken = {r[0] for r in session.execute(text("SELECT name FROM project"))}
    if name not in taken:
        return name
    n = 2
    while True:
        suffix = f" ({n})"
        candidate = name[: MAX_NAME - len(suffix)].rstrip() + suffix
        if candidate not in taken:
            return candidate
        n += 1


def inspect(path: Path, password: str | None = None) -> dict:
    """Header and manifest of an archive: what it holds, without reading the rest."""
    with open_archive(path, password) as (header, tar):
        first = tar.next()
        if first is None or first.name != "manifest.json":
            raise Corrupted("В файле нет описания проекта")
        manifest = read_json(tar, first)
    revision = manifest.get("schema_revision")
    try:
        versions.upgrade_path(revision)
    except ValueError:
        raise TooNew("Файл записан более новой версией BedrockForge: обновите программу") from None
    return {"header": {k: v for k, v in header.items() if k not in ("kdf", "nonce_prefix")}, "manifest": manifest}


# ---- extraction


def _copy_member(tar, member, dest: Path) -> str:
    src = tar.extractfile(member)
    if src is None:
        raise Corrupted(f"Нет данных «{member.name}»")
    h = hashlib.sha256()
    with dest.open("wb") as out:
        while chunk := src.read(8 << 20):
            h.update(chunk)
            out.write(chunk)
    return h.hexdigest()


def _extract(path: Path, password: str | None, work: Path, progress: Progress) -> dict:
    size = max(1, path.stat().st_size)
    out = {"manifest": None, "tables": {}, "blobs": {}, "index": {}, "browser": None}
    sums: dict[str, str] = {}
    checksums = None
    (work / "db").mkdir()
    (work / "blobs").mkdir()

    def on_read(pos: int) -> None:
        progress(0.6 * min(1.0, pos / size), "Распаковка")

    with open_archive(path, password, on_read=on_read) as (_, tar):
        for member in tar:
            name = member.name
            if not member.isfile():
                continue
            if name == "manifest.json":
                out["manifest"] = read_json(tar, member)
            elif name == "checksums.json":
                checksums = read_json(tar, member)
            elif name in ("blobs.json", "browser.json"):
                raw = tar.extractfile(member).read()
                sums[name] = hashlib.sha256(raw).hexdigest()
                try:
                    out["index" if name == "blobs.json" else "browser"] = json.loads(raw)
                except ValueError as exc:
                    raise Corrupted(f"Повреждена запись «{name}»") from exc
            elif name.startswith("db/") and name.endswith(".csv"):
                table = name[3:-4]
                if not re.fullmatch(r"[a-z_]+", table):
                    continue
                dest = work / "db" / f"{table}.csv"
                sums[name] = _copy_member(tar, member, dest)
                out["tables"][table] = dest
            elif name.startswith("blobs/"):
                sha = name[6:]
                if not _SHA.match(sha):
                    continue
                dest = work / "blobs" / sha
                if _copy_member(tar, member, dest) != sha:
                    raise Corrupted(f"Файл хранилища {sha[:12]}… повреждён")
                out["blobs"][sha] = dest
    if out["manifest"] is None:
        raise Corrupted("В файле нет описания проекта")
    if checksums is None:
        raise Corrupted("Файл обрезан: нет контрольных сумм")
    for name, sha in checksums.items():
        if sums.get(name) != sha:
            raise Corrupted(f"Контрольная сумма «{name}» не совпала: файл повреждён")
    return out


# ---- loading


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _columns(session: Session, table: str) -> list[str]:
    return [r[0] for r in session.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() "
        "AND table_name = :t ORDER BY ordinal_position"), {"t": table})]


def _stage(session: Session, t: Table, csv_path: Path) -> None:
    stg = f"stg_{t.name}"
    session.execute(text(f"CREATE TEMP TABLE {stg} (LIKE {t.name} INCLUDING DEFAULTS) ON COMMIT DROP"))
    current = _columns(session, t.name)
    for c in current:
        session.execute(text(f"ALTER TABLE {stg} ALTER COLUMN {_q(c)} DROP NOT NULL"))
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        header = next(csv.reader([f.readline()]), [])
    for c in header:
        if c not in current:  # a column a later migration dropped: kept as text for the upgrades
            session.execute(text(f"ALTER TABLE {stg} ADD COLUMN {_q(c)} text"))
    cols = ", ".join(_q(c) for c in header)
    raw = session.connection().connection.driver_connection
    with raw.cursor() as cur, cur.copy(f"COPY {stg} ({cols}) FROM STDIN WITH (FORMAT csv, HEADER true)") as copy, \
            csv_path.open("rb") as f:
        while chunk := f.read(8 << 20):
            copy.write(chunk)


def _map(session: Session, t: Table) -> None:
    stg, m = f"stg_{t.name}", f"map_{t.name}"
    if t.key == "hex":
        new = "md5(random()::text || clock_timestamp()::text || s.id)"
        session.execute(text(f"CREATE TEMP TABLE {m} ON COMMIT DROP AS SELECT s.id::text AS old, {new} AS new FROM {stg} s"))
    else:
        session.execute(text(
            f"CREATE TEMP TABLE {m} ON COMMIT DROP AS SELECT s.id::bigint AS old, "
            f"nextval(pg_get_serial_sequence('{t.name}', 'id'))::bigint AS new FROM (SELECT id FROM {stg} ORDER BY id) s"))
    session.execute(text(f"ALTER TABLE {m} ADD PRIMARY KEY (old)"))


def _insert(session: Session, t: Table, project: dict) -> int:
    stg, m = f"stg_{t.name}", f"map_{t.name}"
    cols = _columns(session, t.name)
    exprs: dict[str, str] = {"id": "m.new"}
    joins = [f"JOIN {m} m ON m.old = s.id"]
    params: dict = {}
    if t.parent:
        column, parent = t.parent
        exprs[column] = "p.new"
        joins.append(f"JOIN map_{parent} p ON p.old = s.{_q(column)}")
    if t.name == "project":
        exprs["name"] = ":name"
        params["name"] = project["name"]
    if t.name == "data_edit":
        # table_key: an interval table's id as text, or collar / survey / assay; row_id: that table's row.
        joins += [
            "LEFT JOIN map_interval_table mt ON mt.old = CASE WHEN s.table_key ~ '^[0-9]+$' THEN s.table_key::bigint END",
            "LEFT JOIN map_collar mc ON s.table_key = 'collar' AND mc.old = s.row_id",
            "LEFT JOIN map_survey ms ON s.table_key = 'survey' AND ms.old = s.row_id",
            "LEFT JOIN map_assay ma ON s.table_key = 'assay' AND ma.old = s.row_id",
            "LEFT JOIN map_interval_row mr ON s.table_key ~ '^[0-9]+$' AND mr.old = s.row_id",
        ]
        exprs["table_key"] = "CASE WHEN s.table_key ~ '^[0-9]+$' THEN COALESCE(mt.new::text, '0') ELSE s.table_key END"
        # A row deleted before the export has no new id: 0 refers to nothing (undo makes a new row).
        exprs["row_id"] = "COALESCE(mc.new, ms.new, ma.new, mr.new, 0)"
    select = ", ".join(exprs.get(c, f"s.{_q(c)}") for c in cols)
    result = session.execute(text(
        f"INSERT INTO {t.name} ({', '.join(_q(c) for c in cols)}) SELECT {select} FROM {stg} s {' '.join(joins)}"
    ), params)
    return result.rowcount or 0


def _load_map(session: Session, table: str) -> dict:
    return {old: new for old, new in session.execute(text(f"SELECT old, new FROM map_{table}"))}


# ---- ids inside JSON


def _num(v) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.isdigit():
        return int(v)
    return None


def remap_refs(value, maps: dict[str, dict]):
    """Chart settings and style bodies: set ids (`setId`), sample tables (`table`, an interval table id
    as text) and styles (`style` / `palette` / `template` = {"id", "linked"}), at any depth."""
    if isinstance(value, list):
        return [remap_refs(v, maps) for v in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for k, v in value.items():
        if k in ("setId", "set_id") and _num(v) is not None:
            out[k] = maps["dataset"].get(_num(v), v)
        elif k == "table" and isinstance(v, str) and v.isdigit():
            new = maps["interval_table"].get(int(v))
            out[k] = str(new) if new is not None else v
        elif k in ("style", "palette", "template") and isinstance(v, dict) and _num(v.get("id")) is not None:
            new = maps["style"].get(_num(v["id"]))
            out[k] = {**remap_refs(v, maps), "id": new} if new is not None else {**remap_refs(v, maps), "id": None, "linked": False}
        else:
            out[k] = remap_refs(v, maps)
    return out


_SOURCE_KINDS = {"wireframe": "wireframe", "lines": "line_layer", "drillholes": "dataset"}


def _remap_operation(op, maps: dict[str, dict]):
    if not isinstance(op, dict) or not isinstance(op.get("sources"), list):
        return op
    sources = []
    for s in op["sources"]:
        if isinstance(s, dict) and s.get("kind") in _SOURCE_KINDS and _num(s.get("id")) is not None:
            # A source outside the project (or deleted) has no new id.
            s = {**s, "id": maps[_SOURCE_KINDS[s["kind"]]].get(_num(s["id"]))}
        sources.append(s)
    return {**op, "sources": sources}


def _fix_json(session: Session, project_id: int, maps: dict[str, dict]) -> None:
    def update(table: str, column: str, rows, fn) -> None:
        for row_id, value in rows:
            new = fn(value)
            if new != value:
                session.execute(text(f"UPDATE {table} SET {column} = CAST(:v AS jsonb) WHERE id = :id"),
                                {"v": json.dumps(new, ensure_ascii=False), "id": row_id})

    ds = "SELECT id FROM dataset WHERE project_id = :p"
    p = {"p": project_id}
    update("dataset", "meta", session.execute(text(
        "SELECT id, meta FROM dataset WHERE project_id = :p AND meta ? 'operation'"), p),
        lambda m: {**m, "operation": _remap_operation(m["operation"], maps)})
    update("wireframe", "stats", session.execute(text(
        f"SELECT id, stats FROM wireframe WHERE dataset_id IN ({ds}) AND stats ? 'operation'"), p),
        lambda s: {**s, "operation": _remap_operation(s["operation"], maps)})
    update("wireframe", "dtm", session.execute(text(
        f"SELECT id, dtm FROM wireframe WHERE dataset_id IN ({ds}) AND dtm ? 'raster_id'"), p),
        lambda d: {**d, "raster_id": maps["raster"].get(_num(d["raster_id"])) if _num(d["raster_id"]) is not None else d["raster_id"]})
    update("chart", "settings", session.execute(text(
        f"SELECT id, settings FROM chart WHERE dataset_id IN ({ds})"), p), lambda s: remap_refs(s, maps))
    update("style", "body", session.execute(text("SELECT id, body FROM style WHERE project_id = :p"), p),
           lambda b: remap_refs(b, maps))


# ---- the import


def import_project(session: Session, path: Path, *, password: str | None = None, name: str | None = None,
                   mode: str = "copy", progress: Progress = lambda f, m: None,
                   log: Callable[[str, str], None] = lambda t, l: None) -> dict:
    """Restores the archive as a new project. mode: "copy" (a free name like "name (2)" when taken),
    "rename" (exactly `name`; fails when taken) or "replace" (the project called `name` is replaced).
    The caller commits."""
    work = work_root() / uuid.uuid4().hex
    work.mkdir()
    try:
        data = _extract(path, password, work, progress)
        manifest = data["manifest"]
        try:
            path_revs = versions.upgrade_path(manifest.get("schema_revision"))
        except ValueError:
            raise TooNew("Файл записан более новой версией BedrockForge: обновите программу") from None

        wanted = (name or manifest["project"]["name"] or "Проект").strip()[:MAX_NAME] or "Проект"
        replaced_id = None
        if mode == "replace":
            replaced_id = session.execute(text("SELECT id FROM project WHERE name = :n"), {"n": wanted}).scalar()
            final = wanted
            temp = free_name(session, f"{wanted} (импорт)")
        elif mode == "rename":
            if session.execute(text("SELECT 1 FROM project WHERE name = :n"), {"n": wanted}).first():
                raise ImportFailed(f"Проект «{wanted}» уже есть: выберите другое название")
            final = temp = wanted
        else:
            final = temp = free_name(session, wanted)

        progress(0.62, "Загрузка таблиц")
        present = [t for t in TABLES if t.name in data["tables"]]
        if not any(t.name == "project" for t in present):
            raise Corrupted("В файле нет таблицы проекта")
        for k, t in enumerate(present):
            progress(0.62 + 0.2 * k / len(present), f"Таблица {t.name}")
            _stage(session, t, data["tables"][t.name])
        for rev in path_revs:
            for statement in versions.UPGRADES.get(rev, []):
                session.execute(text(statement))
        if path_revs:
            log(f"Файл из версии схемы {manifest.get('schema_revision')}: обновлён до {versions.head()}", "info")

        counts: dict[str, int] = {}
        project = {"name": temp}
        for k, t in enumerate(present):
            progress(0.82 + 0.1 * k / len(present), f"Запись: {t.name}")
            _map(session, t)
        # data_edit refers to the row maps; tables missing from an old archive get empty maps.
        for t in TABLES:
            if t.name not in data["tables"]:
                session.execute(text(f"CREATE TEMP TABLE map_{t.name} (old bigint PRIMARY KEY, new bigint) ON COMMIT DROP"))
        for t in present:
            counts[t.name] = _insert(session, t, project)
        project_id = session.execute(text("SELECT new FROM map_project LIMIT 1")).scalar()
        if project_id is None:
            raise Corrupted("В файле нет проекта")

        maps = {t: _load_map(session, t) for t in RETURNED_MAPS}
        _fix_json(session, project_id, maps)

        progress(0.94, "Файлы хранилища")
        index = data["index"] or {}
        for sha, src in data["blobs"].items():
            blobs.put_file(session, src, sha=sha, media_type=(index.get(sha) or {}).get("media_type"))
        lost = [sha for sha in index if sha not in data["blobs"]]
        if lost:
            log(f"В файле не хватает файлов хранилища: {len(lost)}", "warning")

        if mode == "replace" and replaced_id is not None:
            session.execute(text("DELETE FROM project WHERE id = :id"), {"id": replaced_id})
            session.execute(text("UPDATE project SET name = :n WHERE id = :id"), {"n": final, "id": project_id})
            log(f"Проект «{final}» заменён", "info")
        elif mode == "replace":
            session.execute(text("UPDATE project SET name = :n WHERE id = :id"), {"n": final, "id": project_id})
        session.execute(text("DROP TABLE IF EXISTS " + ", ".join(
            [f"stg_{t.name}" for t in present] + [f"map_{t.name}" for t in TABLES])))
        progress(0.99, "Сохранение")
        return {
            "project_id": project_id, "name": final, "replaced_project_id": replaced_id,
            "source": manifest["project"], "schema_revision": manifest.get("schema_revision"),
            "counts": counts, "files": len(data["blobs"]),
            "maps": {t: {str(k): v for k, v in m.items()} for t, m in maps.items()},
            "browser": data["browser"],
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)
