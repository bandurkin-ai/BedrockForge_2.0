/**
 * The geometry of the planned holes for the 3D window (ТЗ 7.35 Д-34 ч. 2; ADR 0026 решение 29; ТЗ 5.4: float64 in the core, float32 only for the
 * picture, from an integer origin): the paths of all holes become pairs of points (the segments of one `LineSegments2`), the chosen hole goes to its own
 * buffer (drawn thicker), the targets and the collars become points. The origin is the integer centre of the box of everything drawn (`viz/origin.ts`),
 * so the numbers of the buffers are small at any coordinates of the project. A gap in a path (a non-finite coordinate) breaks the line — a segment
 * with it is left out, never drawn to 0 (8.0.1). Pure: no three.js (tested in node).
 */
import { integerOrigin, type Origin } from "../origin";

export interface PlannedDraw {
  id: number;
  /** the path, absolute float64 */
  x: ArrayLike<number>;
  y: ArrayLike<number>;
  z: ArrayLike<number>;
  /** the target the hole goes through; null — none */
  target: readonly [number, number, number] | null;
}

export interface PlannedBuild {
  /** absolute integer origin of every buffer */
  origin: Origin;
  /** the segments of the holes that are not chosen: x0 y0 z0 x1 y1 z1 … */
  lines: Float32Array;
  /** the segments of the chosen hole */
  chosen: Float32Array;
  /** the targets: x y z … */
  targets: Float32Array;
  /** the collars: x y z … */
  collars: Float32Array;
  /** absolute [xmin, ymin, zmin, xmax, ymax, zmax] of what is drawn; null — nothing */
  bbox: number[] | null;
  holes: number;
  /** the segments drawn (both buffers) */
  segments: number;
  /** the segments left out for a gap */
  skipped: number;
}

const finite = (a: number, b: number, c: number): boolean => Number.isFinite(a) && Number.isFinite(b) && Number.isFinite(c);

export function buildPlanned(draws: readonly PlannedDraw[], chosen: number | null = null): PlannedBuild | null {
  let n = 0;
  for (const d of draws) n += Math.min(d.x.length, d.y.length, d.z.length) + (d.target ? 1 : 0);
  if (n === 0) return null;
  // the box: one pass over what will be drawn (the origin needs it before the buffers are filled)
  const lo = [Infinity, Infinity, Infinity];
  const hi = [-Infinity, -Infinity, -Infinity];
  const grow = (a: number, b: number, c: number) => {
    if (a < lo[0]) lo[0] = a;
    if (a > hi[0]) hi[0] = a;
    if (b < lo[1]) lo[1] = b;
    if (b > hi[1]) hi[1] = b;
    if (c < lo[2]) lo[2] = c;
    if (c > hi[2]) hi[2] = c;
  };
  for (const d of draws) {
    const m = Math.min(d.x.length, d.y.length, d.z.length);
    for (let i = 0; i < m; i++) if (finite(d.x[i], d.y[i], d.z[i])) grow(d.x[i], d.y[i], d.z[i]);
    if (d.target && finite(d.target[0], d.target[1], d.target[2])) grow(d.target[0], d.target[1], d.target[2]);
  }
  if (!Number.isFinite(lo[0])) return null;
  const origin = integerOrigin(Float64Array.of(lo[0], lo[1], lo[2], hi[0], hi[1], hi[2]));

  const segs = (d: PlannedDraw) => Math.max(0, Math.min(d.x.length, d.y.length, d.z.length) - 1);
  let others = 0;
  let mine = 0;
  for (const d of draws) (d.id === chosen ? (mine += segs(d)) : (others += segs(d)));
  const lines = new Float32Array(others * 6);
  const picked = new Float32Array(mine * 6);
  const targets: number[] = [];
  const collars: number[] = [];
  let at = 0;
  let atChosen = 0;
  let skipped = 0;
  for (const d of draws) {
    const out = d.id === chosen ? picked : lines;
    let k = d.id === chosen ? atChosen : at;
    const m = Math.min(d.x.length, d.y.length, d.z.length);
    for (let i = 0; i + 1 < m; i++) {
      const [ax, ay, az, bx, by, bz] = [d.x[i], d.y[i], d.z[i], d.x[i + 1], d.y[i + 1], d.z[i + 1]];
      if (!finite(ax, ay, az) || !finite(bx, by, bz)) {
        skipped++;
        continue;
      }
      out[k++] = ax - origin.x;
      out[k++] = ay - origin.y;
      out[k++] = az - origin.z;
      out[k++] = bx - origin.x;
      out[k++] = by - origin.y;
      out[k++] = bz - origin.z;
    }
    if (d.id === chosen) atChosen = k;
    else at = k;
    if (m >= 1 && finite(d.x[0], d.y[0], d.z[0])) collars.push(d.x[0] - origin.x, d.y[0] - origin.y, d.z[0] - origin.z);
    if (d.target && finite(d.target[0], d.target[1], d.target[2])) targets.push(d.target[0] - origin.x, d.target[1] - origin.y, d.target[2] - origin.z);
  }
  return {
    origin,
    lines: at === lines.length ? lines : lines.slice(0, at),
    chosen: atChosen === picked.length ? picked : picked.slice(0, atChosen),
    targets: Float32Array.from(targets),
    collars: Float32Array.from(collars),
    bbox: [lo[0], lo[1], lo[2], hi[0], hi[1], hi[2]],
    holes: draws.length,
    segments: (at + atChosen) / 6,
    skipped,
  };
}
