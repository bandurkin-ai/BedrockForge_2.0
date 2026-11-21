"""/api/v1/stats/tab — the analyses of the tab «Статистика» (ТЗ 7.29; ADR 0019 §7, §10): declustering, normal
scores and their back-transform, the bootstrap (variograms, the map, the local uncertainty — Э16).

    POST /stats/tab/declus     {…common, weights: false} → {set_id, table, field, method, count, naive_mean,
                                declustered_mean, weight_min, weight_max, zero_weights, uniform, best_cell_size,
                                cell_sizes, means, …the parameters used, notes, counts, rows?, weights?}
    POST /stats/tab/nscore     {…common, zmin, zmax} → {set_id, table, field, count, values, scores, zmin, zmax,
                                table_rows, rows, sample_scores, weighted, counts}
    POST /stats/tab/backtr     {…common, zmin, zmax, scores [≤ 20 000], ltail, utail, ltpar, utpar}
                                → {set_id, table, field, values [… | null]}
    POST /stats/tab/bootstrap  {…common, n_boot, seed, confidence} → {set_id, table, field, …`bootstrap_stats`,
                                counts}

Common keys: `set_id`, `table` (`name_key` or id; null — the sample table of the set), `field` (null — the first
component of the table), `holes` (names; empty — all), `declus` — the parameters of declustering {method: cell |
polygon, cmin, cmax, ncell, noff, minmax | nx, ny, bounds} (the names of `composite.declus`, Э12): for `/declus`
what it computes (null — the cells by default), for the others the weights of the values (null — without).
The points are the intervals with a value and a place — the middle on the path of the hole, in plan
(`stats.tab.tab_points`, `stats.source.midpoints`); `rows` — dh_rows.id of the points. More than 20 000 — 422
`too_many_samples` {param: "table"} with the advice to composite (Т7.29-2). Nothing is stored.

The kernels — `stats/tab.py`; the answer is theirs plus {set_id, table (the name), field}; the `table` of
`tab_nscore` (the rows of the transform table) is `table_rows` here. Errors `{code, message, hint, param?}`: 422
`bad_param` {param}, `field_not_found` / `field_not_number` {field}, `no_values`, `too_few_values`, `no_spread`,
`too_many_samples`, `bad_input`; 404 `set_not_found`, `table_not_found`. Synchronous, in a HEAVY slot of
`routes_charts` (the kernels take 2 threads); numpy loads on the first request."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from bf_core.api.errors import ApiError

from . import route_args as ra
from . import routes_charts as rc
from .routes_charts import Settings, Svc

router = APIRouter(prefix="/tab")
THREADS = 2
DECLUS_KEYS = ("method", "cmin", "cmax", "ncell", "noff", "minmax", "nx", "ny", "bounds")
MAX_HOLES = 10_000


def _bad(param: str, message: str, hint: str) -> ApiError:
    return ra.bad(param, message, hint)


def _holes(body: dict[str, Any]) -> list[str]:
    v = body.get("holes")
    if v is None:
        return []
    if not isinstance(v, list) or len(v) > MAX_HOLES or not all(isinstance(h, str) and len(h) <= 200 for h in v):
        raise _bad("holes", f"Скважины — список имён (не больше {MAX_HOLES}).", "Выберите скважины заново.")
    return v


def _declus(body: dict[str, Any]) -> dict[str, Any] | None:
    v = body.get("declus")
    if v is None:
        return None
    if not isinstance(v, dict) or any(k not in DECLUS_KEYS for k in v):
        raise _bad("declus", "Параметры декластеризации: " + ", ".join(DECLUS_KEYS) + ".",
                   "Проверьте параметры декластеризации.")  # fmt: skip
    return dict(v)


def _points(project: Any, body: dict[str, Any]) -> tuple[dict[str, Any], Any, Any]:
    """({set_id, table, field}, `tab.TabPoints`, dh_rows.id of the rows the points index)."""
    import numpy as np

    from . import source
    from .tab import tab_points

    set_id = ra.set_id_of(body)
    t, infos = rc.table_of(project, set_id, ra.name_of(body, "table"))
    name = ra.name_of(body, "field")
    field = rc.first_number(t, infos, "field") if name is None else rc.field_of(t, infos, name, "field")
    cols = source.read(project, set_id, t.id, [field.name])
    xyz = source.midpoints(project, cols)  # of the whole table: the cache holds it so
    values, ids = cols.number(field.name), cols.row_ids
    holes = _holes(body)
    if holes:
        wanted = {h.casefold() for h in holes}
        chosen = np.array([h.casefold() in wanted for h in cols.holes], dtype=bool)
        idx = np.flatnonzero(chosen[cols.hole_of()]) if cols.holes else np.zeros(0, dtype=np.int64)
        xyz, values, ids = xyz[idx], values[idx], ids[idx]
    head = {"set_id": set_id, "table": t.name, "field": field.name}
    return head, tab_points(xyz, values), ids


def _ids(ids: Any, rows: list[int]) -> list[int]:
    import numpy as np

    return ids[np.asarray(rows, dtype=np.int64)].tolist()  # type: ignore[no-any-return]


def declus_of(project: Any, body: dict[str, Any]) -> dict[str, Any]:
    from .tab import tab_declus

    weights = body.get("weights")
    if weights is not None and not isinstance(weights, bool):
        raise _bad("weights", "«Веса проб» — да или нет.", "Передайте true или false.")
    head, points, ids = _points(project, body)
    out = tab_declus(points, _declus(body), threads=THREADS, weights=bool(weights))
    if weights:
        out["rows"] = _ids(ids, out["rows"])
    return {**head, **out}


def nscore_of(project: Any, body: dict[str, Any]) -> dict[str, Any]:
    from .tab import tab_nscore

    head, points, ids = _points(project, body)
    out = tab_nscore(points, declus=_declus(body), zmin=body.get("zmin"), zmax=body.get("zmax"), threads=THREADS)
    out["table_rows"] = out.pop("table")
    out["rows"] = _ids(ids, out["rows"])
    return {**head, **out}


def backtr_of(project: Any, body: dict[str, Any]) -> dict[str, Any]:
    from .tab import tab_backtr

    scores = body.get("scores")
    if not isinstance(scores, list):
        raise _bad("scores", "Нормальные оценки — список чисел.", "Введите хотя бы одно число.")
    head, points, _ids = _points(project, body)
    out = tab_backtr(points, scores, declus=_declus(body), zmin=body.get("zmin"), zmax=body.get("zmax"),
                     ltail=body.get("ltail", "linear"), utail=body.get("utail", "linear"), ltpar=body.get("ltpar"),
                     utpar=body.get("utpar"), threads=THREADS)  # fmt: skip
    return {**head, **out}


def bootstrap_of(project: Any, body: dict[str, Any]) -> dict[str, Any]:
    from .tab import tab_bootstrap

    head, points, _ids = _points(project, body)
    n_boot = 1000 if body.get("n_boot") is None else body["n_boot"]
    confidence = 0.95 if body.get("confidence") is None else body["confidence"]
    out = tab_bootstrap(points, n_boot=n_boot, seed=body.get("seed"), confidence=confidence, declus=_declus(body),
                        threads=THREADS)  # fmt: skip
    return {**head, **out}


def _route(name: str, calc: Any) -> Any:
    def post(svc: Svc, body: Settings) -> dict[str, Any]:
        project = svc.require_current()
        if not isinstance(body, dict):
            raise _bad("settings", "Тело запроса — объект настроек.", "Проверьте параметры анализа.")
        return rc.run(lambda: calc(project, body))

    post.__name__ = f"post_tab_{name}"
    return post


for _name, _calc in (("declus", declus_of), ("nscore", nscore_of), ("backtr", backtr_of),
                     ("bootstrap", bootstrap_of)):  # fmt: skip
    router.add_api_route(f"/{_name}", _route(_name, _calc), methods=["POST"])
