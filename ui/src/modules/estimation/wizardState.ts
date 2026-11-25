/**
 * What the wizard «Оценка содержаний» knows (ТЗ 8.8.4; ADR 0024 §10): the form (`form.ts`), the step, the first wrong thing, the
 * defaults of the core for the data of the form (`GET /estimate/defaults`: the counts, the declustered means, the mapping of the
 * labels, the model of variogram; an answer that was overtaken is dropped), the stored models of variogram with their scale
 * (Д-38: a model of Y without its back transform is not chosen), the row being edited (its version) and the save
 * (`POST` / `PUT /estimations`; 409 `version_conflict` — «Перечитать» / «Сохранить как новые»), the preview of one block of the
 * parameters not saved yet (`POST /estimate/preview`, step 6; the newest answer only) and the edits of the search, the passes and
 * the output fields of steps 4–5 (a pass or a field not touched stays the core's default). One wizard per project; another project —
 * a clean form. No React.
 */
import { refusal } from "../../shell/objectRun";
import { errorText } from "../../shell/props";
import { Store } from "../../shell/store";
import { projectStore } from "../projects/state";
import { loadModel, loadModels, type ModelRow } from "../variography/api";
import { createEstimation, loadDefaults, loadEstimation, refusalOf, replaceEstimation, type DefaultsAnswer, type DefaultsQuery, type Refusal, type Saved, type Triple, type VarioScale } from "./api";
import { bodyOf, defaultsQueryOf, EMPTY_FORM, formOfMeta, scaleOf, stepOfParam, type EstimationForm, type Issue } from "./form";
import { bad } from "./formBase";
import { ALLOWED_FIELDS, anyHy, passesOf, type OutputsForm, type PassForm, type SearchForm, type Tri } from "./formSearch";
import { loadPreview, type PreviewAnswer } from "./previewApi";
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

export interface PreviewState {
  phase: "idle" | "loading" | "ok" | "error";
  /** the block asked for: the indices i, j, k of a parent, as typed */
  at: Tri;
  seq: number;
  data: PreviewAnswer | null;
  error: Issue | null;
  /** the form changed after the answer: it is of other parameters */
  stale: boolean;
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
  preview: PreviewState;
}

const EMPTY_PREVIEW: PreviewState = { phase: "idle", at: ["", "", ""], seq: 0, data: null, error: null, stale: false };
const EMPTY_DEFAULTS: DefaultsState = { phase: "idle", key: "", seq: 0, data: null, error: null };
const EMPTY_VARIO: VarioState = { rows: [], backs: {}, loaded: false, error: null };

const fresh = (): WizardState => ({ form: EMPTY_FORM, step: 1, issue: null, defaults: EMPTY_DEFAULTS, vario: EMPTY_VARIO, editing: null, saved: null, dirty: false, saving: false, preview: EMPTY_PREVIEW });

export const wizardStore = new Store<WizardState>(fresh());

export const form = (): EstimationForm => wizardStore.get().form;

/** The refusal of the core (or of the form) as a thing under a field; a path of the query becomes a path of the body. */
export function issueOfRefusal(r: Refusal): Issue {
  const p = r.param === "variogram_id" ? "variogram.model_id" : r.param;
  return { param: p, code: r.code, message: r.message, hint: r.hint, reread: r.code === "version_conflict" };
}

/** A change of the form (the person types): the old refusal goes. */
export function setForm(patch: Partial<EstimationForm>): void {
  wizardStore.set((s) => {
    // another method writes other fields: the typed list of the keys of the old one does not go over
    const fields = patch.method !== undefined && patch.method !== s.form.method && patch.outputs === undefined ? { outputs: { ...s.form.outputs, fields: null } } : {};
    return { form: { ...s.form, ...patch, ...fields }, issue: null, dirty: true, preview: s.preview.data ? { ...s.preview, stale: true } : s.preview };
  });
}

/** The whole form replaced (a stored row, a call of the command). */
export function putForm(f: EstimationForm, extra: Partial<WizardState> = {}): void {
  wizardStore.set((s) => ({ form: f, issue: null, dirty: false, preview: s.preview.data ? { ...s.preview, stale: true } : s.preview, ...extra }));
}

export const setStep = (step: number): void => wizardStore.set({ step: Math.min(6, Math.max(1, step)) });
export const setIssue = (issue: Issue | null): void => wizardStore.set(issue ? { issue, step: stepOfParam(issue.param) } : { issue });

/** Another project: a clean wizard (what was asked of one project does not belong to another). */
export function resetWizard(): void {
  const old = wizardStore.get();
  wizardStore.set({ ...fresh(), defaults: { ...EMPTY_DEFAULTS, seq: old.defaults.seq + 1 }, preview: { ...EMPTY_PREVIEW, seq: old.preview.seq + 1 } });
}

// ---- the defaults of the core ----

export const EMPTY_DEFAULTS_STATE = EMPTY_DEFAULTS;

/**
 * The requester of the defaults of a wizard (the estimation's below, the simulation's in `modules/simulation`): the core is asked
 * for the defaults of the data of the form after a short pause of the typing; the newest answer only, a request that was asked
 * already is not asked again, another project drops the answer.
 */
export function defaultsRequester(store: Store<{ defaults: DefaultsState }>, query: () => DefaultsQuery | null): (delay?: number) => void {
  let timer: ReturnType<typeof setTimeout> | null = null;
  return (delay = 200) => {
    if (timer) clearTimeout(timer);
    timer = null;
    const q = query();
    const cur = store.get().defaults;
    if (!q) {
      store.set({ defaults: { ...EMPTY_DEFAULTS, seq: cur.seq + 1 } });
      return;
    }
    const key = JSON.stringify(q);
    if (key === cur.key && cur.phase !== "idle" && cur.phase !== "error") return;
    const seq = cur.seq + 1;
    store.set({ defaults: { phase: "loading", key, seq, data: cur.key === key ? cur.data : null, error: null } });
    const uid = projectStore.get().current?.uid ?? null;
    const go = () => {
      timer = null;
      loadDefaults(q)
        .then((data) => {
          if (store.get().defaults.seq === seq && (projectStore.get().current?.uid ?? null) === uid) store.set({ defaults: { phase: "ok", key, seq, data, error: null } });
        })
        .catch((e: unknown) => {
          if (store.get().defaults.seq === seq) store.set({ defaults: { phase: "error", key, seq, data: null, error: issueOfRefusal(refusalOf(e)) } });
        });
    };
    if (delay <= 0) go();
    else timer = setTimeout(go, delay);
  };
}

/** The core is asked for the defaults of the data of the form (after a short pause of the typing); the newest answer only. */
export const requestDefaults = defaultsRequester(wizardStore, () => defaultsQueryOf(form()));

// ---- the stored models of variogram (Д-38) ----

let varioSeq = 0;

/** The models of the project and which of them have a back transform (`meta.back.model`); the wizard of the simulation reads them the same way. */
export async function readVariogramModels(): Promise<Pick<VarioState, "rows" | "backs">> {
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
  return { rows, backs };
}

/** The models of the project with the scale of each: a model in normal scores is Z only with its back transform. */
export async function loadVariogramModels(): Promise<void> {
  const mine = ++varioSeq;
  const uid = projectStore.get().current?.uid ?? null;
  try {
    const { rows, backs } = await readVariogramModels();
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

// ---- the edits of steps 4–5: the search, the passes, the output fields ----

const defaultParams = (): Record<string, unknown> | null => wizardStore.get().defaults.data?.params ?? null;

export const setSearch = (patch: Partial<SearchForm>): void => setForm({ search: { ...form().search, ...patch } });
export const setOutputs = (patch: Partial<OutputsForm>): void => setForm({ outputs: { ...form().outputs, ...patch } });

/** The passes as the person left them (those of the core while none are typed) changed by `fn`; the rule of Д-29 on brings the field NHY. */
export function editPasses(fn: (passes: PassForm[]) => PassForm[]): void {
  const f = form();
  const passes = fn(passesOf(f.search, defaultParams()).map((p) => ({ ...p, hy: { ...p.hy } })));
  const o = f.outputs;
  const nhy = anyHy(passes) && o.fields !== null && !o.fields.includes("NHY") && ALLOWED_FIELDS[f.method].includes("NHY");
  setForm({ search: { ...f.search, passes }, ...(nhy ? { outputs: { ...o, fields: [...o.fields!, "NHY"] } } : {}) });
}

export const setPass = (i: number, patch: Partial<PassForm>): void => editPasses((ps) => ps.map((p, k) => (k === i ? { ...p, ...patch } : p)));
export const setHy = (i: number, patch: Partial<PassForm["hy"]>): void => editPasses((ps) => ps.map((p, k) => (k === i ? { ...p, hy: { ...p.hy, ...patch } } : p)));
export const removePass = (i: number): void => editPasses((ps) => (ps.length > 1 ? ps.filter((_, k) => k !== i) : ps));
export function addPass(): void {
  editPasses((ps) => {
    const last = ps[ps.length - 1];
    // the next pass: the last one with the radii one step farther
    return ps.length >= 5 ? ps : [...ps, { ...last, factor: String(Number.parseFloat(last.factor.replace(",", ".")) + 1 || ps.length + 1), hy: { ...last.hy, on: false } }];
  });
}
/** «Как в ядре»: the passes the person typed go, the core's default ones stay. */
export const resetPasses = (): void => setSearch({ passes: null });

/** A key of the output fields on or off (the keys of the method by default until the first touch); STATUS cannot be switched off. */
export function toggleField(key: string, on: boolean, current: readonly string[]): void {
  if (key === "STATUS") return;
  const next = on ? [...current, key] : current.filter((k) => k !== key);
  setOutputs({ fields: ALLOWED_FIELDS[form().method].filter((k) => k === "STATUS" || next.includes(k)) });
}

// ---- the preview of one block (step 6) ----

export const setPreviewAt = (at: Tri): void => wizardStore.set((s) => ({ preview: { ...s.preview, at } }));

/** The block i, j, k of the parent as typed: whole numbers from 0; the words under the field otherwise. */
export function parseBlock(at: Tri): Triple | Issue {
  const out: number[] = [];
  for (let i = 0; i < 3; i++) {
    const v = Number(at[i].trim().replace(",", "."));
    if (!at[i].trim() || !Number.isInteger(v) || v < 0) return bad(`at.ijk.${i}`, `Блок: индекс ${"ijk"[i]} — целое число от 0.`, "Введите номера ячейки родителя по X, Y, Z, например 4; 4; 7.");
    out.push(v);
  }
  return out as Triple;
}

/** «Показать»: the parameters of the wizard estimate one block (not saved, not run); a wrong form or block is told under the preview, the newest answer only. */
export async function requestPreview(): Promise<void> {
  const s = wizardStore.get();
  const seq = s.preview.seq + 1;
  const fail = (error: Issue) => wizardStore.set((x) => ({ preview: { ...x.preview, seq, phase: "error", data: null, error, stale: false } }));
  const at = parseBlock(s.preview.at);
  if (!Array.isArray(at)) return fail(at);
  const got = bodyOf(s.form, { scales: scalesOf(s.vario) });
  if (!got.ok) return fail(got.issue);
  wizardStore.set((x) => ({ preview: { ...x.preview, seq, phase: "loading", error: null } }));
  const uid = projectStore.get().current?.uid ?? null;
  try {
    const data = await loadPreview(got.body, { ijk: at });
    if (wizardStore.get().preview.seq === seq && (projectStore.get().current?.uid ?? null) === uid) wizardStore.set((x) => ({ preview: { ...x.preview, phase: "ok", data, error: null, stale: false } }));
  } catch (e) {
    if (wizardStore.get().preview.seq === seq) fail(issueOfRefusal(refusalOf(e)));
  }
}

// ---- the run of the row from step 6 ----

/**
 * «Выполнить оценку»: the row is saved when it is new or changed (a refusal stands under its field, on its step), then `estimate.run`
 * runs it (the command asks «Заменить поля?» when the model has them and follows the job). The answer is the line of the command.
 */
export async function runWizard(app: { run: (id: string, args?: Record<string, unknown>) => Promise<unknown> }): Promise<unknown> {
  const s = wizardStore.get();
  const id = !s.dirty && s.editing ? s.editing.id : (await saveWizard()).id;
  return app.run("estimate.run", { estimation_ids: String(id) });
}
