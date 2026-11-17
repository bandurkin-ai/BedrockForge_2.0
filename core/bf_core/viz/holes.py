"""Hole manifest of a set for 3D: `GET /viz/sets/{id}/holes` (ADR 0007 §1).

The layout of a set — holes with a path in Morton order, cut into parts with their extents and origins — is
built from the cached trajectories (`drillholes.trajectory`) and cached by their key, so the manifest and the
paths and intervals of its parts agree. Holes without a path (no collar coordinates) are listed by name in
`skipped`. The audit colour of a hole is the worst severity among the findings of the last run of the set:
0 no audit, 1 no findings, 2 notes, 3 warnings, 4 errors — one GROUP BY over `audit_findings`.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import audit_findings, dh_holes

from . import binary
from .keys import FORMAT, SetState, etag
from .parts import Part, make_parts
from .parts import order as morton_order

KINDS = {"hole": 0, "trench": 1, "design": 2}
AUDIT_NONE, AUDIT_CLEAN, AUDIT_INFO, AUDIT_WARNING, AUDIT_ERROR = range(5)
VERTICAL_M = 0.1  # plan offset of the end below which a hole has no plan azimuth


@dataclass
class Layout:
    key: tuple[Any, ...]
    st: Any  # trajectory.SetTrajectories
    order: np.ndarray  # dense hole index per position of the manifest (parts one after another)
    parts: list[Part]
    part: np.ndarray  # uint16 per position
    slot: np.ndarray  # uint16 per position: index within the part
    kind: np.ndarray  # uint8 per position
    td: np.ndarray  # float64 per position (NaN — no TD)
    skipped: list[str]  # names of holes without a path

    @property
    def names(self) -> list[str]:
        names = self.st.names
        return [names[i] for i in self.order.tolist()]

    def holes_of(self, k: int) -> np.ndarray:
        p = self.parts[k]
        return self.order[p.start : p.stop]


_lock = threading.Lock()
_cache: OrderedDict[tuple[Any, ...], Layout] = OrderedDict()
CACHE_SIZE = 4


def _attrs(conn: sa.Connection, set_id: int, ids: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """TD and the kind (hole, trench, design — by «Тип»/«Статус», Э4) of holes by dh_holes.id."""
    from bf_core.drillholes.router import hole_type

    td = np.full(len(ids), np.nan)
    kind = np.zeros(len(ids), dtype=np.uint8)
    rows = conn.execute(
        sa.select(dh_holes.c.id, dh_holes.c.td, dh_holes.c.has_collar, dh_holes.c.attrs).where(
            dh_holes.c.set_id == set_id
        )
    ).all()
    if not rows or not len(ids):
        return td, kind
    rid = np.array([r.id for r in rows], dtype=np.int64)
    pos = np.searchsorted(ids, rid).clip(0, len(ids) - 1)
    for r, i, ok in zip(rows, pos.tolist(), (ids[pos] == rid).tolist(), strict=True):
        if not ok:
            continue  # added after the trajectories were built: the next key rebuilds
        if r.td is not None and r.has_collar:
            td[i] = float(r.td)
        if r.attrs:
            kind[i] = KINDS[hole_type(r.attrs)["key"]]
    return td, kind


def _build(project: Any, set_id: int, st: Any) -> Layout:
    from .arcs import hole_bounds

    traj = st.traj
    built = np.flatnonzero(traj.offsets[1:] > traj.offsets[:-1])
    collar = traj.xyz[traj.offsets[built]]
    seq = built[morton_order(collar[:, 0], collar[:, 1], st.hole_ids[built])]
    parts = make_parts(hole_bounds(traj, seq))
    part = np.zeros(len(seq), dtype=np.uint16)
    slot = np.zeros(len(seq), dtype=np.uint16)
    for p in parts:
        part[p.start : p.stop] = p.k
        slot[p.start : p.stop] = np.arange(p.n, dtype=np.uint16)
    with dialect.read_tx(project.engine) as conn:
        td, kind = _attrs(conn, set_id, st.hole_ids)
    shown = np.zeros(len(st.hole_ids), dtype=bool)
    shown[seq] = True
    skipped = [st.names[i] for i in np.flatnonzero(~shown).tolist()]
    return Layout(st.key, st, seq, parts, part, slot, kind[seq], td[seq], skipped)


def layout(project: Any, set_id: int, method: str) -> Layout:
    from bf_core.drillholes.trajectory import trajectories

    st = trajectories(project, set_id, method)
    with _lock:
        hit = _cache.get(st.key)
        if hit is not None and hit.st is st:
            _cache.move_to_end(st.key)
            return hit
    built = _build(project, set_id, st)
    with _lock:
        for k in [k for k in _cache if k[:2] == st.key[:2] and k[-1] == st.key[-1]]:  # older versions
            del _cache[k]
        _cache[st.key] = built
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return built


def forget() -> None:
    with _lock:
        _cache.clear()


def audit_levels(conn: sa.Connection, set_id: int, names: list[str]) -> tuple[np.ndarray, dict[str, Any] | None]:
    """`audit u1` per hole name and the header field {run_id, stale, at} (None — the set has no audit)."""
    from bf_core.audit.service import latest

    run = latest(conn, set_id)
    if run is None:
        return np.full(len(names), AUDIT_NONE, dtype=np.uint8), None
    worst = sa.func.min(
        sa.case((audit_findings.c.severity == "error", 0), (audit_findings.c.severity == "warning", 1), else_=2)
    )
    by_hole = {
        h: AUDIT_ERROR - int(w)
        for h, w in conn.execute(
            sa.select(audit_findings.c.hole, worst)
            .where(audit_findings.c.run_id == run["run_id"], audit_findings.c.hole.is_not(None))
            .where(audit_findings.c.count > 0)
            .group_by(audit_findings.c.hole)
        )
    }
    levels = np.array([by_hole.get(n, AUDIT_CLEAN) for n in names], dtype=np.uint8)
    return levels, {"run_id": run["run_id"], "stale": run["stale"], "at": run["created_at"]}


def audit_stamp(conn: sa.Connection, set_id: int) -> list[Any] | None:
    """The last audit run for the ETag: its id and time (SQLite gives a replaced run the freed id again)."""
    from bf_core.db.schema import audit_runs

    r = conn.execute(
        sa.select(audit_runs.c.id, audit_runs.c.created_at)
        .where(audit_runs.c.set_id == set_id)
        .order_by(audit_runs.c.id.desc())
        .limit(1)
    ).first()
    return None if r is None else [int(r.id), r.created_at.isoformat()]


def plan_azimuth(lay: Layout) -> np.ndarray:
    """Azimuth from the collar to the end of the path in plan, degrees clockwise from north; NaN for a hole
    whose end is less than 0.1 m from the collar in plan (vertical)."""
    traj = lay.st.traj
    a = traj.xyz[traj.offsets[lay.order]]
    b = traj.xyz[traj.offsets[lay.order + 1] - 1]
    dx = b[:, 0] - a[:, 0]
    dy = b[:, 1] - a[:, 1]
    az = np.degrees(np.arctan2(dx, dy)) % 360.0
    return np.where(np.hypot(dx, dy) < VERTICAL_M, np.nan, az)


def paths_etag(uid: str, state: SetState, method: str, tol: float, k: int) -> str:
    return etag("paths", uid, state, method, repr(float(tol)), k)


def manifest(project: Any, state: SetState, method: str) -> bytes:
    from .arcs import DEFAULT_TOL

    lay = layout(project, state.set_id, method)
    traj = lay.st.traj
    names = lay.names
    with dialect.read_tx(project.engine) as conn:
        audit, audit_head = audit_levels(conn, state.set_id, names)
    header = {
        "kind": "holes",
        "format": FORMAT,
        "set": {"id": state.set_id, "name": state.name},
        "version": state.version,
        "method": method,
        "n": len(lay.order),
        "parts": [
            {
                "k": p.k,
                "n": p.n,
                "bbox": list(p.bbox),
                "origin": list(p.origin),
                "etag": paths_etag(project.uid, state, method, DEFAULT_TOL, p.k),
            }
            for p in lay.parts
        ],
        "names": names,
        "skipped": lay.skipped,
        "audit": audit_head,
    }
    return binary.encode(
        header,
        [
            ("id", lay.st.hole_ids[lay.order], "<u4"),
            ("collar", traj.xyz[traj.offsets[lay.order]], "<f8"),
            ("td", lay.td, "<f8"),
            ("length", traj.lengths()[lay.order], "<f8"),
            ("plan_az", plan_azimuth(lay), "<f4"),
            ("kind", lay.kind, "<u1"),
            ("audit", audit, "<u1"),
            ("part", lay.part, "<u2"),
            ("slot", lay.slot, "<u2"),
        ],
    )
