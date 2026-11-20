"""Manual edit of the ore intervals of a result (ТЗ 7.35 Д-26 «Место»; ADR 0018 §12, решения 19, 24; подшаг 12.23):
`POST /composites/results/{id}/edit {hole, action, from, to}` — the hole in one transaction, or nothing.

    with_edits(spec, src, made, edits) -> (made, manual)   the calculation of one hole after its edits
    edit_result(project, result_id, body) -> answer          the route

The kernel (`ore_edit.edit_hole`) is given every edit of the hole — those in `report.edits` and the new one
last — and applies them to the calculation of the hole run again (решение 10: nothing but the edits is kept).
An edit it refuses is 422 `ore_edit_refused` (`ore_bad_param` — not an edit at all): nothing is written.

The transaction (решение 24: the hand edit is the only writer of the rows of a result table; it does not go
through `drillholes/edit.py`, whose checks refuse such tables): the rows of the hole in the result table are
written anew (its old row ids reused in order, the rest inserted or deleted; X, Y, Z of the middle, MODE, the
means of the other fields — as the job writes them; MANUAL from the kernel; a column the user added keeps its
value on a row of the same span, else it is empty — said in `warnings`), ORE_ID and ORE_CLS of the samples of
the hole (unless a later ore result on these samples wrote them again: `results.own_columns`), `report.edits`
+ the edit, `report.summary` by class (Σ of the set − the hole before + the hole after; by domain it stays that
of the calculation), the
revisions of both tables. The marks of the results that read the samples but not ORE_ID / ORE_CLS follow the
new revision (`results.shift_marks`) — the result itself among them: it stays fresh and its protocol opens.
«Перестроить» computes it again from the samples: the edits are lost (the window warns; `routes_ore.rebuild`).

The answer: {rows: {from, to, values} of the hole as the protocol has them (`result`), violations: [{code,
message, hint, from, to}], warnings: [text], report: {edits: every edit of the result}, hole}.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_columns, dh_holes, dh_results, dh_rows, dh_tables

ACTIONS = ("split", "merge", "move")
SAMPLE_COLUMNS = (("ORE_ID", "number"), ("ORE_CLS", "text"))


def hole_edits(report: dict[str, Any] | None, hole: str) -> list[dict[str, Any]]:
    """The stored edits of a hole, in order, as the kernel takes them."""
    return [
        {"action": e["action"], "from": e["from"], "to": e["to"]}
        for e in (report or {}).get("edits") or []
        if e.get("hole") == hole
    ]


def edits_by_index(report: dict[str, Any] | None, holes: list[str]) -> dict[int, list[dict[str, Any]]]:
    """The stored edits by the index of the hole in `holes` (`ore_protocol.long_form`); unknown holes skipped."""
    at = {h: k for k, h in enumerate(holes)}
    out: dict[int, list[dict[str, Any]]] = {}
    for e in (report or {}).get("edits") or []:
        k = at.get(str(e.get("hole")))
        if k is not None:
            out.setdefault(k, []).append({"action": e["action"], "from": e["from"], "to": e["to"]})
    return out


def edited_set(made: Any, src: Any, spec: Any, iv: Any) -> Any:
    """`OreSet` of one hole from its rows `iv` (after edits): the means of the other fields, the domains of the
    rows, the summary — as `ore_set.run_set` makes them."""
    from .ore_set import OreSet, _row_domain, _summary, _weighted

    lines = made.lines
    names = list(spec.means)
    means: dict[str, np.ndarray] = {}
    if names:
        values = np.column_stack([np.ascontiguousarray(src.numbers[n], dtype=np.float64) for n in names])
        out = np.empty((len(iv), len(names)), np.float64)
        thick = lines.thick if lines.thick is not None else np.empty(0, np.float64)
        _weighted(
            0,
            len(iv),
            np.ascontiguousarray(iv.row0),
            np.ascontiguousarray(iv.row1),
            iv.sample_state,
            lines.frm,
            lines.to,
            thick,
            lines.thick is not None,
            values,
            out,
        )
        means = {n: np.ascontiguousarray(out[:, k]) for k, n in enumerate(names)}
    domain = _row_domain(iv, lines)
    return OreSet(iv, domain, means, iv.sample_ore_id, iv.sample_cls, _summary(iv, domain, lines.domain is not None))


def with_edits(
    spec: Any, src: Any, made: Any, edits: list[dict[str, Any]] | None
) -> tuple[Any, np.ndarray | None, Any]:
    """The calculation of one hole (`ore_job.compute_ore` on its samples) after `edits`: (made, MANUAL of every
    row or None — no edits, the `HoleEdit` or None). OreError of the kernel → ApiError 422."""
    from . import ore_job as oj
    from .ore_edit import edit_hole

    if not edits:
        return made, None, None
    try:
        he = edit_hole(made.lines, made.kernel, edits, 0)
    except ValueError as exc:
        raise oj.as_api(exc) from None
    res = edited_set(made, src, spec, he.intervals)
    return replace(made, res=res), np.asarray(he.manual, dtype=np.float64), he


def hole_rows(spec: Any, src: Any, made: Any, manual: np.ndarray | None, xyz: np.ndarray | None) -> Any:
    """The rows of the hole as the result table holds them (`sink.OutTable`), MANUAL put in."""
    from . import ore_job as oj

    out = oj.out_table(spec, src, made, xyz)
    if manual is not None:
        k = next(i for i, c in enumerate(out.columns) if c.name == "MANUAL")
        out.values[k] = manual
    return out


def values_of(out: Any) -> dict[str, Any]:
    """{from, to, values} of rows as the preview and the protocol answer them (no X, Y, Z)."""
    from .routes_run import _nulls

    values = {c.name: _nulls(v) for c, v in zip(out.columns, out.values, strict=True) if c.name not in ("X", "Y", "Z")}
    return {"from": _nulls(out.from_m), "to": _nulls(out.to_m), "values": values}


# --- the transaction ---------------------------------------------------------------------------------------


def _slots(conn: sa.Connection, table_id: int) -> tuple[dict[str, tuple[str, int]], int]:
    """Live columns of a table by name (casefold) → (kind, slot), and the width of its number vectors."""
    cols = conn.execute(
        sa.select(dh_columns.c.name, dh_columns.c.kind, dh_columns.c.slot, dh_columns.c.deleted).where(
            dh_columns.c.table_id == table_id
        )
    ).all()
    width = max([int(c.slot) for c in cols if c.kind == "number"], default=-1) + 1
    return {c.name.casefold(): (c.kind, int(c.slot)) for c in cols if not c.deleted}, width


def _txt(raw: Any) -> dict[str, str]:
    got = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    return {str(k): str(v) for k, v in got.items()}


def _cell(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (float, np.floating)):
        return None if math.isnan(v) else float(v)
    return v


def write_hole_rows(conn: sa.Connection, table_id: int, hole_id: int, out: Any) -> tuple[int, list[str]]:
    """The rows of one hole of a result table replaced by `out` (old ids reused in order). A column the user
    added keeps its value on a row the edit did not change (the same from, to, CLASS, NESTED); the others have
    it empty.
    Returns (Δ rows, the names of the columns of the user emptied on some row)."""
    from bf_core.drillholes.store import pack, unpack

    live, width = _slots(conn, table_id)
    old = conn.execute(
        sa.select(dh_rows.c.id, dh_rows.c.from_m, dh_rows.c.to_m, dh_rows.c.num, dh_rows.c.txt)
        .where(dh_rows.c.table_id == table_id, dh_rows.c.hole_id == hole_id)
        .order_by(dh_rows.c.id)
    ).all()
    ours = {c.name.casefold() for c in out.columns}
    cols = [
        (live[c.name.casefold()], v) for c, v in zip(out.columns, out.values, strict=True) if c.name.casefold() in live
    ]
    user = [(name, kind, slot) for name, (kind, slot) in live.items() if name not in ours]
    at = {c.name: k for k, c in enumerate(out.columns)}
    same_by = [n for n in ("CLASS", "NESTED") if n in at and n.casefold() in live]  # a span may hold two rows

    def old_key(o: Any) -> tuple[Any, ...]:
        nums, txt = unpack(o.num, width), _txt(o.txt)
        got = []
        for n in same_by:
            kind, slot = live[n.casefold()]
            got.append(_cell(nums[slot]) if kind == "number" else txt.get(str(slot)))
        return (float(o.from_m), float(o.to_m), *got)

    def new_key(r: int) -> tuple[Any, ...]:
        got = [_cell(out.values[at[n]][r]) for n in same_by]
        return (float(out.from_m[r]), float(out.to_m[r]), *(None if v == "" else v for v in got))

    by_span: dict[tuple[Any, ...], list[Any]] = {}
    for o in old:
        by_span.setdefault(old_key(o), []).append(o)

    def user_values(o: Any) -> dict[str, Any]:
        nums, txt = unpack(o.num, width), _txt(o.txt)
        got = {name: (None if math.isnan(nums[slot]) else nums[slot]) if kind == "number" else txt.get(str(slot))
               for name, kind, slot in user}  # fmt: skip
        return {k: v for k, v in got.items() if v not in (None, "")}

    n = len(out.from_m)
    for r in range(n):
        nums: list[float | None] = [None] * width
        txt: dict[str, str] = {}
        same = by_span.get(new_key(r))
        if same:
            kept = user_values(same.pop(0))
            for name, kind, slot in user:
                if name in kept:
                    if kind == "number":
                        nums[slot] = float(kept[name])
                    else:
                        txt[str(slot)] = str(kept[name])
        for (kind, slot), vals in cols:
            v = _cell(vals[r])
            if kind == "number":
                nums[slot] = None if v is None else float(v)
            elif v not in (None, ""):
                txt[str(slot)] = str(v)
        row = {
            "hole_id": hole_id,
            "from_m": float(out.from_m[r]),
            "to_m": float(out.to_m[r]),
            "num": pack(nums),
            "txt": txt or None,
        }
        if r < len(old):
            conn.execute(sa.update(dh_rows).where(dh_rows.c.id == old[r].id).values(**row))
        else:
            conn.execute(sa.insert(dh_rows).values(table_id=table_id, **row))
    if len(old) > n:
        conn.execute(sa.delete(dh_rows).where(dh_rows.c.id.in_([o.id for o in old[n:]])))
    lost = {name for left in by_span.values() for o in left for name in user_values(o)}
    q = sa.select(dh_columns.c.name).where(dh_columns.c.table_id == table_id, dh_columns.c.deleted.is_(False))
    names = {c.name.casefold(): c.name for c in conn.execute(q)}
    return n - len(old), sorted(names.get(x, x) for x in lost)


def write_sample_columns(conn: sa.Connection, table_id: int, row_ids: np.ndarray, columns: list[Any]) -> list[str]:
    """ORE_ID and ORE_CLS of the samples of the hole (the columns of the job; one the user deleted is left as it
    is). Returns the names written."""
    from bf_core.drillholes.store import pack, unpack

    live, _width = _slots(conn, table_id)
    todo = [
        (live[s.name.casefold()], v)
        for s, v in columns
        if s.name.casefold() in live and live[s.name.casefold()][0] == s.kind
    ]
    if not todo or not len(row_ids):
        return []
    ids = [int(i) for i in row_ids.tolist()]
    cur = {
        int(r.id): r
        for r in conn.execute(sa.select(dh_rows.c.id, dh_rows.c.num, dh_rows.c.txt).where(dh_rows.c.id.in_(ids)))
    }
    for k, rid in enumerate(ids):
        r = cur.get(rid)
        if r is None:
            continue
        nums: list[float | None] = [None if math.isnan(x) else x for x in unpack(r.num, len(r.num or b"") // 8)]
        txt = _txt(r.txt)
        for (kind, slot), vals in todo:
            v = _cell(vals[k])
            if kind == "number":
                nums += [None] * max(0, slot + 1 - len(nums))
                nums[slot] = None if v is None else float(v)
            elif v in (None, ""):
                txt.pop(str(slot), None)
            else:
                txt[str(slot)] = str(v)
        conn.execute(sa.update(dh_rows).where(dh_rows.c.id == rid).values(num=pack(nums), txt=txt or None))
    return [s.name for s, _v in columns if s.name.casefold() in live]


def _emptied(names: list[str]) -> str:
    fields = ", ".join(f"«{n}»" for n in names)
    if len(names) > 1:
        return f"Поля {fields} очищены в строках скважины, изменённых правкой: заполните их заново."
    return f"Поле {fields} очищено в строках скважины, изменённых правкой: заполните его заново."


def shifted_summary(summary: dict[str, Any], before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """The summary of the set with the hole `before` replaced by `after` (by class; `domains` as they were)."""
    from .jobs_prep import clean
    from .ore_set import CLASS_NAMES, NESTED_CLASSES

    def by(s: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {e["cls"]: e for e in s.get("classes") or []}

    tot, old, new = by(summary), by(before), by(after)
    classes = []
    for cls in CLASS_NAMES:
        parts = [(tot.get(cls), 1.0), (old.get(cls), -1.0), (new.get(cls), 1.0)]
        count = sum(int(e["count"]) * int(s) for e, s in parts if e)
        if count <= 0:
            continue
        m = math.fsum(float(e["m"] or 0.0) * s for e, s in parts if e)
        mc = math.fsum(float(e["mc"] or 0.0) * s for e, s in parts if e)
        classes.append(
            {
                "cls": cls,
                "nested": cls in NESTED_CLASSES,
                "count": count,
                "m": m,
                "m_mean": m / count,
                "c": mc / m if m > 0.0 else None,
                "mc": mc,
            }
        )
    rows = int(summary.get("rows") or 0) - int(before.get("rows") or 0) + int(after.get("rows") or 0)
    return clean({**summary, "classes": classes, "rows": rows})  # type: ignore[no-any-return]


def edit_result(project: Any, result_id: int, body: dict[str, Any]) -> dict[str, Any]:
    """`POST /composites/results/{id}/edit` (see the module docstring)."""
    from bf_core.drillholes.snapshot import snapshots

    from . import jobs_coding as jc
    from . import ore_job as oj
    from . import results, source
    from .routes_ore import fresh_spec, record_of

    hole = body.get("hole")
    if not isinstance(hole, str) or not hole.strip() or body.get("action") not in ACTIONS:
        raise ApiError(
            422,
            "ore_bad_param",
            "Правка задана неверно: нужны скважина, действие и две глубины.",
            "Повторите правку из окна результата.",
        )
    hole = hole.strip()
    edit = {"action": body["action"], "from": body.get("from"), "to": body.get("to")}
    with dialect.read_tx(project.engine) as conn:
        rec = record_of(conn, result_id)
        spec = fresh_spec(conn, rec)
        state = conn.execute(sa.select(dh_tables.c.state).where(dh_tables.c.id == rec["table_id"])).scalar()
        if state != "ready":
            raise ApiError(409, "result_busy", "Результат сейчас пересчитывается.", "Дождитесь окончания задания.")
        hole_id = conn.execute(
            sa.select(dh_holes.c.id).where(dh_holes.c.set_id == spec.set_id, dh_holes.c.name == hole)
        ).scalar()
        if hole_id is None:
            raise ApiError(404, "hole_not_found", f"Скважины «{hole}» нет в наборе.", "Выберите скважину из списка.")
    report = dict(rec["report"] or {})
    stored = hole_edits(report, hole)
    src, dom = oj.read_ore(project.engine, spec, holes=[int(hole_id)])
    if not src.rows:
        raise ApiError(422, "ore_edit_refused", f"В скважине «{hole}» нет проб.", "Выберите другую скважину.")
    st = source.trajectories(project, spec.set_id)
    with jc.reading(project):
        made = oj.compute_ore(spec, src, dom, st)
    before, _m, _he = with_edits(spec, src, made, stored)
    after, manual, he = with_edits(spec, src, made, [*stored, edit])
    assert he is not None and manual is not None
    out = hole_rows(spec, src, after, manual, oj.middles(after, src, st))
    applied = {"hole": hole, **he.edits[-1]}
    with dialect.write_tx(project.engine) as conn:
        now = conn.execute(
            sa.select(dh_results.c.report, dh_results.c.table_id).where(dh_results.c.id == result_id)
        ).first()
        if now is None or now.table_id != rec["table_id"] or (now.report or {}).get("edits") != report.get("edits"):
            raise ApiError(
                409,
                "version_conflict",
                "Результат изменился, пока вы его правили.",
                "Откройте протокол скважины заново и повторите правку.",
            )
        tid = int(rec["table_id"])
        delta, emptied = write_hole_rows(conn, tid, int(hole_id), out)
        results.bump_table(conn, spec.set_id, tid)
        if delta:
            conn.execute(
                sa.update(dh_tables).where(dh_tables.c.id == tid).values(row_count=dh_tables.c.row_count + delta)
            )
        key = conn.execute(sa.select(dh_tables.c.name_key).where(dh_tables.c.id == spec.table_id)).scalar_one()
        mine = results.own_columns(conn, rec, key)  # not those a later ore result on these samples wrote again
        sample = [(s, v) for s, v in oj.sample_columns(after) if s.name.casefold() in mine]
        wrote = write_sample_columns(conn, spec.table_id, src.row_ids, sample)
        samples_rev = None
        if wrote:
            b, a, key = results.bump_table(conn, spec.set_id, spec.table_id)
            results.shift_marks(conn, spec.set_id, key, b, a, wrote)
            samples_rev = a[1]
        rep = {**report, "edits": [*(report.get("edits") or []), applied]}
        if isinstance(report.get("summary"), dict):
            rep["summary"] = shifted_summary(report["summary"], before.res.summary, after.res.summary)
        conn.execute(sa.update(dh_results).where(dh_results.c.id == result_id).values(report=rep))
        rev = int(conn.execute(sa.select(dh_tables.c.rev).where(dh_tables.c.id == tid)).scalar_one())
    snapshots.forget(spec.set_id)
    events = [["drillholes.changed", {"set_id": spec.set_id, "table": str(tid), "rev": rev}]]
    if samples_rev is not None:
        events.append(["drillholes.changed", {"set_id": spec.set_id, "table": str(spec.table_id), "rev": samples_rev}])
    events.append(["composites.changed", {"set_id": spec.set_id}])
    return {
        "hole": hole,
        "rows": values_of(out),
        "violations": he.violations,
        "warnings": [_emptied(emptied)] if emptied else [],
        "report": {"edits": rep["edits"]},
        "events": events,
    }
