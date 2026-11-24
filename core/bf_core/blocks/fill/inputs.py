"""The inputs of a fill or a coding and «устарел» (подшаг 17.20; ADR 0023 §6; docs/progress/e17-api.md §2).

    wireframe_sha(conn, id)        # SHA-256 of the geometry blobs of a wireframe; None — no such wireframe
    model_sha(conn, id)            # SHA-256 of all the files of a block model (`cells`, `fld:…`); None — no model
    inputs_of(conn, wires, model)  # the `inputs` of the model: {wireframes: [{id, sha}], model: [{id, sha}]}
    names_in(meta)                 # the names of the inputs the job kept ({"wireframes": {id: name}, "model": {…}})
    stale_reason(conn, inputs, names)   # None — current; else «изменён каркас «…»» / «удалён каркас «…»» /
                                        #        «изменена модель «…»» / «удалена модель «…»»

`stale` of a model (`GET /blocks`) is `stale_reason(conn, meta["inputs"], names_in(meta)) is not None`. A wireframe
edited in place gets new geometry blobs (they are immutable files), so its sha changes; a rename or a colour does not.
Light: no numpy.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

import sqlalchemy as sa

from bf_core.db.schema import blob_owners, objects

OWNER = "object"
WIREFRAME = "wireframe"
BLOCK = "block_model"


def _digest(pairs: Mapping[str, str]) -> str:
    text = json.dumps(sorted(pairs.items()), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _exists(conn: sa.Connection, kind: str, object_id: int) -> bool:
    return (
        conn.execute(
            sa.select(objects.c.id).where(objects.c.kind == kind, objects.c.id == object_id, objects.c.state == "ready")
        ).first()
        is not None
    )


def wireframe_sha(conn: sa.Connection, wf_id: int) -> str | None:
    """The geometry of the wireframe (its `xyz` and `tri` blobs, or its entry of a pack) as one hash."""
    from bf_core.wireframes import files as wf_files

    if not _exists(conn, WIREFRAME, wf_id):
        return None
    shas = wf_files.blob_shas(conn, wf_id)
    return _digest(shas) if shas else None


def model_sha(conn: sa.Connection, model_id: int) -> str | None:
    """Every file of the model {role: sha256} as one hash: the rows and all the fields."""
    if not _exists(conn, BLOCK, model_id):
        return None
    rows = conn.execute(
        sa.select(blob_owners.c.role, blob_owners.c.sha256).where(
            blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == model_id
        )
    ).all()
    return _digest({str(r.role): str(r.sha256) for r in rows}) if rows else None


def inputs_of(
    conn: sa.Connection, wireframes: Sequence[int], model_id: int | None = None
) -> dict[str, list[dict[str, Any]]]:
    """`meta.inputs` of a fill (`wireframes`) or of a coding with splitting (`model` too), as read now."""
    out: dict[str, list[dict[str, Any]]] = {
        "wireframes": [{"id": int(i), "sha": wireframe_sha(conn, int(i)) or ""} for i in wireframes]
    }
    if model_id is not None:
        out["model"] = [{"id": int(model_id), "sha": model_sha(conn, int(model_id)) or ""}]
    return out


def _name(conn: sa.Connection, object_id: int, kind: str) -> str:
    row = conn.execute(sa.select(objects.c.name).where(objects.c.kind == kind, objects.c.id == object_id)).first()
    return str(row.name) if row else f"№ {object_id}"


def names_in(meta: Mapping[str, Any]) -> dict[str, dict[int, str]]:
    """The names the job kept in `source.params.names`: {"wireframes": {id: name}, "model": {id: name}}."""
    raw = ((meta.get("source") or {}).get("params") or {}).get("names") or {}
    return {k: {int(i): str(n) for i, n in (raw.get(k) or {}).items()} for k in ("wireframes", "model")}


def stale_reason(
    conn: sa.Connection, inputs: Mapping[str, Any] | None, names: Mapping[str, Mapping[int, str]] | None = None
) -> str | None:
    """Why the model is out of date: the first input that is gone or whose sha differs. `names` — `names_in(meta)`:
    the names of the objects that are gone (the project does not know them any more)."""
    if not inputs:
        return None
    for key, kind, gone, changed in (
        ("wireframes", WIREFRAME, "удалён каркас", "изменён каркас"),
        ("model", BLOCK, "удалена модель", "изменена модель"),
    ):
        for item in inputs.get(key) or []:
            oid, sha = int(item["id"]), str(item["sha"])
            now = wireframe_sha(conn, oid) if kind == WIREFRAME else model_sha(conn, oid)
            if now is None:
                label = ((names or {}).get(key) or {}).get(oid) or f"№ {oid}"
                return f"{gone} «{label}»"
            if now != sha:
                return f"{changed} «{_name(conn, oid, kind)}»"
    return None
