"""The fields PIT and ORE of the block model (подшаг 21.9; ТЗ 8.11.4 «Выходы»; ADR 0028 decision 22, № 119–120).

    pit, ore = field_names(prefix)                          # «<prefix>PIT», «<prefix>ORE»
    sm = ShellMap.from_graph(g, node_shell)                 # shells of the mined nodes of the graph
    staged = stage_fields(store, model, version, prefix, scenario_id, sm, table.node_of_row, table.ore_row)
    version = commit_scenario_fields(conn, store, staged, scenario_id=..., inputs=..., versions=...)

  * `<prefix>PIT` (i2): the number of the SMALLEST shell that holds the block (1…N, `PIT <= n` is the pit of the shell
    n — the shortcut «Карьер» of the resource report); 0 — outside every shell, «воздух» included. Never empty. Every
    row of a parent (sub-blocks, № 119) gets the PIT of its parent.
  * `<prefix>ORE` (i1): 1 — ore at RF = 1, 0 — waste at RF = 1; EMPTY (NaN, not 0) — «воздух», no tonnage, no volume:
    a row that is not a node of the graph (`node_of_row` = −1). ORE is by row (a sub-block is ore of its own).
  * `source` = {op: "pit", scenario_id, algo, versions, inputs, params}: `commit_fields` of Э17 puts it on both
    fields, in the transaction of the scenario's write (the job `pit.run`, 21.11). A field of the same name with another
    source (an import, a calculator, another scenario) is never replaced: 409 `pit_fields_taken` — «задайте префикс».
    A field of THIS scenario (the same `scenario_id`) is replaced in place by a new run.

`blocks/schema.py` must know the op: `FIELD_OPS` += "pit" (the line of the orchestrator, ADR 0028 decision 22).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks.fields_store import FieldsWriter, StagedFields, commit_fields
from bf_core.blocks.model import CHUNK, Model
from bf_core.mine.graph import PitGraph, parent_index
from bf_core.mine.spec_parts import ALGO
from bf_core.storage.blobs import BlobStore

OP = "pit"
PIT = "PIT"
ORE = "ORE"
MAX_SHELL = 32_767  # i2


def field_names(prefix: str = "") -> tuple[str, str]:
    """(«<prefix>PIT», «<prefix>ORE»)."""
    return f"{prefix}{PIT}", f"{prefix}{ORE}"


def taken_error(name: str) -> ApiError:
    return ApiError(
        409, "pit_fields_taken", f"Поле «{name}» в модели уже есть и создано не этим сценарием.",
        "Задайте префикс полей в параметрах сценария (например «A_»): сценарий запишет «A_PIT» и «A_ORE».",
        {"field": name, "param": "prefix"},
    )  # fmt: skip


def is_own(field: Mapping[str, Any], scenario_id: int) -> bool:
    """The field was written by the scenario `scenario_id` (`source.op = pit`, the same `scenario_id`)."""
    src = field.get("source") or {}
    return src.get("op") == OP and src.get("scenario_id") == int(scenario_id)


def plan_names(fields: Sequence[Mapping[str, Any]], prefix: str, scenario_id: int) -> list[tuple[str, bool]]:
    """[(name, replace)] for PIT and ORE: `replace` — the model has a field of this name written by this scenario
    (a re-run replaces it). A field of another source — 409 `pit_fields_taken`."""
    have = {str(f["name"]).casefold(): f for f in fields}
    out = []
    for name in field_names(prefix):
        f = have.get(name.casefold())
        if f is not None and not is_own(f, scenario_id):
            raise taken_error(str(f["name"]))
        out.append((name, f is not None))
    return out


def check_names(model_meta: Mapping[str, Any], prefix: str, scenario_id: int) -> None:
    """The same check as the writer makes, before any work is done (the route `run`: 409 at once)."""
    plan_names(model_meta.get("fields") or [], prefix, scenario_id)


# --- the shells of the parents ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ShellMap:
    """The smallest shell of the mined parents: `cells` uint64 ascending (P of the parent in the grid of the model),
    `shell` int16 (1…N) beside them. A parent that is not here is outside every shell (PIT = 0)."""

    cells: np.ndarray
    shell: np.ndarray

    def __post_init__(self) -> None:
        if self.cells.shape != self.shell.shape or self.cells.ndim != 1:
            raise ValueError("cells and shell must be 1-D arrays of one length")

    @classmethod
    def from_parents(cls, p: np.ndarray, shell: np.ndarray) -> ShellMap:
        """From any parents with their shell (repeats — the rows of one parent — are folded; an entry with shell ≤ 0 is
        not in a shell). A parent met with two different numbers keeps the smaller."""
        p = np.asarray(p, dtype=np.uint64).reshape(-1)
        s = np.asarray(shell).reshape(-1)
        if p.shape != s.shape:
            raise ValueError("p and shell must have one length")
        if len(s) and int(s.max()) > MAX_SHELL:
            raise ValueError("the number of a shell does not fit i2")
        keep = s > 0
        p, s = p[keep], s[keep].astype(np.int16)
        order = np.lexsort((s, p))  # by P, then the smaller shell first
        p, s = p[order], s[order]
        first = np.ones(len(p), dtype=bool)
        first[1:] = p[1:] != p[:-1]
        return cls(p[first], s[first])

    @classmethod
    def from_graph(cls, g: PitGraph, node_shell: np.ndarray) -> ShellMap:
        """`node_shell` — int [g.n]: the smallest shell of every node of the graph (0 — in none)."""
        s = np.asarray(node_shell)
        if s.shape != (g.n,):
            raise ValueError("node_shell must have one entry per node of the graph")
        return cls.from_parents(parent_index(g).astype(np.uint64), s)

    def lookup(self, p: np.ndarray) -> np.ndarray:
        """int16 [len(p)]: the shell of the parent of each row (0 — none)."""
        p = np.asarray(p, dtype=np.uint64)
        out = np.zeros(p.shape[0], dtype=np.int16)
        if len(self.cells) and len(p):
            pos = np.minimum(np.searchsorted(self.cells, p), len(self.cells) - 1)
            hit = self.cells[pos] == p
            out[hit] = self.shell[pos[hit]]
        return out

    @property
    def count(self) -> int:
        """The number of the largest shell met (N when every shell has a block of its own)."""
        return int(self.shell.max()) if len(self.shell) else 0


# --- writing ---------------------------------------------------------------------------------------------------------


def ore_of(node_of_row: np.ndarray, ore_row: np.ndarray) -> np.ndarray:
    """float64 per row: 1.0 ore, 0.0 waste, NaN — not a node of the graph («воздух», no tonnage); a gap is never 0."""
    node = np.asarray(node_of_row)
    ore = np.asarray(ore_row)
    out = np.full(node.shape[0], np.nan)
    inside = node >= 0
    out[inside] = (ore[inside] != 0).astype(np.float64)
    return out


def stage_fields(
    store: BlobStore, model: Model, expected_version: int, prefix: str, scenario_id: int, shells: ShellMap,
    node_of_row: np.ndarray, ore_row: np.ndarray, *, chunk: int = CHUNK,
) -> StagedFields:  # fmt: skip
    """One file with `<prefix>PIT` and `<prefix>ORE`, row by row in the order of the model; no transaction yet.
    409 `pit_fields_taken` (a field of another source), 409 `version_conflict` (the model changed). `node_of_row`
    int32 and `ore_row` uint8 are per FILE row (`econ_table.Table`)."""
    if node_of_row.shape != (model.rows,) or ore_row.shape != (model.rows,):
        raise ValueError("node_of_row and ore_row must have one entry per row of the model")
    plan = plan_names(model.meta.get("fields") or [], prefix, scenario_id)
    n_pit, n_ore = field_names(prefix)
    with FieldsWriter(store, model, expected_version) as w:  # type: ignore[arg-type]  # ModelView: meta is settable
        texts = {n_pit: "Номер наименьшей оболочки карьера с блоком (0 — вне карьера)",
                 n_ore: "Руда при RF = 1 (1 — руда, 0 — пустая; пусто — нет тоннажа)"}  # fmt: skip
        for name, replace in plan:
            w.add(name, "i2" if name == n_pit else "i1", description=texts[name], replace=replace)
        for lo in range(0, max(model.rows, 1), chunk):
            hi = min(lo + chunk, model.rows)
            if hi <= lo:
                break
            w.write({n_pit: shells.lookup(model.p(lo, hi)), n_ore: ore_of(node_of_row[lo:hi], ore_row[lo:hi])})
        return w.finish()


def commit_scenario_fields(
    conn: sa.Connection, store: BlobStore, staged: StagedFields, *, scenario_id: int, inputs: Sequence[str] = (),
    versions: Mapping[str, Any] | None = None, params: Mapping[str, Any] | None = None, algo: str = ALGO,
) -> int:  # fmt: skip
    """Inside the write transaction of the scenario: the fields into the model (`commit_fields`), `source`
    {op: "pit", scenario_id, algo, versions, inputs [SHA-256], params} → the new version of the model."""
    source = {"op": OP, "scenario_id": int(scenario_id), "algo": algo, "versions": dict(versions or {}),
              "inputs": list(inputs), "params": dict(params or {})}  # fmt: skip
    return commit_fields(conn, store, staged, source=source)
