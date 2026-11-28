"""The description of a block model — `objects.meta` v2 (ADR 0023 §2, decisions 2–5; the forms — `schema.py` and
docs/progress/e17-api.md «Модель»): `upgrade` checks a stored meta and turns a v1 meta into v2 without losses
(rows = parents = `cells`, no sub-blocks, every field in the file `cells`, f8 | cat); `public_meta` — the part of
`GET /blocks[/{id}]` the meta gives; `files_of` — the blobs a reader opens. No numpy here: the grid is `GridV2`.

meta v3 (ADR 0030 decision 8, Д-45 А6) is a v2 meta with `layout: "list"` — a block model that does not lie in any
grid, kept as a list of boxes (`blocks/list_layout.py`): the grid is the enclosing one (`div` [1, 1, 1], `sub` false),
`parents` — the occupied cells of the centres (≤ `rows`: several blocks may have the centre in one cell). v1 and v2
are `layout: "grid"`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .schema import (
    CATEGORY,
    CATEGORY_ROLES,
    DTYPES,
    FIELD_OPS,
    FLD_RE,
    MAX_DESCRIPTION,
    MAX_EXAMPLES,
    META_V2,
    MODEL_OPS,
    NUMBER_ROLES,
    ROLE,
    SHA_RE,
    SUB_COLUMNS,
    GridV2,
    bad_model,
    check_fields,
)

META_V3 = 3
LAYOUT_GRID, LAYOUT_LIST = "grid", "list"
LIST_COLUMNS = ("cx", "cy", "cz", "dx", "dy", "dz")  # the centre and the size of a block of a list, local axes, f8


def layout_of(meta: Mapping[str, Any]) -> str:
    """`grid` (v1, v2) or `list` (v3) of a stored meta or a public one."""
    return LAYOUT_LIST if meta.get("layout") == LAYOUT_LIST else LAYOUT_GRID


def _choice(value: Any, allowed: Sequence[Any], label: str) -> Any:
    if value not in allowed:
        raise bad_model(f"{label}: неизвестное значение {value!r}.")
    return value


def _source(raw: Any, ops: Sequence[str], label: str) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise bad_model(f"{label}: источник задан неверно.")
    return {**raw, "op": _choice(raw.get("op"), ops, label)}


def check_field_v2(raw: Mapping[str, Any], *, sub: bool = False) -> dict[str, Any]:
    """One field of meta v2; a v1 field (no `dtype`) gets f8 | cat, the file `cells`, no role, no source."""
    base = check_fields([raw])[0]  # name, kind, unit, labels
    name, kind = base["name"], base["kind"]
    if sub and name.casefold() in SUB_COLUMNS:
        raise bad_model(f"Поле «{name}» совпадает со служебным столбцом субблоков.")
    dtype = raw.get("dtype") or ("cat" if kind == CATEGORY else "f8")
    if (dtype == "cat") != (kind == CATEGORY) or dtype not in DTYPES:
        raise bad_model(f"Поле «{name}»: тип хранения {dtype!r} не подходит к типу поля.")
    role = raw.get("role")
    if role is not None and role not in (CATEGORY_ROLES if kind == CATEGORY else NUMBER_ROLES):
        raise bad_model(f"Поле «{name}»: роль {role!r} не подходит к типу поля.")
    description = raw.get("description")
    description = " ".join(str(description).split())[:MAX_DESCRIPTION] or None if description is not None else None
    file = raw.get("file") or ROLE
    if file != ROLE and not FLD_RE.fullmatch(str(file)):
        raise bad_model(f"Поле «{name}»: файл {file!r} задан неверно.")
    stats = raw.get("stats")
    if stats is not None and not isinstance(stats, Mapping):
        raise bad_model(f"Поле «{name}»: статистика задана неверно.")
    src = _source(raw.get("source"), FIELD_OPS, f"Поле «{name}»")
    if src is not None and not all(isinstance(s, str) and SHA_RE.fullmatch(s) for s in src.get("inputs") or []):
        raise bad_model(f"Поле «{name}»: входы источника — не SHA-256.")
    out = {**base, "dtype": dtype, "description": description, "role": role,
           "stats": None if stats is None else dict(stats), "file": file, "source": src}  # fmt: skip
    return {k: out[k] for k in ("name", "kind", "dtype", "unit", "description", "role", *(("labels",) if
            kind == CATEGORY else ()), "stats", "file", "source")}  # fmt: skip


def _count_of(meta: Mapping[str, Any], key: str) -> int:
    value = meta.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise bad_model(f"Число строк модели ({key}) задано неверно.")
    return value


def upgrade(meta: Mapping[str, Any]) -> dict[str, Any]:
    """`objects.meta` v1 or v2 → checked meta v2 (idempotent). v1: rows = parents = `cells`, no sub-blocks,
    every field in the file `cells`, `source` {op: import, …v1 keys} (a v1 model without a source keeps null)."""
    if not isinstance(meta, Mapping) or meta.get("v", 1) not in (1, META_V2, META_V3):
        raise bad_model("Неизвестная версия описания блочной модели.", "Обновите программу.")
    listed = meta.get("v", 1) == META_V3
    if listed and meta.get("layout") != LAYOUT_LIST:
        raise bad_model("Описание блочной модели v3 без признака «список блоков».", "Обновите программу.")
    grid = GridV2.of(meta.get("grid"))
    raw_fields = meta.get("fields") or []
    if meta.get("v", 1) == 1:
        rows = _count_of(meta, "cells")
        src = meta.get("source")
        out: dict[str, Any] = {
            "v": META_V2, "grid": grid.to_meta(), "rows": rows, "parents": rows, "sub": False, "full": False,
            "fields": [check_field_v2(f) for f in raw_fields], "checks": None, "groups": None,
            "source": None if src is None else {"op": "import", **dict(src)},
            "warnings": [str(w) for w in meta.get("warnings") or []], "inputs": None, "algo": None,
        }  # fmt: skip
        return out
    rows, parents, sub, full = _count_of(meta, "rows"), _count_of(meta, "parents"), meta.get("sub"), meta.get("full")
    if not isinstance(sub, bool) or not isinstance(full, bool):
        raise bad_model("Признаки субблоков и прототипа заданы неверно.")
    if parents > rows or (not sub and parents != rows and not listed) or (sub and not grid.sub.sub) or (full and rows):
        raise bad_model("Число строк, родителей и субблоков модели не согласовано.")
    if listed and (sub or full or grid.sub.sub or grid.tensors is not None):
        raise bad_model("У списка блоков не бывает субблоков, прототипа и переменного шага сетки.")
    fields = [check_field_v2(f, sub=sub) for f in raw_fields]
    check_fields(fields)  # the names, once more over all the files
    if sum(f["role"] == "frac" for f in fields) > 1:
        raise bad_model("У модели больше одного поля доли объёма (FRAC).")
    groups = meta.get("groups")
    if groups is not None and (not isinstance(groups, Sequence) or sum(int(g["rows"]) for g in groups) != rows):
        raise bad_model("Группы строк модели не согласованы с числом строк.")
    checks = meta.get("checks")
    if checks is not None and (not isinstance(checks, Mapping) or len(checks.get("examples") or []) > MAX_EXAMPLES):
        raise bad_model("Проверки модели заданы неверно.")
    inputs = meta.get("inputs")
    if inputs is not None and (not isinstance(inputs, Mapping) or set(inputs) - {"wireframes", "model"}):
        raise bad_model("Входы модели заданы неверно.")
    algo = meta.get("algo")
    return {
        "v": META_V3 if listed else META_V2, **({"layout": LAYOUT_LIST} if listed else {}),
        "grid": grid.to_meta(), "rows": rows, "parents": parents, "sub": sub, "full": full,
        "fields": fields, "checks": None if checks is None else dict(checks),
        "groups": None if groups is None else [{k: int(g[k]) for k in ("rows", "k0", "k1")} for g in groups],
        "source": _source(meta.get("source"), MODEL_OPS, "Модель"),
        "warnings": [str(w) for w in meta.get("warnings") or []],
        "inputs": None if inputs is None else {k: [{"id": int(i["id"]), "sha": str(i["sha"])} for i in v]
                                               for k, v in inputs.items()},
        "algo": None if algo is None else str(algo),
    }  # fmt: skip


def grid_of(meta: Mapping[str, Any]) -> GridV2:
    """The grid of a stored meta (v1 or v2)."""
    return GridV2.of(meta.get("grid"))


def files_of(meta: Mapping[str, Any]) -> dict[str, list[str]]:
    """{file role: [field names]} of a meta (v1 or v2), `cells` first — the blobs a reader opens."""
    out: dict[str, list[str]] = {ROLE: []}
    for f in upgrade(meta)["fields"]:
        out.setdefault(f["file"], []).append(f["name"])
    return out


BRIEF_FIELD = ("name", "kind", "dtype", "unit", "role")


def public_meta(meta: Mapping[str, Any], *, brief: bool = False) -> dict[str, Any]:
    """The part of `GET /blocks[/{id}]` a meta gives (e17-api.md «Модель»): `meta_v` — the stored version, `cells`
    = `rows` (the name of Э11), the grid with `variable` and `levels` (null — not 2^L), `layout: "list"` of a list of
    blocks (ADR 0030; absent — a grid); `brief` (the tree) — no tensors, no checks, fields without stats, labels and
    sources."""
    m = upgrade(meta)
    grid = dict(m["grid"])
    grid["variable"] = "tensors" in grid
    levels = GridV2.of(m["grid"]).sub.levels
    grid["levels"] = None if levels is None else list(levels)  # None — a division that is not 2^L (ADR 0030)
    if brief:
        grid.pop("tensors", None)
    fields = [{k: f.get(k) for k in BRIEF_FIELD} for f in m["fields"]] if brief else m["fields"]
    out = {"meta_v": int(meta.get("v", 1)), "grid": grid, "cells": m["rows"], "rows": m["rows"],
           "parents": m["parents"], "sub": m["sub"], "full": m["full"], "fields": fields, "source": m["source"],
           "warnings": m["warnings"]}  # fmt: skip
    if layout_of(m) == LAYOUT_LIST:  # only a list of blocks says it: no `layout` is a grid (v1, v2)
        out["layout"] = LAYOUT_LIST
    if not brief:
        out.update(checks=m["checks"], groups=m["groups"], inputs=m["inputs"], algo=m["algo"])
    return out
