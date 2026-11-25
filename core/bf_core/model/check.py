"""«Проверка модели по скважинам» (ТЗ 7.35 Д-32 п. 1; ADR 0026 решение 28; e19-api.md §8.1): the table of the
deviations of an implicit model from its data — one row for every data point.

    loaded = load(project, model_id)            # the arrays of the whole model (cached); 409 `not_built`
    out = page(loaded, cs, highlight)           # §8.1: the page of the sorted rows (`spec.parse_check`)
    data = csv_bytes(loaded, cs, highlight)     # the same rows, all of them, as the file of a table export

A row: the number of the point `i` (0-based, the row of `implicit_points`), the group, the type («контакт», «внутри»,
«снаружи», «ручная», «значение»), the source (a hole, a layer of strings) with `from` / `to` (a depth along the hole,
None for a layer) or the `vertex` of a line, the place x y z, `f_model` = the field of the RBF at the point, `f_data`
= the value given, `residual` = `f_model − f_data`, `distance` = the distance from the point to the wireframe of its
group (m; None — the group has no wireframe), `over` — `abs(residual)` is above the threshold of the highlight.

The field is the field of the build, not a new one: the patches of a group are made again from the stored files
(`implicit_patches`: the centre and the radius of a ball in the space of A·x; of a structural trend — in the project
coordinates, with the A_j of every patch from the file; `implicit_solution`: λ and c), each ball holding the points of
the group within its radius (the same `cKDTree` query as `octree.build_patches`; the count of every ball is checked
against the file — a mismatch is 409 `not_built`), and `evaluate.PatchField` is called at the points (`method`
"field" of a group in the answer). So with η = 0 the residual is the rounding only (ТД32-1). Where the balls overlap
so much that the sum costs seconds (Σ n² of the patches above TERMS_MAX: a thin dense slab) the value is taken from
the systems of the patches (`_by_identity`, `method` "identity": f_i − Σ W·η·λ / Σ W) in one pass. A group of
several fields (an indicator shell — a field of its own for every threshold) has a row per point and field;
`threshold` names it, `f_data` is the indicator (±0,5) of that threshold. Veins and expansions have no RBF of their
own: they have no rows.

The distance is `geo.nearest.nearest_triangles` to the body of the group (a shell: the body of the threshold of the
field; a group with no body: its surface) — the whole boundary of the body, the sheet of its own isosurface and the
faces where it meets the younger groups and the boundary of the model: the sheet alone lacks the contacts the younger
group took over (its top). A missing value is None, never 0. A gap in `f_model` (no patch
reaches the point) is None too.

The sort: `residual` — by `abs(residual)` down (a point with no residual last), equal ones by `i`; `source` — by the
name of the source, the depth, `i`. The arrays are kept for the next page (3 models, by the files and the wireframes
they were made of): the same bits for any number of threads. «Устарела» is not an error: the answer says so
(`warnings`) — the table is of the last build. Refusals {code, message, hint}: 404 `model_not_found`,
`group_not_found`; 409 `model_building`, `not_built`; 422 `bad_param`. Nothing is written.
"""

from __future__ import annotations

import csv
import io
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
import sqlalchemy as sa
from numpy.typing import NDArray

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import objects

from . import store as ms
from . import store_base as sb
from . import store_blobs as bl
from .spec import CheckSpec, refuse
from .spec_parts import D_MAX, POINT_TYPES

if TYPE_CHECKING:
    from .store_blobs import GroupData

F8 = NDArray[np.float64]
TYPE_LABELS = ("контакт", "внутри", "снаружи", "ручная", "значение")  # the order of `POINT_TYPES`
HIGHLIGHT_STEP = 0.1  # the default threshold of the highlight: this share of the step h of the model
KEEP = 3  # the models whose arrays are kept
TERMS_MAX = 400_000_000  # Σ n² of the patches of a field: above it the field is not summed again (`_by_identity`)
COLUMNS = ("№", "Группа", "Порог", "Тип", "Источник", "От, м", "До, м", "Вершина", "X", "Y", "Z", "f модели",
           "f данных", "Невязка", "Расстояние до каркаса, м", "Выше порога")  # fmt: skip
assert len(TYPE_LABELS) == len(POINT_TYPES)


def threads_of() -> int:
    return max(1, min(8, os.cpu_count() or 1))


def broken(what: str) -> ApiError:
    return refuse("not_built", f"Результат модели не подходит сохранённым данным: {what}.",
                  "Выполните «Построить» заново.")  # fmt: skip


# --- the arrays ---------------------------------------------------------------------------------------------------


@dataclass
class Table:
    """The rows of the check of a model, in the order of the groups, the wings, the fields, the points."""

    idx: Any  # int64: the row of `implicit_points`
    group: Any  # int32
    wing: Any  # int8, −1 — none
    fidx: Any  # int32: the number of the field of the group (an indicator shell: the threshold)
    kind: Any  # int8: the index of `POINT_TYPES`
    src: Any  # int32: the index of `sources`, −1 — none
    frm: Any
    to: Any
    vertex: Any  # int64, −1 — none
    xyz: Any  # float64 n × 3
    f_model: Any
    f_data: Any
    distance: Any
    threshold: Any  # float64: the threshold of an indicator field, NaN — none
    sources: list[str]
    names: dict[int, str]  # group id → its name
    groups: list[dict[str, Any]]  # per (group, wing, field): the summary
    h: float | None
    d_max: float
    _orders: dict[str, Any] = field(default_factory=dict)

    @property
    def n(self) -> int:
        return int(self.idx.shape[0])

    def residual(self) -> F8:
        return np.asarray(self.f_model - self.f_data)

    def order(self, sort: str) -> Any:
        """All the rows in the order of `sort` (kept)."""
        got = self._orders.get(sort)
        if got is None:
            if sort == "source":
                names = np.array(self.sources + [""], dtype=object)
                rank = np.unique(names[self.src], return_inverse=True)[1]
                depth = np.where(np.isnan(self.frm), np.inf, self.frm)
                got = np.lexsort((self.fidx, self.idx, depth, rank))
            else:
                r = np.abs(self.residual())
                got = np.lexsort((self.fidx, self.idx, np.where(np.isnan(r), np.inf, -r)))
            self._orders[sort] = got
        return got


@dataclass
class Loaded:
    table: Table
    info: dict[str, Any]  # the model now: id, name, version, state, stale, stale_reason, source_lost


_CACHE: OrderedDict[tuple[Any, ...], Table] = OrderedDict()
_LOCK = threading.Lock()


def _remembered(key: tuple[Any, ...]) -> Table | None:
    with _LOCK:
        got = _CACHE.get(key)
        if got is not None:
            _CACHE.move_to_end(key)
        return got


def _remember(key: tuple[Any, ...], table: Table) -> None:
    with _LOCK:
        _CACHE[key] = table
        _CACHE.move_to_end(key)
        while len(_CACHE) > KEEP:
            _CACHE.popitem(last=False)


def forget() -> None:
    """Drops the arrays kept (tests)."""
    with _LOCK:
        _CACHE.clear()


# --- reading the files --------------------------------------------------------------------------------------------


@dataclass
class Raw:
    spec: Any
    matrix: Any
    fields: dict[tuple[int, int | None], GroupData]
    points: dict[str, Any]
    sources: list[str]
    outputs: list[dict[str, Any]]
    meshes: dict[int, tuple[Any, Any]] = field(default_factory=dict)


def _geometry(conn: sa.Connection, store: Any, raw: Raw) -> None:
    """The vertices and triangles of the outputs the groups need: the surface of a domain, the bodies of a shell."""
    from bf_core.wireframes import store as wf

    for o in raw.outputs:
        if o["group"] is not None and o["kind"] in ("surface", "body") and o["wireframe_id"] not in raw.meshes:
            raw.meshes[int(o["wireframe_id"])] = wf.read_wireframe(conn, store, int(o["wireframe_id"]))


def info_of(got: dict[str, Any]) -> dict[str, Any]:
    return {"id": got["id"], "name": got["name"], "version": got["version"], "state": got["state"],
            "stale": bool(got["stale"]), "stale_reason": got["stale_reason"],
            "source_lost": bool(got["source_lost"])}  # fmt: skip


def load(project: Any, model_id: int) -> Loaded:
    """The table of the model (module text); the arrays come from the cache while the files and the outputs stand."""
    from bf_core.storage.blobs import BlobStore

    from . import job_stages as js

    store = BlobStore(project.folder)
    with dialect.read_tx(project.engine) as conn:
        got = ms.get(conn, model_id)  # 404 `model_not_found`
        if got["state"] == "building":
            raise sb.building()
        shas = bl.shas_of(conn, model_id)
        if set(shas) != set(sb.BLOB_ROLES):
            raise refuse("not_built")
        spec = ms.load_spec(got["meta"])
        matrix = js.global_matrix(conn, spec)
        ids = [int(o["wireframe_id"]) for o in got["outputs"]]
        versions = {int(r.id): int(r.version) for r in conn.execute(
            sa.select(objects.c.id, objects.c.version).where(objects.c.id.in_(ids)))} if ids else {}  # fmt: skip
        key = (str(project.folder), model_id, tuple(sorted(shas.items())), tuple(sorted(versions.items())),
               None if matrix is None else np.asarray(matrix, dtype="<f8").tobytes())  # fmt: skip
        info = info_of(got)
        hit = _remembered(key)
        if hit is not None:
            return Loaded(hit, info)
        points, meta = bl.read_points(store, shas[sb.ROLE_POINTS])
        raw = Raw(spec, matrix, ms.load_fields(conn, store, model_id), points,
                  [str(s) for s in meta.get("sources") or []], list(got["outputs"]))  # fmt: skip
        _geometry(conn, store, raw)
    table = build(raw)
    _remember(key, table)
    return Loaded(table, info)


# --- the fields ---------------------------------------------------------------------------------------------------


def _split(patches: dict[str, Any]) -> int:
    """The number of the patches of one field: the geometry of the patches of a group repeats for every field."""
    cr = np.stack([patches["cx"], patches["cy"], patches["cz"], patches["r"]], axis=1)
    total = len(cr)
    if total == 0:
        raise broken("у группы нет участков")
    same = np.flatnonzero((cr == cr[0]).all(axis=1))
    m = int(same[1]) if len(same) > 1 else total
    if total % m or not np.array_equal(cr, np.tile(cr[:m], (total // m, 1))):
        raise broken("участки полей группы не совпадают")
    return m


def _patch_set(patches: dict[str, Any], m: int, space: F8, spec: Any, matrix: Any, threads: int) -> Any:
    """The `PatchSet` of the first `m` rows: the balls hold the points of `space` within their radius, the order and
    the count of `octree.build_patches`; a count that is not the one stored is 409 `not_built`."""
    from scipy.spatial import cKDTree

    from .octree import PatchSet

    centres = np.ascontiguousarray(np.stack([patches["cx"][:m], patches["cy"][:m], patches["cz"][:m]], axis=1))
    radii = np.ascontiguousarray(patches["r"][:m], dtype=np.float64)
    lists = cKDTree(space).query_ball_point(centres, radii, return_sorted=True, workers=threads)
    sizes = np.fromiter((len(x) for x in lists), dtype=np.int64, count=m)
    if not np.array_equal(sizes, patches["n"][:m].astype(np.int64)):
        raise broken("число точек участков не совпадает с файлом (изменился тренд или анизотропия?)")
    indptr = np.zeros(m + 1, dtype=np.int64)
    np.cumsum(sizes, out=indptr[1:])
    indices = np.concatenate([np.asarray(x, dtype=np.int64) for x in lists]) if m else np.empty(0, np.int64)
    box = (centres - radii[:, None], centres + radii[:, None])
    return PatchSet(centers=centres, radii=radii, leaf_min=centres, leaf_max=centres,
                    leaf_counts=np.zeros(m, dtype=np.int64), indptr=indptr, indices=indices, hashes=(),
                    extent_min=box[0].min(axis=0), extent_max=box[1].max(axis=0), matrix=matrix,
                    n_patch=spec.pou.n_patch, n_min=spec.pou.n_min, delta=spec.pou.delta)  # fmt: skip


def _field_of(gd: GroupData, k: int, m: int, ps: Any, space: F8, spec: Any, threads: int) -> Any:
    """`PatchField` of the field `k` of a group: the solutions of its rows. A structural trend (19.9): the patches are
    in the project coordinates and every one has its A_j (`GroupData.matrices`); its points and centre are turned the
    way `solve.solve_patches` turned them, the radius of the ball is stretched."""
    from .anisotropy import stretch_of
    from .evaluate import PatchField
    from .octree import transform_points
    from .system import PatchSolution

    sols, mats = [], []
    for j in range(m):
        row = k * m + j
        radius = None
        if spec.kernel == "wendland":
            radius = spec.kernel_radius if spec.kernel_radius is not None else 2.0 * float(ps.radii[j])
        x, centre, scale = space[ps.patch(j)], np.array(ps.centers[j]), float(ps.radii[j])
        a = None if gd.matrices is None else np.asarray(gd.matrices[row], dtype=np.float64)
        if a is not None:
            x = transform_points(x, a)
            centre = transform_points(centre.reshape(1, 3), a)[0]
            scale *= stretch_of(a)
        mats.append(a)
        sols.append(PatchSolution(points=np.array(x, dtype=np.float64, order="C", copy=True), lam=gd.lam[row],
                                  poly=gd.c[row], centre=centre, scale=scale, kind=spec.kernel, radius=radius,
                                  eta=float(gd.patches["eta"][row]), cond1=float(gd.patches["cond"][row])))  # fmt: skip
    return PatchField(ps, sols, matrices=mats if gd.matrices is not None else None, threads=threads)


def _by_identity(gd: GroupData, k: int, m: int, ps: Any, space: F8, values: F8) -> F8:
    """f at the data points without summing the kernels again, for a field whose balls overlap so much that the sum
    costs seconds (a thin dense slab). A patch interpolates the data it holds up to its smoothing: its system is
    (K + η·I)·λ + P·c = f, so f_j(x_i) = f_i − η_j·λ_ji for every point i of the patch j, and a point is held by every
    patch whose ball reaches it (`PatchSet.indices`). The field at x_i is the mean of f_j(x_i) with the weights W_j
    of the partition of unity: f_i − Σ_j W_j·η_j·λ_ji / Σ_j W_j. The same number as the sum of the kernels up to the
    rounding of the solution of the systems (cond₁·1e−16 of the values); one pass over the members of the patches."""
    from .evaluate import partition_weight

    sizes = ps.sizes
    owner = np.repeat(np.arange(m), sizes)
    point = ps.indices
    r = np.linalg.norm(space[point] - ps.centers[owner], axis=1) / ps.radii[owner]
    w = partition_weight(r)
    eta = np.asarray(gd.patches["eta"][k * m : (k + 1) * m], dtype=np.float64)[owner]
    lam = np.concatenate([np.asarray(x, dtype=np.float64) for x in gd.lam[k * m : (k + 1) * m]])
    n = len(values)
    num = np.bincount(point, weights=w * eta * lam, minlength=n)
    den = np.bincount(point, weights=w, minlength=n)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.asarray(np.where(den > 0.0, values - num / den, np.nan))


def _meshes_of(raw: Raw, gid: int, wing: int | None, threshold: float | None) -> list[dict[str, Any]]:
    mine = [o for o in raw.outputs if o["group"] == gid and (wing is None or o.get("wing") in (None, wing))]
    pick = [o for o in mine if o["kind"] == "body"] or [o for o in mine if o["kind"] == "surface"]
    if threshold is not None:
        exact = [o for o in pick if o.get("threshold") is not None and abs(float(o["threshold"]) - threshold) < 1e-12]
        pick = exact or pick
    return pick


def _distance(raw: Raw, outputs: list[dict[str, Any]], xyz: F8, threads: int) -> F8:
    """The distance of every point to the nearest of the wireframes `outputs`; NaN — none."""
    from bf_core.geo.intersect import MeshTarget
    from bf_core.geo.nearest import nearest_triangles

    near = None
    for k, o in enumerate(outputs):
        v, t = raw.meshes[int(o["wireframe_id"])]
        if len(t):
            near = nearest_triangles(MeshTarget(v, t), xyz, into=near, mesh=k, threads=threads)
    if near is None:
        return np.full(len(xyz), np.nan)
    return np.asarray(np.sqrt(near.d2))


def build(raw: Raw) -> Table:
    """The arrays of the model from what the files hold (module text)."""
    from .data import indicator
    from .octree import transform_points

    threads = threads_of()
    spec, cols = raw.spec, raw.points
    parts: dict[str, list[Any]] = {k: [] for k in ("idx", "group", "wing", "fidx", "kind", "src", "frm", "to",
                                                  "vertex", "xyz", "f_model", "f_data", "distance", "thr")}  # fmt: skip
    names: dict[int, str] = {}
    summary: list[dict[str, Any]] = []
    for (gid, wing), gd in sorted(raw.fields.items(), key=lambda kv: (kv[0][0], -1 if kv[0][1] is None else kv[0][1])):
        g = next((x for x in spec.groups if x.id == gid), None)
        names[gid] = g.name if g is not None else f"Группа {gid}"
        rows = np.flatnonzero((cols["group"] == gid) & (cols["wing"] == (-1 if wing is None else wing)))
        xyz = np.ascontiguousarray(np.stack([cols["x"][rows], cols["y"][rows], cols["z"][rows]], axis=1))
        f = np.ascontiguousarray(cols["f"][rows])
        space = xyz if raw.matrix is None else transform_points(xyz, raw.matrix)
        m = _split(gd.patches)
        count = len(gd.patches["j"]) // m
        indicator_mode = g is not None and g.type == "shell" and g.params.get("mode") == "indicator"
        thresholds = [float(t) for t in g.params["thresholds"]] if indicator_mode and g is not None else []
        if count > (len(thresholds) if indicator_mode else 1):
            raise broken(f"у группы «{names[gid]}» другое число полей")
        ps = _patch_set(gd.patches, m, space, spec, raw.matrix, threads)
        exact = int((ps.sizes.astype(np.int64) ** 2).sum()) <= TERMS_MAX
        dist_of: dict[tuple[int, ...], F8] = {}
        for k in range(count):
            thr = thresholds[k] if indicator_mode else None
            values = indicator(f, thr) if thr is not None else f
            if exact:
                f_model = _field_of(gd, k, m, ps, space, spec, threads)(xyz, threads=threads)
            else:
                f_model = _by_identity(gd, k, m, ps, space, values)
            outs = _meshes_of(raw, gid, wing, thr)
            dkey = tuple(int(o["wireframe_id"]) for o in outs)
            if dkey not in dist_of:
                dist_of[dkey] = _distance(raw, outs, xyz, threads)
            dist = dist_of[dkey]
            n = len(rows)
            for name, a in (("idx", rows), ("group", np.full(n, gid, np.int32)),
                            ("wing", np.full(n, -1 if wing is None else wing, np.int8)),
                            ("fidx", np.full(n, k, np.int32)), ("kind", cols["kind"][rows]),
                            ("src", cols["src"][rows]), ("frm", cols["from"][rows]), ("to", cols["to"][rows]),
                            ("vertex", cols["vertex"][rows]), ("xyz", xyz), ("f_model", f_model),
                            ("f_data", values), ("distance", dist),
                            ("thr", np.full(n, np.nan if thr is None else thr))):  # fmt: skip
                parts[name].append(a)
            res = np.abs(f_model - values)
            summary.append({"group": gid, "name": names[gid], "type": g.type if g is not None else "domain",
                            "wing": wing, "threshold": thr, "points": n, "method": "field" if exact else "identity",
                            "max_abs_residual": None if not np.isfinite(res).any() else float(np.nanmax(res)),
                            "wireframes": list(dkey)})  # fmt: skip

    def cat(name: str, dtype: Any, shape: tuple[int, ...] = ()) -> Any:
        return np.concatenate(parts[name]).astype(dtype, copy=False) if parts[name] else np.zeros((0, *shape), dtype)

    d_max = float(spec.data.d_max) if spec.data is not None else D_MAX
    return Table(cat("idx", np.int64), cat("group", np.int32), cat("wing", np.int8), cat("fidx", np.int32),
                 cat("kind", np.int8), cat("src", np.int32), cat("frm", np.float64), cat("to", np.float64),
                 cat("vertex", np.int64), cat("xyz", np.float64, (3,)), cat("f_model", np.float64),
                 cat("f_data", np.float64), cat("distance", np.float64), cat("thr", np.float64), raw.sources, names,
                 summary, None if spec.h is None else float(spec.h), d_max)  # fmt: skip


# --- the answer ---------------------------------------------------------------------------------------------------


def _num(x: float) -> float | None:
    return None if x != x else float(x)  # NaN is a gap (8.0.1), not a number


def highlight_of(table: Table, given: float | None) -> float:
    """The threshold of the highlight: `given`, or HIGHLIGHT_STEP of the step h of the model (of D_max without h)."""
    if given is not None:
        return float(given)
    return HIGHLIGHT_STEP * (table.h if table.h is not None else 0.1 * table.d_max)


def _selected(table: Table, cs: CheckSpec) -> Any:
    if cs.group is not None and cs.group not in table.names:
        raise refuse("group_not_found", "В группе нет данных RBF: проверять нечего (жилы и расширения строятся без "
                     "поля).", "Выберите домен или оболочку.", group=cs.group)  # fmt: skip
    order = table.order(cs.sort)
    return order if cs.group is None else order[table.group[order] == cs.group]


def _row(table: Table, i: int, limit: float) -> dict[str, Any]:
    src = int(table.src[i])
    res = float(table.f_model[i] - table.f_data[i])
    thr = float(table.threshold[i])
    return {"i": int(table.idx[i]), "group": int(table.group[i]), "group_name": table.names[int(table.group[i])],
            "wing": None if table.wing[i] < 0 else int(table.wing[i]), "threshold": _num(thr),
            "type": TYPE_LABELS[int(table.kind[i])], "source": table.sources[src] if src >= 0 else None,
            "from": _num(table.frm[i]), "to": _num(table.to[i]),
            "vertex": None if table.vertex[i] < 0 else int(table.vertex[i]),
            "x": float(table.xyz[i, 0]), "y": float(table.xyz[i, 1]), "z": float(table.xyz[i, 2]),
            "f_model": _num(table.f_model[i]), "f_data": _num(table.f_data[i]), "residual": _num(res),
            "distance": _num(table.distance[i]), "over": bool(abs(res) > limit)}  # fmt: skip


def warnings_of(info: dict[str, Any]) -> list[dict[str, str]]:
    if not info["stale"]:
        return []
    why = f" ({info['stale_reason']})" if info.get("stale_reason") else ""
    return [{"code": "model_stale", "message": f"Модель устарела{why}: показан результат последнего построения.",
             "hint": "Выполните «Обновить», чтобы проверка относилась к текущим данным."}]  # fmt: skip


def page(loaded: Loaded, cs: CheckSpec, highlight: float | None = None) -> dict[str, Any]:
    """The answer of `GET …/check` (module text and e19-api §8.1)."""
    t = loaded.table
    sel = _selected(t, cs)
    limit = highlight_of(t, highlight)
    res = np.abs(t.residual()[sel])
    finite = res[np.isfinite(res)]
    start = (cs.page - 1) * cs.size
    shown = sel[start : start + cs.size].tolist()
    return {"model": loaded.info, "warnings": warnings_of(loaded.info), "group": cs.group,
            "n": int(np.count_nonzero(t.fidx[sel] == 0)), "total": int(sel.shape[0]),
            "max_abs_residual": float(finite.max()) if finite.size else None, "highlight": limit,
            "over": int(np.count_nonzero(res > limit)), "page": cs.page, "size": cs.size, "sort": cs.sort,
            "groups": t.groups, "rows": [_row(t, i, limit) for i in shown]}  # fmt: skip


def csv_bytes(loaded: Loaded, cs: CheckSpec, highlight: float | None = None) -> bytes:
    """All the rows of the selection as a file of a table export: «;», decimal comma, UTF-8 with BOM, the headers in
    Russian; a gap is an empty cell."""
    from bf_core.formats.numbers import number_text

    t = loaded.table
    sel = _selected(t, cs)
    limit = highlight_of(t, highlight)

    def cell(v: float | None) -> str:
        return "" if v is None else number_text(v).replace(".", ",")

    out = io.StringIO(newline="")
    w = csv.writer(out, delimiter=";")
    w.writerow(COLUMNS)
    for i in sel.tolist():
        r = _row(t, i, limit)
        w.writerow([r["i"], r["group_name"], cell(r["threshold"]), r["type"], r["source"] or "", cell(r["from"]),
                    cell(r["to"]), "" if r["vertex"] is None else r["vertex"], cell(r["x"]), cell(r["y"]),
                    cell(r["z"]), cell(r["f_model"]), cell(r["f_data"]), cell(r["residual"]), cell(r["distance"]),
                    "да" if r["over"] else ""])  # fmt: skip
    return ("﻿" + out.getvalue()).encode("utf-8")


__all__ = ["COLUMNS", "TYPE_LABELS", "Loaded", "Table", "csv_bytes", "forget", "highlight_of", "info_of", "load",
           "page", "threads_of", "warnings_of"]  # fmt: skip
