"""Provider of the resource reports: `object:resource_report` (ADR 0025 §2–3, §9; ADR 0027 решение 21; ТЗ Д-35, 8.10).

The fingerprint of a report is what its numbers are: the SHA-256 of its file `data` and ALGO (`store.fingerprint`) — a
rename, another template or a new export leave it as it was. Its inputs are written by the store in the transaction of
the write (`lineage.record` at `create` / `replace`): the model, every field it reads, the filter of the library, the
wireframes and the surfaces of the rules. So «устарел» comes through the graph (ТД35-1 up to the report): a table of
samples is edited → the field of the estimation is «устарел» → the report built from it says why, with the chain
(«поле блочной модели «M.AU» ← изменена таблица «assay»»). «Закрепить» keeps a report that went out (`lineage.pin`).
There is no check of the direct inputs of its own: everything it is built from is a node.

«Перестроить» (`lineage.rebuild`): `prepare_rebuild` — the maker of the kind. It takes the stored parameters and puts
the job `resources.report` for the same report (`store.rebuild_params`): the result is written in place, version + 1.

    in `lineage/rebuild.py`:   from .adapt_resources import prepare_rebuild
                               register("object:resource_report", _self, prepare_rebuild)

pyarrow and numpy are not imported here: the module is loaded with the providers before the core is ready.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from bf_core.db.schema import blob_owners

from .adapt_objects import object_rows
from .providers import Ctx, Info, Key, Provider, register
from .store import Node

KIND = "resource_report"
JOB_KIND = "resources.report"  # the job that makes (and rebuilds) a report; registered in `jobs/registry.py`
ROLE = "data"


def _shas(ctx: Ctx, ids: Sequence[int]) -> dict[int, str]:
    """The file `data` of the reports, read once per request."""
    cache: dict[int, str] = ctx.cache.setdefault("resource_shas", {})
    need = [i for i in ids if i not in cache]
    if need:
        q = sa.select(blob_owners.c.owner_id, blob_owners.c.sha256).where(
            blob_owners.c.owner_kind == "object", blob_owners.c.role == ROLE, blob_owners.c.owner_id.in_(need)
        )
        for r in ctx.conn.execute(q):
            cache[int(r.owner_id)] = str(r.sha256)
    return {i: cache[i] for i in ids if i in cache}


class ResourceReport(Provider):
    kind = "object:" + KIND

    def label(self) -> str:
        return "отчёт по ресурсам"

    def lookup(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, Info]:
        from bf_core.resources import store

        rows = object_rows(ctx, KIND, keys)
        shas = _shas(ctx, list(rows))
        return {  # «идёт построение» is in `meta` (the clean-ups of the jobs delete the `building` rows of `objects`)
            (i, ""): Info(store.fingerprint(shas.get(i), store.ALGO, int(r.version)), r.name,
                          _is_building(ctx, r))
            for i, r in rows.items()
        }  # fmt: skip


def _is_building(ctx: Ctx, row: Any) -> bool:
    from bf_core.model.store_base import is_building

    return is_building(ctx.conn, row.meta)


def owner(_conn: sa.Connection, node: Node) -> Node | None:
    """A report is made by a job of its own: it is its own owner (`rebuild.register(kind, owner, prepare)`)."""
    return node


def prepare_rebuild(svc: Any, cap: Any, node: Node, _body: dict[str, Any]) -> None:
    """The «Перестроить» route of the module for the graph: the job with the stored parameters, put into `cap` (the
    stand-in of the scheduler of `rebuild.Capture`). 404 `resource_report_not_found`, 409 `report_building`."""
    from bf_core.db import dialect
    from bf_core.resources import store

    with dialect.read_tx(svc.require_current().engine) as conn:
        params = store.rebuild_params(conn, node.id)
    cap.submit(JOB_KIND, params, title=f"Перестроить отчёт «{params['name']}»")


register(ResourceReport())

__all__ = ["JOB_KIND", "ResourceReport", "owner", "prepare_rebuild"]
