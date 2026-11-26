"""Provider of the pit scenarios: `object:pit_scenario` (ADR 0025 §2–3, §9; ADR 0028 решение 23; ТЗ Д-35, 8.11).

The fingerprint of a scenario is what its result is: the SHA-256 of its file `data` and ALGO (`store.fingerprint`) — a
rename, a new calendar or edited parameters that have not been run leave it as it was; a scenario without a result is
«нет результата». Its inputs are written by the store in the transaction of the write (`lineage.record` in
`store.write_result`): the model, every field it reads (the elements, the density, the fields of the rocks and of the
domains, `FRAC`), the filter of the library, the wireframes of the terrain, of the restriction and of the rules. So
«устарел» comes through the graph (ТД35-1 up to the scenario): a table of samples is edited → the field of the
estimation is «устарел» → the scenario built from it says why, with the chain; the fields PIT and ORE and the DTMs
of the shells are recorded with the scenario as their input (`graph.record(…, [object:pit_scenario])`), so they go
«устарел» after it.
«Закрепить» keeps a scenario that went out (`lineage.pin`).

«Перестроить» (`lineage.rebuild`): `prepare_rebuild` — the maker of the kind. It takes the parameters of the result and
puts the job `pit.run` for the same scenario (`store.rebuild_params`): the result is written in place, version + 1. The
field PIT / ORE of a model (`source.op == "pit"`) is made by its scenario: `pit_field_owner`.

    in `lineage/providers.py`:  adapt_pit,                                    (in the tuple of the imports)
    in `lineage/rebuild.py`:    from .adapt_pit import owner as pit_owner
                                from .adapt_pit import pit_field_owner
                                from .adapt_pit import prepare_rebuild as pit_rebuild
                                register("object:pit_scenario", pit_owner, pit_rebuild)
                                `_field_owner`: the last line `return None` → `return pit_field_owner(meta, node.sub)`

pyarrow and numpy are not imported here: the module is loaded with the providers before the core is ready.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import sqlalchemy as sa

from bf_core.db.schema import blob_owners

from .adapt_objects import object_rows
from .providers import Ctx, Info, Key, Provider, register
from .store import Node

KIND = "pit_scenario"
JOB_KIND = "pit.run"  # the job that makes (and rebuilds) a scenario; registered in `jobs/registry.py`
ROLE = "data"
FIELD_OP = "pit"  # `source.op` of the fields PIT and ORE of a model


def _shas(ctx: Ctx, ids: Sequence[int]) -> dict[int, str]:
    """The file `data` of the scenarios, read once per request."""
    cache: dict[int, str] = ctx.cache.setdefault("pit_shas", {})
    need = [i for i in ids if i not in cache]
    if need:
        q = sa.select(blob_owners.c.owner_id, blob_owners.c.sha256).where(
            blob_owners.c.owner_kind == "object", blob_owners.c.role == ROLE, blob_owners.c.owner_id.in_(need)
        )
        for r in ctx.conn.execute(q):
            cache[int(r.owner_id)] = str(r.sha256)
    return {i: cache[i] for i in ids if i in cache}


def _is_running(ctx: Ctx, row: Any) -> bool:
    from bf_core.model.store_base import is_building

    meta = row.meta or {}
    return is_building(ctx.conn, {"state": meta.get("state"), "job_id": meta.get("run_job")})


class PitScenario(Provider):
    kind = "object:" + KIND

    def label(self) -> str:
        return "сценарий карьера"

    def lookup(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, Info]:
        from bf_core.mine import store

        rows = object_rows(ctx, KIND, keys)
        shas = _shas(ctx, list(rows))
        return {  # «идёт расчёт» is in `meta` (the clean-ups of the jobs delete the `building` rows of `objects`)
            (i, ""): Info(store.fingerprint(shas.get(i), store.ALGO), r.name, _is_running(ctx, r))
            for i, r in rows.items()
        }  # fmt: skip


def owner(_conn: sa.Connection, node: Node) -> Node | None:
    """A scenario is made by a job of its own: it is its own owner (`rebuild.register(kind, owner, prepare)`)."""
    return node


def pit_field_owner(meta: Mapping[str, Any] | None, sub: str) -> Node | None:
    """The scenario that made the field `sub` (the name without case) of a model with the `meta` given: the field has
    `source.op == "pit"` and the id of the scenario; None for any other field."""
    for f in (meta or {}).get("fields") or []:
        src = f.get("source") or {}
        if str(f.get("name", "")).casefold() != sub or src.get("op") != FIELD_OP:
            continue
        if src.get("scenario_id") is not None:
            return Node("object:" + KIND, int(src["scenario_id"]))
    return None


def prepare_rebuild(svc: Any, cap: Any, node: Node, _body: dict[str, Any]) -> None:
    """The «Перестроить» route of the module for the graph: the job with the parameters of the result, put into `cap`
    (the stand-in of the scheduler of `rebuild.Capture`). 404 `pit_scenario_not_found`, 409 `pit_running`,
    `pit_no_result`."""
    from bf_core.db import dialect
    from bf_core.mine import store

    with dialect.read_tx(svc.require_current().engine) as conn:
        params = store.rebuild_params(conn, node.id)
    cap.submit(JOB_KIND, params, title=f"Перестроить сценарий карьера «{params['name']}»")


register(PitScenario())

__all__ = ["JOB_KIND", "PitScenario", "owner", "pit_field_owner", "prepare_rebuild"]
