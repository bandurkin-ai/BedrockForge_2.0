/**
 * What the window «Проверка по скважинам» knows (ТЗ 7.35 Д-32 п. 1; docs/progress/e19-api.md §8.1): the model, the query of the table (the
 * group, the sort, the page, the threshold of the highlight), the page read, the row picked, the points shown in 3D. A read that a newer
 * one overtook is dropped; another project — the state is clean. The file (`check.csv`) is saved through the save dialog of the
 * application. No React.
 */
import { saveFile } from "../../bridge";
import { errorText } from "../../shell/props";
import { Store } from "../../shell/store";
import { windowStore } from "../../shell/windows";
import { projectStore } from "../projects/state";
import { checkLine, hasPlace, boxAround, pointCloud } from "./checkModel";
import { csvName, DEFAULT_QUERY, loadCheck, loadCheckCsv, type CheckPage, type CheckQuery, type CheckRow } from "./checkApi";
import { checkPointsShown, hideCheckPoints, revealBox, showCheckPoints } from "./checkScene";
import { implicitStore } from "./state";

export const CHECK_WINDOW = "implicit-check";
export const CHECK_TITLE = "Проверка по скважинам";

export type CheckTab = "table" | "support";

export interface CheckWin {
  modelId: number | null;
  tab: CheckTab;
  query: CheckQuery;
  /** the text of the field «Порог подсветки, м» (a decimal comma is accepted) */
  highlightText: string;
  phase: "idle" | "loading" | "ok" | "error";
  page: CheckPage | null;
  /** the refusal of the core in words */
  error: string | null;
  /** `i` of the row picked */
  picked: number | null;
  /** the line the last action told: the file saved, the points shown */
  note: string | null;
  /** the points of the page are in the 3D window */
  shown: boolean;
  saving: boolean;
}

const fresh = (): CheckWin => ({ modelId: null, tab: "table", query: { ...DEFAULT_QUERY }, highlightText: "", phase: "idle", page: null, error: null, picked: null, note: null, shown: false, saving: false });

export const checkStore = new Store<CheckWin>(fresh());

let seq = 0;

/** The decimal comma, a point and the minus sign of the typography: a number of a field; null — empty, NaN — not a number. */
export function parseField(text: string): number | null {
  const t = text.trim().replace(/\s/g, "").replace("−", "-").replace(",", ".");
  if (!t) return null;
  const v = Number(t);
  return Number.isFinite(v) ? v : Number.NaN;
}

/** Reads the page of the table for the query now in the store. */
export async function reloadCheck(): Promise<CheckPage | null> {
  const s = checkStore.get();
  if (s.modelId === null) return null;
  const mine = ++seq;
  const uid = projectStore.get().current?.uid ?? null;
  checkStore.set({ phase: "loading", error: null });
  try {
    const page = await loadCheck(s.modelId, s.query);
    if (mine !== seq || (projectStore.get().current?.uid ?? null) !== uid) return null;
    checkStore.set((x) => ({ phase: "ok", page, error: null, picked: x.picked !== null && page.rows.some((r) => r.i === x.picked) ? x.picked : null }));
    if (checkStore.get().shown) refreshPoints(page);
    return page;
  } catch (e) {
    if (mine !== seq) return null;
    checkStore.set({ phase: "error", page: null, error: errorText(e) });
    return null;
  }
}

/** The window for a model: the table from the first page. */
export async function openCheck(modelId: number, tab: CheckTab = "table"): Promise<CheckPage | null> {
  const same = checkStore.get().modelId === modelId;
  if (!same) {
    if (checkStore.get().shown) hideCheckPoints();
    checkStore.set({ ...fresh(), modelId, tab });
  } else checkStore.set({ tab });
  if (tab !== "table") return null; // the table is read when its tab is shown (`CheckWindow`)
  if (same && checkStore.get().phase === "ok") return checkStore.get().page;
  return reloadCheck();
}

/** A change of the query: the page goes back to the first when it is not the page that changed. */
export function setQuery(patch: Partial<CheckQuery>): Promise<CheckPage | null> {
  const q = { ...checkStore.get().query, ...patch, ...("page" in patch ? {} : { page: 1 }) };
  checkStore.set({ query: q, picked: null });
  return reloadCheck();
}

export function setHighlightText(text: string): void {
  checkStore.set({ highlightText: text });
}

/** «Применить» to the field of the threshold: a number (or empty — the default of the core) is a new query; a wrong one is a note. */
export function applyHighlight(): Promise<CheckPage | null> | null {
  const v = parseField(checkStore.get().highlightText);
  if (v !== null && (Number.isNaN(v) || v < 0)) {
    checkStore.set({ note: "Порог подсветки — число метров, не меньше нуля." });
    return null;
  }
  checkStore.set({ note: null });
  return setQuery({ highlight: v });
}

export function setTab(tab: CheckTab): void {
  checkStore.set({ tab });
}

// ---- the points in 3D ----

function refreshPoints(page: CheckPage): void {
  const cloud = pointCloud(page);
  if (!cloud) {
    hideCheckPoints();
    checkStore.set({ shown: false, note: "На этой странице нет точек с известным местом: в 3D показывать нечего." });
    return;
  }
  try {
    showCheckPoints(cloud);
  } catch (e) {
    checkStore.set({ shown: false, note: errorText(e) });
  }
}

/** «Показать в 3D» / «Скрыть»: the points of the page, coloured by the residual. Returns the line for the status. */
export function toggleShown(): string {
  const s = checkStore.get();
  if (s.shown) {
    hideCheckPoints();
    checkStore.set({ shown: false, note: null });
    return "Точки проверки убраны из 3D-окна";
  }
  if (!s.page) return "Таблица ещё не прочитана";
  const cloud = pointCloud(s.page);
  if (!cloud) {
    checkStore.set({ note: "На этой странице нет точек с известным местом." });
    return "Нет точек для показа";
  }
  try {
    showCheckPoints(cloud);
  } catch (e) {
    checkStore.set({ note: errorText(e) });
    return errorText(e);
  }
  checkStore.set({ shown: true, note: `В 3D показано точек: ${cloud.n.toLocaleString("ru-RU")}; цвет — невязка (Cobalt — модель ниже данных, Copper Orange — выше), полный цвет при ±${cloud.limit.toLocaleString("ru-RU", { maximumSignificantDigits: 3 })}.` });
  return `В 3D показано точек: ${cloud.n}`;
}

/** A click on a row: the row is picked and the camera goes to its point (when the 3D window is up). */
export function pickRow(r: CheckRow): void {
  const s = checkStore.get();
  checkStore.set({ picked: r.i, note: null });
  if (!hasPlace(r)) {
    checkStore.set({ note: "У этой точки нет места в проекте: в 3D переходить некуда." });
    return;
  }
  const box = boxAround(r, s.page?.highlight ?? null);
  if (!box) return;
  try {
    if (!s.shown && s.page) {
      const cloud = pointCloud(s.page);
      if (cloud) {
        showCheckPoints(cloud);
        checkStore.set({ shown: true });
      }
    }
    revealBox(box);
  } catch (e) {
    checkStore.set({ note: errorText(e) });
  }
}

// ---- the file ----

/** «Сохранить CSV»: every row of the selection (the group and the sort of the table, not the page), the bytes as the core wrote them. */
export async function saveCsv(): Promise<string> {
  const s = checkStore.get();
  if (s.modelId === null) return "Модель не выбрана";
  const name = s.page?.model.name ?? implicitStore.get().rows.find((m) => m.id === s.modelId)?.name ?? `№${s.modelId}`;
  checkStore.set({ saving: true, note: null });
  try {
    const bytes = await loadCheckCsv(s.modelId, s.query);
    const path = await saveFile(csvName(name), "csv", bytes);
    const text = path ? `Таблица проверки сохранена: ${path}` : "Сохранение отменено";
    checkStore.set({ saving: false, note: text });
    return text;
  } catch (e) {
    const text = errorText(e);
    checkStore.set({ saving: false, note: text });
    return text;
  }
}

/** The line of the page for the assistant. */
export const lineOf = (page: CheckPage): string => checkLine(page);

/** The window is closed: the points leave the 3D window with it, the state is clean. */
export function resetCheck(): void {
  seq++;
  if (checkPointsShown()) hideCheckPoints();
  checkStore.set(fresh());
}

/**
 * The cleanup of a window component: the state goes with the window, not with a remount (the development build mounts, unmounts and mounts
 * again; the window is still in the store then).
 */
export function whenGone(winId: string, fn: () => void): () => void {
  return () =>
    queueMicrotask(() => {
      if (!windowStore.get().windows.some((w) => w.id === winId)) fn();
    });
}
