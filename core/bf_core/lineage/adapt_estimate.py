"""Provider of the rows «Параметры оценки»: `object:estimation` (ADR 0025 §2–3, §6; ADR 0024 «Дополнение»).

A row records its inputs with `lineage.record` (create, update, `record_run` — `geostat/estimate/store.py`), so the
graph reads them from its own rows; the direct check is the module's `freshness` (inputs against the project and
the parameters against `last_run.params`), called as it is.
"""

from __future__ import annotations

from collections.abc import Sequence

from .adapt_objects import object_rows
from .providers import Ctx, Direct, Info, Key, Provider, register


class Estimation(Provider):
    kind = "object:estimation"

    def lookup(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, Info]:
        return {
            (i, ""): Info(str(int(r.version)), r.name, r.state == "building")
            for i, r in object_rows(ctx, "estimation", keys).items()
        }

    def direct(self, ctx: Ctx, key: Key) -> Direct | None:
        from bf_core.geostat.estimate import store_check

        r = object_rows(ctx, "estimation", [key]).get(key[0])
        if r is None:
            return None
        got = store_check.freshness(ctx.conn, r.meta or {})
        if not got.get("stale"):
            return None
        return str(got.get("stale_reason") or "изменены входы"), bool(got.get("source_lost"))


register(Estimation())

__all__ = ["Estimation"]
