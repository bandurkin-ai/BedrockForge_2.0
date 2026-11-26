/**
 * What the planned holes know (ТЗ 7.35 Д-34 ч. 2; ADR 0026 решения 28, 29): the list of the project with the paths (the tree and the 3D layer read
 * it; read when the project opens and on `implicit.changed {kind: "planned_hole"}`), the view (the holes hidden in 3D, the hole chosen), the window
 * «Проектная скважина» (the form, the version, the words under a field, «сохранено») and what the window asked the core about the stored hole (the
 * forecast by the bodies with the nearest holes; «План / факт»). A read that a newer one overtook is dropped. The forecast and the comparison are made
 * on the STORED hole (the core reads it): the window asks to save first. No React.
 */
import { CoreError } from "../../shell/api";
import { Store } from "../../shell/store";
import type { Issue } from "../estimation/formBase";
import { projectStore } from "../projects/state";
import { comparePlanned, createPlanned, evaluatePlanned, getPlanned, listPlanned, refusalOf, updatePlanned, type CompareAnswer, type EvaluateAnswer, type PlannedBody, type PlannedRow } from "./plannedApi";
import { bodyOf, EMPTY_FORM, formOfRow, freeName, sameForm, type PlannedForm } from "./plannedModel";

export const PLANNED_WINDOW = "planned-hole";
export const PLANNED_TITLE = "Проектная скважина";

// ---- the list of the project ----

export interface PlannedList {
  rows: PlannedRow[];
  loaded: boolean;
  error: string | null;
}

export const plannedStore = new Store<PlannedList>({ rows: [], loaded: false, error: null });

let listSeq = 0;
let uidOfList: string | null = null;

/** Reads the list with the paths; a read of another project, or one that a newer read overtook, is dropped. */
export async function loadPlanned(): Promise<void> {
  const mine = ++listSeq;
  const uid = projectStore.get().current?.uid ?? null;
  uidOfList = uid;
  if (!uid) return clearPlanned();
  try {
    const rows = await listPlanned(true);
    if (mine !== listSeq || uid !== (projectStore.get().current?.uid ?? null)) return;
    plannedStore.set({ rows: rows.sort((a, b) => a.id - b.id), loaded: true, error: null });
  } catch (e) {
    if (mine !== listSeq) return;
    const r = refusalOf(e);
    plannedStore.set({ loaded: true, error: `${r.message} ${r.hint}`.trim() });
  }
}

export function clearPlanned(): void {
  listSeq++;
  uidOfList = null;
  plannedStore.set({ rows: [], loaded: false, error: null });
}

export const plannedLoadedFor = (): string | null => uidOfList;
export const rowById = (id: number): PlannedRow | undefined => plannedStore.get().rows.find((r) => r.id === id);

/** An event `implicit.changed` of a planned hole (the models publish the same event with other kinds). */
export function isPlannedEvent(data: string): boolean {
  try {
    return (JSON.parse(data) as { kind?: unknown }).kind === "planned_hole";
  } catch {
    return false; // an event without a body is not ours
  }
}

// ---- the view ----

export interface PlannedView {
  /** the holes switched off in 3D (the others are drawn) */
  hidden: number[];
  /** the hole chosen in the tree: drawn thicker, the window of the command without `id` opens it */
  chosen: number | null;
}

export const plannedViewStore = new Store<PlannedView>({ hidden: [], chosen: null });

export const isShown = (v: PlannedView, id: number): boolean => !v.hidden.includes(id);

export function setShown(id: number, on: boolean): void {
  plannedViewStore.set((v) => ({ hidden: on ? v.hidden.filter((h) => h !== id) : v.hidden.includes(id) ? v.hidden : [...v.hidden, id] }));
}

export const choose = (id: number | null): void => plannedViewStore.set({ chosen: id });

/** A hole that is gone leaves the view. */
export function followRows(): void {
  const st = plannedStore.get();
  if (!st.loaded) return;
  const ids = new Set(st.rows.map((r) => r.id));
  const v = plannedViewStore.get();
  const hidden = v.hidden.filter((h) => ids.has(h));
  const chosen = v.chosen !== null && ids.has(v.chosen) ? v.chosen : null;
  if (hidden.length !== v.hidden.length || chosen !== v.chosen) plannedViewStore.set({ hidden, chosen });
}

// ---- the window ----

export type Tab = "params" | "forecast" | "compare";
export const TAB_TITLES: Record<Tab, string> = { params: "Параметры", forecast: "Прогноз", compare: "План / факт" };

export interface PlannedWin {
  tab: Tab;
  /** the stored hole of the window; null — a new one */
  id: number | null;
  version: number | null;
  form: PlannedForm;
  /** the form as the stored hole gave it; null for a new one */
  base: PlannedForm | null;
  dirty: boolean;
  saving: boolean;
  /** the first wrong thing: with `param` it stands under the field, with `reread` — «Перечитать» */
  issue: Issue | null;
  saved: string | null;
  /** the stored hole was deleted meanwhile: «Сохранить» makes a new one */
  gone: boolean;
}

const freshWin = (): PlannedWin => ({ tab: "params", id: null, version: null, form: EMPTY_FORM, base: null, dirty: false, saving: false, issue: null, saved: null, gone: false });

export const plannedWin = new Store<PlannedWin>(freshWin());

const dirtyOf = (form: PlannedForm, base: PlannedForm | null): boolean => !sameForm(form, base ?? EMPTY_FORM);

export const setTab = (tab: Tab): void => plannedWin.set({ tab });

export function startNew(patch: Partial<PlannedForm> = {}): void {
  const name = freeName(plannedStore.get().rows.map((r) => r.name));
  const form = { ...EMPTY_FORM, name, ...patch };
  plannedWin.set({ ...freshWin(), form, dirty: Object.keys(patch).length > 0 });
  resetChecks();
}

export function startEdit(row: PlannedRow): void {
  const form = formOfRow(row);
  const cur = plannedWin.get();
  plannedWin.set({ ...freshWin(), tab: cur.id === row.id ? cur.tab : "params", id: row.id, version: row.version, form, base: form });
  resetChecks();
}

/** A field of the form changed: the words under it go, the saved line too. */
export function setForm(patch: Partial<PlannedForm>): void {
  const s = plannedWin.get();
  const form = { ...s.form, ...patch };
  plannedWin.set({ form, dirty: dirtyOf(form, s.base), issue: s.issue?.reread ? s.issue : null, saved: null });
}

export function setXyz(which: "collar" | "target", i: number, value: string): void {
  const t = [...plannedWin.get().form[which]] as PlannedForm["collar"];
  t[i] = value;
  setForm({ [which]: t });
}

export function resetWin(): void {
  plannedWin.set(freshWin());
  resetChecks();
}

const issueOf = (e: unknown): Issue => {
  const r = refusalOf(e);
  const conflict = e instanceof CoreError && e.code === "version_conflict";
  return { param: r.param, code: r.code, message: r.message, hint: r.hint, ...(conflict ? { reread: true } : {}) };
};

/** The hole written is the one of the window; the answers about the old one go. */
function adopt(row: PlannedRow, saved: string): void {
  const form = formOfRow(row);
  plannedWin.set({ id: row.id, version: row.version, form, base: form, dirty: false, saving: false, issue: null, saved, gone: false });
  resetChecks();
}

/** «Сохранить» (`asNew` — a new hole next to the open one). The words of a refusal stand under the field (`issue`). */
export async function saveWin(opts: { asNew?: boolean } = {}): Promise<PlannedRow> {
  const s = plannedWin.get();
  const built = bodyOf(s.form);
  if (!built.ok) {
    plannedWin.set({ issue: built.issue });
    throw new CoreError(0, built.issue.code, built.issue.message, built.issue.hint);
  }
  plannedWin.set({ saving: true, issue: null });
  try {
    const create = s.id === null || opts.asNew === true || s.gone;
    const row = create ? await createPlanned(withName(built.body)) : await updatePlanned(s.id!, s.version ?? 0, built.body);
    await loadPlanned();
    adopt(row, create ? `Скважина «${row.name}» записана в проект.` : `Сохранено: версия ${row.version}.`);
    return row;
  } catch (e) {
    plannedWin.set({ saving: false, issue: issueOf(e) });
    throw e;
  }
}

/** A new hole without a name gets the next free one, as the form shows it (the core has its own default too). */
const withName = (body: PlannedBody): PlannedBody => (body.name ? body : { ...body, name: freeName(plannedStore.get().rows.map((r) => r.name)) });

/** «Перечитать»: the window takes the stored hole as it is now (the changes of the form are dropped). */
export async function rereadWin(): Promise<void> {
  const id = plannedWin.get().id;
  if (id === null) return;
  try {
    const row = await getPlanned(id);
    await loadPlanned();
    startEdit(row);
  } catch (e) {
    const gone = e instanceof CoreError && e.code === "planned_hole_not_found";
    plannedWin.set({ issue: issueOf(e), gone });
  }
}

/** The hole of the window left the list (deleted elsewhere): the form stays, «Сохранить» makes a new hole. */
export function followWindow(): void {
  const st = plannedStore.get();
  const s = plannedWin.get();
  if (!st.loaded || s.id === null || s.gone) return;
  const row = st.rows.find((r) => r.id === s.id);
  if (!row) plannedWin.set({ gone: true, saved: null, issue: { param: null, code: "planned_hole_not_found", message: "Проектная скважина удалена в другом окне.", hint: "«Сохранить» запишет её заново." } });
  else if (row.version !== s.version && !s.dirty && !s.saving) startEdit(row); // changed elsewhere, nothing typed here: the window follows
  else if (row.version !== s.version && !s.saving) plannedWin.set({ issue: { param: null, code: "version_conflict", message: "Скважина изменена в другом окне.", hint: "Перечитайте её или сохраните свою под новым именем.", reread: true } });
}

// ---- what the stored hole is asked ----

export type SourceKind = "none" | "model" | "meshes";

export interface Source {
  kind: SourceKind;
  modelId: number | null;
  meshIds: number[];
}

export interface Run<T> {
  phase: "idle" | "loading" | "ok" | "error";
  answer: T | null;
  error: string | null;
}

export interface PlannedCheck {
  source: Source;
  /** the set of the forecast and of «План / факт»: undefined — the set of the form (the one saved with the hole) */
  setId: number | null | undefined;
  /** the actual hole of «План / факт» (its name in the set) */
  hole: string | null;
  evaluate: Run<EvaluateAnswer>;
  compare: Run<CompareAnswer>;
}

const idle = <T,>(): Run<T> => ({ phase: "idle", answer: null, error: null });
const freshCheck = (): PlannedCheck => ({ source: { kind: "none", modelId: null, meshIds: [] }, setId: undefined, hole: null, evaluate: idle(), compare: idle() });

export const plannedCheck = new Store<PlannedCheck>(freshCheck());

/** The answers go (the hole changed, another one was opened); the choice of the source and of the hole stays. */
export function resetChecks(): void {
  const c = plannedCheck.get();
  evalSeq++;
  cmpSeq++;
  plannedCheck.set({ ...c, evaluate: idle(), compare: idle() });
}

export function resetAll(): void {
  evalSeq++;
  cmpSeq++;
  plannedCheck.set(freshCheck());
}

export const setSource = (patch: Partial<Source>): void => plannedCheck.set((c) => ({ source: { ...c.source, ...patch }, evaluate: idle() }));
export const setHole = (hole: string | null): void => plannedCheck.set({ hole, compare: idle() });

/** The set the checks use: the one chosen for them, else the one of the form. */
export const checkSetOf = (c: PlannedCheck, form: PlannedForm): number | null => (c.setId !== undefined ? c.setId : form.setId);

/** Another set for the checks (the hole of «План / факт» is of the old one: it goes, the answers too). */
export function setCheckSet(setId: number | null): void {
  evalSeq++;
  cmpSeq++;
  plannedCheck.set({ setId, hole: null, evaluate: idle(), compare: idle() });
}

/** The words why the stored hole cannot be asked now, or null. */
export function askReason(): string | null {
  const s = plannedWin.get();
  if (s.id === null || s.gone) return "Сначала сохраните скважину: прогноз считается по сохранённой";
  // the set is sent with the request: a form that differs from the stored hole in the set alone is asked as it is
  const geometry = s.base !== null && !sameForm({ ...s.form, setId: s.base.setId }, s.base);
  if (geometry) return "В форме есть несохранённые изменения: сохраните скважину — прогноз считается по сохранённой";
  return null;
}

export function evaluateBody(source: Source, setId: number | null): PlannedBody | string {
  const body: PlannedBody = {};
  if (source.kind === "model") {
    if (source.modelId === null) return "Выберите неявную модель.";
    body.model_id = source.modelId;
  } else if (source.kind === "meshes") {
    if (!source.meshIds.length) return "Отметьте хотя бы один замкнутый каркас.";
    body.mesh_ids = source.meshIds;
  }
  if (setId !== null) body.set_id = setId;
  if (source.kind === "none" && setId === null) return "Выберите модель или каркасы для прогноза либо набор скважин для ближайших.";
  return body;
}

let evalSeq = 0;
let cmpSeq = 0;

/** «Прогноз»: the intervals by the bodies and the nearest holes of the set at the target (or the end). */
export async function runEvaluate(): Promise<EvaluateAnswer | null> {
  const w = plannedWin.get();
  const reason = askReason();
  if (reason || w.id === null) {
    plannedCheck.set((c) => ({ evaluate: { ...c.evaluate, phase: "error", error: reason ?? "Скважина не сохранена." } }));
    return null;
  }
  const body = evaluateBody(plannedCheck.get().source, checkSetOf(plannedCheck.get(), w.form));
  if (typeof body === "string") {
    plannedCheck.set((c) => ({ evaluate: { ...c.evaluate, phase: "error", error: body } }));
    return null;
  }
  const mine = ++evalSeq;
  const id = w.id;
  plannedCheck.set((c) => ({ evaluate: { phase: "loading", answer: c.evaluate.answer, error: null } }));
  try {
    const answer = await evaluatePlanned(id, body);
    if (mine !== evalSeq) return null;
    plannedCheck.set({ evaluate: { phase: "ok", answer, error: null } });
    return answer;
  } catch (e) {
    if (mine !== evalSeq) return null;
    const r = refusalOf(e);
    plannedCheck.set({ evaluate: { phase: "error", answer: null, error: `${r.message} ${r.hint}`.trim() } });
    return null;
  }
}

/** «Сравнить»: the actual hole of the set against the planned one, every 1 m. */
export async function runCompare(): Promise<CompareAnswer | null> {
  const w = plannedWin.get();
  const hole = plannedCheck.get().hole;
  const setId = checkSetOf(plannedCheck.get(), w.form);
  const reason = askReason() ?? (setId === null ? "Выберите набор скважин." : !hole ? "Выберите фактическую скважину." : null);
  if (reason || w.id === null || setId === null || !hole) {
    plannedCheck.set((c) => ({ compare: { ...c.compare, phase: "error", error: reason ?? "Не выбрана скважина." } }));
    return null;
  }
  const mine = ++cmpSeq;
  plannedCheck.set((c) => ({ compare: { phase: "loading", answer: c.compare.answer, error: null } }));
  try {
    const answer = await comparePlanned(w.id, { set_id: setId, hole });
    if (mine !== cmpSeq) return null;
    plannedCheck.set({ compare: { phase: "ok", answer, error: null } });
    return answer;
  } catch (e) {
    if (mine !== cmpSeq) return null;
    const r = refusalOf(e);
    plannedCheck.set({ compare: { phase: "error", answer: null, error: `${r.message} ${r.hint}`.trim() } });
    return null;
  }
}
