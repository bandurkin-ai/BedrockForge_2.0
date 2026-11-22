/**
 * «Подобрать» и «Сохранить» of the window «Вариография» (ТЗ 8.6.3–8.6.4; ADR 0022 §7, §12, §13, decisions 11, 22;
 * e16-api.md «fit», «Модели вариограмм»):
 *
 * - the fit goes by the axes of the last calculation (`Work.expKey`) from the working model (the types of its
 *   structures, or `structures` of the command), its fixings (`Work.fix`, the paths `c0`, `structures.N.c`,
 *   `structures.N.a1…a3`; «закрепить самородок» adds `c0`) and the weights; the answer orders the structures by a1:
 *   the fixings become `fixed` of the answer (renumbered by `order`), the fit is kept (`Work.fitInfo`) for the save;
 * - the fit answer is applied only to the model and the calculation it was asked for (edited meanwhile — dropped);
 * - the save — `POST /models` (a new one; no name — the core gives «‹поле› · ‹таблица›», the next free «(N)»; a name
 *   given and taken — asked in our own window: replace that model, or another name) or `PUT /models/{id}` of the
 *   model of the window, asked first in our own window (any source, the assistant too); the body carries `fit` and
 *   `back` when the window has them; the replacement resets them (`null`) only after an edit dropped them, else the
 *   keys are not sent (the stored ones are kept);
 * - the replacement carries `expected_version` — the version the window read or saved (ТЗ раздел 17 п. 11); the model
 *   saved in another window meanwhile — 409 `version_conflict`, asked in our own window: «Сохранить как новую» (a
 *   `POST` with the next free name) or «Перечитать» (the stored model replaces the window's), else nothing.
 *
 * No React.
 */
import { CoreError } from "../../shell/api";
import { askConfirm, askText } from "../../shell/dialogs";
import { refusal } from "../../shell/objectRun";
import { nextName } from "../composites/api";
import { fitModel, loadModel, loadModels, replaceModel, saveModel, type FitAnswer, type FitWeights, type SaveBody } from "./api";
import { modelBody, newStructure, type StructureType, type VModel } from "./model";
import { setModels, setWork, storedWork, workOf, type FitInfo, type Work } from "./state";

export const noResult = () => refusal("no_result", "Сначала рассчитайте вариограммы осей.", "Кнопка «Рассчитать» в окне или команда variography.calc.");

/** The model of `fit`: the types of `types` (by position; new ones nested), or one spherical when there is none. */
export function fitStart(w: Work, types?: StructureType[]): VModel {
  const m = w.model;
  const sigma2 = w.result?.sigma2 ?? w.defaults?.sigma2 ?? null;
  const lag = Number(w.result?.params.lag ?? w.defaults?.defaults.lag ?? 0);
  const nlag = Number(w.result?.params.nlag ?? 15);
  const range = lag > 0 ? (lag * nlag) / 2 : undefined;
  const want = types ?? (m.structures.length ? m.structures.map((s) => s.type) : ["spherical" as const]);
  const structures = [...m.structures.slice(0, want.length)];
  while (structures.length < want.length) structures.push(newStructure({ ...m, structures }, want[structures.length], { sigma2, range }));
  return { ...m, type: w.data.transform === "nscore" ? "nscore" : m.type, structures: structures.map((s, i) => ({ ...s, type: want[i] })) };
}

/** The fixings of the request: those of the window that exist in the start model (and `c0` with «закрепить самородок»). */
export function fitFix(w: Pick<Work, "fix">, start: VModel, fixNugget: boolean): string[] {
  const n = start.structures.length;
  const ok = (p: string) => p === "c0" || (/^structures\.(\d+)\.(c|a1|a2|a3)$/.exec(p) !== null && Number(p.split(".")[1]) < n);
  return [...new Set([...w.fix.filter(ok), ...(fixNugget ? ["c0"] : [])])];
}

export const fitInfoOf = (got: FitAnswer, weights: FitWeights): FitInfo => ({ weights, residual: got.residual, n: got.n, fixed: got.fixed, warnings: got.warnings });

export interface FitOptions {
  types?: StructureType[];
  weights?: FitWeights;
  fixNugget?: boolean;
}

/** «Подобрать»: the request, then the model of the answer in the window with its fixings and the fit kept. */
export async function fitWork(win: string, opts: FitOptions = {}, fit: typeof fitModel = fitModel): Promise<FitAnswer> {
  const w = workOf(win);
  if (!w) throw refusal("no_window", "Окно «Вариография» закрыто.", "Откройте окно снова.");
  if (!w.expKey) throw noResult();
  const start = fitStart(w, opts.types);
  const weights = opts.weights ?? w.fitWeights ?? "n_h";
  const fixNugget = opts.fixNugget === true;
  const asked = JSON.stringify(modelBody(w.model));
  const key = w.expKey;
  const got = await fit({ key, type: start.type, model: modelBody(start), weights, fix: fitFix(w, start, fixNugget), fix_nugget: fixNugget });
  const cur = workOf(win);
  if (!cur || cur.expKey !== key || JSON.stringify(modelBody(cur.model)) !== asked) throw refusal("stale_answer", "Модель или расчёт окна изменены во время подбора — ответ подбора отброшен.", "Нажмите «Подобрать» ещё раз.");
  setWork(win, { model: got.model, fix: got.fixed, fitInfo: fitInfoOf(got, weights), back: null, fitWeights: weights, dirty: true });
  return got;
}

/** «Модель подобрана: структур 1, невязка 39,07, точек 14. …» */
export function fitText(got: FitAnswer): string {
  const res = got.residual === null ? "" : `, невязка ${got.residual.toLocaleString("ru-RU", { maximumSignificantDigits: 4 })}`;
  return `Модель подобрана: структур ${got.model.structures.length}${res}${got.n === null ? "" : `, точек ${got.n}`}${got.warnings.length ? `. ${got.warnings.join(" ")}` : ""}`;
}

/**
 * The body of the save: the model, the key of its points, the fit and the back-transform the window has.
 * `new` — only those it has; `replace` (its own stored model) — null only when an edit dropped them (`null`), absent
 * (never had, the stored ones kept) — not sent; `other` (another stored model replaced by name) — null when none.
 */
export function saveBody(w: Work, mode: "new" | "replace" | "other"): SaveBody {
  if (!w.expKey) throw noResult();
  const body: SaveBody = { model: modelBody(w.model), exp_key: w.expKey };
  if (w.fitInfo) body.fit = { weights: w.fitInfo.weights, residual: w.fitInfo.residual, n: w.fitInfo.n, fixed: w.fitInfo.fixed };
  else if (mode === "other" || (mode === "replace" && w.fitInfo === null)) body.fit = null;
  if (w.back) body.back = w.back;
  else if (mode === "other" || (mode === "replace" && w.back === null)) body.back = null;
  return body;
}

export async function refreshModels(): Promise<void> {
  setModels(await loadModels());
}

export const replaceQuestion = (id: number, name?: string) => ({ title: "Заменить модель", message: `Заменить сохранённую модель вариограммы №${id}${name ? ` («${name}»)` : ""} рабочей моделью окна?`, okLabel: "Заменить", danger: true });

export const conflictQuestion = (id: number) => ({
  title: "Модель изменена в другом окне",
  message: `Сохранённую модель вариограммы №${id} изменили в другом окне, пока вы её правили. Сохранить модель этого окна как новую? Иначе — перечитать сохранённую.`,
  okLabel: "Сохранить как новую",
});
export const rereadQuestion = (id: number) => ({ title: "Перечитать модель", message: `Перечитать модель №${id} из проекта? Правки этого окна будут заменены сохранённой моделью.`, okLabel: "Перечитать", danger: true });

export const takenQuestion = (name: string) => ({
  title: "Имя занято",
  message: `Модель вариограммы «${name}» уже есть. Заменить её моделью этого окна? Иначе — задать другое имя.`,
  okLabel: "Заменить",
  danger: true,
});

export interface SaveDeps {
  ask?: typeof askConfirm;
  askName?: typeof askText;
  create?: typeof saveModel;
  replace?: typeof replaceModel;
  load?: typeof loadModel;
  list?: typeof loadModels;
  refresh?: () => Promise<void>;
}

type Saved = { id: number; name: string; version?: number };

/** «Заменить» of a taken name: the stored model of that name is replaced (its current version); not found — refused. */
async function replaceNamed(w: Work, name: string, deps: Required<SaveDeps>): Promise<Saved> {
  const want = name.trim().toLowerCase();
  const row = (await deps.list()).find((m) => m.name.trim().toLowerCase() === want);
  if (!row) throw refusal("model_not_found", `Модель «${name}» не найдена.`, "Обновите дерево «Модели вариограмм» и повторите.");
  const { version } = await deps.load(row.id);
  return deps.replace(row.id, { name, ...saveBody(w, "other"), expected_version: version });
}

/** `POST /models`: no name — the core gives the default one (the next free «(N)»); a name given and taken — «Имя
 * занято» in our own window: replace that model, or another name (asked again while taken); null — cancelled. */
async function createNew(w: Work, name: string | undefined, deps: Required<SaveDeps>): Promise<Saved | null> {
  const body = saveBody(w, "new");
  let want = name?.trim() || undefined;
  for (;;) {
    try {
      return { ...(await deps.create({ ...(want ? { name: want } : {}), ...body })), version: 1 };
    } catch (e) {
      if (!(e instanceof CoreError) || e.code !== "name_taken" || !want) throw e;
      if (await deps.ask(takenQuestion(want))) return replaceNamed(w, want, deps);
      const other = (await deps.askName({ title: "Другое имя модели", label: "Имя модели вариограммы", initial: nextName(want), okLabel: "Сохранить" }))?.trim();
      if (!other) return null;
      want = other;
    }
  }
}

/** «Перечитать»: the stored model (its data, values, points, fit, back-transform, version) replaces the window's. */
export async function rereadWork(win: string, id: number, load: typeof loadModel = loadModel): Promise<void> {
  setWork(win, storedWork(await load(id)));
}

/** `POST` (new; a taken name given — «Имя занято») or `PUT` of the version read (the replacement, asked in our own
 * window; 409 `version_conflict` — «Сохранить как новую» / «Перечитать»); null — cancelled or read again. */
export async function saveWork(win: string, name?: string, deps: SaveDeps = {}): Promise<{ id: number; name: string } | null> {
  const d: Required<SaveDeps> = { ask: askConfirm, askName: askText, create: saveModel, replace: replaceModel, load: loadModel, list: loadModels, refresh: refreshModels, ...deps };
  const w = workOf(win);
  if (!w) throw refusal("no_window", "Окно «Вариография» закрыто.", "Откройте окно снова.");
  let saved: Saved | null;
  if (w.modelId !== null) {
    const id = w.modelId;
    const body = saveBody(w, "replace");
    if (!(await d.ask(replaceQuestion(id, name)))) return null;
    const expected = w.modelVersion ?? (await d.load(id)).version; // the version unknown to the window: the one stored now
    try {
      saved = await d.replace(id, { ...(name ? { name } : {}), ...body, expected_version: expected });
    } catch (e) {
      if (!(e instanceof CoreError) || e.code !== "version_conflict") throw e;
      if (await d.ask(conflictQuestion(id))) saved = await createNew(w, name, d);
      else {
        if (await d.ask(rereadQuestion(id))) await rereadWork(win, id, d.load);
        return null;
      }
    }
  } else {
    saved = await createNew(w, name, d);
  }
  if (!saved) return null;
  setWork(win, { modelId: saved.id, modelVersion: saved.version ?? null, dirty: false });
  await d.refresh().catch(() => undefined);
  return { id: saved.id, name: saved.name };
}
