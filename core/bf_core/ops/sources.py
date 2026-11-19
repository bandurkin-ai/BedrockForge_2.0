"""The sources of an operation (ADR 0015 §6): wireframes and lines of layers, picked by their stable numbers.

`Sources` is used twice: by the endpoint before a job is queued (rows only: the objects are there and ready, of
the right kind, the lines picked exist — `lid` of the current version; ADR 0015 §1) and by the job, inside one
read transaction, which then `load()`s the arrays. Every source asked for is recorded in `used` — the
`sources` of `meta.built`: {role, kind, id, name, version, …}; `role` names the parameter («a», «contours[1]»).
A wireframe's version is `objects.version`, a layer's — the number of its version in the journal (`rev`).

A source that is not there is a 422 `source_missing` naming it: by its name in the record of the result being
rebuilt (`known`, by role), else by its number. Lines of a layer: `lines` — `lid`s (null — every line); a
closed line comes without its repeated last vertex. A vertex without a height stays NaN: the runs refuse it,
never take it for 0.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db.schema import line_layers, objects, wireframes
from bf_core.lines import store as ls_store
from bf_core.storage.blobs import BlobStore

WIREFRAME = "wireframe"
LAYER = "line_layer"
KIND_NAMES = {
    WIREFRAME: "каркас",
    LAYER: "слой линий",
    "line_file": "файл линий",
    "drillhole_set": "набор скважин",
    "raster": "растр",
}
REFRESH = "Обновите дерево проекта и выберите объекты заново."


@dataclass
class Wf:
    id: int
    name: str
    version: int
    folder: str | None
    stats: dict[str, Any]
    triangles: int
    points: int
    color: str | None
    attrs: Any
    extent: list[float] | None
    dtm: bool


@dataclass
class Layer:
    id: int
    name: str
    rev: int
    folder: str | None
    line_count: int
    point_count: int
    color: str | None
    picked: np.ndarray  # positions of the picked lines in the current version, in the order asked
    lids: np.ndarray  # their stable numbers
    closed: np.ndarray  # u1 per picked line


@dataclass
class Line:
    lid: int
    layer: str
    points: np.ndarray  # k×3 float64; a closed line without its repeated last vertex
    closed: bool
    attrs: dict[str, Any] = field(default_factory=dict)

    @property
    def where(self) -> str:
        return f"линия {self.lid + 1} слоя «{self.layer}»"


def _missing(what: str, value: Any, known: str | None, role: str) -> ApiError:
    if known:
        message = f"Исходный объект «{known}» ({what}) удалён из проекта."
    elif value is None:
        message = f"Не выбран {what}."
    else:
        message = f"{what[:1].upper()}{what[1:]} № {value} не найден: возможно, он удалён."
    return ApiError(422, "source_missing", message, REFRESH, {"role": role})


class Sources:
    """The sources of one run; `known` — names of the sources of the record being rebuilt, by role."""

    def __init__(self, conn: sa.Connection, store: BlobStore, known: Mapping[str, str] | None = None) -> None:
        self.conn, self.store = conn, store
        self.known = dict(known or {})
        self.used: list[dict[str, Any]] = []
        self.folder: str | None = None
        self.first = False  # the folder of the first source is taken
        self.wfs: dict[int, Wf] = {}
        self.layers: dict[str, Layer] = {}  # by role: one layer may be named twice with other lines
        self._meshes: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        self._lines: dict[str, list[Line]] = {}

    def _place(self, folder: str | None) -> None:
        if not self.first:
            self.first, self.folder = True, folder

    def _row(self, value: Any, role: str, kind: str, what: str) -> sa.Row[Any]:
        from .params import object_id

        known = self.known.get(role)
        oid = object_id(value, what)
        row = None
        if oid is not None:
            row = self.conn.execute(
                sa.select(objects.c.id, objects.c.kind, objects.c.name, objects.c.state, objects.c.version,
                          objects.c.folder, objects.c.parent_id, objects.c.meta, objects.c.extent)
                .where(objects.c.id == oid)
            ).first()  # fmt: skip
        if row is None:
            raise _missing(what, value, known, role)
        if row.kind != kind:
            other = KIND_NAMES.get(str(row.kind), "другой объект")
            raise ApiError(
                422, "wrong_kind", f"«{row.name}» — {other}, а нужен {what}.", f"Выберите {what}.", {"role": role}
            )
        if row.state != "ready":
            raise ApiError(
                422, "source_not_ready", f"«{row.name}» ещё строится.", "Дождитесь окончания задания и повторите.",
                {"role": role},
            )  # fmt: skip
        return row

    # --- wireframes ---------------------------------------------------------------------------------------

    def wireframe(self, value: Any, role: str, what: str = "каркас") -> Wf:
        row = self._row(value, role, WIREFRAME, what)
        wf = self.wfs.get(int(row.id))
        if wf is None:
            w = self.conn.execute(sa.select(wireframes).where(wireframes.c.object_id == row.id)).mappings().one()
            meta = row.meta or {}
            wf = Wf(
                int(row.id), str(row.name), int(row.version), row.folder, dict(w["stats"] or {}),
                int(w["triangle_count"]), int(w["point_count"]), meta.get("color"), w["attrs"], row.extent,
                w["dtm"] is not None,
            )  # fmt: skip
            self.wfs[wf.id] = wf
        self._place(wf.folder)
        self.used.append(
            {"role": role, "kind": WIREFRAME, "id": wf.id, "name": wf.name, "version": wf.version,
             "triangles": wf.triangles}
        )  # fmt: skip
        return wf

    def mesh(self, wf: Wf) -> tuple[np.ndarray, np.ndarray]:
        return self._meshes[wf.id]

    # --- lines --------------------------------------------------------------------------------------------

    def layer(self, value: Any, role: str, lines: Sequence[int] | None = None, what: str = "слой линий") -> Layer:
        """A layer of lines and the lines picked in it (`lid`s; None — every line)."""
        from bf_core.lines.cloud import cloud_meta
        from bf_core.lines.versions import lids_of

        row = self._row(value, role, LAYER, what)
        if cloud_meta(row.meta) is not None:
            raise ApiError(
                422, "not_lines", f"«{row.name}» — облако точек, а нужен слой линий.", f"Выберите {what}.",
                {"role": role},
            )  # fmt: skip
        ll = self.conn.execute(sa.select(line_layers).where(line_layers.c.object_id == row.id)).mappings().one()
        folder = self.conn.execute(sa.select(objects.c.folder).where(objects.c.id == row.parent_id)).scalar()
        count = 0 if ll["points_only"] else int(ll["line_count"])
        shas = ls_store.layer_blobs(self.conn, int(row.id))
        files = ls_store.layer_files(self.store, shas)
        lids = lids_of(self.store, shas, count) if count else np.zeros(0, dtype=np.uint32)
        closed = files.array("closed") if count else None
        closed = np.zeros(count, dtype=np.uint8) if closed is None else np.asarray(closed, dtype=np.uint8)
        if lines is None:
            picked = np.arange(count, dtype=np.int64)
        else:
            where = {int(v): k for k, v in enumerate(lids.tolist())}
            gone = [x for x in lines if x not in where]
            if gone:
                raise ApiError(
                    422, "line_missing",
                    f"В слое «{row.name}» нет линии {gone[0] + 1}: она удалена или слой изменён"
                    + (f" (не найдено линий: {len(gone)})." if len(gone) > 1 else "."),
                    "Выберите линии заново.", {"role": role, "layer_id": int(row.id), "lines": gone[:100]},
                )  # fmt: skip
            picked = np.array([where[x] for x in lines], dtype=np.int64)
        layer = Layer(
            int(row.id), str(row.name), int(ll["rev"] or 1), folder, count, int(ll["point_count"]), ll["color"],
            picked, lids[picked].astype(np.int64), closed[picked],
        )  # fmt: skip
        self.layers[role] = layer
        self._place(folder)
        self.used.append(
            {"role": role, "kind": LAYER, "id": layer.id, "name": layer.name, "version": layer.rev,
             "lines": None if lines is None else [int(x) for x in lines]}
        )  # fmt: skip
        return layer

    def lines(self, role: str) -> list[Line]:
        return self._lines[role]

    # --- reading the arrays (in the job, inside its read transaction) --------------------------------------

    def load(self, tick: Callable[[float], None] | None = None) -> None:
        from bf_core.lines.versions import read_current
        from bf_core.wireframes import store as wf_store

        total = max(1, len(self.wfs) + len(self.layers))
        done = 0
        for wf in self.wfs.values():
            if tick:
                tick(done / total)
            self._meshes[wf.id] = wf_store.read_wireframe(self.conn, self.store, wf.id)
            done += 1
        for role, layer in self.layers.items():
            if tick:
                tick(done / total)
            _head, ls, _lids = read_current(self.conn, self.store, layer.id)
            self._lines[role] = _lines_of(ls, layer)
            done += 1


def _column(table: Any, name: str) -> list[Any] | None:
    if table is None:
        return None
    for col in table.column_names:
        if str(col).upper() == name:
            return list(table.column(col).to_pylist())
    return None


def _lines_of(ls: Any, layer: Layer) -> list[Line]:
    offsets = ls.offsets
    closed = ls.closed
    role = _column(ls.attrs, "ROLE")
    named = _column(ls.attrs, "NAME")
    out: list[Line] = []
    for pos, lid in zip(layer.picked.tolist(), layer.lids.tolist(), strict=True):
        p = np.array(ls.xyz[int(offsets[pos]) : int(offsets[pos + 1])], dtype=np.float64)
        shut = bool(closed[pos])
        if shut and len(p) > 1:
            p = p[:-1]  # stored closed lines repeat the first vertex
        attrs: dict[str, Any] = {}
        if role is not None and role[pos] is not None:
            attrs["ROLE"] = role[pos]
        if named is not None and named[pos] is not None:
            attrs["NAME"] = named[pos]
        out.append(Line(int(lid), layer.name, p, shut, attrs))
    return out
