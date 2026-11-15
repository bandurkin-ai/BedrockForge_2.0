import type { Plane } from "./coords";
import type { EditLayer, EditLine, Vec3 } from "./model";

export function lineLength(points: Vec3[], closed: boolean): number {
  const n = points.length;
  if (n < 2) return 0;
  let sum = 0;
  for (let i = 1; i < n; i++) {
    const a = points[i - 1];
    const b = points[i];
    sum += Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);
  }
  if (closed && n > 2) {
    const a = points[n - 1];
    const b = points[0];
    sum += Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);
  }
  return sum;
}

export function planLength(points: Vec3[], closed: boolean): number {
  const n = points.length;
  if (n < 2) return 0;
  let sum = 0;
  for (let i = 1; i < n; i++) sum += Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]);
  if (closed && n > 2) sum += Math.hypot(points[0][0] - points[n - 1][0], points[0][1] - points[n - 1][1]);
  return sum;
}

export function centroid(points: Vec3[]): Vec3 {
  const n = points.length;
  if (n === 0) return [0, 0, 0];
  // Sums of differences from the first point: UTM-size numbers lose nothing.
  const o = points[0];
  let x = 0;
  let y = 0;
  let z = 0;
  for (const p of points) {
    x += p[0] - o[0];
    y += p[1] - o[1];
    z += p[2] - o[2];
  }
  return [o[0] + x / n, o[1] + y / n, o[2] + z / n];
}

/**
 * The Newell vector of the polygon through `points` (taken as closed): its direction is the normal
 * of the best-fit plane, its length twice the area. Computed relative to the first point.
 */
export function newellVector(points: Vec3[]): Vec3 {
  const n = points.length;
  if (n < 3) return [0, 0, 0];
  const o = points[0];
  let nx = 0;
  let ny = 0;
  let nz = 0;
  let ax = points[n - 1][0] - o[0];
  let ay = points[n - 1][1] - o[1];
  let az = points[n - 1][2] - o[2];
  for (let i = 0; i < n; i++) {
    const bx = points[i][0] - o[0];
    const by = points[i][1] - o[1];
    const bz = points[i][2] - o[2];
    nx += (ay - by) * (az + bz);
    ny += (az - bz) * (ax + bx);
    nz += (ax - bx) * (ay + by);
    ax = bx;
    ay = by;
    az = bz;
  }
  return [nx, ny, nz];
}

/**
 * The Newell normal through the centroid; null for fewer than 3 points and for points on one line.
 * The polygon runs counter-clockwise when seen from the side the normal points to.
 */
export function bestFitPlane(points: Vec3[]): Plane | null {
  if (points.length < 3) return null;
  const v = newellVector(points);
  const len = Math.hypot(v[0], v[1], v[2]);
  const o = points[0];
  let scale = 0;
  for (const p of points) scale = Math.max(scale, (p[0] - o[0]) ** 2 + (p[1] - o[1]) ** 2 + (p[2] - o[2]) ** 2);
  if (!(len > 1e-13 * scale) || !Number.isFinite(len)) return null;
  return { point: centroid(points), normal: [v[0] / len, v[1] / len, v[2] / len] };
}

/** Eigenvectors of a symmetric 3x3 matrix (cyclic Jacobi), sorted by ascending eigenvalue. */
function eigenSymmetric3(a: number[][]): { values: number[]; vectors: Vec3[] } {
  const v = [
    [1, 0, 0],
    [0, 1, 0],
    [0, 0, 1],
  ];
  for (let sweep = 0; sweep < 32; sweep++) {
    const off = Math.abs(a[0][1]) + Math.abs(a[0][2]) + Math.abs(a[1][2]);
    if (off <= 1e-300 || off <= 1e-18 * (Math.abs(a[0][0]) + Math.abs(a[1][1]) + Math.abs(a[2][2]))) break;
    for (let p = 0; p < 2; p++) {
      for (let q = p + 1; q < 3; q++) {
        if (a[p][q] === 0) continue;
        const theta = (a[q][q] - a[p][p]) / (2 * a[p][q]);
        const t = Math.sign(theta || 1) / (Math.abs(theta) + Math.sqrt(theta * theta + 1));
        const c = 1 / Math.sqrt(t * t + 1);
        const s = t * c;
        for (let k = 0; k < 3; k++) {
          const akp = a[k][p];
          const akq = a[k][q];
          a[k][p] = c * akp - s * akq;
          a[k][q] = s * akp + c * akq;
        }
        for (let k = 0; k < 3; k++) {
          const apk = a[p][k];
          const aqk = a[q][k];
          a[p][k] = c * apk - s * aqk;
          a[q][k] = s * apk + c * aqk;
        }
        for (let k = 0; k < 3; k++) {
          const vkp = v[k][p];
          const vkq = v[k][q];
          v[k][p] = c * vkp - s * vkq;
          v[k][q] = s * vkp + c * vkq;
        }
      }
    }
  }
  const order = [0, 1, 2].sort((i, j) => a[i][i] - a[j][j]);
  return {
    values: order.map((i) => a[i][i]),
    vectors: order.map((i): Vec3 => [v[0][i], v[1][i], v[2][i]]),
  };
}

/**
 * The least-squares plane of a point set (works for open lines of any shape, where the Newell vector
 * may cancel out). Points on one straight line give the plane through that line closest to horizontal;
 * null for a single point and for a vertical straight line.
 * The normal is turned up (or, for a vertical plane, to the north / east) to make the result repeatable.
 */
export function leastSquaresPlane(points: Vec3[]): Plane | null {
  if (points.length < 2) return null;
  const c = centroid(points);
  const m = [
    [0, 0, 0],
    [0, 0, 0],
    [0, 0, 0],
  ];
  for (const p of points) {
    const d = [p[0] - c[0], p[1] - c[1], p[2] - c[2]];
    for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) m[i][j] += d[i] * d[j];
  }
  const { values, vectors } = eigenSymmetric3(m);
  if (!(values[2] > 0)) return null;
  let n: Vec3;
  if (values[1] <= 1e-12 * values[2]) {
    const e = vectors[2];
    n = [-e[2] * e[0], -e[2] * e[1], 1 - e[2] * e[2]];
    const len = Math.hypot(n[0], n[1], n[2]);
    if (len < 1e-6) return null;
    n = [n[0] / len, n[1] / len, n[2] / len];
  } else {
    n = vectors[0];
    const len = Math.hypot(n[0], n[1], n[2]);
    n = [n[0] / len, n[1] / len, n[2] / len];
  }
  const lead = Math.abs(n[2]) > 1e-9 ? n[2] : Math.abs(n[1]) > 1e-9 ? n[1] : n[0];
  if (lead < 0) n = [-n[0], -n[1], -n[2]];
  return { point: c, normal: n };
}

/** Area in the polygon's own best-fit plane: exact for planar polygons, tilted ones included. */
export function polygonArea(points: Vec3[]): number {
  const v = newellVector(points);
  return Math.hypot(v[0], v[1], v[2]) / 2;
}

/** Area of the projection onto the horizontal plane. */
export function planArea(points: Vec3[]): number {
  const n = points.length;
  if (n < 3) return 0;
  const o = points[0];
  let sum = 0;
  let ax = points[n - 1][0] - o[0];
  let ay = points[n - 1][1] - o[1];
  for (let i = 0; i < n; i++) {
    const bx = points[i][0] - o[0];
    const by = points[i][1] - o[1];
    sum += ax * by - bx * ay;
    ax = bx;
    ay = by;
  }
  return Math.abs(sum) / 2;
}

export interface LineMeasures {
  vertices: number;
  length: number;
  planLength: number;
  closed: boolean;
  /** null for an open line */
  area: number | null;
  planArea: number | null;
  perimeter: number | null;
}

export function lineMeasures(line: EditLine): LineMeasures {
  const length = lineLength(line.points, line.closed);
  const polygon = line.closed && line.points.length >= 3;
  return {
    vertices: line.points.length,
    length,
    planLength: planLength(line.points, line.closed),
    closed: line.closed,
    area: polygon ? polygonArea(line.points) : null,
    planArea: polygon ? planArea(line.points) : null,
    perimeter: polygon ? length : null,
  };
}

export interface LayerMeasures {
  lines: number;
  vertices: number;
  length: number;
  planLength: number;
  closedLines: number;
  area: number;
  planArea: number;
}

export function layerMeasures(layer: EditLayer): LayerMeasures {
  const out: LayerMeasures = { lines: layer.lines.length, vertices: 0, length: 0, planLength: 0, closedLines: 0, area: 0, planArea: 0 };
  for (const line of layer.lines) {
    out.vertices += line.points.length;
    out.length += lineLength(line.points, line.closed);
    out.planLength += planLength(line.points, line.closed);
    if (line.closed && line.points.length >= 3) {
      out.closedLines++;
      out.area += polygonArea(line.points);
      out.planArea += planArea(line.points);
    }
  }
  return out;
}
