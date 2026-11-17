"""Excel report of the audit (ТЗ 7.12): sheets «Сводка», «По правилам», «Находки»."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import sqlalchemy as sa

from bf_core.db.schema import audit_findings, objects


def _examples(examples: list[dict[str, Any]] | None) -> str:
    out = []
    for e in (examples or [])[:20]:
        out.append("; ".join(f"{k}={v}" for k, v in e.items() if k != "row_id" and v not in (None, "")))
    return " | ".join(out)


def write_report(conn: sa.Connection, set_id: int, res: dict[str, Any], path: Path, *, tick: Any) -> None:
    import openpyxl
    from openpyxl.styles import Font

    from .rules import SEVERITY_LABELS, TABLE_LABELS

    name = conn.execute(sa.select(objects.c.name).where(objects.c.id == set_id)).scalar() or ""
    wb = openpyxl.Workbook(write_only=True)
    bold = Font(bold=True)

    def header(ws: Any, cells: list[str]) -> None:
        from openpyxl.cell import WriteOnlyCell

        row = []
        for text in cells:
            c = WriteOnlyCell(ws, value=text)
            c.font = bold
            row.append(c)
        ws.append(row)

    ws = wb.create_sheet("Сводка")
    header(ws, ["Параметр", "Значение"])
    totals = res["totals"]
    cfg = res["config"]
    for k, v in (
        ("Набор", name),
        ("Проверенная таблица проб", res.get("table_name") or "—"),
        ("Дата аудита", res["created_at"]),
        ("Состояние", "устарел (данные изменены после аудита)" if res.get("stale") else "актуален"),
        ("Критично, строк", totals.get("error", 0)),
        ("Предупреждения, строк", totals.get("warning", 0)),
        ("Информация, строк", totals.get("info", 0)),
        ("Допуск по глубине, м", cfg.get("depth_tolerance")),
        ("Изгиб, ° на 30 м", cfg.get("dogleg_per_30m")),
        ("Изоляция устья, × медианы", cfg.get("isolated_factor")),
        ("Скачок Z, м", cfg.get("z_jump")),
        ("Выброс, робастных σ", cfg.get("assay_outlier_z")),
        ("Признаки плановой скважины", ", ".join(cfg.get("planned_markers") or [])),
    ):
        ws.append([k, v])
    tick(0.3, "Сводка")

    ws = wb.create_sheet("По правилам")
    header(ws, ["Код", "Важность", "Таблица", "Правило", "Описание", "Строк", "Скважин", "Итог"])
    for r in res["summary"]:
        ws.append(
            [
                r["code"],
                SEVERITY_LABELS.get(r["severity"], r["severity"]),
                TABLE_LABELS.get(r["table"], r["table"]),
                r["title"],
                r["description"],
                int(r["rows"]),
                int(r["holes"]),
                "есть замечания" if r["rows"] else "пройдено",
            ]
        )
    tick(0.5, "По правилам")

    ws = wb.create_sheet("Находки")
    header(ws, ["Скважина", "Код", "Важность", "Таблица", "Строк", "Примеры (до 20)"])
    sev = sa.case((audit_findings.c.severity == "error", 0), (audit_findings.c.severity == "warning", 1), else_=2)
    q = (
        sa.select(audit_findings)
        .where(audit_findings.c.run_id == res["run_id"])
        .order_by(audit_findings.c.hole, sev, audit_findings.c.code)
    )
    for f in conn.execute(q.execution_options(yield_per=5000)).mappings():
        ws.append(
            [
                f["hole"] or "—",
                f["code"],
                SEVERITY_LABELS.get(f["severity"], f["severity"]),
                TABLE_LABELS.get(f["source"], f["source"]),
                f["count"],
                _examples(f["examples"]),
            ]
        )
    wb.save(path)
    tick(1.0, "Готово")
