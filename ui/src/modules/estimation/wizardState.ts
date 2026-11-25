/**
 * What the wizard «Оценка содержаний» knows (ТЗ 8.8.4; ADR 0024 §10): the form (`form.ts`), the step, the first wrong thing, the
 * defaults of the core for the data of the form (`GET /estimate/defaults`: the counts, the declustered means, the mapping of the
 * labels, the model of variogram; an answer that was overtaken is dropped), the stored models of variogram with their scale
 * (Д-38: a model of Y without its back transform is not chosen), the row being edited (its version) and the save
 * (`POST` / `PUT /estimations`; 409 `version_conflict` — «Перечитать» / «Сохранить как новые»). One wizard per project;
 * another project — a clean form. No React.
 */
import { refusal } from "../../shell/objectRun";
import { errorText } from "../../shell/props";
import { Store } from "../../shell/store";
import { projectStore } from "../projects/state";
import { loadModel, loadModels, type ModelRow } from "../variography/api";
import { createEstimation, loadDefaults, loadEstimation, refusalOf, replaceEstimation, type DefaultsAnswer, type Refusal, type Saved, type VarioScale } from "./api";
import { bodyOf, defaultsQueryOf, EMPTY_FORM, formOfMeta, scaleOf, stepOfParam, type EstimationForm, type Issue } from "./form";
import { loadRows } from "./state";

export const WIZARD_WINDOW = "estimation-wizard";
export const TITLE = "Оценка содержаний";

export type DefaultsPhase = "idle" | "loading" | "ok" | "error";

export interface DefaultsState {
  phase: DefaultsPhase;
  /** the request the state is of (JSON): a new request overtakes the old one */
  key: string;
  seq: number;
  data: DefaultsAnswer | null;
  error: Issue | null;
}

export interface VarioState {
  rows: ModelRow[];
  /** a model in normal scores has its back transform (`meta.back`) */
  backs: Record<number, boolean>;
  loaded: boolean;
  error: string | null;
}

export interface WizardState {
  form: EstimationForm;
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

const EMPTY_DEFAULTS: DefaultsState = { phase: "idle", key: "", seq: 0, data: null, error: null };
const EMPTY_VARIO: VarioState = { rows: [], backs: {}, loaded: false, error: null };

const fresh = (): WizardState => ({ form: EMPTY_FORM, step: 1, issue: null, defaults: EMPTY_DEFAULTS, vario: EMPTY_VARIO, editing: null, saved: null, dirty: false, saving: false });

export const wizardStore = new Store<WizardState>(fresh());

export const form = (): EstimationForm => wizardStore.get().form;

/** The refusal of the core (or of the form) as a thing under a field; a path of the query becomes a path of the body. */
export function issueOfRefusal(r: Refusal): Issue {
  const p = r.param === "variogram_id" ? "variogram.model_id" : r.param;
  return { param: p, code: r.code, message: r.message, hint: r.hint, reread: r.code === "version_conflict" };
}

/** A change of the form (the person types): the old refusal goes. */
export function setForm(patch: Partial<EstimationForm>): void {
  wizardStore.set((s) => ({ form: { ...s.form, ...patch }, issue: null, dirty: true }));
}

/** The whole form replaced (a stored row, a call of the command). */
export function putForm(f: EstimationForm, extra: Partial<WizardState> = {}): void {
  wizardStore.set({ form: f, issue: null, dirty: false, ...extra });
}

export const setStep = (step: number): void => wizardStore.set({ step: Math.min(6, Math.max(1, step)) });
export const setIssue = (issue: Issue | null): void => wizardStore.set(issue ? { issue, step: stepOfParam(issue.param) } : { issue });

/** Another project: a clean wizard (what was asked of one project does not belong to another). */
export function resetWizard(): void {
  const old = wizardStore.get();
  wizardStore.set({ ...fresh(), defaults: { ...EMPTY_DEFAULTS, seq: old.defaults.seq + 1 } });
}

// ---- the defaults of the core ----

let timer: ReturnType<typeof setTimeout> | null = null;

/** The core is asked for the defaults of the data of the form (after a short pause of the typing); the newest answer only. */
export function requestDefaults(delay = 200): void {
  if (timer) clearTimeout(timer);
  timer = null;
  const q = defaultsQueryOf(form());
  const cur = wizardStore.get().defaults;
  if (!q) {
    wizardStore.set({ defaults: { ...EMPTY_DEFAULTS, seq: cur.seq + 1 } });
    return;
  }
  const key = JSON.stringify(q);
  if (key === cur.key && cur.phase !== "idle" && cur.phase !== "error") return;
  const seq = cur.seq + 1;
  wizardStore.set({ defaults: { phase: "loading", key, seq, data: cur.key === key ? cur.data : null, error: null } });
  const uid = projectStore.get().current?.uid ?? null;
  const go = () => {
    timer = null;
    loadDefaults(q)
      .then((data) => {
        if (wizardStore.get().defaults.seq === seq && (projectStore.get().current?.uid ?? null) === uid) wizardStore.set({ defaults: { phase: "ok", key, seq, data, error: null } });
      })
      .catch((e: unknown) => {
        if (wizardStore.get().defaults.seq === seq) wizardStore.set({ defaults: { phase: "error", key, seq, data: null, error: issueOfRefusal(refusalOf(e)) } });
      });
  };
  if (delay <= 0) go();
  else timer = setTimeout(go, delay);
}

// ---- the stored models of variogram (Д-38) ----

let varioSeq = 0;

/** The models of the project with the scale of each: a model in normal scores is Z only with its back transform. */
export async function loadVariogramModels(): Promise<void> {
  const mine = ++varioSeq;
  const uid = projectStore.get().current?.uid ?? null;
  try {
    const rows = await loadModels();
    const backs: Record<number, boolean> = {};
    await Promise.all(
      rows
        .filter((r) => r.transform !== "none")
        .map(async (r) => {
          // a model that cannot be read has no back transform for us: it is not offered (the core would refuse it too)
          backs[r.id] = await loadModel(r.id).then(
            (m) => !!m.meta.back && typeof m.meta.back === "object" && !!(m.meta.back as Record<string, unknown>).model,
            () => false,
          );
        }),
    );
    if (mine !== varioSeq || (projectStore.get().current?.uid ?? null) !== uid) return;
    wizardStore.set({ vario: { rows, backs, loaded: true, error: null } });
  } catch (e) {
    if (mine !== varioSeq) return;
    wizardStore.set({ vario: { ...EMPTY_VARIO, loaded: true, error: errorText(e) } });
  }
}

/** The scale of every stored model of variogram (Z / back / Y). */
export function scalesOf(v: VarioState = wizardStore.get().vario): Record<number, VarioScale> {
  return Object.fromEntries(v.rows.map((r) => [r.id, scaleOf(r.transform, v.backs[r.id] === true)]));
}

// ---- the stored row, the save ----

/** «Изменить»: the stored row in the form; its version goes with the save. */
export async function startEdit(id: number): Promise<void> {
  const info = await loadEstimation(id);
  putForm(formOfMeta(info.meta), { editing: { id: info.id, version: info.version }, saved: null, step: 1 });
}

/** «Перечитать» after a 409: the form is the stored row again, the version is the new one. */
export async function rereadEstimation(): Promise<void> {
  const e = wizardStore.get().editing;
  if (!e) return;
  const info = await loadEstimation(e.id);
  putForm(formOfMeta(info.meta), { editing: { id: info.id, version: info.version }, saved: null });
}

const fail = (issue: Issue): never => {
  setIssue(issue);
  throw refusal(issue.code, issue.message, issue.hint);
};

/** «Сохранить»: the steps 1–3 are checked here, the core checks the rest (404, 409, 422, 423); a refusal stands under its field of its step. */
export async function saveWizard(opts: { asNew?: boolean } = {}): Promise<Saved> {
  const s = wizardStore.get();
  if (s.saving) throw refusal("saving", "Параметры уже сохраняются.", "Дождитесь конца сохранения.");
  const got = bodyOf(s.form, { scales: scalesOf(s.vario) });
  if (!got.ok) return fail(got.issue);
  const edit = opts.asNew ? null : s.editing;
  wizardStore.set({ saving: true });
  try {
    const saved = edit ? await replaceEstimation(edit.id, edit.version, got.body) : await createEstimation(got.body);
    wizardStore.set({ editing: { id: saved.id, version: saved.version }, saved, dirty: false, issue: null });
    void loadRows();
    return saved;
  } catch (e) {
    return fail(issueOfRefusal(refusalOf(e)));
  } finally {
    wizardStore.set({ saving: false });
  }
}
