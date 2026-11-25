"""The inspector of a block (ТЗ 7.35 Д-30; ADR 0024 «Дополнение»; Э18, 18.Д3; docs/progress/e18-api.md §7): a click on a
block of a model with an estimate → the window «Блок · i, j, k». The block is recalculated SYNCHRONOUSLY by the stored
«Параметры оценки» with the kernel of the job (`kernel.estimate_block` — one block through the same code as
`kernel.run`, no second implementation) and compared with the fields the job wrote.

    out = run(project, parse_inspect(body), tok)       # POST /estimate/inspect (`routes_inspect.py`), ≤ 200 мс warm

Answer (numbers: float64, an empty value is `null`, never 0):

    {model_id, model_version, block {row, p, ijk, sub, centre, size, parent {centre, size}, rows},
     results: [{estimation_id, name, version, method, prefix, label, sub, disc, source {set_id, table, field},
                block {centre, size, rows}, domain {field, label, row_label}, active,
                status, status_label, pass, values {EST, KV, SLOPE, KE, …}, mu, ellipsoid, passes,
                counts {candidates, shown, selected}, soft_dist, candidates [..], segments [..],
                fields [{key, name, dtype, calc, stored, same}], same (bit for bit | null), missing_fields, changed,
                warning, warning_detail, stale_reason, lineage {state, reason, changed}, snapshot}],
     warnings}

The samples are read with `all_domains` (the other domains show as «другой домен (жёсткая граница)») and the row is
compiled on them: the search takes the samples of D (and S) only, so the selection is that of the job (bit for bit; the
position in the canonical order of the samples only moves by the other domains, and the ties go by it). A compiled row
is kept per process by (project, id, version, model, origin, fingerprints of the inputs): the second click is fast.
The candidates of the pass that estimated the block (else of the last one tried): the samples inside its radius, at most
`search.EXPLAIN_MAX`, with the reason of the code of `search.REASONS` and its Russian text (`REASON_LABELS`).

«Побитно равно»: the recalculated value is brought to the type of the stored field (f4 — float32, integers) and compared
with the stored one; an empty one equals an empty one. Not equal, or the row is «устарел» (`store.freshness`) — the
warning «Параметры или входы изменились» (Д-35) and the state of the graph (`lineage.status`).
"""

from __future__ import annotations

import math
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks.model import Model
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.storage.blobs import BlobStore

from . import inspect_spot as isp
from . import kernel as kn
from . import samples as sm
from . import store_base as sb
from .inspect_spot import Spot, domain_of, locate
from .search import REASONS
from .spec import EstimationSpec, InspectSpec
from .spec_parts import STATUS_LABELS
from .store_check import fp_of, freshness

WARNING = "Параметры или входы изменились"
#: the Russian text of every code of `search.REASONS` (ТЗ Д-30); «урезана до t» and «усреднена» get their numbers
REASON_LABELS = {
    "selected": "выбрана",
    "hy_capped": "урезана до t",
    "outside": "вне эллипсоида",
    "hole_limit": "лимит скважины",
    "octant_limit": "лимит октанта",
    "over_n_max": "за n_max",
    "other_domain": "другой домен (жёсткая граница)",
    "soft_far": "дальше d_м (мягкая граница)",
    "hy_dropped": "ураганная, отброшена",
    "merged": "совпадающая точка (усреднена)",
}
CACHE_KEEP = 4  # compiled rows kept per process (the samples of a row are the heavy part)
_CACHE: OrderedDict[tuple[Any, ...], Entry] = OrderedDict()
_LOCK = threading.Lock()  # the cache
_BUILD = threading.Lock()  # the builds of the rows (one at a time: a miss is the slow path)


# --- numbers for the JSON -----------------------------------------------------------------------------------------


def num(x: Any) -> float | None:
    """A finite float or None (NaN and ±inf are «empty», never 0)."""
    if x is None:
        return None
    v = float(x)
    return v if math.isfinite(v) else None


def jsonable(x: Any) -> Any:
    """Plain Python for `JSONResponse`: numpy scalars and arrays, tuples, NaN → null."""
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [jsonable(v) for v in x]
    if isinstance(x, np.ndarray):
        return jsonable(x.tolist())
    if isinstance(x, np.bool_ | bool):
        return bool(x)
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, float | np.floating):
        return num(x)
    return x


def _text(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def as_stored(v: float, dtype: str) -> float:
    """`v` as the field of the model keeps it: f4 — float32, integer types — integers; NaN stays empty."""
    if not math.isfinite(v):
        return math.nan
    if dtype == "f4":
        return float(np.float32(v))
    if dtype in ("i1", "i2", "i4"):
        return float(round(v))
    return float(v)


def same_bits(calc: float, stored: float) -> bool:
    return (math.isnan(calc) and math.isnan(stored)) or calc == stored


# --- the compiled row ---------------------------------------------------------------------------------------------


@dataclass
class Entry:
    """A row of the plan ready for the inspector: its parameters, the kernel on the samples of all domains and the
    restrictor of its filter."""

    spec: EstimationSpec
    rk: kn.RowKernel
    restrictor: Any


def forget() -> None:
    """Empties the cache of the compiled rows (tests; a project that is closed)."""
    with _LOCK:
        _CACHE.clear()
    isp.forget()


def _fingerprints(conn: sa.Connection, meta: dict[str, Any]) -> tuple[Any, ...]:
    out = []
    for i in meta.get("inputs") or []:
        got = fp_of(conn, str(i.get("kind")), i.get("id"))
        out.append((i.get("kind"), str(i.get("id")), None if got is None else got[1]))
    return tuple(out)


def _origin(meta: dict[str, Any]) -> np.ndarray | None:
    o = meta.get("origin")
    return np.asarray(o, dtype=np.float64) if isinstance(o, list) and len(o) == 3 else None


def _entry(project: Any, store: BlobStore, model: Model, row: Any, fps: tuple[Any, ...]) -> Entry:
    meta = dict(row.meta or {})
    org = _origin(meta)
    key = (project.uid, int(row.id), int(row.version), model.id, model.version,
           None if org is None else tuple(float(v) for v in org), fps)  # fmt: skip
    with _LOCK:
        got = _CACHE.get(key)
        if got is not None:
            _CACHE.move_to_end(key)
            return got
    with _BUILD:  # one build at a time: the samples of a row are the heavy part; a hit does not wait for it
        with _LOCK:
            got = _CACHE.get(key)
        if got is None:
            got = _build(project, store, model, meta)
            with _LOCK:
                _CACHE[key] = got
                while len(_CACHE) > CACHE_KEEP:
                    _CACHE.popitem(last=False)
        return got


def _build(project: Any, store: BlobStore, model: Model, meta: dict[str, Any]) -> Entry:
    from bf_core.blocks import restrict
    from bf_core.blocks.filters import store as fst

    spec = sb.load_spec(meta)
    s = sm.load(project, spec, origin=_origin(meta), all_domains=True)
    rk = kn.compile_row(spec, s, rotation_deg=float(model.grid.rotation))
    with dialect.read_tx(project.engine) as conn:
        fid = spec.target.filter_id
        rules = spec.target.rules if fid is None else fst.rules_of(conn, fid)
        restrictor = restrict.prepare(model, rules, conn=conn, store=store)
    return Entry(spec, rk, restrictor)


# --- the candidates -----------------------------------------------------------------------------------------------


def reason_label(code: str, z: float, merged: int) -> str:
    text = REASON_LABELS[code]
    if code == "hy_capped":
        text = f"урезана до {_text(z)}"
    if merged > 1:
        text += f"; совпадающие точки усреднены: {merged}"
    return text


def candidates_table(rk: kn.RowKernel, detail: Any) -> list[dict[str, Any]]:
    """The candidates of the block as the window shows them (by h_s): the sample (hole, from–to, row of the table for
    the window 7.17, coordinates), `h` (h_s), `dist` (euclidean), `octant` (0–7), `z_raw` (before the top cut and the
    rule of the high samples), `z` (used), `lambda` (the weight of a selected one; null for the others and for IK),
    `reason` (code) and `reason_label`, `merged` (rows averaged into the sample), `domain`."""
    s = rk.samples
    c = detail.candidates
    idx = [int(i) for i in c.idx]
    if not idx:
        return []
    labels = {0: str(rk.spec.target.label or "домен оценки"), 1: "мягкая граница"}
    xyz, span = s.xyz[c.idx], s.span[c.idx]
    raw, zused = s.raw[c.idx], c.z
    row_id, merged, dom = s.row_id[c.idx], s.merged[c.idx], s.dom[c.idx]
    out = []
    for n, i in enumerate(idx):
        code = REASONS[int(c.reason[n])]
        d = int(dom[n])
        out.append({
            "i": i, "row_id": int(row_id[n]), "hole": s.holes[int(s.hole[i])], "from": num(span[n, 0]),
            "to": num(span[n, 1]), "xyz": [num(v) for v in xyz[n]], "h": num(c.h[n]), "dist": num(c.dist[n]),
            "octant": int(c.octant[n]), "z_raw": num(raw[n]), "z": num(zused[n]), "lambda": num(detail.cand_lam[n]),
            "reason": code, "reason_label": reason_label(code, float(zused[n]), int(merged[n])),
            "merged": int(merged[n]), "domain": labels[d] if d in labels else s.dom_labels[d - 2],
        })  # fmt: skip
    return out


def segments_of(centre_xyz: list[float | None], table: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The segments of the 3D scene: from the centre of the block to every selected sample (colour — the weight; a
    negative weight is its own colour in the window)."""
    return [{"i": t["i"], "from": centre_xyz, "to": t["xyz"], "lambda": t["lambda"], "z": t["z"]}
            for t in table if t["reason"] in ("selected", "hy_capped")]  # fmt: skip


# --- one row of the plan ------------------------------------------------------------------------------------------


def _fields_of(model: Model) -> dict[str, dict[str, Any]]:
    return {str(f["name"]).casefold(): f for f in model.fields}


def _row_fields(spec: EstimationSpec, have: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The fields the row writes: [{key, name (as stored in the model), dtype, unit, exists}]."""
    out = []
    for c in spec.columns():
        f = have.get(str(c["name"]).casefold())
        out.append({"key": c["key"], "name": str(f["name"]) if f else c["name"], "dtype": c["dtype"], "unit": c["unit"],
                    "exists": f is not None})  # fmt: skip
    return out


def one_row(model: Model, spot: Spot, row: Any, entry: Entry, fresh: dict[str, Any], state: Any, *,
            chosen: bool) -> dict[str, Any] | None:  # fmt: skip
    """The answer for one row of the plan; None — the block is not in its domain and the row was not chosen by the
    user (`params_id`)."""
    spec, rk = entry.spec, entry.rk
    have = _fields_of(model)
    cols = _row_fields(spec, have)
    present = [c["name"] for c in cols if c["exists"]]
    parent = spec.sub == "parent"
    centre, size = (spot.parent_centre, spot.parent_size) if parent else (spot.centre, spot.size)
    dom = domain_of(model, spec, entry.restrictor, spot, present)
    if not dom.active and not chosen:
        return None
    detail = kn.estimate_block(rk, centre, size, active=dom.active)
    values = {k: num(v) for k, v in detail.values.items()}
    status = int(detail.status)
    table = candidates_table(rk, detail)
    ell = {k: (num(v) if isinstance(v, float) else v) for k, v in detail.ellipsoid.items()}
    compared: list[dict[str, Any]] = []
    for c in cols:
        calc = float(detail.values.get(c["key"], math.nan))
        stored = dom.cols.get(c["name"]) if c["exists"] else None
        eq = None
        if stored is not None and dom.active:
            eq = same_bits(as_stored(calc, c["dtype"]), stored)
        compared.append({"key": c["key"], "name": c["name"], "dtype": c["dtype"], "unit": c["unit"],
                         "calc": num(calc), "stored": None if stored is None else num(stored), "same": eq,
                         "exists": c["exists"]})  # fmt: skip
    checked = [c["same"] for c in compared if c["same"] is not None]
    same = None if not checked else all(checked)
    differ = [c["name"] for c in compared if c["same"] is False]
    changed = bool(fresh.get("stale")) or same is False
    detail_text = fresh.get("stale_reason") if fresh.get("stale") else None
    if differ:
        detail_text = "; ".join(x for x in (detail_text, "пересчёт не совпал с полями: " + ", ".join(differ)) if x)
    missing = [c["name"] for c in compared if not c["exists"]]
    centre_xyz = [num(v) for v in centre]
    soft = None if spec.samples.soft is None else num(spec.samples.soft[1])
    lineage = None if state is None else {"state": state.state, "reason": state.reason, "changed": state.changed}
    # the samples that passed the walk of a pass that did not hold (too few of them, a degenerate system) were not
    # used: no segments in 3D, no weights in the snapshot
    selected = [t for t in table if t["reason"] in ("selected", "hy_capped")] if status == 0 else []
    out: dict[str, Any] = {
        "estimation_id": int(row.id), "name": str(row.name), "version": int(row.version), "method": spec.method.kind,
        "prefix": spec.outputs.prefix, "label": spec.target.label, "sub": spec.sub, "disc": list(spec.disc),
        "source": {"set_id": spec.samples.set_id, "table": spec.samples.table, "field": spec.samples.field},
        "block": {"centre": centre_xyz, "size": [num(v) for v in size], "rows": spot.rows if parent else 1},
        "domain": {"field": spec.target.domain_field, "label": spec.target.label, "row_label": dom.label},
        "active": dom.active, "status": status, "status_label": STATUS_LABELS[status] if 0 <= status <= 3 else None,
        "pass": int(detail.pass_), "values": values, "mu": num(detail.mu), "ellipsoid": ell,
        "passes": detail.passes, "counts": {**detail.counts, "shown": len(table)}, "soft_dist": soft,
        "candidates": table, "segments": segments_of(centre_xyz, selected), "fields": compared, "same": same,
        "missing_fields": missing, "changed": changed, "warning": WARNING if changed else None,
        "warning_detail": detail_text, "stale_reason": fresh.get("stale_reason"), "lineage": lineage,
    }  # fmt: skip
    out["snapshot"] = {
        "v": 1, "kind": "estimate.inspect", "model": {"id": model.id, "name": model.name, "version": model.version},
        "block": {"ijk": list(spot.ijk), "row": spot.row, "centre": centre_xyz, "size": out["block"]["size"]},
        "estimation": {"id": int(row.id), "name": str(row.name), "version": int(row.version),
                       "params": spec.canonical()},
        "result": {"status": status, "status_label": out["status_label"], "pass": out["pass"], "values": values},
        "ellipsoid": ell, "passes": detail.passes, "counts": out["counts"],
        "samples": [{k: t[k] for k in ("hole", "from", "to", "xyz", "z_raw", "z", "h", "dist", "octant", "lambda")}
                    for t in selected],
    }  # fmt: skip
    return out


# --- the rows of the plan the block can belong to ----------------------------------------------------------------


def _wrote(model_fields: dict[str, dict[str, Any]], row: Any, meta: dict[str, Any]) -> bool:
    """The row's fields (`<prefix>STATUS`) are in the model and were written by this row (`source.params.
    estimation_ids`, when the field has a source)."""
    prefix = str((meta.get("outputs") or {}).get("prefix") or "")
    f = model_fields.get((prefix + "STATUS").casefold())
    if f is None:
        return False
    ids = ((f.get("source") or {}).get("params") or {}).get("estimation_ids")
    return not isinstance(ids, list) or int(row.id) in [int(i) for i in ids]


def _rows(conn: sa.Connection, model: Model, params_id: int | None) -> list[Any]:
    if params_id is not None:
        row = sb.stored(conn, sb.KIND, params_id)
        if (row.meta or {}).get("model_id") != model.id:
            raise ApiError(422, "bad_param", "Эти параметры оценки относятся к другой блочной модели.",
                           "Выберите параметры оценки этой модели.", {"param": "params_id"})  # fmt: skip
        return [row]
    have = _fields_of(model)
    rows = conn.execute(sa.select(objects).where(objects.c.kind == sb.KIND)
                        .order_by(objects.c.name_key, objects.c.id)).all()  # fmt: skip
    got = [r for r in rows if (r.meta or {}).get("model_id") == model.id and _wrote(have, r, dict(r.meta or {}))]
    if not got:
        raise ApiError(409, "not_estimated", f"В блочной модели «{model.name}» нет полей оценки.",
                       "Запустите оценку по параметрам оценки этой модели.", {"param": "model_id"})  # fmt: skip
    return got


def run(project: Any, req: InspectSpec, tok: Any = None) -> dict[str, Any]:
    """The answer of `POST /estimate/inspect` (module docstring). 404 `block_model_not_found`,
    `estimation_not_found`; 409 `not_estimated`, `source_lost` (a chosen row), `superseded`; 422 `bad_param {param}`."""
    from bf_core import lineage

    def beat() -> None:
        if tok is not None:
            tok.check()

    store = BlobStore(project.folder)
    with dialect.read_tx(project.engine) as conn:
        model = Model.open(conn, store, req.model_id)
        rows = _rows(conn, model, req.params_id)
        nodes = [lineage.Node("object:estimation", int(r.id)) for r in rows]
        states = lineage.status(conn, nodes, store=store)
        fresh = {int(r.id): freshness(conn, dict(r.meta or {})) for r in rows}
        fps = {int(r.id): _fingerprints(conn, dict(r.meta or {})) for r in rows}
    spot = locate(model, req.at)
    beat()
    results: list[dict[str, Any]] = []
    warnings: list[str] = []
    for r, node in zip(rows, nodes, strict=True):
        beat()
        rid = int(r.id)
        try:
            if fresh[rid].get("source_lost") and req.params_id is not None:
                raise ApiError(409, "source_lost", "Источник параметров оценки удалён.",
                               "Восстановите таблицу, модель вариограммы или блочную модель.",
                               {"param": "params_id"})  # fmt: skip
            if fresh[rid].get("source_lost"):
                warnings.append(f"«{r.name}»: источник удалён, блок не пересчитан.")
                continue
            entry = _entry(project, store, model, r, fps[rid])
            got = one_row(model, spot, r, entry, fresh[rid], states.get(node), chosen=req.params_id is not None)
        except ApiError as e:
            if req.params_id is not None:
                raise
            warnings.append(f"«{r.name}»: блок не пересчитан — {e.message[:1].lower()}{e.message[1:]}")
            continue
        if got is not None:
            results.append(got)
    if not results and not warnings:
        warnings.append("Блок вне доменов всех строк оценки этой модели.")
    return jsonable({
        "model_id": model.id, "model_version": model.version,
        "block": {"row": spot.row, "p": spot.p, "ijk": list(spot.ijk),
                  "sub": None if spot.sub is None else list(spot.sub), "centre": spot.centre, "size": spot.size,
                  "parent": {"centre": spot.parent_centre, "size": spot.parent_size}, "rows": spot.rows},
        "results": results, "warnings": warnings,
    })  # fmt: skip
