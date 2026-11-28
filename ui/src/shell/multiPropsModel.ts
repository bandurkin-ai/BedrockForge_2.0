/**
 * «Свойства» of several objects of one kind (Д-44 А5): which fields they share, the value of a field over the
 * whole set («разные» when they differ), and the registry commands that set a value on every object — and the
 * same commands with the previous values, the one step «Отменить». Pure: no React.
 *
 * A kind has a field when the registry has a command that sets it (visibility, colour, opacity, the width of the traces,
 * the folder of a view…). A kind without such a command has no fields: its rows are chosen all the same and the panel
 * says so (`WHY_NO_FIELDS`).
 */
import { normalizeColor } from "./props";
import { plural } from "./treeActions";
import { isObjectMulti, unpair, type PickKind, type RowRef, type Selected } from "./selection";

/** A value of a field: a flag, a colour or a text (null — none of its own) or a number. */
export type FieldValue = boolean | string | number | null;

export interface Step {
  command: string;
  args: Record<string, unknown>;
}

export type FieldType = "check" | "color" | "number" | "text";

export interface MultiField {
  key: "visible" | "color" | "opacity" | "traceColor" | "traceWidth" | "collarSize" | "folder";
  label: string;
  type: FieldType;
  /** the command that changes it, to ask the registry whether it is available */
  command: (kind: PickKind) => string;
  kinds: readonly PickKind[];
  /** the commands that set `value` on object `id` of `kind` */
  steps: (kind: PickKind, id: number, value: FieldValue) => Step[];
  /** number fields: what the value may be and an example for the message of a wrong one */
  range?: { min: number; max: number };
  example?: string;
  /** colour fields: false — the object has a colour always, «Без цвета» is not offered */
  nullable?: boolean;
}

const LAYER: Partial<Record<PickKind, string>> = { lines: "lines", raster: "raster", wireframe: "wireframe", set: "set" };

/** The commands that show or hide an object of a kind: `visible` is a flag of all of them. */
const SHOW: Partial<Record<PickKind, (id: number, visible: boolean) => Step>> = {
  blocks: (id, visible) => ({ command: "blocks.show", args: { model_id: id, visible } }),
  trend: (id, visible) => ({ command: "trend.show", args: { trend_id: id, visible } }),
  planned: (id, visible) => ({ command: "planned.show", args: { id, visible } }),
  swath: (id, visible) => ({ command: "stats.swath_3d", args: { swath_id: id, visible } }),
  // a group of data of a model is named by two numbers (`pairId`): the points of the group are what the check box shows
  implicit_group: (id, visible) => {
    const [model, group] = unpair(id);
    return { command: "implicit.show", args: { model_id: model, group_id: group, what: "points", visible } };
  },
};

const SHOW_COMMAND: Partial<Record<PickKind, string>> = { blocks: "blocks.show", trend: "trend.show", planned: "planned.show", swath: "stats.swath_3d", implicit_group: "implicit.show" };

export const OPACITY_RANGE = { min: 0.05, max: 1 } as const;
export const TRACE_WIDTH_RANGE = { min: 0.5, max: 12 } as const;
export const COLLAR_SIZE_RANGE = { min: 4, max: 40 } as const;

const num = (v: FieldValue): number => (typeof v === "number" ? v : Number(v));

/** The fields that every object of the kind has and the registry can change (the others are in its own panel). */
export const FIELDS: readonly MultiField[] = [
  {
    key: "visible",
    label: "Показан в 3D",
    type: "check",
    kinds: ["lines", "raster", "wireframe", "blocks", "set", "trend", "planned", "swath", "implicit_group"],
    command: (k) => SHOW_COMMAND[k] ?? "layer.show",
    steps: (kind, id, value) => {
      const visible = value === true;
      const show = SHOW[kind];
      return [show ? show(id, visible) : { command: "layer.show", args: { layer: LAYER[kind], id, visible } }];
    },
  },
  {
    key: "color",
    label: "Цвет",
    type: "color",
    kinds: ["lines", "wireframe"],
    command: () => "object.color",
    steps: (kind, id, value) => [{ command: "object.color", args: { kind, id, color: typeof value === "string" ? value : null } }],
  },
  {
    key: "opacity",
    label: "Непрозрачность",
    type: "number",
    kinds: ["raster", "blocks"],
    range: OPACITY_RANGE,
    example: "0,8",
    command: (k) => (k === "blocks" ? "blocks.style" : "raster.set"),
    steps: (kind, id, value) => [kind === "blocks" ? { command: "blocks.style", args: { model_id: id, opacity: value } } : { command: "raster.set", args: { raster_id: id, opacity: value } }],
  },
  {
    key: "traceColor",
    label: "Цвет стволов",
    type: "color",
    kinds: ["set"],
    nullable: false,
    command: () => "holes.trace_style",
    steps: (_kind, id, value) => [{ command: "holes.trace_style", args: { set_id: id, color: value } }],
  },
  {
    key: "traceWidth",
    label: "Толщина стволов, px",
    type: "number",
    kinds: ["set"],
    range: TRACE_WIDTH_RANGE,
    example: "1,5",
    command: () => "holes.trace_style",
    steps: (_kind, id, value) => [{ command: "holes.trace_style", args: { set_id: id, width: num(value) } }],
  },
  {
    key: "collarSize",
    label: "Размер устьев, px",
    type: "number",
    kinds: ["set"],
    range: COLLAR_SIZE_RANGE,
    example: "10",
    command: () => "holes.collar_style",
    steps: (_kind, id, value) => [{ command: "holes.collar_style", args: { set_id: id, size: num(value) } }],
  },
  {
    key: "folder",
    label: "Папка",
    type: "text",
    kinds: ["view"],
    command: () => "views.folder",
    steps: (_kind, id, value) => [{ command: "views.folder", args: { view_id: id, folder: typeof value === "string" ? value : "" } }],
  },
];

export const fieldsOf = (kind: PickKind): MultiField[] => FIELDS.filter((f) => f.kinds.includes(kind));

/** Why a kind has no field to change: shown in the panel instead of the fields. */
const WHY: Partial<Record<PickKind, string>> = {
  series: "У серии нет значений, общих для нескольких: активной бывает одна, параметры задаёт окно серии.",
  filter: "Фильтр задаёт условие и не имеет настроек показа: его условие правит окно «Фильтры».",
  vario: "Показ эллипсоида открывает окно модели, его состояние не хранится в проекте; параметры правит окно «Вариография».",
  est_row: "Параметры оценки задаёт мастер для каждой строки отдельно.",
  est_plan: "Состав плана правит окно плана.",
  sim_row: "Параметры моделирования задаёт мастер для каждой строки отдельно.",
  qaqc_data: "У данных QAQC нет настроек показа; их правит окно «Настройка QAQC».",
  qaqc_report: "Отчёт QAQC только читается.",
  display: "У отображения по стволу нет команды реестра для общего изменения: настройки — в его форме.",
  chart: "Настройки графика хранятся в самом графике: откройте его.",
  result: "Результат обработки проб только читается.",
  implicit: "Показ неявной модели задаётся по группам данных, а не для модели целиком: откройте модель.",
  pit_scenario: "Оболочку карьера показывает команда с номером оболочки для каждого сценария отдельно.",
  resource_report: "Отчёт по ресурсам только читается.",
  line_file: "Видимость файла — это видимость его слоёв: выберите слои линий файла, у них есть общие поля.",
  implicit_fault: "Разлом — результат построения, только чтение: его окно открывает двойной щелчок.",
  implicit_stack: "Порядок и отношения доменов правит окно «Стек отношений».",
  series_section: "Разрез серии — переход по щелчку: у него нет свойств, параметры задаёт окно серии.",
};

export const whyNoFields = (kind: PickKind): string => WHY[kind] || "У этих объектов нет общих свойств для изменения: откройте каждый по отдельности.";

/** What the panel reads the current values from: `undefined` — the object is not in the lists (it is gone). */
export interface Lookup {
  /** the object is in the lists of the project: a deleted one stays in the set of the chosen until the tree is redrawn */
  exists: (kind: PickKind, id: number) => boolean;
  hidden: readonly string[];
  blockShown: (id: number) => boolean;
  layerColor: (id: number) => string | null | undefined;
  wireframeColor: (id: number) => string | null | undefined;
  rasterOpacity: (id: number) => number | undefined;
  /** the opacity of a block model */
  blockOpacity?: (id: number) => number | undefined;
  /** the field of a row of a section without `object.select` (a set, a trend, a view…) */
  other?: (kind: PickKind, id: number, key: MultiField["key"]) => FieldValue | undefined;
}

const HIDDEN_KEY: Partial<Record<PickKind, string>> = { lines: "ln", raster: "rs", wireframe: "wf" };

/** The value of a field of one object. */
export function fieldValue(f: MultiField, kind: PickKind, id: number, lk: Lookup): FieldValue | undefined {
  if (!lk.exists(kind, id)) return undefined;
  if (!isObjectMulti(kind)) return lk.other?.(kind, id, f.key);
  if (f.key === "visible") return kind === "blocks" ? lk.blockShown(id) : !lk.hidden.includes(`${HIDDEN_KEY[kind]}:${id}`);
  if (f.key === "color") return kind === "lines" ? lk.layerColor(id) : lk.wireframeColor(id);
  return kind === "blocks" ? lk.blockOpacity?.(id) : lk.rasterOpacity(id);
}

export interface Common {
  /** the values differ: the field shows «разные» */
  mixed: boolean;
  /** the common value (the first one when they differ) */
  value: FieldValue;
}

/** One value for all, or «разные». Colours compare as written by `normalizeColor`. */
export function commonValue(values: readonly FieldValue[]): Common {
  const norm = (v: FieldValue) => (typeof v === "string" ? (normalizeColor(v) ?? v) : v);
  const first = norm(values[0] ?? null);
  return { mixed: values.some((v) => norm(v) !== first), value: first };
}

/** The values of the field over the objects that exist: `{id, value}`. */
export function readField(f: MultiField, kind: PickKind, ids: readonly number[], lk: Lookup): { id: number; value: FieldValue }[] {
  return ids.flatMap((id) => {
    const value = fieldValue(f, kind, id, lk);
    return value === undefined ? [] : [{ id, value }];
  });
}

/** The commands that set `value` on every object, in the order of the set. */
export function applyPlan(f: MultiField, kind: PickKind, ids: readonly number[], value: FieldValue): Step[] {
  return ids.flatMap((id) => f.steps(kind, id, value));
}

/** The commands that bring the objects back to the values they had (`before` — what `readField` gave). */
export function revertPlan(f: MultiField, kind: PickKind, before: readonly { id: number; value: FieldValue }[]): Step[] {
  return before.flatMap((b) => f.steps(kind, b.id, b.value));
}

export type RunStep = (command: string, args: Record<string, unknown>) => Promise<boolean>;

/** Sets `value` on every chosen object one after another. An object that is gone (before or during the run) is
 * skipped — «объект не найден» does not stop the others; any other failure stops the run. `undo` brings the objects
 * that were changed back — the one step of «Отменить» of the whole change. */
export async function applyAll(run: RunStep, f: MultiField, kind: PickKind, ids: readonly number[], lk: Lookup, value: FieldValue): Promise<{ done: number; total: number; skipped: number; undo: Step[] }> {
  const before = readField(f, kind, ids, lk);
  const changed: { id: number; value: FieldValue }[] = [];
  let gone = 0;
  for (const b of before) {
    if (!lk.exists(kind, b.id)) {
      gone++;
      continue;
    }
    let ok = true;
    for (const step of f.steps(kind, b.id, value)) {
      if (!(await run(step.command, step.args))) {
        ok = false;
        break;
      }
    }
    if (ok) changed.push(b);
    else if (lk.exists(kind, b.id)) break; // a real failure: stop, the rest stays as it was
    else gone++; // deleted while the run was going
  }
  return { done: changed.length, total: before.length - gone, skipped: ids.length - before.length + gone, undo: revertPlan(f, kind, changed) };
}

/** What the text of a number field is: the value, or the reason it is not one («0,5», «0.5»). */
export function parseNumber(text: string, range: { min: number; max: number }, what: string, example = ""): { value: number | null; error: string | null } {
  const t = text.trim().replace(",", ".");
  if (!t) return { value: null, error: null };
  const v = Number(t);
  const show = (x: number) => String(x).replace(".", ",");
  const hint = `от ${show(range.min)} до ${show(range.max)}${example ? `, например ${example}` : ""}`;
  if (!Number.isFinite(v)) return { value: null, error: `«${text.trim()}» — не число. Введите число ${hint}.` };
  if (v < range.min || v > range.max) return { value: null, error: `${what} — число ${hint}.` };
  return { value: v, error: null };
}

/** The text of a number field → the opacity, or the reason it is not one («0,5», «0.5»). */
export const parseOpacity = (text: string): { value: number | null; error: string | null } => parseNumber(text, OPACITY_RANGE, "Непрозрачность", "0,8");

const KIND_PLURALS: Record<PickKind, [string, string, string]> = {
  wireframe: ["каркас", "каркаса", "каркасов"],
  lines: ["слой линий", "слоя линий", "слоёв линий"],
  raster: ["растр", "растра", "растров"],
  blocks: ["блочная модель", "блочные модели", "блочных моделей"],
  implicit: ["неявная модель", "неявные модели", "неявных моделей"],
  resource_report: ["отчёт по ресурсам", "отчёта по ресурсам", "отчётов по ресурсам"],
  pit_scenario: ["сценарий карьера", "сценария карьера", "сценариев карьера"],
  set: ["набор скважин", "набора скважин", "наборов скважин"],
  view: ["вид", "вида", "видов"],
  series: ["серия разрезов", "серии разрезов", "серий разрезов"],
  filter: ["фильтр", "фильтра", "фильтров"],
  trend: ["тренд", "тренда", "трендов"],
  vario: ["модель вариограммы", "модели вариограмм", "моделей вариограмм"],
  est_row: ["строка оценки", "строки оценки", "строк оценки"],
  est_plan: ["план оценки", "плана оценки", "планов оценки"],
  sim_row: ["моделирование", "моделирования", "моделирований"],
  planned: ["проектная скважина", "проектные скважины", "проектных скважин"],
  swath: ["swath plot", "swath plot", "swath plot"],
  qaqc_data: ["набор данных QAQC", "набора данных QAQC", "наборов данных QAQC"],
  qaqc_report: ["отчёт QAQC", "отчёта QAQC", "отчётов QAQC"],
  display: ["отображение по стволу", "отображения по стволу", "отображений по стволу"],
  chart: ["график", "графика", "графиков"],
  result: ["результат обработки проб", "результата обработки проб", "результатов обработки проб"],
  line_file: ["файл линий", "файла линий", "файлов линий"],
  implicit_group: ["группа данных модели", "группы данных модели", "групп данных модели"],
  implicit_fault: ["разлом", "разлома", "разломов"],
  implicit_stack: ["домен стека", "домена стека", "доменов стека"],
  series_section: ["разрез серии", "разреза серии", "разрезов серии"],
};

/** «3 слоя линий» */
export const kindCount = (kind: PickKind, n: number): string => `${n} ${plural(n, ...KIND_PLURALS[kind])}`;

/** The kind all the objects share, or null when they differ (or there are none). */
export function commonKind(items: readonly (Selected | RowRef)[]): PickKind | null {
  const first = items[0]?.kind;
  return first && items.every((x) => x.kind === first) ? first : null;
}
