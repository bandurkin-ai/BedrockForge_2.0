/**
 * The canvas layer of the point clouds (ADR 0019 §11): more than 5000 points (and the density cells) are drawn into
 * an `ImageData` buffer twice the plot box, shown as an `<image>` inside the chart's SVG under the lines and the
 * frame — so the SVG export carries it as an embedded image × 2 with the axes as vectors. On screen the image is a
 * blob URL; `inlineRasters` turns it into a data URL before an export (an SVG loaded as an image takes no blob URLs).
 * The drawing is pure (`rasterPoints`, `rasterRects` — tested in node): 200 000 points ≤ 100 ms.
 */
import { useEffect, useMemo, useState } from "react";
import { axisValue, type AxisKind, type Scale } from "./axisScale";
import type { PlotBox } from "./PlotFrame";

/** Raster pixels per viewBox pixel: the PNG export is twice the chart (Т7.25-3). */
export const RASTER_SCALE = 2;
/** From this many points a cloud is drawn on the canvas layer (fewer — SVG circles with a tooltip each). */
export const RASTER_FROM = 5000;

/** A scale as `px = (t − t0) · k + p0` in raster pixels, t — the value in the space of the axis. */
export interface AxisMap {
  kind: AxisKind;
  t0: number;
  k: number;
  p0: number;
}

/** The map of a scale onto the raster that starts at viewBox pixel `origin`, `factor` raster pixels per pixel. */
export function axisMap(scale: Scale, origin: number, factor = RASTER_SCALE): AxisMap {
  const lo = axisValue(scale.kind, scale.domain.min) ?? 0;
  const hi = axisValue(scale.kind, scale.domain.max) ?? lo + 1;
  const [from, to] = scale.range;
  return { kind: scale.kind, t0: lo, k: (((to - from) / (hi - lo || 1)) * factor), p0: (from - origin) * factor };
}

/** The raster pixel of a value; NaN — the axis cannot show it. */
export function mapValue(m: AxisMap, v: number | null | undefined): number {
  if (v === null || v === undefined) return Number.NaN;
  const t = m.kind === "log" ? (v > 0 ? Math.log10(v) : Number.NaN) : m.kind === "prob" ? (axisValue("prob", v) ?? Number.NaN) : v;
  return (t - m.t0) * m.k + m.p0;
}

/**
 * Squares of `size` raster pixels at the points (x, y) into `buf` (w × h, Uint32 RGBA). `colors` — one colour for
 * all, or one per point (0 — not drawn). Points the axes cannot show or outside the raster are skipped; the count
 * drawn is returned.
 */
export function rasterPoints(buf: Uint32Array, w: number, h: number, xs: ArrayLike<number | null>, ys: ArrayLike<number | null>, mx: AxisMap, my: AxisMap, colors: number | ArrayLike<number>, size = 4, only?: ArrayLike<number>): number {
  const half = size / 2;
  const n = only ? only.length : xs.length;
  const one = typeof colors === "number";
  let drawn = 0;
  for (let j = 0; j < n; j++) {
    const i = only ? only[j] : j;
    const col = one ? colors : (colors as ArrayLike<number>)[i];
    if (!col) continue;
    const px = mapValue(mx, xs[i]);
    const py = mapValue(my, ys[i]);
    if (!(px >= -half && px < w + half && py >= -half && py < h + half)) continue;
    const xa = Math.max(0, Math.round(px - half));
    const xb = Math.min(w, Math.round(px + half));
    const ya = Math.max(0, Math.round(py - half));
    const yb = Math.min(h, Math.round(py + half));
    for (let y = ya; y < yb; y++) {
      const row = y * w;
      for (let x = xa; x < xb; x++) buf[row + x] = col;
    }
    drawn++;
  }
  return drawn;
}

/** A rectangle in raster pixels with its colour. */
export interface RasterRect {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  color: number;
}

/** Filled rectangles (density cells) into `buf`; corners in any order, clipped to the raster. */
export function rasterRects(buf: Uint32Array, w: number, h: number, rects: readonly RasterRect[]): void {
  for (const r of rects) {
    const xa = Math.max(0, Math.round(Math.min(r.x0, r.x1)));
    const xb = Math.min(w, Math.round(Math.max(r.x0, r.x1)));
    const ya = Math.max(0, Math.round(Math.min(r.y0, r.y1)));
    const yb = Math.min(h, Math.round(Math.max(r.y0, r.y1)));
    for (let y = ya; y < yb; y++) buf.fill(r.color, y * w + xa, y * w + xb);
  }
}

/** The size of the raster of a plot box. */
export const rasterSize = (box: PlotBox, factor = RASTER_SCALE): { w: number; h: number } => ({ w: Math.max(1, Math.round((box.x1 - box.x0) * factor)), h: Math.max(1, Math.round((box.y1 - box.y0) * factor)) });

/** Blobs of the raster images on screen by their URL (the export inlines them). */
const BLOBS = new Map<string, Blob>();

const asDataUrl = (blob: Blob): Promise<string> =>
  new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(r.error ?? new Error("raster"));
    r.readAsDataURL(blob);
  });

/** Before an SVG or PNG export: every raster image of the chart gets its picture as a data URL. */
export async function inlineRasters(svg: SVGSVGElement | null): Promise<void> {
  if (!svg) return;
  for (const img of Array.from(svg.querySelectorAll("image[data-layer]"))) {
    const href = img.getAttribute("href") ?? "";
    const blob = BLOBS.get(href);
    if (blob) img.setAttribute("href", await asDataUrl(blob));
  }
}

/** The picture shown by a raster layer: a new one revokes the one before it (and takes its place); `drop` — the last. */
export function rasterUrls(): { show(blob: Blob): string; drop(): void } {
  let current: string | null = null;
  const revoke = (url: string | null) => {
    if (!url) return;
    BLOBS.delete(url);
    URL.revokeObjectURL(url);
  };
  return {
    show(blob) {
      const url = URL.createObjectURL(blob);
      BLOBS.set(url, blob);
      revoke(current);
      current = url;
      return url;
    },
    drop() {
      revoke(current);
      current = null;
    },
  };
}

/**
 * The canvas layer inside a chart's SVG: `draw` fills the buffer (w × h raster pixels of the plot box); it runs
 * again when `version` changes. The previous picture stays until the new one is ready.
 */
export function RasterLayer({ box, draw, version, label = "points", factor = RASTER_SCALE }: { box: PlotBox; draw: (buf: Uint32Array, w: number, h: number) => void; version: unknown; label?: string; factor?: number }) {
  const [href, setHref] = useState<string | null>(null);
  const urls = useMemo(rasterUrls, []);
  useEffect(() => {
    if (typeof document === "undefined") return;
    const { w, h } = rasterSize(box, factor);
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const img = ctx.createImageData(w, h);
    draw(new Uint32Array(img.data.buffer), w, h);
    ctx.putImageData(img, 0, 0);
    let alive = true;
    // the old picture stays until the next one is shown, then goes
    canvas.toBlob((blob) => {
      if (alive && blob) setHref(urls.show(blob));
    }, "image/png");
    return () => {
      alive = false;
    };
  }, [version, box.x0, box.x1, box.y0, box.y1, factor]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => urls.drop(), [urls]);
  if (!href) return null;
  return <image data-layer={label} href={href} x={box.x0} y={box.y0} width={box.x1 - box.x0} height={box.y1 - box.y0} preserveAspectRatio="none" style={{ imageRendering: "auto" }} />;
}
