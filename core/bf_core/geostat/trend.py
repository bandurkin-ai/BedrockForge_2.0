"""The shape of a trend (ТЗ 7.35 Д-15; ADR 0022 §10, решение 20): the angles of GSLIB and the ratios of the axes.

A global trend is the anisotropy ellipsoid without the variance: three angles (`rotation.py`, ТЗ 8.6.3) and the
ratios `[1, a2 / a1, a3 / a1]` of its axes. Its matrix

    A = diag(1, a1 / a2, a1 / a3)·R = diag(1, 1 / r2, 1 / r3)·R

is `matrix` — the one source of A for the estimation (Э18, 8.8) and RBF (Э19, 8.9): ‖A·Δ‖ is the distance
along the trend (the major axis unchanged, the others stretched). `shape_of` is the way back (ТД15-1: 1e−12).

A trend made of a variogram model is a copy (вопрос № 56): the angles and the ratios of one structure (by default —
the one with the largest a1, the first of equal ones), the size shown — its a1. Editing the model does not change
the trend; «Обновить из модели» copies again. The structure is numbered from 1, as in the window (`variography.*`).

`kind: "structural"` (замеры, опорный каркас) — Э19; here it is refused with `not_ready`. Plain numpy, float64.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from .rotation import angles_of, bad_param, geostat_error, normalize_angles, number, positive, rotation_matrix

GLOBAL, STRUCTURAL = "global", "structural"
KINDS = (GLOBAL, STRUCTURAL)
RATIO_MAX = 1e6  # a ratio of the axes beyond 1 : 10⁶ is a typing error, not a trend
SIZE_MAX = 1e7  # metres: the major axis shown


@dataclass(frozen=True)
class Shape:
    """The angles (normalised as `rotation.normalize_angles`) and the ratios `(1, r2, r3)` of a trend."""

    ang1: float
    ang2: float
    ang3: float
    ratios: tuple[float, float, float]

    def as_dict(self) -> dict[str, Any]:
        return {"ang1": self.ang1, "ang2": self.ang2, "ang3": self.ang3, "ratios": list(self.ratios)}


def text_number(value: Any) -> Any:
    """A text «0,5» / «0.5» of a request as a number (e16-api.md «Общие правила»); anything else — as it is (the
    checks of `rotation.number` refuse it with its `param`)."""
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip().replace(",", "."))
        except ValueError:
            return value
    return value


def check_kind(value: Any) -> str:
    """`global` (also when absent); `structural` — 422 `not_ready` until Э19."""
    kind = GLOBAL if value is None or value == "" else value
    if kind == STRUCTURAL:
        raise geostat_error(
            "not_ready",
            "Структурный тренд (по замерам или опорному каркасу) будет доступен позже.",
            "Создайте глобальный тренд: углы и отношения осей.",
            "kind",
        )
    if kind != GLOBAL:
        raise bad_param("kind", f"Неизвестный вид тренда «{str(kind)[:40]}».", "Вид тренда — global.")
    return GLOBAL


def ratios_of(value: Any) -> tuple[float, float, float]:
    """`[1, r2, r3]`: three numbers, the first exactly 1, each > 0 and ≤ 10⁶; absent — `[1, 1, 1]`."""
    if value is None:
        return 1.0, 1.0, 1.0
    if not isinstance(value, Sequence) or isinstance(value, str) or len(value) != 3:
        raise bad_param("ratios", "Отношения осей — три числа: 1, a2 / a1, a3 / a1.", "Например: 1; 0,5; 0,2.")
    first = number(text_number(value[0]), "ratios.0", "Отношение большой оси")
    if first != 1.0:
        raise bad_param("ratios.0", "Первое отношение осей должно быть равно 1.", "Отношения считаются от a1.")
    out = [1.0]
    for i in (1, 2):
        r = positive(text_number(value[i]), f"ratios.{i}", f"Отношение a{i + 1} / a1")
        if r > RATIO_MAX:
            raise bad_param(f"ratios.{i}", f"Отношение a{i + 1} / a1 слишком велико.", "Допустимо до 1 000 000.")
        out.append(r)
    return out[0], out[1], out[2]


def shape(ang1: Any, ang2: Any, ang3: Any, ratios: Any = None) -> Shape:
    """A checked shape: `bad_param {param: "ang2" | "ratios.1" | …}`."""
    a, b, c = normalize_angles(text_number(ang1), text_number(ang2), text_number(ang3))
    return Shape(a, b, c, ratios_of(ratios))


def size_of(value: Any, default: float) -> float:
    """The length of the major axis shown, m: > 0 and ≤ 10⁷; absent — `default`."""
    if value is None or value == "":
        return float(default)
    x = positive(text_number(value), "size", "Размер тренда для показа")
    if x > SIZE_MAX:
        raise bad_param("size", "Размер тренда для показа слишком велик.", "Допустимо до 10 000 000 м.")
    return x


def centre_of(value: Any) -> list[float] | None:
    """The centre shown: three finite numbers (float64), or None — the interface places it."""
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, str) or len(value) != 3:
        raise bad_param("centre", "Центр тренда — три числа X, Y, Z.", "Поставьте центр в сцене или очистите поле.")
    return [number(text_number(v), f"centre.{i}", "Координата центра") for i, v in enumerate(value)]


def matrix(t: Shape | Mapping[str, Any]) -> np.ndarray:
    """A = diag(1, 1 / r2, 1 / r3)·R (ТЗ 8.6.3, 8.8, 8.9), 3×3 float64: the rows are u₁, u₂ / r2, u₃ / r3."""
    s = t if isinstance(t, Shape) else shape(t.get("ang1"), t.get("ang2", 0.0), t.get("ang3", 0.0), t.get("ratios"))
    r = rotation_matrix(s.ang1, s.ang2, s.ang3)
    _one, r2, r3 = s.ratios
    return np.ascontiguousarray(r * np.array([[1.0], [1.0 / r2], [1.0 / r3]]))


def shape_of(a: Any, ang1: Any = 0.0) -> Shape:
    """The way back: the angles and the ratios of a matrix A (`ang1` is kept when the major axis is vertical)."""
    m = np.asarray(a, dtype=np.float64)
    if m.shape != (3, 3) or not np.isfinite(m).all():
        raise bad_param("matrix", "Матрица тренда — 3 × 3 конечных числа.", "Проверьте матрицу.")
    norms = np.sqrt(np.einsum("ij,ij->i", m, m))
    if not (norms > 0.0).all():
        raise bad_param("matrix", "Матрица тренда вырождена.", "Проверьте матрицу.")
    a1, a2, a3 = angles_of(m / norms[:, None], ang1)
    return Shape(a1, a2, a3, (1.0, float(norms[0] / norms[1]), float(norms[0] / norms[2])))


# --- a copy of a variogram model ------------------------------------------------------------------------------------


def _range(s: Mapping[str, Any], key: str) -> float:
    x = s.get(key)
    if isinstance(x, bool) or not isinstance(x, int | float) or not math.isfinite(x) or x <= 0.0:
        raise geostat_error("bad_model", "Модель вариограммы повреждена: дальности структуры не заданы.",
                            "Откройте модель в окне вариографии и сохраните заново.", "model_id")  # fmt: skip
    return float(x)


def pick_structure(structures: Sequence[Mapping[str, Any]], structure: Any = None) -> int:
    """The number (from 1) of the structure to copy: the one given, or the one with the largest a1 (the first)."""
    if not structures:
        raise geostat_error("no_structures", "У модели вариограммы нет структур — тренд не из чего взять.",
                            "Добавьте структуру в модель или задайте тренд вручную.", "model_id")  # fmt: skip
    if structure is None or structure == "":
        best = max(range(len(structures)), key=lambda i: (_range(structures[i], "a1"), -i))
        return best + 1
    if isinstance(structure, bool) or not isinstance(structure, int | float) or structure != int(structure):
        raise bad_param("structure", "Номер структуры — целое число.", "Структуры нумеруются с 1.")
    n = int(structure)
    if not 1 <= n <= len(structures):
        raise bad_param("structure", f"У модели нет структуры № {n}.", f"Структуры модели — от 1 до {len(structures)}.")
    return n


def from_structure(s: Mapping[str, Any]) -> tuple[Shape, float]:
    """(the shape, the size shown = a1) of one structure of a stored model: its angles, `[1, a2 / a1, a3 / a1]`."""
    a1, a2, a3 = _range(s, "a1"), _range(s, "a2"), _range(s, "a3")
    out = shape(s.get("ang1", 0.0), s.get("ang2", 0.0), s.get("ang3", 0.0), [1.0, a2 / a1, a3 / a1])
    return out, a1
