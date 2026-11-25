"""/api/v1/stats/contact — the contact analysis (ТЗ 8.3; ADR 0019 §4, §10): the mean of a grade and the number of
samples by steps of the distance from the contacts of two domains along the holes.

    POST /stats/contact  {…the selection of `routes_charts`, set_id, table, field, domain, domain_table, a, b,
                          distance, step, wireframe_id, holes, blocks?}
        → {set_id, field, domain, domain_table, domain_outside, wireframe_id, distance, step, weighted, counts,
           contacts, holes, edges[], mean[] (null — no samples), n[], a {label, n, mean}, b {label, n, mean},
           conventions, blocks?}

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
by wireframes of `composites` takes it: `jobs_coding.bodies`, `coding.checked_target`); `domain`, `domain_table`, `a`,
`b` are not used then (`domain`, `domain_table` — null, `domain_outside` — 0; `counts.no_path` — rows of holes
without a trajectory). The wireframe is checked before the table is read: 404 `wireframe_not_found`, 422
`wireframe_open` / `wireframe_orientation` {param: wireframe_id} with the advice to repair it.

`blocks` {model_id, field, status_field?, domain_field?} (Д-28, e18-api §8.5) adds the profile of the blocks at the same
contact to the answer: `blocks` — `geostat.estimate.contact.blocks_profile` ({model_id, field, distance, step,
weighted, counts, edges, mean, n, volume, a, b, conventions}; the edges are those of the samples): the mean of
`field` of the blocks with `status_field` = 0 by the steps of the signed distance of the centre of the block to the
contact — the closed wireframe of the samples («до каркаса»), else the category field `domain_field` of the model
(default — `domain`) with the labels `a`, `b`. 404 `block_model_not_found`, `block_field_not_found`; 409
`block_model_broken`; 422 `bad_param` {param: blocks, domain_field, …}, `field_not_number`, `field_not_category`,
`variable_grid`, `no_values`.

Errors `{code, message, hint, param?}`: 422 `bad_param` {param: a, b, distance, step, domain, …},
`field_not_found` / `field_not_number` / `field_not_text` {param}, `no_values`, `too_many_series` (the settings);
404 `set_not_found`, `table_not_found`. Synchronous, in a HEAVY slot of `routes_charts`."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from bf_core.api.errors import ApiError

from . import route_args as ra
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
        raise ra.bad("wireframe_id", "Каркас задаётся номером.", "Выберите каркас из списка.")
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
        return coding.checked_target(next(bodies(project, wf)))
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
    cols = rc.only_holes(source.read(project, set_id, t.id, read), ra.holes_of(settings, "holes"))
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


def _blocks(project: Any, settings: dict[str, Any], blocks: Any, wf: Any) -> dict[str, Any]:
    """The profile of the blocks at the same contact (Д-28; `estimate.contact.blocks_profile`): the model, the field of
    the estimate and `status_field` (0 — estimated) of `blocks`; the contact — the wireframe of the samples (`wf`) or
    the domain field of the model (`blocks.domain_field`, else `domain`) with the same `a`, `b`; the same D and b."""
    from bf_core.blocks.model import Model
    from bf_core.charts.kinds import is_id
    from bf_core.db import dialect
    from bf_core.geostat.estimate import contact as ec
    from bf_core.storage.blobs import BlobStore

    from .weighted import StatsError

    if not isinstance(blocks, dict) or not is_id(blocks.get("model_id")):
        raise ra.bad("blocks", "Блочная модель задаётся номером.", "Выберите блочную модель и поле оценки.")
    if not isinstance(blocks.get("field"), str) or not blocks["field"]:
        raise ra.bad("blocks", "Не выбрано поле блочной модели.", "Выберите поле оценки блочной модели.")
    target = None if wf is None else _target(project, wf)
    with dialect.read_tx(project.engine) as conn:
        model = Model.open(conn, BlobStore(project.folder), int(blocks["model_id"]))
    kw: dict[str, Any] = {"wireframe": target} if target is not None else {
        "domain_field": blocks.get("domain_field") or settings.get("domain"), "a": settings.get("a"),
        "b": settings.get("b"),
    }  # fmt: skip
    try:
        return ec.blocks_profile(model, field=blocks["field"], status_field=blocks.get("status_field") or None,
                                 distance=settings.get("distance"), step=settings.get("step"), threads=THREADS,
                                 **kw)  # fmt: skip
    except StatsError as e:
        raise ra.stats_error(e) from e


@rc.register("contact")
def contact_of(project: Any, settings: dict[str, Any]) -> dict[str, Any]:
    wf = None if settings.get("wireframe_id") is None else _wireframe(project, settings["wireframe_id"])
    out = _samples(project, settings, wf)
    if settings.get("blocks") is not None:
        out["blocks"] = _blocks(project, settings, settings["blocks"], wf)
    return out


def _samples(project: Any, settings: dict[str, Any], wf: Any) -> dict[str, Any]:
    from . import select, source
    from .contact import contact_chart
    from .routes_more import domain_codes, domain_of, number_field, weight_field_of

    flt = select.SampleFilter.from_settings(settings)
    set_id = ra.set_id_of(settings)
    t, infos = rc.table_of(project, set_id, ra.name_of(settings, "table"))
    field = number_field(t, infos, settings, "field")
    weight_field = weight_field_of(t, infos, flt)
    if wf is not None:
        return _to_wireframe(project, set_id, t, field, weight_field, flt, settings, wf)
    dom = domain_of(project, set_id, t, infos, settings)
    if dom is None:
        raise ra.bad("domain", "Не выбрано поле доменов.", "Выберите текстовое поле доменов и домены A и B.")
    read = [field, source.LENGTH, *([weight_field] if weight_field else []), *([dom[1]] if dom[0].id == t.id else [])]
    holes = ra.holes_of(settings, "holes")
    cols = rc.only_holes(source.read(project, set_id, t.id, read), holes)
    codes, labels, outside = domain_codes(project, set_id, cols, dom, not holes)
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
