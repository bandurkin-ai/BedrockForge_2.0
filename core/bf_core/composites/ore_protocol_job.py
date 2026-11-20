"""Export of the protocol of ore intervals (ТЗ 7.35 Д-26 «Результат» (4); ADR 0018 §9, решения 10, 19, 30; подшаг
12.23): the job `composite.ore_protocol` — the book XLSX or the CSV files of a result (`ore_report`).

    POST /composites/results/{id}/protocol {format: "xlsx" | "csv", path, holes?: string[], overwrite?}
        → {source, id}

Only of a fresh result of the current `algo` (решение 19): otherwise 409 `stale`. 422 `bad_format`,
`path_not_absolute`, `bad_param` (`holes`: more than 50 or not in the set), `no_protocol` (not «Рудные
интервалы» nor «Анализ кондиций»); 404 `folder_not_found`, `result_not_found`; 409 `files_exist {files}` —
a file to write is there already: the window asks «Заменить?» and repeats with `overwrite: true`.

The protocol is not stored (решение 10): the kernel runs again on the samples with the parameters of the
result, the manual edits of `report.edits` applied to their holes (`ore_protocol.long_form(edits=)`); the
rows of «Интервалы» and «Сводка» are those of the result table. `holes` — the sheets «Скв. ‹имя›» (at most
50); not given — every hole when the set has at most 50 of them, else none. One row of `export_log` per file.
The result of the job: {path, files, format, intervals, records, holes, sheets, seconds}.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_holes, dh_results, export_log
from bf_core.jobs.context import JobContext
from bf_core.jobs.registry import handler

from . import jobs as cj
from . import ore_report as rep

KIND = "composite.ore_protocol"
FORMATS = ("xlsx", "csv")
MISSING_TITLES = {"break": "разрыв линии опробования", "waste": "пустая порода до L_н"}


def _memory(params: dict[str, Any]) -> int:
    return max(512, int(int(params.get("rows") or 0) * 400 / 2**20) + 256)


def target_path(path: Any, fmt: str) -> Path:
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise ApiError(422, "path_not_absolute", "Путь к файлу должен быть полным.", "Выберите файл в окне сохранения.")
    target = Path(path)
    if target.suffix.lower() != f".{fmt}":
        target = target.with_name(f"{target.name}.{fmt}")
    if not target.parent.is_dir():
        raise ApiError(404, "folder_not_found", "Папка для файла не найдена.", "Выберите папку заново.")
    return target


def files_exist(files: list[Path]) -> ApiError:
    names = ", ".join(f"«{p.name}»" for p in files)
    return ApiError(
        409,
        "files_exist",
        f"Файлы уже есть: {names}.",
        "Подтвердите замену или выберите другое имя файла.",
        {"files": [str(p) for p in files]},
    )


def parse_export(conn: sa.Connection, rec: dict[str, Any], body: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """The body of `POST /results/{id}/protocol` checked: (params of the job, its title)."""
    from .routes_ore import fresh_spec

    fmt = str(body.get("format") or "").lower()
    if fmt not in FORMATS:
        raise ApiError(422, "bad_format", "Формат протокола — XLSX или CSV.", "Выберите формат файла.")
    if rec["op"] not in ("ore", "conditions"):
        raise ApiError(
            422,
            "no_protocol",
            "У этого результата нет протокола оконтуривания.",
            "Протокол есть у результатов «Рудные интервалы» и «Анализ кондиций».",
        )
    target = target_path(body.get("path"), fmt)
    holes = body.get("holes")
    holes = [str(h).strip() for h in holes if str(h).strip()] if isinstance(holes, list) else None
    rows = 0
    if rec["op"] == "conditions":
        fresh_conditions(conn, rec)
    if rec["op"] == "ore":
        spec = fresh_spec(conn, rec, "выгрузите протокол")
        rows = spec.rows
        if holes:
            holes = list(dict.fromkeys(holes))
            if len(holes) > rep.HOLE_SHEETS:
                raise cj._bad(
                    "bad_param",
                    f"Листов скважин {len(holes)}: не больше {rep.HOLE_SHEETS}.",
                    "Выберите меньше скважин для листов «Скв. …».",
                    "holes",
                )
            known = set(
                conn.execute(
                    sa.select(dh_holes.c.name).where(dh_holes.c.set_id == spec.set_id, dh_holes.c.name.in_(holes))
                ).scalars()
            )
            unknown = [h for h in holes if h not in known]
            if unknown:
                raise cj._bad(
                    "bad_param", f"Скважины «{unknown[0]}» нет в наборе.", "Выберите скважины из списка.", "holes"
                )
    files = rep.files_for(target, fmt, rec["op"])
    clash = [p for p in files if p.exists()]
    if clash and body.get("overwrite") is not True:
        raise files_exist(clash)
    params = {"result_id": int(rec["id"]), "format": fmt, "path": str(target), "holes": holes, "rows": rows}
    return params, f"Протокол: {rec['name']} ({fmt.upper()})"


def fresh_conditions(conn: sa.Connection, rec: dict[str, Any]) -> None:
    """409 `stale`: «Анализ кондиций» out of date or of another `algo` is not exported (решение 19)."""
    from . import ore_job as oj
    from .routes_ore import stale_reason

    reason = stale_reason(conn, rec, oj.ALGO)
    if reason is not None:
        raise ApiError(409, "stale", f"Результат устарел: {reason}.", "Перестройте результат, затем выгрузите его.")


def _params_rows(rec: dict[str, Any], spec: Any, extra: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    from bf_core import __version__

    from .ore_types import MODE_TITLES

    k = spec.kernel
    d = spec.domain
    domain = "нет"
    if d is not None:
        domain = (
            f"каркасы: {', '.join(d.wireframes.names())}" if d.wireframes is not None else f"{d.table_name} · {d.field}"
        )
    unit = spec.unit or ""
    u = f", {unit}" if unit else ""
    return [
        ("Результат", rec["name"]),
        ("Таблица проб", spec.table_name),
        ("Поле содержания", spec.field),
        ("Единица содержания", unit or None),
        (f"Бортовое содержание Сб{u}", k.cutoff),
        ("Минимальная мощность рудного тела Мрт, м", k.m_min),
        ("Максимальная мощность прослоя Мпп, м", k.m_waste_max),
        (f"Минимальный метропроцент МС, м{'·' + unit if unit else ''}", k.mc_min),
        (f"Минимальное содержание Смин{u}", k.c_min),
        ("Режим", MODE_TITLES.get(k.mode, k.mode)),
        ("Пропуски", MISSING_TITLES.get(k.missing, k.missing)),
        ("L_н, м", k.missing_max if k.missing == "waste" else None),
        ("Мощность", f"поле «{spec.thick}»" if spec.thick else "длина по стволу"),
        ("Домен", domain),
        ("Коды тела («по контактам»)", ", ".join(map(str, spec.contacts)) if spec.contacts else None),
        ("Правила (algo)", rec["algo"]),
        ("Результат создан", _when(rec["created_at"])),
        ("Ручных правок", len((rec["report"] or {}).get("edits") or [])),
        *extra,
        ("Выгружено", datetime.now().astimezone().strftime("%d.%m.%Y %H:%M")),
        ("Программа", f"BedrockForge 2 {__version__}"),
    ]


def _when(v: Any) -> str | None:
    return None if v is None else v.astimezone().strftime("%d.%m.%Y %H:%M")


def _intervals(engine: sa.Engine, set_id: int, table_id: int) -> tuple[list[Any], int]:
    """The columns and the number of rows of the result table, as «Таблица» shows them (`drillholes.export`)."""
    from bf_core.drillholes.export import read_table, shown_columns

    with dialect.read_tx(engine) as conn:
        snap, _src = read_table(conn, set_id, str(table_id))
    return shown_columns(snap, None)


def _column(columns: list[Any], n: int, name: str) -> list[Any] | None:
    from bf_core.drillholes.export import _portion

    col = next((c for c in columns if c.name == name), None)
    return None if col is None else _portion(col, 0, n)


def _rows(columns: list[Any], n: int) -> Any:
    from bf_core.drillholes.export import _portion

    for start in range(0, n, rep.PORTION):
        stop = min(n, start + rep.PORTION)
        yield from zip(*[_portion(c, start, stop) for c in columns], strict=True)


def _ore_report(
    engine: sa.Engine, folder: Path, rec: dict[str, Any], params: dict[str, Any], tick: cj.Tick2
) -> tuple[list[rep.Table | rep.HoleSheet], dict[str, Any], list[rep.Table]]:
    from . import jobs_coding, source
    from . import ore_job as oj
    from .ore_conditions_job import ore_lines
    from .ore_edit_job import edits_by_index
    from .ore_protocol import LONG_HEAD, hole_protocol, long_form
    from .routes_ore import fresh_spec

    with dialect.read_tx(engine) as conn:
        spec = fresh_spec(conn, rec, "выгрузите протокол")
    tick(0.02, "Чтение проб")
    src, dom = oj.read_ore(engine, spec, cache_dir=folder / "cache" / "tables")
    project = SimpleNamespace(engine=engine, uid=f"job:{folder}", folder=folder)
    by_wf = spec.domain is not None and spec.domain.wireframes is not None
    st = source.trajectories(project, spec.set_id) if by_wf else None
    with jobs_coding.reading(project, keep=False):
        lines, kernel, _journal = ore_lines(spec, src, dom, st)
    edits = edits_by_index(rec["report"], src.holes)
    tick(0.08, "Таблица результата")
    columns, n = _intervals(engine, spec.set_id, int(rec["table_id"]))
    m, mc = _column(columns, n, "M") or [], _column(columns, n, "MC") or []
    summary = rep.summary_table(
        _column(columns, n, "CLASS") or [],
        _column(columns, n, "NESTED") or [0] * n,
        m,
        mc,
        _column(columns, n, "DOMAIN"),
        spec.unit,
    )
    holes = params.get("holes")
    if holes is None:
        holes = list(src.holes) if len(src.holes) <= rep.HOLE_SHEETS else []
    at = {h: k for k, h in enumerate(src.holes)}
    sheets: list[rep.HoleSheet] = []
    for h in holes:
        if h in at:  # a hole without samples in the table has no protocol
            hp = hole_protocol(lines, kernel, at[h], unit=spec.unit or "", edits=edits.get(at[h]))
            sheets.append(rep.HoleSheet(h, hp.grid()))
    count = {"records": 0}
    share = (0.15, 0.8)

    def long_rows() -> Any:
        step = lambda f: tick(share[0] + (share[1] - share[0]) * f, "Протокол")  # noqa: E731
        for block in long_form(lines, kernel, edits=edits, tick=step):
            rows = block.rows(src.holes)
            count["records"] += len(rows)
            yield from rows

    extra: list[tuple[str, Any]] = [
        ("Скважин с пробами", len(src.holes)),
        ("Проб", src.rows),
        ("Интервалов (строк таблицы)", n),
        ("Листов «Скв. …»", len(sheets)),
    ]
    if params.get("holes") is None and len(src.holes) > rep.HOLE_SHEETS:
        extra.append(("Листы скважин", f"не выгружены: скважин больше {rep.HOLE_SHEETS} — выберите их в окне"))
    widths = [14.0, 7, 6, 22, 6, 12, 8, 8, 9, 9, 9, 9, 10, 13, 22, 24]
    protocol = rep.Table("Протокол", list(LONG_HEAD), long_rows(), widths=widths)
    intervals = rep.Table("Интервалы", [c.name for c in columns], _rows(columns, n))
    book: list[rep.Table | rep.HoleSheet] = [
        rep.params_table(_params_rows(rec, spec, extra)),
        intervals,
        summary,
        protocol,
        *sheets,
    ]
    counts = {"intervals": n, "holes": len(src.holes), "sheets": len(sheets), "_count": count}
    return book, counts, [protocol, rep.Table("Интервалы", [c.name for c in columns], _rows(columns, n))]


def _conditions_report(
    conn: sa.Connection, rec: dict[str, Any]
) -> tuple[list[rep.Table | rep.HoleSheet], dict[str, Any]]:
    fresh_conditions(conn, rec)  # again in the job: the result may have gone stale after the start
    r = rec["report"] or {}
    p = rec["params"] or {}
    ex = r.get("exclude") or p.get("exclude")
    rows = [
        ("Результат", rec["name"]),
        ("Поле содержания", r.get("field") or p.get("field")),
        ("Единица содержания", r.get("unit")),
        ("Режим", r.get("mode_title")),
        ("Бортовые содержания Сб", "; ".join(map(str, r.get("cutoffs") or []))),
        ("Минимальные мощности Мрт, м", "; ".join(map(str, r.get("m_min") or []))),
        ("Максимальные мощности прослоя Мпп, м", "; ".join(map(str, r.get("m_waste_max") or []))),
        ("Исключены пробы", f"{ex['field']}: {', '.join(ex['values'])}" if ex else None),
        ("Исключено проб", r.get("excluded")),
        ("Проб", r.get("samples")),
        ("Скважин", r.get("holes")),
        ("Состояние", "актуален"),
        ("Результат создан", _when(rec["created_at"])),
        ("Выгружено", datetime.now().astimezone().strftime("%d.%m.%Y %H:%M")),
    ]
    table = rep.conditions_table(r.get("variants") or [], r.get("unit"))
    return [rep.params_table(rows), table], {"variants": len(r.get("variants") or [])}


def run_export(
    engine: sa.Engine, folder: Path, params: dict[str, Any], *, job_id: int, tick: cj.Tick2
) -> dict[str, Any]:
    """The body of `composite.ore_protocol` (the handler and the tests)."""
    t0 = time.perf_counter()
    fmt, target = params["format"], Path(params["path"])
    with dialect.read_tx(engine) as conn:
        got = conn.execute(sa.select(dh_results).where(dh_results.c.id == int(params["result_id"]))).mappings().first()
        if got is None:
            raise ApiError(404, "result_not_found", "Результат обработки не найден.", "Обновите дерево проекта.")
        rec = dict(got)
        book: list[rep.Table | rep.HoleSheet] = []
        csvs: list[rep.Table] = []
        if rec["op"] == "conditions":
            book, counts = _conditions_report(conn, rec)
            csvs = [t for t in book[1:] if isinstance(t, rep.Table)]
    if rec["op"] == "ore":
        book, counts, csvs = _ore_report(engine, folder, rec, params, tick)
    files = rep.files_for(target, fmt, rec["op"])

    def write(parts: list[Path]) -> None:
        if fmt == "xlsx":
            rep.write_xlsx(parts[0], book, tick)
        else:
            for part, table in zip(parts, csvs, strict=True):
                rep.write_csv(part, table, tick)

    rep.write_files(files, write)
    # no tick from here on: a cancel after the files are in place would report them as not written
    records = counts.pop("_count", {}).get("records", 0)
    out_counts = {**counts, **({"records": records} if rec["op"] == "ore" else {})}
    with dialect.write_tx(engine) as conn:
        for f in files:
            conn.execute(
                sa.insert(export_log).values(
                    object_id=int(rec["set_id"]),
                    job_id=job_id,
                    file_name=f.name,
                    format=fmt,
                    settings={
                        "kind": "ore_protocol",
                        "result_id": int(rec["id"]),
                        "op": rec["op"],
                        "holes": params.get("holes"),
                    },
                    counts=out_counts,
                    created_at=dialect.utcnow(),
                )
            )
    return {
        "path": str(target),
        "files": [str(f) for f in files],
        "format": fmt,
        **out_counts,
        "seconds": round(time.perf_counter() - t0, 3),
    }


@handler(KIND, "Протокол рудных интервалов", queue="heavy", estimate_memory=_memory)
def protocol_job(ctx: JobContext) -> dict[str, Any]:
    assert ctx.project_folder is not None
    try:
        out = run_export(ctx.engine(), ctx.project_folder, ctx.params, job_id=ctx.id, tick=ctx.tick)
    except ApiError as exc:
        raise cj._user(exc) from None
    return out
