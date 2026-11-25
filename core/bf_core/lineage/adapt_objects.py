"""Providers of the objects of geostatistics and QAQC: `object:variogram_model`, `object:trend`,
`object:qaqc_data`, `object:qaqc_report` (ADR 0025 §2–3, §6).

Each module keeps what its object was built from in its own form; the provider gives the nodes they name now
(`inputs`) and calls the module's own check of the direct inputs (`direct`) — `geostat.variogram.store.freshness`,
`qaqc.service.source_state`, `qaqc.report.stale_of` — without rewriting them. A trend is a copy of what it was made
from (вопрос № 56: it does not follow its model): a source that changes with its `version`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from bf_core.db.schema import dh_tables, objects

from .providers import Ctx, Direct, Info, Key, Provider, register
from .store import InputRef, Node


def object_rows(ctx: Ctx, kind: str, keys: Sequence[Key]) -> dict[int, Any]:
    """The rows (id, name, version, state, meta) of objects of `kind`, read once per request."""
    cache: dict[int, Any] = ctx.cache.setdefault(("object_rows", kind), {})
    need = [k[0] for k in keys if k[0] not in cache]
    if need:
        q = sa.select(objects.c.id, objects.c.name, objects.c.version, objects.c.state, objects.c.meta).where(
            objects.c.kind == kind, objects.c.id.in_(need)
        )
        for r in ctx.conn.execute(q):
            cache[int(r.id)] = r
    return {k[0]: cache[k[0]] for k in keys if k[0] in cache}


def set_tables(ctx: Ctx, set_id: int) -> list[Any]:
    cache: dict[int, list[Any]] = ctx.cache.setdefault("set_tables_full", {})
    if set_id not in cache:
        q = sa.select(dh_tables.c.id, dh_tables.c.name, dh_tables.c.name_key).where(
            dh_tables.c.set_id == set_id, dh_tables.c.state == "ready"
        )
        cache[set_id] = list(ctx.conn.execute(q))
    return cache[set_id]


class VariogramModel(Provider):
    kind = "object:variogram_model"

    def lookup(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, Info]:
        return {
            (i, ""): Info(str((r.meta or {}).get("sha256") or int(r.version)), r.name, r.state == "building")
            for i, r in object_rows(ctx, "variogram_model", keys).items()
        }

    def inputs(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, list[InputRef]]:
        """The tables the points were read from (found as `freshness` finds them: by id, then by name) and, when the
        trajectories mattered, the set."""
        out: dict[Key, list[InputRef]] = {}
        for i, r in object_rows(ctx, "variogram_model", keys).items():
            meta = r.meta or {}
            set_id, inputs = (meta.get("source") or {}).get("set_id"), meta.get("inputs") or {}
            refs: list[InputRef] = []
            if set_id is not None:
                now = set_tables(ctx, int(set_id))
                by_id = {int(t.id): t for t in now}
                by_key = {t.name_key: t for t in now}
                for t in inputs.get("tables") or []:
                    cur = by_id.get(t.get("id")) if t.get("id") is not None else None
                    if cur is None or cur.name_key != t.get("name_key"):
                        cur = by_key.get(t.get("name_key"))
                    if cur is not None:
                        refs.append(InputRef(Node("dh_table", int(cur.id)), None, cur.name))
                if inputs.get("trajectories"):
                    refs.append(InputRef(Node("dh_set", int(set_id)), None, ""))
            out[(i, "")] = refs
        return out

    def direct(self, ctx: Ctx, key: Key) -> Direct | None:
        from bf_core.geostat.variogram import store

        r = object_rows(ctx, "variogram_model", [key]).get(key[0])
        if r is None:
            return None
        fresh = store.freshness(ctx.conn, r.meta or {})
        if not fresh["stale"]:
            return None
        return str(fresh["stale_reason"]), bool(fresh["source_lost"])


class Trend(Provider):
    kind = "object:trend"

    def lookup(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, Info]:
        return {
            (i, ""): Info(str(int(r.version)), r.name, r.state == "building")
            for i, r in object_rows(ctx, "trend", keys).items()
        }


class QaqcData(Provider):
    kind = "object:qaqc_data"

    def lookup(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, Info]:
        return {
            (i, ""): Info(str(int(r.version)), r.name, r.state == "building")
            for i, r in object_rows(ctx, "qaqc_data", keys).items()
        }

    def inputs(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, list[InputRef]]:
        from bf_core.qaqc import service

        out: dict[Key, list[InputRef]] = {}
        for i, r in object_rows(ctx, "qaqc_data", keys).items():
            src = (r.meta or {}).get("source") or {}
            t = service.find_table(ctx.conn, src) if src.get("kind") == "table" else None
            out[(i, "")] = [] if t is None else [InputRef(Node("dh_table", int(t.id)), None, t.name)]
        return out

    def direct(self, ctx: Ctx, key: Key) -> Direct | None:
        from bf_core.qaqc import service

        r = object_rows(ctx, "qaqc_data", [key]).get(key[0])
        if r is None:
            return None
        src = (r.meta or {}).get("source") or {}
        state = service.source_state(ctx.conn, r.meta or {})
        if state["source_missing"]:
            return f"удалена таблица «{src.get('table_name') or '?'}»", True
        if state["stale"]:
            return f"изменена таблица «{src.get('table_name') or '?'}»", False
        return None


class QaqcReport(Provider):
    kind = "object:qaqc_report"
    REASONS = {
        "data": ("изменены данные QAQC", False),
        "config": ("изменены настройки QAQC (стандарты, CRM, пределы)", False),
    }

    def lookup(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, Info]:
        return {
            (i, ""): Info(str((r.meta or {}).get("inputs_sha256") or int(r.version)), r.name, r.state == "building")
            for i, r in object_rows(ctx, "qaqc_report", keys).items()
        }

    def _data_by_uid(self, ctx: Ctx) -> dict[Any, tuple[int, str]]:
        if "qaqc_uids" not in ctx.cache:
            q = sa.select(objects.c.id, objects.c.name, objects.c.meta).where(
                objects.c.kind == "qaqc_data", objects.c.state == "ready"
            )
            ctx.cache["qaqc_uids"] = {(r.meta or {}).get("uid"): (int(r.id), r.name) for r in ctx.conn.execute(q)}
        return dict(ctx.cache["qaqc_uids"])

    def inputs(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, list[InputRef]]:
        data = self._data_by_uid(ctx)
        out: dict[Key, list[InputRef]] = {}
        for i, r in object_rows(ctx, "qaqc_report", keys).items():
            hit = data.get((r.meta or {}).get("data_uid"))
            out[(i, "")] = [] if hit is None else [InputRef(Node("object:qaqc_data", hit[0]), None, hit[1])]
        return out

    def direct(self, ctx: Ctx, key: Key) -> Direct | None:
        from bf_core.qaqc import report

        r = object_rows(ctx, "qaqc_report", [key]).get(key[0])
        if r is None:
            return None
        if "qaqc_now" not in ctx.cache:
            ctx.cache["qaqc_now"] = report.now_of(ctx.conn)
        code = report.stale_of(ctx.cache["qaqc_now"], r.meta or {})
        if code is None:
            return None
        if code == "source_deleted":
            return f"удалены данные QAQC «{(r.meta or {}).get('data_name') or '?'}»", True
        return self.REASONS.get(code, (code, False))


for _p in (VariogramModel(), Trend(), QaqcData(), QaqcReport()):
    register(_p)

__all__ = ["QaqcData", "QaqcReport", "Trend", "VariogramModel", "object_rows", "set_tables"]
