"""Parts of a point cloud for 3D (ADR 0014 §2), at the addresses and in the BFA1 kinds of a line layer
(`viz/lines.py`): the 3D window asks for a cloud as for any layer of points.

`GET /viz/lines/{id}` of a cloud — the manifest (BFA1 `line_layer`, header only) from `meta.cloud`: the parts as
they are stored `[{k, n, points, levels [n0 … n4], bbox, origin, etags[5]}]`, the five levels `lods` (`tol` —
the cell of a level, m), and `cloud` {classes {code: points}, modes [{mode, key}], value {step, min, max,
empty}, source, ground, dz}. `GET /viz/lines/{id}/parts/{k}?lod=0…4&color=class|h|dz` — BFA1 `lines` of a layer
of points: `xyz f4×3` from the origin of the part, `line u4` — the number of the point in the cloud (the row of
its attributes), `value u1` — the class code (`color=class`, the default) or the value of the mode in steps:
`h` (height above the ground) and `dz` (Z − DTM) as `round((v + 2.5) / 0.02)` cut to 0…250, 255 — no value.

Nothing is laid out and nothing is kept in memory: level `lod` of a part is the first `n[lod]` of its rows in
the blobs (`lines/cloud_build.py`), so an answer is a slice of two files — the memory of the API process is one
part. Data for display only; calculations use float64 in the core.

ETags. A part: the SHA-256 of the key (project, layer, its creation time, the format, the blob of the points,
the blob of the mode, part, level, mode) — a new name or colour of the layer does not make the window load its
points again, new classes change the ETags of `class` and nothing else. The manifest: the key of a line layer
(with `objects.version`). A repeated request is answered 304 before anything is read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db.schema import objects
from bf_core.lines import cloud as lc

from . import binary
from .keys import FORMAT, etag
from .lines import LAYER, LayerState, part_not_found

MODES = ("class", "h", "dz")
#: `value` of the modes `h` and `dz`: code = round((v − VALUE_MIN) / VALUE_STEP), 0 … VALUE_TOP; EMPTY — none.
VALUE_STEP = 0.02
VALUE_MIN, VALUE_MAX = -2.5, 2.5
VALUE_TOP = 250
EMPTY = 255
_NO_MODE = {
    "h": ("Высот над землёй у облака ещё нет.", "Запустите «Фильтр земли» и выберите режим снова."),
    "dz": ("Отклонений от ЦТМ у облака ещё нет.", "Постройте ЦТМ из облака и выберите режим снова."),
}


def bad_lod() -> ApiError:
    return ApiError(
        422,
        "bad_lod",
        "Неизвестный уровень детализации облака.",
        f"Укажите уровень от 0 (все точки) до {len(lc.LEVELS) - 1} (самый грубый).",
    )


def bad_mode() -> ApiError:
    return ApiError(
        422,
        "bad_color_mode",
        "Неизвестный режим цвета облака.",
        "Укажите class (класс), h (высота над землёй) или dz (отклонение от ЦТМ).",
    )


def no_mode(mode: str) -> ApiError:
    message, hint = _NO_MODE[mode]
    return ApiError(409, "color_mode_unavailable", message, hint)


@dataclass(frozen=True)
class CloudView:
    """What `meta.cloud` says about the parts of a cloud."""

    parts: tuple[lc.CloudPart, ...]
    levels: tuple[float, ...]
    bbox: list[float]
    meta: dict[str, Any]


def is_cloud(state: LayerState) -> bool:
    """A layer of points with the column of classes: no other layer has one. Decided by the blobs the state
    already holds — a layer that is not a cloud costs no query."""
    return state.points_only and state.sha("class") is not None and state.sha("xyz") is not None


def view(conn: sa.Connection, state: LayerState) -> CloudView | None:
    """The parts of the cloud of a layer (None — the layer is not a cloud)."""
    if not is_cloud(state):
        return None
    meta = lc.cloud_meta(conn.execute(sa.select(objects.c.meta).where(objects.c.id == state.layer_id)).scalar())
    if meta is None:
        return None
    try:
        if int(meta["format"]) != lc.FORMAT:
            raise lc.broken()
        parts = tuple(lc.CloudPart.from_dict(p) for p in meta["parts"])
        levels = tuple(float(v) for v in meta["levels"])
        bbox = [float(v) for v in meta["bbox"]]
        if sum(p.n[0] for p in parts) != state.point_count or any(len(p.n) != len(levels) for p in parts):
            raise lc.broken()
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise lc.broken() from exc
    return CloudView(parts, levels, bbox, meta)


def modes(state: LayerState) -> list[str]:
    return [mode for mode in MODES if state.sha(mode) is not None]


def check(state: LayerState, cloud: CloudView, k: int, lod: int, color: str | None) -> str:
    """The colour mode of a request for part k at a level; what is asked for is there."""
    mode = "class" if color is None else color
    if mode not in MODES:
        raise bad_mode()
    if lod not in range(len(cloud.levels)):
        raise bad_lod()
    if not 0 <= k < len(cloud.parts):
        raise part_not_found(k)
    if state.sha(mode) is None:
        raise no_mode(mode)
    return mode


# --- ETags -------------------------------------------------------------------------------------------------


class _Points:
    """The key of the points of a cloud: the layer and the blob of its coordinates, not its version."""

    def __init__(self, state: LayerState) -> None:
        self.state = state

    def key(self) -> list[Any]:
        s = self.state
        return [s.layer_id, s.stamp, s.sha("xyz"), lc.FORMAT, lc.PART_POINTS]


def layer_etag(uid: str, state: LayerState) -> str:
    return etag("cloud", uid, state, lc.FORMAT, list(lc.LEVELS), VALUE_STEP, VALUE_MIN, VALUE_MAX)


def part_etag(uid: str, state: LayerState, k: int, lod: int, mode: str) -> str:
    return etag("cloud_part", uid, _Points(state), k, lod, mode, state.sha(mode), VALUE_STEP, VALUE_MIN, VALUE_MAX)


# --- answers -----------------------------------------------------------------------------------------------


def quantize(values: np.ndarray) -> np.ndarray:
    """`value` of a mode with numbers (`h`, `dz`): steps of 0.02 m from −2.5 m, cut to ±2.5 m; 255 — no value."""
    v = np.asarray(values, dtype=np.float64)
    with np.errstate(invalid="ignore"):
        code = np.clip(np.rint((v - VALUE_MIN) / VALUE_STEP), 0, VALUE_TOP)
    return np.where(np.isnan(v), EMPTY, code).astype(np.uint8)


def _slice(project: Any, state: LayerState, role: str, start: int, count: int) -> np.ndarray:
    """Rows of a column of the cloud, read from its blob."""
    from bf_core.storage.blobs import BlobStore

    sha = state.sha(role)
    if sha is None:
        raise lc.broken()
    dtype = np.dtype(lc.ROLES[role])
    width = 3 if role == "xyz" else 1
    try:
        path = BlobStore(project.folder).path_of(sha)
        arr = np.fromfile(path, dtype=dtype, count=count * width, offset=start * width * dtype.itemsize)
    except OSError as exc:
        raise lc.broken() from exc
    if len(arr) != count * width:
        raise lc.broken()
    return arr


def manifest(project: Any, state: LayerState, cloud: CloudView) -> bytes:
    meta = cloud.meta
    lods = range(len(cloud.levels))
    header = {
        "kind": LAYER,
        "format": FORMAT,
        "layer": {"id": state.layer_id, "name": state.name, "file_id": state.file_id},
        "version": state.version,
        "points_only": True,
        "color": state.color,
        "lines": 0,
        "points": state.point_count,
        "pieces": state.point_count,
        "bbox": cloud.bbox,
        "lods": [{"lod": j, "tol": tol} for j, tol in enumerate(cloud.levels)],
        "max_vertices": lc.PART_POINTS,
        "labels": False,
        "cloud": {
            "classes": meta.get("classes") or {},
            "modes": [{"mode": mode, "key": str(state.sha(mode))[:16]} for mode in modes(state)],
            "value": {"step": VALUE_STEP, "min": VALUE_MIN, "max": VALUE_MAX, "empty": EMPTY},
            "source": {key: value for key, value in (meta.get("source") or {}).items() if key in ("kind", "every")},
            "ground": meta.get("ground"),
            "dz": meta.get("dz"),
        },
        "parts": [
            {
                "k": k,
                "n": p.n[0],
                "points": p.n[0],
                "levels": list(p.n),
                "bbox": list(p.bbox),
                "origin": list(p.origin),
                "etags": [part_etag(project.uid, state, k, lod, "class") for lod in lods],
            }
            for k, p in enumerate(cloud.parts)
        ],
    }
    return binary.encode(header, [])


def part(project: Any, state: LayerState, cloud: CloudView, k: int, lod: int, mode: str) -> bytes:
    """Part k at a level with the values of a colour mode (see the module docstring)."""
    p = cloud.parts[k]
    n = p.n[lod]
    pts = _slice(project, state, "xyz", p.start, n).reshape(-1, 3)
    pts -= np.array(p.origin)
    xyz = pts.astype("<f4")
    del pts
    values = _slice(project, state, mode, p.start, n)
    header: dict[str, Any] = {
        "kind": "lines",
        "format": FORMAT,
        "layer": state.layer_id,
        "k": k,
        "lod": lod,
        "tol": cloud.levels[lod],
        "points_only": True,
        "cloud": True,
        "mode": mode,
        "origin": list(p.origin),
        "bbox": list(p.bbox),
        "source_points": p.n[0],
        "n": n,
        "points": n,
        "start": p.start,
        "simplified": n < p.n[0],
        "labels": None,
    }
    arrays = [
        ("xyz", xyz, "<f4"),
        ("line", np.arange(p.start, p.start + n, dtype="<u4"), "<u4"),
        ("value", values if mode == "class" else quantize(values), "<u1"),
    ]
    return binary.encode(header, arrays)
