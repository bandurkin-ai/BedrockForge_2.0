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

OUTPUT_OPS = ("pit_surface", "pit_solid")  # `meta.source.op` of the wireframes a scenario makes (`dtm_write`)
_SCENARIO = "object:pit_scenario"

__all__ = ["FRAC", "OUTPUT_OPS", "input_nodes", "input_prints", "inputs_changed", "inputs_meta", "orphan_outputs",
           "read_fields", "rock_rules", "rule_trees"]  # fmt: skip


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


def orphan_outputs(conn: sa.Connection, scenario_id: int, model_id: int | None = None) -> None:
    """A scenario is deleted (21.21б-2): what it made must not belong to a scenario that is made later under the same
    number (the id of the last row is taken again on SQLite). In the transaction of the delete:
      * the fields PIT / ORE of its models lose `source.scenario_id` (`None`, the field stays «осиротевшим»: another
        scenario cannot replace it, 409 `pit_fields_taken` — «задайте префикс»);
      * its DTMs and solids lose `meta.source.scenario_id` (a new scenario does not find them as its own);
      * the edges of the graph whose source is the scenario lose their id (`src_id` 0, the name stays): the graph
        says «источник удалён» for good and a new scenario with that number does not revive them."""
    from bf_core import lineage
    from bf_core.db.schema import lineage as edges
    from bf_core.db.schema import objects

    sid = int(scenario_id)
    node = lineage.Node(_SCENARIO, sid)
    deps = lineage.store.dependents(conn, [node]).get(node, [])
    wires = [n.id for n in deps if n.kind == "wireframe"]
    models = {n.id for n in deps if n.kind == "bm_field"}
    if model_id is not None:
        models.add(int(model_id))
    for i in range(0, len(wires), 400):
        q = sa.select(objects.c.id, objects.c.meta).where(objects.c.kind == "wireframe",
                                                          objects.c.id.in_(wires[i : i + 400]))  # fmt: skip
        for ident, meta in conn.execute(q).all():
            src = (meta or {}).get("source")
            if isinstance(src, Mapping) and src.get("op") in OUTPUT_OPS and src.get("scenario_id") == sid:
                conn.execute(sa.update(objects).where(objects.c.id == ident).values(
                    meta={**meta, "source": {**src, "scenario_id": None}}))  # fmt: skip
    for ident in sorted(models):
        row = conn.execute(sa.select(objects.c.meta).where(objects.c.id == ident,
                                                           objects.c.kind == "block_model")).first()  # fmt: skip
        meta = dict(row.meta or {}) if row is not None else {}
        out, hit = [], False
        for f in meta.get("fields") or []:
            src = f.get("source") if isinstance(f, Mapping) else None
            if isinstance(src, Mapping) and src.get("op") == "pit" and src.get("scenario_id") == sid:
                f, hit = {**f, "source": {**src, "scenario_id": None}}, True
            out.append(f)
        if hit:
            conn.execute(sa.update(objects).where(objects.c.id == ident).values(meta={**meta, "fields": out}))
    c = edges.c
    mine = sa.and_(c.src_kind == _SCENARIO, c.src_id == sid, c.role == "in")
    for dst in conn.execute(sa.select(c.dst_kind, c.dst_id, c.dst_sub).where(mine)).all():
        # a row of the same edge with the id 0 (a scenario deleted before) would meet the one renumbered below
        conn.execute(sa.delete(edges).where(
            c.dst_kind == dst.dst_kind, c.dst_id == dst.dst_id, c.dst_sub == dst.dst_sub, c.role == "in",
            c.src_kind == _SCENARIO, c.src_id == 0, c.src_sub == ""))  # fmt: skip
    conn.execute(sa.update(edges).where(mine).values(src_id=0))
