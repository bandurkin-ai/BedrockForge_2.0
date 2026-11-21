"""QAQC data objects (ADR 0020 §2): `objects(kind="qaqc_data", parent_id=NULL)`, ≤ 50 in a project, the records —
a Parquet blob of the owner (`object`, role `data`: schema.BLOB_ROLES has no `records`, and the schema does not
change in Э14). No migration: `.bfggis` takes them with `objects`, `blob_owners`, `settings`.

    staged = stage(store, recs)                                  # outside the write transaction: temp file, SHA-256
    data_id = create(conn, store, staged, name=…, meta=make_meta(recs, counts, notes, source, mapping))
    replace(conn, store, data_id, staged, meta)                  # a new import into the same object: version + 1
    info(conn, data_id, config) / list_data(conn, config)        # the form of `GET /data/{id}` (ADR 0020 §10)
    rename(conn, data_id, name, expected_version); delete(conn, data_id)
    open_records(project, data_id)                               # records.open_file through the column cache

`meta` (`v: 1`): `uid`, `source` (`{kind: "file", name, sha256}` or `{kind: "table", set_id, table_id, set_name,
table_name, rev}`), `mapping`, `elements`, `fields`, `counts`, `notes`, `rows`, `labs`, `batches`, `date_from`,
`date_to`, `type_codes` [{code, n}]. Stale («устарели»): a table source whose `rev` grew, or that is gone
(`source_missing`); after a `.bfggis` import the ids of the set and the table are new — they are found again by
the names of the set and the table. Errors: 404 `qaqc_data_not_found`, 409 `name_taken`, `version_conflict
{current}`, 422 `too_many_data`, `name_empty`; 409 `qaqc_data_broken` — the file of records is not in the project.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, dh_tables, objects
from bf_core.storage.blobs import CHUNK, BlobStore

KIND = "qaqc_data"
OWNER = "object"
ROLE = "data"
MAX_DATA = 50
MAX_NAME = 200
Staged = tuple[Path, str, int]


def not_found() -> ApiError:
    return ApiError(404, "qaqc_data_not_found", "Данные QAQC не найдены.", "Обновите список данных QAQC.")


def broken() -> ApiError:
    return ApiError(409, "qaqc_data_broken", "Файл записей QAQC не найден в проекте.", "Импортируйте данные заново.")


# --- staging the records -------------------------------------------------------------------------------------------


def stage(store: BlobStore, recs: Any, tick: Callable[[], None] | None = None) -> Staged:
    """The records written to a temp file of the store (fsync) with their SHA-256 — outside a transaction."""
    from . import records as r

    store.tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = store.tmp_dir / f"{uuid.uuid4().hex}.parquet"
    try:
        r.write(recs, tmp, tick)
        h = hashlib.sha256()
        with open(tmp, "rb+") as fh:
            for chunk in iter(lambda: fh.read(CHUNK), b""):
                h.update(chunk)
            os.fsync(fh.fileno())
        return tmp, h.hexdigest(), tmp.stat().st_size
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def discard(staged: Staged | None) -> None:
    if staged is not None:
        staged[0].unlink(missing_ok=True)


# --- meta ------------------------------------------------------------------------------------------------------


def iso(ms: float) -> str | None:
    if ms != ms:  # NaN
        return None
    dt = datetime(1970, 1, 1) + timedelta(milliseconds=ms)
    return dt.date().isoformat() if dt.time() == datetime.min.time() else dt.isoformat(timespec="seconds")


def make_meta(
    recs: Any, counts: Mapping[str, Any], notes: list[str], source: Mapping[str, Any], mapping: Mapping[str, Any]
) -> dict[str, Any]:
    """The meta of a data object: what the list, the filters and the setup window show without the file."""
    import numpy as np

    from . import records as r

    sch = recs.schema
    types = recs.text(r.TYPE_CODE)
    n_type = np.bincount(types.codes + 1, minlength=len(types.labels) + 1)
    type_codes = [{"code": None, "n": int(n_type[0])}] if n_type[0] else []
    type_codes += [{"code": c, "n": int(k)} for c, k in zip(types.labels, n_type[1:].tolist(), strict=True) if k]
    lab, batch = recs.text(r.LAB), recs.text(r.BATCH)
    has = batch.codes >= 0
    pairs = np.unique(lab.codes[has].astype(np.int64) * (len(batch.labels) + 1) + batch.codes[has])
    dates = recs.date()
    ok = dates[~np.isnan(dates)]
    fields = [{"name": r.SAMPLE_ID, "kind": "text"}, {"name": r.HOLE, "kind": "text"},
              {"name": r.PARENT_ID, "kind": "text"}, {"name": r.SEQ, "kind": "number"},
              {"name": r.DATE, "kind": "date"}]  # fmt: skip
    fields += [{"name": r.v(e.name), "kind": "number", "element": e.name} for e in sch.elements]
    fields += [{"name": r.x(f.name), "kind": f.kind, "title": f.name} for f in sch.extra]
    return {
        "v": 1,
        "uid": uuid.uuid4().hex,
        "source": dict(source),
        "mapping": dict(mapping),
        "elements": [{"name": e.name, "unit": e.unit, "dl_field": e.has_dl} for e in sch.elements],
        "fields": fields,
        "counts": dict(counts),
        "notes": list(notes),
        "rows": int(recs.n),
        "labs": [s for s in lab.labels if s],
        "batches": int(len(pairs)),
        "date_from": iso(float(ok.min())) if len(ok) else None,
        "date_to": iso(float(ok.max())) if len(ok) else None,
        "type_codes": type_codes,
    }


# --- objects -------------------------------------------------------------------------------------------------------


def _clean(name: Any) -> str:
    text = " ".join(str(name or "").split())[:MAX_NAME]
    if not text:
        raise ApiError(422, "name_empty", "Название не может быть пустым.", "Введите название данных QAQC.",
                       extra={"param": "name"})  # fmt: skip
    return text


def _taken(conn: sa.Connection, name: str, but: int | None = None) -> bool:
    q = sa.select(objects.c.id).where(objects.c.kind == KIND, objects.c.name_key == dialect.name_key(name))
    if but is not None:
        q = q.where(objects.c.id != but)
    return conn.execute(q).first() is not None


def name_taken(name: str) -> ApiError:
    return ApiError(409, "name_taken", f"Данные QAQC «{name}» уже есть.", "Выберите другое название или замените их.",
                    extra={"param": "name"})  # fmt: skip


def check_new(conn: sa.Connection, name: Any, replace_id: int | None = None) -> str:
    """Before a job starts: the name is free (or is that of `replace_id`), the limit of 50 is not reached."""
    clean = _clean(name)
    if replace_id is not None:
        _row(conn, replace_id)
        if _taken(conn, clean, but=replace_id):
            raise name_taken(clean)
        return clean
    if _taken(conn, clean):
        raise name_taken(clean)
    count = conn.execute(sa.select(sa.func.count()).select_from(objects).where(objects.c.kind == KIND)).scalar_one()
    if int(count) >= MAX_DATA:
        raise ApiError(422, "too_many_data", f"В проекте уже {MAX_DATA} наборов данных QAQC.",
                       "Удалите ненужные данные QAQC или замените существующие повторным импортом.")  # fmt: skip
    return clean


def create(
    conn: sa.Connection, store: BlobStore, staged: Staged, *, name: str, meta: dict[str, Any], author: str | None = None
) -> int:
    """Inside the write transaction: the object and its blob (the temp file is moved into place)."""
    clean = check_new(conn, name)
    now = dialect.utcnow()
    res = conn.execute(
        sa.insert(objects).values(
            kind=KIND, name=clean, name_key=dialect.name_key(clean), parent_id=None, sort_key=0.0, state="ready",
            version=1, meta=meta, author=author, created_at=now, updated_at=now,
        )
    )  # fmt: skip
    data_id = int(res.inserted_primary_key[0])  # type: ignore[index]
    store.commit_staged(conn, staged, media_type="application/vnd.apache.parquet", owner_kind=OWNER,
                        owner_id=data_id, role=ROLE)  # fmt: skip
    return data_id


def replace(
    conn: sa.Connection, store: BlobStore, data_id: int, staged: Staged, meta: dict[str, Any], name: str | None = None
) -> int:
    """A new import into the object: the blob replaced, `version` + 1, the uid kept. → the new version."""
    row = _row(conn, data_id)
    clean = check_new(conn, name, data_id) if name is not None else row.name
    meta = {**meta, "uid": (row.meta or {}).get("uid", meta.get("uid"))}
    conn.execute(sa.delete(blob_owners).where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == data_id))
    store.commit_staged(conn, staged, media_type="application/vnd.apache.parquet", owner_kind=OWNER,
                        owner_id=data_id, role=ROLE)  # fmt: skip
    version = int(row.version) + 1
    conn.execute(
        sa.update(objects).where(objects.c.id == data_id).values(
            meta=meta, name=clean, name_key=dialect.name_key(clean), version=version, updated_at=dialect.utcnow()
        )
    )  # fmt: skip
    return version


def _row(conn: sa.Connection, data_id: int) -> Any:
    r = conn.execute(
        sa.select(objects.c.id, objects.c.name, objects.c.version, objects.c.meta, objects.c.author,
                  objects.c.created_at, objects.c.updated_at)
        .where(objects.c.id == data_id, objects.c.kind == KIND, objects.c.state == "ready")
    ).first()  # fmt: skip
    if r is None:
        raise not_found()
    return r


def sha_of(conn: sa.Connection, data_id: int) -> str:
    sha = conn.execute(
        sa.select(blob_owners.c.sha256).where(
            blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == data_id, blob_owners.c.role == ROLE
        )
    ).scalar()
    if sha is None:
        raise broken()
    return str(sha)


def find_table(conn: sa.Connection, source: Mapping[str, Any]) -> Any:
    """The table of a table source: by id (when its set still is the one named), else by the names of the set and
    the table (ids are new after a `.bfggis` import). None — gone."""
    q = sa.select(dh_tables.c.id, dh_tables.c.set_id, dh_tables.c.rev, dh_tables.c.name).join(
        objects, objects.c.id == dh_tables.c.set_id
    ).where(dh_tables.c.state == "ready", objects.c.kind == "drillhole_set")  # fmt: skip
    set_key = dialect.name_key(str(source.get("set_name", "")))
    by_id = conn.execute(
        q.where(dh_tables.c.id == source.get("table_id"), dh_tables.c.set_id == source.get("set_id"))
    ).first()
    if by_id is not None:
        return by_id
    return conn.execute(
        q.where(
            objects.c.name_key == set_key, dh_tables.c.name_key == dialect.name_key(str(source.get("table_name")))
        ).order_by(dh_tables.c.id)
    ).first()


def source_state(conn: sa.Connection, meta: Mapping[str, Any]) -> dict[str, Any]:
    """{stale, source_missing, rev}: «устарели» — the table changed since the snapshot, or is gone."""
    source = meta.get("source") or {}
    if source.get("kind") != "table":
        return {"stale": False, "source_missing": False, "rev": None}
    t = find_table(conn, source)
    if t is None:
        return {"stale": True, "source_missing": True, "rev": None}
    return {"stale": int(t.rev) != int(source.get("rev", -1)), "source_missing": False, "rev": int(t.rev),
            "set_id": int(t.set_id), "table_id": int(t.id)}  # fmt: skip


def _public(conn: sa.Connection, row: Any, config: Mapping[str, Any] | None) -> dict[str, Any]:
    from .config import get_config, type_of

    meta = dict(row.meta or {})
    cfg = config if config is not None else get_config(conn)
    sha = conn.execute(
        sa.select(blob_owners.c.sha256).where(
            blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == row.id, blob_owners.c.role == ROLE
        )
    ).scalar()
    state = source_state(conn, meta)
    return {
        "id": int(row.id), "name": row.name, "version": int(row.version), "rows": meta.get("rows", 0),
        "elements": [{"name": e["name"], "unit": e.get("unit")} for e in meta.get("elements", [])],
        "fields": meta.get("fields", []), "labs": meta.get("labs", []), "batches": meta.get("batches", 0),
        "date_from": meta.get("date_from"), "date_to": meta.get("date_to"),
        "type_codes": [{**t, "type": type_of(dict(cfg), t["code"])} for t in meta.get("type_codes", [])],
        "source": meta.get("source"), "mapping": meta.get("mapping"), "stale": state["stale"],
        "source_missing": state["source_missing"], "sha256": sha, "counts": meta.get("counts", {}),
        "notes": meta.get("notes", []), "author": row.author, "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }  # fmt: skip


def info(conn: sa.Connection, data_id: int, config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return _public(conn, _row(conn, data_id), config)


def list_data(conn: sa.Connection, config: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    from .config import get_config

    cfg = config if config is not None else get_config(conn)
    rows = conn.execute(
        sa.select(objects.c.id, objects.c.name, objects.c.version, objects.c.meta, objects.c.author,
                  objects.c.created_at, objects.c.updated_at)
        .where(objects.c.kind == KIND, objects.c.state == "ready")
        .order_by(objects.c.name_key, objects.c.id)
    ).all()  # fmt: skip
    return [_public(conn, row, cfg) for row in rows]


def rename(conn: sa.Connection, data_id: int, name: Any, expected_version: Any) -> dict[str, Any]:
    current = info(conn, data_id)
    if current["version"] != expected_version:
        raise ApiError(409, "version_conflict", "Данные QAQC изменили, пока вы их правили.", "Проверьте и повторите.",
                       extra={"current": current})  # fmt: skip
    clean = _clean(name)
    if _taken(conn, clean, but=data_id):
        raise name_taken(clean)
    n = conn.execute(
        sa.update(objects)
        .where(objects.c.id == data_id, objects.c.version == expected_version)
        .values(name=clean, name_key=dialect.name_key(clean), version=expected_version + 1,
                updated_at=dialect.utcnow())
    ).rowcount  # fmt: skip
    if n != 1:
        raise ApiError(409, "version_conflict", "Данные QAQC изменили, пока вы их правили.", "Проверьте и повторите.",
                       extra={"current": info(conn, data_id)})  # fmt: skip
    return info(conn, data_id)


def delete(conn: sa.Connection, data_id: int) -> dict[str, Any]:
    """The object and its file references (the file goes with the next collection of the store)."""
    _row(conn, data_id)
    shas = list(
        conn.execute(
            sa.select(blob_owners.c.sha256).where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == data_id)
        ).scalars()
    )
    conn.execute(sa.delete(blob_owners).where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == data_id))
    conn.execute(sa.delete(objects).where(objects.c.id == data_id))
    return {"id": data_id, "shas": shas}


def open_records(project: Any, data_id: int) -> Any:
    """The records of a data object, columns through `records.CACHE` (scope — the project)."""
    from . import records as r

    with dialect.read_tx(project.engine) as conn:
        _row(conn, data_id)
        sha = sha_of(conn, data_id)
    path = BlobStore(project.folder).path_of(sha)
    if not path.is_file():
        raise broken()
    return r.open_file(path, sha=sha, scope=project.uid)
