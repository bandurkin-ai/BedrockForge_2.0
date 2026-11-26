/**
 * What the wizard «Новая модель» knows (ТЗ 8.9.4; ADR 0026): the form (`form.ts`), the step, the first wrong thing (the words under a
 * field — of the form or of the core's 422 with `param`), the defaults and the ranges of the core (`GET /defaults`, once per project),
 * the model being edited (its version) and the save (`POST /models`, `PUT /models/{id}`; 409 `version_conflict` — «Перечитать» /
 * «Сохранить как новую»). After the first save the wizard goes on editing that model. One wizard per project; another project — a
 * clean form. No React.
 */
import { refusal } from "../../shell/objectRun";
import { Store } from "../../shell/store";
import { projectStore } from "../projects/state";
import { createModel, loadDefaults, refusalOf, replaceModel, type DefaultsAnswer, type Refusal, type Saved } from "./api";
import { bodyOf, EMPTY_FORM, formOfMeta, LAST_STEP, stepOfParam, type Issue, type ModelForm } from "./form";
import { loadDetails, loadRows } from "./state";

export const WIZARD_WINDOW = "implicit-wizard";
export const TITLE = "Новая модель";
export const editTitle = (name: string): string => `Модель «${name}»`;

export interface DefaultsState {
  phase: "idle" | "loading" | "ok" | "error";
  data: DefaultsAnswer | null;
  error: string | null;
}

export interface WizardState {
  form: ModelForm;
  /** 1…4 */
  step: number;
  issue: Issue | null;
  defaults: DefaultsState;
  /** the model being edited (the version read), null — a new one */
  editing: { id: number; version: number } | null;
  saved: Saved | null;
  /** the form changed after the save */
  dirty: boolean;
  saving: boolean;
}

const EMPTY_DEFAULTS: DefaultsState = { phase: "idle", data: null, error: null };
const fresh = (): WizardState => ({ form: EMPTY_FORM, step: 1, issue: null, defaults: EMPTY_DEFAULTS, editing: null, saved: null, dirty: false, saving: false });

export const wizardStore = new Store<WizardState>(fresh());

export const form = (): ModelForm => wizardStore.get().form;

/** The refusal of the core as a thing under a field (a name taken has no `param`: it is the name's). */
export function issueOfRefusal(r: Refusal): Issue {
  const param = r.param ?? (r.code === "name_taken" ? "name" : r.code === "version_conflict" ? null : r.code === "wireframe_open" ? "boundary.wireframe_id" : r.code === "trend_not_found" ? "trend_id" : null);
  return { param, code: r.code, message: r.message, hint: r.hint, reread: r.code === "version_conflict" };
}

/** A change of the form (the person types): the old refusal goes. */
export function setForm(patch: Partial<ModelForm>): void {
  wizardStore.set((s) => ({ form: { ...s.form, ...patch }, issue: null, dirty: true }));
}

/** The whole form replaced (a stored model, a call of the command). */
export function putForm(f: ModelForm, extra: Partial<WizardState> = {}): void {
  wizardStore.set({ form: f, issue: null, dirty: false, ...extra });
}

export const setStep = (step: number): void => wizardStore.set({ step: Math.min(LAST_STEP, Math.max(1, step)) });
export const setIssue = (issue: Issue | null): void => wizardStore.set(issue ? { issue, step: stepOfParam(issue.param) || wizardStore.get().step } : { issue });

/** Another project: a clean wizard (what was asked of one project does not belong to another). */
export function resetWizard(): void {
  wizardStore.set(fresh());
}

/** A clean form for a new model; the defaults of the core stay. */
export function startNew(f: ModelForm = EMPTY_FORM, step = 1): void {
  putForm(f, { editing: null, saved: null, step });
}

// ---- the defaults and the ranges of the core ----

let defaultsSeq = 0;

/** Reads `GET /defaults` once per project (a read that a reset overtook is dropped). */
export async function ensureDefaults(): Promise<DefaultsAnswer | null> {
  const s = wizardStore.get().defaults;
  if (s.phase === "ok" || s.phase === "loading") return s.data;
  const mine = ++defaultsSeq;
  const uid = projectStore.get().current?.uid ?? null;
  wizardStore.set({ defaults: { phase: "loading", data: null, error: null } });
  try {
    const data = await loadDefaults();
    if (mine !== defaultsSeq || projectStore.get().current?.uid !== uid) return null;
    wizardStore.set({ defaults: { phase: "ok", data, error: null } });
    return data;
  } catch (e) {
    if (mine !== defaultsSeq) return null;
    const r = refusalOf(e);
    wizardStore.set({ defaults: { phase: "error", data: null, error: `${r.message} ${r.hint}`.trim() } });
    return null;
  }
}

export function forgetDefaults(): void {
  defaultsSeq++;
  wizardStore.set({ defaults: EMPTY_DEFAULTS });
}

// ---- the stored model ----

/** The model with its parameters as stored: the form of the wizard «изменить». */
export async function startEditModel(id: number, step = 1): Promise<void> {
  const info = await loadDetails(id);
  if (!info) throw refusal("project_changed", "Проект изменился.", "Откройте модель снова.");
  putForm(formOfMeta(info.name, info.meta), { editing: { id: info.id, version: info.version }, saved: null, step });
}

/** «Перечитать» after a 409: the stored model replaces the form. */
export async function rereadModel(): Promise<void> {
  const e = wizardStore.get().editing;
  if (e) await startEditModel(e.id, wizardStore.get().step);
}

// ---- the save ----

/**
 * Writes the form as a model (`POST /models`) or as a new version of the open one (`PUT /models/{id}`); `asNew` — a new model next to the
 * open one. The first wrong thing of the form, or the refusal of the core, stands under its field and is thrown as a refusal.
 */
export async function saveWizard(opts: { asNew?: boolean } = {}): Promise<Saved> {
  const s = wizardStore.get();
  if (s.saving) throw refusal("busy", "Модель уже сохраняется.", "Дождитесь окончания сохранения и повторите.");
  const built = bodyOf(s.form, s.defaults.data);
  if (!built.ok) {
    setIssue(built.issue);
    throw refusal(built.issue.code, built.issue.message, built.issue.hint);
  }
  wizardStore.set({ saving: true });
  try {
    const edit = opts.asNew ? null : s.editing;
    const saved = edit ? await replaceModel(edit.id, edit.version, built.body) : await createModel(built.body);
    wizardStore.set({ saved, editing: { id: saved.id, version: saved.version }, dirty: false, issue: null });
    await loadRows();
    return saved;
  } catch (e) {
    const r = refusalOf(e);
    const issue = issueOfRefusal(r);
    setIssue(issue);
    throw refusal(r.code, r.message, r.hint);
  } finally {
    wizardStore.set({ saving: false });
  }
}
