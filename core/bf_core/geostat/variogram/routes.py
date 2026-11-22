"""/api/v1/geostat/variography — the calculation of the experimental variograms (ADR 0022 §14, решение 13; подшаг
16.14; the forms — docs/progress/e16-api.md).

  * `GET /defaults?set_id&table&field[&domain_table&domain_field&domain_values&weights&transform]` → the counts,
    σ², the median length, the defaults of ADR §2 and the ranges of 8.6.4 (`points.defaults`); `domain_values`
    separated by «;»; `weights` absent — DECL_W when the table has it, empty — no weights.
  * `POST /experimental` {data, axes | directions, lags, tolerances, bands, min_pairs, downhole, dh_*, seed} →
    {source, id} of the job `variography.experimental` (queue heavy; `jobs.py`). The body is checked here: 404
    `set_not_found`, `table_not_found`; 422 `bad_param` {param}, `field_not_found`; what only the points tell
    (`no_values`, `too_few_values`, `no_spread`, `no_lengths`) — the failure of the job with that code.
  * `POST /preview` — the same body, synchronously in a HEAVY slot (`stats.routes_charts.SLOTS`): the form of a
    calculation over ≤ 20 000 points (`sampled`), `key: null`; every refusal — 422 / 404 here.
  * `POST /map` {data, plane, what, lags, dip_tol, band_v, min_pairs, fan {n, tol, q}, map {nu, nv, cell,
    thickness, min_pairs}, seed} → {source, id} of the job `variography.map` (`jobs_map.py`; 16.15).
  * `POST /fit` {key, type?, model, weights?, fix?, fix_nugget?} → {model, residual, n, starts, warnings, order,
    fixed} (`fit.fit_variogram` on the record `key` of the cache; 404 `result_not_found` {param: "key"}).
  * `POST /back` {key, model, ltail?, utail?, ltpar?, utpar?} → {model, share, warning, residual, curves}
    (`hermite.back_variogram` with the table of normal scores of the record `key`).
  * `POST /curve` {model, directions?, h?, h_max?, n?} → {directions: [{axis, az, plunge, h, gamma, cov}]}.
  `/fit`, `/back`, `/curve` — synchronously in a HEAVY slot; no record of the project is written.

A project «только просмотр» calculates (the record of the cache is not the project's data). No project — 409
`no_project`. The job not registered in this build — 422 `not_ready`. Plain `def` routes run in the thread pool;
numpy and the kernels load on the first request, not with this module.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.jobs.scheduler import Scheduler
from bf_core.jobs.service import jobs
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

router = APIRouter(prefix="/variography")
Svc = Annotated[ProjectService, Depends(projects)]
Sched = Annotated[Scheduler, Depends(jobs)]
Payload = Annotated[Any, Body()]
KIND = "variography.experimental"  # = jobs.KIND (the module of the job loads numpy-free, but with the registry)
KIND_MAP = "variography.map"  # = jobs_map.KIND_MAP


def _body(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Повторите расчёт из окна вариографии.")
    return raw


def _slot(fn: Any) -> Any:
    """`fn` in one of the HEAVY slots of the synchronous calculations; NaN → null, a kernel refusal → 422."""
    from bf_core.stats import routes_charts

    return routes_charts.run(fn)


@router.get("/defaults")
def defaults(
    svc: Svc,
    set_id: Annotated[int | None, Query()] = None,
    table: Annotated[str | None, Query()] = None,
    field: Annotated[str | None, Query()] = None,
    domain_table: Annotated[str | None, Query()] = None,
    domain_field: Annotated[str | None, Query()] = None,
    domain_values: Annotated[str | None, Query()] = None,
    weights: Annotated[str | None, Query()] = None,
    transform: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    from . import spec as sp

    project = svc.require_current()
    body: dict[str, Any] = {"set_id": set_id, "table": table, "field": field, "transform": transform}
    if weights is not None:
        body["weights"] = weights  # "" — no weights
    if domain_field is not None or domain_values is not None:
        values = [] if domain_values is None else [v for v in domain_values.split(";") if v.strip()]
        body["domain"] = {"field": domain_field, "values": values,
                          **({"table": domain_table} if domain_table else {})}  # fmt: skip

    def calc() -> dict[str, Any]:
        from . import points

        with dialect.read_tx(project.engine) as conn:
            data = sp.parse_data(conn, body)
        return points.defaults(project, data)

    return _slot(calc)  # type: ignore[no-any-return]


@router.post("/experimental")
def experimental(svc: Svc, s: Sched, body: Payload) -> dict[str, Any]:
    from bf_core.jobs.registry import KINDS as REGISTERED
    from bf_core.stats import source

    from . import spec as sp

    raw = _body(body)
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        spec = sp.parse_experimental(conn, raw)
        rows = next((t.rows for t in source.tables(conn, spec.data.set_id) if t.id == spec.data.table_id), 0)
    if KIND not in REGISTERED:
        raise ApiError(422, "not_ready", "Расчёт ещё не подключён в этой сборке.", "Обновите программу.")
    params = {"body": raw, "rows": int(rows), "dirs": 3 if spec.axes is not None else len(spec.directions),
              "nlag": max(spec.lags.nlag, spec.dh.nlag)}  # fmt: skip
    title = f"Вариограммы: {spec.data.field} · {spec.data.table_name}"
    key, job_id = s.submit(KIND, params, title=title)
    return {"source": key, "id": job_id}


@router.post("/preview")
def preview(svc: Svc, body: Payload) -> dict[str, Any]:
    from . import jobs as vj

    raw = _body(body)
    project = svc.require_current()
    return _slot(lambda: vj.preview(project, raw, threads=2))  # 2 threads, as the tab (16.28б)  # type: ignore[no-any-return]


@router.post("/map")
def map_(svc: Svc, s: Sched, body: Payload) -> dict[str, Any]:
    from bf_core.jobs.registry import KINDS as REGISTERED
    from bf_core.stats import source

    from . import spec as sp

    raw = _body(body)
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        spec = sp.parse_map(conn, raw)
        rows = next((t.rows for t in source.tables(conn, spec.data.set_id) if t.id == spec.data.table_id), 0)
    if KIND_MAP not in REGISTERED:
        raise ApiError(422, "not_ready", "Расчёт карты ещё не подключён в этой сборке.", "Обновите программу.")
    params = {"body": raw, "rows": int(rows), "dirs": spec.fan_n, "nlag": spec.lags.nlag}
    what = " и ".join({"fan": "веер", "map": "карта"}[w] for w in spec.what)
    title = f"Вариограммы ({what}): {spec.data.field} · {spec.data.table_name}"
    key, job_id = s.submit(KIND_MAP, params, title=title)
    return {"source": key, "id": job_id}


def _result(project: Any, key: Any) -> dict[str, Any]:
    from . import store

    return store.read_result(project.folder, key, "key")["result"]  # type: ignore[no-any-return]


@router.post("/fit")
def fit(svc: Svc, body: Payload) -> dict[str, Any]:
    raw = _body(body)
    project = svc.require_current()

    def calc() -> dict[str, Any]:
        from . import fit as vf

        return vf.fit_variogram(_result(project, raw.get("key")), raw.get("model"), kind=raw.get("type"),
                                weights=raw.get("weights"), fix=raw.get("fix"),
                                fix_nugget=raw.get("fix_nugget"))  # fmt: skip

    return _slot(calc)  # type: ignore[no-any-return]


@router.post("/back")
def back(svc: Svc, body: Payload) -> dict[str, Any]:
    raw = _body(body)
    project = svc.require_current()

    def calc() -> dict[str, Any]:
        from . import hermite, jobs_map

        result = _result(project, raw.get("key"))
        table = jobs_map.table_of(project.folder, str(raw["key"]))
        tails = {k: raw[k] for k in ("ltail", "utail", "ltpar", "utpar") if raw.get(k) is not None}
        return hermite.back_variogram(result, raw.get("model"), table, **tails)

    return _slot(calc)  # type: ignore[no-any-return]


@router.post("/curve")
def curve(svc: Svc, body: Payload) -> dict[str, Any]:
    raw = _body(body)
    svc.require_current()

    def calc() -> dict[str, Any]:
        from . import jobs_map

        return jobs_map.curve(raw)

    return _slot(calc)  # type: ignore[no-any-return]
