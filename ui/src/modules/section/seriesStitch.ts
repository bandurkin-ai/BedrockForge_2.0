/**
 * «Каркас по серии» (ТЗ 8.1.4; ADR 0016 §8) without React: the table of the contours of the series by sections and
 * their links (`POST /section-series/{id}/links` — the automatic links of the core: the projections in (u, w)
 * overlap, else the centres are ≤ Δ apart), edited by clicks — a contour is picked, a click on a contour of a
 * neighbouring section links or unlinks it with the picked one (the picked stays: 1 → N), the bodies and the
 * groups N : M («разделите связи») follow the edited links; the links as lines between the centres of the
 * contours in the 3D window (the scene layer `series-links`); the parameters of the operation `stitch_series` —
 * the edited links as pairs of lines (`layer_id`, `lid`), none — the automatic ones of the core.
 */
import { api } from "../../shell/api";
import type { App } from "../../shell/app";
import type { CommandContext } from "../../shell/commands";
import { needWritable, noted, refusal, viewport } from "../../shell/objectRun";
import { openWindow } from "../../shell/windows";
import type { ShadowLayer, ShadowLines } from "../../viz/section/shadow";
import { currentScene } from "../scene/sceneHost";
import { NO_SERIES } from "./digitize";
import { currentSection } from "./seriesState";

export const SERIES_STITCH = "series-stitch";
export const LINKS_KEY = "series-links";

export type LinkHow = "overlap" | "centres" | "manual";

export interface LinkContour {
  /** the number of the contour in the table */
  i: number;
  /** the section from 0; −1 — outside the windows */
  k: number;
  name: string | null;
  layer_id: number;
  lid: number;
  vertices: number;
  uw: [number, number] | null;
  area: number | null;
  centre: [number, number, number] | null;
  off_plane: number | null;
}

/** `POST /section-series/{id}/links`. */
export interface LinkTable {
  series_id: number;
  version: number;
  count: number;
  step: number;
  names: string[];
  layers: { id: number; name: string; rev: number }[];
  contours: LinkContour[];
  links: [number, number, "overlap" | "centres"][];
  bodies: number[][];
  conflicts: { sections: [number, number]; contours: number[] }[];
  skipped: { open: number; short: number; outside: number; no_height: number };
  off_plane_limit: number;
  warnings: string[];
}

export const loadLinks = (seriesId: number, layers?: readonly number[]): Promise<LinkTable> => api.post<LinkTable>(`/section-series/${seriesId}/links`, layers?.length ? { layers } : {});

/** The caps at the ends of every body (ADR 0016 §8). */
export type CapMode = "extend" | "flat" | "point" | "line";
export const CAP_TITLES: Record<CapMode, string> = {
  extend: "Продолжение на Δ/2, плоская крышка",
  flat: "Плоская крышка на крайнем разрезе",
  point: "Выклинивание в точку на Δ/2",
  line: "Выклинивание в линию (гребень) на Δ/2",
};

export interface StitchState {
  table: LinkTable;
  /** [lower, upper]: a contour of section k and one of k + 1, sorted */
  pairs: [number, number][];
  how: Record<string, LinkHow>;
  /** the links are not the automatic ones any more */
  edited: boolean;
  picked: number | null;
  start: CapMode;
  end: CapMode;
  single: boolean;
  message: string | null;
}

export const pairKey = (a: number, b: number): string => `${a}:${b}`;

const linkable = (c: LinkContour | undefined): c is LinkContour => !!c && c.k >= 0 && c.uw !== null;

export function initialState(table: LinkTable): StitchState {
  const pairs: [number, number][] = [];
  const how: Record<string, LinkHow> = {};
  for (const [a, b, h] of table.links) {
    const [lo, hi] = table.contours[a].k <= table.contours[b].k ? [a, b] : [b, a];
    pairs.push([lo, hi]);
    how[pairKey(lo, hi)] = h;
  }
  pairs.sort((x, y) => x[0] - y[0] || x[1] - y[1]);
  return { table, pairs, how, edited: false, picked: null, start: "extend", end: "extend", single: false, message: null };
}

/** «линия 12 («Контуры»), 5050N» — a contour for people (a layer named when there are several). */
export function contourTitle(table: LinkTable, i: number): string {
  const c = table.contours[i];
  const layer = table.layers.length > 1 ? ` («${table.layers.find((l) => l.id === c.layer_id)?.name ?? c.layer_id}»)` : "";
  return `линия ${c.lid + 1}${layer}${c.name ? `, ${c.name}` : ""}`;
}

/** A click on a contour: pick it, drop the pick, or link / unlink it with the picked one of a neighbouring section. */
export function clickContour(st: StitchState, i: number): StitchState {
  const t = st.table;
  const c = t.contours[i];
  if (!linkable(c)) return { ...st, message: `Контур ${c ? contourTitle(t, i) : i} вне окон разрезов или без высот: в каркас не входит` };
  if (st.picked === null || st.picked === i) {
    const picked = st.picked === i ? null : i;
    return { ...st, picked, message: picked === null ? null : `Выбран контур ${contourTitle(t, i)}: щёлкните контур соседнего разреза — связать или развязать` };
  }
  const p = t.contours[st.picked];
  if (Math.abs(p.k - c.k) !== 1) return { ...st, picked: i, message: `Связываются только контуры соседних разрезов. Выбран контур ${contourTitle(t, i)}` };
  const [lo, hi] = p.k < c.k ? [st.picked, i] : [i, st.picked];
  const key = pairKey(lo, hi);
  const has = st.pairs.some(([a, b]) => a === lo && b === hi);
  const pairs = has ? st.pairs.filter(([a, b]) => !(a === lo && b === hi)) : [...st.pairs, [lo, hi] as [number, number]].sort((x, y) => x[0] - y[0] || x[1] - y[1]);
  const how = { ...st.how };
  if (has) delete how[key];
  else how[key] = "manual";
  const text = `${has ? "Развязаны" : "Связаны"}: ${contourTitle(t, lo)} — ${contourTitle(t, hi)}`;
  return { ...st, pairs, how, edited: true, message: text };
}

/** The automatic links of the core again. */
export const resetLinks = (st: StitchState): StitchState => ({ ...initialState(st.table), start: st.start, end: st.end, single: st.single, message: "Связи — автоматические" });

/** Connected groups of the linkable contours by the links (every one a body), in the order of their first contour. */
export function bodiesOf(table: LinkTable, pairs: readonly [number, number][]): number[][] {
  const parent = table.contours.map((_, i) => i);
  const find = (x: number): number => (parent[x] === x ? x : (parent[x] = find(parent[x])));
  for (const [a, b] of pairs) parent[find(a)] = find(b);
  const groups = new Map<number, number[]>();
  for (const c of table.contours) {
    if (!linkable(c)) continue;
    const r = find(c.i);
    groups.set(r, [...(groups.get(r) ?? []), c.i]);
  }
  return [...groups.values()].sort((x, y) => x[0] - y[0]);
}

/** Groups N : M (N, M ≥ 2) of the links on one pair of sections (the core refuses them: «разделите связи»). */
export function conflictsOf(table: LinkTable, pairs: readonly [number, number][]): { sections: [number, number]; contours: number[] }[] {
  const byK = new Map<number, [number, number][]>();
  for (const p of pairs) {
    const k = table.contours[p[0]].k;
    byK.set(k, [...(byK.get(k) ?? []), p]);
  }
  const out: { sections: [number, number]; contours: number[] }[] = [];
  for (const [k, mine] of [...byK.entries()].sort((x, y) => x[0] - y[0])) {
    // the groups of the contours these links touch — not of every contour of the series (100 × 500 per click)
    for (const members of linkedGroups(table, mine)) {
      const low = members.filter((i) => table.contours[i].k === k).length;
      if (low > 1 && members.length - low > 1) out.push({ sections: [k, k + 1], contours: members });
    }
  }
  return out;
}

/** Connected groups of the linkable contours of `pairs` only, each sorted, in the order of their first contour. */
function linkedGroups(table: LinkTable, pairs: readonly [number, number][]): number[][] {
  const parent = new Map<number, number>();
  const find = (x: number): number => {
    let r = x;
    while (parent.get(r) !== r) r = parent.get(r)!;
    for (let y = x; y !== r; ) {
      const next = parent.get(y)!;
      parent.set(y, r);
      y = next;
    }
    return r;
  };
  for (const [a, b] of pairs) {
    if (!parent.has(a)) parent.set(a, a);
    if (!parent.has(b)) parent.set(b, b);
    parent.set(find(a), find(b));
  }
  const groups = new Map<number, number[]>();
  for (const i of parent.keys()) {
    if (!linkable(table.contours[i])) continue;
    const r = find(i);
    const g = groups.get(r);
    if (g) g.push(i);
    else groups.set(r, [i]);
  }
  return [...groups.values()].map((g) => g.sort((x, y) => x - y)).sort((x, y) => x[0] - y[0]);
}

/** «между 5050N и 5075N» */
export const sectionsText = (table: LinkTable, s: [number, number]): string => `между ${table.names[s[0]] ?? s[0] + 1} и ${table.names[s[1]] ?? s[1] + 1}`;

/** The parameters of `stitch_series`: the edited links as pairs of lines (`layer_id`, `lid`), else the automatic ones. */
export function stitchParams(st: StitchState): Record<string, unknown> {
  const t = st.table;
  const end = (i: number) => ({ layer_id: t.contours[i].layer_id, line: t.contours[i].lid });
  const params: Record<string, unknown> = { series_id: t.series_id, layer_ids: t.layers.map((l) => l.id), start_cap: st.start, end_cap: st.end, single: st.single };
  if (st.edited) params.links = st.pairs.map(([a, b]) => [end(a), end(b)]);
  return params;
}

/** The links as segments between the centres of the contours (project coordinates). */
export function linkLines(table: LinkTable, pairs: readonly [number, number][]): ShadowLines | null {
  const xyz: number[] = [];
  const offsets = [0];
  for (const [a, b] of pairs) {
    const ca = table.contours[a].centre;
    const cb = table.contours[b].centre;
    if (!ca || !cb) continue;
    xyz.push(...ca, ...cb);
    offsets.push(offsets[offsets.length - 1] + 2);
  }
  return offsets.length > 1 ? { xyz: Float64Array.from(xyz), offsets } : null;
}

// ---------------------------------------------------------------- the links in the 3D window

let links: { layer: ShadowLayer; scene: unknown } | null = null;
/** every call takes a number: a layer made for an older call (or before «none») is not added */
let linksCall = 0;

function accent(): string {
  const css = typeof document === "undefined" ? null : getComputedStyle(document.documentElement);
  return css?.getPropertyValue("--highlight").trim() || "#f46a1a";
}

/** Shows the links between the centres in the 3D window (null — none): dashed, not clipped, not picked. */
export async function showLinks(lines: ShadowLines | null): Promise<void> {
  const call = ++linksCall;
  const scene = currentScene();
  if (links && (links.scene !== scene || !lines)) {
    (links.scene as { removeLayer(k: string): void } | null)?.removeLayer(LINKS_KEY);
    links.layer.dispose();
    links = null;
  }
  if (!scene || !lines) return;
  if (!links) {
    const layer = new (await import("../../viz/section/shadow")).ShadowLayer(accent());
    if (call !== linksCall || links || currentScene() !== scene) return layer.dispose();
    layer.show(lines);
    links = { layer, scene };
    scene.addLayer(LINKS_KEY, layer);
    return;
  }
  if (links.layer.show(lines)) scene.layerChanged();
  scene.invalidate();
}

// ---------------------------------------------------------------- series.stitch

/** `series.stitch`: the window «Каркас по серии» for the active series. */
export function openStitch(): string {
  const cur = currentSection();
  if (!cur) throw refusal("no_series", "Нет активной серии разрезов.", "Сделайте серию активной («Разрезы › Менеджер серий») и повторите.");
  openWindow(SERIES_STITCH, `Каркас по серии «${cur.row.name}»`, "normal", { seriesId: cur.row.id }, ...viewport());
  return `Открыто окно «Каркас по серии» для серии «${cur.row.name}»`;
}

export function bindStitch(app: App): void {
  const writable = needWritable(app);
  app.registry.bind(
    "series.stitch",
    noted(app, () => openStitch()),
    (ctx: CommandContext) => writable(ctx) ?? (currentSection() ? null : NO_SERIES),
  );
}
