/**
 * The handlers of `estimate.kna`, `estimate.global`, `estimate.qq`, `estimate.swath` and the windows «Анализ окрестности» and «Глобальная
 * проверка оценки» (ТЗ 8.8.5; docs/progress/e18-api.md §8.2–§8.4, §12). Without a row a command opens its window (the only suitable row of
 * the project is taken); with `estimation_id` it opens the window and runs the check, the answer is the words about the result (for the
 * assistant). The checks write nothing, so they run on a project open for reading too. `estimate.swath` opens the window of Э13 with the
 * settings the core gives (unsaved: the person saves the swath plot there). `bindChecks` is called by `bindEstimation` (`bind.ts`).
 */
import { createElement, type ReactNode } from "react";
import type { App } from "../../shell/app";
import { needProject, noted, refusal, viewport } from "../../shell/objectRun";
import { focusWindow, openWindow, windowStore, type FloatWin } from "../../shell/windows";
import { projectStore } from "../projects/state";
import { openSwathWindow } from "../swath/bind";
import { KNA_PARAMS, loadSwathSettings, type KnaParam } from "./checksApi";
import { refusalOf } from "./api";
import { GLOBAL_TITLE, GLOBAL_WINDOW, checkedRows, globalStore, resetGlobal, runGlobal, runQq, setGlobalRow } from "./globalState";
import { GlobalWindow } from "./GlobalWindow";
import { KNA_METHOD_HINT, KNA_METHOD_MESSAGE, KNA_TITLE, KNA_WINDOW, knaRows, knaStore, parseBlock, resetKna, runKna, setKnaRow, type BlockPick, type PickKind } from "./knaState";
import { KnaWindow } from "./KnaWindow";
import { estimationStore } from "./state";

const notFound = (id: number) => refusal("estimation_not_found", `Строка «Параметры оценки» №${id} не найдена.`, "Номера строк — в разделе «Геостатистика → Параметры оценки» дерева.");

/** The window of a kind comes to the front, or opens. */
function open(kind: string, title: string): void {
  const have = windowStore.get().windows.find((w) => w.kind === kind);
  if (have) focusWindow(have.id);
  else openWindow(kind, title, "normal", {}, ...viewport());
}

const knownRow = (id: number) => {
  const row = estimationStore.get().rows.find((r) => r.id === id);
  if (!row && estimationStore.get().loaded) throw notFound(id);
  return row;
};

/** `estimation_id` of the arguments, checked against the list of the rows; null — not given. */
function idOf(a: Record<string, unknown>): number | null {
  if (a.estimation_id === undefined || a.estimation_id === null) return null;
  if (typeof a.estimation_id !== "number" || !Number.isInteger(a.estimation_id) || a.estimation_id < 1) throw refusal("bad_param", "Номер строки оценки: нужно целое число.", "Номера строк — в разделе «Геостатистика → Параметры оценки» дерева.");
  return a.estimation_id;
}

// ---- KNA ----

/** «n_max; radius» → the parameters in the order of the core. */
export function varyOf(text: unknown): KnaParam[] {
  const names = String(text)
    .split(/[;,\s]+/)
    .filter(Boolean);
  const bad = names.find((n) => !(KNA_PARAMS as readonly string[]).includes(n));
  if (bad || !names.length) throw refusal("bad_param", bad ? `Неизвестный параметр перебора «${bad.slice(0, 20)}».` : "Не выбраны параметры для перебора.", "Допустимо: n_max, radius, disc, size.");
  return KNA_PARAMS.filter((p) => names.includes(p));
}

/** `block` («4; 4; 7», several — through «|») or `xyz` of the command → the blocks of the person. */
function picksOf(a: Record<string, unknown>): BlockPick[] | null {
  const out: BlockPick[] = [];
  for (const [key, kind] of [["block", "ijk"], ["xyz", "xyz"]] as [string, PickKind][]) {
    if (a[key] === undefined) continue;
    for (const part of String(a[key]).split("|").filter((x) => x.trim())) {
      const got = parseBlock(kind, part);
      if (!got.ok) throw refusal("bad_param", got.message, "Блоки через «|», числа через «;».");
      out.push({ kind, text: part.trim(), at: got.value });
    }
  }
  return out.length ? out : null;
}

export async function runKnaCommand(a: Record<string, unknown>): Promise<string> {
  const id = idOf(a);
  if (id !== null) {
    const row = knownRow(id);
    if (row && row.method !== "ok" && row.method !== "sk") throw refusal("kna_method", KNA_METHOD_MESSAGE, KNA_METHOD_HINT);
  }
  const vary = a.vary === undefined ? null : varyOf(a.vary);
  const picks = picksOf(a);
  if (knaStore.get().phase === "running" && (id !== null || vary || picks)) throw refusal("busy", "Анализ окрестности уже выполняется.", "Дождитесь конца или отмените его в окне.");
  if (id !== null) setKnaRow(id);
  else if (knaStore.get().rowId === null) {
    const only = knaRows();
    if (only.length === 1) setKnaRow(only[0].id);
  }
  if (vary) knaStore.set({ vary, shown: vary.includes(knaStore.get().shown) ? knaStore.get().shown : vary[0] });
  if (picks) knaStore.set({ picks: picks.slice(0, 10), mode: "own" });
  open(KNA_WINDOW, KNA_TITLE);
  return id === null ? `Открыто окно «${KNA_TITLE}»` : runKna();
}

// ---- the global check, Q-Q ----

function globalRow(a: Record<string, unknown>): void {
  const id = idOf(a);
  if (id !== null) {
    knownRow(id);
    if (globalStore.get().phase === "running") throw refusal("busy", "Глобальная проверка уже выполняется.", "Дождитесь конца или отмените её в окне.");
    setGlobalRow(id);
  } else if (globalStore.get().rowId === null) {
    const only = checkedRows();
    if (only.length === 1) setGlobalRow(only[0].id);
  }
}

export async function runGlobalCommand(a: Record<string, unknown>): Promise<string> {
  globalRow(a);
  open(GLOBAL_WINDOW, GLOBAL_TITLE);
  return idOf(a) === null ? `Открыто окно «${GLOBAL_TITLE}»` : runGlobal();
}

export async function runQqCommand(a: Record<string, unknown>): Promise<string> {
  globalRow(a);
  open(GLOBAL_WINDOW, GLOBAL_TITLE);
  return idOf(a) === null ? `Открыто окно «${GLOBAL_TITLE}»` : runQq();
}

// ---- swath ----

/** The row of `estimate.swath`: the argument, else the row of the open window of a check, else the only executed row of the project. */
function swathRow(a: Record<string, unknown>): number {
  const id = idOf(a);
  if (id !== null) {
    knownRow(id);
    return id;
  }
  const have = globalStore.get().rowId ?? knaStore.get().rowId;
  if (have !== null) return have;
  const only = checkedRows();
  if (only.length === 1) return only[0].id;
  throw refusal("bad_param", "Не выбрана строка оценки.", "Укажите estimation_id: номер выполненной строки «Параметры оценки».");
}

export async function runSwathCommand(app: App, a: Record<string, unknown>): Promise<string> {
  const id = swathRow(a);
  const direction = a.direction === undefined ? undefined : a.direction;
  if (direction !== undefined && direction !== "x" && direction !== "y" && direction !== "z") throw refusal("bad_param", "Неизвестное направление полос.", "Выберите x, y или z.");
  let got;
  try {
    got = await loadSwathSettings(id, direction);
  } catch (e) {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  }
  openSwathWindow(null, got.settings, { readOnly: app.store.get().projectReadOnly });
  return `Открыт swath plot оценки «${got.name}»: оценка блоков и композиты домена по полосам; сохраните его в окне, если он нужен в проекте`;
}

// ---- the registry, the windows ----

/** The commands and what the windows follow (the project, the rows); the returned function takes them off. */
export function bindChecks(app: App): () => void {
  app.registry.bind("estimate.kna", noted(app, (a) => runKnaCommand(a)), needProject);
  app.registry.bind("estimate.global", noted(app, (a) => runGlobalCommand(a)), needProject);
  app.registry.bind("estimate.qq", noted(app, (a) => runQqCommand(a)), needProject);
  app.registry.bind("estimate.swath", noted(app, (a) => runSwathCommand(app, a)), needProject);
  let uid: string | null = projectStore.get().current?.uid ?? null;
  const offProject = projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    resetKna();
    resetGlobal();
  });
  // a row that is gone leaves the windows
  const offRows = estimationStore.subscribe(() => {
    if (!estimationStore.get().loaded) return;
    const has = (id: number | null) => id === null || estimationStore.get().rows.some((r) => r.id === id);
    if (!has(knaStore.get().rowId)) setKnaRow(null);
    if (!has(globalStore.get().rowId)) setGlobalRow(null);
  });
  return () => {
    offProject();
    offRows();
  };
}

/** The windows of the checks for the window layer of the shell; null — not a window of them. */
export function renderCheckWindow(w: FloatWin): ReactNode | null {
  if (w.kind === KNA_WINDOW) return createElement(KnaWindow, { winId: w.id });
  return w.kind === GLOBAL_WINDOW ? createElement(GlobalWindow, { winId: w.id }) : null;
}
