/**
 * What the window «План оценки» knows (ТЗ 8.8.4; ADR 0024 §10; docs/progress/e18-api.md §4): the block model, the plan chosen
 * (or a new one that is not saved yet), its rows in the order of the run (a working copy: «вверх» / «вниз», «добавить» and
 * «убрать» change only the copy until «Сохранить»), the rows ticked for «Выполнить выбранные», the first refusal of the core.
 * The plans and the rows are those of `state.ts` (read by the core's events); the run is `runState.ts`. A plan is saved by
 * `POST` / `PUT /estimation-plans {expected_version}`; 409 `version_conflict` — «Перечитать» / «Сохранить как новый».
 * One window per project; another project — a clean one. No React.
 */
import { askConfirm } from "../../shell/dialogs";
import { Store } from "../../shell/store";
import { blocksStore } from "../blocks/state";
import { createPlan, deletePlanApi, loadPlan, refusalOf, replacePlan, type EstimationRow, type PlanRow, type Refusal, type Saved } from "./api";
import { estimableModels, estimationStore, loadDetails, loadRows } from "./state";

export const PLAN_WINDOW = "estimation-plan";
export const PLAN_TITLE = "План оценки";
/** The longest plan the core takes (e18-api §4). */
export const PLAN_MAX = 100;

export interface PlanState {
  modelId: number | null;
  /** the plan stored, null — a new one */
  planId: number | null;
  name: string;
  /** the version of the plan read (`expected_version` of the save) */
  version: number | null;
  /** the ids of the rows in the order of the run */
  items: number[];
  /** the rows ticked */
  picked: number[];
  /** the working copy differs from the stored plan (a new plan with rows is changed too) */
  dirty: boolean;
  saving: boolean;
  issue: (Refusal & { reread?: boolean }) | null;
}

const fresh = (): PlanState => ({ modelId: null, planId: null, name: "", version: null, items: [], picked: [], dirty: false, saving: false, issue: null });

export const planStore = new Store<PlanState>(fresh());

export const resetPlan = (): void => planStore.set(fresh());

// ---- what the stores hold ----

export const rowsOfModel = (modelId: number | null): EstimationRow[] => (modelId === null ? [] : estimationStore.get().rows.filter((r) => r.model_id === modelId));
export const plansOfModel = (modelId: number | null): PlanRow[] => (modelId === null ? [] : estimationStore.get().plans.filter((p) => p.model_id === modelId).sort((a, b) => a.name.localeCompare(b.name, "ru")));
export const planById = (id: number): PlanRow | undefined => estimationStore.get().plans.find((p) => p.id === id);

/** The models to choose from: the ones the estimation can write to; the model of a plan that is there already stays in the list. */
export const planModels = () => estimableModels();

/** The model a window opened without one gets: the one chosen in the tree, else the only model with rows or with the plans. */
export function pickModel(selectedBlocks: number | null): number | null {
  const list = planModels();
  if (selectedBlocks !== null && list.some((m) => m.id === selectedBlocks)) return selectedBlocks;
  const withRows = list.filter((m) => rowsOfModel(m.id).length > 0);
  if (withRows.length === 1) return withRows[0].id;
  return list.length === 1 ? list[0].id : null;
}

// ---- choosing ----

/** The details of the rows (the field of the samples) are read for the window; a row that is gone is not an error. */
function wantDetails(ids: readonly number[]): void {
  for (const id of ids) if (!estimationStore.get().details[id]) void loadDetails(id).catch(() => null);
}

/** A stored plan into the working copy. */
export function takePlan(p: PlanRow): void {
  planStore.set({ modelId: p.model_id, planId: p.id, name: p.name, version: p.version, items: [...p.items], picked: [], dirty: false, issue: null });
  wantDetails(p.items);
}

/** A new plan of the model: no rows yet (the person adds them), the name — the core's default when it stays empty. */
export function newPlan(modelId: number | null): void {
  planStore.set({ ...fresh(), modelId });
}

/** The model chosen: its first plan by name, else a new one. */
export function chooseModel(modelId: number | null): void {
  const first = plansOfModel(modelId)[0];
  if (first) takePlan(first);
  else newPlan(modelId);
}

/** The plan chosen; null — a new one. */
export function choosePlan(id: number | null): void {
  const p = id === null ? undefined : planById(id);
  if (p) takePlan(p);
  else newPlan(planStore.get().modelId);
}

/** A person asked to leave unsaved work: an own dialog (a command from the assistant does not discard anything). */
export const confirmDiscard = (): Promise<boolean> => (planStore.get().dirty ? askConfirm({ title: "План оценки", message: "План изменён и не сохранён. Отказаться от изменений?", okLabel: "Отказаться", danger: true }) : Promise.resolve(true));

// ---- the rows of the working copy ----

const changed = (items: number[], extra: Partial<PlanState> = {}): void => planStore.set((s) => ({ items, picked: s.picked.filter((id) => items.includes(id)), dirty: true, issue: null, ...extra }));

export function addItem(id: number): void {
  const s = planStore.get();
  if (s.items.includes(id) || s.items.length >= PLAN_MAX) return;
  const row = estimationStore.get().rows.find((r) => r.id === id);
  if (!row || row.model_id !== s.modelId) return;
  changed([...s.items, id]);
  wantDetails([id]);
}

/** All the rows of the model that are not in the plan yet, in the order of the list. */
export function addAll(): void {
  const s = planStore.get();
  const have = new Set(s.items);
  const more = rowsOfModel(s.modelId).filter((r) => !have.has(r.id)).map((r) => r.id);
  if (!more.length) return;
  changed([...s.items, ...more].slice(0, PLAN_MAX));
  wantDetails(more);
}

export const removeItem = (id: number): void => changed(planStore.get().items.filter((x) => x !== id));

/** One place up (`by` −1) or down (+1); at the edge nothing moves. */
export function moveItem(id: number, by: -1 | 1): void {
  const items = [...planStore.get().items];
  const i = items.indexOf(id);
  const j = i + by;
  if (i < 0 || j < 0 || j >= items.length) return;
  [items[i], items[j]] = [items[j], items[i]];
  changed(items);
}

export const setPlanName = (name: string): void => planStore.set({ name, dirty: true, issue: null });

export function pick(id: number, on: boolean): void {
  planStore.set((s) => ({ picked: on ? (s.picked.includes(id) ? s.picked : [...s.picked, id]) : s.picked.filter((x) => x !== id) }));
}

export const pickAll = (on: boolean): void => planStore.set((s) => ({ picked: on ? [...s.items] : [] }));

// ---- the arguments of `estimate.run` ----

const text = (ids: readonly number[]): string => ids.join("; ");

/** The whole plan: a stored one as it is (`plan_id`), a changed or a new one — its rows as they stand in the window. */
export function runArgsPlan(s: PlanState = planStore.get()): Record<string, unknown> | null {
  if (!s.items.length) return null;
  return s.planId !== null && !s.dirty ? { plan_id: s.planId } : { estimation_ids: text(s.items) };
}

/** The rows ticked, in the order of the plan. */
export function runArgsPicked(s: PlanState = planStore.get()): Record<string, unknown> | null {
  const ids = s.items.filter((id) => s.picked.includes(id));
  return ids.length ? { estimation_ids: text(ids) } : null;
}

// ---- the save ----

/** What a save of a plan writes: the arguments of the command `estimate.plan_save` (`items` null — the rows of a stored plan stay as they are). */
export interface PlanSave {
  /** the plan stored; null — a new one */
  id: number | null;
  modelId: number | null;
  name: string;
  items: number[] | null;
  /** a new plan beside the stored one `id` (its name gets «(копия)» when it stays the same) */
  asNew: boolean;
}

/** The arguments of the command from the working copy of the window (the buttons «Сохранить» and «Сохранить как новый» run it through the registry). */
export function planSaveArgs(s: PlanState = planStore.get(), asNew = false): Record<string, unknown> {
  const name = s.name.trim();
  return { ...(s.planId !== null ? { id: s.planId } : {}), ...(s.modelId !== null ? { model_id: s.modelId } : {}), ...(name ? { name } : {}), items: text(s.items), ...(asNew ? { as_new: true } : {}) };
}

/**
 * `POST` (a new plan or `asNew`) or `PUT` over the version read; a refusal stands in `issue` of the window (409 — with «Перечитать») when the
 * window holds this plan. The one writer: the buttons of the window and the command `estimate.plan_save` end here; the window follows when it holds
 * the plan that was written (the same stored plan, or the new one it was making).
 */
export async function writePlan(w: PlanSave): Promise<Saved> {
  const holds = (): boolean => {
    const cur = planStore.get();
    return cur.planId === w.id && cur.modelId === w.modelId;
  };
  if (w.modelId === null) {
    const r: Refusal = { code: "bad_param", message: "Блочная модель не выбрана.", hint: "Выберите модель вверху окна.", param: "model_id" };
    throw holds() ? failWith(r) : Object.assign(new Error(`${r.message} ${r.hint}`), r);
  }
  const name = w.name.trim();
  const create = w.id === null || w.asNew;
  const mine = holds();
  if (mine) planStore.set({ saving: true, issue: null });
  try {
    const saved = create
      ? await createPlan({ ...(name ? { name: w.id !== null && name === planById(w.id)?.name ? `${name} (копия)` : name } : {}), model_id: w.modelId, items: w.items ?? [] })
      : await replacePlan(w.id as number, (mine ? planStore.get().version : null) ?? planById(w.id as number)?.version ?? 1, { ...(name ? { name } : {}), ...(w.items ? { items: w.items } : {}) });
    if (mine) planStore.set({ planId: saved.id, name: saved.name, version: saved.version, dirty: false, saving: false });
    await loadRows();
    return saved;
  } catch (e) {
    if (mine) {
      const r = refusalOf(e);
      planStore.set({ saving: false, issue: { ...r, reread: r.code === "version_conflict" } });
    }
    throw e;
  }
}

/** The working copy of the window written (the tests and the code that has no registry at hand). */
export const savePlan = (opts: { asNew?: boolean } = {}): Promise<Saved> => {
  const s = planStore.get();
  return writePlan({ id: s.planId, modelId: s.modelId, name: s.name, items: s.items, asNew: opts.asNew === true });
};

function failWith(r: Refusal): Error {
  planStore.set({ issue: r });
  return Object.assign(new Error(`${r.message} ${r.hint}`), r);
}

/** «Перечитать»: the plan as another window stored it replaces the working copy; a plan that is gone becomes a new one. */
export async function rereadPlan(): Promise<void> {
  const id = planStore.get().planId;
  if (id === null) return;
  try {
    const p = await loadPlan(id);
    planStore.set({ name: p.name, version: p.version, items: p.items.map((i) => i.id), picked: [], dirty: false, issue: null });
    wantDetails(p.items.map((i) => i.id));
  } catch (e) {
    const r = refusalOf(e);
    if (r.code === "plan_not_found") planStore.set({ planId: null, version: null, dirty: true, issue: r });
    else planStore.set({ issue: r });
  }
}

/** `DELETE /estimation-plans/{id}`; the rows stay. The window goes to the next plan of the model, or to a new one. */
export async function deletePlan(id: number): Promise<{ name: string }> {
  const name = planById(id)?.name ?? `№${id}`;
  const here = planStore.get().planId === id; // the lists read after the deletion would make the window a «plan deleted elsewhere» one
  try {
    await deletePlanApi(id);
  } finally {
    await loadRows();
  }
  if (here) chooseModel(planStore.get().modelId);
  return { name };
}

// ---- following the project ----

/**
 * The lists were read again (an event of the core, a save, a run): the working copy follows them. A plan that was not changed here
 * takes the stored one; a changed one keeps its rows, minus the rows that are gone; a plan that is gone becomes a new one with its
 * rows; a model that is gone leaves the window.
 */
export function syncPlan(): void {
  const { loaded, rows } = estimationStore.get();
  const s = planStore.get();
  if (s.modelId !== null && blocksStore.get().loaded && !planModels().some((m) => m.id === s.modelId)) return void resetPlan();
  if (!loaded) return;
  const have = new Set(rows.map((r) => r.id));
  if (s.planId !== null) {
    const p = planById(s.planId);
    if (!p) return void planStore.set({ planId: null, version: null, dirty: true, issue: { code: "plan_not_found", message: `План «${s.name}» удалён.`, hint: "Сохраните его как новый или выберите другой.", param: null } });
    if (!s.dirty) {
      const same = p.version === s.version && p.name === s.name && p.items.join() === s.items.join();
      if (!same) planStore.set({ name: p.name, version: p.version, items: [...p.items], picked: s.picked.filter((id) => p.items.includes(id)) });
      return;
    }
  }
  const items = s.items.filter((id) => have.has(id));
  if (items.length !== s.items.length) planStore.set({ items, picked: s.picked.filter((id) => items.includes(id)) });
}
