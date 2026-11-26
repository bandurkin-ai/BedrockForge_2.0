/**
 * The commands of the planned holes (ТЗ 7.35 Д-34 ч. 2; ADR 0026 «Команды реестра», решения 28, 29; docs/progress/e19-api.md §12): «Главная → Скважины →
 * Проектная скважина». The ribbon, Ctrl+K and the assistant run the same commands; no keys. A command without arguments opens its window (ADR 0021 п. 9),
 * with them it executes. The pure declarations are here (the catalog reads them through `commands.ts`); the handlers are bound in `plannedBind.ts`.
 * A planned hole is an object of its own (`planned_hole`): it is not a hole of a set and does not touch the statistics and the estimation.
 */
import type { Command, ParamSchema } from "../../shell/commands";
import type { IconName } from "../../shell/Icon";

const P = (type: ParamSchema["type"], description: string, extra: Partial<ParamSchema> = {}): ParamSchema => ({ type, description, ...extra });

const ID = P("integer", "номер проектной скважины (раздел «Проектные скважины» дерева)");
const SET = P("integer", "номер набора скважин (ближайшие скважины, «План / факт»)");

export const commands: Command[] = [
  {
    id: "planned.new",
    title: "Проектная скважина",
    description:
      "Проектная скважина (Д-34): устье X, Y, Z, азимут, угол (вниз — отрицательный), длина; изменение азимута и угла на 100 м или «через цель» (прямая от устья через целевую точку: " +
      "азимут и угол считает ядро). Траектория — как у скважин набора (7.11), метод — один из четырёх. Скважина — отдельный объект проекта: в набор не входит, на статистику и оценку не влияет, " +
      "в 3D и в разрезах показана линией с меткой цели. Без параметров открывает окно «Проектная скважина» (параметры, прогноз, План / факт). " +
      "С x, y, z и направлением создаёт скважину: azimuth, dip, length — прямая (с change_azimuth и change_dip — с изменением на 100 м, −30…30°) либо target_x, target_y, target_z — через цель " +
      "(length необязательна: пусто — до цели; короче расстояния до цели нельзя). name — название (пусто — следующий свободный номер ПС-N); set_id — набор для ближайших скважин и «План / факт».",
    params: {
      name: P("string", "название скважины"),
      x: P("number", "устье X, м"),
      y: P("number", "устье Y, м"),
      z: P("number", "устье Z (высота), м"),
      azimuth: P("number", "азимут, ° от севера по часовой стрелке, 0–360 (360 не входит)"),
      dip: P("number", "угол, °: вниз — отрицательный, −90 — вертикально вниз"),
      length: P("number", "длина, м (0–10000; в режиме «через цель» необязательна)"),
      change_azimuth: P("number", "изменение азимута на каждые 100 м, ° (−30…30)"),
      change_dip: P("number", "изменение угла на каждые 100 м, ° (−30…30)"),
      target_x: P("number", "цель X, м"),
      target_y: P("number", "цель Y, м"),
      target_z: P("number", "цель Z, м"),
      set_id: SET,
      method: P("string", "метод траектории: minimum_curvature (по умолчанию), tangent, balanced_tangent, average_angle", { enum: ["minimum_curvature", "tangent", "balanced_tangent", "average_angle"] }),
    },
  },
  {
    id: "planned.open",
    title: "Открыть проектную скважину",
    description:
      "Открывает окно «Проектная скважина» для скважины из раздела «Проектные скважины» дерева: параметры, прогноз, План / факт. id — номер скважины; без него — выбранная в дереве или единственная. " +
      "Окно новой скважины — planned.new.",
    params: { id: ID },
  },
  {
    id: "planned.save",
    title: "Сохранить проектную скважину",
    description:
      "Сохраняет форму, открытую в окне «Проектная скважина»: правку открытой скважины (версия проверяется: другая версия в проекте — отказ с просьбой перечитать) или новую скважину. " +
      "as_new=true — всегда новая скважина рядом с открытой. Ошибка в поле формы остаётся под этим полем окна.",
    params: { as_new: P("boolean", "сохранить как новую скважину, не заменяя открытую") },
  },
  {
    id: "planned.evaluate",
    title: "Прогноз по проектной скважине",
    description:
      "Прогнозные интервалы проектной скважины по телам неявной модели (model_id) или замкнутых каркасов (mesh_ids): FROM, TO, тело; промежуток между телами — «вне тел», не 0 — и " +
      "до 5 ближайших скважин набора (set_id) у цели (без цели — у забоя): расстояние до самой скважины и глубина по ней. Считается по сохранённой скважине. Без источника и набора открывает вкладку «Прогноз» окна.",
    params: {
      id: ID,
      model_id: P("integer", "номер построенной неявной модели (раздел «Неявные модели» дерева)"),
      mesh_ids: P("string", "номера замкнутых каркасов через «;» (вместо модели)"),
      set_id: SET,
    },
  },
  {
    id: "planned.compare",
    title: "План / факт",
    description:
      "После бурения: отклонение фактической скважины набора от проектной по глубинам через 1 м — ΔX, ΔY, ΔZ и расстояние d, наибольшее отклонение с глубиной, длины плана и факта; график d(глубина). " +
      "Для одинаковых траекторий — 0. set_id — набор, hole — название фактической скважины в нём. Без них открывает вкладку «План / факт» окна.",
    params: { id: ID, set_id: SET, hole: P("string", "название фактической скважины набора") },
  },
  {
    id: "planned.export",
    title: "Экспорт проектной скважины",
    description:
      "Выгружает проектную скважину: csv — файлы collar.csv (устье) и survey.csv (инклинометрия) в папку, их принимает импорт набора скважин; dxf — линия траектории (через 1 м) в файле DXF. " +
      "path — папка (csv) или файл (dxf); без него открывается окно выбора. Файлы, которые уже есть, заменяются только с overwrite=true или после подтверждения.",
    params: {
      id: ID,
      format: P("string", "формат: csv или dxf", { enum: ["csv", "dxf"] }),
      path: P("string", "полный путь: папка для csv, файл для dxf"),
      overwrite: P("boolean", "заменить уже существующие файлы"),
    },
    required: ["format"],
  },
  {
    id: "planned.delete",
    title: "Удалить проектную скважину",
    description: "Удаляет проектную скважину из проекта (набор скважин и его данные не затрагиваются). Перед удалением показываются объекты, которые от неё зависят.",
    params: { id: ID },
    required: ["id"],
    confirm: true,
  },
  {
    id: "planned.show",
    title: "Показать проектную скважину в 3D",
    description: "Показывает или скрывает в 3D-окне линию проектной скважины с меткой цели (скважина остаётся в проекте). id — номер скважины; без него — все. visible=false скрывает.",
    params: { id: ID, visible: P("boolean", "показать (true, по умолчанию) или скрыть (false)") },
  },
  {
    id: "planned.pick",
    title: "Указать точку на сцене",
    description: "Следующий щелчок по 3D-окну кладёт в форму проектной скважины устье или цель (точка луча на плоскости высоты Z поля).",
    params: { what: P("string", "что указать: collar — устье, target — цель", { enum: ["collar", "target"] }) },
    required: ["what"],
    uiOnly: true,
  },
];

/** The ribbon: the group «Скважины» of «Главная» (`shell/ribbonConfig.ts` puts it in, by the orchestrator). */
export interface PlannedButton {
  command: string;
  args?: Record<string, unknown>;
  label: string;
  icon: IconName;
  hint: string;
  small?: boolean;
}

const icon = (name: string): IconName => name as IconName;

export const PLANNED_GROUP_TITLE = "Скважины";

export const PLANNED_BUTTONS: readonly PlannedButton[] = [
  { command: "planned.new", label: "Проектная скважина", icon: icon("planned"), hint: "Устье, азимут, угол, длина или цель: траектория, прогноз по модели, ближайшие скважины" },
  { command: "planned.evaluate", label: "Прогноз", icon: icon("intercepts"), hint: "Интервалы по телам модели или каркасов и ближайшие скважины у цели", small: true },
  { command: "planned.compare", label: "План / факт", icon: icon("scatter"), hint: "Отклонение фактической скважины от проектной по глубинам", small: true },
  { command: "planned.export", args: { format: "csv" }, label: "Экспорт проектной", icon: icon("export"), hint: "collar.csv и survey.csv — или DXF-линия траектории", small: true },
];
