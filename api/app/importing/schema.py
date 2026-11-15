"""Target tables for import and how source column names map onto them."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TargetField:
    key: str
    label: str
    required: bool
    numeric: bool
    # Upper-case source names in priority order: the first one found wins.
    aliases: tuple[str, ...]


_HOLE_ID = TargetField(
    "hole_id", "Номер скважины (HoleID)", True, False,
    ("BHID", "HOLEID", "HOLE_ID", "HOLE", "DH_ID", "ID", "СКВАЖИНА", "СКВ", "№_СКВ", "№_СКВАЖИНЫ", "НОМЕР_СКВАЖИНЫ"),
)

SCHEMAS: dict[str, tuple[TargetField, ...]] = {
    "collar": (
        _HOLE_ID,
        TargetField("x", "X (восток)", True, True, ("X", "EAST", "EASTING", "XCOLLAR", "Х")),  # Cyrillic Х too
        TargetField("y", "Y (север)", True, True, ("Y", "NORTH", "NORTHING", "YCOLLAR", "У")),
        TargetField("z", "Z (отметка)", True, True, ("Z", "RL", "ELEV", "ELEVATION", "ZCOLLAR", "ОТМЕТКА", "АБС_ОТМЕТКА", "АБСОЛЮТНАЯ_ОТМЕТКА")),
        TargetField(
            "td", "Глубина скважины (TD)", True, True,
            ("TDEPTH", "TD", "MAX_DEPTH", "TOTAL_DEPTH", "EOH", "LENGTH", "ГЛУБИНА_СКВАЖИНЫ", "ГЛУБИНА", "ЗАБОЙ"),
        ),
        TargetField("azimuth", "Азимут", False, True, ("AZ", "AZIMUTH", "BRG", "АЗИМУТ", "АЗ")),
        TargetField("dip", "Наклон", False, True, ("DIP", "INCL", "INCLINATION", "НАКЛОН", "УГОЛ_НАКЛОНА")),
        TargetField("date", "Дата", False, False, ("DATE", "DRILL_DATE", "START_DATE", "ДАТА")),
        TargetField("status", "Статус", False, False, ("STATUS", "СТАТУС")),
        TargetField("stage", "Этап", False, False, ("STAGE", "ЭТАП")),
    ),
    "survey": (
        _HOLE_ID,
        TargetField("depth", "Глубина замера", True, True, ("DEPTH", "MD", "AT", "SURVEY_DEPTH", "ГЛУБИНА", "ГЛУБИНА_ЗАМЕРА")),
        # BRG_TRUE first: true azimuth is the default, magnetic BRG is a user choice.
        TargetField(
            "azimuth", "Азимут", True, True,
            ("BRG_TRUE", "AZIMUTH_TRUE", "AZIMUTH", "AZ", "BRG", "АЗИМУТ", "АЗ"),
        ),
        TargetField("dip", "Наклон", True, True, ("DIP", "INCL", "INCLINATION", "НАКЛОН", "УГОЛ_НАКЛОНА")),
    ),
    "assay": (
        _HOLE_ID,
        TargetField("from", "От (From)", True, True, ("FROM", "DEPTH_FROM", "FROM_M", "FROM_DEPTH", "ОТ", "ИНТЕРВАЛ_ОТ", "С")),
        TargetField("to", "До (To)", True, True, ("TO", "DEPTH_TO", "TO_M", "TO_DEPTH", "ДО", "ИНТЕРВАЛ_ДО", "ПО")),
        TargetField(
            "sample_id", "Номер пробы", False, False,
            ("SAMPID", "SAMPLE_ID", "SAMPLEID", "SAMPLE", "SAMPLE_NO", "SAMPNO", "ПРОБА", "№_ПРОБЫ", "НОМЕР_ПРОБЫ", "ШИФР_ПРОБЫ"),
        ),
    ),
    # Any interval table besides the assays: every other column is kept as a value.
    "interval": (
        _HOLE_ID,
        TargetField("from", "От (From)", True, True, ("FROM", "DEPTH_FROM", "FROM_M", "FROM_DEPTH", "ОТ", "ИНТЕРВАЛ_ОТ", "С")),
        TargetField("to", "До (To)", True, True, ("TO", "DEPTH_TO", "TO_M", "TO_DEPTH", "ДО", "ИНТЕРВАЛ_ДО", "ПО")),
    ),
}

KIND_LABELS = {
    "collar": "Устья скважин (Collar)",
    "survey": "Инклинометрия (Survey)",
    "assay": "Пробы (Assay)",
    "interval": "Интервальная таблица (литология, изменения, RQD…)",
}

# Chemical symbols offered as assay components (value columns).
ELEMENTS = frozenset(
    "AU AG PT PD CU PB ZN NI CO FE MN MO W SN AS SB BI HG CD CR V TI AL MG CA NA K S P "
    "U TH REE LI B BA SR ZR F CL".split()
)
# Default units are only suggestions; the user sets the real ones in the wizard.
PRECIOUS = frozenset({"AU", "AG", "PT", "PD"})
