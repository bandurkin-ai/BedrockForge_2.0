"""Parameters of «Истинная мощность» and «Рудные пересечения» (ТЗ 8.5.4; ADR 0021 §4–10, решения 4–18): the bodies of
`POST /intercepts/tt` and `POST /intercepts/run` checked against the project and the ranges (docs/progress/e15-api.md).

    spec = parse_tt(conn, body)    # TtSpec; ApiError 422 `bad_param` {param}, `no_orientation` {param}; 404
    spec = parse_run(conn, body)   # RunSpec; its orientation may be empty: TT empty, TT_SRC «нет»

`orientation` — 1–3 sources by priority (a row takes the normal of the first that gave one; forms — e15-api.md).
The simple form of the command (§9: `source` and flat fields, `attitudes` as «код=α/Aп; …», `fallback_alpha` /
`fallback_dipdir` — a second, global source) is turned into that list first. `param` of an error in a source is a
path: «orientation.0.alpha».

`spec.body` is the canonical body (`dh_results.params`): tables as ids (text), fields by their stored names, every
number filled in, the orientation as a list; `rebind` puts the current ids into it for «Перестроить» (the tables of
`inputs` in the order of `tables()`, the wireframes in the order of `mesh_ids()`). No numpy here (before BF-READY).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.composites import jobs as cj
from bf_core.db import dialect
from bf_core.db.schema import dh_columns, dh_results, dh_tables

from . import D_CORE_DEFAULT, D_CORE_RANGE, EPS_DEFAULT, EPS_RANGE, LINES, STEP_DEFAULT, STEP_RANGE

#: Ranges of 8.5.4: key → (low, high, default, label, unit); those the kernels check too — from the package.
RANGES: dict[str, tuple[float, float, float, str, str]] = {
    "eps": (*EPS_RANGE, EPS_DEFAULT, "Порог «почти параллельна» ε_в", ""),
    "step": (*STEP_RANGE, STEP_DEFAULT, "Часть ствола", " м"),
    "d_max": (0.0, 1000.0, 50.0, "Наибольшее расстояние до каркаса d_max", " м"),
    "d_core": (*D_CORE_RANGE, D_CORE_DEFAULT, "Наибольшее расстояние до замера керна d_к", " м"),
    "r_n": (0.0, 200.0, 0.0, "Радиус осреднения нормали r_n", " м"),
    "l_min": (0.0, 100.0, 2.0, "Наименьшая длина (мощность) пересечения", " м"),
    "waste_max": (0.0, 50.0, 2.0, "Наибольшая внутренняя пустая порода L_п", " м"),
}
SOURCES = ("global", "domain", "mesh", "core")
SRC_MAX = 3
ATTITUDES_MAX = 1000
MESH_MAX = 16
MIN_BY = ("length", "tt")
MISSING = ("waste", "break")
TT_COLUMNS = ("TT", "TT_FLAG", "TT_SRC")
ANGLE_COLUMNS = ("ALPHA", "DIPDIR", "HOLE_THETA", "HOLE_AZ")
DEFAULT_RUN_NAME = "Рудные пересечения"
TT_TITLE = "Истинная мощность"


def bad(code: str, message: str, hint: str, param: str | None = None, status: int = 422) -> ApiError:
    return ApiError(status, code, message, hint, {"param": param} if param else None)


def _span(lo: float, hi: float, unit: str) -> str:
    return f"от {lo:g} до {hi:g}{unit}".replace(".", ",")


def number(raw: Any, label: str, param: str) -> float:
    """A finite number (a number or a text «0,5» / «0.5»); 422 `bad_param` otherwise. Empty is not 0."""
    value = math.nan
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        value = float(raw)
    elif isinstance(raw, str) and raw.strip():
        try:
            value = float(raw.strip().replace(",", "."))
        except ValueError:
            value = math.nan
    if not math.isfinite(value):
        raise bad("bad_param", f"{label}: нужно число.", "Введите число, например 0,5.", param)
    return value


def ranged(body: dict[str, Any], key: str, param: str | None = None) -> float:
    """`body[key]` in the range of 8.5.4 (absent or null — the default); 422 `bad_param` {param}."""
    lo, hi, default, label, unit = RANGES[key]
    raw = body.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    value = number(raw, label, param or key)
    if not lo <= value <= hi:
        span = _span(lo, hi, unit)
        raise bad("bad_param", f"{label}: значение вне диапазона {span}.", f"Задайте {span}.", param or key)
    return value


def angle_pair(alpha: Any, dipdir: Any, where: str) -> tuple[float, float]:
    """α ∈ [0; 90], Aп ∈ [0; 360] (360 → 0) — ADR 0021 §2; `where` — the path of the parameters."""
    out = []
    for raw, key, label, hi in (
        (alpha, "alpha", "Угол падения α", 90.0),
        (dipdir, "dipdir", "Азимут падения Aп", 360.0),
    ):
        v = number(raw, label, f"{where}{key}")
        if not 0.0 <= v <= hi:
            span = f"от 0 до {hi:g}°"
            raise bad("bad_param", f"{label}: значение вне диапазона {span}.", f"Задайте {span}.", f"{where}{key}")
        out.append(v + 0.0)
    return out[0], 0.0 if out[1] == 360.0 else out[1]


def choice(raw: Any, choices: tuple[str, ...], default: str, label: str, param: str) -> str:
    if raw in (None, ""):
        return default
    if raw not in choices:
        raise bad("bad_param", f"{label}: неизвестное значение «{raw}».", f"Допустимо: {', '.join(choices)}.", param)
    return str(raw)


@dataclass(frozen=True)
class Attitude:
    code: str  # as the domain writes it: text, a number by `format(x, ".15g")`, the name of a body
    alpha: float
    dipdir: float


@dataclass
class Source:
    """One source of the orientation; the fields of the others are empty."""

    kind: str  # global | domain | mesh | core
    alpha: float = math.nan  # global
    dipdir: float = math.nan
    domain: cj.DomainSpec | None = None  # domain
    attitudes: tuple[Attitude, ...] = ()
    meshes: list[Any] = field(default_factory=list)  # mesh: (MeshState, stats) by priority
    d_max: float = 50.0
    r_n: float = 0.0
    table_id: int = 0  # core: the table of the measurements
    table_name: str = ""
    table_kind: str = ""
    alpha_field: str = ""
    beta_field: str = ""
    line: str = "boh"
    d_core: float = 10.0

    def canonical(self) -> dict[str, Any]:
        if self.kind == "global":
            return {"source": "global", "alpha": self.alpha, "dipdir": self.dipdir}
        if self.kind == "domain":
            d = self.domain
            assert d is not None
            out: dict[str, Any] = {"source": "domain", "table": str(d.table_id), "field": d.field}
            if d.wireframes is not None:
                out = {"source": "domain", "wireframe_ids": list(d.wireframes.ids)}
            return {**out, "attitudes": [{"code": a.code, "alpha": a.alpha, "dipdir": a.dipdir}
                                         for a in self.attitudes]}  # fmt: skip
        if self.kind == "mesh":
            return {"source": "mesh", "wireframe_ids": self.mesh_ids(), "d_max": self.d_max, "r_n": self.r_n}
        return {"source": "core", "table": str(self.table_id), "alpha": self.alpha_field, "beta": self.beta_field,
                "line": self.line, "d_core": self.d_core}  # fmt: skip

    def mesh_ids(self) -> list[int]:
        if self.kind == "mesh":
            return [int(s.wireframe_id) for s, _st in self.meshes]
        if self.kind == "domain" and self.domain is not None and self.domain.wireframes is not None:
            return list(self.domain.wireframes.ids)
        return []

    def table(self) -> tuple[int, list[str]] | None:
        """(table id, fields read) of the source; None — reads no table."""
        d = self.domain
        if self.kind == "domain" and d is not None and d.wireframes is None:
            return d.table_id, [d.field]
        if self.kind == "core":
            return self.table_id, [self.alpha_field, self.beta_field]
        return None

    def hash(self) -> dict[str, Any]:
        """What the result depends on: names and geometry keys, no ids (`.bfggis` renumbers them)."""
        out = {k: v for k, v in self.canonical().items() if k not in ("table", "wireframe_ids")}
        if self.table() is not None:
            out["table_key"] = dialect.name_key(self.table_name)
        if self.meshes:
            out["wireframes"] = [str(s.cache_key()) for s, _st in self.meshes]
        elif self.domain is not None and self.domain.wireframes is not None:
            out["wireframes"] = self.domain.wireframes.keys()
        return out


def parse_attitudes(raw: Any) -> list[dict[str, Any]]:
    """«код=α/Aп; …» of the command (or a list as given) → [{code, alpha, dipdir}]."""
    if not isinstance(raw, str):
        return raw if isinstance(raw, list) else []
    out = []
    for part in (p.strip() for p in raw.split(";") if p.strip()):
        code, _eq, rest = part.partition("=")
        alpha, _sl, dipdir = rest.partition("/")
        out.append({"code": code.strip(), "alpha": alpha.strip(), "dipdir": dipdir.strip()})
    return out


def simple_orientation(body: dict[str, Any]) -> list[dict[str, Any]]:
    """The flat parameters of the command `intercept.tt` / `intercept.run` (ADR 0021 §9) as `orientation`."""
    src = body.get("source")
    out: list[dict[str, Any]] = []
    if src == "global":
        out.append({"source": "global", "alpha": body.get("alpha"), "dipdir": body.get("dipdir")})
    elif src == "domain":
        dom: dict[str, Any] = {"source": "domain", "field": body.get("domain"), "table": body.get("domain_table"),
                               "attitudes": parse_attitudes(body.get("attitudes"))}  # fmt: skip
        if body.get("domain_wireframes"):
            dom = {"source": "domain", "wireframe_ids": body.get("domain_wireframes"), "attitudes": dom["attitudes"]}
        out.append(dom)
    elif src == "mesh":
        out.append({"source": "mesh", "wireframe_ids": body.get("wireframe_ids"), "d_max": body.get("d_max"),
                    "r_n": body.get("r_n")})  # fmt: skip
    elif src == "core":
        out.append({"source": "core", "table": body.get("core_table"), "alpha": body.get("core_alpha"),
                    "beta": body.get("core_beta"), "line": body.get("core_line"),
                    "d_core": body.get("d_core")})  # fmt: skip
    elif src not in (None, ""):
        raise bad("bad_param", f"Источник ориентировки: неизвестное значение «{src}».",
                  f"Допустимо: {', '.join(SOURCES)}.", "source")  # fmt: skip
    if body.get("fallback_alpha") is not None or body.get("fallback_dipdir") is not None:
        out.append({"source": "global", "alpha": body.get("fallback_alpha"), "dipdir": body.get("fallback_dipdir")})
    return out


def _empty(what: str, hint: str, param: str) -> ApiError:
    return bad("no_orientation", f"Ориентировка: {what}.", hint, param)


def _code(raw: Any, kind: str, where: str) -> str:
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)) or not str(raw).strip():
        raise bad("bad_param", "Таблица доменов: пустой код домена.", "Заполните код в каждой строке.", where)
    return (
        format(number(raw, "Таблица доменов: код числового домена", where) + 0.0, ".15g")
        if kind == "number"
        else str(raw).strip()
    )


def _domain(conn: sa.Connection, set_id: int, table: Any, raw: dict[str, Any], where: str) -> Source:
    wf = raw.get("wireframe_ids")
    if wf in (None, "", []) and not str(raw.get("field") or "").strip():
        raise bad("bad_param", "По домену: не выбрано поле домена.", "Выберите поле домена или каркасы-домены.",
                  f"{where}field")  # fmt: skip
    dom = {"wireframe_ids": wf} if wf not in (None, "", []) else {"table": raw.get("table"), "field": raw["field"]}
    key = "field" if "field" in dom else "wireframe_ids"
    d = _located(lambda: cj.parse_domain(conn, set_id, table, dom), where, key)
    rows = raw.get("attitudes")
    rows = parse_attitudes(rows) if isinstance(rows, str) else rows
    if rows is not None and not isinstance(rows, list):
        raise bad("bad_param", "Таблица доменов: нужен список строк «код, α, Aп».", "Заполните таблицу доменов.",
                  f"{where}attitudes")  # fmt: skip
    if not rows:
        raise _empty("таблица доменов пуста", "Заполните таблицу «код, α, Aп» или нажмите «Заполнить кодами поля».",
                     f"{where}attitudes")  # fmt: skip
    if len(rows) > ATTITUDES_MAX:
        raise bad("bad_param", f"Таблица доменов: больше {ATTITUDES_MAX} строк.", "Сократите таблицу доменов.",
                  f"{where}attitudes")  # fmt: skip
    out: list[Attitude] = []
    seen: set[str] = set()
    for k, row in enumerate(rows):
        at = f"{where}attitudes.{k}."
        if not isinstance(row, dict):
            raise bad("bad_param", "Таблица доменов: строка без кода и углов.", "Заполните строку.", f"{at}code")
        code = _code(row.get("code"), d.kind, f"{at}code")
        if code in seen:
            raise bad("bad_param", f"Таблица доменов: код «{code}» повторяется.", "Оставьте одну строку на код.",
                      f"{at}code")  # fmt: skip
        seen.add(code)
        a, dd = angle_pair(row.get("alpha"), row.get("dipdir"), at)
        out.append(Attitude(code, a, dd))
    return Source("domain", domain=d, attitudes=tuple(out), table_name=d.table_name)


def _located(fn: Any, where: str, key: str) -> Any:
    """`fn()` with the `param` of its ApiError as a path inside the source («orientation.0.field»)."""
    try:
        return fn()
    except ApiError as exc:
        sub = "table" if exc.code == "table_not_found" else str(exc.extra.get("param") or key)
        exc.extra = {**exc.extra, "param": f"{where}{key if sub in ('domain', 'field') else sub}"}
        raise


def _mesh(conn: sa.Connection, raw: dict[str, Any], where: str) -> Source:
    from bf_core.composites.jobs_coding import _ids
    from bf_core.wireframes import exact

    if raw.get("wireframe_ids") in (None, "", []):
        raise _empty("не выбраны каркасы", "Отметьте каркасы или поверхности тела.", f"{where}wireframe_ids")
    ids = _located(lambda: _ids(raw.get("wireframe_ids")), where, "wireframe_ids")
    if len(ids) > MESH_MAX:
        raise bad("bad_param", f"Каркасов больше {MESH_MAX}.", f"Выберите не больше {MESH_MAX} каркасов.",
                  f"{where}wireframe_ids")  # fmt: skip
    found = {int(s.wireframe_id): (s, st) for s, st in exact.set_targets(conn, ids)}
    gone = [i for i in ids if i not in found]
    if gone:
        raise bad("wireframe_not_found", f"Каркас №{gone[0]} не найден в проекте или пуст.",
                  "Обновите список каркасов и отметьте их снова.", f"{where}wireframe_ids")  # fmt: skip
    return Source("mesh", meshes=[found[i] for i in ids], d_max=ranged(raw, "d_max", f"{where}d_max"),
                  r_n=ranged(raw, "r_n", f"{where}r_n"))  # fmt: skip


def _any_table(conn: sa.Connection, set_id: int, given: Any, where: str) -> Any:
    """A ready table of the set of any kind (events, intervals, samples) by id or name; 404 `table_not_found`."""
    q = sa.select(dh_tables.c.id, dh_tables.c.name, dh_tables.c.kind, dh_tables.c.row_count).where(
        dh_tables.c.set_id == set_id, dh_tables.c.state == "ready"
    )
    text = str(given).strip() if given is not None else ""
    row = conn.execute(q.where(dh_tables.c.id == int(text))).first() if text.isdigit() else None
    if row is None and text:
        row = conn.execute(q.where(dh_tables.c.name_key == dialect.name_key(text))).first()
    if row is None:
        raise bad("table_not_found", "Таблица замеров керна не найдена.", "Выберите таблицу набора с замерами.",
                  f"{where}table", status=404)  # fmt: skip
    return row


def _core(conn: sa.Connection, set_id: int, raw: dict[str, Any], where: str) -> Source:
    from bf_core.composites.source import _fields

    if not str(raw.get("table") or "").strip():
        raise _empty("не выбрана таблица замеров керна", "Выберите таблицу с углами α_к и β_к.", f"{where}table")
    t = _any_table(conn, set_id, raw.get("table"), where)
    names: dict[str, str] = {}
    for key, label in (("alpha", "α_к"), ("beta", "β_к")):
        given = raw.get(key)
        if not isinstance(given, str) or not given.strip():
            raise _empty(f"не выбрано поле {label}", f"Выберите числовое поле {label} в таблице «{t.name}».",
                         f"{where}{key}")  # fmt: skip
        f = _located(lambda g=given: next(iter(_fields(conn, int(t.id), [g.strip()]).values())), where, key)
        if f.kind != "number":
            raise bad("bad_param", f"Поле {label} «{f.name}» не числовое.", "Выберите числовое поле.", f"{where}{key}")
        names[key] = f.name
    return Source("core", table_id=int(t.id), table_name=str(t.name), table_kind=str(t.kind),
                  alpha_field=names["alpha"], beta_field=names["beta"],
                  line=choice(raw.get("line"), LINES, "boh", "Линия отсчёта керна", f"{where}line"),
                  d_core=ranged(raw, "d_core", f"{where}d_core"))  # fmt: skip


def parse_orientation(conn: sa.Connection, set_id: int, table: Any, body: dict[str, Any]) -> list[Source]:
    raw = body.get("orientation")
    raw = simple_orientation(body) if raw is None else raw
    if not isinstance(raw, list):
        raise bad("bad_param", "Ориентировка: нужен список источников.", "Выберите источник ориентировки.",
                  "orientation")  # fmt: skip
    if len(raw) > SRC_MAX:
        raise bad("bad_param", f"Ориентировка: больше {SRC_MAX} источников.", "Оставьте до трёх источников.",
                  "orientation")  # fmt: skip
    out: list[Source] = []
    for i, item in enumerate(raw):
        where = f"orientation.{i}."
        kind = item.get("source") if isinstance(item, dict) else None
        if kind not in SOURCES:
            raise bad("bad_param", f"Источник ориентировки: неизвестное значение «{kind}».",
                      f"Допустимо: {', '.join(SOURCES)}.", f"{where}source")  # fmt: skip
        assert isinstance(item, dict)
        if any(s.kind == kind for s in out):
            raise bad("bad_param", "Ориентировка: источник выбран дважды.", "Оставьте каждый источник один раз.",
                      f"{where}source")  # fmt: skip
        if kind == "global":
            a, d = angle_pair(item.get("alpha"), item.get("dipdir"), where)
            out.append(Source("global", alpha=a, dipdir=d))
        elif kind == "domain":
            out.append(_domain(conn, set_id, table, item, where))
        elif kind == "mesh":
            out.append(_mesh(conn, item, where))
        else:
            out.append(_core(conn, set_id, item, where))
    return out


@dataclass
class TtSpec:
    set_id: int
    table_id: int
    table_name: str
    orientation: list[Source]
    eps: float
    step: float
    angles: bool
    body: dict[str, Any]  # canonical, without `replace` — `dh_results.params`
    rows: int = 0

    def columns(self) -> list[str]:
        """The fields written into the table (ADR 0021 §8)."""
        return [*TT_COLUMNS, *(ANGLE_COLUMNS if self.angles else ())]

    def tables(self) -> list[tuple[int, list[str]]]:
        """(table id, fields read) — `results.make_inputs`: the source first, then the tables of the orientation."""
        return _tables(self.table_id, [], self.orientation)

    def mesh_ids(self) -> list[int]:
        return [i for s in self.orientation for i in s.mesh_ids()]

    def hash_params(self) -> dict[str, Any]:
        return {"op": "tt", "orientation": [s.hash() for s in self.orientation], "eps": self.eps, "step": self.step,
                "angles": self.angles}  # fmt: skip


@dataclass
class RunSpec:
    set_id: int
    table_id: int
    table_name: str
    field: str  # stored name of the grade
    unit: str | None
    means: list[str]  # numeric fields averaged into the table: the grade first
    units: dict[str, str | None]
    density: str | None
    cutoff: float
    include_cutoff: float | None  # c2 > c of «в том числе»; None — none
    l_min: float  # L_min (min_by = length) or m_min (min_by = tt)
    min_by: str
    waste_max: float
    missing: str
    orientation: list[Source]
    eps: float
    step: float
    name: str
    replace: bool
    body: dict[str, Any]
    rows: int = 0

    def tables(self) -> list[tuple[int, list[str]]]:
        own = [*self.means, *([self.density] if self.density else [])]
        return _tables(self.table_id, own, self.orientation)

    def read_fields(self) -> list[str]:
        """Fields of the samples read (the grades, the density, a domain or core field of the same table)."""
        return self.tables()[0][1]

    def mesh_ids(self) -> list[int]:
        return [i for s in self.orientation for i in s.mesh_ids()]

    def hash_params(self) -> dict[str, Any]:
        return {"op": "intercepts", "field": self.field, "means": self.means, "density": self.density,
                "cutoff": self.cutoff, "include_cutoff": self.include_cutoff, "l_min": self.l_min,
                "min_by": self.min_by, "waste_max": self.waste_max, "missing": self.missing,
                "orientation": [s.hash() for s in self.orientation], "eps": self.eps, "step": self.step}  # fmt: skip


def _tables(table_id: int, own: list[str], orientation: list[Source]) -> list[tuple[int, list[str]]]:
    """The source first, then each other table once, in the order of the sources (`rebind` walks the same)."""
    order: dict[int, list[str]] = {table_id: list(own)}
    for s in orientation:
        t = s.table()
        if t is not None:
            order.setdefault(t[0], []).extend(t[1])
    return [(tid, list(dict.fromkeys(f))) for tid, f in order.items()]


def _core_rows(conn: sa.Connection, orientation: list[Source]) -> int:
    ids = [s.table_id for s in orientation if s.kind == "core"]
    q = sa.select(sa.func.sum(dh_tables.c.row_count)).where(dh_tables.c.id.in_(ids))
    return int(conn.execute(q).scalar() or 0) if ids else 0


def _set_id(body: dict[str, Any]) -> int:
    set_id = body.get("set_id")
    if not isinstance(set_id, int) or isinstance(set_id, bool):
        raise ApiError(404, "set_not_found", "Не задан набор скважин.", "Выберите набор скважин.")
    return set_id


def _source(conn: sa.Connection, set_id: int, body: dict[str, Any]) -> Any:
    return _located(lambda: cj.resolve_table(conn, set_id, body.get("table")), "", "table")


def parse_tt(conn: sa.Connection, body: dict[str, Any]) -> TtSpec:
    """The body of `POST /intercepts/tt`: an interval table of the set and 1–3 sources of the orientation."""
    set_id = _set_id(body)
    t = _source(conn, set_id, body)
    orientation = parse_orientation(conn, set_id, t, body)
    if not orientation:
        raise _empty("не выбран источник", "Выберите источник ориентировки пласта.", "orientation")
    eps, step = ranged(body, "eps"), ranged(body, "step")
    angles = body.get("angles", True)
    if not isinstance(angles, bool):
        raise bad("bad_param", "Углы: ожидается «да» или «нет».", "Отметьте или снимите галочку.", "angles")
    canon = {"set_id": set_id, "table": str(t.id), "orientation": [s.canonical() for s in orientation], "eps": eps,
             "step": step, "angles": angles}  # fmt: skip
    rows = int(t.row_count or 0) + _core_rows(conn, orientation)
    return TtSpec(set_id, int(t.id), str(t.name), orientation, eps, step, angles, canon, rows)


def _numeric(conn: sa.Connection, table_id: int, raw: Any, label: str, param: str) -> Any:
    from bf_core.composites.source import _fields

    if not isinstance(raw, str) or not raw.strip():
        raise bad("bad_param", f"{label}: не выбрано поле.", "Выберите числовое поле таблицы.", param)
    f = _located(lambda: next(iter(_fields(conn, table_id, [raw.strip()]).values())), "", param)
    if f.kind != "number":
        raise bad("bad_param", f"{label}: поле «{f.name}» не числовое.", "Выберите числовое поле.", param)
    return f


def parse_run(conn: sa.Connection, body: dict[str, Any]) -> RunSpec:
    """The body of `POST /intercepts/run`: the rule of 8.5.3 on an interval table, the orientation optional."""
    set_id = _set_id(body)
    t = _source(conn, set_id, body)
    tid = int(t.id)
    g = _numeric(conn, tid, body.get("field"), "Поле содержания", "field")
    cutoff = number(body.get("cutoff"), "Бортовое содержание c", "cutoff")
    inc = body.get("include_cutoff")
    c2 = None if inc in (None, "") else number(inc, "Борт «в том числе» c2", "include_cutoff")
    if c2 is not None and not c2 > cutoff:
        raise bad("bad_param", "Борт «в том числе» c2 должен быть больше бортового c.",
                  "Задайте c2 больше c или оставьте поле пустым.", "include_cutoff")  # fmt: skip
    density = None
    if isinstance(body.get("density"), str) and body["density"].strip():
        density = _numeric(conn, tid, body["density"], "Плотность", "density").name
    means = _means(conn, tid, g.name, density, body.get("fields"))
    orientation = parse_orientation(conn, set_id, t, body)
    name = body.get("name")
    name = str(name).strip() if isinstance(name, str) and name.strip() else DEFAULT_RUN_NAME
    if len(name) > cj.NAME_MAX:
        raise bad("bad_param", "Имя таблицы слишком длинное.", f"Не больше {cj.NAME_MAX} знаков.", "name")
    if dialect.name_key(name) == dialect.name_key(t.name):
        raise bad("name_is_source", "Имя результата совпадает с таблицей-источником.", "Задайте другое имя.", "name")
    units = dict(conn.execute(sa.select(dh_columns.c.name, dh_columns.c.unit).where(
        dh_columns.c.table_id == tid, dh_columns.c.deleted.is_(False))).tuples().all())  # fmt: skip
    rule = {"field": g.name, "cutoff": cutoff, "include_cutoff": c2, "l_min": ranged(body, "l_min"),
            "min_by": choice(body.get("min_by"), MIN_BY, "length", "Критерий приёмки", "min_by"),
            "waste_max": ranged(body, "waste_max"),
            "missing": choice(body.get("missing"), MISSING, "waste", "Пропуски", "missing")}  # fmt: skip
    eps, step = ranged(body, "eps"), ranged(body, "step")
    canon = {"set_id": set_id, "table": str(tid), **rule, "density": density, "fields": means[1:],
             "orientation": [s.canonical() for s in orientation], "eps": eps, "step": step, "name": name}  # fmt: skip
    rows = int(t.row_count or 0) + _core_rows(conn, orientation)
    return RunSpec(set_id=set_id, table_id=tid, table_name=str(t.name), field=g.name, unit=g.unit, means=means,
                   units={n: units.get(n) for n in means}, density=density, cutoff=cutoff, include_cutoff=c2,
                   l_min=rule["l_min"], min_by=rule["min_by"], waste_max=rule["waste_max"], missing=rule["missing"],
                   orientation=orientation, eps=eps, step=step, name=name, replace=body.get("replace") is True,
                   body=canon, rows=rows)  # fmt: skip


def _means(conn: sa.Connection, table_id: int, grade: str, density: str | None, raw: Any) -> list[str]:
    """The grade first, then the numeric fields averaged: `fields` as given, or every numeric field of the table but
    the density, the fixed ones and those «Истинная мощность» writes (TT, ALPHA, …)."""
    from bf_core.composites.source import _fields

    skip = {grade.casefold(), *([density.casefold()] if density else [])}
    if raw is not None and raw != "":
        names = _located(lambda: cj._names(raw), "", "fields")
        chosen = _located(lambda: _fields(conn, table_id, names), "", "fields")
        text = [f.name for f in chosen.values() if f.kind != "number"]
        if text:
            raise bad("bad_param", f"Поле «{text[0]}» не числовое.", "Отметьте числовые поля содержаний.", "fields")
        return [grade, *(n for n in chosen if n.casefold() not in skip)]
    skip |= {c.casefold() for c in (*TT_COLUMNS, *ANGLE_COLUMNS)}
    cols = conn.execute(
        sa.select(dh_columns.c.name, dh_columns.c.role)
        .where(dh_columns.c.table_id == table_id, dh_columns.c.deleted.is_(False), dh_columns.c.kind == "number")
        .order_by(dh_columns.c.position, dh_columns.c.id)
    ).all()
    return [grade, *(c.name for c in cols if c.name.casefold() not in skip and c.role not in ("source", "fixed"))]


def check_columns(conn: sa.Connection, spec: TtSpec) -> None:
    """409 `column_exists` {param: "table", name}: a field TT, ALPHA, … of the table that no earlier «Истинная
    мощность» of this table wrote (a field of the user is never overwritten; a repeat overwrites its own)."""
    q = sa.select(dh_columns.c.name).where(dh_columns.c.table_id == spec.table_id, dh_columns.c.deleted.is_(False))
    live = {r.name.casefold(): r.name for r in conn.execute(q)}
    ours: set[str] = set()
    q = sa.select(dh_results.c.report).where(dh_results.c.table_id == spec.table_id, dh_results.c.op == "tt")
    for r in conn.execute(q):
        for w in (r.report or {}).get("written") or []:
            ours |= {str(n).casefold() for n in w.get("columns") or []}
    clash = [live[n.casefold()] for n in spec.columns() if n.casefold() in live and n.casefold() not in ours]
    if clash:
        raise ApiError(409, "column_exists", f"В таблице «{spec.table_name}» уже есть поле «{clash[0]}».",
                       "Переименуйте поле таблицы: в него пишется истинная мощность.",
                       {"param": "table", "name": clash[0]})  # fmt: skip


def rebind(params: dict[str, Any], tables: list[int], meshes: list[int]) -> dict[str, Any]:
    """The canonical body of a result with the current ids: `tables` — of `inputs.tables` now (the order of
    `tables()`), `meshes` — of `inputs.meshes` now (the order of `mesh_ids()`)."""
    seen: dict[str, str] = {}
    free = iter(str(t) for t in tables)
    flat = iter(meshes)

    def table(old: Any) -> str:
        key = str(old)
        if key not in seen:
            seen[key] = next(free, key)
        return seen[key]

    out = {**params, "table": table(params.get("table"))}
    srcs = []
    for s in params.get("orientation") or []:
        s = dict(s)
        if s.get("source") in ("domain", "core") and s.get("table") is not None:
            s["table"] = table(s["table"])
        if s.get("wireframe_ids"):
            s["wireframe_ids"] = [next(flat, i) for i in s["wireframe_ids"]]
        srcs.append(s)
    out["orientation"] = srcs
    return out
