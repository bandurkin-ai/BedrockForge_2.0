"""The job of ore intervals by the ГКЗ method (ТЗ 7.35 Д-26 «Результат» (1)–(5); ADR 0018 §2–3, §8–11, решения 19–22,
26): `composite.ore_intervals` — a new table «Рудные интервалы», the fields ORE_ID and ORE_CLS of the samples in
place, the display «Рудные интервалы», the summary in `report` — in one final transaction (`sink.commit` with
`table`, `columns` and `after`), or nothing.

    spec = parse_ore(conn, body)                 # the body of `POST /composites/ore` → OreSpec; ApiError 422 / 404
    made = compute_ore(spec, src, dom, st)       # the kernel on the samples read: the job, the preview, the protocol

Body (the window «Рудные интервалы (ГКЗ)», docs/progress/e12-kernels-api.md): {set_id, table, field, cutoff, m_min,
m_waste_max, mc_min?, c_min?, mode, missing, missing_max?, thickness: {source: "length" | "field", field?},
domain?: {table, field} | {wireframe_ids}, contacts_values?: int[], name?, replace?}. `mc_min` and `c_min` not
given — Сб·Мрт and Сб (the kernel). A domain is a field of the samples, of another interval table (the code with
the greatest length inside the sample) or the closed bodies (the code by the middle of the sample); the
calculation goes inside the domains. `contacts_values` — the domain values that are the body in the mode «по
контактам» (a value matches a domain written the same way: 1 → «1»); not given — every non-empty one.

The table: HOLE (the row's hole), FROM, TO, LENGTH, M, C, MC, CLASS, BASIS (names), N_SAMPLES, WASTE_LEN, NESTED,
ORE_ID (empty for rows outside `ore`), FLAG, DOMAIN, the numeric fields of the samples (weighted by thickness;
the field of the calculation first; a name the table has already — «<name>_SRC»), MODE, MANUAL (0), X, Y, Z of
the middle, LABEL («8,0 м @ 2,84 г/т (МС 22,7)» — `ore` only). The samples get ORE_ID (the number of the `ore`
interval in its hole, empty — none) and ORE_CLS (the class, empty — outside the sampling lines); 409
`column_exists` — the table has such a field not written by Д-26. A repeat overwrites them.

The display: a saved striplog of the set named as the table (a colour column by CLASS in the brand colours of
ADR 0006 — those of `charts/column/classes.ts`; `frame` hidden; the label LABEL) — made, or for «Заменить» and
«Перестроить» pointed at the new table (the display is the one whose `settings.origin` names this table).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_columns, dh_results
from bf_core.jobs.registry import handler

from . import jobs as cj

if TYPE_CHECKING:
    from bf_core.drillholes.store import ColumnSpec
    from bf_core.jobs.context import JobContext

    from . import sink, source
    from .ore_set import OreSet
    from .ore_types import OreLines, OreParams

KIND = "composite.ore_intervals"
OP = "ore"
ALGO = "ore-1"  # f"ore-{ore_intervals.ALGO}" (the version of the rules; not imported here: no numba before BF-READY)
cj.ALGO.update(ore=ALGO)
DEFAULT_NAME = "Рудные интервалы"
SAMPLE_COLUMNS = ("ORE_ID", "ORE_CLS")
OWN = ("HOLE", "FROM", "TO", "LENGTH", "M", "C", "MC", "CLASS", "BASIS", "N_SAMPLES", "WASTE_LEN", "NESTED", "ORE_ID",
       "FLAG", "DOMAIN", "MODE", "MANUAL", "X", "Y", "Z", "LABEL")  # fmt: skip
Tick = Callable[[float], None]

# The colours of the classes: those of `ui/src/charts/column/classes.ts` (brand palette, ADR 0006; решение 20).
_BRAND = {"anthracite": "#1b222b", "cobalt": "#1f6bff", "copper": "#f46a1a", "stone": "#e6e9ee", "slate": "#8a99a8"}


def _mix(a: str, b: str, t: float) -> str:
    x = [int(_BRAND[a][i : i + 2], 16) for i in (1, 3, 5)]
    y = [int(_BRAND[b][i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{int(np.floor(v + (w - v) * t + 0.5)):02x}" for v, w in zip(x, y, strict=True))


CLASS_COLORS = {
    "ore": _BRAND["copper"],
    "subgrade": _mix("copper", "stone", 0.55),
    "waste_in": _mix("slate", "stone", 0.3),
    "interlayer": _BRAND["cobalt"],
    "waste": _mix("anthracite", "slate", 0.45),
    "frame": _mix("slate", "stone", 0.65),
}

# Where a message of the kernel points: the parameter of the window.
_PARAMS = (("Бортовое", "cutoff"), ("Минимальная мощность", "m_min"), ("Максимальная мощность", "m_waste_max"),
           ("Минимальный метропроцент", "mc_min"), ("Минимальное содержание", "c_min"), ("Неизвестный режим", "mode"),
           ("Пропуски", "missing"), ("Наибольший", "missing_max"),
           ("Домены рудного тела", "contacts_values"))  # fmt: skip


def as_api(exc: Exception) -> ApiError:
    """OreError of the kernel → 422 {code, message, hint, param}; anything else re-raised."""
    from .ore_types import OreError

    if not isinstance(exc, OreError):
        raise exc
    param = next((p for start, p in _PARAMS if exc.message.startswith(start)), None)
    if exc.code == "ore_no_domain":
        param = "domain"
    return cj._bad(exc.code, exc.message, exc.hint, param)


# --- the request -------------------------------------------------------------------------------------------


@dataclass
class OreSpec:
    set_id: int
    table_id: int
    table_name: str
    field: str  # stored name of the field of the calculation
    unit: str | None
    thick: str | None  # the thickness field; None — the length along the hole
    means: list[str]  # numeric fields averaged into the table: the field first
    units: dict[str, str | None]
    roles: dict[str, str]
    kernel: OreParams  # checked; contacts_values — None (mapped to the codes read: `compute_ore`)
    contacts: tuple[int, ...] | None
    name: str
    replace: bool
    body: dict[str, Any]  # the request as given, without `replace` — `dh_results.params`
    rows: int = 0
    domain: cj.DomainSpec | None = None

    def read_fields(self) -> list[str]:
        d = self.domain
        own = [d.field] if d is not None and d.wireframes is None and d.table_id == self.table_id else []
        return list(dict.fromkeys([*self.means, *([self.thick] if self.thick else []), *own]))

    def other_domain(self) -> cj.DomainSpec | None:
        d = self.domain
        return d if d is not None and d.wireframes is None and d.table_id != self.table_id else None

    def tables(self) -> list[tuple[int, list[str]]]:
        d = self.other_domain()
        return [(self.table_id, self.read_fields()), *([(d.table_id, [d.field])] if d else [])]

    def mesh_ids(self) -> list[int]:
        d = self.domain
        return [] if d is None or d.wireframes is None else d.wireframes.ids

    def hash_params(self) -> dict[str, Any]:
        """What the result depends on besides the data (not the name of the table)."""
        from .jobs_prep import clean

        d = self.domain
        dom = None if d is None else {"table": dialect.name_key(d.table_name), "field": d.field}
        if d is not None and d.wireframes is not None:
            dom = {"wireframes": d.wireframes.keys(), "names": d.wireframes.names()}
        contacts = None if self.contacts is None else list(self.contacts)
        return {"op": OP, "field": self.field, "thick": self.thick, "means": self.means, "domain": dom,
                "kernel": clean(asdict(self.kernel)), "contacts": contacts}  # fmt: skip


def _number(conn: sa.Connection, table_id: int, raw: Any, label: str, param: str) -> Any:
    from .source import _fields

    if not isinstance(raw, str) or not raw.strip():
        raise cj._bad("bad_param", f"{label}: не выбрано поле.", "Выберите числовое поле таблицы.", param)
    f = next(iter(_fields(conn, table_id, [raw.strip()]).values()))
    if f.kind != "number":
        raise cj._bad("bad_param", f"{label}: поле «{f.name}» не числовое.", "Выберите числовое поле.", param)
    return f


def _contacts(raw: Any) -> tuple[int, ...] | None:
    """`contacts_values`: whole numbers ≥ 0 (a list, or a string «1, 2»); empty — every non-empty domain."""
    if raw is None or raw == "" or raw == []:
        return None
    items = raw.replace(";", ",").split(",") if isinstance(raw, str) else raw if isinstance(raw, list) else [raw]
    out: list[int] = []
    for v in items:
        text = str(v).strip()
        if isinstance(v, str) and not text:
            continue
        if isinstance(v, bool) or not text.isdigit():
            raise cj._bad("bad_param", f"Коды тела: «{text}» — не целое число не меньше нуля.", "Например: 1, 2.",
                          "contacts_values")  # fmt: skip
        out.append(int(text))
    return tuple(sorted(set(out))) or None


def _optional(body: dict[str, Any], key: str) -> Any:
    v = body.get(key)
    return None if v is None or (isinstance(v, str) and not v.strip()) else v


def parse_ore(conn: sa.Connection, body: dict[str, Any]) -> OreSpec:
    """The body of `POST /composites/ore` checked against the project and the rules of the kernel."""
    from .ore_types import OreParams

    set_id = body.get("set_id")
    if not isinstance(set_id, int) or isinstance(set_id, bool):
        raise ApiError(404, "set_not_found", "Не задан набор скважин.", "Выберите набор скважин.")
    t = cj.resolve_table(conn, set_id, body.get("table"))
    tid = int(t.id)
    f = _number(conn, tid, body.get("field"), "Поле содержания", "field")
    th = body.get("thickness")
    if th is None and isinstance(body.get("thickness_field"), str) and body["thickness_field"].strip():
        th = {"source": "field", "field": body["thickness_field"]}  # the simple form of the command
    th = {"source": th} if isinstance(th, str) else (th or {"source": "length"})
    if not isinstance(th, dict) or th.get("source", "length") not in ("length", "field"):
        raise cj._bad("bad_param", "Мощность: ожидается «длина по стволу» или «поле».", "Выберите источник мощности.",
                      "thickness")  # fmt: skip
    thick = None
    if th.get("source") == "field":
        thick = _number(conn, tid, th.get("field"), "Поле мощности", "thickness").name
    raw = body.get("domain")
    domain = None
    if not (raw is None or raw == "" or raw == {}):
        if isinstance(raw, dict) and not raw.get("wireframe_ids") and not str(raw.get("field") or "").strip():
            raise cj._bad("domain_missing", "Не выбрано поле домена.", "Выберите поле домена или «без домена».",
                          "domain")  # fmt: skip
        domain = cj.parse_domain(conn, set_id, t, raw)
    contacts = _contacts(body.get("contacts_values"))
    try:
        got: dict[str, Any] = {k: body.get(k) for k in ("cutoff", "m_min", "m_waste_max")}  # checked by the kernel
        kernel = OreParams(
            **got, mc_min=_optional(body, "mc_min"), c_min=_optional(body, "c_min"),
            mode=body.get("mode") or "strict_excl", missing=body.get("missing") or "break",
            missing_max=_optional(body, "missing_max") or 0.0,
        ).checked()  # fmt: skip
    except ValueError as exc:
        raise as_api(exc) from None
    if kernel.mode == "contacts" and domain is None:
        raise cj._bad("ore_no_domain", "Режим «По контактам» требует поле домена.",
                      "Выберите поле домена (или кодирование каркасом) либо другой режим.", "domain")  # fmt: skip
    skip = {f.name.casefold(), *(s.casefold() for s in SAMPLE_COLUMNS), *([thick.casefold()] if thick else [])}
    if domain is not None and domain.table_id == tid and domain.field:
        skip.add(domain.field.casefold())
    cols = conn.execute(
        sa.select(dh_columns.c.name, dh_columns.c.unit, dh_columns.c.role)
        .where(dh_columns.c.table_id == tid, dh_columns.c.deleted.is_(False), dh_columns.c.kind == "number")
        .order_by(dh_columns.c.position, dh_columns.c.id)
    ).all()
    others = [c for c in cols if c.name.casefold() not in skip and c.role not in ("source", "fixed")]
    means = [f.name, *(c.name for c in others)]
    units = {f.name: f.unit, **{c.name: c.unit for c in others}}
    roles = {f.name: "component", **{c.name: c.role if c.role in ("component", "value") else "value" for c in others}}
    name = body.get("name")
    name = str(name).strip() if isinstance(name, str) and name.strip() else DEFAULT_NAME
    if len(name) > cj.NAME_MAX:
        raise cj._bad("bad_param", "Имя таблицы слишком длинное.", f"Не больше {cj.NAME_MAX} знаков.", "name")
    if dialect.name_key(name) == dialect.name_key(t.name):
        raise cj._bad("name_is_source", "Имя результата совпадает с таблицей-источником.", "Задайте другое имя.",
                      "name")  # fmt: skip
    return OreSpec(set_id=set_id, table_id=tid, table_name=t.name, field=f.name, unit=f.unit, thick=thick,
                   means=means, units=units, roles=roles, kernel=kernel, contacts=contacts, name=name,
                   replace=body.get("replace") is True, body={k: v for k, v in body.items() if k != "replace"},
                   rows=int(t.row_count or 0), domain=domain)  # fmt: skip


def check_columns(conn: sa.Connection, spec: OreSpec) -> None:
    """409 `column_exists`: ORE_ID or ORE_CLS is a field of the samples no earlier Д-26 result wrote."""
    from bf_core.db.schema import dh_tables

    key = conn.execute(sa.select(dh_tables.c.name_key).where(dh_tables.c.id == spec.table_id)).scalar()
    live = {
        r.name.casefold(): r.name
        for r in conn.execute(
            sa.select(dh_columns.c.name).where(dh_columns.c.table_id == spec.table_id, dh_columns.c.deleted.is_(False))
        )
    }
    ours: set[str] = set()
    q = sa.select(dh_results.c.report).where(dh_results.c.set_id == spec.set_id, dh_results.c.op == OP)
    for r in conn.execute(q):
        for w in (r.report or {}).get("written") or []:
            if w.get("table") == key:
                ours |= {str(n).casefold() for n in w.get("columns") or []}
    clash = [live[n.casefold()] for n in SAMPLE_COLUMNS if n.casefold() in live and n.casefold() not in ours]
    if clash:
        raise ApiError(
            409,
            "column_exists",
            f"В таблице «{spec.table_name}» уже есть поле «{clash[0]}».",
            "Переименуйте поле таблицы проб: в него пишется результат выделения рудных интервалов.",
            {"param": "table", "name": clash[0]},
        )


# --- the computation ---------------------------------------------------------------------------------------


@dataclass
class OreMade:
    lines: OreLines  # the samples in line order of the reading (`source.Lines`)
    kernel: OreParams  # with the codes of the body of «по контактам»
    res: OreSet
    names: list[str]  # the domain of a code
    dom_kind: str | None  # number | text | wireframes; None — no domain
    journal: list[str]


def _domain_codes(spec: OreSpec, src: source.Lines, dom: source.Lines | None, st: Any,
                  journal: list[str]) -> tuple[np.ndarray | None, list[str]]:  # fmt: skip
    """The domain code of every sample (−1 — none) and the domain of a code."""
    from . import jobs_prep as jp

    d = spec.domain
    if d is None:
        return None, []
    if d.wireframes is not None:
        from . import coding
        from . import intervals as iv
        from .compose import _dense
        from .jobs_coding import read_bodies

        samples = iv.Lines(np.arange(len(src.holes), dtype=np.int64), src.starts, src.from_m, src.to_m)
        c = coding.code_by_wireframes(samples, st.traj, _dense(st, src.hole_db_ids), read_bodies(d.wireframes),
                                      split=False, n_bodies=len(d.wireframes.targets))  # fmt: skip
        journal += c.journal
        return np.ascontiguousarray(c.code, dtype=np.int32), list(c.names)
    if dom is not None:
        codes, names = jp.codes_on(src.holes, src.starts, src.from_m, src.to_m, dom, d)
        return np.ascontiguousarray(codes, dtype=np.int32), list(names)
    if d.kind == "text":
        return src.codes[d.field], list(src.labels[d.field])
    from .domains import number_codes

    codes, uniques = number_codes(src.numbers[d.field])
    return codes, jp.number_names(uniques)


def _stats_journal(stats: dict[str, int], missing: str) -> list[str]:
    out = []
    if stats.get("bad"):
        out.append(f"Проб с неверными глубинами (до ≤ от) — {stats['bad']}: не входят в расчёт.")
    if stats.get("overlap"):
        out.append(f"Перекрывающихся проб — {stats['overlap']}: не входят в расчёт.")
    if stats.get("missing"):
        how = "считаются пустой породой до L_н (FLAG = 1)" if missing == "waste" else "линия опробования разорвана"
        out.append(f"Проб без содержания или мощности — {stats['missing']}: {how}.")
    if stats.get("no_domain"):
        out.append(f"Проб вне доменов — {stats['no_domain']}: не входят в расчёт.")
    return out


def compute_ore(
    spec: OreSpec, src: source.Lines, dom: source.Lines | None, st: Any, *, threads: int = 1,
    tick: Tick | None = None, nested: bool = True,
) -> OreMade:  # fmt: skip
    """Д-26 on the samples read (all the holes of the table, or one for the preview and the protocol); `st` — the
    trajectories (needed for a domain by wireframes only)."""
    from .ore_set import run_set
    from .ore_types import OreLines

    journal: list[str] = []
    if src.skipped:
        journal.append(f"Таблица «{spec.table_name}»: строк без «от» или «до» — {src.skipped}, поля пусты.")
    codes, names = _domain_codes(spec, src, dom, st, journal)
    kernel = spec.kernel
    if kernel.mode == "contacts" and spec.contacts is not None:
        wanted = {str(v) for v in spec.contacts}
        body = tuple(i for i, n in enumerate(names) if n in wanted)
        kernel = replace(kernel, contacts_values=body)
        if len(body) < len(wanted):
            found = {names[i] for i in body}
            miss = ", ".join(sorted(wanted - found, key=lambda s: (len(s), s)))
            journal.append(f"Коды тела без проб в домене: {miss}.")
    thick = src.numbers[spec.thick] if spec.thick else None
    try:
        lines = OreLines.of(src.starts, src.from_m, src.to_m, src.numbers[spec.field], thick, codes)
        res = run_set(lines, kernel, fields={n: src.numbers[n] for n in spec.means}, nested=nested,
                      threads=threads, tick=tick)  # fmt: skip
    except ValueError as exc:
        raise as_api(exc) from None
    journal += _stats_journal(res.summary, kernel.missing)
    kind = None if spec.domain is None else spec.domain.kind
    return OreMade(lines, kernel, res, names, kind, journal)


def _nan0(a: np.ndarray) -> np.ndarray:
    """An integer column where 0 means «none»: float64, 0 → NaN (never a zero for a missing value)."""
    out = np.asarray(a, dtype=np.float64).copy()
    out[out == 0] = np.nan
    return out


def _names_of(codes: np.ndarray, names: Any) -> np.ndarray:
    """Text by code; a code < 0 or an empty name → None."""
    table = np.array([*(n or None for n in names), None], dtype=object)
    c = np.asarray(codes, dtype=np.int64)
    return table[np.where(c < 0, len(table) - 1, c)]


def domain_column(made: OreMade) -> tuple[str, np.ndarray]:
    """("number" | "text", DOMAIN of every row of the table)."""
    code = made.res.domain
    if made.dom_kind == "number":
        vals = np.array([float(n) for n in made.names] + [np.nan], dtype=np.float64)
        return "number", vals[np.where(code < 0, len(made.names), code)]
    return "text", _names_of(code, made.names)


def table_columns(spec: OreSpec, made: OreMade, xyz: np.ndarray | None) -> tuple[list[ColumnSpec], list[np.ndarray]]:
    """The columns of «Рудные интервалы» after FROM and TO (the module docstring), in their order."""
    from bf_core.drillholes.store import ColumnSpec

    from .ore_set import BASIS_NAMES, CLASS_NAMES

    res, t = made.res, made.res.table()
    n = len(res)
    unit = spec.unit
    taken = {o.casefold() for o in OWN}
    kind, domain = domain_column(made)
    cols = [ColumnSpec("LENGTH", "number", "м"), ColumnSpec("M", "number", "м"),
            ColumnSpec("C", "number", unit, "component"), ColumnSpec("MC", "number", f"м·{unit}" if unit else None),
            ColumnSpec("CLASS", "text"), ColumnSpec("BASIS", "text"), ColumnSpec("N_SAMPLES", "number"),
            ColumnSpec("WASTE_LEN", "number", "м"), ColumnSpec("NESTED", "number"), ColumnSpec("ORE_ID", "number"),
            ColumnSpec("FLAG", "number"), ColumnSpec("DOMAIN", kind)]  # fmt: skip
    vals: list[np.ndarray] = [t["LENGTH"], t["M"], t["C"], t["MC"], _names_of(t["CLASS"], CLASS_NAMES),
                              _names_of(t["BASIS"], BASIS_NAMES), t["N_SAMPLES"], t["WASTE_LEN"], t["NESTED"],
                              _nan0(t["ORE_ID"]), t["FLAG"], domain]  # fmt: skip
    for name in spec.means:
        cols.append(ColumnSpec(f"{name}_SRC" if name.casefold() in taken else name, "number", spec.units.get(name),
                               spec.roles.get(name, "value")))  # fmt: skip
        vals.append(res.fields[name])
    if xyz is None:
        xyz = np.full((n, 3), np.nan)
    labels = np.array([s or None for s in res.labels(unit or "")], dtype=object)
    cols += [ColumnSpec("MODE", "text"), ColumnSpec("MANUAL", "number"), ColumnSpec("X", "number", "м"),
             ColumnSpec("Y", "number", "м"), ColumnSpec("Z", "number", "м"), ColumnSpec("LABEL", "text")]  # fmt: skip
    vals += [np.full(n, made.kernel.mode, dtype=object), np.zeros(n), xyz[:, 0], xyz[:, 1], xyz[:, 2], labels]
    typed = [np.asarray(v, dtype=object if c.kind == "text" else np.float64) for c, v in zip(cols, vals, strict=True)]
    return cols, typed


def middles(made: OreMade, src: source.Lines, st: Any, threads: int = 1) -> np.ndarray:
    """X, Y, Z of the middle of every row (NaN — no trajectory). The rows go by hole (the nested ones after their
    `ore` row), so they are lines of the holes of the samples."""
    from . import coords
    from . import intervals as iv
    from .compose import _dense

    rows = made.res.intervals
    hole = np.asarray(rows.hole, dtype=np.int64)
    order = np.argsort(hole, kind="stable")  # already so (the kernel writes them by hole): no move
    nh = len(src.holes)
    starts = np.concatenate(([0], np.cumsum(np.bincount(hole, minlength=nh)))).astype(np.int64)
    lines = iv.Lines(np.arange(nh, dtype=np.int64), starts, np.ascontiguousarray(rows.frm[order]),
                     np.ascontiguousarray(rows.to[order]))  # fmt: skip
    xyz = coords.midpoints(lines, st.traj, _dense(st, src.hole_db_ids), threads=threads)
    out = np.empty_like(xyz)
    out[order] = xyz
    return out


def out_table(spec: OreSpec, src: source.Lines, made: OreMade, xyz: np.ndarray | None) -> sink.OutTable:
    from . import sink

    iv = made.res.intervals
    cols, vals = table_columns(spec, made, xyz)
    at = np.asarray(iv.hole, dtype=np.int64)
    holes: list[str] = np.array(src.holes, dtype=object)[at].tolist() if len(iv) else []
    return sink.OutTable(spec.name, holes, np.ascontiguousarray(iv.frm), np.ascontiguousarray(iv.to), cols, vals)


def sample_columns(made: OreMade) -> list[tuple[ColumnSpec, np.ndarray]]:
    """ORE_ID and ORE_CLS of every sample, in line order."""
    from bf_core.drillholes.store import ColumnSpec

    from .ore_set import CLASS_NAMES

    return [(ColumnSpec("ORE_ID", "number"), _nan0(made.res.ore_id)),
            (ColumnSpec("ORE_CLS", "text"), _names_of(made.res.ore_cls, CLASS_NAMES))]  # fmt: skip


# --- the display «Рудные интервалы» --------------------------------------------------------------------------


def display_settings(name: str) -> dict[str, Any]:
    """The striplog of the result table `name` (`ui/src/charts/striplog/model.ts`): every hole of the set, a tube
    coloured by CLASS (`frame` hidden by default, решение 20) with the label LABEL. The track names its table by
    `dh_tables.name_key`, as every display does (`viz/keys.table_by_key`): it follows a replaced table."""
    from .ore_set import CLASS_NAMES

    label = {"on": True, "template": "{LABEL}", "decimals": None, "size": 12, "color": "#f3f6fb", "place": "outside"}
    track = {"id": "ore-class", "table": dialect.name_key(name), "field": "CLASS", "type": "color", "width": 110,
             "color": CLASS_COLORS["ore"], "min": "", "max": "", "log": False, "boundaries": True, "form": "tube",
             "catColors": dict(CLASS_COLORS), "catOrder": list(CLASS_NAMES), "hidden": ["frame"],
             "label": label}  # fmt: skip
    return {"holes": [], "allHoles": True, "tracks": [track], "from": "", "to": "", "scale": 0, "grid": True,
            "tube": 8, "panel": 0, "labels": True, "form": "lith",
            "origin": {"op": OP, "table": dialect.name_key(name)}}  # fmt: skip


def put_display(conn: sa.Connection, set_id: int, name: str) -> int:
    """The display of the result table `name`: the one made for a table of this name gets its settings again,
    else a new one (named as the table; a name taken by another display — « (2)»). Returns its id."""
    from bf_core.charts import service as charts
    from bf_core.drillholes.edit import author

    settings = display_settings(name)
    shown = charts.list_charts(conn, set_id, "striplog")
    mine = next((c for c in shown if (c["settings"] or {}).get("origin") == settings["origin"]), None)
    if mine is not None:
        return int(charts.update(conn, mine["id"], None, settings, mine["version"])["id"])
    names = {dialect.name_key(c["name"]) for c in charts.list_charts(conn, set_id)}
    title, k = name, 2
    while dialect.name_key(title) in names:
        title, k = f"{name} ({k})", k + 1
    return int(charts.create(conn, set_id, title, "striplog", settings, author())["id"])


def chart_of(conn: sa.Connection, set_id: int, name: str) -> int | None:
    from bf_core.charts import service as charts

    origin = {"op": OP, "table": dialect.name_key(name)}
    return next((int(c["id"]) for c in charts.list_charts(conn, set_id, "striplog")
                 if (c["settings"] or {}).get("origin") == origin), None)  # fmt: skip


# --- the job -----------------------------------------------------------------------------------------------


def read_ore(engine: sa.Engine, spec: OreSpec, *, cache_dir: Any = None,
             holes: list[int] | None = None) -> tuple[source.Lines, source.Lines | None]:  # fmt: skip
    from . import source

    src = source.read(engine, spec.set_id, spec.table_id, spec.read_fields(), cache_dir=cache_dir, holes=holes)
    d = spec.other_domain()
    dom = None if d is None else source.read(engine, spec.set_id, d.table_id, [d.field], cache_dir=cache_dir,
                                             holes=holes)  # fmt: skip
    return src, dom


def report_of(made: OreMade) -> dict[str, Any]:
    """`dh_results.report` of the computation: the summary (Д-26 «Результат» (5)) and the numbers of the kernel."""
    from .jobs_prep import clean

    return clean({"summary": made.res.summary, "kernel": asdict(made.kernel)})  # type: ignore[no-any-return]


def run_ore(
    engine: sa.Engine, folder: Any, params: dict[str, Any], *, job_id: int,
    tick: Callable[[float | None, str | None], None],
) -> dict[str, Any]:  # fmt: skip
    """The body of `composite.ore_intervals` (the handler and the tests)."""
    from . import jobs_coding, results, sink, source, strips

    t0 = time.perf_counter()
    body, rebuild = cj._body_of(params)
    with dialect.read_tx(engine) as conn:
        spec = parse_ore(conn, body)
        check_columns(conn, spec)
        inputs = results.make_inputs(conn, spec.set_id, spec.tables(), trajectories=True, meshes=spec.mesh_ids())
    tick(0.02, "Чтение проб")
    cache = folder / "cache" / "tables"
    src, dom = read_ore(engine, spec, cache_dir=cache)
    project = SimpleNamespace(engine=engine, uid=f"job:{folder}", folder=folder)
    st = source.trajectories(project, spec.set_id)
    stations = source.stations_bytes(st, src.hole_db_ids.tolist())
    sha = source.input_hash([src, *([dom] if dom else [])], spec.hash_params(), stations=stations, algo=ALGO)
    fresh = cj._fresh(engine, rebuild, spec.set_id, sha, ALGO, inputs)
    if fresh is not None:
        with dialect.read_tx(engine) as conn:
            chart = chart_of(conn, spec.set_id, spec.name)
        return {**fresh, "name": spec.name, **({"chart_id": chart} if chart is not None else {})}
    tick(0.1, "Рудные интервалы")
    threads = strips.default_threads()
    with jobs_coding.reading(project, keep=False):  # a domain by wireframes reads their geometry
        made = compute_ore(spec, src, dom, st, threads=threads, tick=lambda f: tick(0.1 + 0.45 * f, None))
    out = out_table(spec, src, made, middles(made, src, st, threads))
    samples = sink.ColumnsOut(spec.table_id, src.row_ids, sample_columns(made))
    shown: dict[str, int] = {}

    def after(conn: sa.Connection, res: dict[str, Any]) -> None:
        shown["chart"] = put_display(conn, spec.set_id, str(res["name"]))

    with sink.guarded(engine, job_id):
        tid = sink.build_table(engine, spec.set_id, out, job_id=job_id,
                               tick=lambda f, m: tick(0.6 + 0.3 * (f or 0.0), m or "Запись таблицы"))  # fmt: skip
        again = cj._again(engine, spec, out, tid)
        report = {**report_of(made), "unit": spec.unit, "journal": made.journal, "versions": cj._versions(),
                  "edits": [], "seconds": round(time.perf_counter() - t0, 3)}  # fmt: skip
        res = sink.commit(engine, sink.Commit(set_id=spec.set_id, op=OP, name=spec.name, params=spec.body,
                                              inputs=inputs, sha256=sha, algo=ALGO, report=report, job_id=job_id,
                                              table=tid, mode="replace" if spec.replace else "rename",
                                              columns=[samples], dependents=again, after=after))  # fmt: skip
    # no tick from here on: a cancel after the final transaction would report a committed result as cancelled
    try:
        sink.write_snapshot(engine, cache, tid, sink.with_dependents(out, again))
    except Exception:  # noqa: BLE001 - the snapshot only speeds up reading; the table is committed
        pass
    set_id = spec.set_id
    events: list[list[Any]] = [["drillholes.changed", {"set_id": set_id, "table": str(tid), "rev": res["rev"]}]]
    events += [["drillholes.changed", {"set_id": set_id, "table": str(t), "rev": 0}] for t in res["replaced"]]
    events += [["drillholes.changed", {"set_id": set_id, "table": c["table"], "rev": c["rev"]}]
               for c in res["changed"] if c["table"] != str(tid)]  # fmt: skip
    events.append(["objects.changed", {"kind": "chart", "set_id": set_id, "id": shown["chart"]}])
    events.append(["composites.changed", {"set_id": set_id}])
    return {"set_id": set_id, "result_id": res["result_id"], "table_id": tid, "name": res["name"],
            "chart_id": shown["chart"], "rows": len(made.res), "columns": list(SAMPLE_COLUMNS),
            "replaced": res["replaced"], "fresh": False, "journal": made.journal, "events": events}  # fmt: skip


@handler(KIND, "Рудные интервалы (ГКЗ)", queue="heavy", estimate_memory=cj.memory_mb)
def ore_job(ctx: JobContext) -> dict[str, Any]:
    assert ctx.project_folder is not None
    try:
        out = run_ore(ctx.engine(), ctx.project_folder, ctx.params, job_id=ctx.id, tick=ctx.tick)
    except ApiError as exc:
        raise cj._user(exc) from None
    cj._log_journal(ctx, out.get("journal") or [])
    return out
