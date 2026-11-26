"""Read tools `data_audit` and `data_findings` of the assistant (подшаг 23.8; ТЗ 7.32, 7.12; ADR 0029 решение 18;
e23-api.md §5.2): the stored result of the audit of a drillhole set, read through `audit.service` (the same result
as the tab «Аудит» — nothing is recomputed, the audit is never started from here).

    data_audit(ctx, params) -> dict
        {set_id, set, table, ran_at, stale, counts {e, w, i}, rules [{rule, title, severity, holes, count}],
         passed [, note]} — counts are the numbers of the status bar «Аудит: Э / П / И» (rows by severity), `rules`
        lists the rules that found something, errors first; `passed` — how many of the 27 rules found nothing.
        «Аудит не запускался» — `ran_at` null, `message` says how to run it. «Устарел» — the data of the set changed
        after the run (`stale` true, `note`).
    data_findings(ctx, params) -> dict
        {total, rows [{rule, severity, hole_id, message, table, count, examples}], next_offset, stale, ran_at}:
        the findings grouped by rule and hole, errors first, filters `rule` (code), `severity` (E / W / I),
        `hole_id` (name), pages `offset` / `limit` ≤ 200.

Severity is shown as E (критично), W (предупреждение), I (информация). Texts from the project (names of holes,
examples) are cleaned (`data_table.clean`). Errors are `ApiError` with a Russian message and a hint.
Registered at the bottom with `data_tools.register(name, schema, fn)`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bf_core.audit import service
from bf_core.audit.rules import EXAMPLE_FIELDS, RULES
from bf_core.db import dialect

from .data_table import clean, error, fit, page_args, project_of, require_set, sig6

LETTER = {"error": "E", "warning": "W", "info": "I"}
FULL = {"e": "error", "w": "warning", "i": "info", "error": "error", "warning": "warning", "info": "info"}
ORDER = {"error": 0, "warning": 1, "info": 2}
NOT_RUN = "Аудит не запускался. Запустите «Анализ → Аудит данных» (команда audit.run), затем повторите вопрос."
STALE = "Результат устарел: данные набора изменились после запуска. Запустите аудит заново (команда audit.run)."
MAX_EXAMPLES = 3


def _latest(project: Any, set_id: int) -> dict[str, Any] | None:
    with dialect.read_tx(project.engine) as conn:
        return service.latest(conn, set_id)


def data_audit(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    project = project_of(ctx)
    set_id, name = require_set(project, ctx, params)
    run = _latest(project, set_id)
    if run is None:
        return {"set_id": set_id, "set": clean(name), "ran_at": None, "stale": False, "counts": None, "rules": [],
                "message": NOT_RUN}  # fmt: skip
    totals = run["totals"]
    found = [r for r in run["summary"] if int(r["rows"]) > 0]
    rules = sorted(found, key=lambda r: (ORDER[r["severity"]], -int(r["rows"]), r["code"]))
    out: dict[str, Any] = {
        "set_id": set_id,
        "set": clean(name),
        "table": clean(run["table_name"], 80),
        "ran_at": run["created_at"],
        "stale": bool(run["stale"]),
        "counts": {"e": int(totals["error"]), "w": int(totals["warning"]), "i": int(totals["info"])},
        "rules": [
            {"rule": r["code"], "title": r["title"], "severity": LETTER[r["severity"]], "holes": int(r["holes"]),
             "count": int(r["rows"])}
            for r in rules
        ],
        "passed": len(run["summary"]) - len(found),
    }  # fmt: skip
    if out["stale"]:
        out["note"] = STALE
    return out


def _severity(params: dict[str, Any]) -> str | None:
    raw = params.get("severity")
    if raw in (None, ""):
        return None
    found = FULL.get(str(raw).strip().lower()) if isinstance(raw, str) else None
    if found is None:
        raise error("bad_param", f"Важность «{clean(raw, 40)}» неизвестна.",
                    "Допустимо: E — критично, W — предупреждение, I — информация.")  # fmt: skip
    return found


def _rule(params: dict[str, Any]) -> str | None:
    raw = params.get("rule")
    if raw in (None, ""):
        return None
    code = str(raw).strip().upper() if isinstance(raw, str) else ""
    if code not in RULES:
        raise error("rule_not_found", f"Правило «{clean(raw, 60)}» неизвестно.",
                    "Коды правил даёт data_audit (поле rule), например COLLAR_MISSING_COORDS.")  # fmt: skip
    return code


def _example(e: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in e.items():
        label = EXAMPLE_FIELDS.get(k, clean(k, 40))
        if isinstance(v, bool) or v is None:
            out[label] = v
        elif isinstance(v, int | float):
            out[label] = sig6(float(v)) if isinstance(v, float) else v
        else:
            out[label] = clean(v, 80)
    return out


def data_findings(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    project = project_of(ctx)
    set_id, _name = require_set(project, ctx, params)
    severity, rule = _severity(params), _rule(params)
    hole = params.get("hole_id")
    if hole not in (None, "") and not isinstance(hole, str):
        raise error("bad_param", "Параметр «hole_id» — имя скважины.", "Передайте имя скважины строкой.")
    offset, limit = page_args(params)
    run = _latest(project, set_id)
    if run is None:
        return {"total": 0, "rows": [], "next_offset": None, "ran_at": None, "stale": False, "message": NOT_RUN}
    with dialect.read_tx(project.engine) as conn:
        got = service.findings(conn, run["run_id"], severity=severity, code=rule, hole=hole or None, offset=offset,
                               limit=limit)  # fmt: skip
    rows = []
    for f in got["findings"]:
        r = RULES.get(f["code"])
        rows.append({
            "rule": f["code"],
            "severity": LETTER[f["severity"]],
            "hole_id": None if f["hole"] is None else clean(f["hole"], 80),
            "message": r.title if r else f["code"],
            "table": r.table if r else f["table"],
            "count": int(f["count"]),
            "examples": [_example(e) for e in (f["examples"] or [])[:MAX_EXAMPLES]],
        })  # fmt: skip
    rows = fit(rows)
    end = offset + len(rows)
    out: dict[str, Any] = {
        "total": int(got["total"]),
        "rows": rows,
        "next_offset": end if end < got["total"] else None,
        "ran_at": run["created_at"],
        "stale": bool(run["stale"]),
    }
    if out["stale"]:
        out["note"] = STALE
    return out


# --- registration -----------------------------------------------------------------------------------------------

SCHEMAS: dict[str, dict[str, Any]] = {
    "data_audit": {
        "type": "object",
        "properties": {"set_id": {"type": "integer", "description": "Набор скважин; без него — активный."}},
        "additionalProperties": False,
    },
    "data_findings": {
        "type": "object",
        "properties": {
            "set_id": {"type": "integer", "description": "Набор скважин; без него — активный."},
            "rule": {"type": "string", "description": "Код правила аудита, например COLLAR_MISSING_COORDS."},
            "severity": {
                "type": "string",
                "enum": ["E", "W", "I"],
                "description": "E критично, W предупреждение, I информация.",
            },
            "hole_id": {"type": "string", "description": "Имя скважины."},
            "offset": {"type": "integer", "description": "Сколько находок пропустить."},
            "limit": {"type": "integer", "description": "Находок на странице, до 200."},
        },
        "additionalProperties": False,
    },
}
FUNCTIONS: dict[str, Callable[[Any, dict[str, Any]], dict[str, Any]]] = {
    "data_audit": data_audit,
    "data_findings": data_findings,
}

from .data_tools import register  # noqa: E402  (the common call of 23.7 imports this module after `register`)

for _name, _fn in FUNCTIONS.items():
    register(_name, SCHEMAS[_name], _fn)
