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
];
