"""Providers of the data of drillhole sets and wireframes: `dh_set`, `dh_table`, `dh_result`, `wireframe`
(ADR 0025 §2–3, §6).

`dh_set` and `wireframe` are sources (no inputs). A `dh_table` is a source too, except a table made by a result
of sample processing (composites…): that one is built from its result (`dh_result`). A `dh_result` keeps its
inputs in the record itself (`dh_results.inputs`, names and revisions, no ids — ADR 0018 §2): the provider reads
them as they are and gives the nodes they name now; the direct check is `composites.results._reason` (and, for the
ore intervals, `routes_ore.stale_reason`), called as it is.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db.schema import dh_results, dh_tables, objects

from .providers import Ctx, Direct, Info, Key, Provider, register
from .store import InputRef, Node

LOST_WORDS = ("удалён", "удалена", "удалено")


def _ids(keys: Sequence[Key]) -> list[int]:
    return [k[0] for k in keys]


class DhSet(Provider):
    kind = "dh_set"

    def lookup(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, Info]:
        q = sa.select(objects.c.id, objects.c.name, objects.c.version, objects.c.state).where(
            objects.c.kind == "drillhole_set", objects.c.id.in_(_ids(keys))
        )
        return {(int(r.id), ""): Info(str(int(r.version)), r.name, r.state == "building") for r in ctx.conn.execute(q)}


class DhTable(Provider):
    kind = "dh_table"

    def lookup(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, Info]:
        from bf_core.composites.results import table_token

        q = sa.select(
            dh_tables.c.id, dh_tables.c.name, dh_tables.c.rev, dh_tables.c.created_at, dh_tables.c.state
        ).where(dh_tables.c.id.in_(_ids(keys)))
        return {
            (int(r.id), ""): Info(f"{table_token(r.created_at)}:{int(r.rev)}", r.name, r.state == "building")
            for r in ctx.conn.execute(q)
        }

    def inputs(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, list[InputRef]]:
        """A table made by an operation of `TABLE_OPS` is built from that result."""
        from bf_core.composites.results import TABLE_OPS

        q = sa.select(dh_results.c.id, dh_results.c.table_id, dh_results.c.name).where(
            dh_results.c.table_id.in_(_ids(keys)), dh_results.c.op.in_(sorted(TABLE_OPS))
        )
        return {
            (int(r.table_id), ""): [InputRef(Node("dh_result", int(r.id)), None, r.name)] for r in ctx.conn.execute(q)
        }


class DhResult(Provider):
    kind = "dh_result"

    def _rows(self, ctx: Ctx, keys: Sequence[Key]) -> dict[int, Any]:
        cache: dict[int, Any] = ctx.cache.setdefault("dh_result_rows", {})
        need = [i for i in _ids(keys) if i not in cache]
        if need:
            q = sa.select(
                dh_results.c.id, dh_results.c.set_id, dh_results.c.op, dh_results.c.name, dh_results.c.inputs,
                dh_results.c.sha256, dh_results.c.algo,
            ).where(dh_results.c.id.in_(need))  # fmt: skip
            for r in ctx.conn.execute(q):
                cache[int(r.id)] = r
        return {i: cache[i] for i in _ids(keys) if i in cache}

    def lookup(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, Info]:
        return {(i, ""): Info(f"{r.sha256}:{r.algo}", r.name) for i, r in self._rows(ctx, keys).items()}

    def _tables(self, ctx: Ctx, set_id: int) -> dict[str, tuple[int, str]]:
        cache: dict[int, dict[str, tuple[int, str]]] = ctx.cache.setdefault("set_tables", {})
        if set_id not in cache:
            q = sa.select(dh_tables.c.id, dh_tables.c.name_key, dh_tables.c.name).where(
                dh_tables.c.set_id == set_id, dh_tables.c.state == "ready"
            )
            cache[set_id] = {r.name_key: (int(r.id), r.name) for r in ctx.conn.execute(q)}
        return cache[set_id]

    def inputs(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, list[InputRef]]:
        from bf_core.composites import results

        out: dict[Key, list[InputRef]] = {}
        for i, r in self._rows(ctx, keys).items():
            rec = r.inputs or {}
            refs: list[InputRef] = []
            tables = self._tables(ctx, int(r.set_id))
            for t in rec.get("tables") or []:
                hit = tables.get(str(t.get("key")))
                if hit is not None:  # a table that is gone is told by the direct check
                    refs.append(InputRef(Node("dh_table", hit[0]), None, hit[1]))
            if rec.get("set"):
                refs.append(InputRef(Node("dh_set", int(r.set_id)), None, ""))
            for m in rec.get("meshes") or []:
                try:
                    wid = results.current_meshes(ctx.conn, {"meshes": [m]})[0]
                except ApiError:
                    continue
                refs.append(InputRef(Node("wireframe", wid), None, str(m.get("name") or "")))
            out[(i, "")] = refs
        return out

    def direct(self, ctx: Ctx, key: Key) -> Direct | None:
        from bf_core.composites import results

        r = self._rows(ctx, [key]).get(key[0])
        if r is None:
            return None
        set_id = int(r.set_id)
        if r.op == "ore":  # + the rules of the intervals (routes_ore.stale_reason); the module is loaded when needed
            from bf_core.composites import routes_ore
            from bf_core.composites.ore_job import ALGO

            rec = {"set_id": set_id, "inputs": r.inputs, "algo": r.algo}
            reason = routes_ore.stale_reason(ctx.conn, rec, ALGO)
        else:
            states: dict[int, Any] = ctx.cache.setdefault("results_state", {})
            if set_id not in states:
                states[set_id] = results._state(ctx.conn, set_id)
            reason = results._reason(ctx.conn, states[set_id], r.inputs or {})
        return None if reason is None else (reason, reason.startswith(LOST_WORDS))


class Wireframe(Provider):
    kind = "wireframe"

    def lookup(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, Info]:
        from bf_core.viz.meshes import mesh_state

        q = sa.select(objects.c.id, objects.c.name, objects.c.version, objects.c.state).where(
            objects.c.kind == "wireframe", objects.c.id.in_(_ids(keys))
        )
        out: dict[Key, Info] = {}
        for r in ctx.conn.execute(q):
            try:  # the same key the results of sample processing keep (ТД35-2: the same geometry — the same state)
                fp = mesh_state(ctx.conn, int(r.id)).cache_key()
            except ApiError:
                fp = f"v{int(r.version)}"
            out[(int(r.id), "")] = Info(fp, r.name, r.state == "building")
        return out


for _p in (DhSet(), DhTable(), DhResult(), Wireframe()):
    register(_p)

__all__ = ["DhResult", "DhSet", "DhTable", "Wireframe"]
