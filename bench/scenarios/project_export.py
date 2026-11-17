"""Export of a project into .bfggis (ТЗ 5.3: a project of 2 GB ≤ 2 min, ≤ 1 GB of memory; 7.4).

Synthetic project: incompressible stored files (the worst case for zstd) in blobs of 64 MB and object rows.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

BLOB_MB = 64


def prepare(params: dict[str, Any], work: Path) -> None:
    from bf_core.db import dialect
    from bf_core.db.schema import objects
    from bf_core.projects.project import create_folder
    from bf_core.storage.blobs import BlobStore

    folder = create_folder(work, "Замер", {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    store = BlobStore(folder)
    now = dialect.utcnow()
    try:
        left = int(params["mb"])
        n = 0
        while left > 0:
            size = min(BLOB_MB, left) << 20
            with dialect.write_tx(engine) as conn:
                oid = conn.execute(
                    objects.insert().values(
                        kind="wireframe",
                        name=f"Каркас {n}",
                        name_key=f"каркас {n}",
                        sort_key=float(n),
                        state="ready",
                        version=1,
                        meta={"n": n},
                        created_at=now,
                        updated_at=now,
                    )
                ).inserted_primary_key[0]
                store.put_bytes(
                    conn,
                    os.urandom(size),
                    media_type="application/x-npy",
                    owner_kind="object",
                    owner_id=oid,
                )
            left -= BLOB_MB
            n += 1
        dialect.checkpoint(engine)
    finally:
        engine.dispose()
    (work / "folder.txt").write_text(str(folder), encoding="utf-8")


def load() -> None:
    import bf_core.archive.export  # noqa: F401


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.archive.export import export_project
    from bf_core.db import dialect

    folder = Path((work / "folder.txt").read_text(encoding="utf-8"))
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    out = work / "out.bfggis"
    try:
        result = export_project(
            engine, folder, out, password="замер" if params.get("password") else None
        )
    finally:
        engine.dispose()
        out.unlink(missing_ok=True)
    return {"bytes": result["bytes"], "files": result["files"]}
