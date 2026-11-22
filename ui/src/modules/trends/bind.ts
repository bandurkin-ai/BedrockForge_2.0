/**
 * Handlers of `trend.*` (ТЗ 7.35 Д-15; ADR 0022 §10, §13, решения 20, 22) and the windows «Тренд» of the shell.
 * `trend.open` — the window of a trend (one per trend) or a new one; `trend.save` without angles and without a model
 * opens the window, with them — checked first (out of range never reaches the core), then `POST` / `PUT /trends`;
 * a trend used by a consumer (Э18, Э19) is changed after a question in our own window. `trend.delete` — the
 * consumers are read first: a used trend is not deleted (the list in our own window), else a question; the
 * assistant is asked by the registry (`confirm`). `trend.show` — the view key `ellipsoids`. `trend.usage` —
 * «Где используется». The list follows `geostat.changed {kind: "trend"}`; the 3D is a lazy chunk (`scene.ts`).
 */
import { createElement, type ReactNode } from "react";
import type { App } from "../../shell/app";
import { CoreError } from "../../shell/api";
import type { CommandSource } from "../../shell/commands";
import { askConfirm, showMessage } from "../../shell/dialogs";
import { needProject, needWritable, noted, refusal, viewport } from "../../shell/objectRun";
import { closeWindow, focusWindow, openWindow, updateWindow, windowStore, type FloatWin } from "../../shell/windows";
import { onCoreEvent } from "../jobs/state";
import { projectStore } from "../projects/state";
import { updateView, viewStore } from "../scene/bind";
import { onScene } from "../scene/sceneHost";
import { createTrend, deleteTrend, loadTrend, loadTrends, trendUsage, updateTrend, usageText, type Trend, type TrendUse } from "./api";
import {
  bodyOfArgs,
  dropObject,
  dropTrend,
  editOf,
  ellipsoidEntries,
  ELLIPSOIDS_KEY,
  findTrend,
  forgetEdit,
  newEdit,
  resetTrends,
  saveArgs,
  setEdit,
  setTrends,
  showObject,
  TREND_WINDOW,
  trendsStore,
  upsertTrend,
  valuesOf,
  windowOfTrend,
  type TrendValues,
} from "./state";
import { TrendWindow } from "./TrendWindow";

const HINT_RANGE = "Диапазоны — в описании команды trend.save; исправьте значение и повторите.";
const given = (a: Record<string, unknown>, k: string) => a[k] !== undefined && a[k] !== null && (typeof a[k] !== "string" || (a[k] as string).trim() !== "");

export const trendTitle = (name: string | null): string => (name ? `Тренд · ${name}` : "Новый тренд");

export async function refreshTrends(): Promise<void> {
  setTrends(await loadTrends());
}

const notFound = (id: unknown) => refusal("trend_not_found", `Тренд №${String(id)} не найден.`, "Номера трендов — в дереве, раздел «Тренды».");

/** The trend of the arguments: the store, else the core (404 — not found). */
export async function trendOf(id: unknown): Promise<Trend> {
  if (typeof id !== "number" || !Number.isInteger(id)) throw notFound(id);
  const known = findTrend(id);
  if (known) return known;
  try {
    const t = await loadTrend(id);
    upsertTrend(t);
    return t;
  } catch (e) {
    if (e instanceof CoreError && e.status === 404) throw notFound(id);
    throw e;
  }
}

/** Opens the window «Тренд» of a trend (focused when open) or a new one (prefilled with `values`). */
export function openTrendWindow(t: Trend | null, values: Partial<TrendValues> = {}): string {
  if (t) {
    const open = windowOfTrend(t.id);
    if (open && windowStore.get().windows.some((w) => w.id === open)) {
      focusWindow(open);
      return open;
    }
  }
  const win = openWindow(TREND_WINDOW, trendTitle(t?.name ?? null), "form", { trendId: t?.id ?? null }, ...viewport());
  if (!editOf(win)) setEdit(win, t ? newEdit({ trendId: t.id, values: valuesOf(t), origin: t.origin }) : newEdit({ values: { ...newEdit().values, ...values } }));
  return win;
}

/** The values of the arguments for the form of a new trend (what is given; the rest — the defaults). */
function prefill(a: Record<string, unknown>): Partial<TrendValues> {
  const out: Partial<TrendValues> = {};
  for (const k of ["name", "ang1", "ang2", "ang3", "size", "color"] as const) if (given(a, k)) out[k] = String(a[k]).replace(".", ",");
  return out;
}

/** The last trend saved by `trend.save` (the window «Тренд» binds itself to a new one). */
let saved: { id: number; name: string } | null = null;
export const lastSaved = (): { id: number; name: string } | null => saved;

/** After a write: the list read again, the window of the trend shows what is stored. */
async function afterWrite(id: number): Promise<Trend | null> {
  await refreshTrends().catch(() => undefined);
  const t = findTrend(id) ?? (await loadTrend(id).catch(() => null));
  if (!t) return null;
  upsertTrend(t);
  const win = windowOfTrend(t.id);
  if (win) {
    setEdit(win, { values: valuesOf(t), origin: t.origin, fromModel: null, dirty: false });
    updateWindow(win, { title: trendTitle(t.name) });
  }
  return t;
}

const usedQuestion = (name: string, list: readonly TrendUse[]) => `Тренд «${name}» используется: ${usageText(list)}. Изменённые оси получат и эти объекты. Изменить тренд?`;

export async function runSave(a: Record<string, unknown>, source: CommandSource): Promise<string> {
  const id = a.trend_id;
  const fields = Object.keys(a).filter((k) => k !== "trend_id" && given(a, k));
  if (id === undefined && !given(a, "ang1") && !given(a, "model_id")) {
    if (a.kind === "structural") throw refusal("not_ready", "Структурный тренд (по замерам или опорному каркасу) будет доступен позже.", "Создайте глобальный тренд: углы и отношения осей.");
    openTrendWindow(null, prefill(a));
    return `Открыто окно «Новый тренд»${source === "claude" ? ": не заданы ни углы (ang1), ни модель вариограммы (model_id)" : ""}`;
  }
  const got = bodyOfArgs(a);
  if ("error" in got) throw refusal("bad_param", got.error, HINT_RANGE);
  if (id !== undefined) {
    const t = await trendOf(id);
    if (!fields.length && !("centre" in a)) {
      openTrendWindow(t);
      return `Открыто окно «${trendTitle(t.name)}»`;
    }
    if (source === "user" && t.used) {
      const list = await trendUsage(t.id).catch(() => []);
      if (list.length && !(await askConfirm({ title: "Изменить тренд", message: usedQuestion(t.name, list), okLabel: "Изменить", danger: true }))) return "Отменено";
    }
    const out = await updateTrend(t.id, got.body);
    saved = out;
    const now = await afterWrite(out.id);
    const refresh = got.body.model_id !== undefined;
    return `Тренд «${out.name}» ${refresh ? "обновлён из модели вариограммы" : "изменён"}${now ? `: ${shapeText(now)}` : ""}`;
  }
  const out = await createTrend(got.body);
  saved = out;
  const now = await afterWrite(out.id);
  return `Тренд «${out.name}» сохранён (№${out.id})${now ? `: ${shapeText(now)}` : ""}`;
}

const n = (v: number) => v.toLocaleString("ru-RU", { maximumSignificantDigits: 6 });

/** «ang1 112,5°, ang2 −35°, ang3 −20°, a1 : a2 : a3 = 1 : 0,36 : 0,05, размер 250 м». */
export const shapeText = (t: Trend): string => `ang1 ${n(t.ang1)}°, ang2 ${n(t.ang2)}°, ang3 ${n(t.ang3)}°, a1 : a2 : a3 = 1 : ${n(t.ratios[1])} : ${n(t.ratios[2])}, размер ${n(t.size)} м`;

export async function runDelete(a: Record<string, unknown>, source: CommandSource): Promise<string> {
  const t = await trendOf(a.trend_id);
  if (source === "user") {
    const list = await trendUsage(t.id);
    if (list.length) {
      await showMessage({ title: "Тренд используется", message: `Тренд «${t.name}» не удалён — он выбран в объектах: ${usageText(list, 20)}. Выберите в них другой тренд или удалите их.` });
      return `Тренд «${t.name}» используется: ${usageText(list)} — не удалён`;
    }
    if (!(await askConfirm({ title: "Удалить тренд", message: `Удалить тренд «${t.name}»?`, okLabel: "Удалить", danger: true }))) return "Отменено";
  }
  await deleteTrend(t.id);
  forgetTrend(t.id);
  return `Тренд «${t.name}» удалён`;
}

/** A trend gone: out of the list, the view key and its window (closed). */
export function forgetTrend(id: number): void {
  dropTrend(id);
  const win = windowOfTrend(id);
  if (win) {
    forgetEdit(win);
    closeWindow(win);
  }
  if (ellipsoidEntries(viewStore.get()[ELLIPSOIDS_KEY]).some((e) => e.object === id)) {
    try {
      updateView(ELLIPSOIDS_KEY, dropObject(id));
    } catch {
      /* no view of a project: nothing to forget */
    }
  }
}

export async function runShow(a: Record<string, unknown>): Promise<string> {
  const t = await trendOf(a.trend_id);
  const visible = a.visible !== false;
  updateView(ELLIPSOIDS_KEY, showObject(t.id, visible));
  return visible ? `Эллипсоид тренда «${t.name}» показан в 3D${t.centre ? "" : " — в центре вида (центр тренда не задан)"}` : `Эллипсоид тренда «${t.name}» скрыт`;
}

export async function runUsage(a: Record<string, unknown>, source: CommandSource): Promise<string> {
  const t = await trendOf(a.trend_id);
  const list = await trendUsage(t.id);
  const lines = list.map((u) => `${u.name} (${u.kind}, роль ${u.role})`);
  const text = list.length ? `Тренд «${t.name}» используется (${list.length}): ${lines.join("; ")}` : `Тренд «${t.name}» нигде не используется — его можно удалить`;
  if (source === "user") void showMessage({ title: `Где используется «${t.name}»`, message: list.length ? `${lines.join("\n")}\n\nПока ссылки есть, тренд не удаляется.` : "Тренд нигде не используется: он не выбран ни в оценке блочных моделей, ни в RBF." });
  return text;
}

/** «Сохранить» of a window «Тренд»: `trend.save` through the registry (the log, the notice); the window binds to the
 * trend saved. A wrong value — its text (null — saved). */
export async function saveWindow(app: App, win: string): Promise<string | null> {
  const e = editOf(win);
  if (!e) return "Окно тренда закрыто";
  const got = saveArgs(e);
  if ("error" in got) return got.error;
  saved = null;
  const r = await app.registry.execute("trend.save", got.args, "user");
  if (!r.ok) return r.result;
  const s = lastSaved();
  if (s && e.trendId === null) {
    const t = findTrend(s.id) ?? null;
    setEdit(win, { trendId: s.id, ...(t ? { values: valuesOf(t), origin: t.origin } : {}), fromModel: null, dirty: false });
    updateWindow(win, { title: trendTitle(s.name), props: { trendId: s.id } });
  }
  return null;
}

/** «Сохранить как тренд» of a variogram window (`trendArgsOfWork`): `trend.save` through the registry; the id of the trend made. */
export async function saveAsTrend(app: App, args: Record<string, unknown>): Promise<{ ok: boolean; result: string; id: number | null }> {
  saved = null;
  const r = await app.registry.execute("trend.save", args, "user");
  return { ...r, id: r.ok ? (lastSaved()?.id ?? null) : null };
}

export function bindTrends(app: App): void {
  const r = app.registry;
  r.bind(
    "trend.open",
    noted(app, async (a) => {
      if (a.trend_id === undefined) {
        openTrendWindow(null);
        return "Открыто окно «Новый тренд»";
      }
      const t = await trendOf(a.trend_id);
      openTrendWindow(t);
      return `Открыто окно «${trendTitle(t.name)}»`;
    }),
    needProject,
  );
  r.bind("trend.save", noted(app, runSave), needWritable(app));
  r.bind("trend.delete", noted(app, runDelete), needWritable(app));
  r.bind("trend.show", noted(app, runShow), needProject);
  r.bind("trend.usage", noted(app, runUsage), needProject);
  // the list of the project: read when it opens, kept by the events of the core
  let uid: string | null = null;
  projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    resetTrends();
    if (cur) void refreshTrends().catch(() => undefined);
  });
  onCoreEvent((event, data) => {
    if (event !== "geostat.changed") return;
    try {
      const d = JSON.parse(data) as { kind?: unknown; id?: unknown; deleted?: unknown };
      if (d.kind !== "trend") return;
      if (d.deleted === true && typeof d.id === "number") forgetTrend(d.id);
    } catch {
      /* a malformed event: the list is read again */
    }
    void refreshTrends().catch(() => undefined);
  });
  // a closed window leaves its edit
  windowStore.subscribe(() => {
    const open = new Set(windowStore.get().windows.map((w) => w.id));
    for (const win of Object.keys(trendsStore.get().edits)) if (!open.has(win)) forgetEdit(win);
  });
  // the ellipsoids of the trends in the main 3D: a lazy chunk with three.js, attached to every new scene
  let detach: (() => void) | null = null;
  let generation = 0;
  onScene((scene) => {
    const mine = ++generation;
    detach?.();
    detach = null;
    if (!scene) return;
    void import("./scene").then((m) => {
      if (mine === generation) detach = m.attachTrendScene(scene);
    });
  });
}

/** The windows «Тренд» for the shell's window layer (`shell/Shell.tsx`); null — not a trend window. */
export function renderTrendWindow(w: FloatWin): ReactNode | null {
  if (w.kind !== TREND_WINDOW) return null;
  return createElement(TrendWindow, { winId: w.id });
}
