/**
 * What the commands `filter*`, the section «Фильтры» of the tree and the window «Фильтры» know (ТЗ 7.35 Д-02; ADR 0023
 * §8): the library of the project (read when a project opens, kept by `objects.changed {kind: "filter"}`), the row
 * chosen in the tree, and the edit of the open window — the name, the note, the tree of the rules as the window edits it
 * (a `Draft`) or its text of 8.7.4, the model «Проверить на модели» asks, the consumers («Где используется»), the last
 * answer of the check. One window «Фильтры» per project. No React.
 */
import { Store } from "../../../shell/store";
import type { Filter, FilterRow, FilterUse } from "./api";
import { checkDraft, EMPTY_DRAFT, ruleToDraft, ruleToText, textToRule, type Draft, type Rule, type RuleIssue } from "./constructor";
import type { ExprIssue } from "./expr";

export const FILTERS_WINDOW = "filters";
export const NEW_NAME = "Новый фильтр";

export type EditMode = "builder" | "text";

/** The last answer of «Проверить на модели»: a result, a notice (the check is not ready yet) or a failure. */
export interface TestNote {
  text: string;
  kind: "ok" | "notice" | "error";
}

export interface FilterEdit {
  /** the filter edited; null — a new one */
  filterId: number | null;
  /** the version read or saved (`expected_version`); null — a new filter */
  version: number | null;
  name: string;
  note: string;
  mode: EditMode;
  draft: Draft;
  /** the text of the mode «Текст» (what is typed; «Конструктор» fills it from the tree) */
  text: string;
  /** the place of a wrong text */
  textIssue: ExprIssue | null;
  /** the first wrong thing of the tree (ours or the core's) — its node is marked */
  issue: RuleIssue | null;
  dirty: boolean;
  /** the block model of «Проверить на модели» */
  modelId: number | null;
  test: TestNote | null;
  /** «Где используется» of the stored filter; null — not read */
  usage: FilterUse[] | null;
  /** the wireframes of the stored filter that are gone (an archive without them) */
  lost: string[];
}

export interface FiltersState {
  rows: FilterRow[];
  loaded: boolean;
  selected: number | null;
  edit: FilterEdit | null;
}

export const filtersStore = new Store<FiltersState>({ rows: [], loaded: false, selected: null, edit: null });

export const filtersByName = (rows: readonly FilterRow[]): FilterRow[] => [...rows].sort((a, b) => a.name.localeCompare(b.name, "ru") || a.id - b.id);
export const findFilter = (id: number): FilterRow | undefined => filtersStore.get().rows.find((f) => f.id === id);

export const setRows = (rows: FilterRow[]): void => filtersStore.set((s) => ({ rows, loaded: true, selected: s.selected !== null && rows.some((r) => r.id === s.selected) ? s.selected : null }));
export const upsertRow = (row: FilterRow): void => filtersStore.set((s) => ({ rows: [...s.rows.filter((x) => x.id !== row.id), row] }));
export const dropRow = (id: number): void => filtersStore.set((s) => ({ rows: s.rows.filter((x) => x.id !== id), selected: s.selected === id ? null : s.selected, edit: s.edit?.filterId === id ? null : s.edit }));
export const resetFilters = (): void => filtersStore.set({ rows: [], loaded: false, selected: null, edit: null });
export const selectFilter = (id: number | null): void => filtersStore.set({ selected: id });

export function newEdit(over: Partial<FilterEdit> = {}): FilterEdit {
  return { filterId: null, version: null, name: NEW_NAME, note: "", mode: "builder", draft: EMPTY_DRAFT, text: "", textIssue: null, issue: null, dirty: false, modelId: null, test: null, usage: null, lost: [], ...over };
}

/** The edit of a stored filter: its tree in the constructor, its text (if it has one) for the mode «Текст». */
export function editOf(f: Filter, keep: Partial<Pick<FilterEdit, "mode" | "modelId">> = {}): FilterEdit {
  const lost = f.wireframes.filter((w) => !w.available).map((w) => (w.name ? w.name : `№${w.id}`));
  const mode = keep.mode === "text" && f.text !== null ? "text" : "builder";
  return newEdit({ filterId: f.id, version: f.version, name: f.name, note: f.note ?? "", mode, draft: ruleToDraft(f.rules), text: f.text ?? "", modelId: keep.modelId ?? null, lost });
}

export const currentEdit = (): FilterEdit | null => filtersStore.get().edit;

export function setEdit(patch: Partial<FilterEdit> | ((e: FilterEdit) => Partial<FilterEdit>)): FilterEdit {
  const cur = filtersStore.get().edit ?? newEdit();
  const next = { ...cur, ...(typeof patch === "function" ? patch(cur) : patch) };
  filtersStore.set({ edit: next });
  return next;
}

export const forgetEdit = (): void => filtersStore.set({ edit: null });

/** The window edit of a filter, if the window shows it. */
export const isEditing = (id: number): boolean => filtersStore.get().edit?.filterId === id;

/** The tree of the window as the rules of the core: the draft of the constructor or the text of the mode «Текст». */
export function rulesOfEdit(e: FilterEdit): { ok: true; rule: Rule } | { ok: false; issue: RuleIssue } {
  if (e.mode === "builder") return checkDraft(e.draft);
  const t = textToRule(e.text);
  return t.ok ? { ok: true, rule: t.rule } : { ok: false, issue: { param: "rules", message: t.issue.message, hint: t.issue.hint, expr: t.issue } };
}

/** Switches the mode: «Текст» needs a complete tree without wireframes and surfaces; «Конструктор» — a text that parses. */
export function switchMode(e: FilterEdit, mode: EditMode): Partial<FilterEdit> | { refusal: string } {
  if (mode === e.mode) return {};
  if (mode === "text") {
    const checked = checkDraft(e.draft);
    if (!checked.ok) return { refusal: `Текст появится, когда правила будут заполнены: ${checked.issue.message}` };
    const text = ruleToText(checked.rule);
    if (text === null) return { refusal: "В фильтре есть каркас или поверхность — у него нет записи в 8.7.4. Правьте фильтр в конструкторе." };
    return { mode, text, textIssue: null, issue: null };
  }
  const t = textToRule(e.text);
  if (!t.ok) return { refusal: `Текст не разобран, конструктор не обновлён: ${t.issue.message}` };
  return { mode, draft: ruleToDraft(t.rule), textIssue: null, issue: null };
}

/** The body of the window as a `POST` / `PUT` of the core; the first wrong thing — as a text (the name first). */
export function bodyOfEdit(e: FilterEdit): { ok: true; body: { name: string; rules: Rule; note: string | null } } | { ok: false; error: string; issue: RuleIssue | null } {
  const name = e.name.trim();
  if (!name) return { ok: false, error: "У фильтра нет имени. Введите имя, например «Руда выше борта».", issue: null };
  const r = rulesOfEdit(e);
  if (!r.ok) return { ok: false, error: `${r.issue.message} ${r.issue.hint}`.trim(), issue: r.issue };
  return { ok: true, body: { name, rules: r.rule, note: e.note.trim() ? e.note.trim() : null } };
}
