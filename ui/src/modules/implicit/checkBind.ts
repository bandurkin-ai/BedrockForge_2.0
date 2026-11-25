/**
 * The handlers of the commands of the check of an implicit model against its data (ТЗ 7.35 Д-32…Д-34 ч. 1; docs/progress/e19-api.md §12) and
 * the windows they open: without arguments a command opens its window, with them it executes (ADR 0021 п. 9).
 * - `implicit.check {model_id?}` — the window «Проверка по скважинам», the table of the deviations; the text is the summary of the first page;
 * - `implicit.support {model_id?, threshold?, wireframe_id?}` — the same window on the tab «Карта опоры»: the share of the area beyond D_оп of
 *   every surface; with a surface (or when the model has one) the map is painted in the 3D window;
 * - `implicit.compare_holes {model_id?, set_id?, table?, field?}` — the window «Сверка данных и модели»; with the table and the field the
 *   matrix is counted;
 * - `drillholes.from_model {set_id, name, model_id | mesh_ids | block_model_id + field + table, codes?}` — the dialog, or (with the whole
 *   request) the write of the new interval table of the set.
 * The windows follow the model: a model rebuilt or deleted meanwhile is read again or leaves them. No React.
 */
import { createElement, type ReactNode } from "react";
import { bridge } from "../../bridge";
import type { App } from "../../shell/app";
import { needProject, needWritable, noted, refusal, viewport } from "../../shell/objectRun";
import { closeWindow, focusWindow, openWindow, windowStore, type FloatWin } from "../../shell/windows";
import { projectStore } from "../projects/state";
import { checkLine } from "./checkModel";
import { checkPointsShown } from "./checkScene";
import { CHECK_TITLE, CHECK_WINDOW, checkStore, openCheck, reloadCheck, resetCheck } from "./CheckState";
import { CheckWindow } from "./CheckWindow";
import { COMPARE_TITLE, COMPARE_WINDOW, compareStore, openCompare, ready as compareReady, resetCompare, runCompare } from "./CompareState";
import { CompareWindow } from "./CompareWindow";
import { compareLine } from "./compareModel";
import { complete, FROM_TITLE, FROM_WINDOW, fromStore, resetFrom, runFromModel, setFrom } from "./FromModelState";
import { FromModelDialog } from "./FromModelDialog";
import { NO_MODEL, needModel } from "./groupBase";
import { implicitStore, modelById } from "./state";
import { hideMap, openSupport, reloadSupport, resetSupport, showMap, supportStore } from "./SupportState";
import { surfaceLine } from "./supportModel";
import { supportStats } from "./supportScene";

const windowOf = (kind: string): string | null => windowStore.get().windows.find((w) => w.kind === kind)?.id ?? null;

function show(kind: string, title: string, size: "normal" | "form" = "normal"): void {
  const open = windowOf(kind);
  if (open) focusWindow(open);
  else openWindow(kind, title, size, {}, ...viewport());
}

/** A built model of the project (a check of a model without results is a refusal that says what to do). */
function builtModel(app: App, given: unknown): { id: number; name: string } {
  const id = needModel(app, given);
  const row = modelById(id);
  if (!row && implicitStore.get().loaded) throw NO_MODEL(id);
  if (row && row.stats === null && row.outputs === 0) throw refusal("not_built", `Модель «${row.name}» ещё не построена.`, "Постройте модель командой «Обновить» (implicit.update) и повторите.");
  return { id, name: row?.name ?? `№${id}` };
}

/** `implicit.check {model_id?}`. */
export async function runCheck(app: App, a: Record<string, unknown>): Promise<string> {
  const m = builtModel(app, a.model_id);
  show(CHECK_WINDOW, CHECK_TITLE);
  const page = await openCheck(m.id, "table");
  if (!page) throw refusal("check_failed", checkStore.get().error ?? `Проверка модели «${m.name}» не получена.`, "Повторите; если повторится — постройте модель заново.");
  return checkLine(page);
}

const thresholdArg = (v: unknown): number | null => {
  if (v === undefined || v === null || v === "") return null;
  const n = typeof v === "number" ? v : Number(String(v).replace(",", "."));
  if (!(Number.isFinite(n) && n > 0)) throw refusal("bad_param", "Порог D_оп — число метров больше нуля.", "Задайте порог в метрах, например 60. Без порога берётся 2·D_max данных.");
  return n;
};

/** `implicit.support {model_id?, threshold?, wireframe_id?}`. */
export async function runSupport(app: App, a: Record<string, unknown>): Promise<string> {
  const m = builtModel(app, a.model_id);
  const threshold = thresholdArg(a.threshold);
  show(CHECK_WINDOW, CHECK_TITLE);
  await openCheck(m.id, "support");
  checkStore.set({ tab: "support" });
  const answer = await openSupport(m.id, threshold);
  if (!answer) throw refusal("support_failed", supportStore.get().error ?? supportStore.get().note ?? `Карта опоры модели «${m.name}» не получена.`, "Повторите; если повторится — постройте модель заново.");
  const lines = answer.surfaces.length ? answer.surfaces.map(surfaceLine).join("; ") : "у модели нет поверхностей";
  const head = `Модель «${m.name}», порог D_оп ${answer.threshold.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} м: ${lines}.`;
  const asked = typeof a.wireframe_id === "number" ? a.wireframe_id : answer.surfaces.length === 1 ? answer.surfaces[0].wireframe_id : null;
  if (asked === null) return `${head} Выберите поверхность в окне и нажмите «Показать карту в 3D».`;
  if (!answer.surfaces.some((s) => s.wireframe_id === asked)) throw refusal("not_a_surface", `Каркас №${asked} не поверхность модели «${m.name}».`, "Поверхности модели — в таблице окна «Карта опоры».");
  supportStore.set({ surfaceId: asked });
  return `${head} ${await showMap(asked)}`;
}

/** `implicit.compare_holes {model_id?, set_id?, table?, field?}`. */
export async function runCompareHoles(app: App, a: Record<string, unknown>): Promise<string> {
  const m = builtModel(app, a.model_id);
  show(COMPARE_WINDOW, COMPARE_TITLE);
  await openCompare(m.id, {
    ...(typeof a.set_id === "number" ? { setId: a.set_id } : {}),
    ...(typeof a.table === "string" && a.table.trim() ? { table: a.table.trim() } : {}),
    ...(typeof a.field === "string" && a.field.trim() ? { field: a.field.trim() } : {}),
  });
  if (!compareReady(compareStore.get())) return `Открыто окно «${COMPARE_TITLE}» модели «${m.name}»: выберите набор, таблицу и поле кода и нажмите «Сверить».`;
  const answer = await runCompare();
  if (!answer) throw refusal("compare_failed", compareStore.get().error ?? "Сверка не получена.", "Проверьте набор, таблицу и поле кода; модель должна быть построена.");
  return compareLine(answer);
}

/** `drillholes.from_model {…}`: the dialog or the write. */
export async function runFrom(a: Record<string, unknown>): Promise<string> {
  if (complete(a)) {
    show(FROM_WINDOW, FROM_TITLE, "form");
    return runFromModel(a);
  }
  const ids = String(a.mesh_ids ?? "")
    .split(/[;,\s]+/)
    .map(Number)
    .filter((n) => Number.isInteger(n) && n > 0);
  const source = typeof a.block_model_id === "number" ? "field" : ids.length ? "meshes" : "model";
  setFrom({
    ...(typeof a.set_id === "number" ? { setId: a.set_id } : {}),
    source,
    ...(typeof a.model_id === "number" ? { modelId: a.model_id } : {}),
    ...(ids.length ? { meshIds: ids } : {}),
    ...(typeof a.block_model_id === "number" ? { blockId: a.block_model_id } : {}),
    ...(typeof a.field === "string" ? { field: a.field } : {}),
    ...(typeof a.table === "string" ? { table: a.table } : {}),
    ...(typeof a.name === "string" && a.name.trim() ? { name: a.name.trim(), nameTouched: true } : {}),
  });
  show(FROM_WINDOW, FROM_TITLE, "form");
  return `Открыт диалог «${FROM_TITLE}»: выберите набор, источник тел и название новой таблицы.`;
}

// ---- the registry ----

const KINDS = [CHECK_WINDOW, COMPARE_WINDOW, FROM_WINDOW];
let unbind: (() => void)[] = [];

function closeAll(): void {
  for (const w of windowStore.get().windows) if (KINDS.includes(w.kind)) closeWindow(w.id);
  resetCheck();
  resetSupport();
  resetCompare();
  resetFrom();
}

/**
 * With BF_E2E=1 the state of the windows is readable as `window.__bfImplicitCheck` (the E2E of 19.28): `check()` — the table (rows, beyond
 * the threshold, the points in 3D), `support()` — the surface painted (the vertices of the layer, the counts, the shares), `compare()` — the
 * share of the agreement and the mismatches, `fromModel()` — the result, the error, the free name.
 */
function e2eHook(): () => void {
  const on = bridge()?.flags?.().e2e === true && typeof window !== "undefined";
  if (!on) return () => undefined;
  window.__bfImplicitCheck = {
    check: () => {
      const s = checkStore.get();
      return { modelId: s.modelId, tab: s.tab, phase: s.phase, total: s.page?.total ?? null, rows: s.page?.rows.length ?? 0, over: s.page?.over ?? null, maxResidual: s.page?.max_abs_residual ?? null, highlight: s.page?.highlight ?? null, picked: s.picked, shown: s.shown, points: checkPointsShown(), note: s.note };
    },
    support: () => {
      const s = supportStore.get();
      return { modelId: s.modelId, phase: s.phase, shownId: s.shownId, layer: supportStats(), counts: s.counts, repaintMs: s.repaintMs, threshold: s.answer?.threshold ?? null, shares: Object.fromEntries((s.answer?.surfaces ?? []).map((x) => [x.wireframe_id, x.share_beyond])), note: s.note };
    },
    compare: () => {
      const s = compareStore.get();
      return { modelId: s.modelId, phase: s.phase, share: s.answer?.agree_share ?? null, mismatches: s.answer?.mismatches.total ?? null, rows: s.answer?.mismatches.rows.length ?? 0, picked: s.picked, error: s.error };
    },
    fromModel: () => {
      const s = fromStore.get();
      return { busy: s.busy, rows: s.result?.rows ?? null, table: s.result?.name ?? null, holes: s.result?.holes ?? null, error: s.error?.code ?? null, suggested: s.suggested };
    },
  };
  return () => {
    if (typeof window !== "undefined") window.__bfImplicitCheck = undefined;
  };
}

declare global {
  interface Window {
    __bfImplicitCheck?: unknown;
  }
}

/** The signature of what a window reads of the model: another one — the results are read again. */
const stampOf = (r: { version: number; state: string; stale: boolean; outputs: number }): string => `${r.version}|${r.state}|${r.stale}|${r.outputs}`;

export function bindChecks(app: App): void {
  unbind.forEach((fn) => fn());
  unbind = [];
  app.registry.bind("implicit.check", noted(app, (a) => runCheck(app, a)), needProject);
  app.registry.bind("implicit.support", noted(app, (a) => runSupport(app, a)), needProject);
  app.registry.bind("implicit.compare_holes", noted(app, (a) => runCompareHoles(app, a)), needProject);
  app.registry.bind("drillholes.from_model", noted(app, (a) => runFrom(a)), needWritable(app));
  let uid: string | null = projectStore.get().current?.uid ?? null;
  unbind.push(e2eHook());
  // what every model looked like at the last notification of the list: a change of a model the windows watch is told by the difference
  let prev = new Map<number, string>(implicitStore.get().rows.map((r) => [r.id, stampOf(r)]));
  unbind.push(
    // what was asked of one project does not belong to another
    projectStore.subscribe(() => {
      const cur = projectStore.get().current?.uid ?? null;
      if (cur === uid) return;
      uid = cur;
      prev = new Map();
      closeAll();
    }),
    // a model rebuilt meanwhile: the table is read again, the map (an old surface) leaves the 3D window; a model that is gone takes the windows
    implicitStore.subscribe(() => {
      const st = implicitStore.get();
      if (!st.loaded) return;
      const now = new Map(st.rows.map((r) => [r.id, stampOf(r)]));
      const before = prev;
      prev = now;
      const changed = (id: number | null): boolean => id !== null && before.has(id) && now.has(id) && before.get(id) !== now.get(id);
      const gone = (id: number | null): boolean => id !== null && !now.has(id);
      const check = checkStore.get().modelId;
      const support = supportStore.get().modelId;
      const compare = compareStore.get().modelId;
      if (gone(check) || gone(support)) closeWindowOf(CHECK_WINDOW);
      else {
        if (changed(check) && checkStore.get().phase === "ok") void reloadCheck();
        if (changed(support)) {
          if (supportStore.get().shownId !== null) hideMap();
          void reloadSupport();
        }
      }
      if (gone(compare)) closeWindowOf(COMPARE_WINDOW);
      else if (changed(compare) && compareStore.get().phase === "ok") compareStore.set({ note: "Модель изменилась: нажмите «Сверить», чтобы пересчитать." });
      // the dialog «Модель на скважины» chose a model that is gone
      const f = fromStore.get();
      if (f.modelId !== null && gone(f.modelId)) setFrom({ modelId: null, codes: {} });
    }),
  );
}

function closeWindowOf(kind: string): void {
  const id = windowOf(kind);
  if (id) closeWindow(id);
  if (kind === CHECK_WINDOW) {
    resetCheck();
    resetSupport();
  } else resetCompare();
}

/** The windows for `renderImplicitWindow`; null — not a window of the check. */
export function renderCheckWindow(w: FloatWin): ReactNode | null {
  switch (w.kind) {
    case CHECK_WINDOW:
      return createElement(CheckWindow, { winId: w.id });
    case COMPARE_WINDOW:
      return createElement(CompareWindow, { winId: w.id });
    case FROM_WINDOW:
      return createElement(FromModelDialog, { winId: w.id });
    default:
      return null;
  }
}
