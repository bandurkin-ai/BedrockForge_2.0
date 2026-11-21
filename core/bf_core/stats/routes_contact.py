"""/api/v1/stats/contact — the contact analysis (ТЗ 8.3; ADR 0019 §4, §10): the mean of a grade and the number of
samples by steps of the distance from the contacts of two domains along the holes.

    POST /stats/contact  {…the selection of `routes_charts`, set_id, table, field, domain, domain_table, a, b,
                          distance, step, wireframe_id, holes}
        → {set_id, field, domain, domain_table, domain_outside, wireframe_id, distance, step, weighted, counts,
           contacts, holes, edges[], mean[] (null — no samples), n[], a {label, n, mean}, b {label, n, mean},
           conventions}

The answer is that of `stats.contact.contact_chart` laid out as the window reads it
(`ui/src/modules/charts/apiMore.ts`); `docs/progress/e13-api.md` has the exact shape. `field` — as the charts
by domains (`routes_more`): null — the first component of the table; `domain` — a text field of the same table or
of `domain_table` (the domain of a sample — the interval of that table its middle lies in, `source.domain_at`;
the rows without one take part as samples without a domain: no contact runs across them); `a`, `b` — two values
of the domain (A — the side below 0); `distance` D and `step` b, m (null — 20 and 1; ≤ 500 steps a side).
`counts` — those of the kernel: the selection, `no_depth`, `overlap`, `no_domain`, `in_steps`.

«До каркаса» (`wireframe_id`, ADR 0019 §4: the contact is the crossing of the hole with a closed wireframe, the
sides — `a` «снаружи», `b` «внутри») is `stats.contact.contact_chart_wireframe` (13.8) on the crossings of the
paths of the holes of the rows (`geo.intersect.hole_crossings` with the tree of the cached geometry, as the coding
by wireframes of `composites` takes it: `jobs_coding.bodies`, `coding._checked`); `domain`, `domain_table`, `a`,
`b` are not used then (`domain`, `domain_table` — null, `domain_outside` — 0; `counts.no_path` — rows of holes
without a trajectory). The wireframe is checked before the table is read: 404 `wireframe_not_found`, 422
`wireframe_open` / `wireframe_orientation` {param: wireframe_id} with the advice to repair it.

Errors `{code, message, hint, param?}`: 422 `bad_param` {param: a, b, distance, step, domain, …},
`field_not_found` / `field_not_number` / `field_not_text` {param}, `no_values`, `too_many_series` (the settings);
404 `set_not_found`, `table_not_found`. Synchronous, in a HEAVY slot of `routes_charts`."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from bf_core.api.errors import ApiError

from . import routes_charts as rc
from .routes_charts import Settings, Svc

router = APIRouter()
THREADS = 2


_FIX = "Контакт «до каркаса» — с замкнутым телом: проверьте каркас («Проверить каркас») и запустите «Исправить каркас»."


def _wireframe(project: Any, wireframe_id: Any) -> Any:
    """The wireframe of the mode «до каркаса»: there and closed by its stored measures — the `jobs_coding.Wireframes`
    of it (its geometry is not read yet)."""
    from bf_core.charts.kinds import is_id
    from bf_core.composites.coding import check_stats
    from bf_core.composites.intervals import CompositeError
    from bf_core.composites.jobs_coding import Wireframes
    from bf_core.db import dialect
    from bf_core.wireframes import exact

    if not is_id(wireframe_id):
        raise rc._bad("wireframe_id", "Каркас задаётся номером.", "Выберите каркас из списка.")
    with dialect.read_tx(project.engine) as conn:
        found = exact.set_targets(conn, [int(wireframe_id)])
    if not found:
        raise ApiError(404, "wireframe_not_found", "Каркас не найден в проекте или пуст.",
                       "Обновите список каркасов и выберите каркас снова.",
                       extra={"param": "wireframe_id"})  # fmt: skip
    state, stats = found[0]
    try:
        check_stats(str(state.name), stats)
    except CompositeError as e:
        raise ApiError(422, e.code, e.message, _FIX, extra={"param": "wireframe_id"}) from e
    return Wireframes([found[0]])


def _target(project: Any, wf: Any) -> Any:
    """The tree of boxes of the wireframe (`geo.intersect.MeshTarget`, `solid` set), as the coding by wireframes
    takes it: closed without a stored orientation — measured here; its neighbouring triangles wound one way."""
    from bf_core.composites import coding
    from bf_core.composites.intervals import CompositeError
    from bf_core.composites.jobs_coding import bodies
    from bf_core.wireframes.files import broken

    try:
        return coding._checked(next(bodies(project, wf)))
    except CompositeError as e:  # `wireframe_open`, `wireframe_orientation`
        raise ApiError(422, e.code, e.message, _FIX, extra={"param": "wireframe_id"}) from e
    except ValueError as e:  # a triangle of a vertex that is not there
        raise broken() from e


def _to_wireframe(
    project: Any, set_id: int, t: Any, field: str, weight_field: str | None, flt: Any, settings: dict[str, Any],
    wf: Any,
) -> dict[str, Any]:  # fmt: skip
    """The contact «до каркаса»: the crossings of the paths of the holes of the rows with the wireframe."""
    import numpy as np

    from bf_core.composites import coords
    from bf_core.composites import source as dh_source
    from bf_core.geo import intersect as gi

    from . import source
    from .contact import SIDES, contact_chart_wireframe

    read = [field, source.LENGTH, *([weight_field] if weight_field else [])]
    cols = rc.only_holes(source.read(project, set_id, t.id, read), rc._holes(settings, "holes"))
    target = _target(project, wf)
    st = dh_source.trajectories(project, set_id)
    dense = coords.hole_index(st.hole_ids, cols.hole_db_ids)[cols.hole_of()]
    hits = gi.hole_crossings(st.traj, [target], holes=np.unique(dense[dense >= 0]))
    out = contact_chart_wireframe(
        dense, cols.from_m, cols.to_m, hits, flt=flt, distance=settings.get("distance"), step=settings.get("step"),
        sides=SIDES, threads=THREADS, **cols.select_args(field, weight_field),
    )  # fmt: skip
    head = {"set_id": set_id, "field": field, "domain": None, "domain_table": None, "domain_outside": 0,
            "wireframe_id": wf.ids[0]}  # fmt: skip
    return {**head, **out}


@rc.register("contact")
def contact_of(project: Any, settings: dict[str, Any]) -> dict[str, Any]:
    from . import select, source
    from .contact import contact_chart
    from .routes_more import _domain, _domain_codes, _number, _weight_field

    flt = select.SampleFilter.from_settings(settings)
    set_id = rc._set_id(settings)
    wf = None if settings.get("wireframe_id") is None else _wireframe(project, settings["wireframe_id"])
    t, infos = rc.table_of(project, set_id, rc._name(settings, "table"))
    field = _number(t, infos, settings, "field")
    weight_field = _weight_field(t, infos, flt)
    if wf is not None:
        return _to_wireframe(project, set_id, t, field, weight_field, flt, settings, wf)
    dom = _domain(project, set_id, t, infos, settings)
    if dom is None:
        raise rc._bad("domain", "Не выбрано поле доменов.", "Выберите текстовое поле доменов и домены A и B.")
    read = [field, source.LENGTH, *([weight_field] if weight_field else []), *([dom[1]] if dom[0].id == t.id else [])]
    holes = rc._holes(settings, "holes")
    cols = rc.only_holes(source.read(project, set_id, t.id, read), holes)
    codes, labels, outside = _domain_codes(project, set_id, cols, dom, not holes)
    args = cols.select_args(field, weight_field)
    out = contact_chart(
        cols.hole_of(), cols.from_m, cols.to_m, codes, labels, settings.get("a"), settings.get("b"), args["values"],
        flt, length=args["length"], weight_values=args["weight_values"], distance=settings.get("distance"),
        step=settings.get("step"), threads=THREADS,
    )  # fmt: skip
    head = {"set_id": set_id, "field": field, "domain": dom[1], "domain_table": None if dom[0].id == t.id
            else dom[0].name, "domain_outside": outside, "wireframe_id": None}  # fmt: skip
    return {**head, **out}


@router.post("/contact")
def post_contact(svc: Svc, body: Settings) -> JSONResponse:
    project = svc.require_current()
    settings = rc.checked("contact", body)
    return JSONResponse(rc.run(lambda: contact_of(project, settings)))
