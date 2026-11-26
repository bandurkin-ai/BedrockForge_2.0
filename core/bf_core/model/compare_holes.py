"""«Сверка данных и модели» (ТЗ 7.35 Д-33; ADR 0026 решение 28; e19-api.md §8.3): the intervals of a lithology table
(the codes of the data) against the bodies of an implicit model (the codes of the model).

    req = parse(body)                      # {set_id, table, field, page?, size?, channel?, seq?} → `Request` (422)
    out = run(project, model_id, req)      # the answer of §8.3, as the request of its channel (409 `superseded`)

The code of the model of an interval is `composites.intervals.mode_by_length` over the stretches the bodies of the
model make on the whole hole (`drillholes.from_model.on_holes` / `fill_rows`, priority — the order of the groups,
the same as «Модель на скважины»): the code with the greatest total length inside the interval, a tie — the one met
first down the hole. The codes of the model are the names of the groups (a shell — «Группа ≥ порог»), then
«Прочее» (the body of the model that no group has, the last of the priority) and «Вне модели» (no body; it takes
part in the vote by its length, so an interval half in a gap and half in a body is told by what is longer).

Agreement. A data code agrees with a column of the model when it is one of the `codes` of the group, or is the
name of the column itself (the same text, whatever the case); «Прочее» and «Вне модели» never agree. A missing
code is not 0 and not a code: it is the row «Нет кода» of the matrix (own length and count), never a mismatch
(there is nothing to compare), and it is not in the base of `agree_share` (`coded_length`).

The answer: `model_codes` (the columns; the last two — «Прочее», «Вне модели»), `data_codes` (the rows; the last
may be «Нет кода» — `no_code_row`), `length` / `count` — the matrices [data × model] in metres / in intervals,
`agree` — the cells that agree, `agree_length`, `total_length` (the sum of the matrix: the length of the intervals
compared), `coded_length`, `agree_share` (null without a coded interval), `no_trajectory` — the holes of the table
that have no path (their intervals are not compared), `mismatches {page, size, total, rows: [{hole, hole_id, from,
to, depth, data_code, model_code, length}]}` — by length, longest first, then by (hole, FROM); `depth` — the middle
of the interval: the window goes to the hole in 7.17 at it.

Refusals {code, message, hint[, param]}: 409 `no_project`, `model_building`, `not_built`, `superseded`; 404
`set_not_found`, `model_not_found`, `table_not_found`; 422 `bad_param` {param}, `no_bodies`, `wireframe_open`.
Nothing is written: it works in a project «только просмотр». numpy and the kernels load with the first request.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_columns

if TYPE_CHECKING:
    import numpy as np

OTHER = "Прочее"
OUTSIDE = "Вне модели"
NO_CODE = "Нет кода"
DEFAULT_SIZE = 500
MAX_SIZE = 5000
Tick = Callable[[float | None, str | None], None]


def bad(param: str, message: str, hint: str) -> ApiError:
    return ApiError(422, "bad_param", message, hint, {"param": param})


# --- the request ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Request:
    set_id: int
    table: Any  # the id or the name of an interval table of the set
    column: str  # the text field with the codes of the data
    page: int
    size: int
    body: dict[str, Any] = field(default_factory=dict)  # as given: `channel`, `seq` are read from it


def _whole(raw: Any, param: str, what: str, default: int, lo: int, hi: int) -> int:
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, int) or not lo <= raw <= hi:
        raise bad(param, f"{what}: целое число от {lo} до {hi}.", "Проверьте номер страницы и размер.")
    return raw


def parse(body: Any) -> Request:
    """The request checked by itself (the project is not read): 422 `bad_param` {param}."""
    if not isinstance(body, dict):
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Повторите действие в окне.")
    set_id = body.get("set_id")
    if isinstance(set_id, bool) or not isinstance(set_id, int):
        raise ApiError(404, "set_not_found", "Не задан набор скважин.", "Выберите набор скважин.")
    table = body.get("table")
    if isinstance(table, bool) or not isinstance(table, (int, str)) or not str(table).strip():
        raise bad("table", "Не выбрана таблица литологии.", "Выберите интервальную таблицу набора.")
    name = body.get("field")
    if not isinstance(name, str) or not name.strip():
        raise bad("field", "Не выбрано поле с кодами данных.", "Выберите текстовое поле таблицы.")
    page = _whole(body.get("page"), "page", "Страница", 1, 1, 10**9)
    size = _whole(body.get("size"), "size", "Размер страницы", DEFAULT_SIZE, 1, MAX_SIZE)
    return Request(set_id, table, name.strip(), page, size, body)


# --- what the project says (reads only) -------------------------------------------------------------------------


@dataclass
class Plan:
    model: dict[str, Any]  # {id, name, stale_reason}
    table: dict[str, Any]  # {id, name, field}
    wireframe_ids: list[int]  # the bodies, by priority; «Прочее» (if the model has it) the last
    body_code: list[int]  # the column of the model of every body
    model_codes: list[str]  # the names of the columns; … «Прочее», «Вне модели»
    accepts: list[set[str]]  # per column: the data codes (folded) that agree with it
    triangles: int = 0
    journal: list[str] = field(default_factory=list)

    @property
    def other_col(self) -> int:
        return len(self.model_codes) - 2

    @property
    def outside_col(self) -> int:
        return len(self.model_codes) - 1


def fold(code: str) -> str:
    return code.strip().casefold()


def _field_name(conn: sa.Connection, table_id: int, given: str) -> str:
    """The stored name of the text column `given`; 422 `bad_param` {param: field} — none, or a number field."""
    cols = conn.execute(sa.select(dh_columns.c.name, dh_columns.c.kind).where(
        dh_columns.c.table_id == table_id, dh_columns.c.deleted.is_(False))).all()  # fmt: skip
    by_name = {c.name.casefold(): c for c in cols}
    c = by_name.get(given.casefold())
    if c is None:
        raise bad("field", f"В таблице нет поля «{given}».", "Выберите поле из списка колонок таблицы.")
    if c.kind != "text":
        raise bad("field", f"Поле «{c.name}» не текстовое: в нём нет кодов.", "Выберите поле с кодами литологии.")
    return str(c.name)


def lookup(conn: sa.Connection, req: Request, model_id: int) -> Plan:
    """The model, the table and the field of the request against the project (module docstring)."""
    from bf_core.composites import jobs as cj
    from bf_core.drillholes import from_model_spec as fs

    from . import spec as sp
    from . import store as ms
    from . import store_base as sb

    set_id = fs._set(conn, req.set_id)
    t = cj.resolve_table(conn, set_id, req.table)  # 404 `table_not_found`
    name = _field_name(conn, int(t.id), req.column)
    got = ms.get(conn, model_id)  # 404 `model_not_found`
    if got["state"] == "building":
        raise sb.building()
    if not got["outputs"]:
        raise sp.refuse("not_built")
    own = fs.Spec(set_id=set_id, name="", mode="bodies", body={})
    fs._model_bodies(conn, own, model_id, None)  # the priority of «Модель на скважины»; 422 `no_bodies`
    groups = {g.get("id"): g for g in got["meta"].get("groups") or [] if isinstance(g, dict)}
    group_of = {int(o["wireframe_id"]): o.get("group") for o in got["outputs"]}
    labels: list[str] = []
    accepts: list[set[str]] = []
    body_code: list[int] = []
    for wid, code in zip(own.wireframe_ids, own.codes, strict=True):
        if code not in labels:
            labels.append(code)
            codes = (groups.get(group_of.get(wid)) or {}).get("codes") or []
            accepts.append({fold(code), *(fold(str(c)) for c in codes)})
        body_code.append(labels.index(code))
    ids = list(own.wireframe_ids)
    rest = [o for o in got["outputs"] if o.get("kind") == "body" and o.get("group") is None]
    if rest:  # «Прочее»: what is left of the boundary, the last of the priority
        ids.append(int(min(rest, key=lambda o: int(o["wireframe_id"]))["wireframe_id"]))
        body_code.append(len(labels))
    plan = Plan({"id": model_id, "name": str(got["name"]), "stale_reason": own.model["stale_reason"] if own.model
                 else None}, {"id": int(t.id), "name": str(t.name), "field": name}, ids, body_code,
                [*labels, OTHER, OUTSIDE], [*accepts, set(), set()], own.triangles, list(own.journal))  # fmt: skip
    return plan


# --- the comparison ---------------------------------------------------------------------------------------------


@dataclass
class Compared:
    """The intervals compared: arrays parallel to the rows of `lines` that have a path."""

    hole_id: np.ndarray  # int64 dh_holes.id
    name: np.ndarray  # object: the name of the hole
    frm: np.ndarray
    to: np.ndarray
    data: np.ndarray  # int32: the row of the matrix (the last one — no code)
    model: np.ndarray  # int32: the column of the matrix
    data_codes: list[str]  # the codes of the rows (without «Нет кода»)
    no_code: bool
    no_trajectory: dict[str, Any]
    journal: list[str]


def _stretches(
    plan: Plan, project: Any, st: Any, lines: Any, dense: np.ndarray, tok: Any
) -> tuple[Any, np.ndarray, np.ndarray]:
    """The rows of the model on the holes of the table that have a path: (`iv.Lines` of the stretches and the gaps,
    the column of the model of every one, the labels of the holes covered)."""
    import numpy as np

    from bf_core.composites import coding, strips
    from bf_core.composites import intervals as iv
    from bf_core.composites import jobs as cj
    from bf_core.composites import jobs_coding as jc
    from bf_core.drillholes import from_model as fm

    keep = np.flatnonzero(dense >= 0)
    samples, used = fm.whole_samples(st.traj, dense[keep], lines.holes[keep])
    with dialect.read_tx(project.engine) as conn:
        wf = jc.parse_wireframes(conn, plan.wireframe_ids)
    try:
        got = coding.body_stretches(samples, st.traj, used, jc.bodies(project, wf, keep=True),
                                    n_bodies=len(plan.wireframe_ids), threads=strips.default_threads(),
                                    tick=tok.tick)  # fmt: skip
    except ValueError as exc:  # CompositeError `wireframe_open`, `wireframe_orientation` → 422
        raise cj.as_api(exc) from None
    rows = fm.fill_rows(got, samples.holes, samples.to, plan.body_code)
    label = samples.holes[rows.hole]
    cut = np.flatnonzero(label[1:] != label[:-1]) + 1
    starts = np.concatenate(([0], cut, [label.shape[0]])).astype(np.int64)
    code = np.where(rows.code < 0, plan.outside_col, rows.code).astype(np.int32)
    holes = label[starts[:-1]] if label.shape[0] else label[:0]
    return iv.Lines(holes, starts, rows.frm, rows.to), code, samples.holes


def compare(project: Any, plan: Plan, req: Request, tok: Any) -> Compared:
    """The model code of every interval of the table (module docstring)."""
    import numpy as np

    from bf_core.composites import coords, source, strips
    from bf_core.composites import intervals as iv

    def beat(_f: float | None = None, _m: str | None = None) -> None:
        tok.check()

    src = source.read(project.engine, req.set_id, plan.table["id"], [plan.table["field"]],
                      cache_dir=project.folder / "cache" / "tables", tick=beat)  # fmt: skip
    name = plan.table["field"]
    lines = iv.build_lines(src.hole_ids, src.from_m, src.to_m, ids=src.row_ids)
    journal = [*plan.journal, *lines.notes(plan.table["name"])]
    labels = list(src.labels[name])
    col = lines.take(src.codes[name]).astype(np.int64)
    st = source.trajectories(project, req.set_id, tick=beat)
    tix = coords.hole_index(st.hole_ids, lines.holes)
    tix[np.isin(tix, np.asarray(st.traj.skipped, dtype=np.int64))] = -1
    tok.check()
    mine = np.zeros(0, dtype=np.int32)
    covered = np.zeros(0, dtype=np.int64)
    rows = None
    if lines.n and (tix >= 0).any():
        rows, mine, covered = _stretches(plan, project, st, lines, tix, tok)
    # the model code of every interval of a hole that has a path
    hole = lines.hole_ids()
    has = np.isin(hole, covered)
    model = np.full(lines.n, -1, dtype=np.int32)
    if rows is not None and has.any():
        model = iv.mode_by_length(rows, mine, lines, threads=strips.default_threads(), tick=tok.tick)
    model[has & (model < 0)] = plan.outside_col  # the part of the hole the table names is below its path: no body
    sel = np.flatnonzero(has)
    lost = np.flatnonzero(~has)
    nt = {"holes": int(np.unique(hole[lost]).shape[0]), "intervals": int(lost.shape[0]),
          "length": float(lines.lengths()[lost].sum())}  # fmt: skip
    if lost.shape[0]:
        journal.append(f"Скважин без траектории — {nt['holes']}: их интервалов ({nt['intervals']}) в сверке нет. "
                       "Проверьте устья.")  # fmt: skip
    present = np.unique(col[sel][col[sel] >= 0])  # the codes of the intervals compared, in the order of the labels
    remap = np.full(max(len(labels), 1), -1, dtype=np.int64)
    remap[present] = np.arange(present.shape[0])
    none = col[sel] < 0
    data = np.where(none, present.shape[0], remap[np.where(none, 0, col[sel])]).astype(np.int32)
    order = np.argsort(src.hole_db_ids, kind="stable")
    at = order[np.searchsorted(src.hole_db_ids[order], hole[sel])] if sel.shape[0] else order[:0]
    names = np.array(src.holes, dtype=object)[at] if sel.shape[0] else np.zeros(0, dtype=object)
    return Compared(hole[sel], names, lines.frm[sel], lines.to[sel], data, model[sel],
                    [labels[int(i)] for i in present], bool(none.any()), nt, journal)  # fmt: skip


def answer(plan: Plan, cmp: Compared, page: int, size: int) -> dict[str, Any]:
    """The matrices, the sums and the page of the list of mismatches."""
    import numpy as np

    nd = len(cmp.data_codes) + (1 if cmp.no_code else 0)
    nm = len(plan.model_codes)
    length = cmp.to - cmp.frm
    flat = cmp.data.astype(np.int64) * nm + cmp.model
    mat = np.bincount(flat, weights=length, minlength=nd * nm).reshape(nd, nm) if nd else np.zeros((0, nm))
    num = np.bincount(flat, minlength=nd * nm).reshape(nd, nm) if nd else np.zeros((0, nm), dtype=np.int64)
    agree = np.zeros((nd, nm), dtype=np.bool_)
    for i, code in enumerate(cmp.data_codes):
        agree[i] = [fold(code) in a for a in plan.accepts]
    coded = np.arange(nd) < len(cmp.data_codes)
    agree_length = float(mat[agree].sum())
    coded_length = float(mat[coded].sum())
    total = float(mat.sum())
    wrong = np.flatnonzero((cmp.data < len(cmp.data_codes)) & ~agree[np.minimum(cmp.data, max(nd - 1, 0)), cmp.model]
                           if nd else np.zeros(0, dtype=np.bool_))  # fmt: skip
    rank = np.unique(cmp.name[wrong], return_inverse=True)[1] if wrong.shape[0] else np.zeros(0, dtype=np.int64)
    first = np.lexsort((cmp.frm[wrong], rank, -length[wrong]))
    shown = wrong[first][(page - 1) * size : page * size]
    rows = [{"hole": str(cmp.name[i]), "hole_id": int(cmp.hole_id[i]), "from": float(cmp.frm[i]),
             "to": float(cmp.to[i]), "depth": float(0.5 * (cmp.frm[i] + cmp.to[i])),
             "data_code": cmp.data_codes[int(cmp.data[i])], "model_code": plan.model_codes[int(cmp.model[i])],
             "length": float(length[i])} for i in shown.tolist()]  # fmt: skip
    nc = np.flatnonzero(~coded[cmp.data]) if cmp.data.shape[0] else cmp.data[:0]
    return {"model": plan.model, "table": plan.table,
            "model_codes": plan.model_codes, "other_col": plan.other_col, "outside_col": plan.outside_col,
            "data_codes": [*cmp.data_codes, *([NO_CODE] if cmp.no_code else [])],
            "no_code_row": len(cmp.data_codes) if cmp.no_code else None,
            "length": mat.tolist(), "count": num.tolist(), "agree": agree.tolist(),
            "agree_length": agree_length, "total_length": total, "coded_length": coded_length,
            "agree_share": agree_length / coded_length if coded_length > 0 else None,
            "no_code": {"count": int(nc.shape[0]), "length": float(length[nc].sum())},
            "no_trajectory": cmp.no_trajectory, "intervals": int(cmp.data.shape[0]),
            "holes": int(np.unique(cmp.hole_id).shape[0]), "notes": cmp.journal,
            "mismatches": {"page": page, "size": size, "total": int(wrong.shape[0]), "rows": rows}}  # fmt: skip


# --- the request as a calculation of its channel -----------------------------------------------------------------


def run(project: Any, model_id: int, req: Request) -> dict[str, Any]:
    """`lookup`, then `compare` in one of the HEAVY slots of the synchronous calculations; a newer request of the
    channel overtakes this one (409 `superseded`)."""
    from bf_core.blocks import viz_spec as sp

    common = sp.parse_common(req.body)
    with dialect.read_tx(project.engine) as conn:
        plan = lookup(conn, req, model_id)

    def work(tok: Any) -> dict[str, Any]:
        with sp.heavy(tok):
            out = answer(plan, compare(project, plan, req, tok), req.page, req.size)
            tok.check()
            return out

    return sp.overtaken(common, work)  # type: ignore[no-any-return]
