"""Snap targets of a drillhole set for the string editor: `GET /viz/sets/{id}/snap` (ADR 0015 §4; ТЗ 7.22).

BFA1 `snap`, float64 project coordinates (the editor snaps to them exactly, nothing goes through float32):

* `collars <f8` n×3 — the first station of every hole chosen, bit for bit as the trajectory has it;
* `trace <f8` + `trace_offsets <u4` (n + 1) — the path of the hole as a polyline within 1 cm of its arc (the
  depths of `viz/arcs.py::densify`, as the display paths of ADR 0007), every point by the desurvey of the core;
* `bounds <f8` + `bound_hole <u4` (index into the holes chosen) + `bound_depth <f8` — the positions of «от» and
  «до» of every row of every interval table, sample table and event of the set (a depth once per hole), by the
  same desurvey (`Trajectories.positions`).

The holes are chosen by the window of a section (`section=x0,y0,az,towards,away[,length]` — the window of
`geo/section.py::section_window`) or by the extent of the view (`bbox=xmin,ymin,xmax,ymax[,zmin,zmax]` in the
order of an extent: `xmin,ymin,zmin,xmax,ymax,zmax`); the box of the path of a hole has to reach into it. The
bounds are kept inside the window only. More than 5 000 holes or 2 million points: `truncated`, collars only.
The header: `{kind, format, set, version, method, holes, names, truncated}`. ETag — the state of the set (its
version and every table revision), the method and the window.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError

from . import binary
from .keys import FORMAT, SetState, etag

MAX_HOLES = 5_000
MAX_POINTS = 2_000_000
TOL = 0.01  # metres from the arc: the display paths of ADR 0007
EPS = 1e-9  # a bound on a face of the window is inside it


@dataclass(frozen=True)
class Window:
    """The half-spaces n·p + c ≥ 0 a target has to be in (none: the whole set) and the key of the ETag."""

    planes: np.ndarray  # k×4
    key: tuple[Any, ...]


def _numbers(text: str, sizes: tuple[int, ...], what: str, hint: str) -> list[float]:
    try:
        values = [float(v) for v in text.replace(";", ",").split(",") if v.strip()]
    except ValueError:
        values = []
    if len(values) not in sizes or not all(math.isfinite(v) for v in values):
        raise ApiError(422, "bad_window", f"{what} задано неверно.", hint)
    return values


def parse_window(section: str | None, bbox: str | None) -> Window:
    from bf_core.geo.section import section_window

    if section and bbox:
        raise ApiError(422, "bad_window", "Задано и окно разреза, и охват вида.", "Укажите что-то одно.")
    if section:
        v = _numbers(section, (5, 6), "Окно разреза", "Укажите x0, y0, азимут, глубину окна к себе и от себя.")
        try:
            w = section_window(v[0], v[1], v[2], towards=v[3], away=v[4], length=v[5] if len(v) == 6 else None)
        except ValueError as exc:
            raise ApiError(
                422, "bad_window", "Окно разреза задано неверно.", "Проверьте глубину и длину окна."
            ) from exc
        return Window(w.planes(), ("section", *v))
    if bbox:
        v = _numbers(bbox, (4, 6), "Охват вида", "Укажите xmin, ymin, xmax, ymax или охват из шести чисел.")
        lo, hi = (v[:2], v[2:]) if len(v) == 4 else (v[:3], v[3:])
        if any(a > b for a, b in zip(lo, hi, strict=True)):
            raise ApiError(422, "bad_window", "Охват вида задан неверно.", "Минимум не может быть больше максимума.")
        planes = []
        for axis, (a, b) in enumerate(zip(lo, hi, strict=True)):
            n = np.zeros(3)
            n[axis] = 1.0
            planes += [[*n, -a], [*(-n), b]]
        return Window(np.array(planes, dtype=np.float64) + 0.0, ("bbox", *v))
    return Window(np.zeros((0, 4)), ("all",))


def snap_etag(uid: str, state: SetState, method: str, window: Window) -> str:
    return etag("snap", uid, state, method, TOL, list(window.key))


def _boxes_in(boxes: np.ndarray, planes: np.ndarray) -> np.ndarray:
    """Boxes (n×6: lo, hi) that reach every half-space: the corner farthest along each normal is inside."""
    ok = np.isfinite(boxes).all(axis=1)
    for nx, ny, nz, c in planes.tolist():
        far = c
        for axis, n in enumerate((nx, ny, nz)):
            if n:
                far = far + np.maximum(n * boxes[:, axis], n * boxes[:, axis + 3])
        ok &= np.asarray(far) >= -EPS
    return ok


def _inside(points: np.ndarray, planes: np.ndarray) -> np.ndarray:
    ok = np.ones(len(points), dtype=bool)
    for nx, ny, nz, c in planes.tolist():
        ok &= points @ np.array([nx, ny, nz]) + c >= -EPS
    return ok


def _depths(project: Any, set_id: int, hole_ids: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(position among `hole_ids`, depth) of every «от» and «до» of the ready tables, once per hole, sorted."""
    import sqlalchemy as sa

    from bf_core.db import dialect
    from bf_core.db.schema import dh_tables

    from .intervals import rows_of_holes, snapshot
    from .keys import TableRef, _stamp

    with dialect.read_tx(project.engine) as conn:
        tables = conn.execute(
            sa.select(dh_tables.c.id, dh_tables.c.name, dh_tables.c.kind, dh_tables.c.rev, dh_tables.c.created_at)
            .where(dh_tables.c.set_id == set_id, dh_tables.c.state == "ready")
            .order_by(dh_tables.c.id)
        ).all()
    holes: list[np.ndarray] = []
    depths: list[np.ndarray] = []
    for t in tables:
        snap = snapshot(project, set_id, TableRef(int(t.id), t.name, t.kind, int(t.rev), _stamp(t.created_at)))
        rows, pos = rows_of_holes(snap, hole_ids)
        holes.append(pos)
        depths.append(snap.data["from"][rows])
        if t.kind != "event":
            holes.append(pos)
            depths.append(snap.data["to"][rows])
    if not holes:
        return np.zeros(0, dtype=np.int64), np.zeros(0)
    h = np.concatenate(holes).astype(np.int64)
    d = np.concatenate(depths).astype(np.float64)
    keep = np.isfinite(d)
    h, d = h[keep], d[keep] + 0.0  # −0.0 and 0.0 are one depth
    order = np.lexsort((d, h))
    h, d = h[order], d[order]
    first = np.ones(len(h), dtype=bool)
    first[1:] = (h[1:] != h[:-1]) | (d[1:] != d[:-1])
    return h[first], d[first]


def build(project: Any, state: SetState, method: str, window: Window) -> bytes:
    from bf_core.drillholes.trajectory import trajectories

    from .arcs import hole_bounds

    st = trajectories(project, state.set_id, method)
    traj = st.traj
    built = np.flatnonzero(traj.offsets[1:] > traj.offsets[:-1])
    if len(window.planes) and len(built):
        built = built[_boxes_in(hole_bounds(traj, built, TOL), window.planes)]
    collars = traj.xyz[traj.offsets[built]]
    header: dict[str, Any] = {
        "kind": "snap", "format": FORMAT, "set": state.set_id, "version": state.version, "method": method,
        "holes": int(len(built)), "names": [st.names[i] for i in built.tolist()], "truncated": False,
    }  # fmt: skip
    arrays: list[tuple[str, Any, str]] = [("collars", collars, "<f8")]
    picked = _traces(project, state.set_id, st, built) if len(built) <= MAX_HOLES else None
    if picked is None:
        header["truncated"] = len(built) > 0
        return binary.encode(header, arrays)
    offsets, trace, h, d = picked
    bounds = traj.positions(built[h], d) if len(d) else np.zeros((0, 3))
    if len(window.planes):
        keep = _inside(bounds, window.planes)
        bounds, h, d = bounds[keep], h[keep], d[keep]
    arrays += [
        ("trace", trace, "<f8"), ("trace_offsets", offsets, "<u4"),
        ("bounds", bounds, "<f8"), ("bound_hole", h, "<u4"), ("bound_depth", d, "<f8"),
    ]  # fmt: skip
    return binary.encode(header, arrays)


def _traces(
    project: Any, set_id: int, st: Any, built: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
    """(trace offsets, trace points, bound hole, bound depth) of the holes; None — over `MAX_POINTS`."""
    from .arcs import densify

    traj = st.traj
    try:
        offsets, _shown, md = densify(traj, built, TOL, np.zeros(3), max_points=MAX_POINTS)
    except ValueError:
        return None
    trace = traj.positions(np.repeat(built, np.diff(offsets)), md) if len(md) else np.zeros((0, 3))
    h, d = _depths(project, set_id, st.hole_ids[built])
    if len(trace) + len(d) > MAX_POINTS:
        return None
    return offsets, trace, h, d
