"""Registry of the audit rules of a drillhole set (ТЗ 7.12; ported from BF `app/audit/rules.py`).

27 rules in the order of the specification. Titles and descriptions are shown to the user (Russian).
A new rule = an entry here + a check in `checks.py` + a test.
"""

from __future__ import annotations

from dataclasses import dataclass

SEVERITIES = ("error", "warning", "info")
SEVERITY_LABELS = {"error": "Критично", "warning": "Предупреждение", "info": "Информация"}
TABLE_LABELS = {"collar": "Устья", "survey": "Инклинометрия", "assay": "Пробы"}

# Keys of the examples of a finding (`checks.py`) and their names for the user.
EXAMPLE_FIELDS = {
    "row_id": "Строка",
    "x": "X",
    "y": "Y",
    "z": "Z",
    "td": "Глубина скважины",
    "azimuth": "Азимут",
    "dip": "Наклон",
    "status": "Статус",
    "nn_distance": "До ближайшего устья, м",
    "neighbour_z": "Медиана Z соседних устьев",
    "depth": "Глубина",
    "dls": "Изгиб, °/30 м",
    "collar_azimuth": "Азимут устья",
    "collar_dip": "Наклон устья",
    "sample": "Проба",
    "from": "От",
    "to": "До",
    "prev_to": "Конец предыдущих интервалов",
    "component": "Компонент",
    "value": "Значение",
}


@dataclass(frozen=True)
class Rule:
    code: str
    severity: str
    table: str
    title: str
    description: str


_RULES = [
    # Collar
    Rule(
        "COLLAR_MISSING_COORDS", "error", "collar", "Не заданы координаты X/Y/Z", "У устья пустое значение X, Y или Z."
    ),
    Rule(
        "COLLAR_INVALID_TD",
        "error",
        "collar",
        "Глубина скважины не задана или не положительна",
        "TD пустая или меньше либо равна нулю.",
    ),
    Rule(
        "COLLAR_ANGLE_RANGE",
        "error",
        "collar",
        "Азимут или наклон вне допустимого диапазона",
        "Азимут должен быть в пределах 0–360°, наклон — от −90° до 90°.",
    ),
    Rule(
        "COLLAR_ISOLATED",
        "warning",
        "collar",
        "Устье далеко от всех остальных",
        "Расстояние до ближайшей скважины в плане в несколько раз больше типичного: возможна опечатка в X или Y.",
    ),
    Rule(
        "COLLAR_Z_JUMP",
        "warning",
        "collar",
        "Отметка Z резко отличается от соседних устьев",
        "Отличие от медианы ближайших скважин превышает порог: возможна опечатка в Z.",
    ),
    Rule(
        "COLLAR_PLANNED_WITH_DATA",
        "warning",
        "collar",
        "Плановая скважина с данными",
        "Статус указывает на проектную скважину, но по ней есть инклинометрия или пробы.",
    ),
    Rule(
        "COLLAR_EMPTY_STATUS",
        "info",
        "collar",
        "Пустой статус устья",
        "Статус не заполнен: неясно, измерены ли координаты.",
    ),
    Rule(
        "COLLAR_WITHOUT_SURVEY",
        "warning",
        "collar",
        "Скважина без инклинометрии",
        "Для устья нет ни одного замера в Survey.",
    ),
    Rule("COLLAR_WITHOUT_ASSAY", "info", "collar", "Скважина без проб", "Для устья нет ни одного интервала в Assay."),
    # Survey
    Rule(
        "SURVEY_ORPHAN_HOLE",
        "error",
        "survey",
        "Инклинометрия по скважине, которой нет в устьях",
        "Номер скважины из Survey отсутствует в Collar.",
    ),
    Rule(
        "SURVEY_EMPTY_VALUES",
        "warning",
        "survey",
        "Пустые значения глубины, азимута или наклона",
        "Замер не может быть использован для расчёта траектории.",
    ),
    Rule(
        "SURVEY_ANGLE_RANGE",
        "error",
        "survey",
        "Азимут или наклон вне допустимого диапазона",
        "Азимут должен быть в пределах 0–360°, наклон — от −90° до 90°.",
    ),
    Rule("SURVEY_BEYOND_TD", "error", "survey", "Замер глубже забоя", "Глубина замера больше TD скважины."),
    Rule(
        "SURVEY_DUPLICATE_DEPTH",
        "error",
        "survey",
        "Повторяющаяся глубина замера",
        "Две и более записи с одинаковой глубиной в одной скважине.",
    ),
    Rule(
        "SURVEY_NOT_ASCENDING",
        "warning",
        "survey",
        "Замеры идут не по возрастанию глубины",
        "Порядок записей в файле нарушает возрастание глубины.",
    ),
    Rule(
        "SURVEY_FIRST_NOT_ZERO",
        "info",
        "survey",
        "Первый замер не на устье",
        "Первая глубина инклинометрии больше нуля.",
    ),
    Rule(
        "SURVEY_DOGLEG",
        "warning",
        "survey",
        "Слишком резкий изгиб ствола",
        "Интенсивность искривления между замерами выше порога (°/30 м): возможна ошибка в данных.",
    ),
    Rule(
        "SURVEY_COLLAR_MISMATCH",
        "warning",
        "survey",
        "Первый замер расходится с наклоном или азимутом устья",
        "Наклон отличается более чем на 10° либо азимут более чем на 30° (для не вертикальных скважин).",
    ),
    # Assay
    Rule(
        "ASSAY_ORPHAN_HOLE",
        "error",
        "assay",
        "Пробы по скважине, которой нет в устьях",
        "Номер скважины из Assay отсутствует в Collar.",
    ),
    Rule(
        "ASSAY_INVALID_INTERVAL",
        "error",
        "assay",
        "Некорректный интервал",
        "Пустые границы, отрицательное «От» или «От» не меньше «До».",
    ),
    Rule("ASSAY_BEYOND_TD", "error", "assay", "Интервал глубже забоя", "Граница «До» больше TD скважины."),
    Rule(
        "ASSAY_OVERLAP",
        "error",
        "assay",
        "Перекрытие интервалов",
        "Интервал начинается раньше, чем закончился предыдущий.",
    ),
    Rule(
        "ASSAY_GAP",
        "warning",
        "assay",
        "Разрыв между интервалами",
        "Между соседними интервалами есть неопробованный участок.",
    ),
    Rule(
        "ASSAY_DUPLICATE_SAMPLE",
        "error",
        "assay",
        "Повторяющийся номер пробы",
        "Один и тот же номер пробы встречается более одного раза.",
    ),
    Rule("ASSAY_NEGATIVE_VALUE", "error", "assay", "Отрицательное содержание", "Содержание компонента меньше нуля."),
    Rule("ASSAY_EMPTY_VALUE", "warning", "assay", "Пустое содержание компонента", "Значение компонента отсутствует."),
    Rule(
        "ASSAY_OUTLIER",
        "info",
        "assay",
        "Экстремально высокое содержание",
        "Значение много выше типичного уровня компонента (по робастной оценке в логарифмах): "
        "проверьте единицы и опечатки.",
    ),
]

RULES: dict[str, Rule] = {r.code: r for r in _RULES}
