/**
 * The window of an operation as data (ТЗ 7.23; ADR 0015 §6 «Команды»): `GET /operations` gives 16 operations and
 * the fields of their windows (`core/bf_core/ops/schema.py`); one window is built per `dialog` (13 of them — the
 * three clips share the window `clip`). The values of the form are what the person typed (numbers as text with a
 * comma or a point; line numbers from 1, «1, 3–5»); `paramsOf` turns them into the `params` of
 * `POST /operations/check` and `POST /operations` or says, in Russian, why they cannot be sent. A field with
 * `when` is shown and sent only while that other field has that value. An empty number is «not set», never 0.
 * Pure: no React, no requests.
 */
import { counted } from "../../shell/treeActions";
import { parseNumber } from "../importing/objects";

export type FieldType = "wireframe" | "wireframes" | "lines" | "line" | "layers" | "sets" | "number" | "integer" | "bool" | "choice" | "vector" | "triangles";

export interface OpField {
  key: string;
  label: string;
  type: FieldType;
  required?: boolean;
  default?: unknown;
  options?: { value: string; label: string }[];
  /** `closed` / `open`: which wireframes fit */
  kinds?: ("closed" | "open")[];
  min?: number;
  max?: number;
  /** shown and sent only while these fields have these values */
  when?: Record<string, unknown>;
  unit?: string;
}

export interface OpSpec {
  op: string;
  title: string;
  /** the window; null — a panel of the edit mode («Править треугольники») */
  dialog: string | null;
  /** `mesh` — wireframes, `lines` — a line file, `report` — a report (and wireframes unless «только отчёт») */
  result: "mesh" | "lines" | "report";
  job: string;
  fields: OpField[];
}

/** The dialog commands and their windows: 13, «Обрезать» — one window of three operations. */
export const DIALOG_COMMANDS: Record<string, string> = {
  "model.stitch": "stitch",
  "model.extrude": "extrude",
  "model.repair": "repair",
  "model.simplify": "simplify",
  "model.smooth": "smooth",
  "model.merge": "merge",
  "model.split": "split",
  "model.boolean": "boolean",
  "model.clip": "clip",
  "model.intersection": "intersection_lines",
  "model.dtm": "dtm",
  "model.contours": "contours",
  "model.volume": "volume_between",
};

/** Titles of the windows (the clip window is not one operation). */
export const DIALOG_TITLES: Record<string, string> = { clip: "Обрезать" };

/** Lines of a layer: `text` — numbers from 1 («1, 3–5»), empty — every line of the layer. */
export interface LinesPick {
  layer_id: number;
  text: string;
}

/** One closed line: the layer and its number from 1 (empty — the only closed line of the layer). */
export interface LinePick {
  layer_id: number | null;
  text: string;
}

export type FieldValue = string | boolean | number | null | number[] | LinesPick[] | LinePick;
export type FormValues = Record<string, FieldValue>;

/** What the window opens with: the object chosen in the tree or named by the command. */
export interface Prefill {
  wireframeId?: number | null;
  layerId?: number | null;
}

/** The windows of the list in its order (each once). */
export function dialogsOf(list: readonly OpSpec[]): string[] {
  return [...new Set(list.map((s) => s.dialog).filter((d): d is string => !!d))];
}

/** The operations of a window: one, or three for `clip`. */
export const dialogOps = (list: readonly OpSpec[], dialog: string): OpSpec[] => list.filter((s) => s.dialog === dialog);

export function dialogTitle(list: readonly OpSpec[], dialog: string): string {
  return DIALOG_TITLES[dialog] ?? dialogOps(list, dialog)[0]?.title ?? dialog;
}

/** «0,5» of a default number; «0,000001» — not «1e-6». */
export function numberText(v: unknown): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "";
  return v.toLocaleString("ru-RU", { maximumFractionDigits: 12, useGrouping: false });
}

function initial(f: OpField): FieldValue {
  switch (f.type) {
    case "number":
    case "integer":
      return numberText(f.default);
    case "bool":
      return typeof f.default === "boolean" ? f.default : false;
    case "choice":
      return typeof f.default === "string" ? f.default : (f.options?.[0]?.value ?? "");
    case "vector":
      return Array.isArray(f.default) ? f.default.map(numberText).join("; ") : "";
    case "wireframe":
      return null;
    case "line":
      return { layer_id: null, text: "" };
    default:
      return [];
  }
}

/** The values a window opens with: the defaults of the schema; the chosen wireframe goes to the first wireframe
 * field, the chosen layer — to the first field of lines or layers. */
export function initialValues(spec: OpSpec, pre: Prefill = {}): FormValues {
  const out: FormValues = Object.fromEntries(spec.fields.map((f) => [f.key, initial(f)]));
  const wf = pre.wireframeId ?? null;
  const layer = pre.layerId ?? null;
  const first = (types: FieldType[]) => spec.fields.find((f) => types.includes(f.type));
  const w = wf === null ? undefined : first(["wireframe", "wireframes"]);
  if (w) out[w.key] = w.type === "wireframe" ? wf : [wf!];
  const l = layer === null ? undefined : first(["lines", "line", "layers"]);
  if (l) out[l.key] = l.type === "lines" ? [{ layer_id: layer!, text: "" }] : l.type === "line" ? { layer_id: layer, text: "" } : [layer!];
  return out;
}

/** Another operation of the same window (`clip`): what fits keeps its value. */
export function switchValues(spec: OpSpec, before: FormValues): FormValues {
  const out = initialValues(spec);
  for (const f of spec.fields) if (f.key in before && f.type !== "choice") out[f.key] = before[f.key];
  return out;
}

/** The field is shown (and sent) now. */
export function shown(f: OpField, values: FormValues): boolean {
  return !f.when || Object.entries(f.when).every(([k, v]) => values[k] === v);
}

export const visibleFields = (spec: OpSpec, values: FormValues): OpField[] => spec.fields.filter((f) => shown(f, values));

/** Numbers of lines from 1 → `lid`s (from 0; a layer without edits numbers its lines by position):
 * «1, 3–5; 7» → [0, 2, 3, 4, 6]; empty → null (every line); undefined — not a list of numbers. */
export function parseLineNumbers(text: string): number[] | null | undefined {
  const t = text.trim();
  if (!t) return null;
  const out: number[] = [];
  for (const part of t.split(/[,;\s]+/).filter(Boolean)) {
    const m = /^(\d+)(?:[-–—](\d+))?$/.exec(part);
    if (!m) return undefined;
    const lo = Number(m[1]);
    const hi = m[2] === undefined ? lo : Number(m[2]);
    if (lo < 1 || hi < lo || hi - lo > 200_000 || out.length > 200_000) return undefined;
    for (let n = lo; n <= hi; n++) out.push(n - 1);
  }
  return [...new Set(out)];
}

export interface ParamsResult {
  params: Record<string, unknown> | null;
  /** why the operation cannot be checked or started; null — it can */
  error: string | null;
  /** the field of the error */
  field: string | null;
}

const span = (f: OpField): string => (f.min !== undefined && f.max !== undefined ? `от ${numberText(f.min)} до ${numberText(f.max)}` : f.min !== undefined ? `не меньше ${numberText(f.min)}` : `не больше ${numberText(f.max)}`);

function numberParam(f: OpField, text: string): { value?: number | null; error?: string } {
  const v = parseNumber(text);
  if (v === undefined) return { error: `«${f.label}» — не число. Введите ${f.type === "integer" ? "целое число, например 10" : "число, например 0,5"}.` };
  if (v === null) return f.required ? { error: `Не задано: «${f.label}».` } : { value: null };
  if (f.type === "integer" && !Number.isInteger(v)) return { error: `«${f.label}» — целое число.` };
  if ((f.min !== undefined && v < f.min) || (f.max !== undefined && v > f.max)) return { error: `«${f.label}» — ${span(f)}.` };
  return { value: v };
}

function vectorParam(f: OpField, text: string): { value?: number[] | null; error?: string } {
  const parts = text.split(/[;\s]+/).filter(Boolean);
  if (!parts.length) return f.required ? { error: `Не задано: «${f.label}» — три числа через точку с запятой.` } : { value: null };
  const nums = parts.map((p) => parseNumber(p));
  if (nums.length !== 3 || nums.some((n) => typeof n !== "number")) return { error: `«${f.label}» — три числа через точку с запятой, например 0; 0; 1.` };
  return { value: nums as number[] };
}

/** The `params` of the operation, or why they cannot be sent. */
export function paramsOf(spec: OpSpec, values: FormValues): ParamsResult {
  const params: Record<string, unknown> = {};
  const fail = (f: OpField, error: string): ParamsResult => ({ params: null, error, field: f.key });
  for (const f of visibleFields(spec, values)) {
    const v = values[f.key];
    switch (f.type) {
      case "number":
      case "integer": {
        const r = numberParam(f, typeof v === "string" ? v : "");
        if (r.error) return fail(f, r.error);
        if (r.value !== null) params[f.key] = r.value;
        break;
      }
      case "vector": {
        const r = vectorParam(f, typeof v === "string" ? v : "");
        if (r.error) return fail(f, r.error);
        if (r.value !== null) params[f.key] = r.value;
        break;
      }
      case "bool":
        params[f.key] = v === true;
        break;
      case "choice":
        params[f.key] = typeof v === "string" && v ? v : (f.options?.[0]?.value ?? null);
        break;
      case "wireframe":
        if (typeof v !== "number") {
          if (f.required) return fail(f, `Выберите: «${f.label}».`);
        } else params[f.key] = v;
        break;
      case "wireframes":
      case "layers":
      case "sets": {
        const ids = Array.isArray(v) ? (v as number[]) : [];
        const least = f.min ?? (f.required ? 1 : 0);
        if (ids.length < least) return fail(f, least > 1 ? `«${f.label}»: отметьте не меньше ${counted(least, "объекта", "объектов", "объектов")}.` : `Выберите: «${f.label}».`);
        if (ids.length || f.type === "wireframes") params[f.key] = [...ids];
        break;
      }
      case "lines": {
        const picks = Array.isArray(v) ? (v as LinesPick[]) : [];
        if (!picks.length) {
          if (f.required) return fail(f, `Отметьте слои: «${f.label}».`);
          break;
        }
        const out: { layer_id: number; lines: number[] | null }[] = [];
        for (const p of picks) {
          const lines = parseLineNumbers(p.text);
          if (lines === undefined) return fail(f, `«${f.label}»: номера линий — целые от 1 через запятую, диапазон через дефис («1, 3–5»), или пусто — все линии слоя.`);
          out.push({ layer_id: p.layer_id, lines });
        }
        params[f.key] = out;
        break;
      }
      case "line": {
        const p = (v ?? { layer_id: null, text: "" }) as LinePick;
        if (p.layer_id === null) {
          if (f.required) return fail(f, `Выберите слой: «${f.label}».`);
          break;
        }
        const t = p.text.trim();
        const n = t ? parseNumber(t) : null;
        if (n === undefined || (typeof n === "number" && (!Number.isInteger(n) || n < 1))) return fail(f, `«${f.label}»: номер линии — целое от 1, или пусто — единственная замкнутая линия слоя.`);
        params[f.key] = { layer_id: p.layer_id, line: n === null ? null : n - 1 };
        break;
      }
      case "triangles":
        if (Array.isArray(v) && v.length) params[f.key] = v;
        break;
    }
  }
  return { params, error: null, field: null };
}

/** A wireframe as the window needs it: its name and what was measured when it was stored. */
export interface WfBrief {
  id: number;
  name: string;
  stats?: { closed?: boolean; open_edges?: number; nonmanifold_edges?: number } | null;
}

/** Why a wireframe cannot be a solid: «открытых рёбер — 14»; null — it is closed (or not known yet). */
export function openDetail(w: WfBrief): string | null {
  const s = w.stats;
  if (!s || s.closed !== false) return null;
  if (s.open_edges) return `открытых рёбер — ${s.open_edges}`;
  if (s.nonmanifold_edges) return `рёбер с тремя и более треугольниками — ${s.nonmanifold_edges}`;
  return "каркас не проверен";
}

/** The fix an open wireframe is offered: «Исправить каркас» (`model.repair` with it). */
export interface Fix {
  op: "repair";
  wireframe_id: number;
}

/** Before any request: a wireframe chosen where a closed solid is needed is not closed — named, with its open
 * edges and the fix. The core says the same at the check (422 `wireframe_open`). */
export function openWireframeNote(spec: OpSpec, values: FormValues, wireframes: readonly WfBrief[]): { text: string; fix: Fix } | null {
  for (const f of visibleFields(spec, values)) {
    if (!f.kinds?.includes("closed")) continue;
    const v = values[f.key];
    const ids = typeof v === "number" ? [v] : Array.isArray(v) ? (v as number[]) : [];
    for (const id of ids) {
      const w = wireframes.find((x) => x.id === id);
      const detail = w && openDetail(w);
      if (w && detail) return { text: `Каркас «${w.name}» не замкнут: ${detail}. Запустите «Исправить каркас» и повторите.`, fix: { op: "repair", wireframe_id: w.id } };
    }
  }
  return null;
}

/** Which wireframes a field offers: `open` — surfaces (not closed solids); `closed` — every wireframe, an open
 * one marked (the check refuses it and offers the fix). */
export function wireframeChoices<W extends WfBrief>(f: OpField, wireframes: readonly W[]): W[] {
  if (f.kinds?.includes("open") && !f.kinds.includes("closed")) return wireframes.filter((w) => w.stats?.closed !== true);
  return [...wireframes];
}
