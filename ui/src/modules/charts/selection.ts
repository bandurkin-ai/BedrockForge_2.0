/**
 * The link of the selection of samples with the charts (ТЗ 8.3, № 237; ADR 0019 §8): a frame or a lasso on the
 * scatter (points — their rows; the density — the polygon, the core picks the rows), on the box plots and on the
 * log-probability plot (the range of values and the groups covered) makes the selection of the application
 * (`shell/sampleSelection.ts`) — the 3D layer (`selectionScene.ts`) and the plaque of «Таблица» follow it; the open
 * charts mark its points, and without one — the samples of the hole selected (`hole.select`). `chart.select_clear`,
 * `Esc` in the window of a chart and «Снять» of the plaque clear it.
 */
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { bridge } from "../../bridge";
import type { AxisKind } from "../../charts/core/axisScale";
import { selectInside, type AxisPolygon, type SelectTool } from "../../charts/core/lasso";
import { selectedRows } from "../../charts/scatter/model";
import type { App } from "../../shell/app";
import { needProject } from "../../shell/objectRun";
import { clearSelection, makeSelection, markedRows, refreshHoleSamples, resetSelection, revalidate, sampleSelectionStore, selectionNotice, setHoleSamples, setSelectionNotice, type SelectionInfo } from "../../shell/sampleSelection";
import { windowStore } from "../../shell/windows";
import { onCoreEvent } from "../jobs/state";
import { projectStore } from "../projects/state";
import { onScene } from "../scene/sceneHost";
import { showSelection, tablesStore } from "../tables/state";
import { bodyOf, type ProbPlotResult, type ScatterResult } from "./apiMore";
import { CHART_WINDOW_PREFIX } from "./commands";
import type { SelectionSceneHandle } from "./selectionScene";

declare global {
  interface Window {
    __bfSelection?: unknown;
  }
}

type Body = Record<string, unknown>;

const NO_SETTINGS = "Выделение не сделано: график ещё не посчитан с этими настройками. Повторите выделение.";

/**
 * The body of `POST /stats/selection` for a frame or a lasso on the scatter: the points drawn — their rows (the
 * table X of the settings); the density — the settings and the polygon (`rect` for a frame) in data units.
 */
export function scatterSelectionBody(r: ScatterResult, poly: AxisPolygon, tool: SelectTool | null, kx: AxisKind, ky: AxisKind): Body | null {
  const body = bodyOf(r);
  if (r.mode !== "density") {
    const rows = selectedRows(r, selectInside(r.x ?? [], r.y ?? [], poly, kx, ky));
    return { set_id: r.set_id, table: body?.table ?? null, rows };
  }
  if (!body) return null;
  const chart = { ...body, kind: "scatter" };
  if (tool === "rect") return { chart, rect: [Math.min(...poly.x), Math.max(...poly.x), Math.min(...poly.y), Math.max(...poly.y)] };
  return { chart, polygon: poly.x.map((x, i) => [x, poly.y[i]]) };
}

/** The boxes a frame covers (px of the plot, its slots of equal width); null — all of them. */
export function boxFrameGroups(labels: readonly (string | null)[], xa: number, xb: number, plotX0: number, plotX1: number): (string | null)[] | null {
  const lo = Math.min(xa, xb);
  const hi = Math.max(xa, xb);
  const slot = (plotX1 - plotX0) / (labels.length || 1);
  const out = labels.filter((_, i) => plotX0 + slot * (i + 1) > lo && plotX0 + slot * i < hi);
  return out.length === labels.length ? null : out;
}

/** The series of the log-probability plot with a point inside the frame (grade lo…hi, P plo…phi as 0…1); null — all. */
export function probFrameGroups(r: Pick<ProbPlotResult, "series">, xlo: number, xhi: number, plo: number, phi: number): (string | null)[] | null {
  const hit = r.series.filter((s) => s.points.some(([x, pc]) => x >= xlo && x <= xhi && pc / 100 >= plo && pc / 100 <= phi));
  return hit.length === r.series.length ? null : hit.map((s) => s.label);
}

/** The body for the box plots and the log-probability plot: their settings, the range of values and the groups. */
export function valueSelectionBody(kind: "box" | "probplot", result: object, range: [number, number], groups: (string | null)[] | null): Body | null {
  const body = bodyOf(result);
  if (!body) return null;
  return { chart: { ...body, kind }, range: [Math.min(...range), Math.max(...range)], groups };
}

/** The store read by the charts (with the server snapshot: the tests render them to markup). */
function useSelectionStore<R>(select: (s: ReturnType<typeof sampleSelectionStore.get>) => R): R {
  const get = () => select(sampleSelectionStore.get());
  return useSyncExternalStore(sampleSelectionStore.subscribe, get, get);
}

/** The rows the open charts mark now (ascending ids), or null. */
export const useMarkedRows = (): Float64Array | null => useSelectionStore(markedRows);

const PENDING = "\u0000pending";

/**
 * A chart and the selection it made: `select(body)` makes it (the old one goes); `ours` — the selection of the
 * application is this chart's; `onLost` — once it went (cleared, replaced elsewhere): the chart drops its frame.
 */
export function useChartSelection(onLost?: () => void): { select: (body: Body | null) => Promise<SelectionInfo | null>; ours: boolean; pending: boolean } {
  const token = useSelectionStore((s) => s.current?.token ?? null);
  const made = useRef<string | null>(null);
  const [mine, setMine] = useState<string | null>(null);
  const lost = useRef(onLost);
  lost.current = onLost;
  const select = useCallback(async (body: Body | null) => {
    if (!body) {
      selectionNotice(NO_SETTINGS);
      return null;
    }
    made.current = PENDING;
    setMine(PENDING);
    const info = await makeSelection(body);
    made.current = info?.token ?? null;
    setMine(made.current);
    if (!info) lost.current?.();
    return info;
  }, []);
  useEffect(() => {
    const m = made.current;
    if (m && m !== PENDING && m !== token) {
      made.current = null;
      setMine(null);
      lost.current?.();
    }
  }, [token]);
  return { select, ours: mine !== null && mine !== PENDING && mine === token, pending: mine === PENDING };
}

const CHART_WINDOW = '[data-testid="chart-window"]';

function e2e(): boolean {
  const b = bridge();
  if (b?.flags) return b.flags().e2e;
  return typeof location !== "undefined" && new URLSearchParams(location.search).get("e2e") === "1";
}

/** The samples of the hole selected, for the marks of the charts: only while a chart window is open. */
function syncHoleSamples(): void {
  const t = tablesStore.get();
  const charts = windowStore.get().windows.some((w) => w.kind.startsWith(CHART_WINDOW_PREFIX));
  const tab = t.tabs.find((x) => x.table === t.table);
  const table = tab && (tab.kind === "assay" || tab.kind === "interval") ? t.table : null;
  void setHoleSamples(charts && t.hole && t.setId !== null ? { setId: t.setId, table, hole: t.hole.id } : null);
}

/**
 * `chart.select_clear`, `Esc` in the window of a chart (the last pressed in, or holding the focus), the table
 * following the selection, the marks of the selected hole, the 3D layer of every scene, an edit of the data, another
 * project; the notices go to the status bar.
 */
export function bindSampleSelection(app: App): void {
  setSelectionNotice((text) => app.store.set({ statusNote: text }));
  app.registry.bind(
    "chart.select_clear",
    async () => (clearSelection() ? "Выделение проб снято" : "Выделения проб нет"),
    (ctx) => needProject(ctx) ?? (sampleSelectionStore.get().current ? null : "Нет выделения проб"),
  );

  if (typeof window !== "undefined") {
    let inChart = false;
    window.addEventListener("pointerdown", (e) => void (inChart = !!(e.target as Element | null)?.closest?.(CHART_WINDOW)), true);
    window.addEventListener(
      "keydown",
      (e) => {
        if (e.key !== "Escape" || e.repeat || !sampleSelectionStore.get().current) return;
        const focus = typeof document !== "undefined" ? (document.activeElement as Element | null) : null;
        if (!inChart && !focus?.closest?.(CHART_WINDOW)) return;
        // Esc of a chart window clears the selection of samples, not the hole (the ribbon's «Снять выбор»)
        e.stopImmediatePropagation();
        e.preventDefault();
        void app.registry.execute("chart.select_clear", {}, "user");
      },
      true,
    );
  }

  // «Показать» of the table: off once the selection goes or another replaces it
  sampleSelectionStore.subscribe(() => {
    const shown = tablesStore.get().selection;
    if (shown && shown !== sampleSelectionStore.get().current?.token) void showSelection(null);
  });
  tablesStore.subscribe(syncHoleSamples);
  windowStore.subscribe(syncHoleSamples);

  let attached: SelectionSceneHandle | null = null;
  onCoreEvent((event, data) => {
    if (event !== "drillholes.changed") return;
    let setId: number | undefined;
    try {
      const d = JSON.parse(data) as { set_id?: unknown };
      if (typeof d.set_id === "number") setId = d.set_id;
    } catch {
      /* a malformed event: the selection is asked again whatever its set */
    }
    void revalidate(setId);
    attached?.refresh(setId);
    refreshHoleSamples();
    syncHoleSamples();
  });

  let uid: string | null = null;
  projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    resetSelection();
  });

  // the segments in the main 3D: a lazy chunk with three.js, attached to every new scene
  let generation = 0;
  onScene((scene) => {
    const mine = ++generation;
    attached?.detach();
    attached = null;
    if (!scene) return;
    void import("./selectionScene").then((m) => {
      if (mine === generation) attached = m.attachSelectionScene(scene);
    });
  });

  if (e2e() && typeof window !== "undefined") {
    window.__bfSelection = {
      /** the selection of samples: its token, n, the segments in 3D (= the samples with a position), rows read */
      state: () => {
        const s = sampleSelectionStore.get();
        return { ...(s.current ?? {}), selected: s.current !== null, rows: s.rows?.length ?? null, segments: attached?.count() ?? null, noPosition: s.noPosition, hole: s.hole ? s.hole.rows.length : null };
      },
    };
  }
}
