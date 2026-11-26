"""The sources of the orientation of «Истинная мощность» and «Рудные пересечения» (ТЗ 8.5.4; ADR 0021 §4–5, §9) and
the ranges of the parameters: `parse_orientation(conn, set_id, table, body)` → [Source] (1–3 by priority), the simple
form of the command (`simple_orientation`), the checks `number`, `ranged`, `angle_pair`, `choice`. Split from `spec`
(which re-exports these names); no numpy here (before BF-READY).

The source `trend` (ТЗ 8.5.2, вопрос № 57; ADR 0026, решение 27): `{"source": "trend", "trend_id": N}` — a trend of
the project, global or structural (`geostat/trend*.py`). `Source.trend_meta` is its `meta` as stored (read here, by
the id; 422 `trend_not_found` — none); the canonical body keeps `trend_id` and the name (`trend`): the id is of the
project, `spec.rebind` finds the trend by its name after a `.bfggis` import. The hash has the name and what the axes
depend on (angles and ratios, the radius and the kind of the source) and, for a structural trend, what it reads when it
is used — the table of the measurements and the trajectories of its set, or the supporting wireframe (`trend_data`: the
keys of the hash, no ids; the ids of the tables and wireframes go to `inputs` of the result, after the tables and the
wireframes of the other sources, so `spec.rebind` walks the same lists): a changed measurement is a changed result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.composites import jobs as cj
from bf_core.db import dialect
from bf_core.db.schema import dh_tables, objects

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
SOURCES = ("global", "domain", "mesh", "core", "trend")
#: keys of the `source` of a structural trend that are ids of the project (not in the hash)
TREND_IDS = ("set_id", "table", "wireframe_id")
SRC_MAX = 3
ATTITUDES_MAX = 1000
MESH_MAX = 16


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

    kind: str  # global | domain | mesh | core | trend
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
    trend_id: int = 0  # trend: the trend of the project, its name and `meta` as read by `parse_orientation`
    trend_name: str = ""
    trend_meta: dict[str, Any] = field(default_factory=dict)
    trend_data: dict[str, Any] = field(default_factory=dict)  # trend: what it reads (`trend_data`)

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
        if self.kind == "trend":
            return {"source": "trend", "trend_id": self.trend_id, "trend": self.trend_name}
        return {"source": "core", "table": str(self.table_id), "alpha": self.alpha_field, "beta": self.beta_field,
                "line": self.line, "d_core": self.d_core}  # fmt: skip

    def mesh_ids(self) -> list[int]:
        if self.kind == "mesh":
            return [int(s.wireframe_id) for s, _st in self.meshes]
        if self.kind == "domain" and self.domain is not None and self.domain.wireframes is not None:
            return list(self.domain.wireframes.ids)
        return []

    def trend_tables(self, set_id: int) -> list[tuple[int, list[str]]]:
        """(table id, fields) of the measurements a structural trend reads, when they are in the set `set_id` (the
        marks «устарел» of a result follow the tables of its own set); else none."""
        got = self.trend_data.get("table")
        if self.kind != "trend" or got is None or self.trend_data.get("set_id") != set_id:
            return []
        return [(int(got[0]), list(got[1]))]

    def trend_meshes(self) -> list[int]:
        """The supporting wireframe of a structural trend."""
        wid = self.trend_data.get("mesh")
        return [] if self.kind != "trend" or wid is None else [int(wid)]

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
        if self.kind == "trend":
            return {"source": "trend", "trend_key": dialect.name_key(self.trend_name),
                    "trend": trend_fingerprint(self.trend_meta), "data": self.trend_data.get("keys")}  # fmt: skip
        out = {k: v for k, v in self.canonical().items() if k not in ("table", "wireframe_ids")}
        if self.table() is not None:
            out["table_key"] = dialect.name_key(self.table_name)
        if self.meshes:
            out["wireframes"] = [str(s.cache_key()) for s, _st in self.meshes]
        elif self.domain is not None and self.domain.wireframes is not None:
            out["wireframes"] = self.domain.wireframes.keys()
        return out


def trend_fingerprint(meta: dict[str, Any]) -> dict[str, Any]:
    """What the axes of a trend depend on (not its size, colour, origin; no ids of the project)."""
    out = {k: meta.get(k) for k in ("kind", "ang1", "ang2", "ang3", "ratios", "radius")}
    src = meta.get("source")
    if isinstance(src, dict):
        out["source"] = {k: v for k, v in src.items() if k not in TREND_IDS}
    return out


def trend_data(conn: sa.Connection, meta: dict[str, Any]) -> dict[str, Any]:
    """What a structural trend reads when it is used (a global one reads nothing): `keys` — for the hash (the name
    of the table with its token and revision, the key of the trajectories of the set, the key of the geometry of the
    wireframe: no ids), `table` (id, fields), `set_id`, `mesh` — for `inputs`. A source that is gone has no key."""
    src = meta.get("source")
    if meta.get("kind") != "structural" or not isinstance(src, dict):
        return {}
    from bf_core.geostat.estimate import store_check as esc

    def ident(value: Any) -> int | None:
        return None if value is None or isinstance(value, bool) or not str(value).isdigit() else int(value)

    if src.get("type") == "mesh":
        wid = ident(src.get("wireframe_id"))
        key = None
        if wid is not None:
            from bf_core.viz.meshes import mesh_state

            try:
                key = str(mesh_state(conn, wid).cache_key())
            except ApiError:
                wid = None
        return {"mesh": wid, "keys": {"mesh": key}}
    table, set_id = ident(src.get("table")), ident(src.get("set_id"))
    tab = esc.fp_of(conn, "dh_table", table) if table is not None else None
    # the paths of the set: its version (collars and surveys), not `trajectories_key` — that one moves with every table
    # of the set, the columns the result itself writes among them
    traj = None if set_id is None else conn.execute(sa.select(objects.c.version).where(
        objects.c.id == set_id, objects.c.kind == "drillhole_set")).scalar()
    fields = [str(src[k]) for k in ("alpha", "dipdir", "depth") if src.get(k)]
    keys = {"table": None if tab is None else [dialect.name_key(tab[0]), tab[1]],
            "traj": traj}  # fmt: skip
    return {"keys": keys, "set_id": set_id, "table": None if tab is None else (table, fields)}


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
    elif src == "trend":
        out.append({"source": "trend", "trend_id": body.get("trend_id")})
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
    as_id = text.isascii() and text.isdigit() and len(text) <= 18  # «²», «١», 10**30 — a name, not an id
    row = conn.execute(q.where(dh_tables.c.id == int(text))).first() if as_id else None
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


def _trend(conn: sa.Connection, raw: dict[str, Any], where: str) -> Source:
    """A trend of the project by `trend_id` (any kind); 422 `no_orientation` — not chosen, `trend_not_found` — none."""
    from bf_core.geostat import trend_store

    given = raw.get("trend_id")
    if given is None or (isinstance(given, str) and not given.strip()):
        raise _empty("не выбран тренд", "Выберите тренд в списке трендов проекта.", f"{where}trend_id")
    text = str(given).strip()
    if isinstance(given, bool) or not (text.isascii() and text.isdigit() and len(text) <= 18):
        raise bad("bad_param", "Источник «тренд»: нужен номер тренда.", "Выберите тренд в списке трендов проекта.",
                  f"{where}trend_id")  # fmt: skip
    try:
        name, meta = trend_store.meta_of(conn, int(text))
    except ApiError:
        raise bad("trend_not_found", f"Тренд №{int(text)} не найден в проекте.",
                  "Обновите список трендов и выберите тренд снова.", f"{where}trend_id") from None
    return Source("trend", trend_id=int(text), trend_name=name, trend_meta=meta, trend_data=trend_data(conn, meta))


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
        elif kind == "trend":
            out.append(_trend(conn, item, where))
        else:
            out.append(_core(conn, set_id, item, where))
    return out
