"""The control of a resource report (подшаг 20.3; ТЗ 8.10 «Контроль», 7.35 Д-27; ADR 0027 decision 13; contract —
docs/progress/e20-api.md §3 «Control»).

    ctl = control.build(model, plan, sums, restrictor=r, rules=rules, conn=, store=, chunk=, tick=, cancel=)
    ctl = {"tolerance", "sum_groups", "no_density", "default_density", "no_grade", "status", "wireframes",
           "warnings"}                                  # → `sidecar:control`; `warnings` join those of the report

  * `sum_groups` — Σ of the groups − the total, per table (cut-off, balance) and the worst relative difference of
    the tonnes, the volume and the sum t·g (the metal is proportional to it) of each element: |Σ − total| / max(|Σ|,
    |total|), the norm ≤ 1e−12, else the warning `sum_mismatch`. The total is its own accumulator (kernels.py), so
    the check is of the order of the sums, not of a formula. No keys — nothing to compare (`checked` false).
  * `no_density` — the blocks with no density (NaN or < 0) and their volume: their tonnage is unknown, not 0;
    the warning `no_density`. `default_density` — the blocks and the tonnes with `DENS_SRC` = 1 (the density of the
    domain by default, ADR 0023), null if the model has no such field.
  * `no_grade` — per element the tonnes and the blocks with no grade (they are in T, not in ḡ and the metal).
  * `status` — per element that an estimate wrote (the field has `source.op = estimate`, and the model has the field
    `<prefix>STATUS` whose prefix starts its name): the blocks, the volume and its share of the volume of the
    restrictor for STATUS 1, 2, 3 (Д-27, ТД27-2: the volume of the status 1 inside the restrictor = Σ of the volumes of
    those blocks). The blocks are counted with the share f > 0 of the restrictor and a volume; volume = V·FRAC·f.
  * `wireframes` — per leaf `wireframe` of the rules: Σ V·FRAC·f of the leaf (side «inside») against the volume of
    the wireframe, %; > 1 % — the warning `wireframe_volume` (№ 110); a wireframe off the box of the model —
    `outside_extent`.

One extra pass over the model (the restrictor, the shares, the volumes) is made only when something needs it (the
field `DENS_SRC`, a status field, a wireframe in the rules); the sums are partial sums of a portion added by
`math.fsum`. Errors of the pass are the ones of the restrictor.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import geometry
from bf_core.blocks import restrict as rs
from bf_core.blocks.model import CHUNK, Model
from bf_core.geostat.estimate.spec_parts import STATUS_LABELS
from bf_core.storage.blobs import BlobStore

from . import compute as cp
from . import kernels as rk
from .result import Plan

TOLERANCE = 1e-12
WIREFRAME_PCT = 1.0  # a wireframe volume off by more than this (%) is a warning (№ 110)
DENS_SRC = "DENS_SRC"
STATUS = "STATUS"
Cancel = Callable[[], None]
Tick = Callable[[float | None, str | None], None]


def _warning(code: str, message: str, hint: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "message": message, "hint": hint, **extra}


def _rel(total: float, other: float) -> float:
    """|other − total| / max(|other|, |total|); 0 when both are 0."""
    scale = max(abs(total), abs(other))
    return 0.0 if scale == 0.0 else abs(other - total) / scale


class _Worst:
    """The worst relative difference of a quantity over the tables: {sum, total, rel}."""

    def __init__(self) -> None:
        self.rel, self.sum, self.total = -1.0, 0.0, 0.0

    def add(self, total: float, other: float) -> None:
        r = _rel(total, other)
        if r > self.rel:
            self.rel, self.sum, self.total = r, other, total

    def out(self) -> dict[str, float]:
        return {"sum": self.sum, "total": self.total, "rel": max(self.rel, 0.0)}


# --- Σ of the groups − the total --------------------------------------------------------------------------------------


def sum_groups(sums: cp.Sums, plan: Plan) -> dict[str, Any]:
    """The first check (module text); the tables are those of `result.row_ranges`."""
    from .result import row_ranges

    if sums.core.n_groups() == 0:
        return {"checked": False, "tonnes": None, "volume": None, "metal": {n: None for n in plan.names}, "ok": True}
    gs, gc, gn = sums.group_view()
    tot = {"tonnes": _Worst(), "volume": _Worst()}
    metal = {n: _Worst() for n in plan.names}
    for r in row_ranges(plan):
        q, _n = rk.range_sums(*sums.total_view(), r.lo, r.hi)
        qg, _ng = rk.range_sums(gs, gc, gn, r.glo, r.ghi)
        tot["volume"].add(float(q[0, 0]), math.fsum(qg[:, 0]))
        tot["tonnes"].add(float(q[0, 1]), math.fsum(qg[:, 1]))
        for i, name in enumerate(plan.names):
            metal[name].add(float(q[0, 3 + 3 * i]), math.fsum(qg[:, 3 + 3 * i]))
    worst = max([tot["tonnes"].rel, tot["volume"].rel, *(m.rel for m in metal.values())])
    return {"checked": True, "tonnes": tot["tonnes"].out(), "volume": tot["volume"].out(),
            "metal": {n: m.out() for n, m in metal.items()}, "ok": worst <= TOLERANCE}  # fmt: skip


# --- what the counted blocks lack -------------------------------------------------------------------------------------


def no_grade(sums: cp.Sums, plan: Plan) -> dict[str, dict[str, Any]]:
    """Per element the blocks and the tonnes with no grade (every counted block, the «без борта» table)."""
    e = len(sums.core.edges)
    q, n = rk.range_sums(*sums.total_view(), 0, e + 2)
    return {name: {"blocks": int(n[0, 1 + i]), "tonnes": float(q[0, 4 + 3 * i])} for i, name in enumerate(plan.names)}


# --- the extra pass ---------------------------------------------------------------------------------------------------


def _find(model: Model, name: str) -> dict[str, Any] | None:
    return next((f for f in model.fields if str(f["name"]).casefold() == name.casefold()), None)


def status_fields(model: Model, plan: Plan) -> dict[str, str]:
    """element → the name of its `<prefix>STATUS` field: the element is a field of an estimate (`source.op`), the
    status field is one of an estimate too and its prefix starts the name of the element (the longest prefix)."""
    stat = [f for f in model.fields if str(f["name"]).casefold().endswith(STATUS.casefold())
            and (f.get("source") or {}).get("op") == "estimate"]  # fmt: skip
    out: dict[str, str] = {}
    for name in plan.names:
        f = _find(model, name)
        if f is None or (f.get("source") or {}).get("op") != "estimate":
            continue
        best = None
        for s in stat:
            prefix = str(s["name"])[: -len(STATUS)].casefold()
            if name.casefold().startswith(prefix) and (best is None or len(prefix) > len(str(best["name"]))):
                best = s
        if best is not None:
            out[name] = str(best["name"])
    return out


def _status_codes(model: Model, field_name: str) -> dict[int, float]:
    """STATUS 1, 2, 3 → the value in the column: the code of its label in the dictionary of the field (the codes are
    the positions of `STATUS_LABELS`; a dictionary that does not have them falls back to the plain code)."""
    labels = [str(x).casefold() for x in (_find(model, field_name) or {}).get("labels") or []]
    out: dict[int, float] = {}
    for s in (1, 2, 3):
        text = STATUS_LABELS[s].casefold()
        out[s] = float(labels.index(text)) if text in labels else float(s)
    return out


class _Scan:
    """The sums of the extra pass (partial sums of the portions, `math.fsum` at the end)."""

    def __init__(self, names: Sequence[str]) -> None:
        self.volume: list[float] = []  # the volume of the restrictor: Σ V·FRAC·f of the counted blocks
        self.dens_blocks = 0
        self.dens_tonnes: list[float] = []
        self.status: dict[str, dict[int, list[float]]] = {p: {s: [] for s in (1, 2, 3)} for p in names}
        self.status_blocks: dict[str, dict[int, int]] = {p: dict.fromkeys((1, 2, 3), 0) for p in names}


def _scan(model: Model, plan: Plan, r: rs.Restrictor, src: str | None, statuses: Mapping[str, str], chunk: int,
          tick: Tick | None, cancel: Cancel | None) -> _Scan:  # fmt: skip
    """One pass: the volume of the restrictor, the tonnes of the blocks with `DENS_SRC` = 1, the blocks and the volume
    of each status. `statuses` — element → status field (a field of several elements is read once)."""
    core = plan.core
    by_field = sorted(set(statuses.values()))
    codes = {name: _status_codes(model, name) for name in by_field}
    out = _Scan(by_field)
    extra = [*core.reads(), *([src] if src else []), *by_field]
    for ch, f in r.iter(model, extra=extra, chunk=chunk, cancel=cancel):
        if tick is not None:
            tick(0.0 + 0.9 * (ch.start + ch.n) / max(model.rows, 1), None)
        frac = ch.cols[core.frac] if core.frac else None
        vol = geometry.row_volumes(model.grid, ch.p, ch.s, ch.w, frac)
        ok = (f > 0.0) & ~np.isnan(vol)
        ve = np.where(ok, vol * f, 0.0)
        out.volume.append(float(ve.sum()))
        if src is not None:
            dens = ch.cols[core.density_field] if core.density_field else np.full(ch.n, float(core.density_value or 0))
            sel = ok & (ch.cols[src] == 1)
            good = sel & np.isfinite(dens) & (dens >= 0.0)
            out.dens_blocks += int(good.sum())
            out.dens_tonnes.append(float((np.where(good, ve, 0.0) * np.where(good, dens, 0.0)).sum()))
        for name in by_field:
            col = ch.cols[name]
            for s in (1, 2, 3):
                m = ok & (col == codes[name][s])
                out.status_blocks[name][s] += int(m.sum())
                out.status[name][s].append(float(ve[m].sum()))
    return out


def _status(model: Model, plan: Plan, scan: _Scan, statuses: Mapping[str, str]) -> dict[str, Any]:
    total = math.fsum(scan.volume)
    out: dict[str, Any] = {}
    for el, field_name in statuses.items():
        prefix = field_name[: -len(STATUS)]
        items = []
        for s in (1, 2, 3):
            v = math.fsum(scan.status[field_name][s])
            items.append({"code": s, "label": STATUS_LABELS[s], "blocks": scan.status_blocks[field_name][s],
                          "volume": v, "share": v / total if total > 0.0 else None})  # fmt: skip
        out[el] = {"prefix": prefix, "field": field_name, "items": items}
    return out


# --- the wireframes of the rules --------------------------------------------------------------------------------------


def wire_leaves(rules: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The leaves `wireframe` of a rule tree with an id, the first of each id."""
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if not isinstance(node, Mapping):
            return
        if isinstance(node.get("wireframe"), Mapping):
            found.append(dict(node["wireframe"]))
        for it in node.get("items") or []:
            walk(it)

    walk(rules)
    seen: set[int] = set()
    out = []
    for leaf in found:
        oid = leaf.get("id")
        if oid is not None and int(oid) not in seen:
            seen.add(int(oid))
            out.append(leaf)
    return out


def _model_box(model: Model) -> tuple[np.ndarray, np.ndarray]:
    lx, ly, lz = (float(x) for x in model.grid.lengths())
    corners = model.grid.to_project(np.array(list(itertools.product((0.0, lx), (0.0, ly), (0.0, lz)))))
    return corners.min(axis=0), corners.max(axis=0)


def _wireframe(model: Model, plan: Plan, leaf: Mapping[str, Any], conn: sa.Connection, store: BlobStore, chunk: int,
               cancel: Cancel | None) -> dict[str, Any] | None:  # fmt: skip
    """Σ V·FRAC·f of the leaf (inside) against the volume of the wireframe; None — the wireframe is gone."""
    from bf_core.wireframes import store as wf

    oid = int(leaf["id"])
    try:
        info = wf.wireframe_info(conn, oid)
    except ApiError:
        return None
    node = {"wireframe": {**leaf, "side": "inside"}}
    r = rs.prepare(model, node, conn=conn, store=store)
    core = plan.core
    parts: list[float] = []
    for ch, f in r.iter(model, extra=[core.frac] if core.frac else [], chunk=chunk, cancel=cancel):
        vol = geometry.row_volumes(model.grid, ch.p, ch.s, ch.w, ch.cols[core.frac] if core.frac else None)
        ok = (f > 0.0) & ~np.isnan(vol)
        parts.append(float(np.where(ok, vol * f, 0.0).sum()))
    vr = math.fsum(parts)
    vw = (info.get("stats") or {}).get("volume")
    vw = None if vw is None else float(vw)
    diff = None if vw is None or not vw > 0.0 else abs(vr - vw) / vw * 100.0
    ext = info.get("extent")
    outside = False
    if ext and len(ext) == 6:
        lo, hi = _model_box(model)
        outside = bool((np.asarray(ext[3:]) < lo).any() or (np.asarray(ext[:3]) > hi).any())
    return {"wireframe_id": oid, "name": str(info["name"]), "volume_restrictor": vr, "volume_wireframe": vw,
            "diff_pct": diff, "warning": diff is not None and diff > WIREFRAME_PCT,
            "outside_extent": outside}  # fmt: skip


# --- the control ------------------------------------------------------------------------------------------------------


def build(
    model: Model,
    plan: Plan,
    sums: cp.Sums,
    *,
    restrictor: rs.Restrictor | None = None,
    rules: Mapping[str, Any] | None = None,
    conn: sa.Connection | None = None,
    store: BlobStore | None = None,
    chunk: int = CHUNK,
    tick: Tick | None = None,
    cancel: Cancel | None = None,
) -> dict[str, Any]:
    """The control of a pass (module text). `restrictor` — the one of the pass (None: every block); `rules` — its rule
    tree (the leaves `wireframe` are checked; None or no `conn`/`store` — none); `cancel` raises to stop the job."""
    warns: list[dict[str, Any]] = []
    sg = sum_groups(sums, plan)
    if not sg["ok"]:
        hint = "Сообщите в поддержку: порядок суммирования нарушен (отклонение больше 1e-12)."
        warns.append(_warning("sum_mismatch", "Сумма по группам не совпала с итогом.", hint))
    nd = {"blocks": int(sums.excluded.get("no_density", 0)), "volume": float(sums.no_density_volume)}
    if nd["blocks"]:
        hint = "Задайте плотность по умолчанию (Блочные модели → Плотность) или поле с плотностью."
        warns.append(_warning("no_density", f"У {nd['blocks']} блоков нет плотности: их тоннаж неизвестен.", hint))
    src_field = _find(model, DENS_SRC)
    src = None if src_field is None else str(src_field["name"])
    statuses = status_fields(model, plan)
    scan = None
    if src is not None or statuses:
        scan = _scan(model, plan, restrictor or rs.Restrictor([], [], None), src, statuses, chunk, tick, cancel)
    default_density = None
    if scan is not None and src is not None:
        default_density = {"blocks": scan.dens_blocks, "tonnes": math.fsum(scan.dens_tonnes)}
    status = _status(model, plan, scan, statuses) if scan is not None and statuses else {}
    wires: list[dict[str, Any]] = []
    if conn is not None and store is not None:
        for leaf in wire_leaves(rules):
            try:
                got = _wireframe(model, plan, leaf, conn, store, chunk, cancel)
            except ApiError as e:
                msg = f"Объём каркаса № {leaf['id']} не проверен: {e.message}"
                warns.append(_warning("wireframe_unchecked", msg, e.hint or "Проверьте каркас."))
                continue
            if got is None:
                continue
            wires.append(got)
            if got["warning"]:
                warns.append(_warning(
                    "wireframe_volume",
                    f"Объём блоков внутри каркаса «{got['name']}» отличается от объёма каркаса на "
                    f"{got['diff_pct']:.2f} %.",
                    "Уменьшите блоки у границы каркаса (субблоки) или проверьте каркас.",
                ))  # fmt: skip
            if got["outside_extent"]:
                warns.append(_warning("wireframe_outside", f"Каркас «{got['name']}» выходит за охват модели.",
                                      "Расширьте модель или проверьте координаты каркаса."))  # fmt: skip
    return {"tolerance": TOLERANCE, "sum_groups": sg, "no_density": nd, "default_density": default_density,
            "no_grade": no_grade(sums, plan), "status": status, "wireframes": wires, "warnings": warns}  # fmt: skip
