"""The regular block model of Э11 — the contract of its storage (ADR 0017 §3, decision 1). Э17 extends it
(sub-blocks, the table `block_models` filled from `meta.grid` by its migration, display, DAT/CSV, checks 7.31)
and does not rewrite the storage of regular cells.

Object: `objects(kind="block_model", state ready | building, job_id of the building job)`; `name` unique
among block models (case-insensitive, «Имя (2)»), `folder`, `crs`, `extent` [xmin, ymin, zmin, xmax, ymax, zmax]
of the whole grid (every cell, occupied or not), float64.

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

Files: one blob of the owner `object` with the role `cells` — Parquet (zstd, row groups of ROW_GROUP rows):
column `idx` uint64, strictly ascending, < nx·ny·nz, then one column per field in the order of `meta.fields`,
named by the field: number — float64 (NaN — empty), category — int32 code into `labels` (−1 — empty).
A cell not in the file is not in the model (outside the body, air). The source file of an import, if kept, is
a blob of an `import_log` row, as for the other objects.

API (`blocks/router.py`): `GET /blocks`, `GET /blocks/{id}`, `PATCH /blocks/{id}` {name, expected_version},
`DELETE /blocks/{id}`; changes publish `objects.changed {kind: "block_model", id[, deleted]}`.
Writing and reading: `blocks/store.py` (`CellsWriter`, `commit_block_model`, `iter_cells`, `read_cells`).

This module has no numpy at import time (tests/test_process.py): the array helpers import it inside.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping, Sequence
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


def check_grid(raw: Mapping[str, Any]) -> dict[str, Any]:
    """The grid of `meta.grid`, checked and normalised (plain floats and ints, `order: "F"`)."""
    if not isinstance(raw, Mapping):
        raise bad_model("Сетка модели не задана.")
    corner = [_finite(v, f"Угол модели {a.upper()}") for v, a in zip(_triple(raw.get("corner"), "Угол модели"), AXES)]
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
        grid["counts"] = [_count(v, a) for v, a in zip(_triple(raw.get("counts"), "Число ячеек"), AXES)]
        grid["size"] = [_step(v, a) for v, a in zip(_triple(size_raw, "Размер ячейки"), AXES)]
    n = math.prod(grid["counts"])
    if n > MAX_CELLS:
        raise ApiError(
            422,
            "block_model_too_large",
            f"В модели {n:,} ячеек — больше предела {MAX_CELLS:,}.".replace(",", " "),
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
