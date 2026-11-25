"""Providers of the kinds of nodes (ADR 0025 §1, §3, §6): what the graph knows about a kind.

A provider answers, for many nodes of its kind at once:

  `lookup(ctx, keys)`  → {key: Info(fp, name, building)} — the fingerprint is the cheap state token of the node (a
                         token and revision of a table, the version of a set, the SHA-256 of a stored result);
                         a node that is not in the answer does not exist (deleted);
  `inputs(ctx, keys)`  → {key: [InputRef]} for the nodes whose inputs the module already keeps in its own form
                         (a result of sample processing, a variogram model, a report); a key not in the answer
                         has its inputs in the table `lineage` (`lineage.record`);
  `direct(ctx, key)`   → (reason, lost) — the existing check of the direct inputs of the module (§6: it is called,
                         not rewritten), or None when they did not change.

A key is `(id, sub)`. The providers are registered by importing their modules at the end of this file (like the
handlers of the jobs): a kind without a provider is an error `lineage_bad_kind`, and
`tests/test_structure.py` fails for an object kind with `algo` that has none.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError

from .store import InputRef

Key = tuple[int, str]
#: (reason in Russian, the source is gone)
Direct = tuple[str, bool]

OBJECT_PREFIX = "object:"
#: The words of the reasons («поле блочной модели «Модель.AU» ← модель вариограммы «Au_D1» ← изменена таблица «assay»»).
LABELS = {
    "dh_set": "набор скважин",
    "dh_table": "таблица",
    "dh_result": "результат обработки",
    "wireframe": "каркас",
    "bm_field": "поле блочной модели",
    "object:variogram_model": "модель вариограммы",
    "object:trend": "тренд",
    "object:qaqc_data": "данные QAQC",
    "object:qaqc_report": "отчёт QAQC",
    "object:block_model": "блочная модель",
    "object:estimation": "параметры оценки",
}


@dataclass(frozen=True)
class Info:
    fp: str
    name: str
    building: bool = False


@dataclass
class Ctx:
    """What a provider works with: the connection and, for the checks that open files of a block model, the store
    of the project (None — those checks are skipped). `cache` lives for one request."""

    conn: sa.Connection
    store: Any = None
    cache: dict[Any, Any] = field(default_factory=dict)


class Provider:
    kind = ""

    def lookup(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, Info]:
        raise NotImplementedError

    def inputs(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, list[InputRef]]:
        return {}

    def direct(self, ctx: Ctx, key: Key) -> Direct | None:
        return None

    def label(self) -> str:
        return LABELS.get(self.kind, self.kind.removeprefix(OBJECT_PREFIX))


_REGISTRY: dict[str, Provider] = {}
_GENERIC: dict[str, Provider] = {}


def register(provider: Provider) -> Provider:
    _REGISTRY[provider.kind] = provider
    return provider


def registered() -> list[str]:
    return sorted(_REGISTRY)


def provider_for(kind: str) -> Provider:
    p = _REGISTRY.get(kind) or _GENERIC.get(kind)
    if p is None and kind.startswith(OBJECT_PREFIX) and len(kind) > len(OBJECT_PREFIX):
        p = _GENERIC[kind] = _GENERIC.get(kind) or GenericObject(kind)  # any other object: a leaf by its version
    if p is None:
        hint = "Вид узла — dh_set, dh_table, dh_result, wireframe, bm_field или object:<вид объекта>."
        raise ApiError(422, "lineage_bad_kind", f"Неизвестный вид узла «{kind}».", hint)
    return p


def label_of(kind: str) -> str:
    return provider_for(kind).label()


class GenericObject(Provider):
    """An object of a kind without its own provider: changes with its `version`; no inputs of its own."""

    def __init__(self, kind: str) -> None:
        self.kind = kind

    def lookup(self, ctx: Ctx, keys: Sequence[Key]) -> dict[Key, Info]:
        from bf_core.db.schema import objects

        kind = self.kind.removeprefix(OBJECT_PREFIX)
        q = sa.select(objects.c.id, objects.c.name, objects.c.version, objects.c.state).where(
            objects.c.kind == kind, objects.c.id.in_([k[0] for k in keys])
        )
        return {(int(r.id), ""): Info(str(int(r.version)), r.name, r.state == "building") for r in ctx.conn.execute(q)}


# The providers of the kinds of the project: imported here, in one place (ADR 0025 §1).
from . import adapt_blocks, adapt_drill, adapt_estimate, adapt_objects, adapt_simulate  # noqa: E402, F401
