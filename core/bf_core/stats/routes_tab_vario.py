"""/api/v1/stats/tab — the variograms and the local uncertainty of the tab «Статистика» (ТЗ 7.29; ADR 0022 §11,
решение 21; подшаг 16.17): the routes of BF `variogram`, `variogram-model`, `variogram-map`, `local-uncertainty`
(`routers/stats.py`) on the kernels of 16.10 (`stats/tab_vario.py`, `stats/local.py`).

    POST /stats/tab/variogram        {…common, lag, nlag, lag_tol, directions: omni | four, min_pairs, standardize,
                                      model {nugget, structures}?} → {set_id, table, field, …`tab_variogram`}
    POST /stats/tab/variogram-model  {nugget, structures, h_max, n, directions} → `tab_variogram_model` (no data)
    POST /stats/tab/map              {…common, cell, n, min_pairs, standardize} → {set_id, table, field, …`tab_map`}
    POST /stats/tab/local            {…common, grid {nx, ny}, k, power, radius, realizations, seed, threshold}
                                      → {set_id, table, field, …`tab_local`}

Common keys — as the other `/stats/tab/*` (`routes_tab`): `set_id`, `table` (null — the sample table), `field`
(null — the first component), `holes` (names; empty — all). The points are the middles of the intervals with a
value and a place, in plan; a hole of `local` is the hole of its interval. More than 20 000 points — 422
`too_many_samples` {param: "table"} with the advice to composite or to use the window «Вариография» (Т7.29-2).
The values of the body go to the kernels as they are: a number or a text «0,5»; absent, null or "" — the default.
Missing values are not points (never 0); an empty lag, cell or node of the answer is null. Nothing is stored.

Errors `{code, message, hint, param?}`: 422 `bad_param` {param — the key of the body; of the model of
`variogram` — `model.<key>`; of the grid — `grid.nx`, `grid.ny`}, `field_not_found` / `field_not_number`,
`no_values`, `too_few_values`, `no_spread`, `too_many_samples`; 404 `set_not_found`, `table_not_found`.
Synchronous, in a HEAVY slot of `routes_charts` (2 threads); numpy and the kernels load on the first request."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from bf_core.api.errors import ApiError

from . import route_args as ra
from . import routes_charts as rc
from . import routes_tab as rt
from .routes_charts import Settings, Svc

router = APIRouter(prefix="/tab")
THREADS = 2
_HINT = (
    "Скомпозитируйте пробы («Анализ → Обработка проб → Композиты») и выберите таблицу композитов или часть "
    "скважин. Вариограммы без предела по числу проб считает окно «Вариография» (вкладка «Геостатистика»)."
)


def _points(project: Any, body: dict[str, Any]) -> tuple[dict[str, Any], Any, Any]:
    """({set_id, table, field}, `tab.TabPoints`, the hole number of every row given to `tab_points`)."""
    import numpy as np

    from . import source
    from .tab import tab_points
    from .weighted import StatsError

    set_id = ra.set_id_of(body)
    t, infos = rc.table_of(project, set_id, ra.name_of(body, "table"))
    name = ra.name_of(body, "field")
    field = rc.first_number(t, infos, "field") if name is None else rc.field_of(t, infos, name, "field")
    cols = source.read(project, set_id, t.id, [field.name])
    xyz = source.midpoints(project, cols)
    values = cols.number(field.name)
    groups = np.asarray(cols.hole_of(), dtype=np.int64)
    holes = rt._holes(body)
    if holes:
        wanted = {h.casefold() for h in holes}
        chosen = np.array([h.casefold() in wanted for h in cols.holes], dtype=bool)
        idx = np.flatnonzero(chosen[groups]) if cols.holes else np.zeros(0, dtype=np.int64)
        xyz, values, groups = xyz[idx], values[idx], groups[idx]
    try:
        points = tab_points(xyz, values)
    except StatsError as e:  # the advice of the variograms, not of declustering
        if e.code != "too_many_samples":
            raise
        raise StatsError(e.code, e.message, _HINT, param="table") from e
    return {"set_id": set_id, "table": t.name, "field": field.name}, points, groups


def variogram_of(project: Any, body: dict[str, Any]) -> dict[str, Any]:
    from .tab_vario import tab_variogram

    head, points, _groups = _points(project, body)
    out = tab_variogram(points, lag=body.get("lag"), nlag=body.get("nlag"), lag_tol=body.get("lag_tol"),
                        directions=body.get("directions"), min_pairs=body.get("min_pairs"),
                        standardize=body.get("standardize"), model=body.get("model"), threads=THREADS)  # fmt: skip
    return {**head, **out}


def model_of(_project: Any, body: dict[str, Any]) -> dict[str, Any]:
    from .tab_vario import tab_variogram_model

    nugget = body.get("nugget", body.get("nug"))
    return tab_variogram_model(nugget, body.get("structures"), h_max=body.get("h_max"), n=body.get("n"),
                               directions=body.get("directions"))  # fmt: skip


def map_of(project: Any, body: dict[str, Any]) -> dict[str, Any]:
    from .tab_vario import tab_map

    head, points, _groups = _points(project, body)
    out = tab_map(points, cell=body.get("cell"), n=body.get("n"), min_pairs=body.get("min_pairs"),
                  standardize=body.get("standardize"), threads=THREADS)  # fmt: skip
    return {**head, **out}


def local_of(project: Any, body: dict[str, Any]) -> dict[str, Any]:
    from .local import tab_local

    head, points, groups = _points(project, body)
    out = tab_local(points, groups, grid=body.get("grid"), k=body.get("k"), power=body.get("power"),
                    radius=body.get("radius"), realizations=body.get("realizations"), seed=body.get("seed"),
                    threshold=body.get("threshold"), threads=THREADS)  # fmt: skip
    return {**head, **out}


def _bad(param: str, message: str, hint: str) -> ApiError:
    return ra.bad(param, message, hint)


def _route(name: str, calc: Any, current: bool) -> Any:
    def post(svc: Svc, body: Settings) -> dict[str, Any]:
        project = svc.require_current() if current else None
        if not isinstance(body, dict):
            raise _bad("settings", "Тело запроса — объект настроек.", "Проверьте параметры анализа.")
        return rc.run(lambda: calc(project, body))

    post.__name__ = f"post_tab_{name.replace('-', '_')}"
    return post


for _name, _calc, _current in (("variogram", variogram_of, True), ("variogram-model", model_of, False),
                               ("map", map_of, True), ("local", local_of, True)):  # fmt: skip
    router.add_api_route(f"/{_name}", _route(_name, _calc, _current), methods=["POST"])
