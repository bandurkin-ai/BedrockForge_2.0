/**
 * Handlers of `variography.*` (ADR 0022 §13): without the required arguments a command opens the window
 * «Вариография · ‹поле›» (`variography`) prefilled with what is given; with them — the values are checked first
 * against 8.6.4 (out of range never reaches the core), the tables named by the assistant are resolved, then the job
 * or the request goes. `fit` and `save` act on the working model of a window: the one of `model_id`, else the last
 * active window. The replacement of a stored model and the delete ask in our own window (`askConfirm`); the
 * registry asks the assistant only for `delete` (its flag), so the replacement asks it here too.
 * `variography.ellipsoid`, `variography.show` — the ellipsoid of the window of a model in 3D (`scene.ts`, a lazy chunk
 * attached to every scene; a stored model not open is opened in a window first: its handles edit that window).
 */
import type { App } from "../../shell/app";
import { CoreError } from "../../shell/api";
import type { CommandSource } from "../../shell/commands";
import { askConfirm } from "../../shell/dialogs";
import { needProject, needWritable, noted, refusal, showJobs, viewport } from "../../shell/objectRun";
import { focusWindow, openWindow, windowStore } from "../../shell/windows";
import { setPanel } from "../../shell/workspace";
import { onScene } from "../scene/sceneHost";
import { refresh as refreshJobs } from "../jobs/state";
import { loadTables, nextName, resolveTable, type Started, type TableRow } from "../composites/api";
import { deleteModel, exportModel, fitModel, formatOfPath, importModel, loadModel, loadModels, replaceModel, saveModel, startExperimental, startMap, type ExchangeFormat, type FitWeights } from "./api";
import { modelBody, newStructure, structureType, type StructureType, type VModel } from "./model";
import {
  checkValue,
  chosenStructure,
  dataFromArgs,
  defaultModelName,
  ellipsoidCentre,
  experimentalBody,
  forgetModel,
  mapBody,
  markEdit,
  newWork,
  setEllipsoid,
  setModel,
  setModels,
  setWork,
  valuesFromArgs,
  VIEWS,
  windowOfModel,
  withStructure,
  workOf,
  type DataState,
  type StructurePatch,
  type VarioView,
  type Work,
} from "./state";

export const VARIO_WINDOW = "variography";

const HINT_RANGE = "Диапазоны — в описании команды (ТЗ 8.6.4); исправьте значение и повторите.";

const given = (a: Record<string, unknown>, k: string) => a[k] !== undefined && a[k] !== null && (typeof a[k] !== "string" || (a[k] as string).trim() !== "");

export const windowTitle = (field: string | null | undefined): string => (field ? `Вариография · ${field}` : "Вариография");

/** Opens a window «Вариография» and gives it its working state (kept when the same window is open already). */
export function openVariography(args: Record<string, unknown> = {}, work: Partial<Work> = {}): string {
  const props = Object.fromEntries(Object.entries(args).filter(([, v]) => v !== undefined && v !== null));
  const data = work.data ?? dataFromArgs(args);
  const id = openWindow(VARIO_WINDOW, windowTitle(data.field), "normal", Object.keys(props).length ? { args: props } : undefined, ...viewport());
  if (!workOf(id)) {
    const view = VIEWS.includes(args.view as VarioView) ? (args.view as VarioView) : "axes";
    setWork(id, newWork({ data, values: valuesFromArgs(args), view, ...work }));
  }
  return id;
}

/** The last active window «Вариография» that has a working state. */
export function lastWindow(): string | null {
  const wins = windowStore.get().windows.filter((w) => w.kind === VARIO_WINDOW && workOf(w.id));
  return wins.length ? wins.reduce((a, b) => (b.z > a.z ? b : a)).id : null;
}

function windowFor(a: Record<string, unknown>): string {
  const win = typeof a.model_id === "number" ? windowOfModel(a.model_id) : lastWindow();
  if (win) return win;
  if (typeof a.model_id === "number") throw refusal("no_window", `Модель №${a.model_id} не открыта в окне «Вариография».`, "Откройте её: variography.open с model_id.");
  throw refusal("no_window", "Нет открытого окна «Вариография».", "Откройте окно и рассчитайте вариограммы осей.");
}

const noResult = () => refusal("no_result", "Сначала рассчитайте вариограммы осей.", "Кнопка «Рассчитать» в окне или команда variography.calc.");

function key(tables: readonly TableRow[], name: string, what: string): string {
  const k = resolveTable(tables, name);
  if (k === null) throw refusal("table_not_found", `${what} «${name}» не найдена среди интервальных таблиц набора.`, "Имена таблиц — в дереве набора скважин.");
  return k;
}

/**
 * The body of `variography.calc` from its arguments: checked with the tables as given, then the tables are
 * resolved by name and the body is built again. `what` axes → `/experimental`, fan / map → `/map`.
 */
export async function calcBody(a: Record<string, unknown>): Promise<{ what: "axes" | "fan" | "map"; body: Record<string, unknown> }> {
  const what = a.what === "fan" || a.what === "map" ? a.what : "axes";
  const values = valuesFromArgs(a);
  const angles = { ang1: Number(a.ang1 ?? 0), ang2: Number(a.ang2 ?? 0), ang3: Number(a.ang3 ?? 0) };
  const build = (d: DataState) => (what === "axes" ? experimentalBody(d, values, { axes: angles }) : mapBody(d, values, angles, [what]));
  const data = dataFromArgs(a);
  const first = build(data);
  if (!first.body) throw refusal("bad_param", first.reason ?? "Параметры не заданы.", HINT_RANGE);
  const tables = await loadTables(data.setId!);
  const resolved: DataState = { ...data, table: key(tables, data.table!, "Таблица") };
  if (data.domain?.table) resolved.domain = { ...data.domain, table: key(tables, data.domain.table, "Таблица домена") };
  const r = build(resolved);
  if (!r.body) throw refusal("bad_param", r.reason ?? "Параметры не заданы.", HINT_RANGE);
  return { what, body: r.body };
}

/** The model of `fit`: the types of `structures` (by position; new ones nested), or one spherical when there is none. */
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

function parseTypes(raw: unknown): StructureType[] | undefined {
  if (typeof raw !== "string" || !raw.trim()) return undefined;
  const list = raw.split(/[,;\s]+/).filter(Boolean);
  const types = list.map(structureType);
  if (!types.length || types.length > 3 || types.some((t) => t === null)) throw refusal("bad_param", `Структуры «${raw}» заданы неверно.`, "До трёх типов через запятую: spherical, exponential, gaussian.");
  return types as StructureType[];
}

const started = async (app: App, what: string, s: Started): Promise<string> => {
  showJobs(app);
  await refreshJobs().catch(() => undefined);
  return `«${what}»: задание №${s.id} поставлено в очередь — ход на вкладке «Задания»`;
};

export async function refreshModels(): Promise<void> {
  setModels(await loadModels());
}

/** `POST` (new; a taken name — the next free one) or `PUT` (the replacement, asked in our own window). */
export async function saveWork(win: string, name?: string): Promise<{ id: number; name: string } | null> {
  const w = workOf(win);
  if (!w) throw refusal("no_window", "Окно «Вариография» закрыто.", "Откройте окно снова.");
  if (!w.expKey) throw noResult();
  const body = { model: modelBody(w.model), exp_key: w.expKey };
  let saved: { id: number; name: string };
  if (w.modelId !== null) {
    const ok = await askConfirm({ title: "Заменить модель", message: `Заменить сохранённую модель вариограммы №${w.modelId}${name ? ` («${name}»)` : ""} рабочей моделью окна?`, okLabel: "Заменить", danger: true });
    if (!ok) return null;
    saved = await replaceModel(w.modelId, { ...(name ? { name } : {}), ...body });
  } else {
    const want = name?.trim() || defaultModelName(w) || undefined;
    try {
      saved = await saveModel({ ...(want ? { name: want } : {}), ...body });
    } catch (e) {
      if (!(e instanceof CoreError) || e.code !== "name_taken" || !want) throw e;
      saved = await saveModel({ name: nextName(want), ...body });
    }
  }
  setWork(win, { modelId: saved.id, dirty: false });
  await refreshModels().catch(() => undefined);
  return saved;
}

const modelId = (a: Record<string, unknown>): number => {
  if (typeof a.model_id !== "number") throw refusal("model_not_found", "Не задана модель вариограммы.", "Номер модели — в дереве «Геостатистика → Модели вариограмм».");
  return a.model_id;
};

/** The window of a stored model: the one open, else a new one read from the core (`read` — the model read then). */
export async function modelWindow(id: number, view?: unknown): Promise<{ win: string; read: Awaited<ReturnType<typeof loadModel>> | null }> {
  const open = windowOfModel(id);
  if (open) return { win: open, read: null };
  const m = await loadModel(id);
  const data: DataState = { ...dataFromArgs({}), setId: m.source.set_id, table: m.source.table, field: m.source.field, transform: m.model.type === "nscore" ? "nscore" : "none" };
  const win = openVariography({ model_id: m.id, view }, { data, model: m.model, modelId: m.id, result: m.points, expKey: m.points?.key ?? null });
  return { win, read: m };
}

/** The angles and ranges of `variography.ellipsoid`, checked (8.6.4; a range — a positive number of metres). */
export function ellipsoidPatch(a: Record<string, unknown>): StructurePatch {
  const out: StructurePatch = {};
  for (const k of ["ang1", "ang2", "ang3"] as const) {
    if (!given(a, k)) continue;
    const r = checkValue(k, String(a[k]).replace(".", ","));
    if (!r.ok || typeof r.value !== "number") throw refusal("bad_param", r.ok ? `${k}: нужно число` : r.error, HINT_RANGE);
    out[k] = k === "ang1" && r.value === 360 ? 0 : r.value;
  }
  for (const k of ["a1", "a2", "a3"] as const) {
    if (!given(a, k)) continue;
    const v = Number(a[k]);
    if (!Number.isFinite(v) || v <= 0 || v > 100_000) throw refusal("bad_param", `Дальность ${k}: нужно число больше 0 до 100 000 м`, HINT_RANGE);
    out[k] = v;
  }
  return out;
}

const noStructures = (id: number) => refusal("no_structures", `В модели №${id} нет структур — эллипсоид не построить.`, "Добавьте структуру в таблице модели или подберите модель кнопкой «Подобрать».");

const centreNote = (win: string): string => (ellipsoidCentre(workOf(win)!) ? "" : " — появится, когда окно прочитает данные расчёта (центр — середина охвата точек)");

export function bindVariography(app: App): void {
  const r = app.registry;
  r.bind(
    "variography.open",
    noted(app, async (a) => {
      if (typeof a.model_id === "number") {
        const { win, read: m } = await modelWindow(a.model_id, a.view);
        if (!m) {
          focusWindow(win);
          return `Окно модели №${a.model_id} уже открыто`;
        }
        return `Открыта модель «${m.name}»${m.stale ? ` — устарела: ${m.stale_reason ?? "изменены данные"}` : ""}`;
      }
      openVariography(a);
      return `Открыто окно «${windowTitle(typeof a.field === "string" ? a.field : null)}»`;
    }),
    needProject,
  );
  r.bind("variography.models", () => {
    app.store.set((s) => ({ workspace: setPanel(s.workspace, "project", true) }));
    return "Модели вариограмм — в дереве проекта, раздел «Геостатистика»";
  }, needProject);
  r.bind(
    "variography.calc",
    noted(app, async (a, source: CommandSource) => {
      if (!["set_id", "table", "field"].every((k) => given(a, k))) {
        openVariography(a);
        return `Открыто окно «Вариография»${source === "claude" ? ": заданы не все обязательные параметры — set_id, table, field" : ""}`;
      }
      const { what, body } = await calcBody(a);
      return started(app, what === "axes" ? "Вариограммы осей" : what === "fan" ? "Веер" : "Карта вариограмм", what === "axes" ? await startExperimental(body) : await startMap(body));
    }),
    needProject,
  );
  r.bind(
    "variography.fit",
    noted(app, async (a) => {
      const win = windowFor(a);
      const w = workOf(win)!;
      if (!w.expKey) throw noResult();
      const start = fitStart(w, parseTypes(a.structures));
      const weights: FitWeights = a.weights === "n" || a.weights === "n_g2" ? a.weights : "n_h";
      const fixNugget = a.fix_nugget === true;
      const fix = [...new Set([...w.fix, ...(fixNugget ? ["c0"] : [])])];
      const got = await fitModel({ key: w.expKey, type: start.type, model: modelBody(start), weights, fix, fix_nugget: fixNugget });
      setModel(win, got.model);
      const res = got.residual === null ? "" : `, невязка ${got.residual.toLocaleString("ru-RU", { maximumSignificantDigits: 4 })}`;
      return `Модель подобрана: структур ${got.model.structures.length}${res}${got.n === null ? "" : `, точек ${got.n}`}${got.warnings.length ? `. ${got.warnings.join(" ")}` : ""}`;
    }),
    needProject,
  );
  r.bind(
    "variography.ellipsoid",
    noted(app, async (a) => {
      const id = modelId(a);
      const patch = ellipsoidPatch(a);
      const { win } = await modelWindow(id);
      const w = workOf(win)!;
      const n = w.model.structures.length;
      if (!n) throw noStructures(id);
      let structure = w.ellipsoid?.structure ?? null;
      if (given(a, "structure")) {
        const k = Number(a.structure);
        if (!Number.isInteger(k) || k < 1 || k > n) throw refusal("bad_param", `Структуры №${String(a.structure)} нет в модели №${id}.`, `Номер структуры — от 1 до ${n}.`);
        structure = k - 1;
      }
      const index = chosenStructure({ model: w.model, ellipsoid: { visible: true, centre: null, structure } });
      if (Object.keys(patch).length) {
        markEdit();
        setModel(win, withStructure(w.model, index, patch));
      }
      setEllipsoid(win, { visible: true, structure });
      return `Эллипсоид модели №${id} в 3D: структура ${index + 1} из ${n}${Object.keys(patch).length ? ", заданные углы и дальности — в таблице модели" : ""}${centreNote(win)}`;
    }),
    needProject,
  );
  r.bind(
    "variography.show",
    noted(app, async (a) => {
      const id = modelId(a);
      if (a.visible === false) {
        const open = windowOfModel(id);
        if (open && workOf(open)?.ellipsoid) setEllipsoid(open, { visible: false });
        return `Эллипсоид модели №${id} скрыт`;
      }
      const { win } = await modelWindow(id);
      if (!workOf(win)!.model.structures.length) throw noStructures(id);
      setEllipsoid(win, { visible: true });
      return `Эллипсоид модели №${id} показан в 3D${centreNote(win)}`;
    }),
    needProject,
  );
  r.bind(
    "variography.save",
    noted(app, async (a) => {
      const s = await saveWork(windowFor(a), typeof a.name === "string" ? a.name : undefined);
      return s ? `Модель вариограммы «${s.name}» сохранена (№${s.id})` : "Отменено";
    }),
    needWritable(app),
  );
  r.bind(
    "variography.delete",
    noted(app, async (a, source) => {
      const id = modelId(a);
      if (source === "user" && !(await askConfirm({ title: "Удалить модель", message: `Удалить модель вариограммы №${id} с её экспериментальными точками?`, okLabel: "Удалить", danger: true }))) return "Отменено";
      await deleteModel(id);
      forgetModel(id);
      return `Модель вариограммы №${id} удалена`;
    }),
    needWritable(app),
  );
  r.bind(
    "variography.export",
    noted(app, async (a) => {
      const id = modelId(a);
      const path = typeof a.path === "string" && a.path.trim() ? a.path.trim() : undefined;
      const format: ExchangeFormat = a.format === "json" || a.format === "gslib" ? a.format : path ? formatOfPath(path) : "json";
      const got = await exportModel(id, format, path);
      if (path) return `Модель №${id} записана: ${got.path ?? path}${typeof got.bytes === "number" ? ` (${got.bytes} байт)` : ""}`;
      const text = got.text ?? "";
      return `${got.filename ?? `модель-${id}.${format === "json" ? "json" : "txt"}`}:\n${text.length > 4000 ? `${text.slice(0, 4000)}…` : text}`;
    }),
    needProject,
  );
  r.bind(
    "variography.import",
    noted(app, async (a) => {
      if (typeof a.path !== "string" || !a.path.trim()) throw refusal("bad_param", "Не задан файл модели.", "Укажите path — абсолютный путь файла JSON или GSLIB.");
      const path = a.path.trim();
      const format: ExchangeFormat = a.format === "json" || a.format === "gslib" ? a.format : formatOfPath(path);
      const model = await importModel({ format, path });
      openVariography({ import: path }, { model, dirty: true });
      return `Модель из «${path}» открыта рабочей моделью окна: структур ${model.structures.length} — сохраните кнопкой «Сохранить»`;
    }),
    needProject,
  );
  // the ellipsoids in the main 3D: a lazy chunk with three.js, attached to every new scene
  let detach: (() => void) | null = null;
  let generation = 0;
  onScene((scene) => {
    const mine = ++generation;
    detach?.();
    detach = null;
    if (!scene) return;
    void import("./scene").then((m) => {
      if (mine === generation) detach = m.attachVarioScene(scene);
    });
  });
}
