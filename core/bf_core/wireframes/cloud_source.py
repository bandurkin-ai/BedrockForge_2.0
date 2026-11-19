"""A point cloud as a source of «Построить ЦТМ» (ADR 0014 §5): the points of the classes asked for, thinned to
one point in every cell of the plan, so that a cloud of 10–50 million points gives at most `MAX_VERTICES`
vertices and `geo/dtm.py::build_dtm` builds the surface as it builds any other.

    thin(cloud, classes, cell=None, budget=MAX_VERTICES, tick=None) -> Thinned

* The classes: a list of ASPRS codes ([2] — the ground) or None — every class but the noise (7).
* The cells are squares of `cell` metres of the project grid (`floor(X / cell)`, `floor(Y / cell)`); they do not
  depend on the extent of the cloud. By default `cell = max(CR / 2, √(area / budget))`: CR — the step of the
  last «Фильтр земли» of the cloud (1 m when it was never filtered), area — of the plan extent of the cloud.
* The representative of a cell is the lower median of its points by the height above the ground (the column
  `h`; a point without one goes last) or, in a cloud that has no `h`, by Z; among equals — the point with the
  smaller number (`geo/cloud_thin.py`). One point a cell also means no two vertices one above another on a wall.
* When the cells give more than `budget` points the step grows (by the square root of the excess) and the
  cloud is thinned again; the step asked for and the step used are both in the answer.
* The vertices are the source points bit for bit (float64), in the storage order of the cloud.

The cloud is read by slices of `ROWS` points (`Cloud.read`), never as a whole. Memory: 24–28 bytes a point of
the classes (the cell, the key, the number, the order) plus 8 a cell, freed before the triangulation; `tick`
gets the share of the work done (0…1) at least once a slice and may raise to cancel.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.lines import cloud as lc

MAX_VERTICES = 2_000_000
DEFAULT_CR = 1.0  # the step of the cloth of «Фильтр земли» when the cloud was never filtered, m
GROUND, NOISE = 2, 7
MIN_CELL, MAX_CELL = 0.01, 1000.0
ROWS = 1 << 19  # points read from the cloud at a time
RUNS = 1 << 21  # points handed to the kernel between two ticks
GROW = 1.01  # the least growth of the step when the cells give more points than the limit
MAX_PASSES = 8
BYTES_A_POINT = 32  # memory of the thinning by a point of the classes (the estimate of the job)

Tick = Callable[[float], None] | None
Classes = Sequence[int] | None


def check_classes(classes: Any) -> list[int] | None:
    """The classes of a source as stored in the parameters of the job: sorted codes, None — all but the noise."""
    if classes is None:
        return None
    try:
        codes = sorted({int(code) for code in classes})
    except (TypeError, ValueError):
        codes = []
    if not codes or codes[0] < 0 or codes[-1] >= lc.CLASS_COUNT:
        raise ApiError(
            422,
            "bad_classes",
            "Классы точек облака заданы неверно.",
            "Укажите коды классов от 0 до 255 (2 — земля) или не указывайте их: возьмутся все, кроме шума.",
        )
    return codes


def check_cell(cell: Any) -> float | None:
    if cell is None:
        return None
    try:
        value = float(cell)
    except (TypeError, ValueError):
        value = math.nan
    if not (math.isfinite(value) and MIN_CELL <= value <= MAX_CELL):
        raise ApiError(
            422,
            "bad_cell",
            "Шаг прореживания облака: допустимо от 0,01 до 1000 м.",
            "Исправьте шаг или оставьте поле пустым — шаг подберётся по числу точек.",
        )
    return value


def check_sources(clouds: Any) -> list[dict[str, Any]]:
    """`clouds` of the job `model.dtm`: [{layer_id, classes, cell}], a cloud named twice counts once."""
    out: dict[int, dict[str, Any]] = {}
    for item in clouds or []:
        layer_id = int(item["layer_id"])
        if layer_id not in out:
            classes, cell = check_classes(item.get("classes")), check_cell(item.get("cell"))
            out[layer_id] = {"layer_id": layer_id, "classes": classes, "cell": cell}
    return list(out.values())


def class_table(classes: Classes) -> np.ndarray:
    """256 flags: the codes that are taken."""
    table = np.zeros(lc.CLASS_COUNT, dtype=bool)
    if classes is None:
        table[:] = True
        table[NOISE] = False
    else:
        table[np.asarray(list(classes), dtype=np.int64)] = True
    return table


def count_of(counts: Mapping[Any, Any] | None, classes: Classes) -> int:
    """The points of the classes by `meta.cloud.classes` ({code: count})."""
    table = class_table(classes)
    return sum(int(n) for code, n in (counts or {}).items() if table[int(code)])


def classes_text(classes: Classes) -> str:
    if classes is None:
        return "все классы, кроме шума"
    codes = list(classes)
    if codes == [GROUND]:
        return "класс 2 (земля)"
    return ("класс " if len(codes) == 1 else "классы ") + ", ".join(str(code) for code in codes)


def no_ground(name: str, classes: Classes) -> ApiError:
    """The cloud has no points of the classes — most often it was never filtered."""
    if classes is not None and list(classes) == [GROUND]:
        message = f"В облаке «{name}» нет точек класса 2 (земля)."
    else:
        message = f"В облаке «{name}» нет точек для поверхности: {classes_text(classes)}."
    return ApiError(
        422,
        "no_ground",
        message,
        "Запустите «Фильтр земли» (Моделирование › Поверхности) или выберите другие классы точек.",
    )


def filter_cr(meta: Mapping[str, Any] | None) -> float | None:
    """CR of the last «Фильтр земли» of a cloud (`meta.cloud.ground.params.cr`), None — never filtered."""
    ground = (meta or {}).get("ground")
    params = ground.get("params") if isinstance(ground, Mapping) else None
    cr = params.get("cr") if isinstance(params, Mapping) else None
    if isinstance(cr, (int, float)) and not isinstance(cr, bool) and math.isfinite(cr) and cr > 0:
        return float(cr)
    return None


def cell_of(bbox: Sequence[float], cr: float | None, budget: int = MAX_VERTICES) -> float:
    """The default step: max(CR / 2, √(area of the plan extent / budget))."""
    area = (float(bbox[3]) - float(bbox[0])) * (float(bbox[4]) - float(bbox[1]))
    return max((cr or DEFAULT_CR) / 2.0, math.sqrt(area / budget))


def default_cell(cloud: lc.Cloud, budget: int = MAX_VERTICES) -> float:
    return cell_of(cloud.bbox, filter_cr(cloud.meta), budget)


@dataclass
class Thinned:
    xyz: np.ndarray  # (k, 3) float64: source points bit for bit, in the storage order
    rows: np.ndarray  # (k,) int64: their numbers in the cloud, rising
    cell: float  # the step used, m
    asked: float | None  # the step asked for (None — the default)
    of: int  # points of the classes in the cloud
    by: str  # "h" — the median of the height above the ground, "z" — of Z


def _slices(count: int) -> list[tuple[int, int]]:
    return [(a, min(ROWS, count - a)) for a in range(0, count, ROWS)]


def _grid(bbox: Sequence[float], cell: float) -> tuple[int, int, int, int]:
    """The cells of the project grid the extent lies in: the first column and row, their numbers."""
    ix0, iy0 = math.floor(bbox[0] / cell), math.floor(bbox[1] / cell)
    return ix0, iy0, math.floor(bbox[3] / cell) - ix0 + 1, math.floor(bbox[4] / cell) - iy0 + 1


def _too_fine() -> ApiError:
    return ApiError(
        422,
        "bad_cell",
        "Шаг прореживания слишком мал для охвата облака.",
        "Увеличьте шаг прореживания или оставьте поле пустым.",
    )


def _medians(
    cloud: lc.Cloud, table: np.ndarray, of: int, cell: float, by: str, tick: Callable[[float], None]
) -> np.ndarray:
    """The numbers of the representatives of the cells, rising."""
    from bf_core.geo.cloud_thin import cell_medians

    ix0, iy0, nx, ny = _grid(cloud.bbox, cell)
    if nx * ny >= 1 << 62:
        raise _too_fine()
    num = np.empty(of, dtype=np.uint32 if cloud.count <= 1 << 32 else np.int64)
    code = np.empty(of, dtype=np.int64)
    key = np.empty(of, dtype=np.float32 if by == "h" else np.float64)
    at = 0
    slices = _slices(cloud.count)
    for done, (a, n) in enumerate(slices):
        tick(0.6 * done / len(slices))
        take = np.flatnonzero(table[cloud.read("class", a, n)])
        if not len(take):
            continue
        b = at + len(take)
        if b > of:  # not the column that was counted
            raise lc.broken()
        xyz = cloud.read("xyz", a, n)
        ix = np.floor(xyz[take, 0] / cell).astype(np.int64) - ix0
        iy = np.floor(xyz[take, 1] / cell).astype(np.int64) - iy0
        if int(ix.min()) < 0 or int(ix.max()) >= nx or int(iy.min()) < 0 or int(iy.max()) >= ny:
            raise lc.broken()  # a point outside the extent of the cloud
        code[at:b] = iy * nx + ix
        if by == "h":
            h = cloud.read("h", a, n)[take]
            key[at:b] = np.where(np.isnan(h), np.float32(np.inf), h)  # no height: after every height
        else:
            key[at:b] = xyz[take, 2]
        num[at:b] = take + a
        at = b
    if at != of:
        raise lc.broken()
    tick(0.6)
    order = np.argsort(code)  # groups the cells; the kernel does not rely on the order inside a cell
    out = np.empty(min(of, nx * ny), dtype=np.int64)
    pos = count = 0
    while pos < of:
        tick(0.8 + 0.2 * pos / of)
        pos, count = cell_medians(code, key, order, pos, pos + RUNS, out, count)
    return np.sort(num[out[:count]].astype(np.int64))


def thin(
    cloud: lc.Cloud, classes: Classes = None, cell: float | None = None, *, budget: int = MAX_VERTICES, tick: Tick = None
) -> Thinned:
    """One source point in every cell of the plan (see the module docstring)."""
    budget = max(4, int(budget))
    table = class_table(classes)
    by = "h" if cloud.has("h") else "z"
    last = 0.0

    def step(share: float) -> None:
        nonlocal last
        last = max(last, min(1.0, share))  # a second pass does not move the progress back
        if tick is not None:
            tick(last)

    step(0.0)
    slices = _slices(cloud.count)
    of = 0
    for done, (a, n) in enumerate(slices):
        of += int(np.count_nonzero(table[cloud.read("class", a, n)]))
        step(0.05 * (done + 1) / len(slices))
    use = default_cell(cloud, budget) if cell is None else float(cell)
    if not of:
        step(1.0)
        return Thinned(np.empty((0, 3), dtype=np.float64), np.empty(0, dtype=np.int64), use, cell, 0, by)
    for attempt in range(1, MAX_PASSES + 2):
        rows = _medians(cloud, table, of, use, by, lambda share: step(0.05 + 0.75 * share))
        if len(rows) <= budget:
            break
        if attempt < MAX_PASSES:
            use *= max(GROW, math.sqrt(len(rows) / budget) * GROW)
        else:  # the cells of the grid themselves are not more than the limit: cannot fail
            for _ in range(4096):
                _ix0, _iy0, nx, ny = _grid(cloud.bbox, use)
                if nx * ny <= budget:
                    break
                use *= 1.05
    xyz = np.empty((len(rows), 3), dtype=np.float64)
    for done, (a, n) in enumerate(slices):
        lo, hi = (int(v) for v in np.searchsorted(rows, [a, a + n]))
        if hi > lo:
            xyz[lo:hi] = cloud.read("xyz", a, n)[rows[lo:hi] - a]
        step(0.8 + 0.2 * (done + 1) / len(slices))
    return Thinned(xyz, rows, use, cell, of, by)


# --- the clouds of a job ---------------------------------------------------------------------------------


def _n(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def _num(x: float) -> str:
    return f"{round(x, 4):.10g}".replace(".", ",")


@dataclass
class Taken:
    """A cloud of the job `model.dtm`, thinned."""

    layer_id: int
    name: str
    classes: list[int] | None
    budget: int
    thinned: Thinned

    def used(self) -> dict[str, Any]:
        """What `meta.built.sources` of the surface keeps about the cloud."""
        t = self.thinned
        return {
            "id": self.layer_id,
            "name": self.name,
            "points": len(t.rows),
            "classes": self.classes,
            "cell": t.cell,
            "class_points": t.of,
        }

    def notes(self) -> list[tuple[str, str]]:
        """Lines of the journal: (text, level)."""
        t = self.thinned
        median = "высоты над землёй" if t.by == "h" else "отметки"
        out = [
            (
                f"Облако «{self.name}»: {classes_text(self.classes)} — точек {_n(t.of)}; шаг прореживания "
                f"{_num(t.cell)} м ({'задан' if t.asked == t.cell else 'подобран'}), в ячейке — точка с медианой "
                f"{median}; вершин {_n(len(t.rows))}",
                "info",
            )
        ]
        if t.asked is not None and t.cell != t.asked:
            out.append(
                (
                    f"Шаг прореживания облака «{self.name}» увеличен с {_num(t.asked)} до {_num(t.cell)} м: "
                    f"вершин не больше {_n(self.budget)}",
                    "warn",
                )
            )
        return out


def take(conn: Any, store: Any, specs: Sequence[Mapping[str, Any]], tick: Tick = None) -> list[Taken]:
    """The clouds of a job (`check_sources`), each thinned; the limit of vertices is shared by the clouds in
    proportion to their points of the classes. A cloud without such points — `no_ground`."""
    opened = [(lc.open_cloud(conn, store, int(spec["layer_id"])), spec) for spec in specs]
    counts = [count_of(cloud.classes, spec.get("classes")) for cloud, spec in opened]
    out: list[Taken] = []
    for k, ((cloud, spec), of) in enumerate(zip(opened, counts, strict=True)):
        classes = None if spec.get("classes") is None else [int(code) for code in spec["classes"]]
        if not of:
            raise no_ground(cloud.name, classes)
        budget = MAX_VERTICES if len(opened) == 1 else MAX_VERTICES * of // sum(counts)

        def share(f: float, k: int = k) -> None:
            if tick is not None:
                tick((k + f) / len(opened))

        got = thin(cloud, classes, spec.get("cell"), budget=budget, tick=share)
        if not len(got.rows):  # `meta.cloud.classes` said there were points
            raise no_ground(cloud.name, classes)
        out.append(Taken(cloud.layer_id, cloud.name, classes, budget, got))
    return out
