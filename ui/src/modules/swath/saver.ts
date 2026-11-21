/**
 * Autosave of a swath plot (ТЗ 7.30 «настройки сохраняются сами»; ADR 0019 §2) — the rules of the saved charts
 * (`modules/charts/saved.ts`: the delay, one `PUT` at a time with `expected_version`, the name of the new settings
 * while it follows them, a 409 — the server's settings win) on the settings of a swath (`normalizeSwath`,
 * `swathName`): `createSaver` there is bound to the kinds of a set. `useSavedSwath` ties a window to its swath:
 * the title follows the stored name, the note says what is not saved.
 */
import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { CoreError } from "../../shell/api";
import { errorText } from "../../shell/props";
import { updateWindow } from "../../shell/windows";
import { SAVE_DELAY_MS, type Clock, type SaverEvents } from "../charts/saved";
import type { ChartRow } from "../charts/api";
import { projectStore } from "../projects/state";
import { updateSwath } from "./api";
import { normalizeSwath, swathName, swathTitle, type SwathSettings } from "./model";
import { swathsStore, upsertSwath } from "./state";

const REAL_CLOCK: Clock = { set: (fn, ms) => setTimeout(fn, ms), clear: (t) => clearTimeout(t as ReturnType<typeof setTimeout>) };

export interface SwathSaver {
  change(settings: SwathSettings): void;
  sync(row: ChartRow): void;
  stop(): void;
}

const key = (s: unknown) => JSON.stringify(normalizeSwath(s));

export function createSwathSaver(chart: ChartRow, events: SaverEvents = {}, put: typeof updateSwath = updateSwath, delay = SAVE_DELAY_MS, clock: Clock = REAL_CLOCK): SwathSaver {
  let version = chart.version;
  let auto = chart.auto_name;
  let stored = key(chart.settings);
  let pending: SwathSettings | null = null;
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
      const row = await put(chart.id, { settings, ...(auto ? { auto_name: swathName(settings) } : {}), expected_version: version });
      if (!alive) return;
      take(row);
      upsertSwath(row);
      events.saved?.(row);
    } catch (e) {
      if (!alive) return;
      const current = e instanceof CoreError && e.code === "version_conflict" ? (e.body?.chart as ChartRow | undefined) : undefined;
      if (current && typeof current.version === "number") {
        take(current);
        upsertSwath(current);
        pending = null;
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

/** The name on screen: the stored one, or that of the settings while it follows them and they are not stored yet. */
export function savedSwathName(settings: SwathSettings, row: ChartRow | undefined): string {
  if (!row) return swathName(settings);
  if (!row.auto_name) return row.name;
  return key(row.settings) === key(settings) ? row.name : swathName(settings);
}

const getProject = () => projectStore.get().current;
const getSwaths = () => swathsStore.get();

export interface SavedSwathProps {
  winId: string;
  swathId: number | null;
  readOnly?: boolean;
  saveError?: string | null;
}

export function useSavedSwath(props: SavedSwathProps, settings: SwathSettings, setSettings: (s: SwathSettings) => void): { note: string | null; name: string } {
  const { winId, swathId } = props;
  const project = useSyncExternalStore(projectStore.subscribe, getProject, getProject);
  const state = useSyncExternalStore(swathsStore.subscribe, getSwaths, getSwaths);
  const row = swathId ? state.swaths.find((c) => c.id === swathId) : undefined;
  const readOnly = props.readOnly === true || project?.read_only === true;
  const [note, setNote] = useState<string | null>(null);
  const first = useMemo(() => (swathId ? (state.swaths.find((c) => c.id === swathId) ?? null) : null), [swathId]); // eslint-disable-line react-hooks/exhaustive-deps

  const saver = useMemo(() => {
    if (!first || readOnly) return null;
    return createSwathSaver(first, {
      saved: () => setNote(null),
      conflict: (r) => {
        setSettings(normalizeSwath(r.settings));
        setNote("Swath plot изменили в другом месте — показаны сохранённые настройки.");
      },
      failed: (e) => setNote(`Настройки не сохранены: ${errorText(e)}`),
    });
  }, [first, readOnly]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => saver?.stop(), [saver]);
  useEffect(() => saver?.change(settings), [saver, settings]);
  useEffect(() => {
    if (row) saver?.sync(row);
  }, [saver, row]);

  const name = savedSwathName(settings, row);
  useEffect(() => updateWindow(winId, { title: swathTitle(name) }), [winId, name]);

  if (readOnly) return { note: "Проект открыт только для чтения: swath plot не сохраняется.", name };
  if (props.saveError) return { note: `Swath plot не сохранён: ${props.saveError}`, name };
  if (swathId && state.loaded && !row) return { note: "Swath plot удалён — настройки не сохраняются.", name };
  return { note, name };
}
