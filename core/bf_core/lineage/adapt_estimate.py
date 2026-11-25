"""Provider of the rows «Параметры оценки»: `object:estimation` (ADR 0025 §2–3, §6; ADR 0024 «Дополнение»).

The fingerprint of a row is what its results depend on: the hash of its parameters (`params_hash`: not the name) and
the fingerprints of its inputs, never its `version` — a rename or any other PUT that leaves them as they were does not
make the fields of the row «устарел».

A row records its inputs with `lineage.record` (create, update, `record_run` — `geostat/estimate/store.py`), so the
graph reads them from its own rows; the direct check is the module's `freshness` (inputs against the project and
the parameters against `last_run.params`), called as it is.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from .adapt_objects import object_rows
from .providers import Ctx, Direct, Info, Key, Provider, register


def fingerprint(meta: Mapping[str, Any], version: int) -> str:
    """`p:` + the hash of the parameters and of the fingerprints of the inputs; a row that does not parse — its version."""
    from bf_core.geostat.estimate import store_base as sb

    try:
        params = sb.params_hash(sb.load_spec(dict(meta)))
    except Exception:  # noqa: BLE001 - a damaged row still has a state
        return str(int(version))
    inputs = "|".join(f"{i.get('kind')}:{i.get('id')}:{i.get('fp')}" for i in meta.get("inputs") or [])
    return "p:" + hashlib.sha256(f"{params}|{inputs}".encode()).hexdigest()[:24]


class Estimation(Provider):
    kind = "object:estimation"

    def lookup(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, Info]:
        return {
            (i, ""): Info(fingerprint(r.meta or {}, int(r.version)), r.name, r.state == "building")
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
