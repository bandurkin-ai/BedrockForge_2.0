"""From the parameters of a scenario to what the cores of 21.2–21.8 take (подшаг 21.11; ADR 0028 decisions 4–7, 9–13).

The job `pit.run` (`job.py`) and the routes use this module; it knows the project (connections, blob store) and the
model, the cores of the optimiser know nothing of them:

    sp = resolve(model, params)                      # PitSpec resolved against the model: 404 / 422 of the fields
    ctx = context(model, met)                        # what `spec.validate` / `require_ready` take
    rr = restrictor_of(conn, store, model, sp)       # the tree (filter · restriction · «ниже рельефа»), the relief
    rm = rock_map(conn, store, model, sp)            # rock id → name, the field or the rules of the rocks
    inp = make_inputs(model, sp, rm, rocks)          # `econ.Inputs`
    met = met_rock_types(model, probe, rr, chunk)    # the types of rocks the DATA meets (a type met needs a price list)
    tpl = build_tpl(model, sp, tick)                 # `template.PitTemplate`, 422 `variable_grid`
    g = make_graph(model, tab, tpl)                  # `graph.PitGraph` of the nodes of `tab`, the table of each node
    doc = template_doc(tpl, model)                   # the JSON of `pit.check` and of `sidecar:template`

Numpy and the kernels are imported inside the functions (the process of the API loads this module only to route).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError

from . import spec as sp_mod
from .spec_parts import ALL_LABEL, EMPTY_LABEL, OTHER_LABEL

if TYPE_CHECKING:
    import numpy as np

    from bf_core.blocks.model import Model
    from bf_core.blocks.restrict import Restrictor
    from bf_core.storage.blobs import BlobStore

    from .econ import Inputs, Rock
    from .econ_table import Table
    from .graph import PitGraph
    from .template import PitTemplate

MAX_PLAN_CELLS = 2**29  # cells of the grid the dense arrays of the graph are made for (a bool and a uint8 each)
Tick = Callable[[float | None, str | None], None]


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


# --- the model --------------------------------------------------------------------------------------------------------


def z_range_of(model: Model) -> tuple[float, float]:
    """The extent of the model along Z (a turn is about Z only): the range of `PitSpec.resolved`."""
    lz = float(model.grid.lengths()[2])
    z0 = float(model.grid.corner[2])
    return z0, z0 + lz


def resolve(model: Model, params: Any) -> sp_mod.PitSpec:
    """`params` (a body or a `PitSpec`) resolved against `model`: 404 `block_field_not_found`, 422 `bad_param`."""
    sp = params if isinstance(params, sp_mod.PitSpec) else sp_mod.parse_params(params)
    return sp.resolved(model.fields, z_range_of(model))


def context(model: Model, met: Sequence[str] | None = None) -> dict[str, Any]:
    """The context of `spec.validate`: the fields of the model and (the job) the types of rocks the data meets."""
    out: dict[str, Any] = {"fields": model.fields}
    if met is not None:
        out["rock_types"] = list(met)
    return out


def model_json(model: Model) -> dict[str, Any]:
    """{id, name, version, cells_sha, grid}: `cells_sha` is the SHA-256 of the file of the cells (the graph)."""
    return {"id": model.id, "name": model.name, "version": model.version, "cells_sha": model.shas().get("cells"),
            "grid": dict(model.meta.get("grid") or {})}  # fmt: skip


def check_grid(model: Model) -> tuple[float, float, float]:
    """The steps of the parents; 422 `variable_grid` for a variable step."""
    if model.grid.size is None:
        raise sp_mod.refuse("variable_grid")
    return model.grid.regular("Оптимизация карьера")


def normalize(conn: sa.Connection, store: BlobStore, params: Any) -> sp_mod.PitSpec:
    """The parameters as the store keeps them (`store.update_params`: the same effective parameters ⇒ the same hash):
    resolved against the model when there is one and they can be (a form that is not filled yet stays as it is)."""
    from bf_core.blocks.model import Model

    sp = params if isinstance(params, sp_mod.PitSpec) else sp_mod.parse_params(params)
    if sp.model_id is None:
        return sp
    model = Model.open(conn, store, sp.model_id)  # 404 `block_model_not_found`
    try:
        return resolve(model, sp)
    except ApiError:
        return sp


# --- the restriction and the relief -----------------------------------------------------------------------------------


@dataclass
class Restriction:
    tree: dict[str, Any] | None
    restrictor: Restrictor
    relief: Any  # dz_kernels.SurfaceIndex | None


def restrictor_of(conn: sa.Connection, store: BlobStore, model: Model, sp: sp_mod.PitSpec) -> Restriction:
    """f_b = the share of the restriction (the filter of the library AND the quick rules) × the share of the block
    below the relief (decision 5). 404 `filter_not_found`, `wireframe_not_found`; 422 `filter_field_missing`."""
    from bf_core.blocks import calc_job, restrict
    from bf_core.geo import dz_kernels
    from bf_core.wireframes import store as wf

    base = calc_job.rules_tree(conn, sp.filter_id, sp.restrict)  # 404 `filter_not_found`
    leaf = None
    relief = None
    if sp.terrain_id is not None:
        xyz, tri = wf.read_wireframe(conn, store, int(sp.terrain_id))  # 404 `wireframe_not_found`
        dz_kernels.warm()
        relief = dz_kernels.surface_index(xyz, tri)
        leaf = {"surface": {"id": int(sp.terrain_id), "side": "below", "by": "frac"}}
    tree = restrict.combine(base, leaf)
    return Restriction(tree, restrict.prepare(model, tree, conn=conn, store=store), relief)


# --- the rocks --------------------------------------------------------------------------------------------------------


@dataclass
class RockMap:
    """The rock of a row: `names` — by the id of the rock; the field with `rock_map` (the code + 1 → id), or the rules
    (`rock_rules`, the first with f > 0; `rock_other` for the rest), or one rock."""

    names: list[str]
    rock_field: str | None
    rock_map: list[int]
    rock_rules: list[tuple[int, Restrictor]]
    rock_other: int


def rock_map(conn: sa.Connection, store: BlobStore, model: Model, sp: sp_mod.PitSpec) -> RockMap:
    from bf_core.blocks import restrict

    by = sp.rocks.by
    if by is None:
        return RockMap([ALL_LABEL], None, [], [], 0)
    if by.kind == "field":
        assert by.field is not None
        names = [EMPTY_LABEL, *model.labels(by.field)]
        return RockMap(names, by.field, list(range(len(names))), [], 0)
    rules = [(i, restrict.prepare(model, r.rules, conn=conn, store=store)) for i, r in enumerate(by.rules)]
    return RockMap([r.name for r in by.rules] + [OTHER_LABEL], None, [], rules, len(by.rules))


def rocks_of(sp: sp_mod.PitSpec, names: Sequence[str]) -> list[Rock]:
    """The rocks of the kernels from the types of the parameters. A rock the scenario has no type for (the data does
    not meet it, `require_ready` has seen to the ones it does) is a rock that is only waste."""
    from .econ import Rock

    ne = len(sp.elements)
    only = sp.rocks.types[0] if (sp.rocks.by is None and len(sp.rocks.types) == 1) else None
    out: list[Rock] = []
    for name in names:
        t = sp.rocks.type_of(name) or (only if name == ALL_LABEL else None)
        if t is None:
            out.append(Rock(name, 0.0, 0.0, [0.0] * ne, waste_only=True))
            continue
        rec = [float(t.rec.get(e.field, 0.0)) for e in sp.elements]
        out.append(Rock(name, float(t.c_p or 0.0), float(t.c_mr), rec, t.waste_only, t.air))
    return out


def make_inputs(model: Model, sp: sp_mod.PitSpec, rm: RockMap, rocks: Sequence[Rock], *, fill_empty: bool) -> Inputs:
    from .econ import Element, Inputs, unit_factor

    dens = sp.density
    frac = model.role_field("frac")
    c_m0 = 0.0 if sp.mining.c_m0 is None else float(sp.mining.c_m0)  # a form not ready is read for the types only
    z_ref = 0.0 if sp.mining.z_ref is None else float(sp.mining.z_ref)
    return Inputs(
        elements=[Element(e.field, unit_factor(str(e.unit)), float(e.price if e.price is not None else 0.0),
                          float(e.sales), float(e.royalty)) for e in sp.elements],
        rocks=list(rocks), c_m0=c_m0, z_ref=z_ref, h_gl=float(sp.mining.h_gl),
        c_gl=float(sp.mining.c_gl), density_field=dens if isinstance(dens, str) else None,
        density_value=None if isinstance(dens, str) or dens is None else float(dens),
        default_density=sp.default_density, frac=None if frac is None else str(frac["name"]),
        rock_field=rm.rock_field, rock_map=rm.rock_map, rock_rules=rm.rock_rules, rock_other=rm.rock_other,
        fill_empty=fill_empty,
    )  # fmt: skip


def met_rock_types(model: Model, inp: Inputs, rm: RockMap, restrictor: Restrictor, *, chunk: int,
                   tick: Tick = _noop) -> list[str]:  # fmt: skip
    """The rocks the data meets: a block with a share f > 0 and at least one grade (a block with no grade at all is a
    waste of any rock and does not ask a price list of it), in the order of the rocks. One pass over the model."""
    import numpy as np

    from .econ import _rocks_of  # noqa: PLC2701 - the same reading of a rock as the pass of the economics

    cnt = np.zeros(len(rm.names), dtype=np.int64)
    total = max(model.rows, 1)
    for ch, f in restrictor.iter(model, extra=inp.reads(), chunk=chunk):
        tick((ch.start + ch.n) / total, None)
        has = np.zeros(ch.n, dtype=np.bool_)
        for e in inp.elements:
            has |= np.isfinite(np.asarray(ch.cols[e.field], dtype=np.float64))
        rock = _rocks_of(inp, ch)
        cnt += np.bincount(rock[(f > 0.0) & has], minlength=len(rm.names))
    return [n for n, c in zip(rm.names, cnt, strict=True) if c > 0]


# --- the template and the graph ---------------------------------------------------------------------------------------


def _domain_label(model: Model, field: str | None, label: str) -> str:
    """The label as the model spells it (a body may differ in case)."""
    if field is None:
        return label
    try:
        have = {x.casefold(): x for x in model.labels(field)}
    except ApiError:
        return label
    return have.get(label.casefold(), label)


def build_tpl(model: Model, sp: sp_mod.PitSpec, tick: Tick = _noop) -> PitTemplate:
    """The template of the scenario on the grid of the model (decisions 9–12); 422 `variable_grid`, `bad_param`."""
    from . import template as tp

    dx, dy, dz = check_grid(model)
    general = tp.angle_table([(s.az, s.angle) for s in sp.slopes.table], "slopes.table")
    domains = [(_domain_label(model, sp.slopes.domain_field, d.label),
                tp.angle_table([(s.az, s.angle) for s in d.table], f"slopes.domains.{d.label}"))
               for d in sp.slopes.domains]  # fmt: skip
    return tp.build_template(dx, dy, dz, general, domains, k=sp.k, rotation=float(model.grid.rotation),
                             tolerance=float(sp.slopes.tolerance), tick=tick)  # fmt: skip


def template_doc(tpl: PitTemplate) -> dict[str, Any]:
    """The JSON of `pit.check` (without `model`, `seconds`) and of `sidecar:template` (e21-api §4)."""
    import numpy as np

    az = list(range(360))
    domains = []
    for t in tpl.tables:
        domains.append({
            "label": t.key,
            "table": [{"az": float(a), "angle": float(p)} for a, p in zip(t.table.azimuths, t.table.angles,
                                                                           strict=True)],
            "offsets": np.asarray(t.offsets).tolist(), "n_offsets": int(t.offsets.shape[0]),
            "error": {"max": round(float(t.err_max), 2), "mean": round(float(t.err_mean), 2)},
            "warning": bool(t.err_max > tpl.tolerance),  # by the exact error; the numbers are shown to 0,01°
            "phi_eff": {"az": az, "angle": [round(float(x), 2) for x in np.asarray(t.phi_eff)[:360]]},
        })  # fmt: skip
    warnings = [{"code": w.get("code", "angle_error"), "param": "slopes", "message": w["message"],
                 "hint": w.get("hint", ""), "domain": w.get("domain"),
                 "error": None if w.get("error") is None else round(float(w["error"]), 2),
                 "tolerance": w.get("tolerance")} for w in tpl.warnings]  # fmt: skip
    return {"k": int(tpl.k), "k_auto": bool(tpl.k_auto), "tolerance": float(tpl.tolerance), "levels": int(tpl.k),
            "domains": domains, "warnings": warnings}  # fmt: skip


def node_tables(model: Model, tab: Table, tpl: PitTemplate, field: str | None, chunk: int) -> np.ndarray:
    """uint8 [nodes]: the table of angles of every node — that of the domain of the node (its first row), 0 — the
    general one (no domain field, a gap, a label without a table, a cell absent from the file)."""
    import numpy as np

    out = np.zeros(tab.n_nodes, dtype=np.uint8)
    if field is None or len(tpl.tables) < 2:
        return out
    labels = model.labels(field)
    lut = np.zeros(len(labels) + 1, dtype=np.uint8)  # the code + 1: the gap −1 → 0
    for c, label in enumerate(labels):
        lut[c + 1] = tpl.table_index(label)
    seen = np.zeros(tab.n_nodes, dtype=np.bool_)
    for ch in model.iter([field], chunk):
        nodes = np.asarray(tab.node_of_row[ch.start : ch.start + ch.n])
        sel = np.flatnonzero(nodes >= 0)
        if not sel.size:
            continue
        nd, first = np.unique(nodes[sel], return_index=True)  # the first row of a node in the portion
        keep = ~seen[nd]
        codes = np.asarray(ch.cols[field])[sel[first[keep]]].astype(np.int64)
        codes[(codes < -1) | (codes >= len(labels))] = -1
        out[nd[keep]] = lut[codes + 1]
        seen[nd[keep]] = True
    return out


def make_graph(model: Model, tab: Table, tpl: PitTemplate, domain_field: str | None, chunk: int) -> PitGraph:
    """The graph of the nodes of `tab` (the parents that are not «воздух») on the grid of the parents, with the BRIDGES:
    an «воздух» cell inside the box of the blocks that carries a chain of the reduced template (a valley of the relief
    between two ridges) is a node of value 0 (`PitGraph.real` is False there; `graph.py`, BRIDGES). The values of the
    blocks are `tab`'s (`Table.values`, one per REAL node, in the order of the table); a bridge takes the general
    table."""
    import numpy as np

    from .graph import build_graph

    nx, ny, nz = (int(c) for c in model.grid.counts)
    total = nx * ny * nz
    if total > MAX_PLAN_CELLS:
        raise sp_mod.refuse("pit_too_large", "Сетка модели слишком велика для оптимизации карьера.",
                            "Укрупните модель регуляризацией.")  # fmt: skip
    air = np.ones(total, dtype=np.bool_)
    cells = np.asarray(tab.p, dtype=np.int64)
    air[cells] = False
    table = None
    if len(tpl.tables) > 1:
        table = np.zeros(total, dtype=np.uint8)
        table[cells] = node_tables(model, tab, tpl, domain_field, chunk)
    return build_graph((nx, ny, nz), np.zeros(total, dtype=np.int64), air, table, [t.offsets for t in tpl.tables],
                       bridges=True)  # fmt: skip


# --- the control ------------------------------------------------------------------------------------------------------


def control_doc(tab: Table, model: Model, *, closed: bool, nested: bool) -> dict[str, Any]:
    """`sidecar:control` (e21-api §6): what the numbers hide — «воздух», the cells absent from the file, the gaps of the
    grades and of the density, the density by default, the rows of the sub-blocks."""
    c = tab.control.to_dict()
    sub_rows = max(int(model.rows) - int(model.parents), 0) if model.sub else 0
    return {
        "air": {"blocks": c["air"]}, "empty_cells": c["empty_cells"], "no_volume": c["no_volume"],
        "no_grade": c["grade_gaps"], "no_density": {"blocks": c["no_density"]},
        "default_density": c["default_density"] if c["default_density"]["blocks"] else None,
        "rocks": c["rocks"], "tonnes": c["tonnes"], "rows": c["rows"], "sub_blocks": sub_rows,
        "closed": bool(closed), "nested": bool(nested),
    }  # fmt: skip


def warning_dicts(lines: Sequence[str], code: str, hint: str) -> list[dict[str, Any]]:
    return [{"code": code, "message": str(x), "hint": hint} for x in lines]


def merge_warnings(*groups: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The warnings of the parts once each (by code and message), in the order of the parts."""
    seen: set[tuple[Any, Any]] = set()
    out: list[dict[str, Any]] = []
    for g in groups:
        for w in g:
            key = (w.get("code"), w.get("message"))
            if key not in seen:
                seen.add(key)
                out.append(dict(w))
    return out
