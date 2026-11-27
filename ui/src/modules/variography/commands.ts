/**
 * Variography (ТЗ 8.6.4; ADR 0022 §13, decision 22): the commands `variography.*` of «Геостатистика → Вариография»
 * (the ribbon tab after «Анализ»). A command without its required arguments opens its window (prefilled with what is
 * given); with them it runs. `confirm` — the delete of a model; the replacement of a stored model (`save` with a
 * model that was saved) asks in the handler, for the assistant too (the registry cannot know it beforehand). No
 * default keys. `variography.ellipsoid`, `variography.show` — the ellipsoid of the model in 3D (`scene.ts`); the
 * trends — by `modules/trends`. `VARIO_BUTTONS` — the buttons of the ribbon group (shell/ribbonConfig.ts puts them in).
 */
import type { Command, ParamSchema } from "../../shell/commands";
import type { IconName } from "../../shell/Icon";

const P = (type: ParamSchema["type"], description: string, extra: Partial<ParamSchema> = {}): ParamSchema => ({ type, description, ...extra });

const DATA: Record<string, ParamSchema> = {
  set_id: P("integer", "номер набора скважин"),
  table: P("string", "интервальная таблица набора (пробы, композиты): имя или номер"),
  field: P("string", "числовое поле таблицы"),
  domain: P("string", "поле домена (той же таблицы или domain_table); без него — все точки"),
  domain_table: P("string", "таблица поля домена, если не та же (по середине пробы)"),
  domain_values: P("string", "значения домена через «;», например «окисл; перех» (до 1000)"),
  weights: P("string", "поле весов; не задано — DECL_W, если есть; «нет» — без весов"),
  transform: P("string", "преобразование: none — без него, nscore — нормальные оценки, indicator — индикатор порога threshold (1, если z ≤ t, иначе 0)", { enum: ["none", "nscore", "indicator"] }),
  threshold: P("number", "порог t индикатора (с transform = indicator), в единицах поля"),
};

const ANGLES: Record<string, ParamSchema> = {
  ang1: P("number", "азимут оси a1, ° (0–360; 0)"),
  ang2: P("number", "ang2 = −погружение оси a1, ° (−90…90; 0)"),
  ang3: P("number", "поворот вокруг a1, ° (−180…180; 0)"),
};

const LAGS: Record<string, ParamSchema> = {
  lag: P("number", "лаг L, м (> 0; по умолчанию 5 × медиана длин проб)"),
  nlag: P("integer", "число лагов (1–100; 15)"),
  lag_tol: P("number", "допуск лага, м (0–L; L / 2)"),
  az_tol: P("number", "допуск азимута, ° (0–90; 22,5)"),
  dip_tol: P("number", "допуск погружения, ° (0–90; 22,5)"),
  band_h: P("number", "горизонтальная полоса, м (> 0; 3L)"),
  band_v: P("number", "вертикальная полоса, м (> 0; 2L)"),
  min_pairs: P("integer", "точка строится при числе пар не меньше (1–1000; 30)"),
};

const MODEL_ID = P("integer", "номер сохранённой модели вариограммы");
const FORMAT = P("string", "формат: json или gslib (nst c0 / it cc ang1 ang2 ang3 / a1 a2 a3)", { enum: ["json", "gslib"] });

export const commands: Command[] = [
  {
    id: "variography.open",
    title: "Вариография",
    description:
      "Окно «Вариография · ‹поле›» (ТЗ 8.6.4): данные, графики трёх осей и вдоль скважин с числом пар, веер, карта, таблица модели. " +
      "view — вкладка окна; model_id — открыть сохранённую модель. Можно открыть несколько окон.",
    params: { ...DATA, view: P("string", "вкладка: axes — оси, downhole — вдоль скважин, fan — веер, map — карта, nscore — нормальные оценки", { enum: ["axes", "downhole", "fan", "map", "nscore"] }), model_id: MODEL_ID },
  },
  {
    id: "variography.models",
    title: "Модели вариограмм",
    description: "Раздел «Геостатистика → Модели вариограмм» дерева проекта: сохранённые модели, «устарел», перестроение.",
    uiOnly: true,
  },
  {
    id: "variography.calc",
    title: "Рассчитать вариограммы",
    description:
      "Экспериментальная вариограмма (ТЗ 8.6.2): what = axes — три оси модели по углам ang1–ang3 и вдоль скважин; fan — веер с розой, map — карта в плоскости осей a1–a2. " +
      "С set_id, table и field — сразу задание, ход на вкладке «Задания»; без них — окно. Лаг без пар — пусто (не ноль).",
    params: { ...DATA, ...ANGLES, ...LAGS, what: P("string", "что рассчитать: axes (по умолчанию), fan, map", { enum: ["axes", "fan", "map"] }) },
  },
  {
    id: "variography.fit",
    title: "Подобрать модель",
    description:
      "Подбор модели (ТЗ 8.6.3) по осям последнего расчёта окна: структуры начальной модели окна (или structures — типы через запятую: spherical, exponential, gaussian; до 3), углы закреплены. " +
      "model_id — окно этой модели; без него — последнее активное окно «Вариография». Сначала нужен расчёт осей.",
    params: {
      model_id: MODEL_ID,
      structures: P("string", "типы структур через запятую, например «spherical, spherical» (до 3)"),
      weights: P("string", "веса подбора: n_h — N / h̄ (по умолчанию), n — N, n_g2 — N / γ²", { enum: ["n_h", "n", "n_g2"] }),
      fix_nugget: P("boolean", "закрепить самородок C0"),
    },
  },
  {
    id: "variography.ellipsoid",
    title: "Эллипсоид в 3D",
    description:
      "Эллипсоид анизотропии модели в 3D с ручками углов и дальностей (Д-14): окно модели model_id (открывается, если закрыто), " +
      "заданные углы и дальности — выбранной структуре (при общих осях углы — всем). Центр — середина охвата точек расчёта.",
    params: { model_id: MODEL_ID, ...ANGLES, a1: P("number", "дальность a1, м"), a2: P("number", "дальность a2, м"), a3: P("number", "дальность a3, м"), structure: P("integer", "номер структуры с 1; по умолчанию — с наибольшей a1") },
    required: ["model_id"],
  },
  {
    id: "variography.show",
    title: "Показать эллипсоид модели",
    description: "Показать или скрыть эллипсоид сохранённой модели вариограммы в 3D.",
    params: { model_id: MODEL_ID, visible: P("boolean", "показать (по умолчанию да)") },
    required: ["model_id"],
  },
  {
    id: "variography.save",
    title: "Сохранить модель вариограммы",
    description:
      "Сохранить рабочую модель окна в «Геостатистика → Модели вариограмм» (с экспериментальными точками последнего расчёта). " +
      "model_id — окно этой модели (замена сохранённой — после подтверждения); name — имя (по умолчанию «‹поле› · ‹таблица›»).",
    params: { model_id: MODEL_ID, name: P("string", "имя модели") },
  },
  {
    id: "variography.delete",
    title: "Удалить модель вариограммы",
    description: "Удалить сохранённую модель вариограммы с её экспериментальными точками.",
    params: { model_id: MODEL_ID },
    required: ["model_id"],
    confirm: true,
  },
  {
    id: "variography.rebuild",
    title: "Перестроить модель вариограммы",
    description:
      "«Перестроить» устаревшую модель: экспериментальные точки заново на текущих данных с прежними параметрами расчёта (задание, ход на вкладке «Задания»); " +
      "параметры модели не меняются, невязка подбора пересчитывается. Недоступно, если набор или таблица модели удалены. Меняет данные: заменяет экспериментальные точки и невязку модели — после подтверждения.",
    params: { model_id: MODEL_ID },
    required: ["model_id"],
    confirm: true,
  },
  {
    id: "variography.export",
    title: "Экспорт модели вариограммы",
    description:
      "Модель в JSON или параметрах GSLIB: с path — файл записан (путь абсолютный, кириллица допустима, вне папки проекта; расширение .json / .par добавляется; существующий файл — вопрос «Заменить?», overwrite: true — без вопроса); без path — текст модели.",
    params: { model_id: MODEL_ID, format: FORMAT, path: P("string", "абсолютный путь файла"), overwrite: P("boolean", "заменить существующий файл без вопроса") },
    required: ["model_id"],
  },
  {
    id: "variography.import",
    title: "Импорт модели вариограммы",
    description: "Модель из файла JSON или GSLIB — рабочей моделью нового окна «Вариография» (не сохраняется, пока не нажато «Сохранить»).",
    params: { path: P("string", "абсолютный путь файла"), format: FORMAT },
    required: ["path"],
  },
];

/** The ribbon group «Геостатистика → Вариография» (ТЗ 8.6.4; ADR 0022 §13). Icons — shell/Icon.tsx (by the orchestrator). */
const icon = (name: string): IconName => name as IconName;

export interface VarioButton {
  command: string;
  args?: Record<string, unknown>;
  label: string;
  icon: IconName;
  hint: string;
  small?: boolean;
}

export const VARIO_TAB_TITLE = "Геостатистика";
export const VARIO_GROUP_TITLE = "Вариография";
export const VARIO_BUTTONS: readonly VarioButton[] = [
  { command: "variography.open", args: { view: "axes" }, label: "Вариограммы", icon: icon("variogram"), hint: "Экспериментальные вариограммы по трём осям, модель и её подбор" },
  { command: "variography.open", args: { view: "downhole" }, label: "Вдоль скважин", icon: icon("variogram"), hint: "Вариограмма вдоль скважин — для самородка", small: true },
  { command: "variography.open", args: { view: "fan" }, label: "Веер", icon: icon("variogram"), hint: "Веер направлений в плоскости и роза дальностей", small: true },
  { command: "variography.open", args: { view: "map" }, label: "Карта вариограмм", icon: icon("variogram"), hint: "Карта вариограммы в плоскости осей a1–a2", small: true },
  { command: "variography.open", args: { view: "nscore" }, label: "Нормальные оценки", icon: icon("variogram"), hint: "Вариограмма нормальных оценок и обратное преобразование (Эрмит)", small: true },
  { command: "variography.models", label: "Модели", icon: icon("ellipsoid"), hint: "Сохранённые модели вариограмм в дереве проекта", small: true },
];
