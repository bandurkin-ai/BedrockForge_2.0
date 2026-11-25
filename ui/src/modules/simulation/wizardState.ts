/**
 * What the wizard «Моделирование» knows (ТЗ 8.8.1; Д-23): the form (`form.ts`), the step, the first wrong thing, the defaults of the core
 * for the data of the form (`GET /estimate/defaults`: the counts, the declustered means, the mapping of the labels — the same
 * requester as the estimation's, without a model of variogram: a model in normal scores is not the one `defaults` reads), the stored
 * models of variogram, the row being edited (its version) and the save (`POST` / `PUT /simulations`; 409 `version_conflict` —
 * «Перечитать» / «Сохранить как новое»). One wizard per project; another project — a clean form. No React.
 */
import { refusal } from "../../shell/objectRun";
import { errorText } from "../../shell/props";
import { Store } from "../../shell/store";
import { defaultsQueryOf as estimationQuery, type EstimationForm } from "../estimation/form";
import { EMPTY_DEFAULTS_STATE, defaultsRequester, isVariogramModelEvent, issueOfRefusal, readVariogramModels, type DefaultsState, type VarioState } from "../estimation/wizardState";
import { loadBlockDetails } from "../blocks/state";
import { onCoreEvent } from "../jobs/state";
import { projectStore } from "../projects/state";
import { createSimulation, loadSimulation, refusalOf, replaceSimulation, type Saved, type SimKind } from "./api";
import { bodyOf, EMPTY_SIM_FORM, formOfMeta, matchIndicators, simStepOfParam, SIM_LAST_STEP, type Issue, type SimForm } from "./form";
import { loadRows } from "./state";

export const SIM_WIZARD_WINDOW = "simulation-wizard";
export const SIM_TITLE = "Моделирование";

export interface SimWizardState {
  form: SimForm;
  /** 1…6 */
  step: number;
  issue: Issue | null;
  defaults: DefaultsState;
  vario: VarioState;
  /** the row being edited (the version read), null — a new one */
  editing: { id: number; version: number } | null;
  saved: Saved | null;
  /** the form changed after the save */
  dirty: boolean;
  saving: boolean;
}

const EMPTY_VARIO: VarioState = { rows: [], backs: {}, loaded: false, error: null };
const fresh = (): SimWizardState => ({ form: EMPTY_SIM_FORM, step: 1, issue: null, defaults: EMPTY_DEFAULTS_STATE, vario: EMPTY_VARIO, editing: null, saved: null, dirty: false, saving: false });

export const simWizardStore = new Store<SimWizardState>(fresh());

export const form = (): SimForm => simWizardStore.get().form;

/** A change of the form (the person types): the old refusal goes. */
export function setForm(patch: Partial<SimForm>): void {
  simWizardStore.set((s) => ({ form: { ...s.form, ...patch }, issue: null, dirty: true }));
}

/** The kind changed: the models of the other kind do not fit (SGS wants normal scores, SIS the units of the field), the list goes. */
export function setKind(kind: SimKind): void {
  if (kind === form().kind) return;
  setForm({ kind, variogramId: null, variogramIds: [], ...(kind === "sis" ? {} : { sisBy: "thresholds" as const, ikModels: "per" as const }) });
}

/** The whole form replaced (a stored row, a call of the command). */
export function putForm(f: SimForm, extra: Partial<SimWizardState> = {}): void {
  simWizardStore.set({ form: f, issue: null, dirty: false, ...extra });
}

export const setStep = (step: number): void => simWizardStore.set({ step: Math.min(SIM_LAST_STEP, Math.max(1, step)) });
export const setIssue = (issue: Issue | null): void => simWizardStore.set(issue ? { issue, step: simStepOfParam(issue.param) } : { issue });

/** Another project: a clean wizard (what was asked of one project does not belong to another). */
export function resetWizard(): void {
  const old = simWizardStore.get();
  simWizardStore.set({ ...fresh(), defaults: { ...EMPTY_DEFAULTS_STATE, seq: old.defaults.seq + 1 } });
}

// ---- the defaults of the core ----

/** The query of the samples: the estimation's without a model of variogram and a method (they are not the simulation's). */
export function defaultsQueryOf(f: EstimationForm) {
  const q = estimationQuery({ ...f, variogramId: null, method: "nn" });
  if (q) delete q.method;
  return q;
}

export const requestDefaults = defaultsRequester(simWizardStore, () => defaultsQueryOf(form()));

// ---- the stored models of variogram ----

let varioSeq = 0;

export async function loadVariogramModels(): Promise<void> {
  const mine = ++varioSeq;
  watchVariogramModels();
  varioReading = true;
  const uid = projectStore.get().current?.uid ?? null;
  try {
    const { rows, backs } = await readVariogramModels();
    if (mine !== varioSeq || (projectStore.get().current?.uid ?? null) !== uid) return;
    simWizardStore.set({ vario: { rows, backs, loaded: true, error: null } });
  } catch (e) {
    if (mine !== varioSeq) return;
    simWizardStore.set({ vario: { ...EMPTY_VARIO, loaded: true, error: errorText(e) } });
  } finally {
    if (mine === varioSeq) varioReading = false;
  }
}

let varioReading = false;

// the list the wizard read goes stale when a model is saved or deleted in «Вариография»: it is read again while the wizard holds it
export function onSimVariogramEvent(event: string, data: string): void {
  if (isVariogramModelEvent(event, data) && projectStore.get().current && (simWizardStore.get().vario.loaded || varioReading)) void loadVariogramModels();
}
let watching = false;
/** The listener is put on at the first read (not when the module is loaded), once. */
function watchVariogramModels(): void {
  if (watching) return;
  watching = true;
  onCoreEvent(onSimVariogramEvent);
}

/** «Подобрать по порогам»: for each threshold the model of an indicator of the same threshold (`indicator:<t>`); a threshold without one keeps what was chosen. */
export function matchIndicatorModels(thresholds: readonly number[]): number {
  const f = form();
  const found = matchIndicators(simWizardStore.get().vario.rows, thresholds);
  setForm({ variogramIds: thresholds.map((_, i) => found[i] ?? f.variogramIds[i] ?? null) });
  return found.filter((x) => x !== null).length;
}

export function chooseModel(id: number | null): void {
  if (id !== null) void loadBlockDetails(id);
}

// ---- the stored row, the save ----

/** «Изменить»: the stored row in the form; its version goes with the save. */
export async function startEdit(id: number): Promise<void> {
  const info = await loadSimulation(id);
  putForm(formOfMeta(info.meta), { editing: { id: info.id, version: info.version }, saved: null, step: 1 });
}

/** «Перечитать» after a 409: the form is the stored row again, the version is the new one. */
export async function rereadSimulation(): Promise<void> {
  const e = simWizardStore.get().editing;
  if (!e) return;
  const info = await loadSimulation(e.id);
  putForm(formOfMeta(info.meta), { editing: { id: info.id, version: info.version }, saved: null });
}

const fail = (issue: Issue): never => {
  setIssue(issue);
  throw refusal(issue.code, issue.message, issue.hint);
};

/** The refusal of the core as a thing under a field. */
const issueOf = (e: unknown): Issue => issueOfRefusal(refusalOf(e));

/** «Сохранить»: the form is checked here, the core checks the rest (404, 409, 422, 423); a refusal stands under its field of its step. */
export async function saveSimulation(opts: { asNew?: boolean } = {}): Promise<Saved> {
  const s = simWizardStore.get();
  if (s.saving) throw refusal("saving", "Параметры уже сохраняются.", "Дождитесь конца сохранения.");
  const got = bodyOf(s.form, s.vario);
  if (!got.ok) return fail(got.issue);
  const edit = opts.asNew ? null : s.editing;
  simWizardStore.set({ saving: true });
  try {
    const saved = edit ? await replaceSimulation(edit.id, edit.version, got.body) : await createSimulation(got.body);
    simWizardStore.set({ editing: { id: saved.id, version: saved.version }, saved, dirty: false, issue: null });
    void loadRows();
    return saved;
  } catch (e) {
    return fail(issueOf(e));
  } finally {
    simWizardStore.set({ saving: false });
  }
}

/** «Выполнить»: the row is saved when it is new or changed (a refusal stands under its field, on its step), then `simulate.run` runs it. */
export async function runWizard(app: { run: (id: string, args?: Record<string, unknown>) => Promise<unknown> }): Promise<unknown> {
  const s = simWizardStore.get();
  const id = !s.dirty && s.editing ? s.editing.id : (await saveSimulation()).id;
  return app.run("simulate.run", { id });
}
