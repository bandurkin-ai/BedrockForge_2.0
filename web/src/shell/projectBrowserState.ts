/**
 * View settings of a project kept in this browser (layer visibility, open logs, collar / trace /
 * block styles, order of the tree groups) — taken into a .bfggis file and put back after an import
 * on another computer. Object ids differ there: they are translated with the maps the import returns.
 */
import type { Dataset } from "../api";
import { loadOrder, normalizeOrder, saveOrder } from "./treeOrder";
import { normalize, type WorkspaceState } from "./workspace";

export interface BrowserState {
  version: 1;
  hidden: string[];
  logs: number[];
  collarStyles: Record<string, unknown>;
  traceStyles: Record<string, unknown>;
  blockStyles: Record<string, unknown>;
  treeOrder: string[];
}

/** old id (as text) -> new id, per table of the archive (archive/spec.py RETURNED_MAPS) */
export type IdMaps = Record<string, Record<string, number>>;

/** Prefix of a layer key -> the table its id belongs to (workspace.ts layerKey). */
const KEY_TABLE: Record<string, string> = { set: "dataset", wf: "wireframe", ln: "line_layer", rs: "raster", bm: "dataset" };

function projectIds(datasets: Dataset[]) {
  return {
    dataset: new Set(datasets.map((d) => d.id)),
    wireframe: new Set(datasets.flatMap((d) => (d.wireframes ?? []).map((w) => w.id))),
    line_layer: new Set(datasets.flatMap((d) => (d.lines ?? []).map((l) => l.id))),
    raster: new Set(datasets.flatMap((d) => (d.rasters ?? []).map((r) => r.id))),
    chart: new Set(datasets.flatMap((d) => (d.charts ?? []).map((c) => c.id))),
  } as Record<string, Set<number>>;
}

const pick = (styles: Record<number, unknown>, ids: Set<number>) =>
  Object.fromEntries(Object.entries(styles).filter(([k]) => ids.has(Number(k))));

/** The settings of the project's objects only (the workspace holds every project's). */
export function collectBrowserState(ws: WorkspaceState, datasets: Dataset[]): BrowserState {
  const ids = projectIds(datasets);
  const ownKey = (key: string) => {
    const [prefix, id] = key.split(":");
    const table = KEY_TABLE[prefix];
    return !!table && ids[table].has(Number(id));
  };
  return {
    version: 1,
    hidden: ws.hidden.filter(ownKey),
    logs: ws.logs.filter((id) => ids.chart.has(id)),
    collarStyles: pick(ws.collarStyles, ids.dataset),
    traceStyles: pick(ws.traceStyles, ids.dataset),
    blockStyles: pick(ws.blockStyles, ids.dataset),
    treeOrder: loadOrder(),
  };
}

function remapStyles(raw: unknown, map: Record<string, number> | undefined): Record<string, unknown> {
  if (!raw || typeof raw !== "object" || !map) return {};
  return Object.fromEntries(Object.entries(raw as Record<string, unknown>).flatMap(([k, v]) => (map[k] !== undefined ? [[String(map[k]), v]] : [])));
}

/** The workspace with the imported settings added; ids without a new counterpart are dropped. */
export function mergeBrowserState(ws: WorkspaceState, raw: unknown, maps: IdMaps): WorkspaceState {
  if (!raw || typeof raw !== "object") return ws;
  const b = raw as Partial<BrowserState>;
  const hidden = (Array.isArray(b.hidden) ? b.hidden : []).flatMap((key) => {
    if (typeof key !== "string") return [];
    const [prefix, id, ...rest] = key.split(":");
    const next = maps[KEY_TABLE[prefix]]?.[id];
    return next === undefined ? [] : [[prefix, String(next), ...rest].join(":")];
  });
  const logs = (Array.isArray(b.logs) ? b.logs : []).flatMap((id) => (maps.chart?.[String(id)] !== undefined ? [maps.chart[String(id)]] : []));
  // normalize() checks the styles as it does for the workspace read from storage.
  return normalize({
    ...ws,
    hidden: [...new Set([...ws.hidden, ...hidden])],
    logs: [...new Set([...ws.logs, ...logs])],
    collarStyles: { ...ws.collarStyles, ...remapStyles(b.collarStyles, maps.dataset) },
    traceStyles: { ...ws.traceStyles, ...remapStyles(b.traceStyles, maps.dataset) },
    blockStyles: { ...ws.blockStyles, ...remapStyles(b.blockStyles, maps.dataset) },
  });
}

/** The order of the tree groups is a preference of the user, not of a project: applied as is. */
export function applyTreeOrder(raw: unknown): void {
  const b = raw as Partial<BrowserState> | null;
  if (b && Array.isArray(b.treeOrder)) saveOrder(normalizeOrder(b.treeOrder));
}
