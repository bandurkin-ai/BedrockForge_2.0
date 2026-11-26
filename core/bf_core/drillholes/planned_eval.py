"""What a planned hole is checked against (ТЗ 7.35 Д-34 ч. 2; ADR 0026 решения 28, 29; e19-api.md §10): the bodies of
a model («прогнозные интервалы»), the holes of a set (the nearest ones at the target; «План / факт»).

    rows = forecast(project, traj, length, model_id=…, mesh_ids=…, codes=…)    # [{from, to, code}], report
    near = nearest(index_of(st), st.traj, point, k=5)                          # [(hole index, distance, depth)]
    out  = compare(traj_planned, length, st, hole)                             # rows every 1 m, max

Forecast. The path is one hole cut by the bodies as `drillholes.from_model` cuts the holes of a set
(`from_model.whole_samples`, `composites.coding.body_stretches`, `from_model.fill_rows`): the bodies of a model by the
priority of its groups, closed wireframes by the order of the list; a gap between the bodies is an interval with
`code` null (never a code of 0); the first `from` is 0, the last `to` is the length of the hole, bit for bit.

Nearest. The point (the target, or the end of the hole) against the paths of the holes of a set: a `cKDTree` of the
survey stations picks the candidates, the distance is the exact one to the segments between the stations (a hole
with one station — to the point). A hole is a candidate when a station of it lies within `d_k + L / 2` of the point
(`d_k` — the distance of the k-th hole found first, `L` — the longest segment): the closest point of a segment is
within `L / 2` of one of its ends, so no hole that belongs to the first `k` is lost (the test checks it against all
the holes). The depth is the depth along the existing hole at its closest point (linear between the stations).

Compare. The actual hole against the planned one at the depths 0, 1, 2, … m and the end of the shorter one:
`dx, dy, dz = actual − planned`, `d` — the distance; two equal paths give 0.

Refusals: 404 `model_not_found`, `wireframe_not_found`, `hole_not_found`; 409 `model_building`; 422 `bad_param`
{param}, `no_bodies`, `wireframe_open`, `wireframe_orientation`, `hole_no_trajectory`.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.db import dialect

from . import planned as pl

if TYPE_CHECKING:
    from .desurvey import Trajectories

NEAREST_MAX = 5
EPS = 1e-9
_TREES = 2  # the trees of the sets kept (a tree of 50 000 holes — a few dozen MB)


# --- forecast -----------------------------------------------------------------------------------------------------


def bodies_of(conn: Any, body: Mapping[str, Any]) -> tuple[Any, Any]:
    """`(spec, wireframes)`: the bodies named by `model_id` or `mesh_ids` of the request, by priority
    (`from_model_spec`: the codes, the order, the refusals). Exactly one of the two."""
    from bf_core.composites import jobs_coding as jc

    from . import from_model_spec as fs

    given = [k for k in ("model_id", "mesh_ids") if body.get(k) is not None]
    if len(given) != 1:
        raise fs.bad("bad_param", "Выберите одно: модель или каркасы.", "Задайте ровно одно из model_id, mesh_ids.",
                     "model_id")  # fmt: skip
    spec = fs.Spec(0, "", "bodies", dict(body))
    if given[0] == "model_id":
        fs._model_bodies(conn, spec, fs._int(body["model_id"], "Модель", "model_id"), body.get("codes"))
    else:
        fs._mesh_bodies(conn, spec, body["mesh_ids"], body.get("codes"))
    return spec, jc.parse_wireframes(conn, spec.wireframe_ids)


def forecast(project: Any, traj: Trajectories, length: float, body: Mapping[str, Any]) -> dict[str, Any]:
    """`{intervals: [{from, to, code}], codes: [{code, length}], length, inside, journal}` of the hole (index 0 of
    `traj`) cut by the bodies of `body` (`bodies_of`)."""
    from bf_core.composites import coding
    from bf_core.composites import jobs_coding as jc

    from . import from_model as fm

    with dialect.read_tx(project.engine) as conn:
        spec, wf = bodies_of(conn, body)
    codes = list(dict.fromkeys(spec.codes))
    code_of = [codes.index(c) for c in spec.codes]
    samples, used = fm.whole_samples(traj, np.zeros(1, dtype=np.int64), np.zeros(1, dtype=np.int64))
    try:
        got = coding.body_stretches(samples, traj, used, jc.bodies(project, wf, keep=True), n_bodies=len(spec.codes))
    except ValueError as exc:  # CompositeError `wireframe_open`, `wireframe_orientation` → 422
        from bf_core.composites import jobs as cj

        raise cj.as_api(exc) from None
    rows = fm.fill_rows(got, samples.holes, samples.to, code_of)
    intervals: list[dict[str, Any]] = [{"from": float(f), "to": float(t), "code": None if c < 0 else codes[int(c)]}
                 for f, t, c in zip(rows.frm.tolist(), rows.to.tolist(), rows.code.tolist(), strict=True)]  # fmt: skip
    per: list[dict[str, Any]] = [{"code": name, "length": 0.0} for name in codes]
    for iv in intervals:
        if iv["code"] is not None:
            per[codes.index(iv["code"])]["length"] += iv["to"] - iv["from"]
    return {"intervals": intervals, "codes": per, "length": float(length), "inside": float(got.lines.lengths().sum()),
            "journal": [*spec.journal, *got.journal]}  # fmt: skip


# --- the nearest holes ----------------------------------------------------------------------------------------------


@dataclass
class Index:
    """The stations of a set for the search: the tree, the hole of every station, the longest segment."""

    tree: Any
    hole_of: np.ndarray
    longest: float


_lock = threading.Lock()
_cache: OrderedDict[tuple[Any, ...], Index] = OrderedDict()


def build_index(traj: Trajectories) -> Index:
    from scipy.spatial import cKDTree

    counts = np.diff(np.asarray(traj.offsets, dtype=np.int64))
    hole_of = np.repeat(np.arange(traj.n_holes, dtype=np.int64), counts)
    xyz = np.ascontiguousarray(traj.xyz, dtype=np.float64)
    longest = 0.0
    if xyz.shape[0] > 1:
        same = hole_of[1:] == hole_of[:-1]
        if same.any():
            longest = float(np.linalg.norm(xyz[1:][same] - xyz[:-1][same], axis=1).max())
    return Index(cKDTree(xyz) if xyz.shape[0] else None, hole_of, longest)


def index_of(st: Any) -> Index:
    """The index of a set of trajectories (`drillholes.trajectory.SetTrajectories`), kept by its key."""
    with _lock:
        hit = _cache.get(st.key)
        if hit is not None:
            _cache.move_to_end(st.key)
            return hit
    built = build_index(st.traj)
    with _lock:
        _cache[st.key] = built
        while len(_cache) > _TREES:
            _cache.popitem(last=False)
    return built


def forget() -> None:
    with _lock:
        _cache.clear()


def hole_distances(traj: Trajectories, holes: np.ndarray, point: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """`(distance, depth)` of the point to each of the `holes` (indices into `traj`, each with stations): the least
    distance to the segments between its stations and to its stations, and the depth along the hole there."""
    off = np.asarray(traj.offsets, dtype=np.int64)
    a, count = off[holes], off[holes + 1] - off[holes]
    total = int(count.sum())
    group = np.repeat(np.arange(holes.shape[0]), count)
    at = np.arange(total) - np.repeat(np.cumsum(count) - count, count) + np.repeat(a, count)
    xyz, md = np.asarray(traj.xyz)[at], np.asarray(traj.md)[at]
    d_point = np.linalg.norm(xyz - point, axis=1)
    same = group[1:] == group[:-1]
    p0, p1 = xyz[:-1][same], xyz[1:][same]
    seg = p1 - p0
    long2 = (seg * seg).sum(axis=1)
    t = np.zeros(long2.shape[0])
    np.divide(((point - p0) * seg).sum(axis=1), long2, out=t, where=long2 > 0.0)
    t = np.clip(t, 0.0, 1.0)
    d_seg = np.linalg.norm(point - (p0 + t[:, None] * seg), axis=1)
    depth_seg = md[:-1][same] + t * (md[1:][same] - md[:-1][same])
    g = np.concatenate((group[:-1][same], group))
    d = np.concatenate((d_seg, d_point))
    depth = np.concatenate((depth_seg, md))
    order = np.lexsort((d, g))
    first = np.concatenate(([True], g[order][1:] != g[order][:-1]))
    pick = order[first]
    return d[pick], depth[pick]


def nearest(index: Index, traj: Trajectories, point: Any, k: int = NEAREST_MAX) -> list[tuple[int, float, float]]:
    """The `k` holes nearest to `point` as `(hole index, distance, depth)`, the nearest first (module docstring)."""
    pt = np.asarray(point, dtype=np.float64).reshape(3)
    counts = np.diff(np.asarray(traj.offsets, dtype=np.int64))
    want = min(k, int((counts > 0).sum()))
    stations = int(index.hole_of.shape[0])
    if want <= 0 or index.tree is None:
        return []
    ask = min(stations, max(16, 4 * want))
    while True:
        _, near = index.tree.query(pt, k=ask)
        near = np.atleast_1d(near)
        holes = np.unique(index.hole_of[near[near < stations]])
        if holes.shape[0] >= want or ask >= stations:
            break
        ask = min(stations, ask * 4)
    first, _ = hole_distances(traj, holes, pt)
    kth = float(np.sort(first)[min(want, first.shape[0]) - 1])
    radius = (kth + 0.5 * index.longest) * (1.0 + 1e-12) + EPS
    candidates = np.unique(index.hole_of[np.asarray(index.tree.query_ball_point(pt, radius), dtype=np.int64)])
    dist, depth = hole_distances(traj, candidates, pt)
    best = np.argsort(dist, kind="stable")[:want]
    return [(int(candidates[i]), float(dist[i]), float(depth[i])) for i in best]


def nearest_rows(st: Any, point: Any, k: int = NEAREST_MAX) -> list[dict[str, Any]]:
    """`[{hole, hole_id, distance, depth}]` of the set `st` (`SetTrajectories`) at `point`."""
    got = nearest(index_of(st), st.traj, point, k)
    return [{"hole": str(st.names[h]), "hole_id": int(st.hole_ids[h]), "distance": d, "depth": z} for h, d, z in got]


# --- «План / факт» --------------------------------------------------------------------------------------------------


def find_hole(st: Any, body: Mapping[str, Any]) -> int:
    """The index of the hole of the request in the set: `hole` (the name) or `hole_id`; 404 `hole_not_found`, 422
    `hole_no_trajectory` for a hole without collar coordinates."""
    ident, name = body.get("hole_id"), body.get("hole")
    if isinstance(name, int) and not isinstance(name, bool) and ident is None:
        ident, name = name, None
    at: int | None = None
    if ident is not None and not isinstance(ident, bool) and isinstance(ident, int):
        at = st.index.get(int(ident))
    elif isinstance(name, str) and name.strip():
        key = name.strip().casefold()
        at = next((i for i, n in enumerate(st.names) if str(n).casefold() == key), None)
    else:
        raise pl.bad("hole", "Не выбрана фактическая скважина.", "Выберите скважину набора.")
    if at is None:
        raise ApiError(404, "hole_not_found", "Скважина не найдена в наборе.", "Выберите скважину из списка набора.")
    off = np.asarray(st.traj.offsets)
    if off[at + 1] <= off[at]:
        raise ApiError(422, "hole_no_trajectory", f"У скважины «{st.names[at]}» нет траектории.",
                       "Проверьте координаты устья и инклинометрию.")  # fmt: skip
    return int(at)


def compare(traj: Trajectories, length: float, st: Any, hole: int) -> dict[str, Any]:
    """`{rows: [{depth, dx, dy, dz, d}], max, max_depth, length: {planned, actual, compared}, hole, hole_id}`: the
    actual hole `hole` (index in `st.traj`) against the planned one (module docstring)."""
    actual = float(st.traj.lengths()[hole])
    common = min(length, actual)
    depth = pl.depth_grid(common)
    plan = pl.positions(traj, depth)
    fact = pl.positions(st.traj, depth, hole)
    delta = fact - plan
    dist = np.linalg.norm(delta, axis=1)
    rows = [{"depth": z, "dx": x, "dy": y, "dz": h, "d": d}
            for z, x, y, h, d in zip(depth.tolist(), delta[:, 0].tolist(), delta[:, 1].tolist(),
                                     delta[:, 2].tolist(), dist.tolist(), strict=True)]  # fmt: skip
    top = int(np.argmax(dist))
    return {"rows": rows, "max": float(dist[top]), "max_depth": float(depth[top]),
            "length": {"planned": float(length), "actual": actual, "compared": float(common)},
            "hole": str(st.names[hole]), "hole_id": int(st.hole_ids[hole])}  # fmt: skip
