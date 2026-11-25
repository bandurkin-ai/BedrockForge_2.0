/**
 * The commands of the estimation of grades (ТЗ 8.8.4; ADR 0024 §10; docs/progress/e18-api.md §12): the tab «Геостатистика», the
 * groups «Оценка», «Проверка», «Моделирование». The ribbon, Ctrl+K, the keys of the user and the assistant run the same commands.
 * A command without arguments opens its window (ADR 0021 п. 9); the pure declarations are here (the catalog reads them first),
 * the handlers — `bind.ts` as the windows get ready: `estimate.open`, `estimate.run`, `estimate.delete` (18.19; `bindEstimation(app)`
 * takes their `stage` off; the window of the plan (18.22) binds `estimate.plan` and `estimate.plan_delete` the same way; `estimate.run` without
 * arguments opens it), the rest stays «появится на этапе» until its window (`stage`). The parameters are flat (`ParamSchema` has no objects): the pair of the
 * soft boundary of the contact analysis (Д-28, `estimate.open {soft: {a, b, distance}}` of the contract) is `soft_a`, `soft_b`,
 * `soft_distance`; the lists are text («41; 42»). The simulation (`simulate.*`) is Э18б.
 */
import type { Command, ParamSchema } from "../../shell/commands";
import type { IconName } from "../../shell/Icon";
import { METHODS } from "./api";

const P = (type: ParamSchema["type"], description: string, extra: Partial<ParamSchema> = {}): ParamSchema => ({ type, description, ...extra });

const MODEL = P("integer", "номер блочной модели (раздел «Блочные модели» дерева)");
const ESTIMATION = P("integer", "номер строки «Параметры оценки» (раздел «Геостатистика → Параметры оценки» дерева)");

export const commands: Command[] = [
  {
    id: "estimate.open",
    title: "Оценка содержаний",
    description:
      "Мастер оценки содержаний (8.8.4): модель, ограничитель, поля назначения → пробы (таблица, поле, домен, урезка, веса) → метод и модель вариограммы → поиск и проходы → выходные поля → задание. " +
      "method — метод заранее (nn, idw, sk, ok, ik); id — открыть сохранённую строку для правки; model_id — блочная модель. soft_a, soft_b, soft_distance — мягкая граница из окна контактного анализа: " +
      "домен проб A, соседний домен B и расстояние d_м. Сохраняет строку в «Параметры оценки».",
    params: {
      method: P("string", "метод оценки: nn, idw, sk, ok, ik", { enum: METHODS }),
      id: ESTIMATION,
      model_id: MODEL,
      soft_a: P("string", "домен проб A мягкой границы (значение поля домена)"),
      soft_b: P("string", "соседний домен B, пробы которого допускаются у границы"),
      soft_distance: P("number", "расстояние мягкой границы d_м, м (0 — жёсткая граница)"),
    },
    uiOnly: true,
    stage: "Э18 (18.19)",
  },
  {
    id: "estimate.plan",
    title: "План оценки",
    description:
      "Окно «План оценки»: планы блочной модели, строки домен × поле × метод в порядке выполнения (вверх, вниз, добавить, убрать), запуск плана или выбранных строк, ход задания и журнал по проходам. " +
      "plan_id — открыть план; model_id — планы этой блочной модели.",
    params: { plan_id: P("integer", "номер плана оценки"), model_id: MODEL },
    uiOnly: true,
    stage: "Э18 (18.22)",
  },
  {
    id: "estimate.plan_delete",
    title: "Удалить план оценки",
    description: "Удаляет план оценки: строки «Параметры оценки» и поля блочной модели остаются.",
    params: { id: P("integer", "номер плана оценки (окно «План оценки»)") },
    required: ["id"],
    confirm: true,
    stage: "Э18 (18.22)",
  },
  {
    id: "estimate.ellipsoid",
    title: "Эллипсоид поиска в 3D",
    description: "Показать в 3D эллипсоид поиска строки «Параметры оценки» у выбранного блока: отобранные пробы и их веса.",
    params: { id: ESTIMATION },
    stage: "Э18 (18.21)",
  },
  {
    id: "estimate.run",
    title: "Выполнить оценку",
    description:
      "Запускает оценку плана или строк «Параметры оценки» одной блочной модели (задание estimate.run, ход — вкладка «Задания»): поля с префиксом строки (STATUS, EST, KV, NS, …) записываются в модель целиком или никак. " +
      "plan_id — план; estimation_ids — номера строк через «;» (одна модель, совместимые префиксы). Поле уже есть в модели — вопрос «Заменить поля?»; replace=true — без вопроса. Без аргументов открывает окно «План оценки».",
    params: {
      plan_id: P("integer", "номер плана оценки"),
      estimation_ids: P("string", "номера строк «Параметры оценки» через «;», например 41; 42"),
      replace: P("boolean", "заменить поля, которые уже есть в модели, без вопроса"),
    },
    confirm: true,
    stage: "Э18 (18.19)",
  },
  {
    id: "estimate.delete",
    title: "Удалить строку «Параметры оценки»",
    description: "Удаляет строку «Параметры оценки»: она уходит из планов, поля блочной модели остаются.",
    params: { id: ESTIMATION },
    required: ["id"],
    confirm: true,
    stage: "Э18 (18.19)",
  },
  {
    id: "estimate.inspect",
    title: "Блок · i, j, k",
    description: "Инспектор блока: какие пробы вошли в оценку блока, с какими весами и почему остальные отброшены; сверка с записанными полями. block — «i; j; k» родителя, xyz — «x; y; z» точки; params_id — одна строка.",
    params: { model_id: MODEL, block: P("string", "ячейка родителя: i; j; k"), xyz: P("string", "точка: x; y; z, м"), params_id: ESTIMATION },
    required: ["model_id"],
    stage: "Э18 (18.Д4)",
  },
  {
    id: "estimate.xval",
    title: "Перекрёстная проверка",
    description:
      "Перекрёстная проверка строки: каждая проба или скважина убирается и оценивается по остальным (by: hole или sample). Таблица, сводка, рассеяние и гистограмма ошибок. " +
      "Без by открывает окно с прежним результатом строки; с by выполняет проверку (задание estimate.xval, результат заменяет прежний) и отвечает сводкой: средняя ошибка, СКО, MAE, r, наклон, среднее ошибка²/KV.",
    params: { estimation_id: ESTIMATION, by: P("string", "что исключать: hole — скважину (по умолчанию), sample — пробу", { enum: ["hole", "sample"] }) },
    stage: "Э18 (18.23)",
  },
  {
    id: "estimate.kna",
    title: "Анализ окрестности",
    description: "Анализ окрестности кригинга (KNA): перебор n_max, радиусов, дискретизации и размера блока на трёх блоках; рекомендация n_max (не применяется автоматически).",
    params: { estimation_id: ESTIMATION },
    stage: "Э18 (18.24)",
  },
  {
    id: "estimate.swath",
    title: "Swath plot оценки",
    description: "Окно swath: оценка блоков и композиты домена по полосам.",
    params: { estimation_id: ESTIMATION },
    stage: "Э18 (18.25)",
  },
  {
    id: "estimate.global",
    title: "Глобальная статистика оценки",
    description: "Среднее и дисперсия композитов (с весами), NN и оценки по блокам домена; расхождение с декластерированным средним в процентах (норма — не больше 5 %).",
    params: { estimation_id: ESTIMATION },
    stage: "Э18 (18.24)",
  },
  {
    id: "estimate.qq",
    title: "Q-Q: композиты и оценка",
    description: "График квантиль–квантиль: композиты домена (с весом) против оценки блоков (с весом объёма).",
    params: { estimation_id: ESTIMATION },
    stage: "Э18 (18.24)",
  },
];

/** The ribbon: the tab «Геостатистика» (`shell/ribbonConfig.ts` puts the groups in, by the orchestrator). */
export interface EstimateButton {
  command: string;
  args?: Record<string, unknown>;
  label: string;
  icon: IconName;
  hint: string;
  small?: boolean;
}

const icon = (name: string): IconName => name as IconName;

export const ESTIMATE_GROUP_TITLE = "Оценка";
export const CHECK_GROUP_TITLE = "Проверка";
export const SIM_GROUP_TITLE = "Моделирование";

export const ESTIMATE_BUTTONS: readonly EstimateButton[] = [
  { command: "estimate.open", label: "Оценка содержаний", icon: icon("blocks"), hint: "Мастер: блочная модель, пробы, метод (NN, IDW, SK, OK, IK), поиск и проходы, выходные поля" },
  { command: "estimate.plan", label: "План оценки", icon: icon("list"), hint: "Строки домен × поле × метод одной модели: запуск и журнал", small: true },
  { command: "estimate.ellipsoid", label: "Эллипсоид поиска", icon: icon("ellipsoid"), hint: "Эллипсоид поиска и отобранные пробы у блока в 3D", small: true },
];

export const CHECK_BUTTONS: readonly EstimateButton[] = [
  { command: "estimate.xval", label: "Перекрёстная проверка", icon: icon("scatter"), hint: "Каждая проба или скважина оценивается по остальным: ошибки, сводка, рассеяние" },
  { command: "estimate.kna", label: "Анализ окрестности", icon: icon("variogram"), hint: "Перебор n_max, радиусов и дискретизации: эффективность кригинга и наклон регрессии", small: true },
  { command: "estimate.swath", label: "Swath plot", icon: icon("swath"), hint: "Композиты и оценка блоков по полосам", small: true },
  { command: "estimate.global", label: "Глобальная статистика", icon: icon("histogram"), hint: "Среднее композитов, NN и оценки: расхождение не больше 5 %", small: true },
  { command: "estimate.qq", label: "Q-Q", icon: icon("qq"), hint: "Квантили композитов и оценки блоков", small: true },
];

export const SIM_BUTTONS: readonly EstimateButton[] = [{ command: "estimate.open", args: { method: "ik" }, label: "Индикаторы", icon: icon("domains"), hint: "Индикаторный кригинг: пороги, модели по порогам, вероятности и E-type" }];
