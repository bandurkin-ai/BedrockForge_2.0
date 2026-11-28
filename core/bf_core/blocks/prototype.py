"""The prototype of a block model — an empty grid for filling and coding by wireframes (подшаг 17.19; ТЗ 8.7.3,
8.7.7; ADR 0023 §6, decision 66; forms — docs/progress/e17-api.md §6).

    plan = make_plan(size, extent, pad=20, rotation=30, levels=(3, 3, 2))   # pure: the grid, the counts, the disk
    box = resolve_extent(conn, object_ids=[3, 7], extent=None)              # the box of objects, or the given one
    out = create_prototype(conn, store, plan, name="Прототип", full=False)  # a model with 0 rows (no big file)

The grid. The box (the extent of the objects or a given one) in the axes of the project: its 8 corners go to the
local axes q′ = R_z(−φ)·p (about the origin of the project's coordinates), the origin is a multiple of the block:
O′ = ⌊(min q′ − pad) / d⌋·d, n = ⌈(max q′ + pad − O′) / d⌉ (at least 1), O = R_z(φ)·O′ (Z is not turned). So a
prototype of a rotated model is on the lattice of its own axes: R_z(−φ)·O is a multiple of the block (exact for the
multiples of 90°, within rounding of float64 otherwise). Sin and cos of the multiples of 90° are exact (`GridV2`).

The model. A prototype has no rows — there is no file of millions of rows, only the empty `cells` (the columns of
the schema, 0 row groups of data) and `meta`: grid v2 with `div` = 2^levels, `rows` = 0, `parents` = 0, `full` —
№ 66: a flag «all the parents are occupied» (the parents are not written; a consumer counts nx·ny·nz of the grid).
`source` = {op: prototype, params}. Creating a prototype does not depend on the number of parents: 20·10⁶ parents
are the same few milliseconds as 20.

Errors are {code, message, hint} in Russian: 422 `bad_param {param}`, `no_extent`, `subblock_div {param}`,
`block_model_too_large`; 404 `object_not_found {param}`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import schema as bs
from bf_core.blocks import store as bm
from bf_core.db.schema import objects
from bf_core.sections.geometry import sin_cos_deg
from bf_core.storage.blobs import BlobStore

OP = "prototype"
DISK_PARENT = 8 + 10  # bytes of a row without compression: idx uint64 + the field slots of a fill (CODE, FRAC)
DISK_SUB = 6  # sx … wz of a model with sub-blocks
MAX_OBJECTS = 20_000
NAME = "Прототип"


def bad_param(param: str, message: str, hint: str, code: str = "bad_param") -> ApiError:
    return ApiError(422, code, message, hint, extra={"param": param})


def _number(value: Any, param: str, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise bad_param(param, f"{label}: нужно конечное число.", "Задайте значение числом, в метрах.")
    return float(value)


def _triple(raw: Any, param: str, label: str) -> tuple[float, float, float]:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        raw = (raw, raw, raw)
    if not isinstance(raw, Sequence) or isinstance(raw, str) or len(raw) != 3:
        raise bad_param(param, f"{label}: нужны три значения (X, Y, Z) или одно число.",
                        "Задайте число или три числа через запятую.")  # fmt: skip
    a, b, c = (_number(v, f"{param}.{i}", label) for i, v in enumerate(raw))
    return (a, b, c)


def check_extent(raw: Any, param: str = "extent") -> list[float]:
    """[xmin, ymin, zmin, xmax, ymax, zmax] of the request: finite, min ≤ max, else 422 `bad_param`."""
    if not isinstance(raw, Sequence) or isinstance(raw, str) or len(raw) != 6:
        raise bad_param(param, "Охват задан неверно: нужны шесть чисел.", "Задайте мин. и макс. по X, Y, Z.")
    box = [_number(v, f"{param}.{i}", "Охват") for i, v in enumerate(raw)]
    if any(box[a] > box[a + 3] for a in range(3)):
        raise bad_param(param, "В охвате минимум больше максимума.", "Поменяйте границы местами.")
    return box


@dataclass(frozen=True)
class Plan:
    """What `make_plan` makes of the request: the grid of the new model and the numbers the window shows."""

    grid: bs.GridV2
    extent: list[float]  # the box asked for (project axes), without the pad
    pad: tuple[float, float, float]
    full: bool

    @property
    def parents(self) -> int:
        return self.grid.cells

    @property
    def disk(self) -> int:
        """Bytes of the model a fill would make from this grid, no compression («≈» in the window)."""
        return self.parents * (DISK_PARENT + (DISK_SUB if self.grid.sub.sub else 0))

    def frame(self) -> list[list[float]]:
        """The 8 corners of the grid in the project axes: q ∈ {0, L}³, x fastest, then y, then z."""
        lx, ly, lz = self.grid.lengths()
        q = [[x, y, z] for z in (0.0, lz) for y in (0.0, ly) for x in (0.0, lx)]
        return [[float(v) for v in row] for row in self.grid.to_project(q)]

    def preview(self) -> dict[str, Any]:
        """The answer of `POST /blocks/prototype/preview`."""
        return {"grid": self.grid.to_meta(), "parents": self.parents, "disk": self.disk,
                "extent": self.grid.extent(), "frame": self.frame()}  # fmt: skip

    def params(self) -> dict[str, Any]:
        """`source.params` of the model: enough to repeat the request."""
        return {"size": list(self.grid.size or ()), "extent": list(self.extent), "pad": list(self.pad),
                "rotation": self.grid.rotation, "levels": list(self.grid.sub.levels or ()) or None,
                "full": self.full}  # fmt: skip


def make_plan(
    size: Any,
    extent: Any,
    *,
    pad: Any = 0.0,
    rotation: Any = 0.0,
    levels: Any = None,
    full: bool = False,
) -> Plan:
    """The grid for `extent` (project axes) with a block `size`, `pad` on every side, a turn of φ degrees about Z and
    `levels` of sub-blocks (L per axis, 0…7). Pure. ApiError: 422 `bad_param`, `subblock_div`,
    `block_model_too_large`. No tolerance: the grid always covers the box (a hair of rounding may add a layer)."""
    d = _triple(size, "size", "Размер блока")
    if any(v <= 0 for v in d):
        raise bad_param("size", "Размер блока должен быть больше нуля.", "Задайте размер блока по X, Y, Z в метрах.")
    box = check_extent(extent)
    pd = _triple(pad, "pad", "Отступ")
    if any(v < 0 for v in pd):
        raise bad_param("pad", "Отступ не может быть отрицательным.", "Задайте отступ от охвата 0 или больше.")
    if not isinstance(full, bool):
        raise bad_param("full", "Признак «Все родители заняты» — да или нет.", "Включите или выключите флажок.")
    phi = bs.parse_rotation(rotation)
    if levels is not None and (not isinstance(levels, Sequence) or isinstance(levels, str) or len(levels) != 3):
        raise bad_param("levels", "Уровни субблоков: нужны три числа (X, Y, Z).",
                        "Задайте от 0 до 7 уровней по каждой оси.")  # fmt: skip
    sub = bs.SubblockSpec.of_levels((0, 0, 0) if levels is None else levels)
    s, c = sin_cos_deg(phi)
    xs, ys = (box[0], box[3]), (box[1], box[4])
    # the corners in the local axes: q′ = R_z(−φ)·p, about the origin of the project
    qx = [c * x + s * y for x in xs for y in ys]
    qy = [c * y - s * x for x in xs for y in ys]
    lo = (min(qx) - pd[0], min(qy) - pd[1], box[2] - pd[2])
    hi = (max(qx) + pd[0], max(qy) + pd[1], box[5] + pd[2])
    o_local = [math.floor(lo[a] / d[a]) * d[a] for a in range(3)]
    counts = [max(1, math.ceil((hi[a] - o_local[a]) / d[a])) for a in range(3)]
    corner = [c * o_local[0] - s * o_local[1], s * o_local[0] + c * o_local[1], o_local[2]]
    grid = bs.GridV2.of({"corner": corner, "counts": counts, "size": list(d), "rotation": phi,
                         "div": list(sub.div)})  # fmt: skip
    return Plan(grid, box, pd, full)


def resolve_extent(
    conn: sa.Connection, object_ids: Sequence[int] | None = None, extent: Any = None
) -> list[float]:
    """The box to cover: the union of `objects.extent` of the objects, or the given `extent` — exactly one of the two,
    else 422 `bad_param {param: "object_ids"}`; an unknown object — 404 `object_not_found {param:
    "object_ids.N"}`; one with no extent — 422 `no_extent`."""
    if (object_ids is None) == (extent is None):
        raise bad_param("object_ids", "Нужно задать охват: либо объекты, либо границы по X, Y, Z (не оба сразу).",
                        "Выберите объекты в менеджере данных или введите границы вручную.")  # fmt: skip
    if extent is not None:
        return check_extent(extent)
    ids = list(object_ids or ())
    if not ids or len(ids) > MAX_OBJECTS or any(isinstance(i, bool) or not isinstance(i, int) for i in ids):
        raise bad_param("object_ids", "Список объектов для охвата задан неверно.",
                        "Выберите от 1 до 20 000 объектов.")  # fmt: skip
    found = {int(r.id): r.extent for r in conn.execute(
        sa.select(objects.c.id, objects.c.extent).where(objects.c.id.in_(ids)))}  # fmt: skip
    boxes: list[list[float]] = []
    for n, i in enumerate(ids):
        if i not in found:
            raise ApiError(404, "object_not_found", "Объект для охвата не найден.", "Обновите менеджер данных.",
                           extra={"param": f"object_ids.{n}"})  # fmt: skip
        raw = found[i]
        ok = (isinstance(raw, Sequence) and not isinstance(raw, str) and len(raw) == 6
              and all(isinstance(v, (int, float)) and math.isfinite(v) for v in raw)
              and all(raw[a] <= raw[a + 3] for a in range(3)))  # fmt: skip
        if ok:
            boxes.append([float(v) for v in raw])
    if not boxes:
        raise ApiError(422, "no_extent", "У выбранных объектов нет охвата: сетку не по чему построить.",
                       "Выберите объекты с данными или задайте границы вручную.",
                       extra={"param": "object_ids"})  # fmt: skip
    return [min(b[a] for b in boxes) for a in range(3)] + [max(b[a] for b in boxes) for a in range(3, 6)]


def create_prototype(
    conn: sa.Connection,
    store: BlobStore,
    plan: Plan,
    *,
    name: str = NAME,
    folder: str | None = None,
    parent_id: int | None = None,
    job_id: int | None = None,
    crs: str | None = None,
    author: str | None = None,
) -> dict[str, Any]:
    """Inside the write transaction: the model with 0 rows (the flag `full` — № 66), `source {op: prototype,
    params}`. → {model_id, name, grid, parents, events}: the job's result (`parents` = nx·ny·nz — the preview's number;
    `meta.parents` of a prototype is 0 — it writes no row, `full` tells the parents are all occupied)."""
    with bm.CellsWriterV2(store, plan.grid, [], full=plan.full) as w:
        staged = w.finish()
    try:
        bm_id = bm.commit_block_model(
            conn, store, staged, name=name or NAME, source={"op": OP, "params": plan.params()}, parent_id=parent_id,
            folder=folder, job_id=job_id, crs=crs, author=author,
        )  # fmt: skip
    finally:
        staged.discard()
    final = conn.execute(sa.select(objects.c.name).where(objects.c.id == bm_id)).scalar_one()
    return {"model_id": bm_id, "name": str(final), "grid": plan.grid.to_meta(), "parents": plan.parents,
            "events": [["objects.changed", {"kind": bs.KIND, "id": bm_id}]]}  # fmt: skip


def prototype_info(meta: Mapping[str, Any]) -> dict[str, Any] | None:
    """For a consumer (the fill job): {grid, parents, sub, full} of a prototype meta, or None — not a prototype
    (it has rows, or it is not made by `blocks.prototype`)."""
    from bf_core.blocks import meta as bmeta

    m = bmeta.upgrade(meta)
    if m["rows"] or not (m["source"] or {}).get("op") == OP:
        return None
    grid = bs.GridV2.of(m["grid"])
    return {"grid": grid, "parents": grid.cells, "sub": grid.sub.sub, "full": m["full"]}
