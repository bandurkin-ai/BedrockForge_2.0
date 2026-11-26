"""The other bodies of the resource routes (подшаг 20.1; ADR 0027 решения 22–26, API; docs/progress/e20-api.md
§5–§9): rename, the query of a report, a cell of the report (Д-36), the rows of a cell, the export, the comparison
of two reports and of two estimated fields (Д-37). Pure: no database, no numpy.

    parse_rename(body) -> (name, expected_version)        parse_query(q) -> Query
    parse_drill(body) -> Drill                            parse_drill_rows(body) -> DrillRows
    parse_export(body) -> Export                          parse_compare_reports(body) -> (report_a, report_b)
    parse_field_compare(model_id, body) -> FieldCompare
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from bf_core.geostat.estimate.spec_parts import id_of, list_of, obj, text_of
from bf_core.geostat.variogram.spec import bad

from .spec_parts import (
    EXPORT_FORMATS,
    MAX_KEYS,
    NAME_MAX,
    PAGE_SIZE_MAX,
    PATH_MAX,
    REPORT_PAGE_MAX,
    ROW_KINDS,
    _cutoff,
    _flag,
    _int,
    _name,
    _template_id,
)


def parse_rename(body: Any) -> tuple[str, int]:
    """`PUT /resources/reports/{id}`: `{name, expected_version}` → (name, version); 422 / 409 `version_conflict`."""
    b = obj(body, "Запрос", "body")
    name = text_of(b.get("name"), "Имя отчёта", "name", NAME_MAX)
    assert name is not None
    return name, _expected_version(b)


def _expected_version(b: Mapping[str, Any]) -> int:
    v = b.get("expected_version")
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise bad("bad_param", "Не задана версия объекта.", "Обновите вкладку и повторите.", "expected_version")
    return v


@dataclass(frozen=True)
class Query:
    """`GET /resources/reports/{id}?template=&cutoff=&page=&size=`: the form of a template for a cut-off, the page
    of the rows."""

    template: str | None
    cutoff: float | None
    page: int
    size: int


def parse_query(q: Mapping[str, Any]) -> Query:
    return Query(_template_id(q.get("template"), "template"), _cutoff(q.get("cutoff"), "cutoff"),
                 _int(q.get("page"), "Страница", "page", 1, 10**6, 1),
                 _int(q.get("size"), "Строк на странице", "size", 1, REPORT_PAGE_MAX, 500))  # fmt: skip


@dataclass(frozen=True)
class Drill:
    """A cell of the report (Д-36): the group labels (None — the total), the cut-off and the row kind."""

    report_id: int
    group: tuple[str, ...] | None
    cutoff: float | None
    kind: str


def parse_drill(body: Any) -> Drill:
    """`resources.drill {report_id, group, cutoff, kind?}`: `kind` — by the cut-off (null → `all`, a number →
    `cutoff`) unless `balance` / `offbalance` (the cut-offs are those of the report)."""
    b = obj(body, "Ячейка отчёта", "body")
    rid = id_of(b.get("report_id"), "отчёт", "report_id")
    assert rid is not None
    g = b.get("group")
    group: tuple[str, ...] | None = None
    if g is not None:
        items = list_of(g, "Группа", "group", MAX_KEYS)
        group = tuple(text_of(x, "Подпись группы", f"group.{i}", NAME_MAX) or "" for i, x in enumerate(items)) or None
    cutoff = _cutoff(b.get("cutoff"), "cutoff")
    kind = b.get("kind") or ("all" if cutoff is None else "cutoff")
    if kind not in ROW_KINDS:
        raise bad("bad_param", "Вид строки: неизвестное значение.", f"Допустимо: {', '.join(ROW_KINDS)}.", "kind")
    if (kind == "cutoff") != (cutoff is not None) and kind in ("cutoff", "all"):
        raise bad("bad_param", "Борт задаётся только для строки «cutoff».", "Для строки «без борта» уберите борт.",
                  "cutoff")  # fmt: skip
    return Drill(rid, group, cutoff, str(kind))


@dataclass(frozen=True)
class DrillRows:
    cell: Drill
    page: int
    size: int
    csv: bool  # the CSV text of the page in `csv` of the answer


def parse_drill_rows(body: Any) -> DrillRows:
    """`POST /resources/reports/{id}/drill/rows` — the cell, `page` ≥ 1, `size` 1–1000 (1000), `csv`."""
    b = obj(body, "Таблица блоков", "body")
    return DrillRows(parse_drill(b), _int(b.get("page"), "Страница", "page", 1, 10**6, 1),
                     _int(b.get("size"), "Строк на странице", "size", 1, PAGE_SIZE_MAX, PAGE_SIZE_MAX),
                     _flag(b.get("csv"), False, "CSV", "csv"))  # fmt: skip


@dataclass(frozen=True)
class Export:
    format: str
    path: str
    template: str | None
    cutoff: float | None
    overwrite: bool


def parse_export(body: Any) -> Export:
    """`POST /resources/reports/{id}/export`: `{format: xlsx | csv, path, template?, cutoff?, overwrite?}`; the path
    is checked as absolute by the job (422 `bad_param {param: "path"}`)."""
    b = obj(body, "Выгрузка отчёта", "body")
    fmt = b.get("format")
    if fmt not in EXPORT_FORMATS:
        raise bad("bad_param", "Формат выгрузки: неизвестное значение.", f"Допустимо: {', '.join(EXPORT_FORMATS)}.",
                  "format")  # fmt: skip
    path = text_of(b.get("path"), "Путь к файлу", "path", PATH_MAX)
    assert path is not None
    return Export(str(fmt), path, _template_id(b.get("template"), "template"), _cutoff(b.get("cutoff"), "cutoff"),
                  _flag(b.get("overwrite"), False, "Замена файла", "overwrite"))  # fmt: skip


def parse_compare_reports(body: Any) -> tuple[int, int]:
    """`POST /resources/compare {report_a, report_b}` (A = B is allowed: Δ = 0, ТД37-1)."""
    b = obj(body, "Сравнение отчётов", "body")
    a, c = id_of(b.get("report_a"), "отчёт A", "report_a"), id_of(b.get("report_b"), "отчёт B", "report_b")
    assert a is not None and c is not None
    return a, c


@dataclass(frozen=True)
class FieldCompare:
    model_id: int
    field_a: str
    field_b: str
    domain: str | None  # a category field for the table by domains; None — by the field `DOMAIN`-role code, else none
    out: str | None  # the name of the field Δ; None — «<A>_<B>_D»


def parse_field_compare(model_id: int, body: Any) -> FieldCompare:
    """`POST /blocks/{id}/compare {field_a, field_b, domain?, out?}` (Д-37; `estimate.compare`)."""
    b = obj(body, "Сравнение оценок", "body")

    def opt(k: str, label: str) -> str | None:
        return None if b.get(k) in (None, "") else _name(b[k], label, k)

    a, c = _name(b.get("field_a"), "Поле A", "field_a"), _name(b.get("field_b"), "Поле B", "field_b")
    return FieldCompare(model_id, a, c, opt("domain", "Поле доменов"), opt("out", "Имя поля Δ"))
