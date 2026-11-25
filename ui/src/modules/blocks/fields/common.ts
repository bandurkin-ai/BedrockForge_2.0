/**
 * What the forms of the calculator, the density and the categories share (ТЗ 8.7.4, 8.7.5; ADR 0023 §7, §9): the place
 * of a wrong thing in the form (`Issue` — the words and the hint of the core, or ours, never a bare code), the
 * restrictor (a filter of the library and the quick one — `RuleEditor` of the window «Фильтры»), the names of fields,
 * the limits of the core, and where a refusal of the core belongs in a form (its `param`). No React.
 */
import { checkDraft, EMPTY_DRAFT, ruleToDraft, type Draft, type Rule } from "../filters/constructor";
import type { Refusal } from "./api";

export const MAX_NAME = 100;
export const MAX_UNIT = 40;
export const MAX_DESCRIPTION = 500;
export const MAX_EXPR = 4000;
/** The name the core keeps for the index of the rows. */
export const RESERVED = "idx";

export type Args = Record<string, unknown>;
export type Tab = "calc" | "density" | "classify" | "props";

/** The first wrong thing: the tab and the place in its form, the words, the hint (as `{code, message, hint}` of the core). */
export interface Issue {
  tab: Tab;
  place: string;
  /** the full `param` of the core (`table.values.1.density`, `rules.2.expr`): rows of a list are marked by it */
  param: string | null;
  message: string;
  hint: string;
}

export const issue = (tab: Tab, place: string, message: string, hint: string, param: string | null = null): Issue => ({ tab, place, param, message, hint });

/** A key of the arguments is given: not absent, not null, not an empty text. */
export const given = (a: Args, k: string): boolean => a[k] !== undefined && a[k] !== null && (typeof a[k] !== "string" || (a[k] as string).trim() !== "");

export const isInt = (v: unknown): v is number => typeof v === "number" && Number.isInteger(v);

/** A JSON value of the arguments (a text of JSON or the value itself). */
export function jsonArg(v: unknown): { ok: true; value: unknown } | { ok: false } {
  if (typeof v !== "string") return { ok: true, value: v };
  try {
    return { ok: true, value: JSON.parse(v) as unknown };
  } catch {
    return { ok: false };
  }
}

/** The name of a new field: 1…100 characters, not the name of the index. */
export function nameIssue(tab: Tab, place: string, name: string, what = "Имя поля"): Issue | null {
  if (!name.trim()) return issue(tab, place, `${what} не задано.`, "Введите имя, например AU2.");
  if (name.trim().length > MAX_NAME) return issue(tab, place, `${what} длиннее ${MAX_NAME} знаков.`, "Сократите имя.");
  if (name.trim().toLowerCase() === RESERVED) return issue(tab, place, `Имя «${RESERVED}» занято: так называется номер строки модели.`, "Выберите другое имя.");
  return null;
}

/** A free text of the limit (the unit, the description). */
export function limitIssue(tab: Tab, place: string, text: string, limit: number, what: string): Issue | null {
  return text.length > limit ? issue(tab, place, `${what}: не больше ${limit} знаков.`, "Сократите текст.") : null;
}

// ---- the restrictor ----

export interface RestrictForm {
  /** a filter of the library; null — none */
  filterId: number | null;
  restrictOn: boolean;
  restrict: Draft;
}

export const EMPTY_RESTRICT: RestrictForm = { filterId: null, restrictOn: false, restrict: EMPTY_DRAFT };

/** The arguments of the restrictor of a form (`filter_id`, `restrict` as JSON); a wrong tree — the first wrong thing. */
export function restrictArgs(tab: Tab, r: RestrictForm): { ok: true; args: Args } | { ok: false; issue: Issue } {
  const args: Args = {};
  if (r.filterId !== null) args.filter_id = r.filterId;
  if (r.restrictOn) {
    const c = checkDraft(r.restrict);
    if (!c.ok) return { ok: false, issue: issue(tab, "restrict", c.issue.message, c.issue.hint) };
    args.restrict = JSON.stringify(c.rule);
  }
  return { ok: true, args };
}

/** The restrictor of the arguments of a command (the body keys `filter_id`, `restrict`), every wrong thing told here. */
export function restrictOfArgs(tab: Tab, a: Args): { ok: true; body: { filter_id?: number; restrict?: Rule } } | { ok: false; issue: Issue } {
  const body: { filter_id?: number; restrict?: Rule } = {};
  if (given(a, "filter_id")) {
    if (!isInt(a.filter_id) || a.filter_id < 1) return { ok: false, issue: issue(tab, "filter", "Номер фильтра — целое число.", "Номера фильтров — в разделе «Фильтры» дерева.") };
    body.filter_id = a.filter_id;
  }
  if (given(a, "restrict")) {
    const j = jsonArg(a.restrict);
    if (!j.ok || !j.value || typeof j.value !== "object" || Array.isArray(j.value)) {
      return { ok: false, issue: issue(tab, "restrict", "Ограничитель — не дерево правил в JSON.", 'Пример: {"cond": {"field": "DOM", "op": "=", "value": "Руда"}}.') };
    }
    body.restrict = j.value as Rule;
  }
  return { ok: true, body };
}

/** The form of the restrictor of the arguments of a call (the window then shows what the core got). */
export function restrictFormOfArgs(a: Args, keep: RestrictForm): RestrictForm {
  const out = { ...keep };
  if (given(a, "filter_id") && typeof a.filter_id === "number") out.filterId = a.filter_id;
  if (given(a, "restrict")) {
    const j = jsonArg(a.restrict);
    if (j.ok && j.value && typeof j.value === "object" && !Array.isArray(j.value)) Object.assign(out, { restrictOn: true, restrict: ruleToDraft(j.value as Rule) });
  }
  return out;
}

// ---- a refusal of the core in its place in the form ----

/** Where a `param` of the body belongs in the forms (the head of the path; a few keys are named in the form differently). */
const PLACE: Record<string, string> = { filter_id: "filter", expected_version: "general", src_field: "src_field", table: "table", id: "scheme_id", name: "scheme_name" };

/** The place of a refusal of the core: by its `param`, or by the code when it names none. `field` is the name asked for. */
export function issueOfRefusal(tab: Tab, r: Refusal, restrictOn = false, filterId: number | null = null): Issue {
  const head = (r.param ?? "").split(".")[0];
  let place = PLACE[head] ?? (head || "general");
  if (r.code === "block_model_not_found") place = "model";
  else if (r.code === "filter_not_found") place = "filter";
  else if (r.code === "filter_field_missing") place = restrictOn && filterId === null ? "restrict" : "filter";
  else if (r.code === "scheme_not_found") place = "scheme";
  else if (r.code === "scheme_incomplete") place = "rules";
  else if (["version_conflict", "project_read_only", "density_invalid", "not_ready", "bad_answer"].includes(r.code)) place = "general";
  return issue(tab, place, r.message, r.hint, r.param);
}
