/**
 * «Свойства» of several objects of one kind (Д-44 А5): which fields they share, the value of a field over the
 * whole set («разные» when they differ), and the registry commands that set a value on every object — and the
 * same commands with the previous values, the one step «Отменить». Pure: no React.
 */
import { normalizeColor } from "./props";
import { plural } from "./treeActions";
import type { ObjectKind, Selected } from "./selection";

/** A value of a field: a flag, a colour (null — none of its own) or a number. */
export type FieldValue = boolean | string | number | null;

export interface Step {
  command: string;
  args: Record<string, unknown>;
}

export type FieldType = "check" | "color" | "number";

export interface MultiField {
  key: "visible" | "color" | "opacity";
  label: string;
  type: FieldType;
  /** the command that changes it, to ask the registry whether it is available */
  command: (kind: ObjectKind) => string;
  kinds: readonly ObjectKind[];
  /** the commands that set `value` on object `id` of `kind` */
  steps: (kind: ObjectKind, id: number, value: FieldValue) => Step[];
}

const LAYER: Partial<Record<ObjectKind, string>> = { lines: "lines", raster: "raster", wireframe: "wireframe" };

export const OPACITY_RANGE = { min: 0.05, max: 1 } as const;

/** The fields that every object of the kind has and the registry can change (the others are in its own panel). */
export const FIELDS: readonly MultiField[] = [
  {
    key: "visible",
    label: "Показан в 3D",
    type: "check",
    kinds: ["lines", "raster", "wireframe", "blocks"],
    command: (k) => (k === "blocks" ? "blocks.show" : "layer.show"),
    steps: (kind, id, value) =>
      kind === "blocks" ? [{ command: "blocks.show", args: { model_id: id, visible: value === true } }] : [{ command: "layer.show", args: { layer: LAYER[kind], id, visible: value === true } }],
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
    kinds: ["raster"],
    command: () => "raster.set",
    steps: (_kind, id, value) => [{ command: "raster.set", args: { raster_id: id, opacity: value } }],
  },
];

export const fieldsOf = (kind: ObjectKind): MultiField[] => FIELDS.filter((f) => f.kinds.includes(kind));

/** What the panel reads the current values from: `undefined` — the object is not in the lists (it is gone). */
export interface Lookup {
  /** the object is in the lists of the project: a deleted one stays in the set of the chosen until the tree is redrawn */
  exists: (kind: ObjectKind, id: number) => boolean;
  hidden: readonly string[];
  blockShown: (id: number) => boolean;
  layerColor: (id: number) => string | null | undefined;
  wireframeColor: (id: number) => string | null | undefined;
  rasterOpacity: (id: number) => number | undefined;
}

const HIDDEN_KEY: Partial<Record<ObjectKind, string>> = { lines: "ln", raster: "rs", wireframe: "wf" };

/** The value of a field of one object. */
export function fieldValue(f: MultiField, kind: ObjectKind, id: number, lk: Lookup): FieldValue | undefined {
  if (!lk.exists(kind, id)) return undefined;
  if (f.key === "visible") return kind === "blocks" ? lk.blockShown(id) : !lk.hidden.includes(`${HIDDEN_KEY[kind]}:${id}`);
  if (f.key === "color") return kind === "lines" ? lk.layerColor(id) : lk.wireframeColor(id);
  return lk.rasterOpacity(id);
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
export function readField(f: MultiField, kind: ObjectKind, ids: readonly number[], lk: Lookup): { id: number; value: FieldValue }[] {
  return ids.flatMap((id) => {
    const value = fieldValue(f, kind, id, lk);
    return value === undefined ? [] : [{ id, value }];
  });
}

/** The commands that set `value` on every object, in the order of the set. */
export function applyPlan(f: MultiField, kind: ObjectKind, ids: readonly number[], value: FieldValue): Step[] {
  return ids.flatMap((id) => f.steps(kind, id, value));
}

/** The commands that bring the objects back to the values they had (`before` — what `readField` gave). */
export function revertPlan(f: MultiField, kind: ObjectKind, before: readonly { id: number; value: FieldValue }[]): Step[] {
  return before.flatMap((b) => f.steps(kind, b.id, b.value));
}

export type RunStep = (command: string, args: Record<string, unknown>) => Promise<boolean>;

/** Sets `value` on every chosen object one after another. An object that is gone (before or during the run) is
 * skipped — «объект не найден» does not stop the others; any other failure stops the run. `undo` brings the objects
 * that were changed back — the one step of «Отменить» of the whole change. */
export async function applyAll(run: RunStep, f: MultiField, kind: ObjectKind, ids: readonly number[], lk: Lookup, value: FieldValue): Promise<{ done: number; total: number; skipped: number; undo: Step[] }> {
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

/** The text of a number field → the opacity, or the reason it is not one («0,5», «0.5»). */
export function parseOpacity(text: string): { value: number | null; error: string | null } {
  const t = text.trim().replace(",", ".");
  if (!t) return { value: null, error: null };
  const v = Number(t);
  if (!Number.isFinite(v)) return { value: null, error: `«${text.trim()}» — не число. Введите число от 0,05 до 1, например 0,8.` };
  if (v < OPACITY_RANGE.min || v > OPACITY_RANGE.max) return { value: null, error: "Непрозрачность — число от 0,05 до 1, например 0,8." };
  return { value: v, error: null };
}

const KIND_PLURALS: Record<ObjectKind, [string, string, string]> = {
  wireframe: ["каркас", "каркаса", "каркасов"],
  lines: ["слой линий", "слоя линий", "слоёв линий"],
  raster: ["растр", "растра", "растров"],
  blocks: ["блочная модель", "блочные модели", "блочных моделей"],
  implicit: ["неявная модель", "неявные модели", "неявных моделей"],
  resource_report: ["отчёт по ресурсам", "отчёта по ресурсам", "отчётов по ресурсам"],
  pit_scenario: ["сценарий карьера", "сценария карьера", "сценариев карьера"],
};

/** «3 слоя линий» */
export const kindCount = (kind: ObjectKind, n: number): string => `${n} ${plural(n, ...KIND_PLURALS[kind])}`;

/** The kind all the objects share, or null when they differ (or there are none). */
export function commonKind(items: readonly Selected[]): ObjectKind | null {
  const first = items[0]?.kind;
  return first && items.every((x) => x.kind === first) ? first : null;
}
