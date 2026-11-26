"""Provider of the implicit models: `object:implicit_model` (ADR 0025 §2–3, §6; ADR 0026 решение 25; ТЗ 8.9).

The fingerprint of a model is what its outputs depend on: the SHA-256 of its parameters (without the numbers of the
objects they name: a project import renumbers those — `model/store_base.without_ids`; `algo` is among them) and of
the SHA-256 of its blobs `implicit_points` and `implicit_solution`, never its `version` — a rename or a `PUT` that
leaves them as they were does not make its outputs «устарел». A model that was never built has no blobs: its
fingerprint follows its parameters only.

The inputs are written by the store (`lineage.record` at create / update / `record_run`): the set, the tables, the
composites, the layers of the manual data, the trend, the wireframes and the DTM of the boundary, the expansions and
the clip. An output (a wireframe that is a child of the model) is recorded by `record_run` with the model as its
input; the table «Модель на скважины» (`drillholes.from_model`) records the model or the wireframes and the set.
The direct check is the module's `freshness` (inputs against the project, the parameters against
`last_run.params`), called as it is.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from bf_core.db.schema import blob_owners

from .adapt_objects import object_rows
from .providers import Ctx, Direct, Info, Key, Provider, register

KIND = "implicit_model"


def _shas(ctx: Ctx, ids: Sequence[int]) -> dict[int, dict[str, str]]:
    """`implicit_points` and `implicit_solution` of the models, read once per request."""
    from bf_core.model import store_base as sb

    cache: dict[int, dict[str, str]] = ctx.cache.setdefault("implicit_shas", {})
    need = [i for i in ids if i not in cache]
    if need:
        for i in need:
            cache[i] = {}
        q = sa.select(blob_owners.c.owner_id, blob_owners.c.role, blob_owners.c.sha256).where(
            blob_owners.c.owner_kind == sb.OWNER, blob_owners.c.owner_id.in_(need),
            blob_owners.c.role.in_((sb.ROLE_POINTS, sb.ROLE_SOLUTION)))  # fmt: skip
        for r in ctx.conn.execute(q):
            cache[int(r.owner_id)][str(r.role)] = str(r.sha256)
    return {i: cache[i] for i in ids}


def fingerprint(meta: Any, version: int, shas: dict[str, str]) -> str:
    """`m:` + the hash of the parameters and of the two blobs; a model that does not parse: its version."""
    from bf_core.model import store_base as sb

    try:
        params = sb.load_spec(dict(meta)).hash_params()
        return "m:" + sb.graph_fingerprint(params, shas.get(sb.ROLE_POINTS), shas.get(sb.ROLE_SOLUTION))[:24]
    except Exception:  # noqa: BLE001 - a damaged model still has a state
        return str(int(version))


class ImplicitModel(Provider):
    kind = "object:" + KIND

    def label(self) -> str:
        return "неявная модель"

    def lookup(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, Info]:
        from bf_core.model import store_base as sb

        rows = object_rows(ctx, KIND, keys)
        shas = _shas(ctx, list(rows))
        return {  # «идёт построение» is in `meta` (the clean-ups of the jobs delete the `building` rows of `objects`)
            (i, ""): Info(fingerprint(r.meta or {}, int(r.version), shas[i]), r.name,
                          sb.is_building(ctx.conn, r.meta, i))
            for i, r in rows.items()
        }

    def direct(self, ctx: Ctx, key: Key) -> Direct | None:
        from bf_core.model import store_check

        r = object_rows(ctx, KIND, [key]).get(key[0])
        if r is None:
            return None
        got = store_check.freshness(ctx.conn, r.meta or {})
        if not got.get("stale"):
            return None
        return str(got.get("stale_reason") or "изменены входы"), bool(got.get("source_lost"))


register(ImplicitModel())

__all__ = ["ImplicitModel", "fingerprint"]
