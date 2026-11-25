"""Provider of the rows «Моделирование»: `object:simulation` (ADR 0025 §2–3, §6; ADR 0024 §9).

The fingerprint of a row is what its realizations depend on: the hash of its parameters (`params_hash`: not the
name, but the seed and the number of realizations — they decide the numbers) and the fingerprints of its inputs, never
its `version`. A row records its inputs with `lineage.record` (create, update, `commit_run` —
`geostat/simulation/store.py`), so the graph reads them from its own rows; the direct check is the module's
`freshness` (inputs against the project and the parameters against `last_run.params`), called as it is. The fields of
`simulate.post` are nodes `bm_field` whose input is this node.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .adapt_objects import object_rows
from .providers import Ctx, Direct, Info, Key, Provider, register


def fingerprint(meta: Mapping[str, Any], version: int) -> str:
    """`p:` + the hash of the parameters (without the ids of the objects: a project import renumbers them) and of the
    inputs' fingerprints; a row that does not parse: its version."""
    from bf_core.geostat.simulation import store_check as sc

    try:
        return "p:" + sc.graph_fingerprint(dict(meta))[:24]
    except Exception:  # noqa: BLE001 - a damaged row still has a state
        return str(int(version))


class Simulation(Provider):
    kind = "object:simulation"

    def lookup(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, Info]:
        return {
            (i, ""): Info(fingerprint(r.meta or {}, int(r.version)), r.name, r.state == "building")
            for i, r in object_rows(ctx, "simulation", keys).items()
        }

    def direct(self, ctx: Ctx, key: Key) -> Direct | None:
        from bf_core.geostat.simulation import store_check

        r = object_rows(ctx, "simulation", [key]).get(key[0])
        if r is None:
            return None
        got = store_check.freshness(ctx.conn, r.meta or {})
        if not got.get("stale"):
            return None
        return str(got.get("stale_reason") or "изменены входы"), bool(got.get("source_lost"))


register(Simulation())

__all__ = ["Simulation"]
