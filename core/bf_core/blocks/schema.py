"""The block model — the contract of its storage: meta v1 of Э11 (ADR 0017 §3) and meta v2 of Э17 (ADR 0023 §2,
decisions 2–6; forms of the API — docs/progress/e17-api.md). v2 extends v1: a v1 meta reads as v2 (`meta.upgrade`:
rotation 0, div [1, 1, 1], every field in the file `cells`), the files of a v1 model are never rewritten.

Object: `objects(kind="block_model", state ready | building, job_id of the building job)`; `name` unique
among block models (case-insensitive, «Имя (2)»), `folder`, `crs`, `extent` [xmin, ymin, zmin, xmax, ymax, zmax]
of the whole grid (every cell, occupied or not; v2 — the box of the turned grid in the axes of the project), float64.

`objects.meta` (v 1):

    {"v": 1,
     "grid": {"corner": [x, y, z],              # float64: the minimum corner of cell (0, 0, 0)
              "counts": [nx, ny, nz],           # ≥ 1 each, nx·ny·nz ≤ MAX_CELLS
              "size": [dx, dy, dz],             # a regular step, > 0 — or, for a variable step (OMF tensors):
              "tensors": {"x": [dx0, …], "y": […], "z": […]},   # len = counts; exactly one of size / tensors
              "order": "F"},                    # idx = i + nx·(j + ny·k): i along +X fastest, then +Y, then +Z
     "cells": 10000,                            # occupied cells = rows of the Parquet file
     "fields": [{"name": "Au", "kind": "number", "unit": "g/t" | null,
                 "stats": {"count", "missing", "min", "max", "mean", "std"}},       # std — population (n)
                {"name": "Порода", "kind": "category", "unit": null, "labels": ["руда", "порода"],
                 "stats": {"count", "missing", "counts": [n per label]}}],
     "source": {"format": "omf", "file": "model.omf", "element": "Блоки"} | null,
     "warnings": ["…"]}                         # what was not carried over (Russian, for «Свойства»)

`count` — occupied cells with a value, `missing` — occupied cells without one (never turned into 0); with no
values `min`… `std` are null. Field names: 1–100 characters, unique ignoring case, never `idx`.

`objects.meta` (v 2) — `blocks/meta.py::upgrade` checks and normalises it (and turns a v1 meta into it):

    {"v": 2,
     "grid": {"corner", "counts" (nx·ny·nz ≤ MAX_GRID_V2), "size" | "tensors", "order": "F",
              "rotation": φ,                    # degrees about +Z, (−180; 180], counter-clockwise seen from above
              "div": [2^Lx, 2^Ly, 2^Lz]},       # sub-cells of a parent per axis, L ≤ 7; with tensors only [1, 1, 1]
     "rows": n, "parents": m, "sub": bool,      # rows of the files; occupied parents; the file has sx … wz
     "full": bool,                              # a prototype (0 rows) whose every parent counts as occupied
     "fields": [{"name", "kind", "dtype": f8|f4|i4|i2|i1|cat, "unit", "description", "role":
                 grade|density|frac|code|class|null, "labels"? (cat), "stats", "file": "cells" | "fld:<8 hex>",
                 "source": {"op", "params", "inputs" [sha256], "algo", "versions", "at"} | null}],
     "checks": {"at", "duplicates", "overlaps": {"n", "method": exact|volume}, "empty" [], "constant" [],
                "volume", "examples" [≤ 20]} | null,
     "groups": [{"rows", "k0", "k1"}] | null,  # row groups of 2²⁰ rows of every file, layers k they cover
     "source": {"op": import|prototype|fill|code|regularize, "format"?, "file"?, "element"?, "params"?} | null,
     "warnings": [...], "inputs": {"wireframes" | "model": [{"id", "sha"}]} | null, "algo": str | null}

A point of the model: p = O + R_z(φ)·q (ТЗ 8.7.2), O = `corner`, q — local axes, parent (i, j, k) covers
q ∈ [i·dx; (i+1)·dx) × …; P = i + nx·(j + ny·k) (ADR 0017; ТЗ «Мортон» — question № 62). Rows of every file go by
(P, sz, sy, sx) strictly ascending (decision 3); a parent without sub-blocks has s = 0, w = div.

Files: one blob of the owner `object` with the role `cells` — Parquet (zstd, row groups of ROW_GROUP rows in v1,
ROW_GROUP_V2 in v2): column `idx` uint64 (P), with `sub` the uint8 columns sx sy sz wx wy wz, then one column per
field of the file, named by the field: v1 number — float64 (NaN — empty), v1 category — int32 code into `labels`
(−1 — empty); v2: f8 double, f4 float (NaN — empty), i4/i2/i1 int32/16/8 and cat int16 (null — empty). A blob
`fld:<key>` holds the fields of one calculation in the same rows and row groups, without `idx`. A cell not in the
file is not in the model. The source file of an import, if kept, is a blob of an `import_log` row.

API (`blocks/router.py`): `GET /blocks`, `GET /blocks/{id}`, `PATCH /blocks/{id}` {name, expected_version},
`DELETE /blocks/{id}`; changes publish `objects.changed {kind: "block_model", id[, deleted]}`.
Writing and reading: `blocks/store.py` (`CellsWriter`, `commit_block_model`, `iter_cells`, `read_cells`).

This module has no numpy at import time (tests/test_process.py): the array helpers import it inside.
"""

from __future__ import annotations

import itertools
import math
import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bf_core.api.errors import ApiError

if TYPE_CHECKING:
    import numpy as np

KIND = "block_model"
ROLE = "cells"
META_VERSION = 1
NUMBER, CATEGORY = "number", "category"
FIELD_KINDS = (NUMBER, CATEGORY)
ORDER = "F"
IDX = "idx"
ROW_GROUP = 1_000_000
MAX_CELLS = 200_000_000  # ADR 0017 §2: a larger grid is refused
MAX_FIELDS = 1000
MAX_FIELD_NAME = 100
MAX_LABELS = 100_000
MAX_UNIT = 40
AXES = ("x", "y", "z")
PARQUET_MEDIA = "application/vnd.apache.parquet"


def bad_model(message: str, hint: str = "Проверьте исходный файл модели.") -> ApiError:
    return ApiError(422, "block_model_invalid", message, hint)


def _finite(value: Any, label: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise bad_model(f"{label}: не число.") from None
    if not math.isfinite(out):
        raise bad_model(f"{label}: не конечное число.")
    return out


def _triple(raw: Any, label: str) -> list[Any]:
    if not isinstance(raw, Sequence) or isinstance(raw, str) or len(raw) != 3:
        raise bad_model(f"{label}: нужны три значения (X, Y, Z).")
    return list(raw)


def check_grid(raw: Mapping[str, Any], limit: int = MAX_CELLS) -> dict[str, Any]:
    """The grid of `meta.grid`, checked and normalised (plain floats and ints, `order: "F"`); v1 keys only."""
    if not isinstance(raw, Mapping):
        raise bad_model("Сетка модели не задана.")
    corner_raw = _triple(raw.get("corner"), "Угол модели")
    corner = [_finite(v, f"Угол модели {a.upper()}") for v, a in zip(corner_raw, AXES, strict=True)]
    tensors_raw, size_raw = raw.get("tensors"), raw.get("size")
    if (tensors_raw is None) == (size_raw is None):
        raise bad_model("У сетки должен быть либо постоянный шаг (size), либо переменный (tensors).")
    grid: dict[str, Any] = {"corner": corner}
    if tensors_raw is not None:
        if not isinstance(tensors_raw, Mapping):
            raise bad_model("Переменный шаг сетки задан неверно.")
        tensors: dict[str, list[float]] = {}
        for a in AXES:
            steps = tensors_raw.get(a)
            if not isinstance(steps, Sequence) or isinstance(steps, str) or not steps:
                raise bad_model(f"Шаги сетки по {a.upper()} не заданы.")
            tensors[a] = [_step(v, a) for v in steps]
        counts = [len(tensors[a]) for a in AXES]
        given = raw.get("counts")
        if given is not None and [int(c) for c in _triple(given, "Число ячеек")] != counts:
            raise bad_model("Число ячеек сетки не совпадает с числом шагов.")
        grid["counts"] = counts
        grid["tensors"] = tensors
    else:
        grid["counts"] = [_count(v, a) for v, a in zip(_triple(raw.get("counts"), "Число ячеек"), AXES, strict=True)]
        grid["size"] = [_step(v, a) for v, a in zip(_triple(size_raw, "Размер ячейки"), AXES, strict=True)]
    n = math.prod(grid["counts"])
    if n > limit:
        raise ApiError(
            422,
            "block_model_too_large",
            f"В модели {n:,} ячеек — больше предела {limit:,}.".replace(",", " "),
            "Разделите модель на части или укрупните ячейки.",
        )
    grid["order"] = ORDER
    return grid


def _step(value: Any, axis: str) -> float:
    out = _finite(value, f"Размер ячейки по {axis.upper()}")
    if out <= 0:
        raise bad_model(f"Размер ячейки по {axis.upper()} должен быть больше нуля.")
    return out


def _count(value: Any, axis: str) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        raise bad_model(f"Число ячеек по {axis.upper()}: не целое число.") from None
    if out != value or out < 1:
        raise bad_model(f"Число ячеек по {axis.upper()} должно быть целым и не меньше 1.")
    return out


def cell_count(grid: Mapping[str, Any]) -> int:
    return math.prod(int(c) for c in grid["counts"])


def steps(grid: Mapping[str, Any], axis: int) -> list[float]:
    """Cell sizes along one axis (0 — X, 1 — Y, 2 — Z), regular or variable."""
    if "tensors" in grid:
        return [float(v) for v in grid["tensors"][AXES[axis]]]
    return [float(grid["size"][axis])] * int(grid["counts"][axis])


def edges(grid: Mapping[str, Any], axis: int) -> list[float]:
    """Cell boundaries along one axis, float64: corner, corner + d0, …, n + 1 values."""
    corner = float(grid["corner"][axis])
    return [corner, *(corner + d for d in itertools.accumulate(steps(grid, axis)))]


def extent(grid: Mapping[str, Any]) -> list[float]:
    """[xmin, ymin, zmin, xmax, ymax, zmax] of the whole grid."""
    lo = [float(c) for c in grid["corner"]]
    return [*lo, *(edges(grid, a)[-1] for a in range(3))]


def check_fields(raw: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Fields of `meta.fields` without the stats: {name, kind, unit, labels?}, checked."""
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise bad_model("Поля модели заданы неверно.")
    if len(raw) > MAX_FIELDS:
        raise bad_model(f"У модели больше {MAX_FIELDS} полей.")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for f in raw:
        name = " ".join(str(f.get("name") or "").split())
        if not name or len(name) > MAX_FIELD_NAME:
            raise bad_model(f"Имя поля должно быть от 1 до {MAX_FIELD_NAME} символов.")
        key = name.casefold()
        if key == IDX or key in seen:
            raise bad_model(f"Поле «{name}» повторяется или совпадает со служебным именем.")
        seen.add(key)
        kind = f.get("kind", NUMBER)
        if kind not in FIELD_KINDS:
            raise bad_model(f"Поле «{name}»: неизвестный тип {kind!r}.")
        unit = f.get("unit")
        unit = (" ".join(str(unit).split())[:MAX_UNIT] or None) if unit is not None else None
        field: dict[str, Any] = {"name": name, "kind": kind, "unit": unit}
        if kind == CATEGORY:
            labels = f.get("labels") or []
            if not isinstance(labels, Sequence) or isinstance(labels, str) or len(labels) > MAX_LABELS:
                raise bad_model(f"Поле «{name}»: подписи категорий заданы неверно.")
            field["labels"] = [str(v) for v in labels]
        out.append(field)
    return out


def field_names(names: Sequence[str]) -> list[str]:
    """Names a reader can give the fields of a file: spaces collapsed, cut to the limit, empty → «Поле N»,
    `idx` and repeats (ignoring case) → «… (2)»; `check_fields` accepts the result."""
    out: list[str] = []
    seen: set[str] = {IDX}
    for n, raw in enumerate(names, start=1):
        base = " ".join(str(raw or "").split())[:MAX_FIELD_NAME] or f"Поле {n}"
        candidate, k = base, 1
        while candidate.casefold() in seen:
            k += 1
            suffix = f" ({k})"
            candidate = base[: MAX_FIELD_NAME - len(suffix)] + suffix
        seen.add(candidate.casefold())
        out.append(candidate)
    return out


def make_meta(
    grid: Mapping[str, Any],
    cells: int,
    fields: Sequence[Mapping[str, Any]],
    *,
    source: Mapping[str, Any] | None = None,
    warnings: Sequence[str] = (),
) -> dict[str, Any]:
    """`objects.meta` v1; `fields` carry their `stats` (store.CellsWriter.finish)."""
    src = None
    if source:
        src = {k: (None if source.get(k) is None else str(source.get(k))) for k in ("format", "file", "element")}
    return {
        "v": META_VERSION,
        "grid": dict(grid),
        "cells": int(cells),
        "fields": [dict(f) for f in fields],
        "source": src,
        "warnings": [str(w) for w in warnings],
    }


# --- cell numbers (numpy inside) ----------------------------------------------------------------------


def idx_of(i: Any, j: Any, k: Any, counts: Sequence[int]) -> np.ndarray:
    """Cell numbers `i + nx·(j + ny·k)` (uint64)."""
    import numpy as np

    nx, ny = int(counts[0]), int(counts[1])
    i, j, k = (np.asarray(v, dtype=np.uint64) for v in (i, j, k))
    return i + np.uint64(nx) * (j + np.uint64(ny) * k)  # type: ignore[no-any-return]


def ijk_of(idx: Any, counts: Sequence[int]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    import numpy as np

    nx, ny = np.uint64(counts[0]), np.uint64(counts[1])
    idx = np.asarray(idx, dtype=np.uint64)
    i = idx % nx
    rest = idx // nx
    return i, rest % ny, rest // ny


def centers(grid: Mapping[str, Any], idx: Any) -> np.ndarray:
    """Centres of the cells `idx` in project coordinates, float64 n×3."""
    import numpy as np

    ijk = ijk_of(idx, grid["counts"])
    out = np.empty((len(ijk[0]), 3), dtype=np.float64)
    for a in range(3):
        e = np.asarray(edges(grid, a), dtype=np.float64)
        mid = (e[:-1] + e[1:]) * 0.5
        out[:, a] = mid[ijk[a].astype(np.intp)]
    return out


# --- meta v2: grid, rotation, sub-blocks (ADR 0023 §2–3, decisions 2–6) -------------------------------

META_V2 = 2
MAX_GRID_V2 = 2_000_000_000  # ТЗ 8.7.2: nx·ny·nz ≤ 2·10⁹
MAX_LEVEL = 7  # L per axis; div = 2^L ≤ 128
ROW_GROUP_V2 = 1 << 20
MAX_LABELS_V2 = 32_767  # cat of the new files: int16 codes
MAX_DESCRIPTION = 500
MAX_EXAMPLES = 20
DTYPES = ("f8", "f4", "i4", "i2", "i1", "cat")
NUMBER_DTYPES = DTYPES[:5]
NUMBER_ROLES, CATEGORY_ROLES = ("grade", "density", "frac"), ("code", "class")
FIELD_OPS = ("import", "calc", "fill", "code", "density", "classify", "regularize", "estimate", "simulate")
MODEL_OPS = ("import", "prototype", "fill", "code", "regularize")
FLD = "fld:"
SUB_COLUMNS = ("sx", "sy", "sz", "wx", "wy", "wz")
FLD_RE = re.compile(r"fld:[0-9a-f]{8}")
SHA_RE = re.compile(r"[0-9a-f]{64}")


def _err(code: str, message: str, hint: str, param: str) -> ApiError:
    return ApiError(422, code, message, hint, extra={"param": param})


def rotation_unsupported(param: str = "grid.rotation") -> ApiError:
    return _err("rotation_unsupported", "Поворот вокруг X/Y в v1 не поддерживается.",
                "Сохраните модель в исходной программе без поворота вокруг X и Y (допустим один угол вокруг Z).",
                param)  # fmt: skip


def bad_div(axis: int, value: Any) -> ApiError:
    return _err("subblock_div", f"Деление на субблоки по {AXES[axis].upper()} — {value!r}: нужна степень двойки "
                "от 1 до 128.", "Субблоки хранятся по подъячейкам 2^L, L ≤ 7 (ТЗ 8.7.2): 1, 2, 4, 8, 16, 32, 64 "
                "или 128 по каждой оси.", f"grid.div.{axis}")  # fmt: skip


def variable_grid(what: str = "Операция") -> ApiError:
    return _err("variable_grid", f"{what}: у модели переменный шаг сетки.",
                "Субблоки, заполнение и регуляризация — только для сетки с постоянным шагом; модель с переменным "
                "шагом можно показать, отфильтровать, экспортировать и посчитать в отчёте.",
                "grid.tensors")  # fmt: skip


def normalize_angle(deg: float) -> float:
    """Degrees into (−180; 180]; −0.0 → 0.0."""
    r = math.fmod(float(deg), 360.0)
    if r <= -180.0:
        r += 360.0
    elif r > 180.0:
        r -= 360.0
    return r + 0.0


def _angle(value: Any, param: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _err("bad_param", "Угол поворота: нужно конечное число градусов.", "Задайте угол числом.", param)
    return normalize_angle(value)


def parse_rotation(raw: Any) -> float:
    """φ of `grid.rotation`: a number of degrees about Z, or {x?, y?, z?} — a turn about X or Y → 422."""
    if raw is None:
        return 0.0
    if isinstance(raw, Mapping):
        for a in ("x", "y"):
            if _angle(raw.get(a, 0.0), f"grid.rotation.{a}") != 0.0:
                raise rotation_unsupported(f"grid.rotation.{a}")
        return _angle(raw.get("z", 0.0), "grid.rotation.z")
    return _angle(raw, "grid.rotation")


def rotation_of_angles(turns: Sequence[tuple[Any, Any]]) -> float:
    """φ of successive turns [(axis, degrees)], axis "x" | "y" | "z" or 1 | 2 | 3 (Datamine ROTAXIS); a turn about
    X or Y that is not a multiple of 360° → 422 `rotation_unsupported`."""
    phi = 0.0
    for n, (axis, deg) in enumerate(turns):
        a = None if isinstance(axis, bool) else {1: "x", 2: "y", 3: "z"}.get(axis, axis)
        if a not in AXES:
            raise _err("bad_param", f"Ось поворота {axis!r}: нужна X, Y или Z.", "Проверьте параметры поворота.",
                       f"grid.rotation.{n}")  # fmt: skip
        angle = _angle(deg, f"grid.rotation.{n}")
        if a != "z" and angle != 0.0:
            raise rotation_unsupported(f"grid.rotation.{n}")
        phi = normalize_angle(phi + angle)
    return phi


def rotation_of_axes(u: Sequence[float], v: Sequence[float], w: Sequence[float], tol: float = 1e-9) -> float:
    """φ of the unit axes u, v, w of a turned grid (OMF `axis_u/v/w`): w = +Z, u and v in the plane, else 422."""
    uu, vv, ww = ([float(c) for c in _triple(x, "Ось сетки")] for x in (u, v, w))
    if abs(ww[0]) > tol or abs(ww[1]) > tol or abs(ww[2] - 1.0) > tol or abs(uu[2]) > tol or abs(vv[2]) > tol:
        raise rotation_unsupported()
    phi = math.degrees(math.atan2(uu[1], uu[0]))
    phi = float(round(phi)) if abs(phi - round(phi)) < 1e-9 else phi
    s, c = _sin_cos(phi)
    if abs(uu[0] - c) > tol or abs(uu[1] - s) > tol or abs(vv[0] + s) > tol or abs(vv[1] - c) > tol:
        raise rotation_unsupported()
    return normalize_angle(phi)


def _sin_cos(deg: float) -> tuple[float, float]:
    from bf_core.sections.geometry import sin_cos_deg  # exact on the multiples of 90°

    return sin_cos_deg(deg)


@dataclass(frozen=True)
class SubblockSpec:
    """Sub-cells of a parent, div = (2^Lx, 2^Ly, 2^Lz). A sub-block is (s, w) per axis in sub-cells: offset
    s ∈ [0; div), size w ≥ 1, s + w ≤ div — any aligned box (ТЗ 8.7.2); a whole parent — s = 0, w = div."""

    div: tuple[int, int, int] = (1, 1, 1)

    @classmethod
    def of(cls, raw: Any) -> SubblockSpec:
        if raw is None:
            return cls()
        out = []
        for a, value in enumerate(_triple(raw, "Деление на субблоки")):
            whole = not isinstance(value, bool) and isinstance(value, (int, float)) and float(value).is_integer()
            d = int(value) if whole else 0
            if not 1 <= d <= 1 << MAX_LEVEL or d & (d - 1):
                raise bad_div(a, value)
            out.append(d)
        return cls((out[0], out[1], out[2]))

    @classmethod
    def of_levels(cls, levels: Sequence[int]) -> SubblockSpec:
        """From L per axis (0…7): div = 2^L; another L → 422 `subblock_div {param: "levels.N"}`."""
        out = []
        for a, v in enumerate(_triple(levels, "Уровни субблоков")):
            if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= MAX_LEVEL:
                raise _err("subblock_div", f"Уровней субблоков по {AXES[a].upper()} — {v!r}: нужно от 0 до 7.",
                           "Каждый уровень делит блок пополам по оси (ТЗ 8.7.2): не больше 7 уровней.",
                           f"levels.{a}")  # fmt: skip
            out.append(1 << v)
        return cls((out[0], out[1], out[2]))

    @property
    def levels(self) -> tuple[int, int, int]:
        return (self.div[0].bit_length() - 1, self.div[1].bit_length() - 1, self.div[2].bit_length() - 1)

    @property
    def sub(self) -> bool:
        return self.div != (1, 1, 1)

    def bad_rows(self, s: Any, w: Any) -> np.ndarray:
        """Rows whose sub-block is off the sub-cells of its parent; `s`, `w` — n×3 (sx sy sz, wx wy wz)."""
        import numpy as np

        s, w = np.asarray(s, dtype=np.int64).reshape(-1, 3), np.asarray(w, dtype=np.int64).reshape(-1, 3)
        div = np.asarray(self.div, dtype=np.int64)
        return (s < 0).any(axis=1) | (w < 1).any(axis=1) | (s + w > div).any(axis=1)  # type: ignore[no-any-return]


@dataclass(frozen=True)
class GridV2:
    """`meta.grid` v2 (a v1 grid reads as v2): O = `corner`, counts, a regular `size` or `tensors`, φ = `rotation`,
    sub-cells `sub`. Local axes q: p = O + R_z(φ)·q (ТЗ 8.7.2); float64 throughout."""

    corner: tuple[float, float, float]
    counts: tuple[int, int, int]
    size: tuple[float, float, float] | None = None
    tensors: tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]] | None = None
    rotation: float = 0.0
    sub: SubblockSpec = SubblockSpec()

    @classmethod
    def of(cls, raw: Any) -> GridV2:
        if not isinstance(raw, Mapping):
            raise bad_model("Сетка модели не задана.")
        if raw.get("order", ORDER) != ORDER:
            raise bad_model("Порядок ячеек сетки должен быть «F» (i — быстрее всех).")
        g = check_grid({k: v for k, v in raw.items() if k in ("corner", "counts", "size", "tensors")}, MAX_GRID_V2)
        sub = SubblockSpec.of(raw.get("div"))
        t = g.get("tensors")
        if sub.sub and t:
            raise variable_grid("Субблоки")
        return cls(corner=(g["corner"][0], g["corner"][1], g["corner"][2]),
                   counts=(g["counts"][0], g["counts"][1], g["counts"][2]),
                   size=None if t else (g["size"][0], g["size"][1], g["size"][2]),
                   tensors=(tuple(t["x"]), tuple(t["y"]), tuple(t["z"])) if t else None,
                   rotation=parse_rotation(raw.get("rotation")), sub=sub)  # fmt: skip

    def to_meta(self) -> dict[str, Any]:
        out: dict[str, Any] = {"corner": list(self.corner), "counts": list(self.counts)}
        if self.tensors is not None:
            out["tensors"] = {a: list(t) for a, t in zip(AXES, self.tensors, strict=True)}
        else:
            out["size"] = list(self.size or ())
        return {**out, "order": ORDER, "rotation": self.rotation, "div": list(self.sub.div)}

    @property
    def cells(self) -> int:
        return self.counts[0] * self.counts[1] * self.counts[2]

    def regular(self, what: str = "Операция") -> tuple[float, float, float]:
        """The parent size; a variable step → 422 `variable_grid`."""
        if self.size is None:
            raise variable_grid(what)
        return self.size

    def subcell(self) -> tuple[float, float, float]:
        d, div = self.regular("Субблоки"), self.sub.div
        return (d[0] / div[0], d[1] / div[1], d[2] / div[2])

    def steps(self, axis: int) -> np.ndarray:
        """Parent steps along a LOCAL axis, `counts[axis]` values (float64)."""
        import numpy as np

        if self.tensors is not None:
            return np.asarray(self.tensors[axis], dtype=np.float64)
        return np.full(self.counts[axis], self.regular()[axis], dtype=np.float64)

    def edges(self, axis: int) -> np.ndarray:
        """Parent boundaries along a LOCAL axis, from 0: n + 1 values of q. A regular grid: k·size (a product, never
        a running sum); a variable one: the running sum of its steps."""
        import numpy as np

        if self.tensors is not None:
            return np.concatenate(([0.0], np.cumsum(np.asarray(self.tensors[axis], dtype=np.float64))))
        return np.arange(self.counts[axis] + 1, dtype=np.float64) * self.regular()[axis]

    def lengths(self) -> tuple[float, float, float]:
        """The length of the grid along the local axes: size·count (regular) or the last edge (variable)."""
        if self.tensors is None:
            d = self.regular()
            return (d[0] * self.counts[0], d[1] * self.counts[1], d[2] * self.counts[2])
        return (float(self.edges(0)[-1]), float(self.edges(1)[-1]), float(self.edges(2)[-1]))

    def sin_cos(self) -> tuple[float, float]:
        return _sin_cos(self.rotation)

    def matrix(self) -> list[list[float]]:
        """R_z(φ) by rows: p − O = R·q."""
        s, c = self.sin_cos()
        return [[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]]

    def to_project(self, q: Any) -> np.ndarray:
        """Local n×3 → project n×3, element by element (no BLAS: the same bits at any thread count)."""
        import numpy as np

        q = np.asarray(q, dtype=np.float64).reshape(-1, 3)
        s, c = self.sin_cos()
        out = np.empty_like(q)
        out[:, 0] = self.corner[0] + (c * q[:, 0] - s * q[:, 1])
        out[:, 1] = self.corner[1] + (s * q[:, 0] + c * q[:, 1])
        out[:, 2] = self.corner[2] + q[:, 2]
        return out

    def to_local(self, p: Any) -> np.ndarray:
        """Project n×3 → local n×3: q = R_z(−φ)·(p − O)."""
        import numpy as np

        p = np.asarray(p, dtype=np.float64).reshape(-1, 3)
        s, c = self.sin_cos()
        dx, dy = p[:, 0] - self.corner[0], p[:, 1] - self.corner[1]
        out = np.empty_like(p)
        out[:, 0] = c * dx + s * dy
        out[:, 1] = c * dy - s * dx
        out[:, 2] = p[:, 2] - self.corner[2]
        return out

    def extent(self) -> list[float]:
        """[xmin, ymin, zmin, xmax, ymax, zmax] of the turned grid in the project axes (`objects.extent`)."""
        (lx, ly, lz), (s, c) = self.lengths(), self.sin_cos()
        xs = [self.corner[0] + (c * x - s * y) for x in (0.0, lx) for y in (0.0, ly)]
        ys = [self.corner[1] + (s * x + c * y) for x in (0.0, lx) for y in (0.0, ly)]
        return [min(xs), min(ys), self.corner[2], max(xs), max(ys), self.corner[2] + lz]


def sort_rows(p: Any, s: Any | None = None) -> np.ndarray:
    """The order of the rows of the files (decision 3): (P, sz, sy, sx) ascending — a stable argsort; `s` — n×3."""
    import numpy as np

    p = np.asarray(p, dtype=np.uint64)
    if s is None:
        return np.argsort(p, kind="stable")
    s = np.asarray(s).reshape(-1, 3)
    return np.lexsort((s[:, 0], s[:, 1], s[:, 2], p))


def ascending(p: Any, s: Any | None = None) -> bool:
    """The rows go by (P, sz, sy, sx) strictly ascending (no repeats)."""
    import numpy as np

    p = np.asarray(p, dtype=np.uint64)
    if len(p) < 2:
        return True
    if s is None:
        return bool((p[1:] > p[:-1]).all())
    s = np.asarray(s, dtype=np.int64).reshape(-1, 3)
    key = (s[:, 2] << 16) | (s[:, 1] << 8) | s[:, 0]  # s < 128 per axis
    return bool(((p[1:] > p[:-1]) | ((p[1:] == p[:-1]) & (key[1:] > key[:-1]))).all())


def new_field_file() -> str:
    """The blob role of a new file of fields: `fld:` + 8 hex digits (prefix `fld:` of db/schema.py)."""
    return FLD + secrets.token_hex(4)
