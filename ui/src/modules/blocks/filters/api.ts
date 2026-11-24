/**
 * The client of `/api/v1/filters` — the library of filters (ТЗ 7.35 Д-02; ADR 0023 §8; docs/progress/e17-api.md §13;
 * core — `blocks/filters/routes.py`). The forms are those of the core's answers (a run of the core through TestClient:
 * `core/tests/data/blocks/filters_window_answers.json`, `core/tests/test_filters_window_answers.py`):
 *
 * - the list `GET /filters` → `{filters: [{id, name, folder, version, text, title, fields, note, created_at,
 *   updated_at}]}` (no `rules`); `GET /filters/{id}` → the same with `rules` and `wireframes: [{id, name, available}]`;
 * - `POST /filters {name, rules | expr, note?, folder?}` and `PUT /filters/{id} {expected_version, name?, rules | expr?,
 *   note?}` → the whole filter (409 `name_taken`; 409 `version_conflict {version}` — with another version stored and
 *   without one; 422 `filter_rules {param}`, `expr_syntax {param, pos, line, column, char, reason}`,
 *   `wireframe_not_found {param}`; 423 for a project open for reading);
 * - `DELETE /filters/{id}` → `{deleted}`; used — 422 `filter_in_use {usage}`; `GET /filters/{id}/usage` →
 *   `{usage: [{kind: "object" | "view", id, name, role}]}`;
 * - `POST /filters/{id}/test {model_id}` and `POST /filters/test {model_id, rules | expr}` → `{passed, total, weight}`
 *   (422 `not_ready` until the restrictor of 17.8; 422 `filter_field_missing {field, model}`; 404
 *   `block_model_not_found`).
 *
 * An unknown number stays `null`, never 0. No React.
 */
import { api, CoreError } from "../../../shell/api";
import type { Rule } from "./constructor";

export const FILTER_API = "/filters";

/** `text` is null when a wireframe or a surface is in the tree (no record in 8.7.4). */
export interface FilterRow {
  id: number;
  name: string;
  folder: string | null;
  version: number;
  text: string | null;
  title: string;
  fields: string[];
  note: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface FilterWireframe {
  id: number;
  name: string | null;
  /** false — the object is gone (an archive without it): its share is 0, a warning */
  available: boolean;
}

export interface Filter extends FilterRow {
  rules: Rule;
  wireframes: FilterWireframe[];
}

/** A consumer of a filter («Где используется»): an object of a later step, or a view of a block model. */
export interface FilterUse {
  kind: "object" | "view";
  id: number;
  name: string;
  role: string;
}

export interface TestResult {
  passed: number;
  total: number;
  /** Σf of the blocks (the shares of a wireframe or a surface) */
  weight: number;
}

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const str = (v: unknown): string | null => (typeof v === "string" ? v : null);

export function readRow(raw: unknown): FilterRow {
  const f = obj(raw);
  return {
    id: isNum(f.id) ? f.id : 0,
    name: str(f.name) ?? "",
    folder: str(f.folder),
    version: isNum(f.version) ? f.version : 0,
    text: str(f.text),
    title: str(f.title) ?? "",
    fields: Array.isArray(f.fields) ? f.fields.filter((x): x is string => typeof x === "string") : [],
    note: str(f.note),
    created_at: str(f.created_at),
    updated_at: str(f.updated_at),
  };
}

export function readFilter(raw: unknown): Filter {
  const f = obj(raw);
  return {
    ...readRow(raw),
    rules: (f.rules && typeof f.rules === "object" ? f.rules : { expr: "" }) as Rule,
    wireframes: Array.isArray(f.wireframes) ? f.wireframes.map((w) => obj(w)).map((w) => ({ id: isNum(w.id) ? w.id : 0, name: str(w.name), available: w.available !== false })) : [],
  };
}

export function readUsage(raw: unknown): FilterUse[] {
  const list = obj(raw).usage;
  return Array.isArray(list)
    ? list
        .map((u) => obj(u))
        .map((u) => ({ kind: u.kind === "view" ? ("view" as const) : ("object" as const), id: isNum(u.id) ? u.id : 0, name: str(u.name) ?? "", role: str(u.role) ?? "" }))
    : [];
}

export function readTest(raw: unknown): TestResult | null {
  const t = obj(raw);
  return isNum(t.passed) && isNum(t.total) && isNum(t.weight) ? { passed: t.passed, total: t.total, weight: t.weight } : null;
}

/** The body of `POST` / `PUT`: the tree or a text of 8.7.4 (exactly one of them). */
export interface FilterBody {
  name?: string;
  rules?: Rule;
  expr?: string;
  note?: string | null;
  folder?: string | null;
}

export const loadFilters = async (): Promise<FilterRow[]> => {
  const raw = obj(await api.get(FILTER_API)).filters;
  return Array.isArray(raw) ? raw.map(readRow) : [];
};
export const loadFilter = async (id: number): Promise<Filter> => readFilter(await api.get(`${FILTER_API}/${id}`));
export const createFilter = async (body: FilterBody): Promise<Filter> => readFilter(await api.post(FILTER_API, body));
/** The fields of `body` over the version `expectedVersion` (the one read); another one stored — 409 `version_conflict`. */
export const updateFilter = async (id: number, body: FilterBody, expectedVersion: number): Promise<Filter> => readFilter(await api.put(`${FILTER_API}/${id}`, { ...body, expected_version: expectedVersion }));
export const deleteFilter = (id: number): Promise<{ deleted: number }> => api.del(`${FILTER_API}/${id}`);
export const filterUsage = async (id: number): Promise<FilterUse[]> => readUsage(await api.get(`${FILTER_API}/${id}/usage`));
/** «Проверить на модели» of a stored filter. */
export const testStored = async (id: number, modelId: number): Promise<TestResult | null> => readTest(await api.post(`${FILTER_API}/${id}/test`, { model_id: modelId }));
/** «Проверить на модели» of a filter not saved yet (the tree or the text of the window). */
export const testInline = async (modelId: number, body: { rules?: Rule; expr?: string }): Promise<TestResult | null> => readTest(await api.post(`${FILTER_API}/test`, { model_id: modelId, ...body }));

/** The consumers of a 422 `filter_in_use` (its body `usage`); [] — another error. */
export function usageOfError(e: unknown): FilterUse[] {
  return e instanceof CoreError && e.code === "filter_in_use" ? readUsage(e.body ?? {}) : [];
}

const ROLE_TEXT: Record<string, string> = { filter: "применяет фильтр", block_style: "показ модели в виде проекта" };

/** «Поле AU2 (объект, применяет фильтр)» — one consumer as a line. */
export const useText = (u: FilterUse): string => `${u.name} (${u.kind === "view" ? "вид" : "объект"}, ${ROLE_TEXT[u.role] ?? u.role})`;

/** «Поле AU2», «Вид проекта · Модель Б» — the consumers as a line of text (up to `max`, then «и ещё N»). */
export function usageText(list: readonly FilterUse[], max = 5): string {
  const names = list.slice(0, max).map((u) => `«${u.name}»`);
  return list.length > max ? `${names.join(", ")} и ещё ${list.length - max}` : names.join(", ");
}

const n = (v: number) => v.toLocaleString("ru-RU", { maximumFractionDigits: 3 });

/** «Прошло 6 из 12 блоков» (+ «вес 5,5» when a wireframe or a surface shares blocks). */
export function testText(model: string, r: TestResult): string {
  const share = Math.abs(r.weight - r.passed) > 1e-9 ? `, вес по долям ${n(r.weight)}` : "";
  return `Модель «${model}»: прошло ${n(r.passed)} из ${n(r.total)} блоков${share}`;
}

/** What «Проверить на модели» says of a refusal: its words and hint, in one text (`not_ready` — as a notice, not a failure;
 * `filter_field_missing` and the others — the words of the core, which name the field and the model). */
export function testRefusal(e: unknown): { text: string; notReady: boolean } {
  if (e instanceof CoreError) {
    if (e.code === "not_ready") return { text: `${e.message} ${e.hint}`.trim(), notReady: true };
    return { text: e.full, notReady: false };
  }
  return { text: e instanceof Error ? e.message : String(e), notReady: false };
}
