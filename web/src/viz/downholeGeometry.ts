import type { DownholeData, DownholeInterval, TrajectoryHole } from "../api";
import { along, categoryColors, classColor, classLabel, fieldRange, type MarkerShape, type StripLogSettings, type Track } from "../charts/stripLog";
import { formatNumber, parseNumber } from "../stats/chartMath";
import { ColorScale } from "./colorScale";

/**
 * Geometry of the downhole data in the 3D view (pure numbers, no three.js, so it is testable).
 *
 * Positions are relative to the scene origin. Panels (bars, lines, colour ribbons) hang beside the hole:
 * a vertex is a point of the hole plus `amp` metres in the *side* direction, which the shader finds from
 * the camera — across the hole, in the plane of the screen — so a panel always faces the viewer, in 3D
 * and in sections alike.
 */

export interface TubeLayer {
  trackId: string;
  /** px */
  width: number;
  /** pairs of points, x y z each, and one colour per point */
  positions: number[];
  colors: number[];
}

/** Triangles and segments with a side offset: position, hole direction, metres to the side, colour. */
export interface SideMesh {
  positions: number[];
  tangents: number[];
  amps: number[];
  colors: number[];
  /** triangles (index into the vertices); empty for segments */
  index: number[];
}

export interface Label {
  /** point on the hole, relative to the origin */
  position: [number, number, number];
  tangent: [number, number, number];
  /** to the side, m: where the text starts */
  amp: number;
  text: string;
  /** length of the interval along the hole, m */
  length: number;
  trackId: string;
  /** shift to the right of the point on screen, px (labels of event markers) */
  px?: number;
}

/** Markers of events on the holes: one point each. */
export interface MarkerLayer {
  trackId: string;
  shape: MarkerShape;
  /** px */
  size: number;
  positions: number[];
  colors: number[];
}

/** Where a display starts when others of the same set are shown before it: columns already taken, tubes already drawn. */
export interface StackStart {
  units: number;
  tubes: number;
}

export interface LegendEntry {
  trackId: string;
  title: string;
  /** colours of categories, or the range of a colour scale / bars */
  items: [string, string][];
  range: [number, number] | null;
  unit: string;
}

export interface DownholeBuild {
  tubes: TubeLayer[];
  panels: SideMesh;
  /** frames of panels and graph lines (segments) */
  lines: SideMesh;
  labels: Label[];
  markers: MarkerLayer[];
  legend: LegendEntry[];
  /** width of the default panel column, m */
  panelWidth: number;
  holes: number;
  intervals: number;
}

export const MAX_LABELS = 2500;
/** The label of an event shows at the zoom where an interval of this length would get its label, m. */
const EVENT_LABEL_M = 4;
export const holeKey = (id: string) => String(id).trim().toUpperCase();
const FRAME_COLOR = "#8a93a6";

const emptyMesh = (): SideMesh => ({ positions: [], tangents: [], amps: [], colors: [], index: [] });

export function hexRgb(hex: string): [number, number, number] {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  const v = m ? parseInt(m[1], 16) : 0x9aa3b5;
  return [((v >> 16) & 255) / 255, ((v >> 8) & 255) / 255, (v & 255) / 255];
}

/**
 * The point of a hole path (x, y, z, measured depth) at a depth. Above the collar: the collar.
 * Below the last point the hole goes on straight (a hole without a depth in the collar table still
 * has intervals); a path of one point goes straight down.
 */
export function pointAt(path: number[][], depth: number): [number, number, number] {
  if (path.length === 0) return [0, 0, 0];
  if (depth <= path[0][3]) return [path[0][0], path[0][1], path[0][2]];
  const last = path[path.length - 1];
  if (depth >= last[3]) {
    const over = depth - last[3];
    if (over <= 0) return [last[0], last[1], last[2]];
    const prev = path.length > 1 ? path[path.length - 2] : null;
    const d = prev ? [last[0] - prev[0], last[1] - prev[1], last[2] - prev[2]] : [0, 0, -1];
    const l = Math.hypot(d[0], d[1], d[2]);
    const u = l > 1e-9 ? [d[0] / l, d[1] / l, d[2] / l] : [0, 0, -1];
    return [last[0] + u[0] * over, last[1] + u[1] * over, last[2] + u[2] * over];
  }
  let lo = 0;
  let hi = path.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (path[mid][3] <= depth) lo = mid;
    else hi = mid;
  }
  const a = path[lo];
  const b = path[hi];
  const t = b[3] > a[3] ? (depth - a[3]) / (b[3] - a[3]) : 0;
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

/** Points of the hole from one depth to another: both ends and every survey station between. */
export function pathBetween(path: number[][], from: number, to: number): [number, number, number][] {
  const out: [number, number, number][] = [pointAt(path, from)];
  for (const p of path) if (p[3] > from && p[3] < to) out.push([p[0], p[1], p[2]]);
  out.push(pointAt(path, to));
  return out;
}

function unit(a: [number, number, number], b: [number, number, number]): [number, number, number] {
  const d: [number, number, number] = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
  const l = Math.hypot(...d);
  return l > 1e-9 ? [d[0] / l, d[1] / l, d[2] / l] : [0, 0, -1];
}

const valueText = (v: unknown) => {
  const t = typeof v === "number" ? formatNumber(v, 3) : v === null || v === undefined ? "" : String(v);
  return t.length > 18 ? `${t.slice(0, 17)}…` : t;
};

interface Resolved {
  track: Track;
  numeric: boolean;
  range: [number, number] | null;
  colors: Map<string, string> | null;
  scale: ColorScale | null;
  unit: string;
}

function resolve(data: DownholeData, t: Track): Resolved {
  const table = data.tables.find((x) => x.key === t.table);
  const column = table?.columns.find((c) => c.name === t.field);
  const numeric = column?.kind === "number";
  const auto = numeric ? fieldRange(data, t.table, t.field, t.log) : null;
  const lo = parseNumber(t.min) ?? auto?.[0] ?? null;
  const hi = parseNumber(t.max) ?? auto?.[1] ?? null;
  const range: [number, number] | null = lo !== null && hi !== null && hi > lo ? [lo, hi] : auto;
  const wantsColor = t.type === "color" || t.type === "color_text";
  return {
    track: t, numeric, range, unit: column?.unit ?? "",
    colors: !numeric && (wantsColor || t.type === "event") ? withCatColors(categoryColors(data, t.table, t.field), t.catColors) : null,
    scale: numeric && range && (t.colorBy === "scale" || (wantsColor && t.colorBy !== "classes" && t.colorBy !== "single"))
      ? new ColorScale(range[0], range[1], t.log) : null,
  };
}

function withCatColors(auto: Map<string, string>, own: Record<string, string> | undefined): Map<string, string> {
  if (own) for (const [k, c] of Object.entries(own)) if (auto.has(k)) auto.set(k, c);
  return auto;
}

/** Columns (in units of the default column) and tubes a display takes: the next display of the set starts after them. */
export function stackOf(settings: StripLogSettings): StackStart {
  const tubes = tubeTracks(settings.tracks);
  let units = 0;
  for (const t of settings.tracks) {
    if (tubes.has(t.id) || t.type === "event") continue;
    units += Math.max(0.3, t.width / 90) + 0.08;
  }
  return { units, tubes: tubes.size };
}

/** Colour of an interval's value: by classes, category or scale; null when it has none. */
function colorOf(r: Resolved, v: unknown): [number, number, number] | null {
  if (r.track.colorBy === "classes" && r.numeric) {
    const c = classColor(r.track.classes, v);
    return c ? hexRgb(c) : null;
  }
  if (r.colors && v !== null && v !== undefined && v !== "") {
    const c = r.colors.get(String(v));
    return c ? hexRgb(c) : null;
  }
  if (r.scale && typeof v === "number" && Number.isFinite(v)) {
    const c = r.scale.rgb(v);
    return [c[0] / 255, c[1] / 255, c[2] / 255];
  }
  return null;
}

/** Is this column a tube along the hole (not a panel beside it)? The first colour column is, unless it says otherwise. */
export function tubeTracks(tracks: Track[]): Set<string> {
  const out = new Set<string>();
  let first = true;
  for (const t of tracks) {
    if (t.type !== "color" && t.type !== "color_text") continue;
    const tube = t.form ? t.form === "tube" : first;
    first = false;
    if (tube) out.add(t.id);
  }
  return out;
}

/** Width of the default panel column: about a twentieth of a typical hole, between 2 and 40 m. */
export function autoPanelWidth(holes: TrajectoryHole[]): number {
  const lengths = holes.map((h) => (h.path.length ? h.path[h.path.length - 1][3] - h.path[0][3] : 0)).filter((l) => l > 0).sort((a, b) => a - b);
  const median = lengths.length ? lengths[lengths.length >> 1] : 100;
  return Math.min(40, Math.max(2, Math.round(median / 20)));
}

/** The depths an interval covers inside the depth window of the settings; null when outside it. */
function clip(iv: DownholeInterval, from: number | null, to: number | null, event = false): [number, number] | null {
  if (event) {
    const d = iv.from ?? iv.to;
    return d === null || d < (from ?? -Infinity) || d > (to ?? Infinity) ? null : [d, d];
  }
  if (iv.from === null || iv.to === null || !(iv.to > iv.from)) return null;
  const a = Math.max(iv.from, from ?? -Infinity);
  const b = Math.min(iv.to, to ?? Infinity);
  return b > a ? [a, b] : null;
}

export function buildDownhole(
  trajectories: TrajectoryHole[],
  data: DownholeData,
  settings: StripLogSettings,
  origin: [number, number, number],
  start: StackStart = { units: 0, tubes: 0 },
): DownholeBuild {
  // Hole names are matched without regard to case and outer spaces (tables often differ in that).
  const holes = new Map(trajectories.map((h) => [holeKey(h.hole_id), h]));
  const resolved = settings.tracks.map((t) => resolve(data, t));
  const tubeIds = tubeTracks(settings.tracks);
  const panelWidth = settings.panel && settings.panel > 0 ? settings.panel : autoPanelWidth(trajectories);
  const from = parseNumber(settings.from);
  const to = parseNumber(settings.to);
  const tubeWidth = Math.max(1, settings.tube ?? 8);

  const build: DownholeBuild = {
    tubes: [], panels: emptyMesh(), lines: emptyMesh(), labels: [], markers: [], legend: [], panelWidth, holes: 0, intervals: 0,
  };

  // Panel columns, side by side from the hole outwards: offset and width in metres.
  const columns = new Map<string, { offset: number; width: number }>();
  let offset = panelWidth * (0.15 + start.units);
  for (const r of resolved) {
    if (r.track.type === "event" || (tubeIds.has(r.track.id) && r.track.type === "color")) continue; // a tube without text takes no room
    const width = panelWidth * Math.max(0.3, r.track.width / 90);
    if (!tubeIds.has(r.track.id)) {
      columns.set(r.track.id, { offset, width });
      offset += width + panelWidth * 0.08;
    } else {
      columns.set(r.track.id, { offset: panelWidth * 0.15, width }); // the text of a tube column: beside the tube
    }
  }

  const rel = (p: [number, number, number]): [number, number, number] => [p[0] - origin[0], p[1] - origin[1], p[2] - origin[2]];
  let tubeIndex = start.tubes;
  const markerOf = new Map<string, MarkerLayer>();
  const tubeOf = new Map<string, TubeLayer>();
  for (const r of resolved) {
    if (tubeIds.has(r.track.id)) {
      const layer: TubeLayer = { trackId: r.track.id, width: Math.max(2, tubeWidth - 2.5 * tubeIndex++), positions: [], colors: [] };
      tubeOf.set(r.track.id, layer);
      build.tubes.push(layer);
    }
    if (r.track.type === "event") {
      const layer: MarkerLayer = { trackId: r.track.id, shape: r.track.marker ?? "diamond", size: Math.max(4, r.track.size ?? 12), positions: [], colors: [] };
      markerOf.set(r.track.id, layer);
      build.markers.push(layer);
    }
    if (r.colors || r.numeric || r.track.type === "text") {
      const classes = r.numeric && r.track.colorBy === "classes" ? r.track.classes ?? [] : [];
      build.legend.push({
        trackId: r.track.id,
        title: r.track.field,
        items: r.colors ? [...r.colors].slice(0, 30) : classes.map((c, i) => [classLabel(classes, i), c.color] as [string, string]),
        range: r.numeric ? r.range : null,
        unit: r.unit,
      });
    }
  }

  /** A quad from the hole outwards: along the interval, from `a0` to `a1` metres to the side. */
  const addTriangles = (m: SideMesh, a: [number, number, number], b: [number, number, number], t: [number, number, number],
    a0: number, a1: number, c: [number, number, number]) => {
    const base = m.amps.length;
    const verts: [[number, number, number], number][] = [[a, a0], [b, a0], [b, a1], [a, a1]];
    for (const [p, amp] of verts) {
      m.positions.push(p[0], p[1], p[2]);
      m.tangents.push(t[0], t[1], t[2]);
      m.amps.push(amp);
      m.colors.push(c[0], c[1], c[2]);
    }
    m.index.push(base, base + 1, base + 2, base, base + 2, base + 3);
  };
  const addSegment = (m: SideMesh, a: [number, number, number], b: [number, number, number], t: [number, number, number],
    a0: number, a1: number, c: [number, number, number]) => {
    for (const [p, amp] of [[a, a0], [b, a1]] as [[number, number, number], number][]) {
      m.positions.push(p[0], p[1], p[2]);
      m.tangents.push(t[0], t[1], t[2]);
      m.amps.push(amp);
      m.colors.push(c[0], c[1], c[2]);
    }
  };

  const frame = hexRgb(FRAME_COLOR);
  for (const hole of data.holes) {
    const traj = holes.get(holeKey(hole.hole_id));
    if (!traj || traj.path.length < 1 || traj.path[0].length < 4) continue;
    build.holes += 1;
    let lastBottom = -Infinity;
    for (const r of resolved) {
      const t = r.track;
      const rows = (hole.tables[t.table] ?? []).filter((iv) => iv.from !== null && iv.to !== null);
      const col = columns.get(t.id);
      const tube = tubeOf.get(t.id);
      const wantsColor = t.type === "color" || t.type === "color_text";
      const showText = t.type === "text" || t.type === "color_text";
      const oneColor = hexRgb(t.color);
      const perValue = r.numeric && (t.colorBy === "classes" || t.colorBy === "scale");
      const markers = markerOf.get(t.id);
      let prevEnd: { depth: number; amp: number } | null = null;
      for (const iv of rows) {
        const span = clip(iv, from, to, t.type === "event");
        if (!span) continue;
        build.intervals += 1;
        const value = iv.values[t.field];
        if (markers) {
          const p = rel(pointAt(traj.path, span[0]));
          const c = (r.colors ? colorOf(r, value) : null) ?? oneColor;
          markers.positions.push(p[0], p[1], p[2]);
          markers.colors.push(c[0], c[1], c[2]);
          const text = valueText(value);
          if (settings.labels !== false && text && build.labels.length < MAX_LABELS) {
            build.labels.push({ position: p, tangent: [0, 0, -1], amp: 0, text, length: EVENT_LABEL_M, trackId: t.id, px: markers.size / 2 + 4 });
          }
          continue;
        }
        // bars and lines: one colour, or the colour of the value's class / place on the scale
        const barColor = perValue ? colorOf(r, value) ?? hexRgb(FRAME_COLOR) : oneColor;
        const pa = rel(pointAt(traj.path, span[0]));
        const pb = rel(pointAt(traj.path, span[1]));
        const tan = unit(pa, pb);
        lastBottom = Math.max(lastBottom, span[1]);
        if (wantsColor) {
          const c = colorOf(r, value);
          if (c && tube) {
            const pts = pathBetween(traj.path, span[0], span[1]).map(rel);
            for (let i = 0; i + 1 < pts.length; i++) {
              tube.positions.push(...pts[i], ...pts[i + 1]);
              tube.colors.push(...c, ...c);
            }
          } else if (c && col) {
            addTriangles(build.panels, pa, pb, tan, col.offset, col.offset + col.width, c);
          }
        }
        if (t.type === "bars" && r.numeric && col && typeof value === "number" && Number.isFinite(value) && r.range) {
          const amp = col.offset + along(value, r.range[0], r.range[1], t.log) * col.width;
          addTriangles(build.panels, pa, pb, tan, col.offset, Math.max(amp, col.offset + col.width * 0.01), barColor);
        }
        if (t.type === "line" && r.numeric && col && typeof value === "number" && Number.isFinite(value) && r.range) {
          const amp = col.offset + along(value, r.range[0], r.range[1], t.log) * col.width;
          addSegment(build.lines, pa, pb, tan, amp, amp, barColor);
          if (prevEnd && Math.abs(prevEnd.depth - span[0]) < 1e-6) addSegment(build.lines, pa, pa, tan, prevEnd.amp, amp, barColor);
          prevEnd = { depth: span[1], amp };
        } else if (t.type === "line") {
          prevEnd = null;
        }
        if (t.boundaries && col && !tube) addSegment(build.lines, pa, pa, tan, col.offset, col.offset + col.width, frame);
        if (showText && settings.labels !== false && build.labels.length < MAX_LABELS) {
          const text = valueText(value);
          if (text) {
            const mid = rel(pointAt(traj.path, (span[0] + span[1]) / 2));
            const amp = col ? col.offset : panelWidth * 0.15;
            build.labels.push({ position: mid, tangent: tan, amp, text, length: span[1] - span[0], trackId: t.id });
          }
        }
      }
    }
    // The frame of every panel column along the hole.
    if (lastBottom > -Infinity) {
      const top = from ?? traj.path[0][3];
      const a = rel(pointAt(traj.path, Math.max(top, traj.path[0][3])));
      const b = rel(pointAt(traj.path, lastBottom));
      const tan = unit(a, b);
      for (const r of resolved) {
        const col = columns.get(r.track.id);
        if (!col || tubeIds.has(r.track.id) || r.track.type === "text" || r.track.type === "event") continue;
        addSegment(build.lines, a, b, tan, col.offset, col.offset, frame);
        if (r.track.type === "bars" || r.track.type === "line") addSegment(build.lines, a, b, tan, col.offset + col.width, col.offset + col.width, frame);
      }
    }
  }
  return build;
}
