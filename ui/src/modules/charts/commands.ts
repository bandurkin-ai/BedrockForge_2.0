/**
 * Charts of sample data (ТЗ 7.25–7.28, 8.3; ADR 0019 §9): the commands `chart.*` of Э13 with their parameters —
 * the ribbon «Анализ → Графики», the set's menu, the tree «Графики», Ctrl+K and the assistant run the same
 * commands. Until a chart's window is bound (`bind.ts`) its command stays unavailable with the stage.
 * `CHART_BUTTONS` — the buttons of the ribbon group (shell/ribbonConfig.ts puts them in).
 * Also here: «Данные по скважинам» as text (Э6) and the block model report (Э17).
 */
import type { Command, ParamSchema } from "../../shell/commands";

const STAGE = "Э13";
const P = (type: ParamSchema["type"], description: string, extra: Partial<ParamSchema> = {}): ParamSchema => ({ type, description, ...extra });

const SET = P("integer", "номер набора скважин (без — активный набор)");
const TABLE = P("string", "интервальная таблица набора: имя («Пробы») или номер (без — таблица проб)");
const FIELD = P("string", "числовое поле: компонент, числовой атрибут или length — длина интервала (без — первый компонент)");
const WEIGHT = P("string", "вес: none — без веса, length — длина интервала (по умолчанию), field — поле weight_field, length_field — длина × поле", { enum: ["none", "length", "field", "length_field"] });
const DOMAIN = {
  domain: P("string", "текстовое поле домена"),
  domain_table: P("string", "таблица поля домена, если она другая: код берётся по середине пробы"),
};

/** Selection of values common to the charts (ADR 0019 §4: order of BF — weight, < 0, zeros, cuts). */
const SELECT: Record<string, ParamSchema> = {
  weight: WEIGHT,
  weight_field: P("string", "поле веса для weight = field или length_field (например плотность)"),
  negatives: P("string", "значения < 0: keep — оставить, half_abs — половина предела (|x| / 2), exclude — исключить", { enum: ["keep", "half_abs", "exclude"] }),
  exclude_zero: P("boolean", "исключить нули"),
  bottom_cut: P("number", "отсечка снизу"),
  bottom_mode: P("string", "отсечка снизу: exclude — исключить (по умолчанию), cap — поднять до значения", { enum: ["exclude", "cap"] }),
  top_cut: P("number", "отсечка сверху"),
  top_mode: P("string", "отсечка сверху: cap — урезать до значения (по умолчанию), exclude — исключить", { enum: ["exclude", "cap"] }),
};

const SAVED = "Окно открывается с этими параметрами, график сразу сохраняется в набор → «Графики» (ТЗ 7.28); ответ — имя и номер графика, число проб, среднее, медиана, CV.";
const CHART_ID = P("integer", "номер сохранённого графика (дерево набора › «Графики»)");

export const commands: Command[] = [
  {
    id: "chart.histogram",
    title: "Гистограмма",
    description: `Гистограмма и описательная статистика проб (ТЗ 7.25): бины линейные или равные в log10 (авто, ширина или число, диапазон «от — до»), статистика с весом и без. ${SAVED}`,
    params: {
      set_id: SET,
      table: TABLE,
      field: FIELD,
      ...SELECT,
      log: P("boolean", "логарифмическая шкала (бины равные в log10)"),
      bin_width: P("number", "ширина бина (для логарифмической шкалы — в log10); без ширины и числа — авто"),
      bins: P("integer", "число бинов, до 1000"),
      start: P("number", "начало диапазона бинов"),
      end: P("number", "конец диапазона бинов"),
    },
    stage: STAGE,
  },
  {
    id: "chart.qq",
    title: "Q-Q график",
    description: `Квантили проб против распределения (логнормальное, нормальное; PPCC) или против другой выборки — другого набора, таблицы, поля или скважин (D Колмогорова–Смирнова, таблица X / Y / отношение) (ТЗ 7.26). ${SAVED}`,
    params: {
      set_id: SET,
      table: TABLE,
      field: FIELD,
      distribution: P("string", "lognormal — логнормальное (по умолчанию), normal — нормальное, sample — другая выборка (y_*)", { enum: ["lognormal", "normal", "sample"] }),
      y_set_id: P("integer", "набор второй выборки (без — тот же)"),
      y_table: P("string", "таблица второй выборки"),
      y_field: P("string", "поле второй выборки"),
      weight: WEIGHT,
      holes: P("string", "скважины выборки X через запятую (без — все)"),
      y_holes: P("string", "скважины выборки Y через запятую (без — все)"),
      quantiles: P("integer", "число квантилей двух выборок (по умолчанию 99)"),
      log: P("boolean", "логарифмические оси"),
    },
    stage: STAGE,
  },
  {
    id: "chart.dendrogram",
    title: "Дендрограмма",
    description: `Кластеры полей проб по корреляции (ТЗ 7.27): дерево, группы по порогу, матрица корреляций в порядке листьев. Нужно 2 и больше компонента. ${SAVED}`,
    params: {
      set_id: SET,
      table: TABLE,
      fields: P("string", "поля через запятую (без — все компоненты)"),
      method: P("string", "pearson — Пирсон (по умолчанию), spearman — Спирмен", { enum: ["pearson", "spearman"] }),
      log: P("boolean", "корреляция логарифмов"),
      weight: WEIGHT,
      missing: P("string", "пропуски: pairwise — попарно, complete — только полные строки", { enum: ["pairwise", "complete"] }),
      linkage: P("string", "связь: average (UPGMA, по умолчанию), complete, single, weighted", { enum: ["average", "complete", "single", "weighted"] }),
      distance: P("string", "расстояние: 1-r или 1-abs (1 − |r|)", { enum: ["1-r", "1-abs"] }),
      cut: P("number", "порог расстояния: группы полей"),
    },
    stage: STAGE,
  },
  {
    id: "chart.scatter",
    title: "Рассеяние",
    description: `Диаграмма рассеяния двух полей (ТЗ 8.3): регрессии МНК, RMA, взвешенная, r Пирсона и Спирмена, лог-оси, цвет по полю; больше 200 000 точек — плотность. Рамка и лассо выделяют пробы в 3D и в таблице. ${SAVED}`,
    params: {
      set_id: SET,
      table: TABLE,
      x: P("string", "поле по оси X"),
      y: P("string", "поле по оси Y"),
      y_table: P("string", "таблица поля Y, если она другая: строки сводятся по ключу пробы"),
      color: P("string", "поле цвета точек"),
      log_x: P("boolean", "логарифмическая ось X"),
      log_y: P("boolean", "логарифмическая ось Y"),
      weight: WEIGHT,
      regression: P("string", "линия регрессии: ols — МНК, rma — RMA, weighted — взвешенная, none — нет", { enum: ["ols", "rma", "weighted", "none"] }),
    },
    stage: STAGE,
  },
  {
    id: "chart.box",
    title: "Ящики с усами",
    description: `Ящики с усами по доменам (ТЗ 8.3): квартили, усы 1,5 · IQR, выбросы, среднее и число проб. ${SAVED}`,
    params: { set_id: SET, table: TABLE, field: FIELD, ...DOMAIN, log: P("boolean", "логарифмическая шкала"), weight: WEIGHT },
    stage: STAGE,
  },
  {
    id: "chart.probplot",
    title: "Лог-вероятностный",
    description: `Лог-вероятностный график (ТЗ 8.3): содержание на логарифмической оси против вероятности, домены — отдельными линиями; щелчок по перегибу открывает «Урезку» с этим порогом. ${SAVED}`,
    params: { set_id: SET, table: TABLE, field: FIELD, domain: P("string", "текстовое поле домена (без — одна линия)"), domain_table: DOMAIN.domain_table, weight: WEIGHT },
    stage: STAGE,
  },
  {
    id: "chart.contact",
    title: "Контактный анализ",
    description: `Контактный анализ вдоль ствола (ТЗ 8.3): средние содержания и число проб по шагам по обе стороны контакта доменов A и B (или до каркаса), число контактов. ${SAVED}`,
    params: {
      set_id: SET,
      table: TABLE,
      field: FIELD,
      ...DOMAIN,
      a: P("string", "домен A"),
      b: P("string", "домен B"),
      distance: P("number", "расстояние от контакта, м (по умолчанию 20)"),
      step: P("number", "шаг, м (по умолчанию 1)"),
      wireframe_id: P("integer", "каркас: контакт — пересечение ствола с ним (снаружи / внутри)"),
      blocks_model_id: P("integer", "профиль блоков рядом с пробами (Д-28): номер блочной модели"),
      blocks_field: P("string", "поле оценки блочной модели (среднее по блокам со STATUS = 0 по шагам знакового расстояния до границы)"),
      blocks_status_field: P("string", "поле статуса оценки модели (учитываются блоки со значением 0); без него — все блоки"),
      blocks_domain_field: P("string", "поле домена модели с теми же значениями A и B (без каркаса); по умолчанию — поле домена проб"),
    },
    stage: STAGE,
  },
  {
    id: "chart.ternary",
    title: "Тернарная",
    description: `Тернарная диаграмма трёх полей (ТЗ 8.3): доли (a, b, c) / (a + b + c), цвет по полю. ${SAVED}`,
    params: { set_id: SET, table: TABLE, a: P("string", "поле вершины A"), b: P("string", "поле вершины B"), c: P("string", "поле вершины C"), color: P("string", "поле цвета точек") },
    stage: STAGE,
  },
  {
    id: "chart.domains",
    title: "По доменам",
    description: `Статистика по доменам (ТЗ 8.3): n, длина, среднее взвешенное и декластерированное (DECL_W), медиана, σ, CV, min, max, P10, P50, P90; таблица с экспортом. ${SAVED}`,
    params: { set_id: SET, table: TABLE, field: FIELD, ...DOMAIN, weight: WEIGHT },
    stage: STAGE,
  },
  {
    id: "chart.striplog",
    title: "Данные по скважинам (текст)",
    description: "Значения полей таблицы набора по глубинам для скважин рядом (до 30) — текст CSV «Скважина;От;До;поля». Без полей — числовые поля таблицы.",
    params: { holes: { type: "string", description: "Номера скважин через запятую (без — выбранная скважина)" }, table: { type: "string", description: "Название таблицы (без — «Пробы»)" }, fields: { type: "string", description: "Поля через запятую" } },
    stage: "Э6",
  },
  { id: "chart.open", title: "Открыть график", description: "Открыть сохранённый график с его настройками по текущим данным (ТЗ 7.28); ответ — имя, число проб, среднее, медиана, CV.", params: { chart_id: CHART_ID }, required: ["chart_id"], stage: STAGE },
  { id: "chart.rename", title: "Переименовать график", description: "Переименовать сохранённый график; ручное имя больше не следует за настройками.", params: { chart_id: CHART_ID, name: P("string", "новое имя; занятое — отказ") }, required: ["chart_id", "name"], stage: STAGE },
  { id: "chart.delete", title: "Удалить график", description: "Удалить сохранённый график (данные проб не меняются).", params: { chart_id: CHART_ID }, required: ["chart_id"], confirm: true, stage: STAGE },
  { id: "chart.select_clear", title: "Снять выделение проб", description: "Снять выделение проб, сделанное рамкой или лассо на графике: в 3D и в таблице (Esc в окне графика).", uiOnly: true, stage: STAGE },
];

/** Icons of the chart buttons: `shell/Icon.tsx` has (or gets on acceptance of 13.21) each of them. */
export type ChartIcon = "histogram" | "qq" | "dendrogram" | "scatter" | "box" | "probplot" | "contact" | "ternary" | "domains";

export interface ChartButton {
  command: string;
  label: string;
  icon: ChartIcon;
  hint: string;
  small?: boolean;
}

/** The ribbon group «Анализ → Графики» (ТЗ 7.25–7.27, 8.3; ADR 0019 §9): nine buttons. */
export const CHART_GROUP_TITLE = "Графики";
export const CHART_BUTTONS: readonly ChartButton[] = [
  { command: "chart.histogram", label: "Гистограмма", icon: "histogram", hint: "Распределение и статистика проб с весом и без; сохраняется в набор → «Графики»" },
  { command: "chart.qq", label: "Q-Q график", icon: "qq", hint: "Квантили проб против распределения или другой выборки", small: true },
  { command: "chart.dendrogram", label: "Дендрограмма", icon: "dendrogram", hint: "Кластеры полей по корреляции; нужно 2 и больше компонента", small: true },
  { command: "chart.scatter", label: "Рассеяние", icon: "scatter", hint: "Два поля, регрессии; лассо выделяет пробы в 3D и в таблице", small: true },
  { command: "chart.box", label: "Ящики с усами", icon: "box", hint: "Квартили, усы и выбросы по доменам", small: true },
  { command: "chart.probplot", label: "Лог-вероятностный", icon: "probplot", hint: "Содержание против вероятности по доменам; перегиб — порог урезки", small: true },
  { command: "chart.contact", label: "Контактный анализ", icon: "contact", hint: "Средние по обе стороны контакта доменов вдоль ствола", small: true },
  { command: "chart.ternary", label: "Тернарная", icon: "ternary", hint: "Доли трёх полей в треугольнике", small: true },
  { command: "chart.domains", label: "По доменам", icon: "domains", hint: "Таблица статистики по доменам с экспортом", small: true },
];

/** Window kinds of the charts: `chart-<kind>` (their place and size are remembered per kind). */
export const CHART_WINDOW_PREFIX = "chart-";
