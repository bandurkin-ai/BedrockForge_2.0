"""Saving a line layer from the string editor, with a journal of versions.

The store is immutable (files by SHA-256), so a save writes new arrays and moves the layer's
references to them; the previous arrays stay on disk while a row of `line_layer_version`
refers to them. Returning to a version writes one more version with the old arrays.
"""

from __future__ import annotations

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.lines.measure import layer_measures
from app.models import LineLayer, LineLayerVersion
from app.storage import geometry

EXACT_ROLES = ("vertices", "offsets", "closed")
MAX_ATTRIBUTES = 200


class LineEditError(ValueError):
    """The geometry sent by the editor cannot be saved; the message is shown to the user."""


class VersionConflict(Exception):
    """The layer was saved by someone else since the editor read it."""

    def __init__(self, current: int, base: int):
        super().__init__(f"Слой уже сохранён в другой вкладке (версия {current}, правка начата с версии {base}). "
                         "Перечитайте слой, чтобы не затереть те изменения, или сохраните правку как новый слой")
        self.current, self.base = current, base


def clean_attributes(attributes: list | None, lines: int) -> list[dict]:
    out: list[dict] = []
    for i in range(lines):
        row = attributes[i] if attributes and i < len(attributes) and isinstance(attributes[i], dict) else {}
        clean = {}
        for k, v in list(row.items())[:MAX_ATTRIBUTES]:
            key = str(k).strip()[:100]
            if not key:
                continue
            if isinstance(v, bool) or v is None or isinstance(v, int):
                clean[key] = v
            elif isinstance(v, float):
                clean[key] = v if np.isfinite(v) else None
            else:
                clean[key] = str(v)[:2000]
        out.append(clean)
    return out


def validate(vertices: np.ndarray, offsets: np.ndarray, closed: np.ndarray) -> None:
    n = len(vertices)
    lines = len(offsets) - 1
    if lines < 0 or len(closed) != lines:
        raise LineEditError("Неверная структура слоя: число линий и признаков замкнутости не совпадает")
    if offsets[0] != 0 or offsets[-1] != n or np.any(np.diff(offsets) < 1):
        raise LineEditError("Неверная структура слоя: начала линий не соответствуют числу вершин (пустых линий быть не должно)")
    if n and not np.isfinite(vertices).all():
        raise LineEditError("В координатах есть нечисловые значения")
    # Storage convention (as the file readers give it): a closed line repeats its first vertex at the end.
    for i in np.flatnonzero(closed):
        a, b = int(offsets[i]), int(offsets[i + 1])
        if b - a < 4 or not np.array_equal(vertices[a], vertices[b - 1]):
            raise LineEditError(f"Замкнутая линия {int(i) + 1} должна иметь не меньше трёх вершин и повторять первую вершину в конце")


def record_version(session: Session, layer: LineLayer, action: str, *, note: str = "", author: str | None = None,
                   prev: tuple[int, int] = (0, 0)) -> LineLayerVersion:
    """The journal row of the layer's present state."""
    session.flush()
    row = LineLayerVersion(
        layer_id=layer.id, version=layer.version, action=action, note=note[:255], author=(author or None),
        line_count=layer.line_count, point_count=layer.point_count, prev_line_count=prev[0], prev_point_count=prev[1],
        blobs={k: layer.blobs[k] for k in EXACT_ROLES}, line_attributes=layer.line_attributes, stats=layer.stats,
    )
    session.add(row)
    return row


def locked_layer(session: Session, layer_id: int) -> LineLayer | None:
    """The layer row locked until the end of the transaction: two saves cannot interleave."""
    return session.scalars(select(LineLayer).where(LineLayer.id == layer_id).with_for_update()).first()


def save_layer(session: Session, layer: LineLayer, vertices: np.ndarray, offsets: np.ndarray, closed: np.ndarray,
               attributes: list | None, *, base_version: int | None, action: str = "save", note: str = "",
               author: str | None = None) -> LineLayer:
    """Writes the new state of the layer as its next version. `base_version` is the version the
    editor started from: when the layer has moved on meanwhile, nothing is written (VersionConflict)."""
    if base_version is not None and base_version != layer.version:
        raise VersionConflict(layer.version, base_version)
    v = np.ascontiguousarray(vertices, dtype="<f8").reshape(-1, 3)
    o = np.ascontiguousarray(offsets, dtype="<i8")
    c = np.ascontiguousarray(closed, dtype="u1")
    validate(v, o, c)
    # A layer stored before versions existed and never saved since: keep its state as a version first.
    if not session.scalar(select(LineLayerVersion.id).where(LineLayerVersion.layer_id == layer.id).limit(1)):
        record_version(session, layer, "import")
    prev = (layer.line_count, layer.point_count)
    stored = geometry.store_lines(session, v, o, c)
    layer.blobs = stored.blobs
    layer.origin = stored.origin
    layer.extent = stored.extent
    layer.line_count = len(c)
    layer.point_count = len(v)
    layer.line_attributes = clean_attributes(attributes, len(c))
    layer.stats = layer_measures(v, o, c)
    layer.version = layer.version + 1
    record_version(session, layer, action, note=note, author=author, prev=prev)
    return layer


def restore_version(session: Session, layer: LineLayer, version: int, *, author: str | None = None) -> LineLayer:
    """Makes an old version the current one as a new journal row; nothing is erased."""
    row = session.scalars(select(LineLayerVersion).where(LineLayerVersion.layer_id == layer.id,
                                                         LineLayerVersion.version == version)).first()
    if row is None:
        raise LineEditError(f"Версии {version} у слоя нет")
    v, o, c = geometry.line_arrays(row.blobs)
    return save_layer(session, layer, v, o, c, row.line_attributes, base_version=None, action="restore",
                      note=f"Возврат к версии {version}", author=author)
