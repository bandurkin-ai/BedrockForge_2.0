/**
 * The commands of the simulation (ТЗ 8.8.1; Д-23; docs/progress/e18-api.md §9, §12): the group «Моделирование» of the tab «Геостатистика».
 * The ribbon, Ctrl+K, the keys of the user and the assistant run the same commands. A command without arguments opens its window
 * (ADR 0021 п. 9); the pure declarations are here (the catalog reads them first), the handlers — `bind.ts` as the windows get ready
 * (`bindSimulation(app)` takes their `stage` off). The parameters are flat (`ParamSchema` has no objects): the lists are text
 * («0,5; 1; 2,5»), the models of the indicators — their numbers («5; 6; 7»).
 *
 *  - `simulate.open {kind?, id?, model_id?}` — the wizard;
 *  - `simulate.sgs`, `simulate.sis`, `simulate.cosgs` — the wizard of the kind without arguments; with `id` — runs the stored row; with the
 *    parameters of the simulation (`model_id`, `set_id`, `table`, `field`, `variogram_id`, …) — runs them as a new row;
 *  - `simulate.run {id}` — runs the stored row; `simulate.save {id?, as_new?}` — saves the form of the wizard;
 *  - `simulate.summary {id?}` — the window «Сводка реализаций»; `simulate.post {id?, run?, …}` — the window «Постобработка реализаций»
 *    or, with `run` or the parameters, the post-processing itself; `simulate.delete {id}`.
 */
import type { Command, ParamSchema } from "../../shell/commands";
import type { IconName } from "../../shell/Icon";
import type { EstimateButton } from "../estimation/commands";
import { SIM_KINDS } from "./api";

const P = (type: ParamSchema["type"], description: string, extra: Partial<ParamSchema> = {}): ParamSchema => ({ type, description, ...extra });

const MODEL = P("integer", "номер блочной модели (раздел «Блочные модели» дерева)");
const SIM = P("integer", "номер строки «Моделирование» (раздел «Геостатистика → Моделирование» дерева)");

/** The parameters of a simulation shared by the three kinds (a row stored by `id`, or a new one by these). */
const COMMON: Record<string, ParamSchema> = {
  id: SIM,
  model_id: MODEL,
  name: P("string", "имя строки; пусто — «‹поле› · ‹ВИД› · ‹домен›»"),
  domain_field: P("string", "поле домена блоков модели (пусто — все блоки)"),
  label: P("string", "значение поля домена блоков, которое моделируется"),
  set_id: P("integer", "номер набора скважин с пробами"),
  table: P("string", "ключ таблицы проб в наборе (число из окна «Менеджер данных», как текст)"),
  field: P("string", "числовое поле проб (для категорий SIS — поле кодов литологии)"),
  sample_domain_field: P("string", "поле домена проб; значения — domain_values"),
  domain_table: P("string", "ключ таблицы домена проб, если не та же, что у проб"),
  domain_values: P("string", "значения домена проб через «;»"),
  variogram_id: P("integer", "номер сохранённой модели вариограммы: SGS и ко-SGS — в нормальных оценках, SIS «одна модель» — индикатора"),
  n_real: P("integer", "число реализаций, 1–100 (по умолчанию 20)"),
  seed: P("integer", "зерно случайных чисел, 0 и больше (по умолчанию 12345): то же зерно — те же реализации"),
  nodes: P("integer", "сколько ранее смоделированных узлов учитывает узел, 0–64 (по умолчанию 12)"),
  n_max: P("integer", "наибольшее число проб в окрестности узла, 1–128 (по умолчанию 24)"),
  factor: P("number", "множитель радиусов эллипсоида поиска (по умолчанию 1)"),
  search_from: P("string", "эллипсоид поиска: model — из модели вариограммы (по умолчанию), manual — r1, r2, r3 и углы заданы", { enum: ["model", "manual"] }),
  r1: P("number", "радиус r1, м (при search_from=manual)"),
  r2: P("number", "радиус r2, м (по умолчанию r1)"),
  r3: P("number", "радиус r3, м (по умолчанию r1)"),
  ang1: P("number", "угол 1 (азимут), °"),
  ang2: P("number", "угол 2 (падение), °"),
  ang3: P("number", "угол 3 (поворот), °"),
  ltail: P("string", "нижний хвост обратного преобразования: linear, power, none", { enum: ["linear", "power", "none"] }),
  utail: P("string", "верхний хвост обратного преобразования: linear, power, hyperbolic, none", { enum: ["linear", "power", "hyperbolic", "none"] }),
};

const SIS_ONLY: Record<string, ParamSchema> = {
  thresholds: P("string", "пороги непрерывной величины через «;» по возрастанию, 1–20, например 0,5; 1; 2,5"),
  categories: P("string", "коды категорий через «;» — целые 0–255, 2–20 различных; вместо порогов"),
  labels: P("string", "названия категорий через «;» — по одному на код"),
  models: P("string", "модели индикаторов: per — своя на каждый порог (категорию, variogram_ids), median — одна (variogram_id)", { enum: ["per", "median"] }),
  variogram_ids: P("string", "номера моделей индикаторов через «;» — по одной на порог (категорию) в порядке списка"),
};

const COSGS_ONLY: Record<string, ParamSchema> = {
  secondary_field: P("string", "вторичная переменная — числовое поле блочной модели (например, плотность)"),
  rho: P("number", "коэффициент корреляции нормальных оценок ρ от −1 до 1; без него — считается по пробам"),
};

const RUN_NOTE = "Без аргументов открывает мастер; id — выполнить сохранённую строку; с параметрами моделирования (model_id, set_id, table, field, variogram_id, …) — выполнить их как новую строку и записать реализации отдельным объектом (задание simulate.run, ход — вкладка «Задания»). В блочную модель реализации не пишутся: для этого — simulate.post.";

export const commands: Command[] = [
  {
    id: "simulate.open",
    title: "Моделирование",
    description:
      "Мастер моделирования (Д-23): вид (SGS, SIS, ко-SGS), блочная модель и домен → пробы → модель вариограммы (и пороги или категории для SIS, вторичная переменная для ко-SGS) → поиск → реализации (число, зерно, хвосты) → задание. " +
      "kind — вид заранее (sgs, sis, cosgs); id — открыть сохранённую строку для правки; model_id — блочная модель. Сохраняет строку в «Геостатистика → Моделирование».",
    params: { kind: P("string", "вид моделирования: sgs, sis, cosgs", { enum: SIM_KINDS }), id: SIM, model_id: MODEL },
    uiOnly: true,
    stage: "Э18б (18.35)",
  },
  {
    id: "simulate.sgs",
    title: "SGS — гауссово моделирование",
    description: `Последовательное гауссово моделирование (SGS, 8.8.1): нормальные оценки проб → случайный путь по родителям блочной модели → простой кригинг по пробам и ранее смоделированным узлам → обратное преобразование; N реализаций. Модель вариограммы — в нормальных оценках. ${RUN_NOTE}`,
    params: COMMON,
    stage: "Э18б (18.35)",
  },
  {
    id: "simulate.sis",
    title: "SIS — индикаторное моделирование",
    description: `Последовательное индикаторное моделирование (SIS, Д-23): непрерывная величина по порогам (thresholds) или категории — коды литологии (categories); индикаторные модели — своя на порог (models=per, variogram_ids) или одна (median, variogram_id); N реализаций. ${RUN_NOTE}`,
    params: { ...COMMON, ...SIS_ONLY },
    stage: "Э18б (18.35)",
  },
  {
    id: "simulate.cosgs",
    title: "Ко-SGS — вторичная переменная",
    description: `SGS с колокированной вторичной переменной (ко-SGS, Д-23, марковская модель 1): secondary_field — числовое поле блочной модели (плотность), rho — корреляция нормальных оценок (без него считается по пробам); ρ = 0 даёт SGS. ${RUN_NOTE}`,
    params: { ...COMMON, ...COSGS_ONLY },
    stage: "Э18б (18.35)",
  },
  {
    id: "simulate.save",
    title: "Сохранить параметры моделирования",
    description:
      "Сохраняет форму, открытую в мастере «Моделирование», строкой «Моделирование»: правку открытой строки (версия проверяется) или новую строку. " +
      "as_new=true — всегда новая строка рядом с открытой; id — номер строки, которую должна править форма (проверка: открыта другая — отказ). Не запускает моделирование; ошибка в поле формы остаётся под этим полем.",
    params: { id: SIM, as_new: P("boolean", "сохранить как новую строку, не заменяя открытую") },
    stage: "Э18б (18.35)",
  },
  {
    id: "simulate.run",
    title: "Выполнить моделирование",
    description:
      "Выполняет сохранённую строку «Моделирование» (задание simulate.run, очередь heavy: ход и отмена — вкладка «Задания»): реализации записываются отдельным объектом, прежние реализации строки заменяются. " +
      "Итог — число узлов и реализаций, вырожденные системы, узлы без проб, ρ ко-SGS, журнал. В блочную модель ничего не пишется — для этого simulate.post.",
    params: { id: SIM },
    required: ["id"],
    stage: "Э18б (18.35)",
  },
  {
    id: "simulate.summary",
    title: "Сводка реализаций",
    description: "Окно «Сводка реализаций»: по каждой реализации число узлов, среднее, σ, минимум и максимум (для категорий SIS — доли категорий), условные данные в узле, вырожденные системы. id — строка «Моделирование»; без неё — выбранная в окне или единственная.",
    params: { id: SIM },
    uiOnly: true,
    stage: "Э18б (18.35)",
  },
  {
    id: "simulate.post",
    title: "Постобработка реализаций",
    description:
      "Постобработка реализаций в поля блочной модели: E-type (MEAN), STD, P10, P50, P90 и вероятность превышения PROB_GT_t для порогов t; для категорий SIS — PROB_код каждой категории и MODE (мода). Префикс по умолчанию «‹ПОЛЕ›_SGS_», «‹ПОЛЕ›_SIS_» или «‹ПОЛЕ›_COSGS_». " +
      "Без run и без параметров выполнения открывает окно «Постобработка реализаций» (id — строка); с run=true или с prefix, stats, thresholds, replace выполняет (задание simulate.post). stats — через «;» из mean, std, p10, p50, p90, prob (для категорий — prob, mode); thresholds — через «;», нужны для prob непрерывной величины. " +
      "Поле уже есть в модели — вопрос «Заменить поля?»; replace=true — без вопроса.",
    params: {
      id: SIM,
      run: P("boolean", "выполнить с параметрами по умолчанию, не открывая окно"),
      prefix: P("string", "префикс полей модели: буквы, цифры и «_», до 40 знаков"),
      stats: P("string", "статистики через «;»: mean, std, p10, p50, p90, prob (для категорий — prob, mode)"),
      thresholds: P("string", "пороги для prob через «;», например 0,5; 1; 2,5"),
      replace: P("boolean", "заменить поля, которые уже есть в модели, без вопроса"),
    },
    confirm: true,
    stage: "Э18б (18.35)",
  },
  {
    id: "simulate.delete",
    title: "Удалить моделирование",
    description: "Удаляет строку «Моделирование» вместе с её реализациями. Поля блочной модели, уже записанные постобработкой, остаются.",
    params: { id: SIM },
    required: ["id"],
    confirm: true,
    stage: "Э18б (18.35)",
  },
];

/** The ribbon: the group «Моделирование» of the tab «Геостатистика» (`shell/ribbonConfig.ts` puts the group in, by the orchestrator). */
export type SimButton = EstimateButton;

const icon = (name: string): IconName => name as IconName;

export const SIM_GROUP_TITLE = "Моделирование";

export const SIM_BUTTONS: readonly SimButton[] = [
  { command: "simulate.sgs", label: "SGS", icon: icon("variogram"), hint: "Последовательное гауссово моделирование: реализации по нормальным оценкам и простому кригингу" },
  { command: "simulate.sis", label: "SIS", icon: icon("domains"), hint: "Последовательное индикаторное моделирование: пороги или категории (литология), доли классов" },
  { command: "simulate.cosgs", label: "Ко-SGS", icon: icon("scatter"), hint: "SGS со вторичной переменной блочной модели (плотность): корреляция ρ задаётся или считается" },
  { command: "simulate.summary", label: "Сводка реализаций", icon: icon("histogram"), hint: "Среднее, σ, минимум и максимум каждой реализации; доли категорий", small: true },
  { command: "simulate.post", label: "Постобработка", icon: icon("blocks"), hint: "E-type, σ, P10, P50, P90, вероятность превышения; для категорий — вероятности и мода: поля блочной модели", small: true },
];
