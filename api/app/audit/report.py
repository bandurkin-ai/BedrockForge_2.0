"""Excel export of an audit run."""

from __future__ import annotations

from io import BytesIO

import pandas as pd
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from app.audit.rules import SEVERITY_LABELS, TABLE_LABELS
from app.models import AuditFinding, AuditRun, Project


def _format_examples(details: dict) -> str:
    examples = details.get("examples") or []
    return "; ".join(
        ", ".join(f"{k}={v}" for k, v in example.items() if v is not None) for example in examples
    )


def build_xlsx(project: Project, run: AuditRun, rules: list[dict], findings: list[AuditFinding]) -> bytes:
    severity = run.counts["by_severity"]
    summary = pd.DataFrame(
        [
            ("Проект", project.name),
            ("Дата аудита", run.created_at.strftime("%Y-%m-%d %H:%M")),
            ("Критично (находок)", severity["error"]),
            ("Предупреждения (находок)", severity["warning"]),
            ("Информация (находок)", severity["info"]),
            ("Порог изгиба, °/30 м", run.config["dogleg_per_30m"]),
            ("Допуск по глубине, м", run.config["depth_tolerance"]),
        ],
        columns=["Параметр", "Значение"],
    )
    rules_df = pd.DataFrame(
        [
            {
                "Код": r["code"],
                "Уровень": SEVERITY_LABELS[r["severity"]],
                "Таблица": TABLE_LABELS[r["table"]],
                "Правило": r["title"],
                "Скважин с находками": r["findings"],
                "Строк с находками": r["rows"],
                "Описание": r["description"],
            }
            for r in rules
        ]
    )
    findings_df = pd.DataFrame(
        [
            {
                "Уровень": SEVERITY_LABELS[f.severity],
                "Таблица": TABLE_LABELS[f.table_name],
                "Код": f.code,
                "Скважина": f.hole_id,
                "Строк": f.count,
                "Описание": f.message,
                "Примеры": _format_examples(f.details),
            }
            for f in findings
        ],
        columns=["Уровень", "Таблица", "Код", "Скважина", "Строк", "Описание", "Примеры"],
    )

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, frame in (("Сводка", summary), ("По правилам", rules_df), ("Находки", findings_df)):
            frame.to_excel(writer, sheet_name=name, index=False)
            sheet = writer.sheets[name]
            sheet.freeze_panes = "A2"
            for cell in sheet[1]:
                cell.font = Font(bold=True)
            for idx, column in enumerate(frame.columns, start=1):
                longest = max([len(str(column))] + [len(str(v)) for v in frame[column].head(200)])
                sheet.column_dimensions[get_column_letter(idx)].width = min(max(longest + 2, 10), 70)
    return buffer.getvalue()

