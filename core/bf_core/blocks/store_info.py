"""The list and the info of the block models of the project (`GET /blocks`, `GET /blocks/{id}`; ADR 0017 §3, ADR 0023
§2): the rows of `objects` of the kind `block_model` as the API tells them — the grid, the fields, the checks (meta v2,
v3: `meta.public_meta`) or the older form (v1). Split from `store.py` (the file is at most 600 lines); `store.py` names
`list_block_models` and `block_model_info` as before."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db.schema import blob_owners, blobs, objects

from . import meta as bmeta
from . import schema as bs

OWNER = "object"


def not_found() -> ApiError:
    return ApiError(404, "block_model_not_found", "Блочная модель не найдена.", "Обновите дерево проекта.")


_COLUMNS = (
    objects.c.id, objects.c.name, objects.c.folder, objects.c.version, objects.c.meta, objects.c.extent,
    objects.c.crs, objects.c.created_at, objects.c.updated_at,
)  # fmt: skip


def _size(conn: sa.Connection, ids: Sequence[int]) -> dict[int, int]:
    rows = conn.execute(
        sa.select(blob_owners.c.owner_id, blobs.c.size)
        .join(blobs, blobs.c.sha256 == blob_owners.c.sha256)
        .where(
            blob_owners.c.owner_kind == OWNER,
            sa.or_(blob_owners.c.role == bs.ROLE, blob_owners.c.role.like(bs.FLD + "%")),
            blob_owners.c.owner_id.in_(ids),
        )
    ).all()
    out: dict[int, int] = {}
    for r in rows:  # `cells` and the files `fld:` of the model
        out[int(r.owner_id)] = out.get(int(r.owner_id), 0) + int(r.size)
    return out


def _public(row: Mapping[str, Any], size: int | None, brief: bool) -> dict[str, Any]:
    meta = dict(row["meta"] or {})
    if meta.get("v", 1) in (bs.META_V2, bmeta.META_V3):  # e17-api.md §4: the meta gives the grid, the fields, checks
        head = {k: row[k] for k in ("name", "folder", "extent", "crs")}
        return {**head, "id": int(row["id"]), "version": int(row["version"]), "size": size,
                **bmeta.public_meta(meta, brief=brief), "created_at": row["created_at"].isoformat(),
                "updated_at": row["updated_at"].isoformat()}  # fmt: skip
    grid = dict(meta.get("grid") or {})
    variable = "tensors" in grid
    if brief:
        grid.pop("tensors", None)
    fields = meta.get("fields") or []
    if brief:
        fields = [{k: f.get(k) for k in ("name", "kind", "unit")} for f in fields]
    return {
        "id": int(row["id"]), "name": row["name"], "folder": row["folder"], "version": int(row["version"]),
        "extent": row["extent"], "crs": row["crs"], "grid": {**grid, "variable": variable},
        "cells": int(meta.get("cells") or 0), "fields": fields, "source": meta.get("source"),
        "warnings": list(meta.get("warnings") or []), "size": size,
        "created_at": row["created_at"].isoformat(), "updated_at": row["updated_at"].isoformat(),
    }  # fmt: skip


def _rows(conn: sa.Connection, *where: Any, brief: bool) -> list[dict[str, Any]]:
    rows = (
        conn.execute(
            sa.select(*_COLUMNS)
            .where(objects.c.kind == bs.KIND, objects.c.state == "ready", *where)
            .order_by(objects.c.sort_key, objects.c.name_key, objects.c.id)
        )
        .mappings()
        .all()
    )
    sizes = _size(conn, [int(r["id"]) for r in rows])
    return [_public(dict(r), sizes.get(int(r["id"])), brief) for r in rows]


def list_block_models(conn: sa.Connection) -> list[dict[str, Any]]:
    """Block models of the tree: grid without `tensors` (`variable` says if they exist), fields without stats."""
    return _rows(conn, brief=True)


def block_model_info(conn: sa.Connection, bm_id: int) -> dict[str, Any]:
    """Everything «Свойства» show: the grid (tensors too), the fields with units and stats, the source."""
    found = _rows(conn, objects.c.id == bm_id, brief=False)
    if not found:
        raise not_found()
    return found[0]
