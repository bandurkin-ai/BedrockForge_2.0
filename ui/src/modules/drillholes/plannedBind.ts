/**
 * The handlers of the commands of the planned holes (ТЗ 7.35 Д-34 ч. 2; ADR 0026 решения 28, 29; docs/progress/e19-api.md §12) and the window they
 * open: without arguments a command opens its window, with them it executes (ADR 0021 п. 9).
 * - `planned.new` — the window «Проектная скважина» (the arguments prefill it); with the collar and the direction (or the target) — `POST /planned_holes`;
 * - `planned.open {id?}` — the window of a hole; `planned.save {as_new?}` — the form of the window is written (the buttons run this command);
 * - `planned.evaluate`, `planned.compare` — the forecast and «План / факт» (the window on its tab; with a source or a hole — the answer too);
 * - `planned.export {id?, format, path?, overwrite?}` — the files; the path is chosen in the save dialog of the application (`bridge().pickFolder` /
 *   `pickSave`, as the other exports); a file that is there is replaced after the question or with `overwrite`;
 * - `planned.delete {id}` — after the question of the graph (`askImpact`); `planned.show` — the layer of 3D; `planned.pick` — a click of the 3D window.
 * The list follows the project: read when it opens and on `implicit.changed {kind: "planned_hole"}`; another project — a clean window. With BF_E2E=1 the
 * state of the window is `window.__bfPlannedUi` (the E2E of 19.40). One app at a time (the tests create several). No React.
 */
import { createElement, type ReactNode } from "react";
import { bridge } from "../../bridge";
import { CoreError } from "../../shell/api";
import type { App } from "../../shell/app";
import type { CommandContext, CommandSource } from "../../shell/commands";
import { askConfirm } from "../../shell/dialogs";
import { needProject, needWritable, noted, refusal, viewport } from "../../shell/objectRun";
import { closeWindow, focusWindow, openWindow, windowStore, type FloatWin } from "../../shell/windows";
import { askImpact } from "../lineage/impactAsk";
import { onCoreEvent } from "../jobs/state";
import { projectStore } from "../projects/state";
import { onScene } from "../scene/sceneHost";
import { isIssue } from "../estimation/formBase";
import { deletePlanned, exportPlanned, getPlanned, refusalOf, createPlanned, type ExportFormat, type PlannedRow } from "./plannedApi";
import { bodyOf, compareLine, evaluateLine, EMPTY_FORM, exportName, formOfArgs, freeName, rowLine } from "./plannedModel";
import { pickStore, startPick, stopPick, type PickWhat } from "./plannedPick";
import {
  checkSetOf,
  choose,
  clearPlanned,
  followRows,
  followWindow,
  isPlannedEvent,
  loadPlanned,
  refreshPlanned,
  plannedCheck,
  plannedStore,
  plannedViewStore,
  plannedWin,
  PLANNED_TITLE,
  PLANNED_WINDOW,
  resetAll,
  resetWin,
  rowById,
  runCompare,
  runEvaluate,
  saveWin,
  setCheckSet,
  setHole,
  setShown,
  setSource,
  setTab,
  startEdit,
  startNew,
  type Tab,
} from "./plannedState";
import { PlannedWindow } from "./PlannedWindow";

const windowId = (): string | null => windowStore.get().windows.find((w) => w.kind === PLANNED_WINDOW)?.id ?? null;

function show(title = PLANNED_TITLE): void {
  const open = windowId();
  if (open) focusWindow(open);
  else openWindow(PLANNED_WINDOW, title, "normal", {}, ...viewport());
}

const NO_HOLE = (id: number) => refusal("planned_hole_not_found", `Проектная скважина №${id} не найдена.`, "Номера — в разделе «Проектные скважины» дерева.");
const NO_PICK = () => refusal("no_planned", "Проектная скважина не выбрана.", "Передайте id или выберите скважину в разделе «Проектные скважины» дерева.");

/** The hole a command means: the given id, else the one chosen in the tree, else the one of the window, else the only one of the project. */
export function pickHole(given: unknown): number {
  if (typeof given === "number") return given;
  const v = plannedViewStore.get().chosen;
  if (v !== null) return v;
  const w = plannedWin.get().id;
  if (w !== null && windowId()) return w;
  const rows = plannedStore.get().rows;
  if (rows.length === 1) return rows[0].id;
  throw NO_PICK();
}

/** The reason of a command that needs a planned hole: none in the project. */
export const plannedReason =
  (base: (ctx: CommandContext) => string | null) =>
  (ctx: CommandContext): string | null => {
    const r = base(ctx);
    if (r) return r;
    const st = plannedStore.get();
    return st.loaded && !st.rows.length ? "В проекте нет проектных скважин: создайте её командой «Проектная скважина»" : null;
  };

async function rowOf(id: number): Promise<PlannedRow> {
  const known = rowById(id);
  if (known) return known;
  try {
    return await getPlanned(id);
  } catch (e) {
    const r = refusalOf(e);
    throw r.code === "planned_hole_not_found" ? NO_HOLE(id) : refusal(r.code, r.message, r.hint);
  }
}

/** The window shows a form with changes the person has not saved: another hole replaces it only after the question. */
async function confirmDiscard(source: CommandSource): Promise<boolean> {
  if (!plannedWin.get().dirty || !windowId() || source !== "user") return true;
  return askConfirm({ title: PLANNED_TITLE, message: "В окне есть несохранённые изменения проектной скважины. Заменить их другой скважиной?", okLabel: "Заменить", danger: true });
}

const numbersOf = (a: Record<string, unknown>, keys: string[]): boolean => keys.every((k) => typeof a[k] === "number");

/** A command with the whole hole (the collar and the direction or the target) writes it; the rest opens the window. */
export const complete = (a: Record<string, unknown>): boolean => numbersOf(a, ["x", "y", "z"]) && (numbersOf(a, ["target_x", "target_y", "target_z"]) || numbersOf(a, ["azimuth", "dip", "length"]));

/** `planned.new {…}` with the whole hole: `POST /planned_holes`. */
export async function createFromArgs(a: Record<string, unknown>): Promise<string> {
  const f = formOfArgs(a, { ...EMPTY_FORM, name: freeName(plannedStore.get().rows.map((r) => r.name)) });
  if (isIssue(f)) throw refusal(f.code, f.message, f.hint);
  const built = bodyOf(f);
  if (!built.ok) throw refusal(built.issue.code, built.issue.message, built.issue.hint);
  try {
    const row = await createPlanned(built.body);
    await loadPlanned();
    choose(row.id);
    return `Создана проектная скважина ${rowLine(row)}.`;
  } catch (e) {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  }
}

/** `planned.new` without the whole hole: the window of a new hole, the arguments in its form. */
export async function openNew(a: Record<string, unknown>, source: CommandSource): Promise<string> {
  const open = windowId() !== null && plannedWin.get().id === null;
  const f = formOfArgs(a, open ? plannedWin.get().form : { ...EMPTY_FORM, name: freeName(plannedStore.get().rows.map((r) => r.name)) });
  if (isIssue(f)) throw refusal(f.code, f.message, f.hint);
  if (!open && !(await confirmDiscard(source))) return "Отменено";
  if (open) plannedWin.set(Object.keys(a).length > 0 ? { form: f, dirty: true } : {}); // no arguments: the form stays as it is, and is not «changed»
  else {
    startNew();
    plannedWin.set({ form: f, dirty: Object.keys(a).length > 0 });
  }
  show();
  return `Открыто окно «${PLANNED_TITLE}»: задайте устье и направление (или цель), длину и нажмите «Сохранить».`;
}

/** The window of a stored hole, on a tab. */
async function openHole(id: number, source: CommandSource, tab?: Tab): Promise<PlannedRow> {
  const row = await rowOf(id);
  const w = plannedWin.get();
  if (!(w.id === id && windowId())) {
    if (!(await confirmDiscard(source))) throw refusal("cancelled", "Отменено.", "Окно осталось как было.");
    startEdit(row);
  }
  if (tab) setTab(tab);
  choose(id);
  show();
  return row;
}

/** `planned.open {id?}`. */
export async function runOpen(a: Record<string, unknown>, source: CommandSource): Promise<string> {
  try {
    const row = await openHole(pickHole(a.id), source);
    return `Открыто окно проектной скважины ${rowLine(row)}.`;
  } catch (e) {
    if (e instanceof CoreError && e.code === "cancelled") return "Отменено";
    throw e;
  }
}

/** `planned.save {as_new?}`. */
export async function runSave(a: Record<string, unknown>): Promise<string> {
  if (!windowId()) throw refusal("no_window", "Нет открытой формы проектной скважины: сохранять нечего.", "Откройте окно командой «Проектная скважина» (planned.new) или скважину из раздела «Проектные скважины» дерева.");
  const row = await saveWin({ asNew: a.as_new === true });
  choose(row.id);
  return `Сохранено: ${rowLine(row)}.`;
}

const idsOf = (v: unknown): number[] =>
  String(v ?? "")
    .split(/[;,\s]+/)
    .map(Number)
    .filter((n) => Number.isInteger(n) && n > 0);

const failure = (text: string | null, dflt: string) => refusal("check_failed", text ?? dflt, "Исправьте параметры и повторите.");

/** `planned.evaluate {id?, model_id?, mesh_ids?, set_id?}`. */
export async function runEvaluateCommand(a: Record<string, unknown>, source: CommandSource): Promise<string> {
  const id = pickHole(a.id);
  let row: PlannedRow;
  try {
    row = await openHole(id, source, "forecast");
  } catch (e) {
    if (e instanceof CoreError && e.code === "cancelled") return "Отменено";
    throw e;
  }
  const meshes = idsOf(a.mesh_ids);
  const asked = typeof a.model_id === "number" || meshes.length > 0 || typeof a.set_id === "number";
  const cur = plannedCheck.get();
  if (typeof a.set_id === "number" && a.set_id !== checkSetOf(cur, plannedWin.get().form)) setCheckSet(a.set_id); // the same set: the hole of «План / факт» stays
  // the arguments are the request: no bodies named — only the nearest holes (what the window had chosen before stays for its own «Прогноз»)
  if (typeof a.model_id === "number") setSource({ kind: "model", modelId: a.model_id });
  else if (meshes.length) setSource({ kind: "meshes", meshIds: meshes });
  else if (asked) setSource({ kind: "none" });
  if (!asked) return `Открыта вкладка «Прогноз» проектной скважины «${row.name}»: выберите модель или каркасы и нажмите «Прогноз».`;
  const answer = await runEvaluate();
  if (!answer) throw failure(plannedCheck.get().evaluate.error, "Прогноз не получен.");
  return evaluateLine(answer, row.name);
}

/** `planned.compare {id?, set_id?, hole?}`. */
export async function runCompareCommand(a: Record<string, unknown>, source: CommandSource): Promise<string> {
  const id = pickHole(a.id);
  let row: PlannedRow;
  try {
    row = await openHole(id, source, "compare");
  } catch (e) {
    if (e instanceof CoreError && e.code === "cancelled") return "Отменено";
    throw e;
  }
  if (typeof a.set_id === "number") setCheckSet(a.set_id);
  if (typeof a.hole === "string" && a.hole.trim()) setHole(a.hole.trim());
  if (typeof a.hole !== "string" || !a.hole.trim()) {
    if (!plannedCheck.get().hole) return `Открыта вкладка «План / факт» проектной скважины «${row.name}»: выберите набор и фактическую скважину и нажмите «Сравнить».`;
  }
  const answer = await runCompare();
  if (!answer) throw failure(plannedCheck.get().compare.error, "План / факт не получен.");
  return compareLine(answer, row.name);
}

/** The path of an export: the one given, or the choice of the person in the save dialog of the application; null — cancelled. */
async function pathOf(a: Record<string, unknown>, format: ExportFormat, name: string): Promise<string | null> {
  if (typeof a.path === "string" && a.path.trim()) return a.path.trim();
  const b = bridge();
  if (!b) throw refusal("no_path", "Не указан путь: диалог выбора доступен только в приложении.", format === "csv" ? "Передайте path — полный путь существующей папки." : "Передайте path — полный путь файла .dxf.");
  return format === "csv" ? b.pickFolder() : b.pickSave(exportName(name, "dxf"), "dxf");
}

/** `planned.export {id?, format, path?, overwrite?}`. */
export async function runExport(a: Record<string, unknown>, source: CommandSource): Promise<string> {
  const format: ExportFormat = a.format === "dxf" ? "dxf" : "csv";
  const id = pickHole(a.id);
  const row = await rowOf(id);
  const path = await pathOf(a, format, row.name);
  if (!path) return "Отменено";
  const send = (overwrite: boolean) => exportPlanned(id, { format, path, ...(overwrite ? { overwrite: true } : {}) });
  let overwrite = a.overwrite === true;
  for (;;) {
    try {
      const r = await send(overwrite);
      const text = `Проектная скважина «${row.name}» выгружена (${format === "csv" ? "CSV" : "DXF"}): ${r.files.join("; ")}, точек ${r.points}, ${r.size.toLocaleString("ru-RU")} Б.`;
      if (plannedWin.get().id === id) plannedWin.set({ saved: text });
      return text;
    } catch (e) {
      if (e instanceof CoreError && e.code === "files_exist" && !overwrite) {
        if (source !== "user") throw refusal("files_exist", e.message, "Передайте overwrite=true, чтобы заменить файлы, или выберите другую папку.");
        if (!(await askConfirm({ title: "Экспорт проектной скважины", message: `${e.message} Заменить?`, okLabel: "Заменить", danger: true }))) return "Отменено";
        overwrite = true;
        continue;
      }
      const r = refusalOf(e);
      throw refusal(r.code, r.message, r.hint);
    }
  }
}

/** `planned.delete {id}`: the question, the question of the graph, the deletion. */
export async function runDelete(a: Record<string, unknown>, source: CommandSource): Promise<string> {
  const id = a.id as number;
  const row = rowById(id);
  if (!row && plannedStore.get().loaded) throw NO_HOLE(id);
  const name = row?.name ?? `№${id}`;
  if (source === "user" && !(await askConfirm({ title: "Удалить проектную скважину", message: `Удалить проектную скважину «${name}»? Набор скважин и его данные не затрагиваются.`, okLabel: "Удалить", danger: true }))) return "Отменено";
  if (!(await askImpact({ kind: "object:planned_hole", id }, `удалить проектную скважину «${name}»`))) return "Отменено";
  try {
    await deletePlanned(id);
    return `Проектная скважина «${name}» удалена.`;
  } catch (e) {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  } finally {
    await loadPlanned();
  }
}

/** `planned.show {id?, visible?}`: the layer of 3D (the hole stays in the project). */
export function runShow(a: Record<string, unknown>): string {
  const on = a.visible !== false;
  const rows = plannedStore.get().rows;
  if (typeof a.id === "number") {
    const row = rowById(a.id);
    if (!row && plannedStore.get().loaded) throw NO_HOLE(a.id);
    setShown(a.id, on);
    return `Проектная скважина «${row?.name ?? `№${a.id}`}» ${on ? "показана в 3D и в разрезах" : "скрыта в 3D и в разрезах"}.`;
  }
  rows.forEach((r) => setShown(r.id, on));
  return `Проектные скважины (${rows.length}) ${on ? "показаны" : "скрыты"} в 3D и в разрезах.`;
}

/** `planned.pick {what}` (the window must be open: the point goes into its form). */
export function runPick(a: Record<string, unknown>): string {
  if (!windowId()) throw refusal("no_window", "Нет открытого окна проектной скважины.", "Откройте окно командой «Проектная скважина» и повторите.");
  const what = a.what === "target" ? "target" : "collar";
  startPick(what as PickWhat, () => plannedWin.get().form[what][2]);
  return pickStore.get().note ?? "Щёлкните по 3D-окну.";
}

// ---- the registry ----

let unbind: (() => void)[] = [];

/** With BF_E2E=1 the state of the window and of the stores is readable as `window.__bfPlannedUi` (the E2E of 19.40). */
function e2eHook(): () => void {
  const on = bridge()?.flags?.().e2e === true && typeof window !== "undefined";
  if (!on) return () => undefined;
  window.__bfPlannedUi = {
    list: () => plannedStore.get().rows.map((r) => ({ id: r.id, name: r.name, version: r.version, path: r.trajectory?.depth.length ?? 0, target: r.meta.target })),
    window: () => {
      const s = plannedWin.get();
      return { id: s.id, version: s.version, tab: s.tab, dirty: s.dirty, saved: s.saved, issue: s.issue?.code ?? null, form: s.form };
    },
    check: () => {
      const c = plannedCheck.get();
      return { evaluate: c.evaluate.phase, intervals: c.evaluate.answer?.intervals?.length ?? null, nearest: c.evaluate.answer?.nearest?.length ?? null, compare: c.compare.phase, max: c.compare.answer?.max ?? null, rows: c.compare.answer?.rows.length ?? null };
    },
    view: () => plannedViewStore.get(),
  };
  return () => {
    if (typeof window !== "undefined") window.__bfPlannedUi = undefined;
  };
}

declare global {
  interface Window {
    __bfPlannedUi?: unknown;
  }
}

export function bindPlanned(app: App): void {
  unbind.forEach((fn) => fn());
  unbind = [];
  const writable = needWritable(app);
  const have = plannedReason(needProject);
  app.registry.bind("planned.new", noted(app, (a, s) => (complete(a) ? createFromArgs(a) : openNew(a, s))), writable);
  app.registry.bind("planned.open", noted(app, (a, s) => runOpen(a, s)), have);
  app.registry.bind("planned.save", noted(app, (a) => runSave(a)), writable);
  app.registry.bind("planned.evaluate", noted(app, (a, s) => runEvaluateCommand(a, s)), have); // reads only: a project for viewing may ask too
  app.registry.bind("planned.compare", noted(app, (a, s) => runCompareCommand(a, s)), have);
  app.registry.bind("planned.export", noted(app, (a, s) => runExport(a, s)), have);
  app.registry.bind("planned.delete", noted(app, (a, s) => runDelete(a, s)), plannedReason(writable));
  app.registry.bind("planned.show", noted(app, (a) => runShow(a)), have);
  app.registry.bind("planned.pick", noted(app, (a) => runPick(a)), writable);
  let uid: string | null = projectStore.get().current?.uid ?? null;
  if (uid) void loadPlanned();
  unbind.push(
    e2eHook(),
    bindPlannedScene(),
    // what was asked of one project does not belong to another
    projectStore.subscribe(() => {
      const cur = projectStore.get().current?.uid ?? null;
      if (cur === uid) return;
      uid = cur;
      stopPick();
      const open = windowId();
      if (open) closeWindow(open);
      clearPlanned();
      plannedViewStore.set({ hidden: [], chosen: null });
      resetWin();
      resetAll();
      if (cur) void loadPlanned();
    }),
    onCoreEvent((event, data) => {
      if (event === "implicit.changed" && projectStore.get().current && isPlannedEvent(data)) void refreshPlanned(data);
    }),
    // a hole that is gone leaves the view; the window of a hole that is gone says so
    plannedStore.subscribe(() => {
      followRows();
      followWindow();
    }),
  );
}

/** The layer of the planned holes in the main 3D: a lazy chunk with three.js, attached to every new scene. */
export function bindPlannedScene(): () => void {
  let detach: (() => void) | null = null;
  let generation = 0;
  const off = onScene((scene) => {
    const mine = ++generation;
    detach?.();
    detach = null;
    if (!scene) return;
    void import("./plannedScene").then((m) => {
      if (mine === generation) detach = m.attachPlannedScene(scene);
    });
  });
  return () => {
    generation++;
    detach?.();
    detach = null;
    off();
  };
}

/** The window for the shell's window layer (`shell/Shell.tsx`); null — not a window of the planned holes. */
export function renderPlannedWindow(w: FloatWin): ReactNode | null {
  return w.kind === PLANNED_WINDOW ? createElement(PlannedWindow, { winId: w.id }) : null;
}

