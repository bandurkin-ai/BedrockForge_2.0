"""The exact geometry of a line layer and the bodies of the editor in BFA1 (ADR 0015 §2; ADR 0007 §1).

`GET /lines/layers/{id}/geometry` — BFA1 `linegeom`: the header `{kind, layer_id, version, lines, points,
points_only, ties}` and the arrays `xyz <f8` (the storage form: a closed line repeats its first vertex), `offsets
<u4` (lines + 1), `closed u1`, `lid <u4`. A layer of points is given as lines of one vertex. `ties` — the
positions of the lines marked `ROLE = TIE`. `?lines=` — only the lines with these `lid` (in that order).

`PUT /lines/layers/{id}/geometry` — the header `{base_version, note, new_attrs}` (`new_attrs` {"<line>": {field:
value}}) and the arrays `src <i4`, `touched u1`, `count <u4`, `closed u1` — one entry per line of the new layer —
and `xyz <f8`, the vertices of touched and new lines only (`lines/assemble.py`).

`POST /lines/layers/{id}/move-lines` — the same body for the source without the lines moved, its header with
`target_id`, `target_base`, plus the moved lines as `moved_src`, `moved_touched`, `moved_count`, `moved_closed`,
`moved_xyz` (their geometry as the editor holds it).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.formats.lineset import LineSet
from bf_core.viz import binary

from .assemble import Edit, as_lines

KIND = "linegeom"
MAX_NOTE = 255
TIE = "TIE"
ROLE_FIELD = "ROLE"


def bad_body(message: str) -> ApiError:
    return ApiError(
        422,
        "bad_body",
        message,
        "Тело запроса записывает редактор стрингов; если ошибка повторяется — отправьте журнал разработчику.",
    )


def tie_lines(ls: LineSet) -> list[int]:
    """Positions of the lines whose attribute `ROLE` is `TIE` (the tool `tie`, ADR 0015 §7)."""
    if ls.attrs is None or ROLE_FIELD not in ls.attrs.column_names:
        return []
    values = ls.attrs.column(ROLE_FIELD).to_pylist()
    return [i for i, v in enumerate(values) if isinstance(v, str) and v.strip().upper() == TIE]


def parse_lines(text: str | None) -> list[int] | None:
    if text is None or not text.strip():
        return None
    try:
        out = [int(part) for part in text.replace(";", ",").split(",") if part.strip()]
    except ValueError as exc:
        raise ApiError(
            422, "bad_lines", "Номера линий заданы неверно.", "Перечислите номера линий через запятую."
        ) from exc
    if any(v < 0 for v in out):
        raise ApiError(422, "bad_lines", "Номер линии не может быть отрицательным.", "Обновите слой.")
    return out


def encode_geometry(layer_id: int, version: int, ls: LineSet, lid: np.ndarray, lines: Sequence[int] | None) -> bytes:
    offsets, closed = as_lines(ls)
    xyz = ls.xyz
    ties = tie_lines(ls)
    if lines is not None:
        pos = {int(v): i for i, v in enumerate(lid.tolist())}
        missing = [v for v in lines if v not in pos]
        if missing:
            raise ApiError(
                404, "line_not_found", f"Линии {missing[0]} в слое нет.", "Слой изменился — перечитайте его."
            )
        idx = np.array([pos[v] for v in lines], dtype=np.int64)
        counts = np.diff(offsets)[idx]
        take = np.concatenate([np.arange(offsets[i], offsets[i + 1]) for i in idx]) if len(idx) else np.zeros(0, int)
        xyz = xyz[take]
        offsets = np.concatenate([[0], np.cumsum(counts)])
        closed = closed[idx]
        lid = lid[idx]
        chosen = {int(i): k for k, i in enumerate(idx.tolist())}
        ties = [chosen[t] for t in ties if t in chosen]
    if len(xyz) >= 2**32:
        raise ApiError(422, "layer_too_big", "Слой слишком велик для редактора.", "Разделите слой на части.")
    header = {
        "kind": KIND, "format": binary.FORMAT, "layer_id": int(layer_id), "version": int(version),
        "lines": int(len(closed)), "points": int(len(xyz)), "points_only": bool(ls.points_only), "ties": ties,
    }  # fmt: skip
    return binary.encode(
        header,
        [("xyz", xyz, "<f8"), ("offsets", offsets, "<u4"), ("closed", closed, "<u1"), ("lid", lid, "<u4")],
    )


def _array(arrays: Mapping[str, np.ndarray], name: str, dtype: str) -> np.ndarray:
    arr = arrays.get(name)
    if arr is None:
        raise bad_body(f"В теле запроса нет массива «{name}».")
    if arr.dtype != np.dtype(dtype):
        raise bad_body(f"Массив «{name}» должен быть {dtype}.")
    return np.array(arr)  # a writable copy, off the request buffer


def _edit(header: Mapping[str, Any], arrays: Mapping[str, np.ndarray], prefix: str = "") -> Edit:
    xyz = _array(arrays, prefix + "xyz", "<f8")
    if len(xyz) % 3:
        raise bad_body("Число координат не кратно трём.")
    raw = header.get("new_attrs") if not prefix else header.get("moved_attrs")
    new_attrs: dict[int, dict[str, Any]] = {}
    if raw is not None:
        if not isinstance(raw, Mapping):
            raise bad_body("Атрибуты новых линий заданы неверно.")
        for key, values in raw.items():
            if not isinstance(values, Mapping):
                raise bad_body("Атрибуты новых линий заданы неверно.")
            try:
                new_attrs[int(key)] = dict(values)
            except ValueError as exc:
                raise bad_body("Атрибуты новых линий заданы неверно.") from exc
    return Edit(
        _array(arrays, prefix + "src", "<i4"),
        _array(arrays, prefix + "touched", "<u1"),
        _array(arrays, prefix + "closed", "<u1"),
        _array(arrays, prefix + "count", "<u4"),
        xyz.reshape(-1, 3),
        new_attrs,
    )


def _decode(data: bytes) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    try:
        return binary.decode(data)
    except (binary.FormatError, ValueError, KeyError, TypeError) as exc:
        raise bad_body("Тело запроса — не BFA1 или повреждено.") from exc


def _int(header: Mapping[str, Any], name: str) -> int:
    v = header.get(name)
    if not isinstance(v, int) or isinstance(v, bool) or v < 1:
        raise bad_body(f"В заголовке нет поля «{name}».")
    return v


def decode_save(data: bytes) -> tuple[int, str, Edit]:
    """(base_version, note, edit) of a PUT of the geometry."""
    header, arrays = _decode(data)
    note = str(header.get("note") or "")[:MAX_NOTE]
    return _int(header, "base_version"), note, _edit(header, arrays)


def decode_move(data: bytes) -> tuple[int, int, int, Edit, Edit]:
    """(base_version, target_id, target_base, source edit, moved lines) of a move into another layer."""
    header, arrays = _decode(data)
    return (
        _int(header, "base_version"),
        _int(header, "target_id"),
        _int(header, "target_base"),
        _edit(header, arrays),
        _edit(header, arrays, "moved_"),
    )


def encode_save(base_version: int, edit: Edit, note: str = "") -> bytes:
    """The body the editor sends (tools and tests)."""
    header = {"base_version": base_version, "note": note, "new_attrs": {str(k): v for k, v in edit.new_attrs.items()}}
    return binary.encode(header, _arrays(edit))


def _arrays(edit: Edit, prefix: str = "") -> list[tuple[str, Any, str]]:
    return [
        (prefix + "src", edit.src, "<i4"), (prefix + "touched", edit.touched, "<u1"),
        (prefix + "count", edit.count, "<u4"), (prefix + "closed", edit.closed, "<u1"),
        (prefix + "xyz", edit.xyz, "<f8"),
    ]  # fmt: skip


def encode_move(base_version: int, target_id: int, target_base: int, source: Edit, moved: Edit) -> bytes:
    header = {
        "base_version": base_version, "target_id": target_id, "target_base": target_base,
        "new_attrs": {str(k): v for k, v in source.new_attrs.items()},
        "moved_attrs": {str(k): v for k, v in moved.new_attrs.items()},
    }  # fmt: skip
    return binary.encode(header, [*_arrays(source), *_arrays(moved, "moved_")])
