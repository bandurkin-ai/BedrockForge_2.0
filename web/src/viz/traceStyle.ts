/** How the hole traces of a set look in 3D: the line, depth ticks, and marks where they cross objects. */
export interface TraceStyle {
  /** line width, px */
  width: number;
  color: string;
  /** depth ticks across the trace */
  ticks: boolean;
  /** m between ticks */
  tickStep: number;
  /** tick length to each side, px */
  tickSize: number;
  /** depth written at every n-th tick */
  tickLabels: boolean;
  /** m between written depths (a multiple of the tick step reads best) */
  labelStep: number;
  labelSize: number;
  labelColor: string;
  /** a tick at the end of each hole with its depth, m */
  endDepth: boolean;
  /** marks where the holes cross wireframes and enter / leave block models */
  intersections: boolean;
  withWireframes: boolean;
  withBlocks: boolean;
  /** only objects shown in the view */
  onlyVisible: boolean;
  markerSize: number;
  /** wireframe crossings; "object": the colour of the wireframe */
  wireframeColor: string;
  blockColor: string;
  /** depth and object name next to a mark */
  markerLabels: boolean;
}

export const DEFAULT_TRACE_STYLE: TraceStyle = {
  width: 1.5, color: "#8a93a6",
  ticks: false, tickStep: 10, tickSize: 4, tickLabels: true, labelStep: 50, labelSize: 11, labelColor: "#c9d1de", endDepth: false,
  intersections: false, withWireframes: true, withBlocks: true, onlyVisible: true, markerSize: 9,
  wireframeColor: "object", blockColor: "#ffb020", markerLabels: true,
};

const HEX = /^#[0-9a-f]{6}$/i;

/** A style from stored state: unknown or broken values fall back to the defaults. */
export function normalizeTraceStyle(raw: unknown): TraceStyle {
  const r = (raw && typeof raw === "object" ? raw : {}) as Partial<Record<keyof TraceStyle, unknown>>;
  const d = DEFAULT_TRACE_STYLE;
  const num = (v: unknown, lo: number, hi: number, fallback: number) => (typeof v === "number" && Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : fallback);
  const bool = (v: unknown, fallback: boolean) => (typeof v === "boolean" ? v : fallback);
  const hex = (v: unknown, fallback: string) => (typeof v === "string" && HEX.test(v) ? v : fallback);
  return {
    width: num(r.width, 0.5, 12, d.width),
    color: hex(r.color, d.color),
    ticks: bool(r.ticks, d.ticks),
    tickStep: num(r.tickStep, 0.5, 1000, d.tickStep),
    tickSize: num(r.tickSize, 1, 30, d.tickSize),
    tickLabels: bool(r.tickLabels, d.tickLabels),
    labelStep: num(r.labelStep, 0.5, 5000, d.labelStep),
    labelSize: num(r.labelSize, 7, 30, d.labelSize),
    labelColor: hex(r.labelColor, d.labelColor),
    endDepth: bool(r.endDepth, d.endDepth),
    intersections: bool(r.intersections, d.intersections),
    withWireframes: bool(r.withWireframes, d.withWireframes),
    withBlocks: bool(r.withBlocks, d.withBlocks),
    onlyVisible: bool(r.onlyVisible, d.onlyVisible),
    markerSize: num(r.markerSize, 3, 40, d.markerSize),
    wireframeColor: r.wireframeColor === "object" ? "object" : hex(r.wireframeColor, d.wireframeColor),
    blockColor: hex(r.blockColor, d.blockColor),
    markerLabels: bool(r.markerLabels, d.markerLabels),
  };
}

/** Depths of ticks along a hole of this length: multiples of the step, the collar left out. */
export function tickDepths(length: number, step: number, from = 0): number[] {
  if (!(step > 0) || !(length > 0)) return [];
  const out: number[] = [];
  const first = Math.max(step, Math.ceil(from / step) * step);
  for (let d = first; d <= length + 1e-9 && out.length < 100_000; d += step) out.push(Math.round(d * 1e6) / 1e6);
  return out;
}

/** Is this tick depth one of the written ones (a multiple of the label step)? */
export function isLabelDepth(depth: number, labelStep: number): boolean {
  if (!(labelStep > 0)) return false;
  const k = depth / labelStep;
  return Math.abs(k - Math.round(k)) < 1e-6;
}

/** "125 м", "12.5 м": a depth written short. */
export function depthText(depth: number): string {
  const r = Math.round(depth * 100) / 100;
  return `${Number.isInteger(r) ? r : r.toFixed(r * 10 === Math.round(r * 10) ? 1 : 2)} м`;
}
