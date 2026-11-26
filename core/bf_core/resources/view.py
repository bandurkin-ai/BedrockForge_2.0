"""The JSON of a resource report for the job and the routes (подшаг 20.7; docs/progress/e20-api.md §3, §5).

One shape for the answer of the job `resources.report` (a preview or a saved report) and for `GET /reports/{id}`:

    report_json(spec, report_id=, name=, version=, saved=, model=, rows=, rows_total=, truncated=, totals=, curve=,
                control=, excluded=, form=, warnings=, files=, seconds=) -> the answer of §3
    summary(spec) -> {elements, cutoff_field, density, keys, bench}      # what the window shows of the parameters
    form_sums(sums, plan, template, roles, cutoff=None) -> form           # the form of a pass (the job)
    form_table(table, elements, template, roles, cutoff=None) -> form     # the form of the stored rows (the route)
    stored_spec(conn, store, params) -> (spec, roles | None)              # a stored report against the model as it is

The forms are made by `forms.build` over the rows of ONE table (the cut-off of the form, the balance and the off-balance
when the template wants them) — not over every row of the result, whose number is up to 2·10⁶. pyarrow and numpy are
imported when a function needs them (the core reports BF-READY before them).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError

from .spec import ALGO, ReportSpec, metal_family, unit_label
from .spec import METAL_BASE_LABEL as BASE_LABEL

if TYPE_CHECKING:
    import pyarrow as pa

    from bf_core.blocks.model import Model
    from bf_core.storage.blobs import BlobStore

    from . import compute as cp
    from .result import Plan

PREVIEW_ROWS = 20_000  # the rows of a preview (ADR 0027 решение 16; `truncated` beyond)
FIRST_PAGE = 500  # the rows of a saved report in the answer of the job (the rest — `GET /reports/{id}?page=`)


# --- the parameters as the window shows them -------------------------------------------------------------------------


def elements_json(spec: ReportSpec) -> list[dict[str, Any]]:
    out = []
    for e in spec.elements:
        fam = metal_family(e.unit)
        out.append({"field": e.field, "unit": e.unit, "unit_label": unit_label(e.unit), "metal_family": fam,
                    "metal_base": None if fam is None else BASE_LABEL[fam]})  # fmt: skip
    return out


def keys_json(spec: ReportSpec) -> list[dict[str, Any]]:
    return [{"kind": k.kind, "field": k.field, "labels": None if k.labels is None else list(k.labels)}
            for k in spec.keys]  # fmt: skip


def bench_json(spec: ReportSpec) -> dict[str, Any] | None:
    b = spec.bench
    if b is None:
        return None
    return {"h": b.h, "z0": b.z0, "z_min": b.z_min, "z_max": b.z_max, "j0": b.j0, "n": b.n}


def summary(spec: ReportSpec) -> dict[str, Any]:
    dens = spec.density
    return {"elements": elements_json(spec), "cutoff_field": spec.cutoff_field,
            "density": {"field": dens if isinstance(dens, str) else None,
                        "value": None if isinstance(dens, str) or dens is None else float(dens)},
            "keys": keys_json(spec), "bench": bench_json(spec)}  # fmt: skip


def model_json(model: Model) -> dict[str, Any]:
    """{id, name, version, cells_sha, grid}: `cells_sha` is the SHA-256 of the file of the cells (the graph: the
    fingerprint of the model)."""
    return {"id": model.id, "name": model.name, "version": model.version, "cells_sha": model.shas().get("cells"),
            "grid": dict(model.meta.get("grid") or {})}  # fmt: skip


def report_json(
    spec: ReportSpec, *, report_id: int | None, name: str, version: int, saved: bool, model: Mapping[str, Any],
    rows: Sequence[Any], rows_total: int, truncated: bool, totals: Mapping[str, Any] | None,
    curve: Mapping[str, Any] | None, control: Mapping[str, Any] | None, excluded: Mapping[str, Any] | None,
    form: Mapping[str, Any] | None, warnings: Sequence[Mapping[str, Any]], files: Sequence[Any] = (),
    seconds: Mapping[str, float] | None = None,
) -> dict[str, Any]:  # fmt: skip
    """The answer of §3."""
    return {"report_id": report_id, "name": name, "version": version, "saved": saved, "algo": ALGO,
            "params": spec.canonical(), "params_hash": spec.params_hash(), "model": dict(model), **summary(spec),
            "rows": list(rows), "rows_total": rows_total, "truncated": truncated,
            "totals": None if totals is None else dict(totals), "curve": None if curve is None else dict(curve),
            "control": None if control is None else dict(control),
            "excluded": None if excluded is None else dict(excluded),
            "form": None if form is None else dict(form), "warnings": [dict(w) for w in warnings],
            "files": [dict(f) if isinstance(f, Mapping) else f for f in files],
            "seconds": None if seconds is None else dict(seconds)}  # fmt: skip


def api_rows(rows: Sequence[Mapping[str, Any]], nkeys: int) -> list[dict[str, Any]]:
    """The rows of a stored report as the pass makes them: a total row has `g` = [null] per key (the file: null)."""
    return [{**r, "g": [None] * nkeys} if r.get("total") and r.get("g") is None and nkeys else dict(r) for r in rows]


def merge_warnings(*groups: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The warnings of the result and of the control in one list (the same code and parameter once)."""
    seen: set[tuple[Any, Any, Any]] = set()
    out: list[dict[str, Any]] = []
    for g in groups:
        for w in g:
            key = (w.get("code"), w.get("param"), w.get("message"))
            if key not in seen:
                seen.add(key)
                out.append(dict(w))
    return out


def plain(x: Any) -> Any:
    """JSON form: NaN and ±inf → null (a gap is never 0, 8.0.1), numpy scalars → Python."""
    if isinstance(x, dict):
        return {str(k): plain(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [plain(v) for v in x]
    if hasattr(x, "item") and not isinstance(x, str | bytes):
        return plain(x.item())
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    return x


# --- the forms --------------------------------------------------------------------------------------------------------


def form_sums(sums: cp.Sums, plan: Plan, template: Mapping[str, Any], roles: Sequence[str] | None) -> dict[str, Any]:
    """The form of the pass: the rows of the table the form shows (the rows «all» when the cut-offs have «без борта»,
    else the lowest cut-off) and, for a balance template, the balance and the off-balance. The numbers are those of
    the rows the report stores (the same bins and sums, `result.iter_rows`)."""
    from . import forms
    from . import result as rr

    cuts = [c for c in plan.cutoffs if c is not None]
    want = None if None in plan.cutoffs or not cuts else min(cuts)
    small = replace(plan, cutoffs=(want,), balance=plan.balance if template["balance"] else None)
    rows = [rr.flat_row(r, plan.nkeys) for r in rr.iter_rows(sums, small)]
    return plain(forms.build(rows, template, rr.elements_of(plan.spec), roles))  # type: ignore[no-any-return]


def form_table(table: pa.Table, elements: Sequence[Mapping[str, Any]], template: Mapping[str, Any],
               roles: Sequence[str] | None, cutoff: float | None = None) -> dict[str, Any]:  # fmt: skip
    """The form of stored rows (`store.read_table`): only the rows of the table the form shows are turned into dicts."""
    import pyarrow as pa
    import pyarrow.compute as pc

    from . import forms

    kind = table["kind"]
    keep = pc.is_in(kind, value_set=pa.array(["all", "balance", "offbalance"]))
    cut_rows = table.filter(pc.equal(kind, "cutoff"))
    cuts = sorted({float(c) for c in cut_rows["cutoff"].to_pylist() if c is not None})
    if cuts:
        want = cutoff if cutoff is not None else cuts[0]
        close = [c for c in cuts if math.isclose(c, want, rel_tol=1e-12, abs_tol=1e-12)]
        if close:
            keep = pc.or_(
                keep,
                pc.and_(pc.equal(kind, "cutoff"), pc.is_in(table["cutoff"], value_set=pa.array(close, pa.float64()))),
            )
    rows = table.filter(keep).to_pylist()  # a total row has `g1` null: `forms.build` takes it as the total
    return plain(forms.build(rows, template, elements, roles, cutoff))  # type: ignore[no-any-return]


def stored_spec(
    conn: sa.Connection, store: BlobStore, params: Mapping[str, Any]
) -> tuple[ReportSpec, list[str] | None]:
    """The spec of a stored report and the role of each of its keys. The canonical form is resolved against the model
    as it is now (the labels of the keys, the benches); a model that is gone or has no such field any more leaves the
    spec as stored and no roles (the form then takes the first key as the class)."""
    from bf_core.blocks.model import Model

    from . import result as rr
    from .spec import parse_params

    spec = parse_params(dict(params))
    try:
        model = Model.open(conn, store, spec.model_id)
        spec = spec.resolved(model.fields, rr.z_range_of(model))
    except ApiError:
        return spec, None
    return spec, rr.roles_of(spec, model.fields)
