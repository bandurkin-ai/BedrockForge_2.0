/**
 * The swath commands (ADR 0019 §6, §9; ТЗ 7.30, 7.35 Д-01) and the swath windows of the shell. `stats.swath`
 * without `swath_id` makes a swath by default (the first block model against the active set, either alone when
 * the other is missing), saves it at once in «Аудит и статистика» and opens its window; with `swath_id` — gives
 * it the new settings (an open window takes them and saves them as its own edit; else they are saved here) and
 * opens its window. One window per swath, several swaths at once, none modal: «Swath plot · ‹название›».
 * `stats.swath_3d` shows or hides it in the main 3D (view key `swaths`). The assistant gets the totals of the
 * series. The 3D part is a lazy chunk (`scene.ts`).
 */
import { createElement, type ReactNode } from "react";
import type { App } from "../../shell/app";
import { CoreError } from "../../shell/api";
import type { CommandContext } from "../../shell/commands";
import { needProject, noted, refusal, viewport } from "../../shell/objectRun";
import { errorText } from "../../shell/props";
import { focusWindow, openWindow, windowStore, type FloatWin } from "../../shell/windows";
import { blocksStore } from "../blocks/state";
import { defaultField, getChart, loadFields, type ChartRow } from "../charts/api";
import { onCoreEvent } from "../jobs/state";
import { projectStore } from "../projects/state";
import { onScene } from "../scene/sceneHost";
import { updateView, viewStore } from "../scene/bind";
import { dropSwathEntry, shownSwaths, showSwath, SWATHS_KEY } from "../scene/viewSync";
import { tablesStore } from "../tables/state";
import { computeSwath, createSwath, loadSources, updateSwath, type SwathSources } from "./api";
import { applyArgs, blockSeries, defaultSwath, matchComponent, normalizeSwath, sampleSeries, swathName, swathRequest, swathSummary, swathTitle, type SwathSeries, type SwathSettings } from "./model";
import { findSwath, liveStore, loadSwaths, pushSettings, resetSwaths, swathChanged, swathDataChanged, swathsStore, upsertSwath } from "./state";
import { SwathWindow } from "./SwathWindow";

export const SWATH_WINDOW = "swath";

const notFound = (id: unknown) => refusal("chart_not_found", `Swath plot №${String(id)} не найден.`, "Номера swath plot — в дереве, раздел «Аудит и статистика».");

/** The swath of the arguments: the store, else the core (404 — not found). */
export async function swathOf(id: unknown): Promise<ChartRow> {
  if (typeof id !== "number" || !Number.isInteger(id)) throw notFound(id);
  const known = findSwath(id);
  if (known) return known;
  try {
    const row = await getChart(id);
    if (row.kind !== "swath") throw refusal("bad_chart", `«${row.name}» — не swath plot.`, "Графики набора открываются командой chart.open.");
    upsertSwath(row);
    return row;
  } catch (e) {
    if (e instanceof CoreError && e.status === 404) throw notFound(id);
    throw e;
  }
}

/** Why a swath cannot be made now: no project; the project has neither sets of holes nor block models. */
export function swathReason(ctx: CommandContext): string | null {
  const base = needProject(ctx);
  if (base) return base;
  const blocks = blocksStore.get();
  if (!tablesStore.get().sets.length && blocks.loaded && !blocks.list.length) return "В проекте нет наборов скважин и блочных моделей";
  return null;
}

/** The first block model (its first grade) against the samples of the active set (the component it was estimated from). */
export async function defaultSeries(sources: SwathSources | null, activeSet: number | null): Promise<SwathSeries[]> {
  const series: SwathSeries[] = [];
  const model = sources?.models[0];
  if (model) {
    const numbers = model.numbers.map((n) => n.name);
    const field = model.grades.find((g) => numbers.includes(g)) ?? numbers.find((n) => n !== model.density) ?? null;
    series.push(blockSeries(series, model.id, field, model.density));
  }
  const setId = activeSet ?? sources?.sets[0]?.id ?? null;
  if (setId !== null) {
    try {
      const f = await loadFields(setId);
      const components = f.numeric.filter((x) => x.kind === "component").map((x) => x.name);
      const block = series[0]?.field ?? null;
      series.push(sampleSeries(series, setId, f.table, matchComponent(components, block) ?? defaultField(f)));
    } catch {
      series.push(sampleSeries(series, setId, null, null)); // the window asks the fields again
    }
  }
  return series;
}

/** Opens the window of a swath (one per swath: an open one comes to the front); unsaved settings — a window of their own. */
export function openSwathWindow(row: ChartRow | null, settings: SwathSettings, opts: { error?: string | null; readOnly?: boolean } = {}): string {
  const open = row ? windowStore.get().windows.find((w) => w.kind === SWATH_WINDOW && w.props?.swathId === row.id) : undefined;
  if (open) {
    focusWindow(open.id);
    return open.id;
  }
  const props = { swathId: row?.id ?? null, settings, ...(opts.error ? { saveError: opts.error } : {}), ...(opts.readOnly ? { readOnly: true } : {}) };
  return openWindow(SWATH_WINDOW, swathTitle(row?.name ?? swathName(settings)), "normal", props, ...viewport());
}

/** «Swath plot · X · 25 м» (swath plot №12). */
const savedName = (row: ChartRow | null, settings: SwathSettings) => (row ? `«${row.name}» (swath plot №${row.id})` : `«${swathName(settings)}»`);

/** The assistant's text: by the stored swath, or by the settings not stored yet (`bySettings`). */
async function summary(row: ChartRow | null, settings: SwathSettings, bySettings = !row): Promise<string> {
  const r = await computeSwath(row && !bySettings ? { swath_id: row.id } : { settings: swathRequest(settings) });
  return swathSummary(savedName(row, settings), r);
}

/** `stats.swath` (ADR 0019 §9). */
export async function runSwath(app: App, args: Record<string, unknown>, source: "user" | "claude"): Promise<string> {
  const readOnly = app.store.get().projectReadOnly;
  if (args.swath_id !== undefined && args.swath_id !== null) {
    let row = await swathOf(args.swath_id);
    // an open window: over its settings as edited (an edit may not be saved yet)
    const before = liveStore.get().live[row.id]?.settings ?? normalizeSwath(row.settings);
    const settings = applyArgs(before, args);
    const changed = JSON.stringify(settings) !== JSON.stringify(before);
    let note = "";
    let saved: ChartRow | null = null;
    if (changed && !pushSettings(row.id, settings)) {
      if (readOnly) note = "; проект только для чтения — настройки не сохранены";
      else {
        try {
          saved = row = await updateSwath(row.id, { settings, ...(row.auto_name ? { auto_name: swathName(settings) } : {}), expected_version: row.version });
        } catch (e) {
          note = `; настройки не сохранены: ${errorText(e)}`;
        }
      }
    }
    // the window first: it computes the new settings, the 3D does not ask for them again (one request)
    openSwathWindow(row, settings, { readOnly });
    if (saved) upsertSwath(saved);
    if (source === "claude") return `${await summary(row, settings, changed)}${note}`;
    return `Открыт swath plot «${row.name}»${note}`;
  }
  let sources: SwathSources | null = null;
  try {
    sources = await loadSources();
  } catch {
    sources = null; // the window says what is missing
  }
  const series = await defaultSeries(sources, tablesStore.get().setId);
  if (!series.length) throw refusal("no_sources", "В проекте нет наборов скважин и блочных моделей.", "Загрузите скважины с пробами или блочную модель.");
  const settings = applyArgs(defaultSwath(series), args);
  let row: ChartRow | null = null;
  let error: string | null = null;
  if (!readOnly) {
    try {
      row = await createSwath(swathName(settings), settings);
      upsertSwath(row);
    } catch (e) {
      error = errorText(e);
    }
  }
  openSwathWindow(row, settings, { error, readOnly });
  const unsaved = error ? `; swath plot не сохранён: ${error}` : "";
  if (source === "claude") return `${await summary(row, settings)}${unsaved}`;
  return `Открыто окно «${swathTitle(row?.name ?? swathName(settings))}»${unsaved}`;
}

/** `stats.swath_3d {swath_id, visible}`: the view key `swaths` of the project. */
export async function runSwath3d(args: Record<string, unknown>): Promise<string> {
  const row = await swathOf(args.swath_id);
  const visible = args.visible === true;
  updateView(SWATHS_KEY, showSwath(row.id, visible));
  return `${visible ? "Показан" : "Скрыт"} в 3D swath plot «${row.name}»`;
}

export const isShown3d = (id: number): boolean => shownSwaths(viewStore.get()[SWATHS_KEY]).includes(id);

export function bindSwath(app: App): void {
  app.registry.bind("stats.swath", noted(app, (a, source) => runSwath(app, a, source)), swathReason);
  app.registry.bind("stats.swath_3d", noted(app, (a) => runSwath3d(a)), needProject);
  let uid: string | null = null;
  projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    resetSwaths();
    if (cur) void loadSwaths();
  });
  onCoreEvent((event, data) => {
    if (event !== "drillholes.changed" && event !== "objects.changed") return;
    try {
      const d = JSON.parse(data) as { kind?: unknown; id?: unknown; deleted?: unknown; chart?: unknown };
      if (event === "objects.changed" && d.kind === "chart") {
        if (d.chart !== "swath") return;
        if (d.deleted === true && typeof d.id === "number" && projectStore.get().current && isShown3d(d.id)) updateView(SWATHS_KEY, dropSwathEntry(d.id));
        return void swathChanged(d);
      }
    } catch {
      /* a malformed event: everything is computed again */
    }
    swathDataChanged();
  });
  // the boxes and plots in the main 3D: a lazy chunk with three.js, attached to every new scene
  let detach: (() => void) | null = null;
  let generation = 0;
  onScene((scene) => {
    const mine = ++generation;
    detach?.();
    detach = null;
    if (!scene) return;
    void import("./scene").then((m) => {
      // a release of a handle: `stats.swath` with the angles, through the registry (the log, the notice)
      if (mine === generation) detach = m.attachSwathScene(scene, async (args) => (await app.registry.execute("stats.swath", { ...args }, "user")).ok);
    });
  });
}

/** The swath windows for the shell's window layer (`shell/Shell.tsx`); null — not a swath window. */
export function renderSwathWindow(w: FloatWin): ReactNode | null {
  if (w.kind !== SWATH_WINDOW) return null;
  const p = w.props ?? {};
  const swathId = typeof p.swathId === "number" ? p.swathId : null;
  const row = swathId !== null ? swathsStore.get().swaths.find((c) => c.id === swathId) : undefined;
  const settings = normalizeSwath(p.settings ?? row?.settings ?? {});
  return createElement(SwathWindow, { winId: w.id, swathId, settings, readOnly: p.readOnly === true, saveError: typeof p.saveError === "string" ? p.saveError : null });
}
