/**
 * Handlers of the chart commands (ADR 0019 §9) and the chart windows of the shell. A chart command opens its
 * floating window («‹имя графика› · ‹набор›», kind `chart-<kind>`) with the settings from its arguments over the
 * defaults (no arguments — the first component of the active set); the assistant gets a short text: the name,
 * the number of samples, mean, median, CV. A kind is bound once its window is in `CHART_WINDOWS` — until then
 * its command stays unavailable with the stage (`commands.ts`).
 */
import { createElement, type ReactNode } from "react";
import type { App } from "../../shell/app";
import type { CommandContext, CommandSource } from "../../shell/commands";
import { needProject, noted, refusal, viewport } from "../../shell/objectRun";
import { errorText } from "../../shell/props";
import { focusWindow, openWindow, windowStore, type FloatWin } from "../../shell/windows";
import { tablesStore } from "../tables/state";
import { histogramRequest, histogramSummary } from "../../charts/histogram/model";
import { HistogramWindow } from "../../charts/histogram/HistogramWindow";
import { qqRequest, qqSummary } from "../../charts/qq/model";
import { QQWindow } from "../../charts/qq/QQWindow";
import { dendrogramRequest, dendrogramSummary } from "../../charts/dendrogram/model";
import { DendrogramWindow } from "../../charts/dendrogram/DendrogramWindow";
import { scatterRequest, scatterSummary } from "../../charts/scatter/model";
import { ScatterWindow, secondField } from "../../charts/scatter/ScatterWindow";
import { onCoreEvent } from "../jobs/state";
import { projectStore } from "../projects/state";
import { computeDendrogram, computeHistogram, computeQQ, defaultField, loadFields, type ChartRow, type DendrogramResult, type HistogramResult, type QQResult } from "./api";
import { computeScatter, type ScatterResult } from "./apiMore";
import { bindSavedCharts } from "./open";
import { saveNewChart } from "./saved";
import { chartChanged, chartsStore, componentsReason, dataChanged, loadCharts, readComponents } from "./state";
import { CHART_WINDOW_PREFIX } from "./commands";
import { chartName, isChartKind, settingsFromArgs, windowTitle, type ChartKind, type SettingsOf } from "./settings";

/** The props every chart window gets from the shell. */
export interface ChartWindowProps<K extends ChartKind = ChartKind> {
  winId: string;
  setId: number;
  setName: string;
  settings: SettingsOf[K];
  /** the saved chart of the window (ТЗ 7.28); none — not saved (read-only project, a failed save) */
  chartId?: number | null;
  /** why the chart was not saved when it opened */
  saveError?: string | null;
  readOnly?: boolean;
}

export interface ChartWindowSpec<K extends ChartKind = ChartKind> {
  render: (props: ChartWindowProps<K>) => ReactNode;
  /** The assistant's text for these settings under the chart's name: computes the chart (name, n, mean, median, CV). */
  summary?: (settings: SettingsOf[K], name: string) => Promise<string>;
  /** The same text from the answer of `POST /stats/chart/{id}` (`chart.open` from the assistant). */
  text?: (result: unknown, name: string, settings: SettingsOf[K]) => string;
  /** Fills what the defaults leave empty (the first component) before the window opens. */
  prepare?: (settings: SettingsOf[K]) => Promise<SettingsOf[K]>;
  /** Why the kind cannot be built besides `chartReason` (the dendrogram: fewer than 2 components). */
  reason?: (ctx: CommandContext) => string | null;
}

/** The table (its `name_key`: the reference stays when tables come and go) and its first component when not given (no answer of the core — the window tells why). */
async function withDefaultField<S extends { set_id: number; table: string | null; field: string | null }>(s: S): Promise<S> {
  if (s.field && s.table) return s;
  try {
    const f = await loadFields(s.set_id, s.table);
    return { ...s, table: s.table ?? f.table, field: s.field ?? defaultField(f) };
  } catch {
    return s;
  }
}

/** The chart windows made so far; a kind not here keeps its command unavailable. */
export const CHART_WINDOWS: { [K in ChartKind]?: ChartWindowSpec<K> } = {
  histogram: {
    render: (p) => createElement(HistogramWindow, p),
    prepare: withDefaultField,
    text: (r, name) => histogramSummary(name, r as HistogramResult),
    summary: async (s, name) => {
      const body = histogramRequest(s);
      if (!body) throw refusal(s.field ? "bad_param" : "field_not_found", s.field ? "Не задано поле веса." : "В таблице нет числовых полей.", s.field ? "Укажите weight_field или другой вес." : "Укажите таблицу с пробами и поле.");
      return histogramSummary(name, await computeHistogram(body));
    },
  },
  qq: {
    render: (p) => createElement(QQWindow, p),
    prepare: withDefaultField,
    text: (r, name) => qqSummary(name, r as QQResult),
    summary: async (s, name) => {
      const body = qqRequest(s);
      if (!body) throw refusal(s.field ? "bad_param" : "field_not_found", s.field ? "Не задано поле веса." : "В таблице нет числовых полей.", s.field ? "Укажите поле веса или другой вес." : "Укажите таблицу с пробами и поле.");
      return qqSummary(name, await computeQQ(body));
    },
  },
  dendrogram: {
    render: (p) => createElement(DendrogramWindow, p),
    prepare: withComponents,
    reason: () => componentsReason(tablesStore.get().setId),
    text: (r, name, s) => dendrogramSummary(name, r as DendrogramResult, s.cut),
    summary: async (s, name) => {
      const f = await loadFields(s.set_id, s.table);
      const body = dendrogramRequest(s, f.numeric);
      if (!body) throw refusal("too_few_fields", "Нужно 2 и больше поля.", "Укажите fields или таблицу с компонентами.");
      return dendrogramSummary(name, await computeDendrogram(body), s.cut);
    },
  },
  scatter: {
    render: (p) => createElement(ScatterWindow, p),
    prepare: withDefaultXY,
    text: (r, name, s) => scatterSummary(name, r as ScatterResult, s.regression),
    summary: async (s, name) => {
      const body = scatterRequest(s);
      if (!body) throw refusal(s.x && s.y ? "bad_param" : "field_not_found", s.x && s.y ? "Не задано поле веса." : "Нужно два числовых поля.", s.x && s.y ? "Укажите поле веса или другой вес." : "Укажите x и y.");
      return scatterSummary(name, await computeScatter(body), s.regression);
    },
  },
};

/** The table and fields X (the first component) and Y (the next one) when not given. */
async function withDefaultXY(s: SettingsOf["scatter"]): Promise<SettingsOf["scatter"]> {
  if (s.x && s.y && s.table) return s;
  try {
    const f = await loadFields(s.set_id, s.table);
    const x = s.x ?? defaultField(f);
    return { ...s, table: s.table ?? f.table, x, y: s.y ?? (s.y_table ? null : secondField(f, x)) };
  } catch {
    return s;
  }
}

/** Without a table — the first table with 2 and more components (else the set's first): the dendrogram opens there. */
async function withComponents(s: SettingsOf["dendrogram"]): Promise<SettingsOf["dendrogram"]> {
  if (s.table) return s;
  try {
    const f = await loadFields(s.set_id);
    const t = f.tables.find((x) => x.components >= 2) ?? f.tables[0];
    return t ? { ...s, table: t.key } : s;
  } catch {
    return s;
  }
}

export const chartWindowKind = (kind: ChartKind): string => `${CHART_WINDOW_PREFIX}${kind}`;

/** Why a chart cannot be built now (ADR 0019 §9): no project, no active set, the set has no sample tables. */
export function chartReason(ctx: CommandContext): string | null {
  const base = needProject(ctx);
  if (base) return base;
  if (!ctx.hasSet) return "Нет активного набора скважин";
  const tabs = tablesStore.get().tabs;
  if (tabs.length && !tabs.some((t) => t.kind === "assay" || t.kind === "interval")) return "В наборе нет таблиц проб";
  return null;
}

/** The set of a chart: `set_id` of the arguments, else the active one. */
export function chartSet(args: Record<string, unknown>): { id: number; name: string } {
  const t = tablesStore.get();
  const id = typeof args.set_id === "number" ? args.set_id : t.setId;
  if (id === null) throw refusal("set_not_found", "Нет активного набора скважин.", "Выберите набор в дереве или укажите set_id.");
  const set = t.sets.find((s) => s.id === id);
  if (!set && typeof args.set_id === "number" && t.sets.length) throw refusal("set_not_found", `Набор скважин №${id} не найден.`, "Номера наборов — в дереве проекта.");
  return { id, name: set?.name ?? `Набор ${id}` };
}

/**
 * Opens the window of a chart with its settings. A saved chart has one window: open — it comes to the front;
 * unsaved settings already open come to the front too.
 */
export function openChartWindow<K extends ChartKind>(kind: K, settings: SettingsOf[K], setName: string, saved: { chart?: ChartRow | null; error?: string | null; readOnly?: boolean } = {}): string {
  const chart = saved.chart ?? null;
  const open = chart ? windowStore.get().windows.find((w) => w.props?.chartId === chart.id) : undefined;
  if (open) {
    focusWindow(open.id);
    return open.id;
  }
  const props = { setId: settings.set_id, setName, settings, ...(chart ? { chartId: chart.id } : {}), ...(saved.error ? { saveError: saved.error } : {}), ...(saved.readOnly ? { readOnly: true } : {}) };
  return openWindow(chartWindowKind(kind), windowTitle(chart?.name ?? chartName(kind, settings), setName), "normal", props, ...viewport());
}

/** «Гистограмма · AU · по длине» (график №12). */
export const savedName = (name: string, chart: ChartRow | null): string => (chart ? `«${chart.name}» (график №${chart.id})` : `«${name}»`);

async function runChart<K extends ChartKind>(app: App, kind: K, spec: ChartWindowSpec<K>, args: Record<string, unknown>, source: CommandSource): Promise<string> {
  const set = chartSet(args);
  let settings = settingsFromArgs(kind, args, set.id);
  if (spec.prepare) settings = await spec.prepare(settings);
  // saved at once (ТЗ 7.28); a project open only for reading — computed and drawn, not saved
  let chart: ChartRow | null = null;
  let error: string | null = null;
  const readOnly = app.store.get().projectReadOnly;
  if (!readOnly) {
    try {
      chart = await saveNewChart(kind, settings);
    } catch (e) {
      error = errorText(e);
    }
  }
  openChartWindow(kind, settings, set.name, { chart, error, readOnly });
  const name = chart?.name ?? chartName(kind, settings);
  const unsaved = error ? `; график не сохранён: ${error}` : "";
  if (source === "claude" && spec.summary) return `${await spec.summary(settings, chart ? savedName(name, chart) : name)}${unsaved}`;
  return `Открыто окно «${windowTitle(name, set.name)}»${unsaved}`;
}

export function bindCharts(app: App): void {
  for (const kind of Object.keys(CHART_WINDOWS)) {
    if (!isChartKind(kind)) continue;
    const spec = CHART_WINDOWS[kind] as ChartWindowSpec;
    app.registry.bind(`chart.${kind}`, noted(app, (a, source) => runChart(app, kind, spec, a, source)), (ctx) => chartReason(ctx) ?? spec.reason?.(ctx) ?? null);
  }
  // the components of the active set, again after an edit of its data
  let seen: number | null = null;
  tablesStore.subscribe(() => {
    const id = tablesStore.get().setId;
    if (id === seen) return;
    seen = id;
    if (id !== null) void readComponents(id);
  });
  bindSavedCharts(app);
  // the saved charts of the project: read when it opens
  let uid: string | null = null;
  projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    dataChanged();
    if (cur) void loadCharts();
    else chartsStore.set({ charts: [], loaded: false });
  });
  onCoreEvent((event, data) => {
    if (event !== "drillholes.changed" && event !== "objects.changed") return;
    let setId: number | undefined;
    try {
      const d = JSON.parse(data) as { set_id?: unknown; kind?: unknown; id?: unknown; deleted?: unknown; chart?: unknown };
      if (event === "objects.changed" && d.kind === "chart") return void chartChanged(d); // the data are the same
      if (event === "objects.changed") void loadCharts(); // a set deleted takes its charts
      if (event === "drillholes.changed" && typeof d.set_id === "number") setId = d.set_id;
    } catch {
      /* a malformed event: forget all */
    }
    dataChanged(setId);
    const active = tablesStore.get().setId;
    if (active !== null && (setId === undefined || setId === active)) void readComponents(active);
  });
}

/** The chart windows for the shell's window layer (`shell/Shell.tsx`); null — not a chart window. */
export function renderChartsWindow(w: FloatWin): ReactNode | null {
  if (!w.kind.startsWith(CHART_WINDOW_PREFIX)) return null;
  const kind = w.kind.slice(CHART_WINDOW_PREFIX.length);
  if (!isChartKind(kind)) return null;
  const spec = CHART_WINDOWS[kind] as ChartWindowSpec | undefined;
  if (!spec) return null;
  const p = w.props ?? {};
  const setId = Number(p.setId);
  const chartId = typeof p.chartId === "number" ? p.chartId : null;
  const saveError = typeof p.saveError === "string" ? p.saveError : null;
  return spec.render({ winId: w.id, setId, setName: String(p.setName ?? ""), settings: settingsFromArgs(kind, (p.settings ?? {}) as Record<string, unknown>, setId), chartId, saveError, readOnly: p.readOnly === true });
}
