"""The rule tree of a filter (ТЗ 7.35 Д-02, 8.7.5; ADR 0023 §8; contract — `docs/progress/e17-api.md` §13).

    meta v1: {v: 1, rules: Node, text: str | null, fields: [names], note: str | null}
    Node = {op: "and" | "or", items: [Node, …]} | {op: "not", items: [Node]} | {expr: "<8.7.4>"}
         | {cond: {field, op: "=" | "<>" | ">" | ">=" | "<" | "<=" | "empty" | "notempty" | "in", value? | values?}}
         | {wireframe: {id, side: "inside" | "outside", by: "frac" | "centre", m: 4}}
         | {surface: {id, side: "above" | "below", by: "centre" | "frac"}}
         | {slab: {axis: "x" | "y" | "z" | "u" | "v", from, to, by: "centre" | "share", frame?}}

`normalize_rules` checks a tree and returns its canonical form (defaults filled, numbers as numbers: «0,5» → 0.5;
nothing else is changed); a refusal is 422 `filter_rules {param: "rules.items.0.cond.op"}` in Russian, a wrong
expression is 422 `expr_syntax {param, pos, …}` (`calc.ExprError`). Limits: depth ≤ 32, nodes ≤ 500, `values`
1–1000, `expr` ≤ 4000 characters. The id of a wireframe or a surface may be null: the leaf the archive of a
`.bfggis` did not have (`remap.py`) — «недоступен», its share is 0 (`restrict.py`).

The leaf `slab` (Э20, ADR 0027 decision 9) is a layer between two planes: `axis` x, y, z — the axes of the project,
u, v — the axes of the swath frame of 8.8.3 (`stats/swath_frame`; `frame` {azimuth, dip, roll} in degrees, defaults 0,
only for u and v); `from` < `to` are numbers; `by` «centre» — 1 where from <= c < to, «share» — the share of the
height of the block inside [from; to], only for z. It names no object: `leaves`, `object_ids`, `remap_ids` pass it by.

`to_text` writes the whole tree as one expression of 8.7.4 (the canonical form of `calc`: upper-case words, the
fewest parentheses); null when the tree has a wireframe, a surface or a slab, or when the text would be longer than the
parser takes. `title_of` — the description in Russian that the windows show. Nothing here needs numpy.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterator, Mapping
from typing import Any

from bf_core.api.errors import ApiError
from bf_core.calc import MAX_DEPTH, MAX_LENGTH, Expr, ExprError, parse
from bf_core.calc import nodes as cn

VERSION = 1
MAX_RULE_DEPTH = 32
MAX_NODES = 500
MAX_VALUES = 1000
MAX_EXPR = MAX_LENGTH
MAX_FIELD = 100
MAX_LABEL = 200
MAX_NOTE = 2000
MAX_M = 16
DEFAULT_M = 4
GROUP_OPS = ("and", "or", "not")
COND_OPS = ("=", "<>", ">", ">=", "<", "<=", "empty", "notempty", "in")
ORDER_OPS = (">", ">=", "<", "<=")
NO_VALUE_OPS = ("empty", "notempty")
SIDES = {"wireframe": ("inside", "outside"), "surface": ("above", "below")}
BY = {"wireframe": ("frac", "centre"), "surface": ("centre", "frac")}
LEAVES = ("wireframe", "surface")
SLAB_AXES = ("x", "y", "z", "u", "v")
SLAB_BY = ("centre", "share")
SLAB_FRAME = ("azimuth", "dip", "roll")
NODE_KEYS = ("op", "expr", "cond", "wireframe", "surface", "slab")
CODE = "filter_rules"
_NUMBER = re.compile(r"^[+-]?(\d+([.,]\d*)?|[.,]\d+)([eE][+-]?\d+)?$")


def bad(param: str, message: str, hint: str = "Проверьте правило фильтра.") -> ApiError:
    return ApiError(422, CODE, message, hint, {"param": param})


def expr_error(e: ExprError, param: str) -> ApiError:
    return ApiError(
        422,
        e.code,
        e.message,
        e.hint,
        {**{k: v for k, v in e.to_body().items() if k not in ("code", "message", "hint")}, "param": param},
    )


# --- checking ---------------------------------------------------------------------------------------------------------


def _number(v: Any) -> float | None:
    """A JSON number, or a text «0,5» / «0.5» / «1e-7», as float; anything else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v) if math.isfinite(float(v)) else None
    if isinstance(v, str) and _NUMBER.match(v.strip()):
        f = float(v.strip().replace(",", "."))
        return f if math.isfinite(f) else None
    return None


def _scalar(v: Any, param: str, *, numeric: bool) -> str | int | float:
    """A value of a condition: a number (also as text) or, unless `numeric`, a label."""
    f = _number(v)
    if f is not None:
        return v if isinstance(v, int) and not isinstance(v, bool) and abs(v) < 2**53 else f
    if numeric:
        raise bad(param, "Значение для сравнения по порядку — число.", "Введите число, например 0,5 или 12.")
    if isinstance(v, str) and 0 < len(v) <= MAX_LABEL:
        return v
    raise bad(param, "Значение условия — число или подпись категории (до 200 символов).",
              "Введите число или выберите подпись.")  # fmt: skip


def _cond(raw: Any, path: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise bad(path, "Условие — «поле — оператор — значение».", "Заполните поле, оператор и значение.")
    extra = set(raw) - {"field", "op", "value", "values"}
    if extra:
        raise bad(path, f"В условии лишнее: «{sorted(map(str, extra))[0][:40]}».", "Уберите лишние ключи.")
    field = raw.get("field")
    if not isinstance(field, str) or not field.strip() or len(field.strip()) > MAX_FIELD or "]" in field:
        raise bad(f"{path}.field", "Имя поля — от 1 до 100 символов, без «]».", "Выберите поле модели.")
    op = raw.get("op")
    if op not in COND_OPS:
        raise bad(f"{path}.op", "Оператор условия: =, <>, >, >=, <, <=, пусто, не пусто, в списке.",
                  "Выберите оператор из списка.")  # fmt: skip
    out: dict[str, Any] = {"field": field.strip(), "op": op}
    if op in NO_VALUE_OPS:
        return out
    if op == "in":
        values = raw.get("values")
        if not isinstance(values, list) or not 1 <= len(values) <= MAX_VALUES:
            raise bad(f"{path}.values", "Список значений — от 1 до 1000.", "Добавьте значения в список.")
        out["values"] = [_scalar(v, f"{path}.values.{i}", numeric=False) for i, v in enumerate(values)]
        return out
    if raw.get("value") is None:
        raise bad(f"{path}.value", "У условия нет значения.", "Введите значение для сравнения.")
    out["value"] = _scalar(raw["value"], f"{path}.value", numeric=op in ORDER_OPS)
    return out


def _leaf(kind: str, raw: Any, path: str) -> dict[str, Any]:
    what = "каркаса" if kind == "wireframe" else "поверхности"
    if not isinstance(raw, Mapping):
        raise bad(path, f"Правило {what}: нужны номер, сторона и способ.", "Выберите объект и сторону.")
    allowed = {"id", "side", "by"} | ({"m"} if kind == "wireframe" else set())
    extra = set(raw) - allowed
    if extra:
        raise bad(path, f"В правиле {what} лишнее: «{sorted(map(str, extra))[0][:40]}».", "Уберите лишние ключи.")
    oid = raw.get("id")
    if oid is not None and (isinstance(oid, bool) or not isinstance(oid, int) or not 0 < oid < 10**12):
        raise bad(f"{path}.id", f"Номер {what} задан неверно.", "Выберите объект из списка.")
    side = raw.get("side")
    if side not in SIDES[kind]:
        raise bad(f"{path}.side", f"Сторона: {' или '.join(SIDES[kind])}.", "Выберите сторону.")
    by = raw.get("by", BY[kind][0])
    if by not in BY[kind]:
        raise bad(f"{path}.by", f"Способ: {' или '.join(BY[kind])}.", "Выберите способ.")
    out: dict[str, Any] = {"id": oid, "side": side, "by": by}
    if kind == "wireframe":
        m = raw.get("m", DEFAULT_M)
        if isinstance(m, bool) or not isinstance(m, int) or not 1 <= m <= MAX_M:
            raise bad(f"{path}.m", "Подсетка доли — целое от 1 до 16.", "Укажите m, по умолчанию 4.")
        out["m"] = m
    return out


def _slab_frame(raw: Any, path: str) -> dict[str, float]:
    """`frame` of a slab along u or v: the angles of the swaths in degrees (missing — 0), the dip within ±90°."""
    hint = "Укажите азимуты, наклон и поворот в градусах, например {azimuth: 90}."
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise bad(path, "Оси полосы u и v задаются углами: азимут, наклон, поворот.", hint)
    extra = set(raw) - set(SLAB_FRAME)
    if extra:
        raise bad(path, f"В осях полосы лишнее: «{sorted(map(str, extra))[0][:40]}».", "Оставьте azimuth, dip и roll.")
    out: dict[str, float] = {}
    for name in SLAB_FRAME:
        v = 0.0 if raw.get(name) is None else _number(raw[name])
        if v is None:
            raise bad(f"{path}.{name}", "Угол — число в градусах.", hint)
        out[name] = v
    if abs(out["dip"]) > 90.0:
        raise bad(f"{path}.dip", "Наклон оси полос — от −90° до +90°.", "Отрицательный наклон — ось идёт вниз.")
    return out


def _slab(raw: Any, path: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise bad(path, "Правило слоя: нужны ось, границы «от» и «до» и способ.", "Выберите ось и границы слоя.")
    extra = set(raw) - {"axis", "from", "to", "by", "frame"}
    if extra:
        raise bad(path, f"В правиле слоя лишнее: «{sorted(map(str, extra))[0][:40]}».", "Уберите лишние ключи.")
    axis = raw.get("axis")
    if axis not in SLAB_AXES:
        raise bad(f"{path}.axis", "Ось слоя: x, y, z, u или v.", "Выберите ось.")
    lim: dict[str, float] = {}
    for key, word in (("from", "нижняя"), ("to", "верхняя")):
        v = _number(raw.get(key))
        if v is None:
            raise bad(f"{path}.{key}", f"Граница слоя ({word}) — число.", "Введите число, например 250 или 12,5.")
        lim[key] = v
    if not lim["from"] < lim["to"]:
        raise bad(f"{path}.to", "Граница «от» должна быть меньше границы «до».", "Поменяйте границы слоя местами.")
    by = raw.get("by", SLAB_BY[0])
    if by not in SLAB_BY:
        raise bad(f"{path}.by", f"Способ: {' или '.join(SLAB_BY)}.", "Выберите способ.")
    if by == "share" and axis != "z":
        raise bad(f"{path}.by", "Доля блока в слое считается только по оси z.", "Выберите способ «по центру».")
    out: dict[str, Any] = {"axis": axis, "from": lim["from"], "to": lim["to"], "by": by}
    if axis in ("u", "v"):
        out["frame"] = _slab_frame(raw.get("frame"), f"{path}.frame")
    elif raw.get("frame") is not None:
        raise bad(f"{path}.frame", "Углы осей задаются только для осей u и v.",
                  "Уберите frame или выберите ось u или v.")  # fmt: skip
    return out


def _expr(raw: Any, path: str) -> str:
    if not isinstance(raw, str):
        raise bad(path, "Выражение — текст по правилам калькулятора.", "Введите выражение, например AU > 1.")
    try:
        parse(raw)
    except ExprError as e:
        raise expr_error(e, path) from e
    return raw


class _Walk:
    def __init__(self) -> None:
        self.nodes = 0

    def node(self, raw: Any, path: str, depth: int) -> dict[str, Any]:
        if depth > MAX_RULE_DEPTH:
            raise bad(path, "Правило вложено глубже 32 уровней.", "Упростите дерево правил.")
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise bad(path, "В фильтре больше 500 правил.", "Разделите фильтр на несколько.")
        if not isinstance(raw, Mapping):
            raise bad(path, "Правило — набор «И / ИЛИ / НЕ», выражение, условие, каркас, поверхность или слой.",
                      "Выберите вид правила.")  # fmt: skip
        keys = [k for k in NODE_KEYS if k in raw]
        extra = set(raw) - set(NODE_KEYS) - ({"items"} if "op" in raw else set())
        if len(keys) != 1 or extra:
            raise bad(path, "У правила должно быть ровно одно из: op, expr, cond, wireframe, surface, slab.",
                      "Уберите лишние ключи правила.")  # fmt: skip
        key = keys[0]
        if key == "op":
            return self._group(raw, path, depth)
        if key == "expr":
            return {"expr": _expr(raw["expr"], f"{path}.expr")}
        if key == "cond":
            return {"cond": _cond(raw["cond"], f"{path}.cond")}
        if key == "slab":
            return {"slab": _slab(raw["slab"], f"{path}.slab")}
        return {key: _leaf(key, raw[key], f"{path}.{key}")}

    def _group(self, raw: Mapping[str, Any], path: str, depth: int) -> dict[str, Any]:
        op = raw["op"]
        if op not in GROUP_OPS:
            raise bad(f"{path}.op", "Группа правил: и, или, не.", "Выберите И, ИЛИ или НЕ.")
        if set(raw) - {"op", "items"}:
            raise bad(path, "В группе правил лишние ключи.", "Оставьте op и items.")
        items = raw.get("items")
        if not isinstance(items, list) or not items:
            raise bad(f"{path}.items", "В группе нет правил.", "Добавьте правило или удалите группу.")
        if op == "not" and len(items) != 1:
            raise bad(f"{path}.items", "«НЕ» относится ровно к одному правилу.", "Оставьте одно правило внутри «НЕ».")
        return {"op": op, "items": [self.node(it, f"{path}.items.{i}", depth + 1) for i, it in enumerate(items)]}


def normalize_rules(raw: Any, param: str = "rules") -> dict[str, Any]:
    """The tree checked and in the canonical form (a new dict; the argument stays as it is)."""
    return _Walk().node(raw, param, 1)


def rules_of_body(body: Mapping[str, Any], *, required: bool) -> dict[str, Any] | None:
    """`rules` or `expr` of a request: exactly one (none — only when not `required`); `expr` → `{expr}`."""
    given = [k for k in ("rules", "expr") if body.get(k) is not None]
    if len(given) > 1:
        raise bad("rules", "Фильтр задаётся деревом правил или выражением, не обоими сразу.",
                  "Передайте только rules или только expr.")  # fmt: skip
    if not given:
        if required:
            raise bad("rules", "У фильтра нет правил.", "Задайте правила или выражение, например AU > 1.")
        return None
    if given[0] == "expr":
        return {"expr": _expr(body["expr"], "expr")}
    return normalize_rules(body["rules"])


# --- walking ----------------------------------------------------------------------------------------------------------


def walk(rules: Mapping[str, Any], path: str = "rules") -> Iterator[tuple[str, Mapping[str, Any]]]:
    """Every node with its `param` path, parents before children."""
    yield path, rules
    for i, item in enumerate(rules.get("items") or ()):
        yield from walk(item, f"{path}.items.{i}")


def leaves(rules: Mapping[str, Any]) -> list[tuple[str, str, Mapping[str, Any]]]:
    """[(path of the `id`, "wireframe" | "surface", the body of the leaf)] in the order of the tree."""
    out = []
    for path, node in walk(rules):
        for kind in LEAVES:
            if kind in node:
                out.append((f"{path}.{kind}.id", kind, node[kind]))
    return out


def object_ids(rules: Mapping[str, Any]) -> list[int]:
    """The wireframes and the surfaces the tree names, once each, in the order of the tree (null ids left out)."""
    seen: dict[int, None] = {}
    for _, _, body in leaves(rules):
        if body.get("id") is not None:
            seen.setdefault(int(body["id"]), None)
    return list(seen)


def fields_of(rules: Mapping[str, Any]) -> list[str]:
    """The fields the tree reads, once each (names are compared without case), in the order of first use."""
    seen: dict[str, str] = {}
    for _, node in walk(rules):
        if "cond" in node:
            seen.setdefault(node["cond"]["field"].casefold(), node["cond"]["field"])
        elif "expr" in node:
            for name in parse(node["expr"]).fields():
                seen.setdefault(name.casefold(), name)
    return list(seen.values())


def remap_ids(rules: Mapping[str, Any], new_id: Callable[[int], int | None]) -> dict[str, Any]:
    """The tree with the ids of wireframes and surfaces renumbered; one the lookup has not — null (`.bfggis`)."""

    def one(node: Mapping[str, Any]) -> dict[str, Any]:
        if "items" in node:
            return {**node, "items": [one(i) for i in node["items"]]}
        for kind in LEAVES:
            if kind in node:
                old = node[kind].get("id")
                return {kind: {**node[kind], "id": None if old is None else new_id(int(old))}}
        return dict(node)

    return one(rules)


# --- the text of 8.7.4 ------------------------------------------------------------------------------------------------


def _value(v: Any) -> cn.Expr:
    return cn.Str(v) if isinstance(v, str) else cn.Num(float(v))


def _cond_expr(c: Mapping[str, Any]) -> cn.Expr:
    field, op = cn.Field(c["field"]), c["op"]
    if op == "empty":
        return cn.Call("ISNULL", (field,))
    if op == "notempty":
        return cn.Not(cn.Call("ISNULL", (field,)))
    if op == "in":
        return cn.In(field, tuple(v if isinstance(v, str) else float(v) for v in c["values"]))
    return cn.Compare(op, field, _value(c["value"]))


def _as_expr(node: Mapping[str, Any]) -> Expr | None:
    """The tree as an expression of `calc`; None when a wireframe, a surface or a slab is in it."""
    if "expr" in node:
        return parse(node["expr"])
    if "cond" in node:
        return _cond_expr(node["cond"])
    if "items" in node:
        parts = [_as_expr(i) for i in node["items"]]
        if any(p is None for p in parts):
            return None
        kids: list[Expr] = [p for p in parts if p is not None]
        if node["op"] == "not":
            return cn.Not(kids[0])
        return kids[0] if len(kids) == 1 else cn.BoolOp.of("AND" if node["op"] == "and" else "OR", kids)
    return None


def to_text(rules: Mapping[str, Any]) -> str | None:
    """The record of the whole filter in the language of 8.7.4 or None (a wireframe, a surface, or too long)."""
    try:
        tree = _as_expr(rules)
        if tree is None or tree.depth() > MAX_DEPTH:
            return None
        text = tree.to_text()
    except (ValueError, ExprError):
        return None
    return text if len(text) <= MAX_LENGTH else None


# --- the description in Russian ---------------------------------------------------------------------------------------

_OR, _AND, _NOT, _ATOM = 1, 2, 3, 4
_OBJECT = {"wireframe": ("внутри каркаса", "снаружи каркаса"), "surface": ("выше поверхности", "ниже поверхности")}


def _cond_title(c: Mapping[str, Any]) -> str:
    field = cn.format_field(c["field"])
    op = c["op"]
    if op == "empty":
        return f"{field} — пусто"
    if op == "notempty":
        return f"{field} — не пусто"
    if op == "in":
        return f"{field} в ({', '.join(cn.format_string(v) if isinstance(v, str) else _num(v) for v in c['values'])})"
    v = c["value"]
    return f"{field} {op} {cn.format_string(v) if isinstance(v, str) else _num(v)}"


def _num(v: Any) -> str:
    f = float(v)
    return ("-" if f < 0 else "") + cn.format_number(f)


def _title(node: Mapping[str, Any], names: Mapping[int, str]) -> tuple[str, int]:
    if "expr" in node:
        try:
            tree = parse(node["expr"])
        except ExprError:
            return str(node["expr"]), _OR  # not parsed (a stored text of a later version): in parentheses
        prec = _OR if isinstance(tree, cn.BoolOp) and tree.op == "OR" else _AND if isinstance(tree, cn.BoolOp) else (
            _NOT if isinstance(tree, cn.Not) else _ATOM)  # fmt: skip
        return tree.to_text(), prec
    if "cond" in node:
        return _cond_title(node["cond"]), _ATOM
    if "slab" in node:
        body = node["slab"]
        share = " (по доле)" if body["by"] == "share" else ""
        return f"{str(body['axis']).upper()} от {_num(body['from'])} до {_num(body['to'])}{share}", _ATOM
    for kind in LEAVES:
        if kind in node:
            body = node[kind]
            first, second = _OBJECT[kind]
            name = names.get(body["id"]) if body.get("id") is not None else None
            label = f"«{name}»" if name else "(недоступен)"
            how = "по центру" if body["by"] == "centre" else "по доле"
            return f"{first if body['side'] in ('inside', 'above') else second} {label} ({how})", _ATOM
    kids = [_title(i, names) for i in node["items"]]
    op = node["op"]
    if op == "not":
        text, prec = kids[0]
        return f"НЕ {text if prec == _ATOM else f'({text})'}", _NOT
    own = _OR if op == "or" else _AND
    if len(kids) == 1:
        return kids[0]
    word = " ИЛИ " if op == "or" else " И "
    return word.join(t if p > own else f"({t})" for t, p in kids), own


def title_of(rules: Mapping[str, Any], names: Mapping[int, str] | None = None) -> str:
    """«AU > 1 И внутри каркаса «Руда» (по доле)»; `names` — the names of the wireframes by id."""
    text, _ = _title(rules, names or {})
    return text


def meta_of(rules: Mapping[str, Any], note: str | None) -> dict[str, Any]:
    """`objects.meta` of a filter for a checked tree."""
    return {"v": VERSION, "rules": dict(rules), "text": to_text(rules), "fields": fields_of(rules), "note": note}
