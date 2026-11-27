"""The parts of the import of a block file (`object_blockfile.py`): the answers of the wizard (`BlockOptions`), the
columns found and chosen (`resolve`), the plan of the fields (`plan_fields`), the labels of a category over the whole
file, the spool of raw columns on the disk and `busy` — a long call that keeps ticking (ТЗ 7.31, 8.7.2; ADR 0023 §4).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.blocks import schema as bs
from bf_core.blocks.grid_infer import GridHint
from bf_core.formats import blocks_roles as br
from bf_core.formats.blocks_text import TextColumn

Tick = Callable[[float, str | None], None]
GEOMETRY = br.GEOMETRY


def _err(code: str, message: str, hint: str, extra: dict[str, Any] | None = None) -> ApiError:
    return ApiError(422, code, message, hint, extra=extra)


def _n(x: int) -> str:
    return f"{x:,}".replace(",", " ")


# --- the answers of the wizard -------------------------------------------------------------------------------------


def _triple(value: Any, label: str, cast: Callable[[Any], Any] = float) -> tuple[Any, Any, Any] | None:
    if value is None or value == "":
        return None
    try:
        a, b, c = (cast(v) for v in value)
    except (TypeError, ValueError):
        raise _err("blocks_options", f"{label}: нужны три числа.",
                   "Проверьте начало, размер и число ячеек сетки.") from None  # fmt: skip
    return (a, b, c)


@dataclass
class BlockOptions:
    """`options[<путь>].blocks` (docs/progress/e17-api.md §5) and the encoding and delimiter of a text file. `roles`
    — the columns the user chose (a role absent or None is found by name, "" — there is no such column); `grid` —
    corner, size, counts, rotation, div (what is absent is read from the file or inferred); `centres` — `project` |
    `model`; `empty` — the codes of «пусто»; `fields` — {column: {unit, role, skip}}."""

    roles: dict[str, str | None] = field(default_factory=dict)
    grid: dict[str, Any] = field(default_factory=dict)
    centres: str = "project"
    empty: list[float] = field(default_factory=list)
    fields: dict[str, dict[str, Any]] = field(default_factory=dict)
    encoding: str | None = None
    delimiter: str | None = None
    name: str | None = None

    @classmethod
    def of(
        cls, blocks: Mapping[str, Any] | None, *, encoding: str | None = None, name: str | None = None
    ) -> BlockOptions:
        from bf_core.formats.numbers import parse_value

        raw = dict(blocks or {})
        roles = {str(k): (None if v is None else str(v)) for k, v in dict(raw.get("roles") or {}).items()}
        if set(roles) - set(GEOMETRY):
            raise _err("blocks_options", f"Неизвестная роль столбца: {sorted(set(roles) - set(GEOMETRY))[0]}.",
                       "Роли столбцов: x, y, z, dx, dy, dz.")  # fmt: skip
        centres = str(raw.get("centres") or "project")
        if centres not in ("project", "model"):
            raise _err("blocks_options", f"Оси центров «{centres}» не подходят.", "Выберите оси проекта или модели.")
        empty = []
        for v in raw.get("empty") or []:
            value, _ = parse_value(v)
            if value is None:
                raise _err("blocks_options", f"Код пустого «{v}» не число.", "Введите числа, например -99.")
            empty.append(float(value))
        fields = {str(k): dict(v or {}) for k, v in dict(raw.get("fields") or {}).items()}
        return cls(roles, dict(raw.get("grid") or {}), centres, empty, fields, raw.get("encoding") or encoding,
                   raw.get("delimiter") or None, raw.get("name") or name)  # fmt: skip

    def hint(self, base: GridHint | None) -> GridHint:
        """The hint of the file (its grid line, a Datamine header) overridden by what the wizard says."""
        g, b = self.grid, base or GridHint()
        rotation = g.get("rotation")
        return GridHint(
            corner=_triple(g.get("corner"), "Начало сетки") or b.corner,
            size=_triple(g.get("size"), "Размер родителя") or b.size,
            counts=_triple(g.get("counts"), "Число ячеек", int) or b.counts,
            rotation=b.rotation if rotation in (None, "") else float(rotation),
            div=_triple(g.get("div"), "Деление на субблоки", int) or b.div,
            notes=b.notes,
        )


@dataclass
class Resolved:
    geometry: dict[str, str | None]  # x y z dx dy dz → column
    fields: list[str]  # the data fields, in the order of the file
    skip: list[str]

    @property
    def sized(self) -> bool:
        return all(self.geometry["d" + a] for a in "xyz")


def resolve(names: Sequence[str], kinds: Mapping[str, str], opts: BlockOptions) -> Resolved:
    roles = br.classify(names)
    geometry = dict(roles.geometry)
    for role, column in opts.roles.items():
        if column is None:
            continue
        if column == "":
            geometry[role] = None
        elif column not in names:
            raise _err("blocks_columns", f"Столбца «{column}» в файле нет.", "Выберите столбец из списка файла.")
        else:
            geometry[role] = column
    missing = [a.upper() for a in "xyz" if not geometry[a]]
    if missing:
        raise _err("blocks_columns", f"Не найдены столбцы центра блока: {', '.join(missing)}.",
                   "Укажите столбцы центров (X, Y, Z; XC, YC, ZC; EAST, NORTH, RL) в мастере импорта.")  # fmt: skip
    sizes = [geometry["d" + a] for a in "xyz"]
    if any(sizes) and not all(sizes):
        raise _err("blocks_columns", "Размеры блоков заданы не по всем осям.",
                   "Укажите столбцы размеров по X, Y и Z или уберите их все: тогда модель регулярная.")  # fmt: skip
    for role, column in geometry.items():
        if column and kinds.get(column) != "number":
            raise _err("blocks_columns", f"Столбец «{column}» ({role}) должен быть числовым.",
                       "Выберите столбец с числами.")  # fmt: skip
    taken = {c for c in geometry.values() if c}
    auto = set(roles.skip) - {c for c in roles.geometry.values() if c}
    skip = [n for n in names if n in auto | taken]
    fields = [n for n in names if n not in skip and not opts.fields.get(n, {}).get("skip")]
    return Resolved(geometry, fields, skip + [n for n in names if n not in skip and n not in fields])


class _Labels:
    """The labels of a category over the whole file, in the order they come; codes of a portion → codes of the field."""

    def __init__(self, name: str) -> None:
        self.name, self.items, self._index = name, [], {}  # type: ignore[var-annotated]

    def map(self, tc: TextColumn, empty: np.ndarray) -> np.ndarray:
        table = np.full(len(tc.labels) + 1, -1, dtype=np.int32)  # the last slot answers for the code −1
        for i, label in enumerate(tc.labels):
            if empty[i]:
                continue
            j = self._index.get(label)
            if j is None:
                j = self._index[label] = len(self.items)
                self.items.append(label)
            table[i] = j
        if len(self.items) > bs.MAX_LABELS_V2:
            raise _err("too_many_labels", f"У поля «{self.name}» больше {_n(bs.MAX_LABELS_V2)} разных значений.",
                       "Это не категория: исключите столбец («пропустить») или объедините значения в исходной "
                       "программе.")  # fmt: skip
        return table[tc.codes]  # code −1 → the last slot


@dataclass
class FieldPlan:
    column: str
    name: str
    kind: str  # number | category
    text: bool  # the column of the file is text
    unit: str | None
    role: str | None
    key: str
    labels: _Labels | None = None
    emptied: int = 0
    guess: str | None = None  # the role the name suggests, before «one density and one fraction a model»
    lo: float = np.inf  # the least and the greatest value read (a number field whose `guess` is in `WATCHED`)
    hi: float = -np.inf

    def spec(self, source: Mapping[str, Any]) -> dict[str, Any]:
        dtype = "cat" if self.kind == "category" else "f8"
        out: dict[str, Any] = {"name": self.name, "kind": self.kind, "dtype": dtype, "unit": self.unit,
                               "role": self.role, "source": source}  # fmt: skip
        if self.labels is not None:
            out["labels"] = list(self.labels.items)
        return out


def plan_fields(res: Resolved, kinds: Mapping[str, str], opts: BlockOptions) -> list[FieldPlan]:
    plans: list[FieldPlan] = []
    taken: set[str] = set()
    for i, (column, name) in enumerate(zip(res.fields, bs.field_names(res.fields), strict=True)):
        choice = opts.fields.get(column, {})
        text = kinds[column] == "text"
        role = choice["role"] if "role" in choice else br.field_role(column, "category" if text else "number")
        kind = "category" if text or role in bs.CATEGORY_ROLES else "number"
        if role is not None and role not in (bs.CATEGORY_ROLES if kind == "category" else bs.NUMBER_ROLES):
            raise _err("blocks_options", f"Поле «{name}»: роль «{role}» не подходит к его типу.",
                       "Число — содержание, плотность или доля объёма; текст — код или класс.")  # fmt: skip
        guess = role
        if role in ("density", "frac") and role in taken:
            role = None  # one density and one fraction a model; the first field with the name keeps the role
        taken.add(role or "")
        unit = choice["unit"] if "unit" in choice else (None if kind == "category" else br.guess_unit(column))
        labels = _Labels(name) if kind == "category" else None
        plans.append(FieldPlan(column, name, kind, text, unit, role, f"f{i}", labels, guess=guess))
    return plans


FRAC_TOP = 1.0 + 1e-9  # as `blocks.fields_ops._check_role`: a share of the volume lies in 0…1
WATCHED = ("frac", "density", "grade")  # the roles the values of a field can refuse (`check_roles`)


def check_roles(plans: Sequence[FieldPlan], chosen: Mapping[str, dict[str, Any]]) -> list[str]:
    """The roles the values of the file refuse, once it is read (the wizard of the import shows the same by its
    sample, `object_blockfile._inspect`); a role the user chose himself is left as it is. Lines for the journal.

      * `frac` — a field named as the share of the volume (FRAC) whose values are not shares: percents 0…100, a
        code of «пусто» left as a number (−99). The share multiplies the volume and the tonnage of every block
        (checks, report, regularization), so a field that is not a share stays a plain number and the volume of a
        block is its size (`fields_ops._check_role` refuses the role on an edit the same way);
      * `density`, `grade` — a constant field (ТЗ 7.31: «постоянное DENSITY = 2,6 пропускается»): it tells the
        blocks nothing apart, and the defaults of the report and of the resources skip a constant field anyway.
    """
    notes: list[str] = []
    for p in plans:
        if p.role not in WATCHED or "role" in chosen.get(p.column, {}) or not _fits(p.role, p):
            continue
        if p.role == "frac":
            notes.append(f"Поле «{p.name}» не принято за долю объёма блока: его значения ({p.lo:g} … {p.hi:g}) выходят "
                         "за 0…1. Объём блоков взят по их размерам; если это доля — исправьте значения (например, "
                         "укажите код пустого) и загрузите модель заново.")  # fmt: skip
        else:
            what = "плотность" if p.role == "density" else "содержание"
            notes.append(f"Поле «{p.name}» постоянное ({p.lo:g}): роль «{what}» ему не присвоена. "
                         "Задайте роль в свойствах поля, если она нужна.")  # fmt: skip
        p.role = None
    for role in ("density", "frac"):  # the role of the model goes to another candidate the names found
        if any(p.role == role for p in plans):
            continue
        for p in _candidates(role, plans):
            if "role" not in chosen.get(p.column, {}) and not _fits(role, p):
                p.role = role
                break
    return notes


def _fits(role: str | None, p: FieldPlan) -> bool:
    """The values read refuse this role: a share outside 0…1, a constant density or grade. A field without a single
    value (`hi < lo`) refuses nothing — an empty field keeps the role of its name."""
    if role not in WATCHED or p.hi < p.lo:
        return False
    if role == "frac":
        return p.lo < 0.0 or p.hi > FRAC_TOP
    return bool(p.lo == p.hi)


def _candidates(role: str, plans: Sequence[FieldPlan]) -> list[FieldPlan]:
    """The fields whose names suggest `role`: a field named SG first (ТЗ 7.31 «меняющееся поле SG»), then file
    order."""
    found = [p for p in plans if p.guess == role and p.kind == "number"]
    return sorted(found, key=lambda p: (p.name.casefold() != "sg", plans.index(p)))


# --- the spool ---------------------------------------------------------------------------------------------------


def unmap(mm: Any) -> None:
    inner = getattr(mm, "_mmap", None)
    if inner is not None:
        inner.close()  # on Windows an open map keeps the file locked


class Spool:
    """Raw columns in the order of the file, one file each (float64, or int32 for the codes of a category)."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self._files: dict[str, Any] = {}
        self.dtypes: dict[str, np.dtype[Any]] = {}

    def put(self, key: str, values: np.ndarray) -> None:
        if key not in self._files:
            self._files[key] = (self.folder / f"{key}.bin").open("wb")
            self.dtypes[key] = values.dtype
        np.ascontiguousarray(values).tofile(self._files[key])

    def flush(self) -> None:
        for fh in self._files.values():
            fh.close()
        self._files.clear()

    def read(self, key: str, start: int, stop: int) -> np.ndarray:
        """Rows [start, stop) of a column, read from the file (no mapped pages pile up in the memory of the process)."""
        dtype = self.dtypes[key]
        return np.fromfile(self.folder / f"{key}.bin", dtype=dtype, count=stop - start, offset=start * dtype.itemsize)

    def take(self, key: str, rows: int, ordered: np.ndarray, order: np.ndarray) -> np.ndarray:
        """The values at the raw rows `ordered` (ascending: one pass), put back in the order of the request."""
        mm = np.memmap(self.folder / f"{key}.bin", dtype=self.dtypes[key], mode="r", shape=(rows,))
        try:
            got = np.array(mm[ordered])
        finally:
            unmap(mm)
        out = np.empty_like(got)
        out[order] = got
        return out


def busy(fn: Callable[[], Any], tick: Tick, at: float, message: str) -> Any:
    """`fn` in a thread while the job ticks (a sort of 20 million rows takes seconds; a cancel arrives in `tick`)."""
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(fn)
    try:
        while True:
            try:
                return future.result(timeout=0.5)
            except FutureTimeout:
                tick(at, message)
    finally:
        pool.shutdown(wait=False)


