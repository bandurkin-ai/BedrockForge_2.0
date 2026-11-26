"""Provider of the planned holes: `object:planned_hole` (ADR 0025 §2–3; ADR 0026 решение 25; ТЗ 7.35 Д-34 ч. 2).

A planned hole is a source: it is built from nothing (its path is computed from its own `meta`), so it has no inputs
and no check of the direct inputs; it changes with its `version` (a `PUT` of the window). Whoever is built from it
(a later stage of the project) is «устарел» when it changes.
"""

from __future__ import annotations

from collections.abc import Sequence

from .adapt_objects import object_rows
from .providers import Ctx, Info, Key, Provider, register

KIND = "planned_hole"


class PlannedHole(Provider):
    kind = "object:" + KIND

    def label(self) -> str:
        return "проектная скважина"

    def lookup(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, Info]:
        return {(i, ""): Info(str(int(r.version)), r.name, r.state == "building")
                for i, r in object_rows(ctx, KIND, keys).items()}  # fmt: skip


register(PlannedHole())

__all__ = ["PlannedHole"]
