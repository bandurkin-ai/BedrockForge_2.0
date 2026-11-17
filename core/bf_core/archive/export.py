"""Export of a project into `.bfggis` (ТЗ 7.4). Reads a `VACUUM INTO` snapshot: one consistent state of the
project, and no long read transaction on the live database. The project is not changed.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import sqlalchemy as sa

import bf_core
from bf_core.db import dialect, migrate
from bf_core.db.schema import blobs, project_md, project_meta
from bf_core.storage.blobs import BlobStore

from .container import ArchiveWriter
from .rows import encode_row
from .spec import APP, TABLES, Table

Progress = Callable[[float, str], None]
Log = Callable[[str, str], None]


def snapshot(engine: sa.Engine, folder: Path) -> Path:
    """A copy of the project; objects and drillhole tables still being built by jobs (state='building') are dropped."""
    target = folder / "cache" / f"export-{uuid.uuid4().hex}.sqlite"
    target.parent.mkdir(parents=True, exist_ok=True)
    dialect.vacuum_into(engine, str(target))
    snap = dialect.create_engine(dialect.sqlite_url(str(target)))
    try:
        from bf_core.db.schema import dh_tables, objects
        from bf_core.jobs.store import drop_partial

        with dialect.write_tx(snap) as conn:
            for job_id in conn.execute(
                sa.select(objects.c.job_id).where(objects.c.state == "building").distinct()
            ).scalars():
                drop_partial(conn, job_id)
            conn.execute(sa.delete(objects).where(objects.c.state == "building"))
            # Tables being built into a ready set (an import still running) are not part of the project yet.
            from bf_core.drillholes.store import drop_building

            for job_id in conn.execute(
                sa.select(dh_tables.c.job_id).where(dh_tables.c.state == "building").distinct()
            ).scalars():
                drop_building(conn, job_id)  # with the holes it alone brought
            conn.execute(sa.delete(dh_tables).where(dh_tables.c.state == "building"))
    finally:
        snap.dispose()
    return target


class _Reading:
    """A file read by tar that reports progress (and lets a cancel through) inside a big member."""

    def __init__(self, f: Any, on_bytes: Callable[[int], None]) -> None:
        self._f, self._on = f, on_bytes

    def read(self, n: int = -1) -> bytes:
        data = self._f.read(n)
        self._on(len(data))
        return bytes(data)


def _rows_of(t: Table, include_view: bool, query: sa.Select[Any]) -> sa.Select[Any]:
    """The rows a table puts into the archive: without the view scope when view settings are left out."""
    table = project_md.tables[t.name]
    query = query.select_from(table)
    if t.view_scope and not include_view:
        query = query.where(table.c.scope != t.view_scope)
    return query


def manifest_of(conn: sa.Connection, include_view: bool) -> dict[str, Any]:
    meta = conn.execute(sa.select(project_meta)).mappings().one()
    counts = {
        t.name: conn.execute(_rows_of(t, include_view, sa.select(sa.func.count()))).scalar_one()
        for t in TABLES
        if include_view or not t.view
    }
    files = conn.execute(sa.select(sa.func.count(), sa.func.coalesce(sa.func.sum(blobs.c.size), 0))).one()
    from bf_core.projects.store import normalize_description, summary

    return {
        "format": "bfggis",
        "app": APP,
        "app_version": bf_core.__version__,
        "schema_revision": migrate.head("project"),
        "exported_at": dialect.utcnow().isoformat(),
        "project": {
            "uid": meta["uid"].hex,
            "name": meta["name"],
            "description": normalize_description(meta["description"]),
            "created_at": meta["created_at"].isoformat(),
        },
        "summary": summary(conn),
        "tables": counts,
        "files": int(files[0]),
        "file_bytes": int(files[1]),
        "view_settings": include_view,
    }


def export_project(
    engine: sa.Engine,
    folder: Path,
    out_path: Path,
    *,
    password: str | None = None,
    include_view: bool = True,
    progress: Progress = lambda _f, _m: None,
    log: Log = lambda _t, _l: None,
) -> dict[str, Any]:
    snap = snapshot(engine, folder)
    part = out_path.with_name(out_path.name + ".part")
    snap_engine = dialect.create_engine(dialect.sqlite_url(str(snap), read_only=True))
    store = BlobStore(folder)
    try:
        with dialect.read_tx(snap_engine) as conn:
            manifest = manifest_of(conn, include_view)
            files = conn.execute(
                sa.select(blobs.c.sha256, blobs.c.size, blobs.c.media_type).order_by(blobs.c.sha256)
            ).all()
            missing = [f.sha256 for f in files if not store.exists(f.sha256)]
            if missing:
                log(f"Файлов нет в хранилище: {len(missing)} (будут пропущены)", "warn")
            missing_set = set(missing)
            present = [f for f in files if f.sha256 not in missing_set]
            manifest["missing_files"] = missing
            rows_total = sum(manifest["tables"].values())
            total = max(1, rows_total * 200 + sum(int(f.size) for f in present))
            done = 0
            header = {
                "app": APP,
                "app_version": bf_core.__version__,
                "schema_revision": manifest["schema_revision"],
                "created_at": manifest["exported_at"],
                "summary": {**manifest["project"], "summary": manifest["summary"], "files": len(present)},
            }
            checksums: dict[str, str] = {}
            with ArchiveWriter(part, header, password) as w:
                raw = json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")
                w.add_bytes("manifest.json", raw)
                checksums["manifest.json"] = hashlib.sha256(raw).hexdigest()
                for t in TABLES:
                    if t.view and not include_view:
                        continue
                    progress(done / total, f"Таблица {t.name}")
                    table = project_md.tables[t.name]
                    order = list(table.primary_key.columns)
                    name = f"db/{t.name}.jsonl"
                    work = folder / "cache" / f"export-{uuid.uuid4().hex}.jsonl"
                    h = hashlib.sha256()
                    try:
                        with open(work, "wb") as out:  # streamed: tables of millions of rows (Э3)
                            rows = conn.execution_options(yield_per=10_000).execute(
                                _rows_of(t, include_view, sa.select(table)).order_by(*order)
                            )
                            for k, r in enumerate(rows.mappings()):
                                line = encode_row(table, dict(r))
                                h.update(line)
                                out.write(line)
                                if k % 5000 == 4999:
                                    progress(done / total, f"Таблица {t.name}")
                        w.add_file(name, work)
                    finally:
                        work.unlink(missing_ok=True)
                    checksums[name] = h.hexdigest()
                    done += manifest["tables"][t.name] * 200
                index = {f.sha256: {"size": int(f.size), "media_type": f.media_type} for f in present}
                raw = json.dumps(index).encode()
                w.add_bytes("blobs.json", raw)
                checksums["blobs.json"] = hashlib.sha256(raw).hexdigest()
                for k, f in enumerate(present):
                    msg = f"Файлы хранилища: {k + 1} из {len(present)}"
                    progress(done / total, msg)
                    sent = {"n": 0}

                    def moved(n: int, msg: str = msg, sent: dict[str, int] = sent, base: int = done) -> None:
                        sent["n"] += n
                        progress((base + sent["n"]) / total, msg)

                    with store.open(f.sha256) as fh:
                        w.add_stream(f"blobs/{f.sha256}", int(f.size), _Reading(fh, moved))  # type: ignore[arg-type]
                    done += int(f.size)
                w.add_bytes("checksums.json", json.dumps(checksums, indent=1).encode())
        os.replace(part, out_path)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    finally:
        snap_engine.dispose()
        snap.unlink(missing_ok=True)
    return {
        "path": str(out_path),
        "bytes": out_path.stat().st_size,
        "rows": rows_total,
        "files": len(present),
        "missing_files": len(missing),
        "encrypted": bool(password),
        "name": manifest["project"]["name"],
    }
