"""What a pit scenario is built from, for the graph of dependencies (подшаг 21.10; ADR 0028 решение 23; ADR 0025).

The inputs of the node `object:pit_scenario` (written by `store.write_result` in the transaction of the write,
`lineage.record`): the model (its grid), every field the run reads (the elements, the density, the field of the rocks
and of the domains, the fields of the rock rules and of the restriction, `FRAC`), the filter of the library, and the
wireframes: the terrain, the leaves of the restriction, of the filter and of the rock rules. What is not there any more
is left out (the graph says «источник удалён» for a node it recorded earlier).

    names = read_fields(params)                              # the fields of the parameters (`FRAC` included)
    nodes = input_nodes(conn, params, names)                 # model, fields, filter, wireframes (terrain first)
    prints = input_prints(conn, params, names)               # {node: fingerprint} as it is now — before the pass
    meta = inputs_meta(refs, model_id, names)                # `meta.inputs`, `meta.fields`, the print of the model

`params` is the canonical form of the scenario (`spec.PitSpec.canonical()`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError

FRAC = "FRAC"  # the share of a block the model fills (`geometry.row_volumes`)

__all__ = ["FRAC", "input_nodes", "input_prints", "inputs_changed", "inputs_meta", "read_fields", "rock_rules",
           "rule_trees"]  # fmt: skip


def rock_rules(params: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The trees of the rock rules (`rocks.by.rules[].rules`), in the order of the rules."""
    by = (params.get("rocks") or {}).get("by") or {}
    if by.get("kind") != "rules":
        return []
    return [r["rules"] for r in by.get("rules") or [] if isinstance(r.get("rules"), Mapping)]


def rule_trees(conn: sa.Connection, params: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Every tree of rules the scenario evaluates: the restriction, the stored filter, the rock rules."""
    from bf_core.blocks.filters import store as filters

    trees: list[Mapping[str, Any]] = []
    if params.get("restrict"):
        trees.append(params["restrict"])
    if params.get("filter_id") is not None:
        try:
            trees.append(filters.rules_of(conn, int(params["filter_id"])))
        except ApiError:
            pass  # a filter that is gone: the node is left out and the graph says «источник удалён»
    return [*trees, *rock_rules(params)]


def read_fields(params: Mapping[str, Any]) -> list[str]:
    """The fields the parameters name: the elements, the density (when it is a field), the field of the rocks and of
    the domains, the fields of the rock rules, and `FRAC`. The fields of the restriction and of the filter are added
    by `input_nodes`. Once each (names are compared without case), in this order."""
    from bf_core.blocks.filters import schema as fs

    seen: dict[str, str] = {}

    def add(name: Any) -> None:
        if isinstance(name, str) and name:
            seen.setdefault(name.casefold(), name)

    for e in params.get("elements") or []:
        add(e.get("field"))
    add(params.get("density"))
    by = (params.get("rocks") or {}).get("by") or {}
    if by.get("kind") == "field":
        add(by.get("field"))
    add((params.get("slopes") or {}).get("domain_field"))
    for tree in rock_rules(params):
        for name in fs.fields_of(tree):
            add(name)
    add(FRAC)
    return list(seen.values())


def input_nodes(conn: sa.Connection, params: Mapping[str, Any], fields: Sequence[str]) -> list[Any]:
    """The nodes of the scenario now: the model, the fields (`fields` and those of the restriction, the filter and the
    rock rules), the filter, the wireframes (the terrain, then those of the trees in their order). Only the nodes the
    graph knows. Order: model, fields, filter, wireframes."""
    from bf_core import lineage
    from bf_core.blocks.filters import schema as fs

    model_id = params.get("model_id")
    if model_id is None:
        return []
    names: dict[str, str] = {str(f).casefold(): str(f) for f in fields}
    wires: dict[int, None] = {}
    if params.get("terrain_id") is not None:
        wires[int(params["terrain_id"])] = None
    for tree in rule_trees(conn, params):
        names.update({n.casefold(): n for n in fs.fields_of(tree)})
        wires.update(dict.fromkeys(fs.object_ids(tree)))
    nodes = [lineage.Node("object:block_model", int(model_id))]
    nodes += [lineage.Node("bm_field", int(model_id), key) for key in names]
    if params.get("filter_id") is not None:
        nodes.append(lineage.Node("object:filter", int(params["filter_id"])))
    nodes += [lineage.Node("wireframe", i) for i in wires]
    return [n for n in nodes if lineage.fingerprint(conn, n) is not None]


def input_prints(conn: sa.Connection, params: Mapping[str, Any], fields: Sequence[str]) -> dict[Any, str]:
    """The fingerprint of every input node as it is now: what a job takes before it reads the data and `write_result`
    compares in the transaction of the write (the model, a field, a filter or a surface changed meanwhile — 409
    `model_changed`: the rows of the graph would name what the pass did not read)."""
    from bf_core import lineage

    return {n: str(lineage.fingerprint(conn, n)) for n in input_nodes(conn, params, fields)}


def inputs_changed() -> ApiError:
    from .spec import refuse

    return refuse("model_changed")


def inputs_meta(refs: Sequence[Any], model_id: int, names: Mapping[str, str]) -> dict[str, Any]:
    """`meta.inputs`, `meta.fields` and the fingerprint of the model from what the graph recorded. A field of a model
    is `{kind: "bm_field", id: "<model>:<name>"}` (the name as spelled in `names`, found by the name without case)."""
    inputs: list[dict[str, Any]] = []
    fields: dict[str, str] = {}
    cells: str | None = None
    for r in refs:
        n = r.node
        if n.kind == "bm_field":
            name = names.get(n.sub, n.sub)
            inputs.append({"kind": n.kind, "id": f"{n.id}:{name}", "fp": r.fp, "name": r.name})
            fields[name] = r.fp
        else:
            inputs.append({"kind": n.kind, "id": n.id, "fp": r.fp, "name": r.name})
            if n.kind == "object:block_model" and n.id == model_id:
                cells = r.fp
    return {"inputs": inputs, "fields": fields, "cells_sha": cells}
