"""«Анализ кондиций» (ТЗ 7.35 Д-26, Т8.2-20; ADR 0018 §12, решения 23, 27; подшаг 12.23): the job
`composite.conditions_analysis` — the kernel of Д-26 for every variant Сб × Мрт × Мпп (`ore_conditions`) on the
samples of a table; a result without a table: the record `dh_results` op `conditions` with `report.variants`.

    POST /composites/conditions {set_id, table, field, cutoffs[], m_min[], m_waste_max[], mode, missing,
        missing_max?, mc_min?, c_min?, thickness {source, field?}, domain?, contacts_values?,
        exclude? {field, values: string[]}, name?} → {source, id}

422 before the job: `ore_bad_param` (> 200 variants — param `cutoffs`; a value of a list — its list), the
refusals of `POST /ore` (`ore_job.parse_ore`), `bad_param` `exclude` (no field or no values); 404
`field_not_found`. A repeat for the same table and field replaces the earlier record. The result of the job:
{set_id, result_id, name, variants, unit, count, journal, events}. A cancel leaves nothing.

    ore_lines(spec, src, dom, st) -> (lines, kernel, journal)   the samples as the kernel takes them (no run)
"""

from __future__ import annotations

import time
from dataclasses import replace
from itertools import product
from types import SimpleNamespace
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_results
from bf_core.jobs.context import JobContext
from bf_core.jobs.registry import handler

from . import jobs as cj

KIND = "composite.conditions_analysis"
OP = "conditions"
LISTS = (
    ("cutoffs", "cutoff", "Бортовые содержания Сб"),
    ("m_min", "m_min", "Минимальные мощности Мрт"),
    ("m_waste_max", "m_waste_max", "Максимальные мощности прослоя Мпп"),
)


def _list(raw: Any, key: str, title: str) -> list[float]:
    items = raw.replace(";", ",").split(",") if isinstance(raw, str) else raw if isinstance(raw, list) else [raw]
    out: set[float] = set()
    for v in items:
        if isinstance(v, str) and not v.strip():
            continue
        try:
            x = float(str(v).strip().replace(",", ".")) if isinstance(v, str) else float(v)
        except (TypeError, ValueError):
            x = float("nan")
        if isinstance(v, bool) or x != x or x in (float("inf"), float("-inf")):
            raise cj._bad("ore_bad_param", f"{title}: «{v}» — не число.", "Введите значения числами через «;».", key)
        out.add(x)
    if not out:
        raise cj._bad("ore_bad_param", f"{title}: задайте хотя бы одно значение.", "Введите значения через «;».", key)
    return sorted(out)


def _exclude(conn: sa.Connection, table_id: int, raw: Any) -> dict[str, Any] | None:
    """`exclude {field, values}` (решение 23): the field of the samples and the values as typed (strings)."""
    from .source import _fields

    if raw is None or raw == {} or raw == "":
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("field"), str) or not raw["field"].strip():
        raise cj._bad(
            "bad_param", "Исключение проб: не выбрано поле.", "Выберите поле или уберите исключение.", "exclude"
        )
    values = raw.get("values")
    values = values.replace(";", ",").split(",") if isinstance(values, str) else values
    texts = [str(v).strip() for v in values or [] if str(v).strip()] if isinstance(values, list) else []
    if not texts:
        raise cj._bad(
            "bad_param",
            "Исключение проб: не заданы значения поля.",
            "Перечислите значения через «,», например: 1, 2.",
            "exclude",
        )
    f = next(iter(_fields(conn, table_id, [raw["field"].strip()]).values()))
    return {"field": f.name, "values": texts}


def parse_conditions(conn: sa.Connection, body: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """The body of `POST /composites/conditions` checked: (params of the job, its title); ApiError 422 / 404."""
    from . import ore_job as oj
    from .ore_conditions import MAX_VARIANTS
    from .ore_types import OreParams

    lists = {k: _list(body.get(k), k, title) for k, _one, title in LISTS}
    total = len(lists["cutoffs"]) * len(lists["m_min"]) * len(lists["m_waste_max"])
    if total > MAX_VARIANTS:
        raise cj._bad(
            "ore_bad_param",
            f"Вариантов {total}: не больше {MAX_VARIANTS}.",
            "Сократите списки Сб, Мрт или Мпп.",
            "cutoffs",
        )
    first = {one: lists[k][0] for k, one, _t in LISTS}
    plain = {
        k: v for k, v in body.items() if k not in ("cutoffs", "m_min", "m_waste_max", "exclude", "name", "replace")
    }
    spec = oj.parse_ore(conn, {**plain, **first})
    for cb, mrt, mpp in product(lists["cutoffs"], lists["m_min"], lists["m_waste_max"]):  # every value checked
        try:
            OreParams(
                cb,
                mrt,
                mpp,
                mc_min=oj._optional(body, "mc_min"),
                c_min=oj._optional(body, "c_min"),
                mode=spec.kernel.mode,
                missing=spec.kernel.missing,
                missing_max=spec.kernel.missing_max,
            ).checked()
        except ValueError as exc:
            err = oj.as_api(exc)
            key = {"cutoff": "cutoffs"}.get(str(err.extra.get("param")), err.extra.get("param"))
            raise cj._bad(err.code, err.message, err.hint, key) from None
    exclude = _exclude(conn, spec.table_id, body.get("exclude"))
    name = body.get("name")
    name = name.strip() if isinstance(name, str) and name.strip() else f"Анализ кондиций · {spec.field}"
    checked = {
        **{k: v for k, v in body.items() if k not in ("replace", "exclude")},
        **lists,
        "name": name,
        **({"exclude": exclude} if exclude else {}),
    }
    return {"body": checked, "rows": spec.rows}, f"{name} ({spec.table_name})"


def ore_lines(spec: Any, src: Any, dom: Any, st: Any) -> tuple[Any, Any, list[str]]:
    """The samples read as `OreLines` and the parameters with the codes of the body of «по контактам» — what
    `ore_job.compute_ore` gives the kernel, without running it (the long form and the analysis run it)."""
    from . import ore_job as oj
    from .ore_types import OreLines

    journal: list[str] = []
    if src.skipped:
        journal.append(f"Таблица «{spec.table_name}»: строк без «от» или «до» — {src.skipped}, поля пусты.")
    codes, names = oj._domain_codes(spec, src, dom, st, journal)
    kernel = spec.kernel
    if kernel.mode == "contacts" and spec.contacts is not None:
        kernel = replace(kernel, contacts_values=oj.body_codes(spec.contacts, names)[0])
    thick = src.numbers[spec.thick] if spec.thick else None
    try:
        lines = OreLines.of(src.starts, src.from_m, src.to_m, src.numbers[spec.field], thick, codes)
    except ValueError as exc:
        raise oj.as_api(exc) from None
    return lines, kernel, journal


def _column(src: Any, name: str) -> np.ndarray:
    if name in src.numbers:
        return np.asarray(src.numbers[name])
    labels = np.array([*src.labels[name], None], dtype=object)
    codes = np.asarray(src.codes[name], dtype=np.int64)
    return labels[np.where(codes < 0, len(labels) - 1, codes)]  # type: ignore[no-any-return]


def run_conditions(
    engine: sa.Engine, folder: Any, params: dict[str, Any], *, job_id: int, tick: cj.Tick2
) -> dict[str, Any]:
    """The body of `composite.conditions_analysis` (the handler and the tests)."""
    from . import jobs_coding, results, source, strips
    from . import ore_job as oj
    from .ore_conditions import conditions_analysis, exclusion

    t0 = time.perf_counter()
    body = dict(params.get("body") or {})
    with dialect.read_tx(engine) as conn:
        again, _title = parse_conditions(conn, body)
        body = again["body"]
        plain = {k: v for k, v in body.items() if k not in ("cutoffs", "m_min", "m_waste_max", "exclude", "name")}
        spec = oj.parse_ore(conn, {**plain, **{one: body[k][0] for k, one, _t in LISTS}})
        exclude = body.get("exclude")
        fields = list(dict.fromkeys([*spec.read_fields(), *([exclude["field"]] if exclude else [])]))
        tables = [(spec.table_id, fields), *spec.tables()[1:]]
        by_wf = spec.domain is not None and spec.domain.wireframes is not None
        inputs = results.make_inputs(conn, spec.set_id, tables, trajectories=by_wf, meshes=spec.mesh_ids())
    tick(0.02, "Чтение проб")
    cache = folder / "cache" / "tables"
    src = source.read(engine, spec.set_id, spec.table_id, fields, cache_dir=cache)
    d = spec.other_domain()
    dom = None if d is None else source.read(engine, spec.set_id, d.table_id, [d.field], cache_dir=cache)
    project = SimpleNamespace(engine=engine, uid=f"job:{folder}", folder=folder)
    st = source.trajectories(project, spec.set_id) if by_wf else None
    with jobs_coding.reading(project, keep=False):
        lines, kernel, journal = ore_lines(spec, src, dom, st)
    mask = exclusion(_column(src, exclude["field"]), exclude["values"]) if exclude else None
    tick(0.1, "Варианты кондиций")
    try:
        found = conditions_analysis(
            lines,
            body["cutoffs"],
            body["m_min"],
            body["m_waste_max"],
            mode=kernel.mode,
            missing=kernel.missing,
            missing_max=kernel.missing_max,
            contacts_values=kernel.contacts_values,
            mc_min=oj._optional(body, "mc_min"),
            c_min=oj._optional(body, "c_min"),
            exclude=mask,
            threads=strips.default_threads(),
            tick=lambda f: tick(0.1 + 0.85 * f, None),
        )
    except ValueError as exc:
        raise oj.as_api(exc) from None
    if found.stats.get("excluded"):
        journal.append(f"Исключено проб по полю «{exclude['field']}»: {found.stats['excluded']}.")  # type: ignore[index]
    stations = source.stations_bytes(st, src.hole_db_ids.tolist()) if st is not None else None
    hashed = {
        **spec.hash_params(),
        "op": OP,
        "lists": [body[k] for k, _o, _t in LISTS],
        "exclude": exclude,
        "mc_min": oj._optional(body, "mc_min"),
        "c_min": oj._optional(body, "c_min"),
    }
    sha = source.input_hash([src, *([dom] if dom else [])], hashed, stations=stations, algo=oj.ALGO)
    from .jobs_prep import clean

    report = clean(
        {
            **found.as_dict(),
            "unit": spec.unit,
            "field": spec.field,
            "exclude": exclude,
            "journal": journal,
            "versions": cj._versions(),
            "seconds": round(time.perf_counter() - t0, 3),
        }
    )
    key = inputs["tables"][0]["key"]
    # no tick from here on: one transaction, the earlier analysis of this table and field replaced
    with dialect.write_tx(engine) as conn:
        for r in conn.execute(
            sa.select(dh_results.c.id, dh_results.c.params, dh_results.c.inputs).where(
                dh_results.c.set_id == spec.set_id, dh_results.c.op == OP
            )
        ).all():
            mine = ((r.inputs or {}).get("tables") or [{}])[0].get("key") == key
            if mine and str((r.params or {}).get("field") or "").casefold() == spec.field.casefold():
                conn.execute(sa.delete(dh_results).where(dh_results.c.id == r.id))
        rid = results.record(
            conn,
            set_id=spec.set_id,
            table_id=None,
            op=OP,
            name=body["name"],
            params=body,
            inputs=inputs,
            sha256=sha,
            algo=oj.ALGO,
            report=report,
            job_id=job_id,
        )
    return {
        "set_id": spec.set_id,
        "result_id": rid,
        "name": body["name"],
        "variants": report["variants"],
        "unit": spec.unit,
        "count": len(found.variants),
        "fresh": False,
        "journal": journal,
        "events": [["composites.changed", {"set_id": spec.set_id}]],
    }


@handler(KIND, "Анализ кондиций", queue="heavy", estimate_memory=cj.memory_mb)
def conditions_job(ctx: JobContext) -> dict[str, Any]:
    assert ctx.project_folder is not None
    try:
        out = run_conditions(ctx.engine(), ctx.project_folder, ctx.params, job_id=ctx.id, tick=ctx.tick)
    except ApiError as exc:
        raise cj._user(exc) from None
    cj._log_journal(ctx, out.get("journal") or [])
    return out
