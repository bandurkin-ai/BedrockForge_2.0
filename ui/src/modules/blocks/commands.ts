/**
 * Commands of block models (ADR 0017 §3; ADR 0023 §11, §13). Rename, delete and select are the common `object.*` with
 * `kind: "blocks"` (`wireframes/objectCommands.ts`); «Свойства» is `object.select`. The display in 3D (Э17, 17.31) —
 * `blocks.show`, `blocks.filter`, `blocks.quick`, `blocks.style`, `blocks.pick`: the declarations are here (pure, the
 * catalog reads them first), the handlers — `showCommands.ts`. `SHOW_BUTTONS` — the buttons of the ribbon tab
 * «Блочные модели», group «Вид» (`shell/ribbonConfig.ts` puts them in, by the orchestrator).
 */
import type { Command, ParamSchema } from "../../shell/commands";
import type { IconName } from "../../shell/Icon";
import { COND_OPS } from "./filters/constructor";

const P = (type: ParamSchema["type"], description: string, extra: Partial<ParamSchema> = {}): ParamSchema => ({ type, description, ...extra });

export const MODEL = P("integer", "номер блочной модели (раздел «Блочные модели» дерева); без него — выбранная модель или единственная в проекте");
export const OPS = COND_OPS.map((o) => o.op);

export const commands: Command[] = [
  {
    id: "blocks.show",
    title: "Показать блочную модель в 3D",
    description:
      "Показать блочную модель в 3D-окне или скрыть её. Оболочка (mode=shell, по умолчанию) — только грани блоков, видимые снаружи; «все блоки» (mode=all) — каждая ячейка с рёбрами до лимита граней. " +
      "Раскраска, фильтр и ограничитель — в виде модели (blocks.style, blocks.filter, blocks.quick) и сохраняются в проекте. Если граней больше лимита, модель показана упрощённо (родители 2×2×2, 4×4×4…) — строка состояния это говорит.",
    params: {
      model_id: MODEL,
      id: P("integer", "то же, что model_id (прежнее имя параметра, его передаёт «Менеджер данных»)"),
      visible: P("boolean", "true — показать (по умолчанию), false — скрыть"),
      mode: P("string", "shell — оболочка, all — все блоки", { enum: ["shell", "all"] }),
    },
  },
  {
    id: "blocks.filter",
    title: "Фильтр блочной модели",
    description:
      "Привязать к блочной модели фильтр из библиотеки проекта (раздел «Фильтры» дерева): в 3D остаются блоки, которые он пропускает; модель при этом показывается. filter_id=null снимает фильтр. " +
      "Без filter_id открывает окно «Фильтры». Фильтр сохраняется в виде проекта.",
    params: { model_id: MODEL, filter_id: P("integer", "номер фильтра; null — без фильтра", { nullable: true }) },
  },
  {
    id: "blocks.quick",
    title: "Быстрый ограничитель блочной модели",
    description:
      "Быстрый ограничитель показа: условие «поле — оператор — значение», например AU > 1. Вместе с фильтром — оба сразу (И). Условия объединяются по И или ИЛИ (join); invert — показать блоки, не прошедшие условия. " +
      "append=true добавляет условие к имеющимся, иначе заменяет их; clear=true снимает ограничитель. Пустое значение — «пусто» и «не пусто» (op=empty, notempty); для «в списке» (op=in) значения — через «;».",
    params: {
      model_id: MODEL,
      field: P("string", "поле модели, например AU"),
      op: P("string", "оператор: = <> > >= < <= empty notempty in", { enum: OPS }),
      value: P("string", "значение: число (0,5) или подпись категории; для op=in — список через «;»"),
      join: P("string", "как объединять условия: and — И (по умолчанию), or — ИЛИ", { enum: ["and", "or"] }),
      invert: P("boolean", "true — обратный ограничитель (НЕ)"),
      append: P("boolean", "true — добавить условие к имеющимся"),
      clear: P("boolean", "true — снять ограничитель"),
    },
  },
  {
    id: "blocks.style",
    title: "Раскраска блочной модели",
    description:
      "Раскраска и вид блочной модели в 3D: поле (field; число — шкала или градации, категория — по кодам; null — без раскраски), kind (none, scale, classes, categories), границы шкалы from и to (null — минимум и максимум поля), log — логарифмическая шкала, " +
      "breaks — границы градаций через «;», hidden — коды скрытых категорий через «;», режим mode, непрозрачность opacity (0,05–1), рёбра edges и их цвет edge_color, лимит граней limit (500 000 – 10 000 000). Блоки без значения не получают цвета нуля — они серые.",
    params: {
      model_id: MODEL,
      field: P("string", "поле модели; null — без раскраски", { nullable: true }),
      kind: P("string", "none — одним цветом, scale — шкала, classes — градации, categories — категории", { enum: ["none", "scale", "classes", "categories"] }),
      from: P("number", "граница шкалы «от»; null — минимум поля", { nullable: true }),
      to: P("number", "граница шкалы «до»; null — максимум поля", { nullable: true }),
      log: P("boolean", "логарифмическая шкала (границы больше нуля)"),
      breaks: P("string", "границы градаций через «;» по возрастанию, например 0,5; 1; 2"),
      hidden: P("string", "коды скрытых категорий через «;»; пустая строка — показать все"),
      mode: P("string", "shell — оболочка, all — все блоки", { enum: ["shell", "all"] }),
      opacity: P("number", "непрозрачность граней, 0,05–1"),
      edges: P("boolean", "рёбра граней: true — рисовать, false — нет"),
      edge_color: P("string", "цвет рёбер #RRGGBB"),
      limit: P("integer", "лимит граней: больше — упрощённый показ (500 000 – 10 000 000)"),
    },
  },
  {
    id: "blocks.pick",
    title: "Блок в точке",
    description:
      "Блок блочной модели в точке проекта (как щелчок по блоку в 3D): ячейка, центр, размер, значения полей (пустое — «—»), проходит ли блок фильтр модели. Координаты — метры проекта. " +
      "Блок скрытый фильтром отвечает «скрыт фильтром». fields — имена полей через «;» (по умолчанию все).",
    params: { model_id: MODEL, x: P("number", "X точки, м"), y: P("number", "Y точки, м"), z: P("number", "Z точки, м"), fields: P("string", "поля через «;»; по умолчанию все") },
    required: ["x", "y", "z"],
  },
  {
    id: "blocks.slice",
    title: "Срез блочной модели плоскостью",
    description:
      "Срез блочной модели плоскостью в 3D: растр пикселей (ячейка, подъячейка или отсчёт наклонной плоскости), раскрашенный полем модели (blocks.style), с фильтром и ограничителем модели; блок без значения серый, не нулевого цвета. " +
      "plane=section — по разрезу: плоскость текущего разреза (линия разреза или разрез серии), срез лежит подложкой под скважинами и идёт за разрезом; plane=x, y или z — перпендикулярно оси проекта на отметке at (по умолчанию — середина модели); " +
      "plane=point — по точке (x, y, z) и нормали (nx, ny, nz). shift (метры) и cells (ячейки, со знаком) сдвигают плоскость вдоль нормали. on=false выключает срез. Вид сохраняется в проекте. " +
      "Плоскость мимо модели — пустой срез, строка состояния это говорит. Многоугольники сечения для печати считает ядро (Э22), здесь их нет.",
    params: {
      model_id: MODEL,
      on: P("boolean", "true — показать срез (по умолчанию), false — убрать"),
      plane: P("string", "section — по разрезу; x, y, z — по оси проекта; point — по точке и нормали", { enum: ["section", "x", "y", "z", "point"] }),
      at: P("number", "отметка плоскости по оси, м (для plane=x, y, z)"),
      x: P("number", "X точки плоскости, м (plane=point)"),
      y: P("number", "Y точки плоскости, м (plane=point)"),
      z: P("number", "Z точки плоскости, м (plane=point)"),
      nx: P("number", "составляющая нормали по X (plane=point)"),
      ny: P("number", "составляющая нормали по Y (plane=point)"),
      nz: P("number", "составляющая нормали по Z (plane=point); 1 — горизонтальный срез"),
      shift: P("number", "сдвиг плоскости вдоль нормали, м (со знаком)"),
      cells: P("number", "сдвиг плоскости вдоль нормали в ячейках модели (со знаком): 1 — на ячейку выше"),
    },
  },
];

/** The ribbon: the tab «Блочные модели», the group «Вид» (`shell/ribbonConfig.ts`, by the orchestrator). */
export interface ShowButton {
  command: string;
  args?: Record<string, unknown>;
  label: string;
  icon: IconName;
  hint: string;
  small?: boolean;
}

export const SHOW_GROUP_TITLE = "Вид";

export const SHOW_BUTTONS: readonly ShowButton[] = [
  { command: "blocks.show", label: "Оболочка", icon: "blocks" as IconName, hint: "Показать выбранную блочную модель в 3D: оболочка — видимые снаружи грани блоков" },
  { command: "blocks.show", args: { mode: "all" }, label: "Все блоки", icon: "blocks" as IconName, hint: "Показать каждую ячейку выбранной модели с рёбрами (до лимита граней)", small: true },
  { command: "blocks.show", args: { visible: false }, label: "Скрыть", icon: "blocks" as IconName, hint: "Убрать выбранную блочную модель из 3D", small: true },
  { command: "blocks.filter", label: "Фильтр", icon: "filter" as IconName, hint: "Фильтр из библиотеки проекта для выбранной модели; без выбора — окно «Фильтры»" },
  { command: "blocks.slice", label: "Срез", icon: "vwSection" as IconName, hint: "Срез выбранной модели плоскостью: по разрезу, если он нарисован, иначе горизонтальный в середине модели" },
  { command: "blocks.slice", args: { plane: "section" }, label: "Срез по разрезу", icon: "vwSection" as IconName, hint: "Срез по плоскости текущего разреза: подложка под скважинами, идёт за разрезами серии", small: true },
  { command: "blocks.slice", args: { cells: 1 }, label: "Срез выше", icon: "chevronUp" as IconName, hint: "Сдвинуть плоскость среза на одну ячейку вдоль нормали (по оси Z — вверх)", small: true },
  { command: "blocks.slice", args: { cells: -1 }, label: "Срез ниже", icon: "chevronDown" as IconName, hint: "Сдвинуть плоскость среза на одну ячейку против нормали (по оси Z — вниз)", small: true },
  { command: "blocks.slice", args: { on: false }, label: "Убрать срез", icon: "close" as IconName, hint: "Убрать срез выбранной блочной модели из 3D", small: true },
];
