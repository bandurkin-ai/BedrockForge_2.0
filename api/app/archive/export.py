"""Writing a whole project into a `.bfggis` file (app/archive/container.py)."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app import __version__
from app.archive import versions
from app.archive.container import ArchiveWriter
from app.archive.spec import BLOB_OWNERS, TABLES, scope_sql
from app.storage import blobs

Progress = Callable[[float, str], None]
# A rough size of one table row in the progress estimate.
_ROW_BYTES = 120


class ExportFailed(Exception):
    pass


def _raw(session: Session):
    return session.connection().connection.driver_connection


def project_blobs(session: Session, project_id: int) -> list[tuple[str, int, str | None]]:
    """(sha256, size, media type) of every stored file the project refers to."""
    refs = " UNION ".join(
        f"SELECT value FROM {t}, jsonb_each_text({t}.blobs) WHERE value IS NOT NULL AND {scope_sql(t, project_id)}"
        for t in BLOB_OWNERS
    )
    rows = session.execute(text(f"SELECT sha256, size, media_type FROM blob WHERE sha256 IN ({refs}) ORDER BY sha256"))
    return [(r[0], int(r[1]), r[2]) for r in rows]


def summary(session: Session, project_id: int) -> dict:
    """What the archive will hold: rows per table and the stored files."""
    p = session.execute(text("SELECT name, description, created_at FROM project WHERE id = :id"),
                        {"id": project_id}).first()
    if p is None:
        raise ExportFailed("Проект не найден")
    tables = {t.name: int(session.execute(text(f"SELECT count(*) FROM {t.name} WHERE {scope_sql(t.name, project_id)}")).scalar())
              for t in TABLES}
    files = project_blobs(session, project_id)
    return {"name": p[0], "description": p[1] or "", "created_at": p[2].isoformat() if p[2] else None,
            "tables": tables, "files": len(files), "file_bytes": sum(f[1] for f in files)}


def _dump_table(session: Session, table: str, project_id: int, dest: Path) -> tuple[int, str]:
    """COPY of the project's rows into a CSV file with a header; returns (bytes, sha256)."""
    h = hashlib.sha256()
    size = 0
    with dest.open("wb") as out, _raw(session).cursor() as cur:
        with cur.copy(f"COPY (SELECT * FROM {table} WHERE {scope_sql(table, project_id)} ORDER BY 1) "
                      "TO STDOUT WITH (FORMAT csv, HEADER true)") as copy:
            for chunk in copy:
                data = bytes(chunk)
                out.write(data)
                h.update(data)
                size += len(data)
    return size, h.hexdigest()


def export_project(session_factory: Callable[[], Session], project_id: int, out_path: Path, *,
                   password: str | None = None, browser: dict | None = None,
                   progress: Progress = lambda f, m: None, log: Callable[[str, str], None] = lambda t, l: None) -> dict:
    """Writes the project to `out_path`. Everything is read from one snapshot of the database."""
    with session_factory() as session:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        info = summary(session, project_id)
        revision = session.execute(text("SELECT version_num FROM alembic_version")).scalar() or versions.head()
        files = project_blobs(session, project_id)
        missing = [sha for sha, _, _ in files if not blobs.exists(sha)]
        if missing:
            log(f"Файлов нет в хранилище: {len(missing)} (будут пропущены)", "warning")
        present = [f for f in files if f[0] not in set(missing)]
        rows_total = sum(info["tables"].values())
        total = max(1, rows_total * _ROW_BYTES + sum(f[1] for f in present))
        done = 0
        now = datetime.now(timezone.utc).isoformat()
        manifest = {
            "format": "bfggis", "app_version": __version__, "schema_revision": revision, "exported_at": now,
            "project": {"id": project_id, "name": info["name"], "description": info["description"],
                        "created_at": info["created_at"]},
            "tables": info["tables"], "files": len(present), "file_bytes": sum(f[1] for f in present),
            "missing_files": missing, "browser": browser is not None,
        }
        header = {"app_version": __version__, "schema_revision": revision, "created_at": now,
                  "summary": {**manifest["project"], "tables": info["tables"], "files": manifest["files"],
                              "file_bytes": manifest["file_bytes"]}}
        checksums: dict[str, str] = {}
        work = blobs.tmp_path(".csv")
        try:
            with ArchiveWriter(out_path, header, password) as w:
                w.add_bytes("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1).encode())
                for t in TABLES:
                    progress(done / total, f"Таблица {t.name}")
                    _, sha = _dump_table(session, t.name, project_id, work)
                    w.add_file(f"db/{t.name}.csv", work)
                    checksums[f"db/{t.name}.csv"] = sha
                    done += info["tables"][t.name] * _ROW_BYTES
                index = {sha: {"size": size, "media_type": mt} for sha, size, mt in present}
                raw = json.dumps(index).encode()
                w.add_bytes("blobs.json", raw)
                checksums["blobs.json"] = hashlib.sha256(raw).hexdigest()
                for k, (sha, size, _) in enumerate(present):
                    progress(done / total, f"Файлы хранилища: {k + 1} из {len(present)}")
                    w.add_file(f"blobs/{sha}", blobs.path_of(sha))
                    done += size
                if browser is not None:
                    raw = json.dumps(browser, ensure_ascii=False).encode()
                    w.add_bytes("browser.json", raw)
                    checksums["browser.json"] = hashlib.sha256(raw).hexdigest()
                w.add_bytes("checksums.json", json.dumps(checksums, indent=1).encode())
            session.rollback()
        except BaseException:
            out_path.unlink(missing_ok=True)
            raise
        finally:
            work.unlink(missing_ok=True)
    return {"bytes": out_path.stat().st_size, "rows": rows_total, "files": len(present),
            "file_bytes": manifest["file_bytes"], "missing_files": len(missing), "encrypted": bool(password),
            "name": info["name"]}


def csv_columns(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return next(csv.reader(io.StringIO(f.readline())), [])
