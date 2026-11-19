/**
 * Hit-testing the working copy on screen (ADR 0015 §3; BF `edit/pick.ts`). The working copy is at most
 * 1 million vertices, so this is brute force over its vertices with the projection written out in place (no
 * allocation per vertex). A vertex without a height is drawn — and hit — at `view.missingZ`.
 */
import type { EditLayer, VertexRef } from "./model";
import { projectSegment, segmentParamAt, SEG_X0, SEG_X1, SEG_Y0, SEG_Y1 } from "./snap";
import type { ViewTransform } from "./snap";

/** The vertex nearest to the pixel (px, py) within the radius; hidden vertices (`view.visible`) are skipped. */
export function nearestVertex(
  layer: EditLayer,
  view: ViewTransform,
  px: number,
  py: number,
  radiusPx: number,
): { ref: VertexRef; distancePx: number } | null {
  const m = view.matrix;
  const W = view.width;
  const H = view.height;
  const vis = view.visible;
  const zFill = view.missingZ ?? NaN;
  let best = radiusPx * radiusPx;
  let line = -1;
  let index = -1;
  for (const l of layer.lines) {
    const pts = l.points;
    for (let i = 0; i < pts.length; i++) {
      const x = pts[i][0];
      const y = pts[i][1];
      const z0 = pts[i][2];
      const z = z0 === z0 ? z0 : zFill;
      const cw = m[3] * x + m[7] * y + m[11] * z + m[15];
      if (!(cw > 0)) continue;
      const cz = m[2] * x + m[6] * y + m[10] * z + m[14];
      if (cz < -cw || cz > cw) continue;
      const dx = ((m[0] * x + m[4] * y + m[8] * z + m[12]) / cw + 1) * 0.5 * W - px;
      const dy = (1 - (m[1] * x + m[5] * y + m[9] * z + m[13]) / cw) * 0.5 * H - py;
      const d = dx * dx + dy * dy;
      if (d > best || (d === best && line >= 0)) continue;
      if (vis && !vis(x, y, z)) continue;
      best = d;
      line = l.id;
      index = i;
    }
  }
  return line < 0 ? null : { ref: { line, index }, distancePx: Math.sqrt(best) };
}

/**
 * The segment nearest to the pixel within the radius. `segment` k joins points k and k + 1 (the last
 * segment of a closed line joins the last point and the first); `t` is the parameter of the nearest
 * point along the 3D segment (0 at point k), ready for `insertVertex`.
 */
export function nearestSegment(
  layer: EditLayer,
  view: ViewTransform,
  px: number,
  py: number,
  radiusPx: number,
): { line: number; segment: number; t: number; distancePx: number } | null {
  const m = view.matrix;
  const W = view.width;
  const H = view.height;
  const vis = view.visible;
  const zFill = view.missingZ ?? NaN;
  const seg = new Float64Array(8);
  let best = radiusPx * radiusPx;
  let line = -1;
  let segment = -1;
  let param = 0;
  for (const l of layer.lines) {
    const pts = l.points;
    const n = pts.length;
    const segs = l.closed && n >= 3 ? n : n - 1;
    for (let k = 0; k < segs; k++) {
      const a = pts[k];
      const b = pts[k + 1 < n ? k + 1 : 0];
      const az = a[2] === a[2] ? a[2] : zFill;
      const bz = b[2] === b[2] ? b[2] : zFill;
      if (!projectSegment(m, W, H, a[0], a[1], az, b[0], b[1], bz, seg)) continue;
      const x0 = seg[SEG_X0];
      const y0 = seg[SEG_Y0];
      const ex = seg[SEG_X1] - x0;
      const ey = seg[SEG_Y1] - y0;
      const len2 = ex * ex + ey * ey;
      let s = len2 > 0 ? ((px - x0) * ex + (py - y0) * ey) / len2 : 0;
      s = s < 0 ? 0 : s > 1 ? 1 : s;
      const dx = x0 + ex * s - px;
      const dy = y0 + ey * s - py;
      const d = dx * dx + dy * dy;
      if (d > best || (d === best && line >= 0)) continue;
      const t = segmentParamAt(seg, px, py);
      if (vis && !vis(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, az + (bz - az) * t)) continue;
      best = d;
      line = l.id;
      segment = k;
      param = t;
    }
  }
  return line < 0 ? null : { line, segment, t: param, distancePx: Math.sqrt(best) };
}

/** Calls `hit(lineIndex, vertexIndex)` for every visible vertex inside the screen rectangle; stops a line when `hit` returns true. */
function scanBox(
  layer: EditLayer,
  view: ViewTransform,
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  hit: (line: number, index: number) => boolean,
): void {
  const m = view.matrix;
  const W = view.width;
  const H = view.height;
  const vis = view.visible;
  const zFill = view.missingZ ?? NaN;
  const left = Math.min(x0, x1);
  const right = Math.max(x0, x1);
  const top = Math.min(y0, y1);
  const bottom = Math.max(y0, y1);
  for (let li = 0; li < layer.lines.length; li++) {
    const pts = layer.lines[li].points;
    for (let i = 0; i < pts.length; i++) {
      const x = pts[i][0];
      const y = pts[i][1];
      const z0 = pts[i][2];
      const z = z0 === z0 ? z0 : zFill;
      const cw = m[3] * x + m[7] * y + m[11] * z + m[15];
      if (!(cw > 0)) continue;
      const cz = m[2] * x + m[6] * y + m[10] * z + m[14];
      if (cz < -cw || cz > cw) continue;
      const sx = ((m[0] * x + m[4] * y + m[8] * z + m[12]) / cw + 1) * 0.5 * W;
      if (sx < left || sx > right) continue;
      const sy = (1 - (m[1] * x + m[5] * y + m[9] * z + m[13]) / cw) * 0.5 * H;
      if (sy < top || sy > bottom) continue;
      if (vis && !vis(x, y, z)) continue;
      if (hit(li, i)) break;
    }
  }
}

/** Ids of the lines with at least one vertex inside the screen rectangle (any two opposite corners). */
export function linesInBox(layer: EditLayer, view: ViewTransform, x0: number, y0: number, x1: number, y1: number): number[] {
  const ids: number[] = [];
  scanBox(layer, view, x0, y0, x1, y1, (li) => {
    ids.push(layer.lines[li].id);
    return true;
  });
  return ids;
}

export function verticesInBox(layer: EditLayer, view: ViewTransform, x0: number, y0: number, x1: number, y1: number): VertexRef[] {
  const refs: VertexRef[] = [];
  scanBox(layer, view, x0, y0, x1, y1, (li, index) => {
    refs.push({ line: layer.lines[li].id, index });
    return false;
  });
  return refs;
}
