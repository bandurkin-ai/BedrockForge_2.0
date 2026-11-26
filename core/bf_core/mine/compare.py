"""Two pit scenarios against each other: `POST /pit/compare {a, b}` (подшаг 21.15; ТЗ 8.11; ADR 0028 решение 27, 30;
`docs/progress/e21-api.md` §11). Synchronous, over the stored results of both scenarios; nothing is written.

    out = compare_scenarios(conn, store, a, b)          # the route wrapper (21.11): 404 `pit_scenario_not_found`,
                                                        # 409 `pit_no_result` (a scenario that has no result)
    sa, sb = load(conn, store, a), load(conn, store, b)       # the two read scenarios (pure data)
    out = compare(sa, sb, blocks=read_blocks(...), notes=…)   # the pure part: no connection, no files

The head of each side: {id, name, version, model {id, name, cells_sha}, solver, params_hash}. `params_diff` — the
parameters of the two RESULTS (`meta.params`, the resolved form) that differ, a list of {param, a, b} by the path of the
keys («elements.0.price», «rf.2»); `calendar` and `solver` are views, not parameters of the result. A path that only one
side has (a longer list, a key) has `null` on the other side.

`shells` — by RF (the values are the same number after `clean`): a shell that both have is `status: "both"` with
`a`, `b` and `diff` = a − b; an RF only one scenario has is `only_a` / `only_b`, the other side and `diff` are `null`
(a gap, never 0). The numbers of a shell are those of the pit C_k (cumulative at the base price, decision 18): blocks,
ore_t, waste_t, cf, and the NPV of its best and worst case (decision 19) — each side under ITS OWN calendar (the NPV of
both is shown, the warning `calendar_differs` says the calendars are not the same). A side without Q_р has no NPV:
`null` and the warning `no_calendar`. A value that is not known (NaN) is `null`, never 0.

`totals` — {a, b, diff} over the last shell C_N (`Report.totals()` and the NPV of C_N); `diff` is a − b (`null` when a
side has none).

`blocks` — the blocks of the FINAL pit that only A has and that only B has ({blocks, tonnes}: the rows of the model
with PIT > 0 of one scenario and PIT = 0 of the other; the tonnes = volume · density of that scenario). Only when
both are on one model and one `cells_sha` (`same_grid`); otherwise `null` with the warning `grids_differ`. The field PIT
of each scenario is the field `<prefix>PIT` of the model whose `source` is `{op: "pit", scenario_id}`: a field
that is another scenario's (overwritten) — `blocks: null`, the warning `pit_field_foreign` «поле принадлежит другому
сценарию»; no field — `pit_field_missing`. Rows with no tonnes (NaN density, no default) are counted in `no_tonnes`
and are not in the sum.

Warnings `{code, message, hint, side?}`: `grids_differ`, `stale` (a scenario is «устарел»), `calendar_differs`,
`no_calendar`, `pit_field_foreign`, `pit_field_missing`, `blocks_unavailable` (the model or its field is gone).
A = A (even the same scenario) gives 0 in every difference. Deterministic: the same bytes for the same results.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from bf_core.api.errors import ApiError

from . import npv as npv_mod
from . import spec
from . import store as st
from . import store_data as sd
from .report import Report
from .spec_parts import Calendar

if TYPE_CHECKING:
    import sqlalchemy as sa

    from bf_core.storage.blobs import BlobStore

ALGO = "pit-compare-1"
VIEWS = ("calendar", "solver")  # the parameters that are views of a result (решение 19), not a part of it
SHELL_KEYS = ("blocks", "ore_t", "waste_t", "cf", "npv_best", "npv_worst")
TOTAL_KEYS = ("blocks", "ore_t", "waste_t", "strip", "revenue", "cost_mine", "cost_process", "cost_sales", "royalty",
              "cf", "npv_best", "npv_worst")  # fmt: skip
RF_DIGITS = 9  # RFs are matched by the value rounded to the 9th digit (`clean` of the parameters)
PIT = "PIT"
_MISSING = object()


# --- the read scenario ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    """A scenario with a result, read from the project: the head, the parameters of the result and its report (the two
    stored tables); `stale` — «устарел» in the graph."""

    id: int
    name: str
    version: int
    params: dict[str, Any]
    params_hash: str | None
    model: dict[str, Any]
    solver: str | None
    stale: bool
    calendar: Calendar
    report: Report


def _report(conn: sa.Connection, store: BlobStore, scenario_id: int) -> Report:
    table = st.read_data(conn, store, scenario_id)
    benches = st.read_benches(conn, store, scenario_id)
    elements = sd.elements_of(table)
    cols = table.to_pydict()
    return Report.from_columns(elements, cols, benches.to_pydict() if benches is not None else {})


def load(conn: sa.Connection, store: BlobStore, scenario_id: int) -> Scenario:
    """The scenario with its result. 404 `pit_scenario_not_found`; 409 `pit_no_result`, `pit_scenario_broken`."""
    info = st.info(conn, scenario_id, store)
    if not info["has_result"]:
        raise st.no_result()
    meta = info["meta"]
    params = dict(meta.get("params") or {})
    cal = spec.parse_params({"calendar": params.get("calendar")}).calendar
    return Scenario(int(info["id"]), str(info["name"]), int(info["version"]), params, meta.get("params_hash"),
                    dict(meta.get("model") or {}), meta.get("solver"), bool(info["graph"]["stale"]), cal,
                    _report(conn, store, scenario_id))  # fmt: skip


# --- numbers ----------------------------------------------------------------------------------------------------------


def _num(x: Any) -> float | None:
    """A float of the arrays; NaN and ±inf — a gap (None), never 0."""
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def _sub(a: Any, b: Any) -> Any:
    return None if a is None or b is None else a - b


def _npv(sc: Scenario) -> npv_mod.Npv | None:
    """The NPV of every shell under the calendar of the scenario; None — the calendar has no Q_р."""
    return None if sc.calendar.q_ore is None else npv_mod.npv(sc.report, sc.calendar)


def _shell_map(sc: Scenario, npv: npv_mod.Npv | None) -> dict[float, dict[str, Any]]:
    """{RF (rounded): {k, rf, blocks, ore_t, waste_t, cf, npv_best, npv_worst}} of the pit C_k of every shell."""
    d = sc.report.data
    out: dict[float, dict[str, Any]] = {}
    for i in range(sc.report.n_shells):
        rf = float(d["rf"][i])
        out[round(rf, RF_DIGITS)] = {
            "k": int(d["k"][i]), "rf": rf, "blocks": int(d["cum_blocks"][i]), "ore_t": _num(d["cum_ore_t"][i]),
            "waste_t": _num(d["cum_waste_t"][i]), "cf": _num(d["cum_cf"][i]),
            "npv_best": None if npv is None else _num(npv.best[i]),
            "npv_worst": None if npv is None else _num(npv.worst[i]),
        }  # fmt: skip
    return out


def _side(row: Mapping[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in SHELL_KEYS}


def _shells(a: Scenario, b: Scenario, na: npv_mod.Npv | None, nb: npv_mod.Npv | None) -> list[dict[str, Any]]:
    ma, mb = _shell_map(a, na), _shell_map(b, nb)
    out = []
    for key in sorted(ma.keys() | mb.keys()):
        ra, rb = ma.get(key), mb.get(key)
        if ra is not None and rb is not None:
            status, diff = "both", {k: _sub(ra[k], rb[k]) for k in SHELL_KEYS}
        else:
            status, diff = ("only_a" if rb is None else "only_b"), None  # a gap on the other side, not 0
        out.append({"rf": (ra or rb or {})["rf"], "status": status, "k_a": None if ra is None else ra["k"],
                    "k_b": None if rb is None else rb["k"], "a": None if ra is None else _side(ra),
                    "b": None if rb is None else _side(rb), "diff": diff})  # fmt: skip
    return out


def _totals_of(sc: Scenario, npv: npv_mod.Npv | None) -> dict[str, Any]:
    out = sc.report.totals()
    n = sc.report.n_shells
    out["npv_best"] = None if npv is None or not n else _num(npv.best[-1])
    out["npv_worst"] = None if npv is None or not n else _num(npv.worst[-1])
    out["calendar"] = sc.calendar.canonical()
    return out


def _totals(a: Scenario, b: Scenario, na: npv_mod.Npv | None, nb: npv_mod.Npv | None) -> dict[str, Any]:
    ta, tb = _totals_of(a, na), _totals_of(b, nb)
    diff: dict[str, Any] = {k: _sub(ta.get(k), tb.get(k)) for k in TOTAL_KEYS}
    diff["elements"] = {
        e: {p: _sub(ta["elements"][e][p], tb["elements"][e][p]) for p in sd.ELEMENT_PARTS}
        for e in ta["elements"] if e in tb["elements"]
    }  # fmt: skip
    return {"a": ta, "b": tb, "diff": diff}


# --- the parameters ---------------------------------------------------------------------------------------------------


def _walk(a: Any, b: Any, path: str, out: list[dict[str, Any]]) -> None:
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        for key in sorted(a.keys() | b.keys(), key=str):
            _walk(a.get(key, _MISSING), b.get(key, _MISSING), f"{path}.{key}" if path else str(key), out)
    elif isinstance(a, list | tuple) and isinstance(b, list | tuple):
        for i in range(max(len(a), len(b))):
            _walk(a[i] if i < len(a) else _MISSING, b[i] if i < len(b) else _MISSING, f"{path}.{i}", out)
    elif a is _MISSING or b is _MISSING or not (a == b or (a != a and b != b)):  # NaN equals NaN here
        out.append({"param": path, "a": None if a is _MISSING else a, "b": None if b is _MISSING else b})


def params_diff(pa: Mapping[str, Any], pb: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The parameters of two results that differ, by the path of the keys («elements.0.price»), in the order of the
    paths; `calendar` and `solver` are not parameters of the result."""
    out: list[dict[str, Any]] = []
    keys = (pa.keys() | pb.keys()) - set(VIEWS)
    for key in sorted(keys, key=str):
        _walk(pa.get(key, _MISSING), pb.get(key, _MISSING), str(key), out)
    return out


# --- the blocks of the final pit --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BlockDiff:
    """The blocks of the final pit that only A has / that only B has: {blocks, tonnes, no_tonnes}."""

    only_a: dict[str, Any]
    only_b: dict[str, Any]


def _warn(code: str, message: str, hint: str, side: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"code": code, "message": message, "hint": hint}
    if side is not None:
        out["side"] = side
    return out


def _pit_field(model_fields: Sequence[Mapping[str, Any]], sc: Scenario, side: str) -> tuple[str | None, dict[str, Any]]:
    """(the name of the field PIT of the scenario in the model, None) or (None, the warning why there is none)."""
    want = f"{sc.params.get('prefix') or ''}{PIT}"
    found = next((f for f in model_fields if str(f["name"]).casefold() == want.casefold()), None)
    if found is None:
        return None, _warn("pit_field_missing", f"В модели нет поля «{want}» сценария «{sc.name}».",
                           "Запустите сценарий заново: поле будет записано.", side)  # fmt: skip
    src = found.get("source") or {}
    if not (src.get("op") == "pit" and src.get("scenario_id") == sc.id):  # the same test as `fields.is_own`
        return None, _warn("pit_field_foreign", f"Поле «{found['name']}» принадлежит другому сценарию: "
                           f"блоки сценария «{sc.name}» не сравниваются.",
                           "Задайте сценариям разные префиксы полей и запустите их заново.", side)  # fmt: skip
    return str(found["name"]), {}


def _tonnes(sc: Scenario, cols: Mapping[str, np.ndarray], vol: np.ndarray) -> np.ndarray:
    """volume · density of every row of a portion: the density is a field or a number; NaN → the default density."""
    dens = sc.params.get("density")
    n = vol.shape[0]
    d = np.asarray(cols[dens], dtype=np.float64) if isinstance(dens, str) else np.full(n, float(dens or np.nan))
    default = sc.params.get("default_density")
    if default is not None:
        d = np.where(np.isnan(d), float(default), d)
    out: np.ndarray = vol * d
    return out


def read_blocks(conn: sa.Connection, store: BlobStore, a: Scenario, b: Scenario
                ) -> tuple[BlockDiff | None, list[dict[str, Any]]]:  # fmt: skip
    """The blocks of the final pit only in A / only in B (module docstring) → (the diff or None, the warnings why
    there is none). `a`, `b` must be on one model and one `cells_sha` (the caller checks)."""
    from bf_core.blocks import geometry
    from bf_core.blocks.model import Model

    try:
        model = Model.open(conn, store, int(a.model["id"]))
        names, notes = [], []
        for sc, side in ((a, "a"), (b, "b")):
            name, note = _pit_field(model.fields, sc, side)
            names.append(name)
            if note:
                notes.append(note)
        if notes:
            return None, notes
        frac = model.role_field("frac")
        reads = [str(n) for n in names] + [
            str(sc.params["density"]) for sc in (a, b) if isinstance(sc.params.get("density"), str)
        ]
        reads += [str(frac["name"])] if frac is not None else []
        parts: dict[str, list[list[float]]] = {"a": [], "b": []}
        rows = {"a": 0, "b": 0}
        unknown = {"a": 0, "b": 0}
        for ch in model.iter(reads):
            vol = geometry.row_volumes(model.grid, ch.p, ch.s, ch.w, None if frac is None else ch.cols[frac["name"]])
            with np.errstate(invalid="ignore"):
                ina = ch.cols[str(names[0])] > 0  # PIT: the number of the smallest shell, 0 — outside (never empty)
                inb = ch.cols[str(names[1])] > 0
            for side, sc, mask in (("a", a, ina & ~inb), ("b", b, inb & ~ina)):
                t = _tonnes(sc, ch.cols, vol)[mask]
                ok = np.isfinite(t)
                rows[side] += int(mask.sum())
                unknown[side] += int((~ok).sum())
                parts[side].append(t[ok].tolist())
    except ApiError as e:
        return None, [_warn("blocks_unavailable", f"Блоки конечных карьеров не сравниваются: {e.message}", e.hint)]
    out = [{"blocks": rows[s], "tonnes": math.fsum(x for p in parts[s] for x in p), "no_tonnes": unknown[s]}
           for s in ("a", "b")]  # fmt: skip
    return BlockDiff(out[0], out[1]), []


# --- the comparison ---------------------------------------------------------------------------------------------------


def _head(sc: Scenario) -> dict[str, Any]:
    m = sc.model
    return {"id": sc.id, "name": sc.name, "version": sc.version,
            "model": {"id": m.get("id"), "name": m.get("name"), "cells_sha": m.get("cells_sha")},
            "solver": sc.solver, "params_hash": sc.params_hash}  # fmt: skip


def same_grid(a: Scenario, b: Scenario) -> bool:
    """One model and one `cells_sha` (the blocks of the two are the same rows); the same scenario — always."""
    if a.id == b.id:
        return True
    ma, mb = a.model, b.model
    return ma.get("id") is not None and ma.get("id") == mb.get("id") and ma.get("cells_sha") is not None and (
        ma.get("cells_sha") == mb.get("cells_sha"))  # fmt: skip


def compare(a: Scenario, b: Scenario, blocks: BlockDiff | None = None,
            notes: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:  # fmt: skip
    """The pure comparison of two read scenarios (module docstring). `blocks` — `read_blocks` (only for `same_grid`;
    else the field `blocks` is null), `notes` — its warnings."""
    same = same_grid(a, b)
    na, nb = _npv(a), _npv(b)
    warnings: list[dict[str, Any]] = []
    if not same:
        warnings.append(_warn("grids_differ", "Сетки различаются: блоки конечных карьеров не сравниваются.",
                              "Блоки сравниваются у сценариев одной модели с одной сеткой."))  # fmt: skip
    for sc, side in ((a, "a"), (b, "b")):
        if sc.stale:
            warnings.append(_warn("stale", f"Сценарий «{sc.name}» устарел: его результат не отражает текущих данных.",
                                  "Перестройте сценарий в дереве проекта.", side))  # fmt: skip
    for sc, n, side in ((a, na, "a"), (b, nb, "b")):
        if n is None:
            warnings.append(_warn("no_calendar", f"У сценария «{sc.name}» не задана переработка Q_р: NPV не рассчитан.",
                                  "Укажите Q_р в календаре сценария.", side))  # fmt: skip
    if a.calendar != b.calendar:
        warnings.append(_warn("calendar_differs", "Календари сценариев разные: NPV посчитаны каждый по своему.",
                              "Для честной разности задайте обоим сценариям один календарь."))  # fmt: skip
    warnings.extend(dict(w) for w in notes)
    shown = None
    if same and blocks is not None:
        shown = {"only_a": dict(blocks.only_a), "only_b": dict(blocks.only_b)}
    return {"algo": ALGO, "a": _head(a), "b": _head(b), "same_grid": same,
            "params_diff": params_diff(a.params, b.params), "shells": _shells(a, b, na, nb),
            "totals": _totals(a, b, na, nb), "blocks": shown, "warnings": warnings}  # fmt: skip


def compare_scenarios(conn: sa.Connection, store: BlobStore, a: int, b: int) -> dict[str, Any]:
    """`POST /pit/compare {a, b}` over one read transaction (A = B is allowed): 404 `pit_scenario_not_found`,
    409 `pit_no_result`."""
    sa_, sb_ = load(conn, store, a), load(conn, store, b)
    blocks, notes = read_blocks(conn, store, sa_, sb_) if same_grid(sa_, sb_) else (None, [])
    return compare(sa_, sb_, blocks, notes)
