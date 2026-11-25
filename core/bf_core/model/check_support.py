"""«Карта опоры» (ТЗ 7.35 Д-32 п. 2; ADR 0026 решение 28; e19-api.md §8.2): how far from the data a surface of an
implicit model lies — the distance of every vertex to the nearest data point of its group.

    req = parse(body)                          # {wireframe_id?, threshold?, format?} → `Request` (422)
    out = run(project, model_id, req)          # `Support`: the answer of §8.2 and the distances of the vertices

The distance is the exact nearest neighbour (`scipy.spatial.cKDTree` over the points of the group in `implicit_points`,
all of them — contacts, the points inside and outside, the manual ones) in float64 metres; a surface whose group has no
points has NaN, never 0. `threshold` is D_оп (default 2·D_max of the data, 8.9.4, `spec.SUPPORT_FACTOR`); a vertex
beyond it is «экстраполяция». The share beyond is of the area of the surface: every triangle gives a third of its area
to each of its vertices, so `area_beyond` is the area that belongs to the vertices beyond the threshold, `share_beyond`
= `area_beyond / area` (None for a surface without area).

The surfaces are the `surface` outputs of the model (a domain: its isosurface); a model that has none (shells only)
gives the bodies of its groups; `wireframe_id` names one output (any kind), and only then there are `values` — the
distances in the order of the vertices of the wireframe: `format` json — a list (null for a gap, up to 500 thousand
vertices, else 422 `bad_param` {param: "format"} with the hint «f4»), f4 — float32 little-endian for the picture only
(8.0.1; the meaning of the answer is in the headers of the route, `Support.summary`), f4mesh — the same distances
together with the whole surface (`mesh_body`): the vertices and triangles of the wireframe itself, in the order the
distances have, so the picture of ANY wireframe (several parts, over 64 thousand triangles) is painted without matching
vertices.

The share is written into `meta.stats.by_group[…].support_share` of the group (with `support_threshold`): a record of
the last map, the version of the model does not change (a model edited or built meanwhile, a project «только
просмотр» — nothing is written, `recorded` is false). Refusals {code, message, hint}: 404 `model_not_found`,
`wireframe_not_found`; 409 `model_building`, `not_built`; 422 `bad_param`. numpy and scipy load with the first request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import objects

from . import store as ms
from . import store_base as sb
from . import store_blobs as bl
from .check import info_of, threads_of, warnings_of
from .spec import parse_support, refuse
from .spec_parts import D_MAX

JSON_MAX = 500_000  # vertices: more are given as float32
FORMATS = ("json", "f4", "f4mesh")


@dataclass(frozen=True)
class Request:
    wireframe_id: int | None
    threshold: float | None  # None — 2·D_max of the data
    format: str
    body: dict[str, Any]


def parse(body: Any) -> Request:
    """The request checked by itself (the project is not read): 422 `bad_param` {param}."""
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Повторите действие в окне.")
    fmt = body.get("format", "json")
    if fmt not in FORMATS:
        raise sb.bad("format", "Формат значений: json, f4 или f4mesh.", "Выберите json, f4 или f4mesh.")
    wid, thr = parse_support({k: v for k, v in body.items() if k in ("wireframe_id", "threshold")}, -1.0)
    given = body.get("threshold")
    return Request(wid, None if given is None or given == "" else thr, str(fmt), body)


@dataclass
class Support:
    out: dict[str, Any]  # the answer of §8.2 (`values` only for the format json)
    values: Any | None  # float64 [vertices] when `wireframe_id` was given (the route gives f4 as float32)
    summary: dict[str, Any] | None  # the entry of the surface asked for
    mesh: tuple[Any, Any] | None = None  # (float64 [vertices, 3], int [triangles, 3]) of that surface — for `f4mesh`


def _area(v: Any, t: Any) -> tuple[Any, float]:
    """(the area of every vertex — a third of its triangles, the area of the surface); the edges are differences of
    the coordinates, exact for any place of the project."""
    e1, e2 = v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]]
    a = 0.5 * np.linalg.norm(np.cross(e1, e2), axis=1)
    share = np.zeros(len(v))
    for k in range(3):
        share += np.bincount(t[:, k], weights=a / 3.0, minlength=len(v))
    return share, float(a.sum())


def _record(
    project: Any,
    model_id: int,
    version: int,
    shares: dict[tuple[int, int | None], tuple[float | None, float]],
) -> bool:
    """`support_share` into the entries of `meta.stats.by_group` (the group and the wing); the version of the model,
    its state and `updated_at` stay. False — nothing written."""
    if getattr(project, "read_only", False) or not shares:
        return False
    try:
        with dialect.write_tx(project.engine) as conn:
            row = sb.stored(conn, model_id)
            meta = dict(row.meta or {})
            stats = meta.get("stats")
            if int(row.version) != version or row.state == "building" or not isinstance(stats, dict):
                return False
            groups = []
            for g in stats.get("by_group") or []:
                key = (g.get("group"), g.get("wing"))
                if key in shares:
                    g = {**g, "support_share": shares[key][0], "support_threshold": shares[key][1]}
                groups.append(g)
            meta["stats"] = {**stats, "by_group": groups}
            conn.execute(sa.update(objects).where(objects.c.id == model_id, objects.c.version == version).values(
                meta=meta))  # fmt: skip
        return True
    except ApiError:
        return False


def run(project: Any, model_id: int, req: Request) -> Support:
    """The map of the support of the surfaces of the model (module text)."""
    from scipy.spatial import cKDTree

    from bf_core.storage.blobs import BlobStore
    from bf_core.wireframes import store as wf

    store = BlobStore(project.folder)
    with dialect.read_tx(project.engine) as conn:
        got = ms.get(conn, model_id)  # 404 `model_not_found`
        if got["state"] == "building":
            raise sb.building()
        shas = bl.shas_of(conn, model_id)
        if sb.ROLE_POINTS not in shas:
            raise refuse("not_built")
        spec = ms.load_spec(got["meta"])
        d_max = float(spec.data.d_max) if spec.data is not None else D_MAX
        threshold = req.threshold if req.threshold is not None else parse_support({}, d_max)[1]
        outs = list(got["outputs"])
        if req.wireframe_id is not None:
            pick = [o for o in outs if int(o["wireframe_id"]) == req.wireframe_id]
            if not pick:
                raise ApiError(404, "wireframe_not_found", "Эта поверхность не принадлежит модели.",
                               "Выберите поверхность из выходов модели.", {"param": "wireframe_id"})  # fmt: skip
        else:
            pick = [o for o in outs if o["kind"] == "surface"] or [
                o for o in outs if o["kind"] == "body" and o["group"] is not None]  # fmt: skip
        cols, _meta = bl.read_points(store, shas[sb.ROLE_POINTS])
        meshes = {int(o["wireframe_id"]): wf.read_wireframe(conn, store, int(o["wireframe_id"])) for o in pick}
    threads = threads_of()
    trees: dict[tuple[int | None, int | None], Any] = {}
    surfaces: list[dict[str, Any]] = []
    shares: dict[tuple[int, int | None], tuple[float | None, float]] = {}
    values = None
    summary = None
    mesh = None
    for o in pick:
        gid, wing = o["group"], o.get("wing")
        key = (gid, wing)
        if key not in trees:
            m = np.ones(len(cols["f"]), dtype=bool) if gid is None else cols["group"] == gid
            if wing is not None:
                m &= cols["wing"] == wing
            xyz = np.stack([cols["x"][m], cols["y"][m], cols["z"][m]], axis=1)
            trees[key] = cKDTree(xyz) if len(xyz) else None
        v, t = meshes[int(o["wireframe_id"])]
        tree = trees[key]
        d = np.full(len(v), np.nan) if tree is None else np.asarray(tree.query(v, workers=threads)[0], dtype=np.float64)
        per_vertex, area = _area(v, t) if len(t) else (np.zeros(len(v)), 0.0)
        beyond = float(per_vertex[d > threshold].sum())  # NaN is not beyond
        share = beyond / area if area > 0.0 and tree is not None else None
        finite = d[np.isfinite(d)]
        entry = {"wireframe_id": int(o["wireframe_id"]), "name": o["name"], "group": gid, "wing": wing,
                 "kind": o["kind"], "vertices": int(len(v)), "area": area,
                 "area_beyond": None if share is None else beyond, "share_beyond": share,
                 "max_distance": float(finite.max()) if finite.size else None}  # fmt: skip
        surfaces.append(entry)
        if gid is not None and o["kind"] == "surface":
            shares[(int(gid), None if wing is None else int(wing))] = (share, threshold)
        if req.wireframe_id is not None:
            values, summary, mesh = d, entry, (v, t)
    info = info_of(got)
    recorded = _record(project, model_id, int(got["version"]), shares)
    out: dict[str, Any] = {"model": info, "warnings": warnings_of(info), "threshold": threshold, "d_max": d_max,
                           "recorded": recorded, "surfaces": surfaces}  # fmt: skip
    if values is not None and req.format == "json":
        if len(values) > JSON_MAX:
            raise sb.bad("format", f"Вершин больше {JSON_MAX:,}: список не передать текстом.".replace(",", " "),
                         "Запросите значения в формате f4.")  # fmt: skip
        out["values"] = [None if x != x else x for x in values.tolist()]
    return Support(out, values, summary, mesh if req.format == "f4mesh" else None)


def mesh_body(values: Any, mesh: tuple[Any, Any]) -> tuple[bytes, dict[str, Any]]:
    """The body of `f4mesh` and its description for the header: the vertices as float32 offsets (x, y, z — three per
    vertex) from a whole-metre origin at the centre of the extent (`viz/meshes.py`, `ui/src/viz/origin.ts`: float32 is
    for the picture only), then the distances (float32, NaN — no data points), then the triangles (uint32, three
    vertices each), all little-endian in this order. `{origin, vertices, triangles}` is the description."""
    v, t = np.asarray(mesh[0], dtype=np.float64), np.asarray(mesh[1])
    origin = np.round((v.min(axis=0) + v.max(axis=0)) / 2.0) if len(v) else np.zeros(3)
    body = b"".join((
        (v - origin).astype("<f4").tobytes(),
        np.asarray(values).astype("<f4").tobytes(),
        t.astype("<u4").tobytes(),
    ))  # fmt: skip
    return body, {"origin": [float(x) for x in origin], "vertices": int(len(v)), "triangles": int(len(t))}


__all__ = ["FORMATS", "JSON_MAX", "Request", "Support", "mesh_body", "parse", "run"]
