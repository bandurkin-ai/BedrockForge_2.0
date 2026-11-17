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
from bf_core.db.schema import dh_holes, dh_rows, dh_surveys, dh_tables, objects


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


def _build(conn: sa.Connection, set_id: int, method: str, key: tuple[Any, ...]) -> SetTrajectories:
    from .desurvey import Collars, Surveys, desurvey

    holes = conn.execute(
        sa.select(
            dh_holes.c.id,
            dh_holes.c.name,
            dh_holes.c.x,
            dh_holes.c.y,
            dh_holes.c.z,
            dh_holes.c.td,
            dh_holes.c.azimuth,
            dh_holes.c.dip,
            dh_holes.c.has_collar,
        )
        .where(dh_holes.c.set_id == set_id)
        .order_by(dh_holes.c.id)
    ).all()
    ids = np.array([h.id for h in holes], dtype=np.int64)
    index = {int(h): i for i, h in enumerate(ids.tolist())}

    def col(name: str) -> np.ndarray:
        return np.array(
            [np.nan if getattr(h, name) is None or not h.has_collar else float(getattr(h, name)) for h in holes],
            dtype=float,
        )

    td = col("td")
    deepest = np.full(len(ids), np.nan)
    deep_rows = conn.execute(
        sa.select(dh_rows.c.hole_id, sa.func.max(sa.func.coalesce(dh_rows.c.to_m, dh_rows.c.from_m)))
        .join(dh_tables, dh_tables.c.id == dh_rows.c.table_id)
        .where(dh_tables.c.set_id == set_id, dh_tables.c.state == "ready")
        .group_by(dh_rows.c.hole_id)
    ).all()
    deep_surv = conn.execute(
        sa.select(dh_surveys.c.hole_id, sa.func.max(dh_surveys.c.depth))
        .where(dh_surveys.c.set_id == set_id)
        .group_by(dh_surveys.c.hole_id)
    ).all()
    for hid, d in [*deep_rows, *deep_surv]:
        i = index.get(int(hid))
        if i is not None and d is not None:
            deepest[i] = np.fmax(deepest[i], float(d))
    length = np.where(np.isnan(td), deepest, np.fmax(td, np.where(np.isnan(deepest), td, deepest)))
    collars = Collars(np.arange(len(ids)), col("x"), col("y"), col("z"), length, col("azimuth"), col("dip"))
    srows = conn.execute(
        sa.select(dh_surveys.c.hole_id, dh_surveys.c.depth, dh_surveys.c.azimuth, dh_surveys.c.dip).where(
            dh_surveys.c.set_id == set_id
        )
    ).all()
    surveys = Surveys(
        np.array([index[int(r.hole_id)] for r in srows], dtype=np.int64),
        np.array([r.depth for r in srows], dtype=float),
        np.array([np.nan if r.azimuth is None else r.azimuth for r in srows], dtype=float),
        np.array([np.nan if r.dip is None else r.dip for r in srows], dtype=float),
    )
    traj = desurvey(collars, surveys, method=method, n_holes=len(ids))
    return SetTrajectories(key, ids, [h.name for h in holes], index, traj)


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
