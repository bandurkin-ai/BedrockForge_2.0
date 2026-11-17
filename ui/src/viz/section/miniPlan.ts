/**
 * The mini-plan beside a section (ТЗ 7.14, ADR 0007 §4; BF `SectionMap.tsx` and `planOverview` 2603): the
 * collars and hole traces of the visible sets in plan — at most 20 000 points, more are thinned evenly and
 * marked «упрощено» — the line, its window and the side it is viewed from (an orange triangle). The frame
 * maps project coordinates to the SVG and back (clicks on the plan draw the line too). Pure, float64.
 */
import { sinCosDeg, windowCorners, type SectionLine } from "./math";

export const MINI_PLAN_POINTS = 20_000;
export const PLAN_W = 260;
export const PLAN_H = 220;
const PAD = 14;

export interface PlanOverview {
  /** traces in plan, project coordinates */
  holes: [number, number][][];
  collars: [number, number][];
  /** [x1, y1, x2, y2] — lines of other layers (Э7) */
  segments: [number, number, number, number][];
  /** [xmin, ymin, xmax, ymax] — outlines of objects */
  boxes: [number, number, number, number][];
  /** points were thinned to the limit */
  simplified: boolean;
}

/** The holes of one part: absolute collars and the trace points from the part origin (float32). */
export interface PlanSource {
  origin: readonly [number, number, number];
  /** absolute collar xyz per slot (float64 × 3), NaN — none */
  collars: ArrayLike<number>;
  /** vertices of slot s are offsets[s] … offsets[s + 1]; null — the paths are not loaded yet */
  offsets: ArrayLike<number> | null;
  xyz: ArrayLike<number> | null;
}

export function emptyOverview(): PlanOverview {
  return { holes: [], collars: [], segments: [], boxes: [], simplified: false };
}

/** Collars and traces of the sources, thinned evenly to `limit` points in all. */
export function buildOverview(sources: readonly PlanSource[], limit = MINI_PLAN_POINTS): PlanOverview {
  const out = emptyOverview();
  let collars = 0;
  let points = 0;
  for (const s of sources) {
    collars += Math.floor(s.collars.length / 3);
    if (s.offsets) points += s.offsets[s.offsets.length - 1] ?? 0;
  }
  const total = collars + points;
  const every = Math.max(1, Math.ceil(total / limit));
  out.simplified = every > 1;
  let c = 0;
  for (const s of sources) {
    const n = Math.floor(s.collars.length / 3);
    for (let i = 0; i < n; i++, c++) {
      const x = s.collars[i * 3];
      const y = s.collars[i * 3 + 1];
      if (c % every === 0 && Number.isFinite(x) && Number.isFinite(y)) out.collars.push([x, y]);
    }
    if (!s.offsets || !s.xyz) continue;
    const [ox, oy] = s.origin;
    for (let h = 0; h + 1 < s.offsets.length; h++) {
      const a = s.offsets[h];
      const b = s.offsets[h + 1];
      if (b - a < 2) continue;
      const trace: [number, number][] = [];
      for (let p = a; p < b; p += every) trace.push([s.xyz[p * 3] + ox, s.xyz[p * 3 + 1] + oy]);
      if ((b - 1 - a) % every) trace.push([s.xyz[(b - 1) * 3] + ox, s.xyz[(b - 1) * 3 + 1] + oy]);
      out.holes.push(trace);
    }
  }
  return out;
}

/** Bounds of the overview and the extra points, [minX, minY, maxX, maxY]; null when empty. */
export function overviewBounds(o: PlanOverview, extra: readonly [number, number][]): [number, number, number, number] | null {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  const add = (x: number, y: number) => {
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x);
    maxY = Math.max(maxY, y);
  };
  for (const h of o.holes) for (const [x, y] of h) add(x, y);
  for (const [x, y] of o.collars) add(x, y);
  for (const [x1, y1, x2, y2] of o.segments) {
    add(x1, y1);
    add(x2, y2);
  }
  for (const [x1, y1, x2, y2] of o.boxes) {
    add(x1, y1);
    add(x2, y2);
  }
  for (const [x, y] of extra) add(x, y);
  return Number.isFinite(minX) ? [minX, minY, maxX, maxY] : null;
}

export interface PlanFrame {
  /** SVG units per map unit */
  k: number;
  sx(x: number): number;
  sy(y: number): number;
  /** SVG point → project coordinates */
  toWorld(px: number, py: number): { x: number; y: number };
  /** the window of the section, the line from end to end, the eye triangle (centre, rotation in degrees) */
  corners: [number, number][];
  line: [number, number][];
  eye: { x: number; y: number; rotate: number } | null;
}

/** The plan frame of the overview with the section (BF SectionMap): the data, a limited line always inside. */
export function planFrame(o: PlanOverview, section: SectionLine | null, w = PLAN_W, h = PLAN_H): PlanFrame {
  const data = overviewBounds(o, []);
  const reach = data ? Math.max(data[2] - data[0], data[3] - data[1]) * 0.75 + 50 : 500;
  const corners = section ? windowCorners(section, reach) : [];
  const b = overviewBounds(o, section?.length != null ? corners : section ? [[section.x0, section.y0]] : []) ?? [0, 0, 100, 100];
  const span = Math.max(b[2] - b[0], b[3] - b[1], 1);
  const k = Math.min((w - 2 * PAD) / Math.max(b[2] - b[0], span * 0.05), (h - 2 * PAD) / Math.max(b[3] - b[1], span * 0.05));
  const cx = (b[0] + b[2]) / 2;
  const cy = (b[1] + b[3]) / 2;
  const sx = (x: number) => w / 2 + (x - cx) * k;
  const sy = (y: number) => h / 2 - (y - cy) * k;
  let line: [number, number][] = [];
  let eye: PlanFrame["eye"] = null;
  if (section) {
    const [s, c] = sinCosDeg(section.az);
    const u0 = section.length === null ? -reach : 0;
    const u1 = section.length ?? reach;
    line = [u0, u1].map((u) => [section.x0 + s * u, section.y0 + c * u]);
    const mid = (u0 + u1) / 2;
    // the viewer stands on the «towards» side, to the right of the line
    const off = section.towards + 18 / k;
    eye = { x: section.x0 + s * mid + c * off, y: section.y0 + c * mid - s * off, rotate: section.az - 90 };
  }
  return { k, sx, sy, toWorld: (px, py) => ({ x: cx + (px - w / 2) / k, y: cy - (py - h / 2) / k }), corners, line, eye };
}
