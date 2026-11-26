"""The pit slope template (подшаг 21.3; ТЗ 8.11.2; ADR 0028 decisions 9–12): from the angle tables to the offsets of the
arcs «a block needs the blocks above it» and the check of the angle (`pit.check`, the graph of 21.4).

    general = angle_table([(0.0, 40.0), (180.0, 50.0)])            # (azimuth of the centre, angle), degrees
    tpl = build_template(dx, dy, dz, general, [("Окисленные", angle_table([(0.0, 35.0)]))], k=None, rotation=30.0)
    tpl.k, tpl.tables[i].offsets, tpl.tables[i].level(m), tpl.table_index("Окисленные"), tpl.warnings

Angle table: up to 16 rows (azimuth of the centre, angle 10…80°), the azimuth is from north clockwise in the PROJECT
axes; between the centres φ(az) is linear round the circle; one row — a constant angle. The tables: `tables[0]` is the
general one (the default), `tables[1…]` — one per domain in the order given (≤ 16); a node of the graph takes the table
of the domain of its LOWER block (`table_index(label)`; a domain without a table, `None` — 0). The same K for all.

An offset (Δi, Δj, m), m = 1…K, int32, in the canonical order (m, Δj, Δi): the block (i, j, k) needs the block
(i + Δi, j + Δj, k + m) — UP (the local z grows with k: `GridV2.to_project` adds q_z to the corner, no flip). Δi, Δj
are the LOCAL grid steps (the grid may be turned by φ about Z); the azimuth of the offset is taken in the project axes,
so a turn by φ is a shift of the azimuths by φ (a model turned by 30° = the table with the centres + 30°).

Kernels — `template_kernels.py`. Errors — `ApiError` 422 `bad_param {param}` in Russian; a too large angle error is a
WARNING (`warnings`, `{code: "angle_error", domain, error, tolerance, message, hint}`), never a refusal.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.sections.geometry import sin_cos_deg

from . import template_kernels as tk

MAX_ROWS = 16  # sectors of a table
MAX_DOMAINS = 16
ANGLE_MIN, ANGLE_MAX = 10.0, 80.0  # degrees (ADR 0028 decision 10)
K_MIN_AUTO, K_MAX = 8, 20
REACH_CELLS = 8.0  # auto K: K·dz / (min(dx, dy)·tan φ_max) ≥ 8 (ТЗ 8.11.2)
TOLERANCE_DEFAULT = 2.5
TOLERANCE_RANGE = (0.5, 10.0)
AZ_STEP = 1.0  # φ_эфф is computed every degree
Tick = Callable[[float | None, str | None], None]


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


def bad_param(param: str, message: str, hint: str = "Проверьте углы бортов.") -> ApiError:
    return ApiError(422, "bad_param", message, hint, {"param": param})


def _ru(x: float, digits: int = 2) -> str:
    return f"{x:.{digits}f}".replace(".", ",")


@dataclass(frozen=True)
class AngleTable:
    """The sectors of a table: `azimuths` sorted in [0; 360), `angles` — the degrees at the centres."""

    azimuths: tuple[float, ...]
    angles: tuple[float, ...]

    @property
    def phi_max(self) -> float:
        return max(self.angles)

    @property
    def phi_min(self) -> float:
        return min(self.angles)

    def arrays(self) -> tuple[np.ndarray, np.ndarray]:
        return np.asarray(self.azimuths, dtype=np.float64), np.asarray(self.angles, dtype=np.float64)

    def phi(self, az: float) -> float:
        cs, an = self.arrays()
        return float(tk.phi_at(float(az), cs, an))

    def to_json(self) -> list[dict[str, float]]:
        return [{"azimuth": a, "angle": p} for a, p in zip(self.azimuths, self.angles, strict=True)]


HINT_ROW = "Укажите азимут от севера по часовой и угол в градусах."


def angle_table(rows: Sequence[Any], param: str = "slopes") -> AngleTable:
    """A table of up to 16 rows; a row is a pair (azimuth, angle) or a mapping {azimuth | az, angle}. The azimuth is
    reduced to [0; 360) and the rows are sorted; two rows of one azimuth, an angle outside 10…80° or no rows → 422."""
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence) or not 1 <= len(rows) <= MAX_ROWS:
        raise bad_param(
            param,
            f"В таблице углов от 1 до {MAX_ROWS} строк.",
            "Оставьте одну строку (постоянный угол) или до 16 секторов.",
        )
    pairs: list[tuple[float, float]] = []
    for r in rows:
        try:
            if isinstance(r, Mapping):
                az = r["azimuth"] if "azimuth" in r else r["az"]
                ang = r["angle"]
            else:
                az, ang = r
            a, p = float(az), float(ang)
        except (KeyError, TypeError, ValueError):
            raise bad_param(param, "Строка таблицы углов — азимут центра и угол, числа.", HINT_ROW) from None
        if not (math.isfinite(a) and math.isfinite(p)):
            raise bad_param(param, "Азимут и угол — конечные числа.", HINT_ROW)
        if not ANGLE_MIN <= p <= ANGLE_MAX:
            raise bad_param(
                param,
                f"Угол борта {_ru(p, 1)}° вне диапазона {ANGLE_MIN:g}…{ANGLE_MAX:g}°.",
                "Угол борта — от 10° до 80° от горизонтали.",
            )
        pairs.append((a % 360.0, p))
    pairs.sort()
    if len(pairs) > 1:
        for (a0, _), (a1, _) in zip(pairs, pairs[1:] + pairs[:1], strict=True):
            if abs((a1 - a0) % 360.0) < 1e-9:
                raise bad_param(
                    param,
                    f"Два сектора с одним азимутом центра ({_ru(a0, 1)}°).",
                    "Азимуты центров секторов не должны совпадать.",
                )
    return AngleTable(tuple(a for a, _ in pairs), tuple(p for _, p in pairs))


@dataclass(frozen=True)
class DomainTemplate:
    """The template of one table: `offsets` (n, 3) int32 (Δi, Δj, m); `level_ptr` (K + 1) int32 — the level m is the
    rows [level_ptr[m − 1]; level_ptr[m]); `phi` and `phi_eff` — φ(az) and φ_эфф(az) at az = 0°…359° (project axes);
    `err_max`, `err_mean` — max and mean of abs(φ_эфф − φ) over the 360 azimuths, degrees."""

    key: Hashable | None
    table: AngleTable
    offsets: np.ndarray
    level_ptr: np.ndarray
    phi: np.ndarray
    phi_eff: np.ndarray
    err_max: float
    err_mean: float

    def level(self, m: int) -> np.ndarray:
        """The offsets (Δi, Δj, m) of the level m (1…K), a view."""
        return self.offsets[int(self.level_ptr[m - 1]) : int(self.level_ptr[m])]


@dataclass(frozen=True)
class PitTemplate:
    """The whole template: `tables[0]` — the general one, `tables[1:]` — the domains (`keys` — their labels)."""

    dx: float
    dy: float
    dz: float
    rotation: float
    k: int
    k_auto: bool
    tolerance: float
    tables: tuple[DomainTemplate, ...]
    warnings: tuple[dict[str, Any], ...] = ()
    keys: tuple[Hashable, ...] = field(default=())

    def table_index(self, key: Hashable | None) -> int:
        """The index of the table of a domain label; a label without a table (or None) — the general table, 0."""
        try:
            return self.keys.index(key) + 1 if key is not None else 0
        except ValueError:
            return 0

    @property
    def err_max(self) -> float:
        return max(t.err_max for t in self.tables)

    def signature(self) -> str:
        """sha256 of the inputs and the offsets — the same template, the same hash (the scenario keeps it)."""
        h = hashlib.sha256()
        h.update(np.asarray([self.dx, self.dy, self.dz, self.rotation, self.k], dtype=np.float64).tobytes())
        for t in self.tables:
            h.update(np.asarray(t.table.azimuths + t.table.angles, dtype=np.float64).tobytes())
            h.update(np.ascontiguousarray(t.offsets).tobytes())
        return h.hexdigest()

    def to_json(self, *, with_offsets: bool = True) -> dict[str, Any]:
        """JSON of `pit.check` and of `sidecar:template`: K, errors, φ_эфф(az) — a list per 360 azimuths per table."""
        tables = []
        for t in self.tables:
            row: dict[str, Any] = {
                "domain": t.key,
                "slopes": t.table.to_json(),
                "count": int(t.offsets.shape[0]),
                "err_max": t.err_max,
                "err_mean": t.err_mean,
                "phi": t.phi.tolist(),
                "phi_eff": t.phi_eff.tolist(),
            }
            if with_offsets:
                row["offsets"] = t.offsets.tolist()
            tables.append(row)
        return {
            "k": self.k,
            "k_auto": self.k_auto,
            "tolerance": self.tolerance,
            "rotation": self.rotation,
            "steps": [self.dx, self.dy, self.dz],
            "signature": self.signature(),
            "tables": tables,
            "warnings": [dict(w) for w in self.warnings],
        }


def auto_k(dx: float, dy: float, dz: float, phi_max: float) -> int:
    """The least K in 8…20 with K·dz / (min(dx, dy)·tan φ_max) ≥ 8 (the cap — 20)."""
    need = REACH_CELLS * min(dx, dy) * math.tan(math.radians(phi_max)) / dz
    return int(min(K_MAX, max(K_MIN_AUTO, math.ceil(need - 1e-9))))


def _steps(dx: float, dy: float, dz: float) -> None:
    for name, v in (("dx", dx), ("dy", dy), ("dz", dz)):
        if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0.0):
            raise bad_param(name, "Шаг сетки должен быть положительным числом.", "Проверьте размер блока модели.")


def _one(
    key: Hashable | None, table: AngleTable, dx: float, dy: float, dz: float, sn: float, cs_: float, k: int
) -> DomainTemplate:
    cs, an = table.arrays()
    reach = dz / math.tan(math.radians(table.phi_min))  # the longest horizontal reach of a level
    wi = int(math.ceil(k * reach / dx - 1e-9)) + 1
    wj = int(math.ceil(k * reach / dy - 1e-9)) + 1
    dist, rh = tk.cone_grid(dx, dy, dz, sn, cs_, cs, an, wi, wj)
    offs, ptr = tk.build_offsets(dist, rh, k, wi, wj)
    phi_eff = tk.effective_angles(offs, ptr, dx, dy, dz, sn, cs_, k)
    phi = tk.phi_row(cs, an)
    err = np.abs(phi_eff - phi)
    offs = np.ascontiguousarray(offs)
    return DomainTemplate(key, table, offs, ptr, phi, phi_eff, float(err.max()), float(err.mean()))


def build_template(
    dx: float,
    dy: float,
    dz: float,
    general: AngleTable,
    domains: Sequence[tuple[Hashable, AngleTable]] | Mapping[Hashable, AngleTable] = (),
    *,
    k: int | None = None,
    rotation: float = 0.0,
    tolerance: float = TOLERANCE_DEFAULT,
    tick: Tick | None = None,
) -> PitTemplate:
    """The template of a regular grid with the parent steps dx, dy, dz turned by `rotation` degrees about Z.

    `k` — None: auto 8…20 by the steepest angle of all the tables; else 1…20. `tolerance` — 0,5…10°: the error of a
    table above it gives a warning (not a refusal). `tick(share, text)` is called once per distinct table (it raises
    to cancel). Equal tables are computed once; the result does not depend on anything but the arguments (the same
    bits on repeat)."""
    tick = tick or _noop
    _steps(dx, dy, dz)
    if not isinstance(rotation, (int, float)) or not math.isfinite(rotation):
        raise bad_param("rotation", "Поворот модели — число.", "Проверьте сетку модели.")
    if not (isinstance(tolerance, (int, float)) and TOLERANCE_RANGE[0] <= tolerance <= TOLERANCE_RANGE[1]):
        raise bad_param("tolerance", "Допуск угла — от 0,5° до 10°.", "Допуск угла по умолчанию — 2,5°.")
    items = list(domains.items()) if isinstance(domains, Mapping) else list(domains)
    if len(items) > MAX_DOMAINS:
        raise bad_param(
            "domains",
            f"Доменов с собственными углами не больше {MAX_DOMAINS}.",
            "Объедините домены или оставьте общую таблицу.",
        )
    keys = [key for key, _ in items]
    if len(set(keys)) != len(keys):
        raise bad_param("domains", "Подпись домена повторяется.", "У каждого домена — одна таблица углов.")
    tables = [general] + [t for _, t in items]
    k_auto = k is None
    if k is None:
        k = auto_k(dx, dy, dz, max(t.phi_max for t in tables))
    elif isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= K_MAX:
        raise bad_param("k", f"K — целое от 1 до {K_MAX}.", "Оставьте автоматический выбор K (8…20).")
    sn, cs_ = sin_cos_deg(float(rotation))
    done: dict[AngleTable, DomainTemplate] = {}
    out: list[DomainTemplate] = []
    labels: list[Hashable | None] = [None] + keys
    for idx, (key, table) in enumerate(zip(labels, tables, strict=True)):
        tick(idx / len(tables), "Шаблон")
        base = done.get(table)
        if base is None:
            base = done[table] = _one(key, table, dx, dy, dz, sn, cs_, k)
        out.append(base if base.key == key else replace(base, key=key))  # a shared table is computed once
    warns = tuple(_warning(t, tolerance, k) for t in out if t.err_max > tolerance)
    tick(1.0, "Шаблон")
    steps = (float(dx), float(dy), float(dz))
    return PitTemplate(*steps, float(rotation), int(k), k_auto, float(tolerance), tuple(out), warns, tuple(keys))


def _warning(t: DomainTemplate, tolerance: float, k: int) -> dict[str, Any]:
    hint = (
        "Увеличьте K (автоматический выбор — 8…20)."
        if k < K_MAX
        else "K уже наибольший (20): увеличьте допуск угла или регуляризуйте модель с меньшим шагом по X и Y."
    )
    where = "" if t.key is None else f" (домен «{t.key}»)"
    return {
        "code": "angle_error",
        "domain": t.key,
        "error": t.err_max,
        "tolerance": tolerance,
        "message": f"Ошибка угла {_ru(t.err_max)}° больше допуска{where}: увеличьте K",
        "hint": hint,
    }


def azimuth_of(di: float, dj: float, dx: float, dy: float, rotation: float = 0.0) -> float:
    """The project azimuth (degrees from north clockwise, [0; 360)) of the offset (Δi, Δj) of a grid turned by
    `rotation` — the same as the kernels use (for the tests and for 21.4's checks of a single arc)."""
    sn, cs_ = sin_cos_deg(float(rotation))
    a, b = di * dx, dj * dy
    az = math.degrees(math.atan2(cs_ * a - sn * b, sn * a + cs_ * b))
    return az + 360.0 if az < 0.0 else az
