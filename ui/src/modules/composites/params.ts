/**
 * Parameters of the sample-processing tools as data (ТЗ 8.2.2, 7.35.4; ADR 0018 §10): what the form of a tool
 * window shows, the ranges of 8.2.2, the text of a number in Russian («0,5»), the check of the values before a
 * request — a value out of its range never reaches the core — and the same check of the arguments of a command
 * from the assistant. A tool is a `ToolSpec`: its command, the route of its job, the operation of its preview,
 * what it takes from the source (table, fields, domain). Pure: no React.
 */

export type ParamKind = "number" | "integer" | "choice" | "boolean" | "field" | "text" | "list";

export interface Choice {
  value: string;
  label: string;
}

export interface ParamSpec {
  /** The key of the command argument and of the body of the request. */
  key: string;
  label: string;
  kind: ParamKind;
  unit?: string;
  /** Inclusive range (8.2.2); `list` — of every item. */
  min?: number;
  max?: number;
  default?: number | string | boolean | null;
  /** Empty is allowed: the key is left out of the body (the core takes its default). */
  optional?: boolean;
  choices?: readonly Choice[];
  /** `field` — numeric fields only. */
  numeric?: boolean;
  hint?: string;
  /** Shown and sent only when this is true for the current values. */
  when?: (values: FormValues) => boolean;
  /** A switch of the form only (which of the parameters below is given): never sent. */
  formOnly?: boolean;
}

/** The values of a form as typed: numbers stay text until the check («0,5», «2»). */
export type FormValues = Record<string, string | boolean>;

export interface SourceNeeds {
  /** One table (`table`) or several (`tables`). */
  table: "one" | "many";
  /** Fields of the table: none, one (`field`) or several (`fields`). */
  fields: "none" | "one" | "many";
  /** A domain may be chosen: a field of an interval table or wireframes. */
  domain: boolean;
  /** Wireframes are the subject of the tool (`wireframe_ids`). */
  wireframes?: boolean;
}

export interface ToolSpec {
  /** The tool id — also the window kind `composites-<id>` and its file `tools/<id>.ts`. */
  id: string;
  command: string;
  title: string;
  /** The route of the job under `/composites` (ADR 0018 §11). */
  endpoint: string;
  /** The operation of `POST /composites/{op}/preview`; null — no preview. */
  preview: ((values: FormValues) => string) | null;
  source: SourceNeeds;
  params: readonly ParamSpec[];
  /** The arguments without which the command opens the window instead of starting the job. */
  required: readonly string[];
  /** What the job makes (ADR 0018 §3): a new table of the set (409 `table_exists` → «Заменить?»), fields written
   * into the source table (the assistant asks first — `confirm`), or a report without a table. */
  writes: "table" | "fields" | "report";
  /** Shown above the form. */
  note: string;
  /** The values of the form from the arguments of the command beyond `valuesFromArgs` (form-only switches). */
  fromArgs?: (args: Record<string, unknown>, values: FormValues) => FormValues;
  /** The last step of the body (parsing of a tool's own lists); a string — why it cannot be sent. */
  finish?: (body: Record<string, unknown>) => Record<string, unknown> | string;
}

/** «0,5», «0.5», « 2 » → number; empty or not a number — null. */
export function parseNumber(text: string): number | null {
  const t = text.trim().replace(/\s+/g, "").replace(",", ".");
  if (!t || !/^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(t)) return null;
  const v = Number(t);
  return Number.isFinite(v) ? v : null;
}

/** 0.5 → «0,5»; at most 6 decimals, no trailing zeros. */
export const numberText = (v: number): string => String(Number(v.toFixed(6))).replace(".", ",");

/** «0,1–100 м», «0–1», «≥ 0». */
export function rangeText(p: Pick<ParamSpec, "min" | "max" | "unit">): string {
  const unit = p.unit ? ` ${p.unit}` : "";
  if (p.min !== undefined && p.max !== undefined) return `${numberText(p.min)}–${numberText(p.max)}${unit}`;
  if (p.min !== undefined) return `≥ ${numberText(p.min)}${unit}`;
  if (p.max !== undefined) return `≤ ${numberText(p.max)}${unit}`;
  return "";
}

/** «0,5; 1; 1,5» → [0.5, 1, 1.5]; a word that is not a number — null. */
export function parseList(text: string): number[] | null {
  const parts = text.split(/[;\s]+/).filter(Boolean);
  if (!parts.length) return null;
  const out: number[] = [];
  for (const part of parts) {
    const v = parseNumber(part);
    if (v === null) return null;
    out.push(v);
  }
  return out;
}

/** «ПОЛЕ1, ПОЛЕ2» → ["ПОЛЕ1", "ПОЛЕ2"] (names keep their case; repeats go). */
export function parseNames(text: string): string[] {
  const out: string[] = [];
  for (const n of text.split(/[,;]+/).map((x) => x.trim()).filter(Boolean)) if (!out.includes(n)) out.push(n);
  return out;
}

/** «7, 12» → [7, 12]; a word that is not a whole number — null. */
export function parseIdList(text: string): number[] | null {
  const parts = text.split(/[\s,;]+/).filter(Boolean);
  if (!parts.length) return null;
  const out: number[] = [];
  for (const p of parts) {
    if (!/^\d+$/.test(p)) return null;
    const id = Number(p);
    if (!out.includes(id)) out.push(id);
  }
  return out;
}

function inRange(p: ParamSpec, v: number): string | null {
  if ((p.min !== undefined && v < p.min) || (p.max !== undefined && v > p.max)) return `«${p.label}»: допустимо ${rangeText(p)}`;
  return null;
}

export type Checked = { ok: true; value: unknown } | { ok: false; error: string };

/** One value of the form → the value of the body; undefined value — left out. */
export function checkParam(p: ParamSpec, raw: string | boolean | undefined): Checked {
  if (p.kind === "boolean") return { ok: true, value: typeof raw === "boolean" ? raw : raw === undefined ? (p.default ?? false) : raw === "true" };
  const text = typeof raw === "string" ? raw.trim() : raw === undefined ? "" : String(raw);
  if (!text) return p.optional ? { ok: true, value: undefined } : { ok: false, error: `«${p.label}»: введите значение` };
  if (p.kind === "number" || p.kind === "integer") {
    const v = parseNumber(text);
    if (v === null) return { ok: false, error: `«${p.label}»: нужно число` };
    if (p.kind === "integer" && !Number.isInteger(v)) return { ok: false, error: `«${p.label}»: нужно целое число` };
    const bad = inRange(p, v);
    return bad ? { ok: false, error: bad } : { ok: true, value: v };
  }
  if (p.kind === "list") {
    const list = parseList(text);
    if (!list) return { ok: false, error: `«${p.label}»: числа через точку с запятой, например «0,5; 1; 1,5»` };
    for (const v of list) {
      const bad = inRange(p, v);
      if (bad) return { ok: false, error: bad };
    }
    return { ok: true, value: list };
  }
  if (p.kind === "choice") {
    if (p.choices && !p.choices.some((c) => c.value === text)) return { ok: false, error: `«${p.label}»: выберите из списка` };
    return { ok: true, value: text };
  }
  return { ok: true, value: text };
}

export interface CheckedParams {
  body: Record<string, unknown>;
  /** key → Russian error; empty — all good. */
  errors: Record<string, string>;
}

export const shown = (p: ParamSpec, values: FormValues): boolean => !p.when || p.when(values);

/** Every shown parameter checked; a value with an error is not put into the body. */
export function checkParams(params: readonly ParamSpec[], values: FormValues): CheckedParams {
  const body: Record<string, unknown> = {};
  const errors: Record<string, string> = {};
  for (const p of params) {
    if (!shown(p, values)) continue;
    const r = checkParam(p, values[p.key]);
    if (!r.ok) errors[p.key] = r.error;
    else if (r.value !== undefined && !p.formOnly) body[p.key] = r.value;
  }
  return { body, errors };
}

/** The values of a new form: the defaults of 8.2.2 as text. */
export function defaultValues(params: readonly ParamSpec[]): FormValues {
  const out: FormValues = {};
  for (const p of params) {
    if (p.kind === "boolean") out[p.key] = p.default === true;
    else out[p.key] = p.default === undefined || p.default === null ? "" : typeof p.default === "number" ? numberText(p.default) : String(p.default);
  }
  return out;
}

/** The values of the form from the arguments of a command (the window opened by the assistant, «Параметры…»). */
export function valuesFromArgs(params: readonly ParamSpec[], args: Record<string, unknown>): FormValues {
  const out = defaultValues(params);
  for (const p of params) {
    const v = args[p.key];
    if (v === undefined || v === null) continue;
    if (p.kind === "boolean") out[p.key] = v === true;
    else if (typeof v === "number") out[p.key] = numberText(v);
    else if (Array.isArray(v)) out[p.key] = v.map((x) => (typeof x === "number" ? numberText(x) : String(x))).join("; ");
    else out[p.key] = String(v);
  }
  return out;
}

/** The first error of a check as one line, or null. */
export function firstError(errors: Record<string, string>): string | null {
  const keys = Object.keys(errors);
  return keys.length ? errors[keys[0]] : null;
}

// ---- the source of a tool ----

export type DomainChoice = { kind: "none" } | { kind: "field"; table: string; field: string } | { kind: "wireframes"; ids: number[] };

export interface SourceState {
  setId: number | null;
  /** The key of the table (`table`). */
  table: string | null;
  /** `tables` of a tool that takes several. */
  tables: string[];
  /** `field` / `fields` — names of the fields of the table. */
  fields: string[];
  domain: DomainChoice;
  /** `wireframe_ids` of a tool that codes by wireframes. */
  wireframeIds: number[];
}

export const NO_SOURCE: SourceState = { setId: null, table: null, tables: [], fields: [], domain: { kind: "none" }, wireframeIds: [] };

/** The body of the source (ADR 0018 §10): `set_id`, `table` | `tables`, `field` | `fields`, `domain`, `wireframe_ids`;
 * a string — what is not chosen yet. */
export function sourceBody(needs: SourceNeeds, src: SourceState): Record<string, unknown> | string {
  if (src.setId === null) return "Выберите набор скважин";
  const body: Record<string, unknown> = { set_id: src.setId };
  if (needs.table === "many") {
    if (src.tables.length < 2) return "Отметьте не меньше двух таблиц";
    body.tables = [...src.tables];
  } else {
    if (!src.table) return "Выберите таблицу";
    body.table = src.table;
  }
  if (needs.fields === "one") {
    if (!src.fields.length) return "Выберите поле";
    body.field = src.fields[0];
  } else if (needs.fields === "many") {
    if (!src.fields.length) return "Отметьте поля";
    body.fields = [...src.fields];
  }
  if (needs.wireframes) {
    if (!src.wireframeIds.length) return "Отметьте каркасы";
    body.wireframe_ids = [...src.wireframeIds];
  }
  if (needs.domain && src.domain.kind === "field") body.domain = { table: src.domain.table, field: src.domain.field };
  if (needs.domain && src.domain.kind === "wireframes" && src.domain.ids.length) body.domain = { wireframe_ids: [...src.domain.ids] };
  return body;
}

export interface ToolBody {
  /** null — something is missing or wrong: `reason` says what. */
  body: Record<string, unknown> | null;
  errors: Record<string, string>;
  reason: string | null;
}

/** The whole body of a tool: the source, then the checked parameters, then the tool's own last step. */
export function toolBody(tool: ToolSpec, src: SourceState, values: FormValues): ToolBody {
  const s = sourceBody(tool.source, src);
  const { body, errors } = checkParams(tool.params, values);
  const bad = firstError(errors);
  if (typeof s === "string") return { body: null, errors, reason: s };
  if (bad) return { body: null, errors, reason: bad };
  const merged = { ...s, ...body };
  const done = tool.finish ? tool.finish(merged) : merged;
  return typeof done === "string" ? { body: null, errors, reason: done } : { body: done, errors, reason: null };
}

/** The source given by the arguments of a command (`set_id`, `table`, `tables`, `field`, `fields`, `domain`,
 * `domain_table`, `domain_wireframes`, `wireframe_ids`; lists — through commas). Tables stay as given. */
export function sourceFromArgs(args: Record<string, unknown>): SourceState {
  const text = (k: string): string | null => (typeof args[k] === "string" && (args[k] as string).trim() ? (args[k] as string).trim() : null);
  const table = text("table");
  const fields = text("fields") ? parseNames(text("fields")!) : text("field") ? [text("field")!] : [];
  const wf = text("domain_wireframes") ? (parseIdList(text("domain_wireframes")!) ?? []) : [];
  const domain: DomainChoice = text("domain") ? { kind: "field", table: text("domain_table") ?? table ?? "", field: text("domain")! } : wf.length ? { kind: "wireframes", ids: wf } : { kind: "none" };
  return {
    setId: typeof args.set_id === "number" ? args.set_id : null,
    table,
    tables: text("tables") ? parseNames(text("tables")!) : [],
    fields,
    domain,
    wireframeIds: text("wireframe_ids") ? (parseIdList(text("wireframe_ids")!) ?? []) : [],
  };
}
