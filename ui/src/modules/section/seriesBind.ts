/**
 * The handlers of `series.*` (ADR 0016 §4) and the keys of the 3D window: `]` / `[` are ribbon keys (k ± 1, a
 * polyline — its segments, without a series — the free line one window on, as `section.step`); `Home` / `End` /
 * `Ctrl+G` work only while the focus is in the 3D window — not in a text field, the tree, a list, a table or a
 * floating window — and never under a modal window. At an edge k stays and the status line says so.
 */
import type { App } from "../../shell/app";
import type { CommandContext, CommandSource } from "../../shell/commands";
import { dialogStore } from "../../shell/dialogs";
import { eventShortcut, type KeyLike } from "../../shell/keys";
import { viewStore } from "../scene/bind";
import { holeSection, stepProfile } from "./bind";
import { edgeText, moveIndex, resolveGoto, type Move } from "./goto";
import { needSectionToLock, runLock } from "./lockView";
import { faultText, seriesApi, seriesFault, type ShadowMode } from "./seriesApi";
import { activateSeries, copySeries, deleteSeries, openGoto, openNewSeries, openSeriesManager, seriesSaved } from "./seriesActions";
import { PRINT_REASON } from "./seriesCommands";
import { fmt, formFromLine, formFromSection, newForm, rangeOver, saveSeries, SHADOW_TITLES, type SeriesForm } from "./seriesForm";
import { boxCorners, currentSection, ensureTable, loadSeries, putSeries, seriesById, seriesStatus, setActiveSeries, visibleBoxes } from "./seriesState";
import { freeLine } from "./state";

export const NO_SERIES = "Нет активной серии разрезов: выберите её в «Менеджере серий» или создайте «Новую серию»";
const NO_SECTION = "Сначала задайте разрез: линию (S) или серию";

const needProject = (ctx: CommandContext) => (ctx.hasProject ? null : "Откройте или создайте проект");
const needSeries = (ctx: CommandContext) => needProject(ctx) ?? (currentSection() ? null : NO_SERIES);
const needSection = (ctx: CommandContext) => needProject(ctx) ?? (currentSection() || freeLine(viewStore.get()) ? null : NO_SECTION);
const needLine = (ctx: CommandContext) => needProject(ctx) ?? (freeLine(viewStore.get()) ? null : "Сначала проведите линию разреза (S)");

const int = (v: unknown, what: string): number => {
  if (typeof v !== "number" || !Number.isInteger(v)) throw new Error(`${what}: нужно целое число`);
  return v;
};

/** A move along the active series; without one `]` / `[` move the free line one window on. */
export function moveSeries(app: App, move: Move): string {
  const cur = currentSection();
  const note = (t: string) => (app.store.set({ statusNote: t }), t);
  if (!cur) {
    if ((move === 1 || move === -1) && freeLine(viewStore.get())) return stepProfile(move);
    throw new Error(move === 1 || move === -1 ? NO_SECTION : NO_SERIES);
  }
  const poly = cur.seg !== null;
  const r = moveIndex(cur.index, cur.steps, move);
  if (r.edge) return note(edgeText(move as 1 | -1, poly));
  if (r.index !== cur.index) setActiveSeries({ id: cur.row.id, k: poly ? 0 : r.index, seg: poly ? r.index : null });
  app.store.set({ statusNote: null }); // the status line shows the section itself (an edge message goes)
  return seriesStatus() ?? "";
}

/** The form of `series.create` from its flat arguments; what is not given — the defaults of «Новая серия». */
export function createForm(a: Record<string, unknown>): SeriesForm {
  const f = newForm(freeLine(viewStore.get()), []);
  const s = (v: unknown) => (typeof v === "number" ? fmt(v) : undefined);
  const set: Partial<SeriesForm> = { type: a.type as SeriesForm["type"], name: typeof a.name === "string" ? a.name : "" };
  const map: [string, keyof SeriesForm][] = [["x1", "p1x"], ["y1", "p1y"], ["z1", "p1z"], ["x2", "p2x"], ["y2", "p2y"], ["az", "az"], ["phi", "phi"], ["s0", "s0"], ["s1", "s1"], ["step", "step"], ["towards", "towards"], ["away", "away"], ["top", "top"], ["bottom", "bottom"], ["corridor", "corridor"]];
  for (const [arg, field] of map) if (s(a[arg]) !== undefined) (set as Record<string, string>)[field] = s(a[arg])!;
  if (a.towards !== undefined || a.away !== undefined) set.step = s(a.step) ?? fmt(Number(a.towards ?? 25) + Number(a.away ?? 25));
  if (typeof a.polyline === "string") set.polyline = a.polyline;
  if (typeof a.template === "string") set.template = a.template;
  if (typeof a.shadow === "string") set.shadow = a.shadow as ShadowMode;
  set.dir = a.x2 !== undefined || a.y2 !== undefined ? "p2" : "az";
  const out = { ...f, ...set };
  const ranged = a.s0 === undefined && a.s1 === undefined && a.top === undefined && a.bottom === undefined;
  return ranged ? { ...out, ...rangeOver(out, boxCorners(visibleBoxes())) } : out;
}

export function bindSeries(app: App): void {
  const r = app.registry;
  const keys = () => viewStore.get();
  const hidden = () => app.store.get().view.hidden;

  r.bind(
    "series.manager",
    () => {
      openSeriesManager(currentSection()?.row.id);
      return "Открыт «Менеджер серий»";
    },
    needProject,
  );
  r.bind(
    "series.new",
    () => {
      openNewSeries(newForm(freeLine(keys()), boxCorners(visibleBoxes(hidden()))));
      return "Открыто окно «Новая серия»";
    },
    needProject,
  );
  r.bind(
    "series.from_line",
    () => {
      const line = freeLine(keys());
      if (!line) throw new Error("Сначала проведите линию разреза (S)");
      openNewSeries(formFromLine(line), "Серия из линии разреза");
      return "Открыто окно «Новая серия» по линии разреза";
    },
    needLine,
  );
  r.bind(
    "series.through_hole",
    (a) => {
      const { line, name } = holeSection(app, a);
      openNewSeries(formFromLine(line, `Через скважину ${name}`), "Серия через скважину");
      return `Открыто окно «Новая серия» через скважину «${name}»`;
    },
    needProject,
  );
  r.bind(
    "series.from_section",
    () => {
      const cur = currentSection();
      const line = freeLine(keys());
      if (!cur && !line) throw new Error(NO_SECTION);
      openNewSeries(cur ? formFromSection(cur) : formFromLine(line!), "Серия из одного разреза");
      return "Открыто окно «Новая серия» из одного разреза";
    },
    needSection,
  );

  r.bind(
    "series.create",
    async (a) => {
      const res = await saveSeries({ mode: "create" }, createForm(a));
      if (res.ok) return seriesSaved(res.row, true);
      throw new Error("error" in res ? `${res.error}${res.hint ? ` ${res.hint}` : ""}` : "Серия не создана");
    },
    needProject,
  );
  r.bind(
    "series.copy",
    async (a) => {
      const row = await copySeries(int(a.series_id, "Номер серии"));
      return `Создана копия «${row.name}» (серия ${row.number})`;
    },
    needProject,
  );
  r.bind(
    "series.delete",
    async (a, source: CommandSource) => {
      const id = int(a.series_id, "Номер серии");
      const name = seriesById(id)?.name ?? String(id);
      return (await deleteSeries(id, source !== "claude")) ? `Серия «${name}» удалена; её контуры остались` : "Удаление серии отменено";
    },
    needProject,
  );
  r.bind(
    "series.activate",
    (a) => {
      const text = activateSeries(int(a.series_id, "Номер серии"), a.k === undefined ? 0 : int(a.k, "Номер разреза") - 1);
      app.store.set({ statusNote: text });
      return text;
    },
    needProject,
  );

  r.bind("series.first", () => moveSeries(app, "first"), needSeries);
  r.bind("series.last", () => moveSeries(app, "last"), needSeries);
  r.bind("series.prev", () => moveSeries(app, -1), needSection);
  r.bind("series.next", () => moveSeries(app, 1), needSection);
  r.bind(
    "series.goto",
    async (a) => {
      const cur = currentSection();
      if (!cur) throw new Error(NO_SERIES);
      if (a.name === undefined && a.number === undefined) {
        openGoto();
        return "Открыто окно «Перейти к разрезу»";
      }
      const table = await ensureTable(cur.row.id);
      const what = typeof a.number === "number" ? a.number : String(a.name ?? "");
      const k = resolveGoto(table.sections, what);
      if (k === null) throw new Error(`Разрез «${what}» не найден в серии «${cur.row.name}»: имя разреза или номер от 1 до ${cur.steps}`);
      const text = activateSeries(cur.row.id, k);
      app.store.set({ statusNote: seriesStatus() ?? text });
      return seriesStatus() ?? text;
    },
    needSeries,
  );

  r.bind("series.lock", (a) => runLock(a), needSectionToLock);
  r.bind(
    "series.shadow",
    async (a) => {
      const cur = currentSection();
      if (!cur) throw new Error(NO_SERIES);
      const mode = a.mode as ShadowMode;
      if (!(mode in SHADOW_TITLES)) throw new Error("Тень: none, prev, next или both");
      try {
        putSeries(await seriesApi.patch(cur.row.id, cur.row.version, { params: { shadow: mode } }));
      } catch (e) {
        const f = seriesFault(e);
        if (f.status === 409) await loadSeries();
        throw new Error(faultText(f));
      }
      return `Тень серии «${cur.row.name}»: ${SHADOW_TITLES[mode].toLowerCase()}`;
    },
    needSeries,
  );
  r.bind(
    "series.print",
    () => {
      throw new Error(PRINT_REASON);
    },
    () => PRINT_REASON,
  );

  bindSeriesKeys(app);
}

// ---------------------------------------------------------------- keys of the 3D window

/** The keys that act only while the focus is in the 3D window. */
export const SCENE_KEYS: Readonly<Record<string, string>> = { home: "series.first", end: "series.last", "ctrl+g": "series.goto" };

interface KeyTargetLike {
  tagName?: string;
  isContentEditable?: boolean;
  closest?: (selector: string) => unknown;
}

const OUTSIDE_SCENE = '[role="tree"],[role="treegrid"],[role="listbox"],[role="grid"],[role="menu"],[role="dialog"],table';

/** The focus is in the 3D window: the page itself, the viewport, or a button of the ribbon — not a field or a list. */
export function sceneFocused(target: unknown): boolean {
  const t = target as KeyTargetLike | null;
  if (!t || !t.tagName || t.tagName === "BODY" || t.tagName === "HTML") return true;
  if (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName)) return false;
  if (t.closest?.('[data-testid="scene-host"]')) return true;
  return !t.closest?.(OUTSIDE_SCENE);
}

/** The command of a key of the 3D window, or null (another key, a text field, a list, a modal window). */
export function sceneKey(e: KeyLike & { target?: unknown; repeat?: boolean }, modal: boolean): string | null {
  const command = SCENE_KEYS[eventShortcut(e)];
  if (!command || modal || e.repeat || !sceneFocused(e.target)) return null;
  return command;
}

let offKeys: (() => void) | null = null;

function bindSeriesKeys(app: App): void {
  offKeys?.();
  offKeys = null;
  if (typeof window === "undefined") return;
  const onKey = (e: KeyboardEvent) => {
    if (e.defaultPrevented) return;
    const modal = app.palette.get().query !== null || dialogStore.get().queue.length > 0 || app.store.get().modal > 0;
    const command = sceneKey(e, modal);
    if (!command || app.registry.reason(command)) return;
    e.preventDefault();
    void app.run(command);
  };
  window.addEventListener("keydown", onKey);
  offKeys = () => window.removeEventListener("keydown", onKey);
}
