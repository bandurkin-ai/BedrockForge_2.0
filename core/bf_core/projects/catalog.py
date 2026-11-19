"""The catalog of the data of the project — «Менеджер данных» (ТЗ 7.35 Д-20; ADR 0010 §2, ADR 0017 §6, decisions
10–11): one row per data object over `objects`, not a file system (ADR 0002 §5: the blobs stay what they are).

A row: `{id, kind, name, folder, format, size, rows, modified, crs, state}`:

* `kind` — `drillhole_set`, `wireframe`, `dtm` (a wireframe with `dtm`), `line_file`, `cloud` (a line file
  with a cloud layer), `raster`, `block_model`; views, series, charts and styles are not data (decision 11);
* `folder` — `objects.folder` («Участок 1/Борт», «/» between levels), null — the root;
* `format` — the extension of the file the object was loaded from («STR», «TRIDB»; a set of several files —
  «CSV, TXT»), null — made in the program;
* `size` — bytes of the blobs of the object, of its layers and of its imports; a blob several owners share (a
  pack of small layers `pack:<n>`, the source file of the wireframes of one TRIDB) counts by its share — its
  size over the number of its owners (decision 10);
* `rows` — of a drillhole set: its rows in the database (collars, surveys and the rows of its tables);
* `modified` — ISO time of `objects.updated_at`; `crs` — `objects.crs`; `state` — `ready` | `building`.

`page` filters (`folder` — that folder and the folders in it, "" — the root only; `kind`; `q` — in the name
and the folder, without case), sorts (missing values last) and cuts a page; `folders` — the folders of the user
with the number of objects in each; `move` — many objects into a folder in one transaction.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import PureWindowsPath
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db.schema import blob_owners, blobs, dh_holes, dh_surveys, dh_tables, import_log, objects, wireframes

DATA_KINDS = ("drillhole_set", "wireframe", "line_file", "raster", "block_model")
KINDS = ("drillhole_set", "wireframe", "dtm", "line_file", "cloud", "raster", "block_model")
#: the kind a row shows → the kind of its object
OBJECT_KIND = {"dtm": "wireframe", "cloud": "line_file"}
SORTS = ("name", "kind", "folder", "format", "size", "rows", "modified", "crs", "state")
MAX_LIMIT = 5000
MAX_FOLDER = 200
MAX_MOVE = 20_000
_CHUNK = 900  # ids in one IN (…): under the variable limit of every SQLite


def _chunks(ids: Sequence[Any]) -> Iterable[Sequence[Any]]:
    for start in range(0, len(ids), _CHUNK):
        yield ids[start : start + _CHUNK]


def norm_folder(folder: str | None) -> str | None:
    """«a//b/ » → «a/b»; empty → None (the root). `\\` is a separator too."""
    if not folder:
        return None
    parts = [p.strip() for p in str(folder).replace("\\", "/").split("/")]
    path = "/".join(p for p in parts if p)
    return path or None


# --- the rows -------------------------------------------------------------------------------------------------


def rows(conn: sa.Connection) -> list[dict[str, Any]]:
    """Every row of the catalog, unsorted."""
    objs = conn.execute(
        sa.select(
            objects.c.id, objects.c.kind, objects.c.name, objects.c.folder, objects.c.meta, objects.c.crs,
            objects.c.state, objects.c.updated_at,
        ).where(objects.c.kind.in_(DATA_KINDS))
    ).all()  # fmt: skip
    if not objs:
        return []
    ids = [int(r.id) for r in objs]
    kind = {int(r.id): str(r.kind) for r in objs}
    files = [i for i in ids if kind[i] == "line_file"]
    layer_of: dict[int, int] = {}  # layer → its file
    clouds: set[int] = set()
    for chunk in _chunks(files):
        for lr in conn.execute(
            sa.select(objects.c.id, objects.c.parent_id, objects.c.meta).where(
                objects.c.parent_id.in_(chunk), objects.c.kind == "line_layer"
            )
        ):
            layer_of[int(lr.id)] = int(lr.parent_id)
            if isinstance((lr.meta or {}).get("cloud"), dict):
                clouds.add(int(lr.parent_id))
    dtm: set[int] = set()
    for chunk in _chunks([i for i in ids if kind[i] == "wireframe"]):
        for w in conn.execute(
            sa.select(wireframes.c.object_id, wireframes.c.dtm).where(wireframes.c.object_id.in_(chunk))
        ):
            if w.dtm:
                dtm.add(int(w.object_id))
    logs: dict[int, int] = {}  # import → its object
    formats: dict[int, list[str]] = {}
    for chunk in _chunks(ids):
        for lg in conn.execute(
            sa.select(import_log.c.id, import_log.c.object_id, import_log.c.file_name, import_log.c.format)
            .where(import_log.c.object_id.in_(chunk))
            .order_by(import_log.c.id.desc())
        ):  # fmt: skip
            obj = int(lg.object_id)
            logs[int(lg.id)] = obj
            label = _format_label(lg.file_name, lg.format)
            found = formats.setdefault(obj, [])
            if label and label not in found and (kind[obj] == "drillhole_set" or not found):
                found.append(label)
    size = _sizes(conn, ids, layer_of, logs)
    counts = _set_rows(conn, [i for i in ids if kind[i] == "drillhole_set"])
    out = []
    for r in objs:
        obj = int(r.id)
        shown = "dtm" if obj in dtm else "cloud" if obj in clouds else kind[obj]
        out.append({
            "id": obj, "kind": shown, "name": str(r.name), "folder": norm_folder(r.folder),
            "format": ", ".join(sorted(formats[obj])) if obj in formats else None, "size": size.get(obj, 0),
            "rows": counts.get(obj, 0) if kind[obj] == "drillhole_set" else None,
            "modified": r.updated_at.isoformat() if r.updated_at is not None else None, "crs": r.crs,
            "state": str(r.state),
        })  # fmt: skip
    return out


def _format_label(file_name: Any, fmt: Any) -> str | None:
    suffix = PureWindowsPath(str(file_name or "")).suffix.lstrip(".")
    if suffix and len(suffix) <= 6:
        return suffix.upper()
    return str(fmt).upper() if fmt else None


def _sizes(conn: sa.Connection, ids: list[int], layer_of: dict[int, int], logs: dict[int, int]) -> dict[int, int]:
    """Bytes of each object: its blobs, those of its layers and of its imports; a blob of several owners counts
    by the share of the owners that are this object's (its size × theirs / all its owners)."""
    owner_of: dict[tuple[str, int], int] = {("object", i): i for i in ids}
    owner_of.update({("object", layer): file for layer, file in layer_of.items()})
    owner_of.update({("import", log): obj for log, obj in logs.items()})
    held: dict[str, dict[int, set[tuple[str, int]]]] = {}  # blob → object of the catalog → its owners of it
    for owner_kind, owner_ids in (("object", [*ids, *layer_of]), ("import", list(logs))):
        for chunk in _chunks(owner_ids):
            for b in conn.execute(
                sa.select(blob_owners.c.sha256, blob_owners.c.owner_id).where(
                    blob_owners.c.owner_kind == owner_kind, blob_owners.c.owner_id.in_(chunk)
                )
            ):
                owner = (owner_kind, int(b.owner_id))
                held.setdefault(str(b.sha256), {}).setdefault(owner_of[owner], set()).add(owner)
    if not held:
        return {}
    shas = sorted(held)
    owners: dict[str, set[tuple[str, int]]] = {}
    length: dict[str, int] = {}
    for chunk in _chunks(shas):
        for b in conn.execute(
            sa.select(blob_owners.c.sha256, blob_owners.c.owner_kind, blob_owners.c.owner_id).where(
                blob_owners.c.sha256.in_(chunk)
            )
        ):
            owners.setdefault(str(b.sha256), set()).add((str(b.owner_kind), int(b.owner_id)))
        for b in conn.execute(sa.select(blobs.c.sha256, blobs.c.size).where(blobs.c.sha256.in_(chunk))):
            length[str(b.sha256)] = int(b.size)
    out: dict[int, float] = {}
    for sha, by_object in held.items():
        everyone = max(1, len(owners.get(sha, ())))
        for obj, mine in by_object.items():
            out[obj] = out.get(obj, 0.0) + length.get(sha, 0) * len(mine) / everyone
    return {k: int(round(v)) for k, v in out.items()}


def _set_rows(conn: sa.Connection, sets: list[int]) -> dict[int, int]:
    out: dict[int, int] = {}
    for chunk in _chunks(sets):
        for table, column in ((dh_holes, None), (dh_surveys, None), (dh_tables, dh_tables.c.row_count)):
            agg = sa.func.count() if column is None else sa.func.sum(column)
            for set_id, n in conn.execute(
                sa.select(table.c.set_id, agg).where(table.c.set_id.in_(chunk)).group_by(table.c.set_id)
            ):
                out[int(set_id)] = out.get(int(set_id), 0) + int(n or 0)
    return out


# --- the page, the folders, the move ---------------------------------------------------------------------------


def _key(sort: str) -> Any:
    def key(row: dict[str, Any]) -> tuple[Any, ...]:
        v = row.get(sort)
        if isinstance(v, str):
            v = v.casefold()
        return (v is None, v if v is not None else 0, row["name"].casefold(), row["id"])

    return key


def in_folder(row_folder: str | None, folder: str | None) -> bool:
    """A row of `row_folder` is shown in `folder`: the root shows its own rows, a folder — also those of its folders."""
    if folder is None:
        return row_folder is None
    return row_folder is not None and (row_folder == folder or row_folder.startswith(folder + "/"))


def page(
    conn: sa.Connection,
    *,
    folder: str | None = None,
    kind: str | None = None,
    q: str | None = None,
    sort: str = "name",
    desc: bool = False,
    offset: int = 0,
    limit: int = 500,
) -> dict[str, Any]:
    """`GET /catalog`: {total, rows} of the rows found, sorted, from `offset`, at most `limit`."""
    if sort not in SORTS:
        raise ApiError(422, "bad_sort", f"Неизвестная колонка сортировки «{sort}».", f"Выберите: {', '.join(SORTS)}.")
    if kind and kind not in KINDS:
        raise ApiError(422, "bad_kind", f"Неизвестный тип данных «{kind}».", "Выберите тип в списке слева.")
    found = rows(conn)
    if folder is not None:
        f = norm_folder(folder)
        found = [r for r in found if in_folder(r["folder"], f)]
    if kind:
        found = [r for r in found if r["kind"] == kind]
    if q and q.strip():
        needle = q.strip().casefold()
        found = [r for r in found if needle in r["name"].casefold() or needle in (r["folder"] or "").casefold()]
    nones = [r for r in found if r.get(sort) is None]
    some = sorted((r for r in found if r.get(sort) is not None), key=_key(sort), reverse=desc)
    ordered = some + sorted(nones, key=_key("name"))  # missing values last either way
    limit = max(1, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))
    return {"total": len(ordered), "rows": ordered[offset : offset + limit]}


def folders(conn: sa.Connection) -> dict[str, Any]:
    """`GET /catalog/folders`: the folders of the user with the number of data objects right in each."""
    counted: dict[str, int] = {}
    for folder, n in conn.execute(
        sa.select(objects.c.folder, sa.func.count())
        .where(objects.c.kind.in_(DATA_KINDS), objects.c.folder.is_not(None))
        .group_by(objects.c.folder)
    ):  # fmt: skip
        f = norm_folder(folder)
        if f is not None:
            counted[f] = counted.get(f, 0) + int(n)
    return {"folders": [{"folder": f, "count": n} for f, n in sorted(counted.items(), key=lambda x: x[0].casefold())]}


def move(conn: sa.Connection, ids: Sequence[int], folder: str | None) -> dict[str, list[int]]:
    """Objects of the catalog into `folder` (None or "" — the root) in the caller's transaction. Returns the ids
    moved by the kind of their objects (for `objects.changed`). ApiError: 404 `object_not_found`, 422
    `folder_too_long`, `too_many_objects`."""
    wanted = list(dict.fromkeys(int(i) for i in ids))
    if len(wanted) > MAX_MOVE:
        raise ApiError(422, "too_many_objects", "Объектов для переноса больше 20 000.", "Перенесите их частями.")
    target = norm_folder(folder)
    if target is not None and len(target) > MAX_FOLDER:
        raise ApiError(
            422, "folder_too_long", f"Путь папки длиннее {MAX_FOLDER} символов.", "Выберите папку с коротким именем."
        )
    by_kind: dict[str, list[int]] = {}
    for chunk in _chunks(wanted):
        for r in conn.execute(sa.select(objects.c.id, objects.c.kind).where(objects.c.id.in_(chunk))):
            if str(r.kind) in DATA_KINDS:
                by_kind.setdefault(str(r.kind), []).append(int(r.id))
    if sum(len(v) for v in by_kind.values()) != len(wanted):
        raise ApiError(404, "object_not_found", "Часть объектов не найдена.", "Обновите менеджер данных и повторите.")
    for chunk in _chunks(wanted):
        conn.execute(sa.update(objects).where(objects.c.id.in_(chunk)).values(folder=target))
    return by_kind
