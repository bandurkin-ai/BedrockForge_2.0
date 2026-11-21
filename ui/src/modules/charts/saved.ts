/**
 * Saved charts (ТЗ 7.28; ADR 0019 §2): a chart opened by its command is saved at once into its set → «Графики»
 * (`POST /charts`, `auto_name`); an edit of its settings is saved 400 ms after the last one (`PUT` with
 * `expected_version`; while the name follows the settings — `auto_name: <имя>`, the core makes a taken one « (2)»).
 * A 409 `version_conflict` — the window takes the settings of the server. A project open only for reading — the
 * window computes and draws, not saving (a note in the window). `useSavedChart` ties a window to its chart: the
 * title follows the stored name (or the name of the settings while they follow), the notes tell what is not saved.
 */
import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { CoreError } from "../../shell/api";
import { errorText } from "../../shell/props";
import { updateWindow } from "../../shell/windows";
import { projectStore } from "../projects/state";
import { createChart, updateChart, type ChartRow } from "./api";
import type { ChartWindowProps } from "./bind";
import { chartsStore, upsertChart } from "./state";
import { chartName, normalizeSettings, windowTitle, type ChartKind, type SettingsOf } from "./settings";

export const SAVE_DELAY_MS = 400;

/** A new chart of the set with these settings; its name follows them (`auto_name`). */
export async function saveNewChart<K extends ChartKind>(kind: K, settings: SettingsOf[K]): Promise<ChartRow> {
  const row = await createChart({ set_id: settings.set_id, kind, name: chartName(kind, settings), settings, auto_name: true });
  upsertChart(row);
  return row;
}

export type Clock = { set: (fn: () => void, ms: number) => unknown; clear: (t: unknown) => void };
const REAL_CLOCK: Clock = { set: (fn, ms) => setTimeout(fn, ms), clear: (t) => clearTimeout(t as ReturnType<typeof setTimeout>) };

type Put = typeof updateChart;

export interface SaverEvents {
  /** the core stored the settings (the row as it is now) */
  saved?: (row: ChartRow) => void;
  /** the chart was changed elsewhere: its settings replace the window's */
  conflict?: (row: ChartRow) => void;
  failed?: (e: unknown) => void;
}

export interface Saver<K extends ChartKind> {
  /** New settings of the window: saved `delay` ms after the last change; the stored ones are not sent again. */
  change(settings: SettingsOf[K]): void;
  /** The row as the project has it now (after a rename, another edit): a newer version is taken. */
  sync(row: ChartRow): void;
  stop(): void;
}

/**
 * The autosave of one chart: one `PUT` at a time, the newest settings after the one in flight; `expected_version`
 * — the last version known; while the chart's name follows its settings the name of the new settings goes along.
 */
export function createSaver<K extends ChartKind>(kind: K, chart: ChartRow, events: SaverEvents = {}, put: Put = updateChart, delay = SAVE_DELAY_MS, clock: Clock = REAL_CLOCK): Saver<K> {
  const setId = chart.set_id ?? 0;
  const key = (s: unknown) => JSON.stringify(normalizeSettings(kind, s, setId));
  let version = chart.version;
  let auto = chart.auto_name;
  let stored = key(chart.settings);
  let pending: SettingsOf[K] | null = null;
  let timer: unknown = null;
  let flying = false;
  let alive = true;

  const schedule = (ms: number) => {
    if (timer !== null) clock.clear(timer);
    timer = clock.set(() => {
      timer = null;
      void flush();
    }, ms);
  };

  const take = (row: ChartRow) => {
    version = row.version;
    auto = row.auto_name;
    stored = key(row.settings);
  };

  async function flush(): Promise<void> {
    if (!alive || flying || pending === null) return;
    const settings = pending;
    pending = null;
    if (key(settings) === stored) return;
    flying = true;
    try {
      const row = await put(chart.id, { settings, ...(auto ? { auto_name: chartName(kind, settings) } : {}), expected_version: version });
      if (!alive) return;
      take(row);
      upsertChart(row);
      events.saved?.(row);
    } catch (e) {
      if (!alive) return;
      const current = e instanceof CoreError && e.code === "version_conflict" ? (e.body?.chart as ChartRow | undefined) : undefined;
      if (current && typeof current.version === "number") {
        take(current);
        upsertChart(current);
        pending = null; // the server's settings win (ADR 0019 §2)
        events.conflict?.(current);
      } else events.failed?.(e);
    } finally {
      flying = false;
      if (alive && pending !== null) schedule(0);
    }
  }

  return {
    change(settings) {
      if (!alive) return;
      if (pending === null && !flying && key(settings) === stored) {
        if (timer !== null) clock.clear(timer);
        timer = null;
        return;
      }
      pending = settings;
      schedule(delay);
    },
    sync(row) {
      if (!alive || row.id !== chart.id || row.version <= version) return;
      const other = key(row.settings) !== stored;
      take(row);
      if (other && !flying) {
        pending = null;
        if (timer !== null) clock.clear(timer);
        timer = null;
        events.conflict?.(row);
      }
    },
    stop() {
      alive = false;
      if (timer !== null) clock.clear(timer);
    },
  };
}

const getProject = () => projectStore.get().current;
const getCharts = () => chartsStore.get();

/** The title of a saved chart: its stored name, or the name of the settings while it follows them and they are not stored yet. */
export function savedTitle<K extends ChartKind>(kind: K, settings: SettingsOf[K], row: ChartRow | undefined): string {
  if (!row) return chartName(kind, settings);
  if (!row.auto_name) return row.name;
  const setId = row.set_id ?? settings.set_id;
  return JSON.stringify(normalizeSettings(kind, row.settings, setId)) === JSON.stringify(normalizeSettings(kind, settings, setId)) ? row.name : chartName(kind, settings);
}

/**
 * Ties a chart window to its saved chart: saves its settings, takes the server's on a conflict, keeps the title.
 * `note` — what the window says about saving (null — all is saved).
 */
export function useSavedChart<K extends ChartKind>(kind: K, props: ChartWindowProps<K>, settings: SettingsOf[K], setSettings: (s: SettingsOf[K]) => void): { note: string | null } {
  const { winId, setName, chartId } = props;
  const project = useSyncExternalStore(projectStore.subscribe, getProject, getProject);
  const charts = useSyncExternalStore(chartsStore.subscribe, getCharts, getCharts);
  const row = chartId ? charts.charts.find((c) => c.id === chartId) : undefined;
  const readOnly = props.readOnly === true || project?.read_only === true;
  const [note, setNote] = useState<string | null>(null);
  const first = useMemo(() => (chartId ? (charts.charts.find((c) => c.id === chartId) ?? null) : null), [chartId]); // eslint-disable-line react-hooks/exhaustive-deps

  const saver = useMemo(() => {
    if (!first || readOnly) return null;
    return createSaver(kind, first, {
      saved: () => setNote(null),
      conflict: (r) => {
        setSettings(normalizeSettings(kind, r.settings, r.set_id ?? settings.set_id));
        setNote("График изменили в другом месте — показаны сохранённые настройки.");
      },
      failed: (e) => setNote(`Настройки не сохранены: ${errorText(e)}`),
    });
  }, [first, readOnly]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => saver?.stop(), [saver]);
  useEffect(() => saver?.change(settings), [saver, settings]);
  useEffect(() => {
    if (row) saver?.sync(row);
  }, [saver, row]);

  const title = savedTitle(kind, settings, row);
  useEffect(() => updateWindow(winId, { title: windowTitle(title, setName) }), [winId, title, setName]);

  if (readOnly) return { note: "Проект открыт только для чтения: график не сохраняется." };
  if (props.saveError) return { note: `График не сохранён: ${props.saveError}` };
  if (chartId && charts.loaded && !row) return { note: "График удалён из набора — настройки не сохраняются." };
  return { note };
}
