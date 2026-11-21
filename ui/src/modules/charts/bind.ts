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
import { openWindow, type FloatWin } from "../../shell/windows";
import { tablesStore } from "../tables/state";
import { histogramRequest, histogramSummary } from "../../charts/histogram/model";
import { HistogramWindow } from "../../charts/histogram/HistogramWindow";
import { qqRequest, qqSummary } from "../../charts/qq/model";
import { QQWindow } from "../../charts/qq/QQWindow";
import { computeHistogram, computeQQ, defaultField, loadFields } from "./api";
import { CHART_WINDOW_PREFIX } from "./commands";
import { chartName, isChartKind, settingsFromArgs, windowTitle, type ChartKind, type SettingsOf } from "./settings";

/** The props every chart window gets from the shell. */
export interface ChartWindowProps<K extends ChartKind = ChartKind> {
  winId: string;
  setId: number;
  setName: string;
  settings: SettingsOf[K];
}

export interface ChartWindowSpec<K extends ChartKind = ChartKind> {
  render: (props: ChartWindowProps<K>) => ReactNode;
  /** The assistant's text for these settings: computes the chart (13.22: name, n, mean, median, CV). */
  summary?: (settings: SettingsOf[K]) => Promise<string>;
  /** Fills what the defaults leave empty (the first component) before the window opens. */
  prepare?: (settings: SettingsOf[K]) => Promise<SettingsOf[K]>;
}

/** The first component of the table when no field is given (no answer of the core — the window tells why). */
async function withDefaultField<S extends { set_id: number; table: string | null; field: string | null }>(s: S): Promise<S> {
  if (s.field) return s;
  try {
    const f = await loadFields(s.set_id, s.table);
    return { ...s, table: s.table ?? f.table, field: defaultField(f) };
  } catch {
    return s;
  }
}

/** The chart windows made so far; a kind not here keeps its command unavailable. */
export const CHART_WINDOWS: { [K in ChartKind]?: ChartWindowSpec<K> } = {
  histogram: {
    render: (p) => createElement(HistogramWindow, p),
    prepare: withDefaultField,
    summary: async (s) => {
      const body = histogramRequest(s);
      if (!body) throw refusal(s.field ? "bad_param" : "field_not_found", s.field ? "Не задано поле веса." : "В таблице нет числовых полей.", s.field ? "Укажите weight_field или другой вес." : "Укажите таблицу с пробами и поле.");
      return histogramSummary(chartName("histogram", s), await computeHistogram(body));
    },
  },
  qq: {
    render: (p) => createElement(QQWindow, p),
    prepare: withDefaultField,
    summary: async (s) => {
      const body = qqRequest(s);
      if (!body) throw refusal(s.field ? "bad_param" : "field_not_found", s.field ? "Не задано поле веса." : "В таблице нет числовых полей.", s.field ? "Укажите поле веса или другой вес." : "Укажите таблицу с пробами и поле.");
      return qqSummary(chartName("qq", s), await computeQQ(body));
    },
  },
};

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

/** Opens the window of a chart with its settings; the same settings already open come to the front. */
export function openChartWindow<K extends ChartKind>(kind: K, settings: SettingsOf[K], setName: string): string {
  return openWindow(chartWindowKind(kind), windowTitle(chartName(kind, settings), setName), "normal", { setId: settings.set_id, setName, settings }, ...viewport());
}

async function runChart<K extends ChartKind>(kind: K, spec: ChartWindowSpec<K>, args: Record<string, unknown>, source: CommandSource): Promise<string> {
  const set = chartSet(args);
  let settings = settingsFromArgs(kind, args, set.id);
  if (spec.prepare) settings = await spec.prepare(settings);
  openChartWindow(kind, settings, set.name);
  const name = chartName(kind, settings);
  if (source === "claude" && spec.summary) return spec.summary(settings);
  return `Открыто окно «${windowTitle(name, set.name)}»`;
}

export function bindCharts(app: App): void {
  for (const kind of Object.keys(CHART_WINDOWS)) {
    if (!isChartKind(kind)) continue;
    const spec = CHART_WINDOWS[kind] as ChartWindowSpec;
    app.registry.bind(`chart.${kind}`, noted(app, (a, source) => runChart(kind, spec, a, source)), chartReason);
  }
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
  return spec.render({ winId: w.id, setId, setName: String(p.setName ?? ""), settings: settingsFromArgs(kind, (p.settings ?? {}) as Record<string, unknown>, setId) });
}
