"""Builds the workbook that tests/formats/data/*.xls were converted from (LibreOffice, headless)."""

import sys
from datetime import datetime, time

from openpyxl import Workbook

wb = Workbook()
wb.active.title = "Пусто"
s = wb.create_sheet("Устья")
s.append(["Сводка по скважинам"])
s.append(["Скважина", "X, м", "Y, м", "Z, м", "Глубина (м)", "Дата", None, "AU", "Флаг", "Код"])
s.append(["С-1", 466570.586, 4584699.697, 557.95, 300.3, datetime(2024, 5, 17), None, "<0.01", True, "007"])
s.append(["С-2", "466543,962", 4584688.653, 561.438, 250, datetime(2024, 6, 1, 14, 30), None, 0.5, False, "012"])
s.append(["С-3", 466500, 4584600.5, 560, "н/д", None, None, "=NA()", None, "100"])
s.append([None] * 10)
o = wb.create_sheet("Замеры")
o.append(["Скважина", "Глубина", "Азимут", "Наклон", "Время"])
o.append(["С-1", 0, 10, -60, time(8, 30)])
o.append(["С-1", 50, 12.5, -61, time(9, 0)])
wb.save(sys.argv[1])
