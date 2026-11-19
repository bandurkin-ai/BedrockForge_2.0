"""/api/v1/section-series/{id}/contours and …/links — the contours of a series for the 3D window (ТЗ 8.1.3–8.1.4;
ADR 0016 §3, §5, §8). Included by `router.py` (its prefix `/section-series`).

`GET /section-series/{id}/contours?k=&layers=` — BFA1 `series_contours` (`contours.encode`): the closed lines of
the layers (by default — the contour layer of the series) whose vertices are all in the window of section k
(`k` — one number from 0 or up to 8 of them, «2,4»: the shadow of k − 1 and k + 1 in one request). The header:
{kind, format, series_id, version, k, lines, points, layers [{id, name, rev}]}; the arrays: `xyz <f8` (no repeated
vertex: every line is closed), `offsets <u4`, `layer <u4`, `lid <u4`, `section <u4`. ETag by the version of the
series, k and the versions of the layers (If-None-Match → 304).

`POST /section-series/{id}/links {layers?: [id]}` — the contours by sections and their automatic links
(`links.link_table`): {series_id, version, count, step, names, layers, contours [{i, k, name, layer_id, lid,
vertices, uw, area, centre, off_plane}], links [[i, j, "overlap" | "centres"]], bodies [[i, …]], conflicts
[{sections, contours}], skipped {open, short, outside, no_height}, off_plane_limit, warnings}. The window shows
it as the table of «Каркас по серии» and sends the edited links as `links` of the operation `stitch_series`.

Errors {code, message, hint}: 404 `series_not_found`, `line_layer_not_found`; 422 `no_contour_layer` (no layers and
none set on the series), `bad_section` (k out of the series), `not_lines` (a cloud), `bad_series`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

router = APIRouter()
Svc = Annotated[ProjectService, Depends(projects)]
IfNoneMatch = Annotated[str | None, Header()]
FORMAT = 1  # bump when the rules of the contours change: every ETag changes
MAX_LAYERS = 50


def _ids(text: str | None, what: str, hint: str) -> list[int] | None:
    if text is None or not text.strip():
        return None
    try:
        out = [int(part) for part in text.replace(";", ",").split(",") if part.strip()]
    except ValueError as exc:
        raise ApiError(422, "bad_query", f"{what}: ожидаются целые числа через запятую.", hint) from exc
    return out


def _layers(series: Any, given: list[int] | None) -> list[int]:
    from .schema import params_of

    if given is None:
        layer = params_of(series.meta).get("contour_layer")
        if not isinstance(layer, int) or isinstance(layer, bool):
            raise ApiError(
                422, "no_contour_layer", f"У серии «{series.name}» не задан слой контуров.",
                "Задайте слой контуров в параметрах серии или выберите слои.",
            )  # fmt: skip
        given = [layer]
    ids = list(dict.fromkeys(int(i) for i in given))
    if not ids or len(ids) > MAX_LAYERS:
        raise ApiError(422, "bad_query", f"Слоёв контуров — от 1 до {MAX_LAYERS}.", "Выберите меньше слоёв.")
    return ids


def _series(conn: Any, series_id: int) -> tuple[Any, Any, list[str]]:
    from . import store
    from .geometry import SeriesError, series_geometry
    from .names import section_names

    try:
        row = store.row(conn, series_id)
        meta = row.meta if isinstance(row.meta, dict) else {}
        return row, series_geometry(meta), section_names(meta)
    except store.NotFound as e:
        raise ApiError(
            404, "series_not_found", "Серия разрезов не найдена: её удалили.", "Обновите список серий."
        ) from e
    except SeriesError as e:
        raise ApiError(422, e.code, e.message, e.hint or "Проверьте параметры серии.") from e


@router.get("/{series_id}/contours")
def series_contours(
    svc: Svc, series_id: int, k: Annotated[str, Query(max_length=200)],
    layers: Annotated[str | None, Query(max_length=2000)] = None, if_none_match: IfNoneMatch = None,
) -> Response:  # fmt: skip
    from bf_core.storage.blobs import BlobStore
    from bf_core.viz.keys import matches

    from . import contours

    project = svc.require_current()
    bs = BlobStore(project.folder)
    ks = _ids(k, "Номера разрезов", "Передайте номер разреза k (с нуля) или несколько через запятую.") or []
    with dialect.read_tx(project.engine) as conn:
        row, geom, _names = _series(conn, series_id)
        if not ks or len(ks) > contours.MAX_PICKED or any(not 0 <= x < geom.count for x in ks):
            raise ApiError(
                422, "bad_section", f"Номера разрезов: от 0 до {geom.count - 1}, не больше {contours.MAX_PICKED}.",
                "Обновите таблицу разрезов серии.",
            )  # fmt: skip
        ids = _layers(row, _ids(layers, "Слои", "Перечислите номера слоёв через запятую."))
        keys = [contours.layer_key(conn, project.uid, i)[0] for i in ids]
        tag_key = json.dumps([contours.KIND, FORMAT, project.uid, series_id, int(row.version), ks, keys])
        tag = '"' + hashlib.sha256(tag_key.encode("utf-8")).hexdigest() + '"'
        headers = {"ETag": tag, "Cache-Control": "no-cache"}
        if matches(if_none_match, tag):
            return Response(status_code=304, headers=headers)
        sc = contours.cached_contours(conn, bs, project.uid, row, ids)
    data = contours.encode(sc, contours.picked(sc, ks), {"series_id": series_id, "version": int(row.version), "k": ks})
    return Response(data, media_type="application/octet-stream", headers=headers)


class LinksBody(BaseModel):
    layers: list[int] | None = Field(default=None, max_length=MAX_LAYERS)


@router.post("/{series_id}/links")
def series_links(svc: Svc, series_id: int, body: LinksBody | None = None) -> dict[str, Any]:
    from bf_core.storage.blobs import BlobStore

    from . import contours
    from .links import link_table

    project = svc.require_current()
    bs = BlobStore(project.folder)
    with dialect.read_tx(project.engine) as conn:
        row, geom, names = _series(conn, series_id)
        ids = _layers(row, body.layers if body else None)
        sc = contours.cached_contours(conn, bs, project.uid, row, ids)
    return {"series_id": series_id, "version": int(row.version), **link_table(geom, sc, names)}
