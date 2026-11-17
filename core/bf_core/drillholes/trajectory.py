"""Trajectories of a set from its collars and surveys (ТЗ 7.11), cached by the set version (collar and survey
edits bump it), and 3D coordinates of interval ends for the «Таблица» panel and later stages.

Holes are indexed densely here (0…n-1, sorted by dh_holes.id); a hole without a collar or collar coordinates
gets no trajectory and is listed in `skipped` by name. The length of a hole is its TD, or the deepest
interval / survey when TD is missing (so samples below a missing TD are not pinned to the collar).
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import dh_holes, dh_tables, objects


@dataclass
class SetTrajectories:
    key: tuple[Any, ...]
    hole_ids: np.ndarray  # dh_holes.id per dense index
    names: list[str]
    index: dict[int, int]  # dh_holes.id → dense index
    traj: Any  # desurvey.Trajectories

    @property
    def skipped(self) -> list[str]:
        return [self.names[i] for i in np.asarray(self.traj.skipped).tolist()]


_lock = threading.Lock()
_cache: OrderedDict[tuple[Any, ...], SetTrajectories] = OrderedDict()


def _numbers(conn: sa.Connection, sql: str, params: tuple[Any, ...], n: int) -> np.ndarray:
    """A raw select of numbers as a float64 matrix (rows, n), NULL → NaN: read through the driver in portions,
    converted per portion in C (50 000 holes × 20 stations, no per-row Python work)."""
    parts = [np.array(chunk, dtype=float).reshape(-1, n) for chunk in dialect.iter_driver_rows(conn, sql, params)]
    return np.concatenate(parts) if parts else np.zeros((0, n))


def _build(conn: sa.Connection, set_id: int, method: str, key: tuple[Any, ...]) -> SetTrajectories:
    from .desurvey import Collars, Surveys, desurvey

    holes = _numbers(
        conn,
        "SELECT id, x, y, z, td, azimuth, dip, has_collar FROM dh_holes WHERE set_id = ? ORDER BY id",
        (set_id,),
        8,
    )
    names = [
        r[0]
        for r in conn.execute(sa.select(dh_holes.c.name).where(dh_holes.c.set_id == set_id).order_by(dh_holes.c.id))
    ]
    ids = holes[:, 0].astype(np.int64)
    index = {int(h): i for i, h in enumerate(ids.tolist())}
    no_collar = ~(holes[:, 7] > 0)
    col: dict[str, np.ndarray] = {}
    for j, name in enumerate(("x", "y", "z", "td", "azimuth", "dip"), start=1):
        col[name] = np.ascontiguousarray(holes[:, j])
        col[name][no_collar] = np.nan
    sv = _numbers(conn, "SELECT hole_id, depth, azimuth, dip FROM dh_surveys WHERE set_id = ?", (set_id,), 4)
    s_dense = np.searchsorted(ids, sv[:, 0].astype(np.int64))  # ids ascend; surveys of holes of the set
    s_depth = np.ascontiguousarray(sv[:, 1])
    surveys = Surveys(s_dense, s_depth, np.ascontiguousarray(sv[:, 2]), np.ascontiguousarray(sv[:, 3]))

    td = col["td"]
    deepest = np.full(len(ids), np.nan)
    np.fmax.at(deepest, s_dense, s_depth)  # the deepest survey (NaN depths ignored)
    tables = conn.execute(
        sa.select(dh_tables.c.id).where(dh_tables.c.set_id == set_id, dh_tables.c.state == "ready")
    ).scalars()
    for table_id in tables.all():
        # one table at a time: its index (table, hole, from) gives the groups in order, no temporary B-tree
        deep = _numbers(
            conn,
            "SELECT hole_id, max(coalesce(to_m, from_m)) FROM dh_rows WHERE table_id = ? GROUP BY hole_id",
            (int(table_id),),
            2,
        )
        pos = np.searchsorted(ids, deep[:, 0].astype(np.int64)).clip(0, max(len(ids) - 1, 0))
        ok = (ids[pos] == deep[:, 0]) if len(ids) else np.zeros(len(deep), dtype=bool)
        np.fmax.at(deepest, pos[ok], deep[ok, 1])
    length = np.where(np.isnan(td), deepest, np.fmax(td, np.where(np.isnan(deepest), td, deepest)))
    collars = Collars(np.arange(len(ids)), col["x"], col["y"], col["z"], length, col["azimuth"], col["dip"])
    traj = desurvey(collars, surveys, method=method, n_holes=len(ids))
    return SetTrajectories(key, ids, names, index, traj)


def trajectories(project: Any, set_id: int, method: str = "minimum_curvature") -> SetTrajectories:
    with dialect.read_tx(project.engine) as conn:
        version = conn.execute(sa.select(objects.c.version).where(objects.c.id == set_id)).scalar()
        if version is None:
            from bf_core.api.errors import ApiError

            raise ApiError(404, "set_not_found", "Набор скважин не найден.", "Обновите дерево проекта.")
        # interval depths matter for holes without TD: the newest table revision is part of the key
        revs = conn.execute(
            sa.select(sa.func.coalesce(sa.func.sum(dh_tables.c.rev), 0), sa.func.count()).where(
                dh_tables.c.set_id == set_id
            )
        ).one()
        key = (project.uid, set_id, int(version), int(revs[0]), int(revs[1]), method)
        with _lock:
            hit = _cache.get(key)
            if hit is not None:
                _cache.move_to_end(key)
                return hit
        built = _build(conn, set_id, method, key)
    with _lock:
        for k in [k for k in _cache if k[:2] == key[:2]]:
            del _cache[k]
        _cache[key] = built
        while len(_cache) > 4:
            _cache.popitem(last=False)
    return built


def interval_xyz(
    st: SetTrajectories, hole_ids: np.ndarray, from_m: np.ndarray, to_m: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """(from, to) coordinates (n, 3) of intervals; NaN for holes without a trajectory."""
    dense = np.array([st.index.get(int(h), -1) for h in hole_ids.tolist()], dtype=np.int64)
    skipped = np.asarray(st.traj.skipped, dtype=np.int64)
    ok = (dense >= 0) & ~np.isin(dense, skipped) & ~np.isnan(from_m)
    a = np.full((len(dense), 3), np.nan)
    b = np.full((len(dense), 3), np.nan)
    if ok.any():
        to = np.where(np.isnan(to_m), from_m, to_m)
        a[ok] = st.traj.positions(dense[ok], from_m[ok])
        b[ok] = st.traj.positions(dense[ok], to[ok])
    return a, b
