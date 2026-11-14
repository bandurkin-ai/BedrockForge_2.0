import { niceStep } from "../viz/geometry";

/** Plan extent of the points, ignoring holes without coordinates; null when fewer than two have them. */
export function planSpan(points: { x: number | null; y: number | null }[]): number | null {
  const xs: number[] = [];
  const ys: number[] = [];
  for (const p of points) {
    if (p.x !== null && p.y !== null && Number.isFinite(p.x) && Number.isFinite(p.y)) {
      xs.push(p.x);
      ys.push(p.y);
    }
  }
  if (xs.length < 2) return null;
  const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
  return span > 0 ? span : null;
}

/** A round lag distance giving about 20 lags across the plan extent; 50 when the extent is unknown. */
export function defaultLag(span: number | null): number {
  return span === null ? 50 : niceStep(span / 2, 10);
}
