/** 1-2-5 step giving roughly `target` ticks over `span`. */
export function niceStep(span: number, target = 6): number {
  if (!(span > 0)) return 1;
  const raw = span / Math.max(target, 1);
  const pow = 10 ** Math.floor(Math.log10(raw));
  const f = raw / pow;
  return (f < 1.5 ? 1 : f < 3.5 ? 2 : f < 7.5 ? 5 : 10) * pow;
}

export function niceTicks(min: number, max: number, target = 6): number[] {
  if (!(max > min)) return [];
  const step = niceStep(max - min, target);
  const out: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) out.push(Number(v.toPrecision(12)));
  return out;
}

/** A vertical section: a line from (x0, y0) along azimuth `az`, with a corridor of ±halfWidth metres. */
export interface SectionLine {
  x0: number;
  y0: number;
  /** degrees clockwise from north */
  az: number;
  /** null = unlimited */
  length: number | null;
  halfWidth: number;
}

/** u: distance along the section from its origin; d: signed distance to the right of the line. */
export function toSection(x: number, y: number, line: SectionLine): { u: number; d: number } {
  const a = (line.az * Math.PI) / 180;
  const dx = x - line.x0;
  const dy = y - line.y0;
  return { u: dx * Math.sin(a) + dy * Math.cos(a), d: dx * Math.cos(a) - dy * Math.sin(a) };
}

export function azimuthDeg(dx: number, dy: number): number {
  const deg = (Math.atan2(dx, dy) * 180) / Math.PI;
  return (deg + 360) % 360;
}

export function inCorridor(x: number, y: number, line: SectionLine): boolean {
  const { u, d } = toSection(x, y, line);
  return Math.abs(d) <= line.halfWidth && (line.length === null || (u >= -1 && u <= line.length + 1));
}

/**
 * A sensible first section: through the centre of the collars, across their main
 * direction (perpendicular to the strike), so most holes are seen from the side.
 */
export function defaultSection(points: [number, number][], halfWidth = 25): SectionLine {
  if (points.length === 0) return { x0: 0, y0: 0, az: 90, length: null, halfWidth };
  const n = points.length;
  const mx = points.reduce((s, p) => s + p[0], 0) / n;
  const my = points.reduce((s, p) => s + p[1], 0) / n;
  if (n < 2) return { x0: mx, y0: my, az: 90, length: null, halfWidth };
  let sxx = 0;
  let syy = 0;
  let sxy = 0;
  for (const [x, y] of points) {
    sxx += (x - mx) ** 2;
    syy += (y - my) ** 2;
    sxy += (x - mx) * (y - my);
  }
  const theta = 0.5 * Math.atan2(2 * sxy, sxx - syy); // main axis, counter-clockwise from east
  const strike = 90 - (theta * 180) / Math.PI; // clockwise from north
  return { x0: mx, y0: my, az: (((strike + 90) % 360) + 360) % 360, length: null, halfWidth };
}
