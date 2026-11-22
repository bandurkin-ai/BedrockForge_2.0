/**
 * «Подобрать» и «Сохранить» of the window «Вариография» (ТЗ 8.6.3–8.6.4; ADR 0022 §7, §12, §13, decisions 11, 22;
 * e16-api.md «fit», «Модели вариограмм»):
 *
 * - the fit goes by the axes of the last calculation (`Work.expKey`) from the working model (the types of its
 *   structures, or `structures` of the command), its fixings (`Work.fix`, the paths `c0`, `structures.N.c`,
 *   `structures.N.a1…a3`; «закрепить самородок» adds `c0`) and the weights; the answer orders the structures by a1:
 *   the fixings become `fixed` of the answer (renumbered by `order`), the fit is kept (`Work.fitInfo`) for the save;
 * - the save — `POST /models` (a new one; the default name «‹поле› · ‹таблица›», a taken one — the next free «(2)»)
 *   or `PUT /models/{id}` of the model of the window, asked first in our own window (any source, the assistant too);
 *   the body carries `fit` and `back` when the window has them, the replacement resets them otherwise (`null`);
 * - the replacement carries `expected_version` — the version the window read or saved (ТЗ раздел 17 п. 11); the model
 *   saved in another window meanwhile — 409 `version_conflict`, asked in our own window: «Сохранить как новую» (a
 *   `POST` with the next free name) or «Перечитать» (the stored model replaces the window's), else nothing.
 *
 * No React.
 */
import { CoreError } from "../../shell/api";
import { askConfirm } from "../../shell/dialogs";
import { refusal } from "../../shell/objectRun";
import { nextName } from "../composites/api";
import { fitModel, loadModel, loadModels, replaceModel, saveModel, type FitAnswer, type FitWeights, type SaveBody } from "./api";
import { modelBody, newStructure, type StructureType, type VModel } from "./model";
import { defaultModelName, setModels, setWork, workOf, type FitInfo, type Work } from "./state";

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
  const got = await fit({ key: w.expKey, type: start.type, model: modelBody(start), weights, fix: fitFix(w, start, fixNugget), fix_nugget: fixNugget });
  setWork(win, { model: got.model, fix: got.fixed, fitInfo: fitInfoOf(got, weights), back: null, fitWeights: weights, dirty: true });
  return got;
}

/** «Модель подобрана: структур 1, невязка 39,07, точек 14. …» */
export function fitText(got: FitAnswer): string {
  const res = got.residual === null ? "" : `, невязка ${got.residual.toLocaleString("ru-RU", { maximumSignificantDigits: 4 })}`;
  return `Модель подобрана: структур ${got.model.structures.length}${res}${got.n === null ? "" : `, точек ${got.n}`}${got.warnings.length ? `. ${got.warnings.join(" ")}` : ""}`;
}

/** The body of the save: the model, the key of its points, the fit and the back-transform the window has. */
export function saveBody(w: Work, replace: boolean): SaveBody {
  if (!w.expKey) throw noResult();
  const body: SaveBody = { model: modelBody(w.model), exp_key: w.expKey };
  const fit = w.fitInfo ? { weights: w.fitInfo.weights, residual: w.fitInfo.residual, n: w.fitInfo.n, fixed: w.fitInfo.fixed } : null;
  if (fit || replace) body.fit = fit;
  if (w.back || replace) body.back = w.back ?? null;
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

export interface SaveDeps {
  ask?: typeof askConfirm;
  create?: typeof saveModel;
  replace?: typeof replaceModel;
  load?: typeof loadModel;
  refresh?: () => Promise<void>;
}

/** `POST /models`: the name asked (or the default), a taken one — the next free. */
async function createNew(w: Work, name: string | undefined, create: typeof saveModel): Promise<{ id: number; name: string }> {
  const body = saveBody(w, false);
  const want = name?.trim() || defaultModelName(w) || undefined;
  try {
    return await create({ ...(want ? { name: want } : {}), ...body });
  } catch (e) {
    if (!(e instanceof CoreError) || e.code !== "name_taken" || !want) throw e;
    return create({ name: nextName(want), ...body });
  }
}

/** «Перечитать»: the stored model, its points and version replace those of the window. */
export async function rereadWork(win: string, id: number, load: typeof loadModel = loadModel): Promise<void> {
  const m = await load(id);
  setWork(win, { model: m.model, modelId: m.id, modelVersion: m.version, result: m.points, expKey: m.points?.key ?? null, fix: [], fitInfo: null, back: null, dirty: false });
}

/** `POST` (new; a taken name — the next free one) or `PUT` of the version read (the replacement, asked in our own
 * window; 409 `version_conflict` — «Сохранить как новую» / «Перечитать»); null — cancelled or read again. */
export async function saveWork(win: string, name?: string, deps: SaveDeps = {}): Promise<{ id: number; name: string } | null> {
  const { ask = askConfirm, create = saveModel, replace = replaceModel, load = loadModel, refresh = refreshModels } = deps;
  const w = workOf(win);
  if (!w) throw refusal("no_window", "Окно «Вариография» закрыто.", "Откройте окно снова.");
  let saved: { id: number; name: string; version?: number };
  if (w.modelId !== null) {
    const id = w.modelId;
    const body = saveBody(w, true);
    if (!(await ask(replaceQuestion(id, name)))) return null;
    const expected = w.modelVersion ?? (await load(id)).version; // the version unknown to the window: the one stored now
    try {
      saved = await replace(id, { ...(name ? { name } : {}), ...body, expected_version: expected });
    } catch (e) {
      if (!(e instanceof CoreError) || e.code !== "version_conflict") throw e;
      if (await ask(conflictQuestion(id))) saved = { ...(await createNew(w, name, create)), version: 1 };
      else {
        if (await ask(rereadQuestion(id))) await rereadWork(win, id, load);
        return null;
      }
    }
  } else {
    saved = { ...(await createNew(w, name, create)), version: 1 };
  }
  setWork(win, { modelId: saved.id, modelVersion: saved.version ?? null, dirty: false });
  await refresh().catch(() => undefined);
  return { id: saved.id, name: saved.name };
}
