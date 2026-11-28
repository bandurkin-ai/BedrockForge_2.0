"""Providers of block models: `object:block_model` and `bm_field` (ADR 0025 §2, §6).

A field of a block model is a node of its own, `(bm_field, model id, casefolded name of the field)`: an estimation
writes a field, not the whole model, and a report reads fields. Its fingerprint is the file the field lives in
(`cells` or `fld:<key>`, new for every rewrite) and the time of the write. The model's fingerprint is the SHA-256 of
its `cells` file (the grid and the rows), so adding a field does not make everything built on the grid «устарел».

What Э17 keeps stays where it is: the inputs of a model made by a fill, a code or a regularization are
`meta.inputs` (`blocks.fill.inputs.stale_reason`), the fields of the calculator, the density and the classification
have their own check (`fields_ops.stale_of`, needs the store of the project: without `ctx.store` it is skipped).
The fields of an estimation (Э18) keep their inputs in `lineage` (`lineage.record` of the job).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db.schema import blob_owners

from .adapt_drill import LOST_WORDS
from .adapt_objects import object_rows
from .providers import Ctx, Direct, Info, Key, Provider, register
from .store import InputRef, Node

MODEL_MAKERS = ("fill", "code", "regularize")
CELLS = "cells"


def _model_inputs(meta: Mapping[str, Any]) -> list[InputRef]:
    from bf_core.blocks.fill.inputs import names_in

    names = names_in(meta)
    raw = meta.get("inputs") or {}
    out: list[InputRef] = []
    for key, kind in (("wireframes", "wireframe"), ("model", "object:block_model")):
        for item in raw.get(key) or []:
            oid = int(item["id"])
            out.append(InputRef(Node(kind, oid), None, names.get(key, {}).get(oid) or f"№ {oid}"))
    return out


def _model_reason(ctx: Ctx, meta: Mapping[str, Any]) -> Direct | None:
    from bf_core.blocks.fill.inputs import names_in, stale_reason

    reason = stale_reason(ctx.conn, meta.get("inputs"), names_in(meta))
    return None if reason is None else (reason, reason.startswith(LOST_WORDS))


class BlockModel(Provider):
    kind = "object:block_model"

    def lookup(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, Info]:
        rows = object_rows(ctx, "block_model", keys)
        shas = {
            int(r.owner_id): str(r.sha256)
            for r in ctx.conn.execute(
                sa.select(blob_owners.c.owner_id, blob_owners.c.sha256).where(
                    blob_owners.c.owner_kind == "object",
                    blob_owners.c.role == CELLS,
                    blob_owners.c.owner_id.in_(list(rows)),
                )  # fmt: skip
            )
        }
        return {
            (i, ""): Info(shas.get(i) or str(int(r.version)), r.name, r.state == "building") for i, r in rows.items()
        }

    def inputs(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, list[InputRef]]:
        out: dict[Key, list[InputRef]] = {}
        for i, r in object_rows(ctx, "block_model", keys).items():
            if (r.meta or {}).get("inputs"):  # a model made by a fill / a regularization: the rest are sources
                out[(i, "")] = _model_inputs(r.meta)
        return out

    def direct(self, ctx: Ctx, key: Key) -> Direct | None:
        r = object_rows(ctx, "block_model", [key]).get(key[0])
        return None if r is None else _model_reason(ctx, r.meta or {})


def _field(row: Any, sub: str) -> dict[str, Any] | None:
    return next((f for f in (row.meta or {}).get("fields") or [] if str(f.get("name", "")).casefold() == sub), None)


class BmField(Provider):
    kind = "bm_field"

    def lookup(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, Info]:
        rows = object_rows(ctx, "block_model", keys)
        out: dict[Key, Info] = {}
        for i, sub in keys:
            r = rows.get(i)
            f = None if r is None else _field(r, sub)
            if r is not None and f is not None:
                src = f.get("source") or {}
                out[(i, sub)] = Info(f"{f.get('file')}|{src.get('at')}", f"{r.name}.{f['name']}", r.state == "building")
        return out

    def inputs(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, list[InputRef]]:
        out: dict[Key, list[InputRef]] = {}
        rows = object_rows(ctx, "block_model", keys)
        for i, sub in keys:
            f = None if i not in rows else _field(rows[i], sub)
            if f is not None and (f.get("source") or {}).get("op") in MODEL_MAKERS:
                out[(i, sub)] = _model_inputs(rows[i].meta)
        return out

    def direct(self, ctx: Ctx, key: Key) -> Direct | None:
        r = object_rows(ctx, "block_model", [key]).get(key[0])
        f = None if r is None else _field(r, key[1])
        op = ((f or {}).get("source") or {}).get("op")
        if r is None or f is None:
            return None
        if op in MODEL_MAKERS:
            return _model_reason(ctx, r.meta or {})
        if op in ("calc", "density", "classify") and ctx.store is not None:
            from bf_core.blocks import fields_ops
            from bf_core.blocks.model import ANY, Model

            try:
                model = Model.open(ctx.conn, ctx.store, key[0], ANY)
                stale, why = fields_ops.stale_of(ctx.conn, ctx.store, model, f)
            except ApiError:
                return "Нет входов: файлы модели недоступны.", True
            return (why or "изменились входы поля", False) if stale else None
        return None


for _p in (BlockModel(), BmField()):
    register(_p)

__all__ = ["BlockModel", "BmField"]
