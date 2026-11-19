/**
 * The marks of «Править треугольники» in the 3D window (ADR 0015 §6; `SceneLayer`, key `triangles`): the
 * triangles marked to be removed (a translucent fill and the outline in the colour of danger), the new ones (in
 * the colour of success) and the vertices clicked for the next triangle with the edges between them. The corners
 * come in project coordinates float64 (exact, from `POST /wireframes/pick`) and are drawn as float32 offsets from
 * an integer origin (`viz/origin.ts`, ТЗ 5.4), over the wireframe, clipped like the rest.
 */
import * as THREE from "three";
import { integerOrigin, toRelativeFloat32, type Origin } from "../origin";
import type { SceneLayer } from "../sceneTypes";

/** Corners in project coordinates: nine numbers a triangle; `pending` — three numbers a vertex. */
export interface MarkData {
  remove: Float64Array;
  add: Float64Array;
  pending: Float64Array;
}

export interface MarkColors {
  remove: string;
  add: string;
  pending: string;
}

const ORDER = 55;

/** All the corners of the marks, one array (for the origin and the extent). */
export function markPoints(d: MarkData): Float64Array {
  const all = new Float64Array(d.remove.length + d.add.length + d.pending.length);
  all.set(d.remove, 0);
  all.set(d.add, d.remove.length);
  all.set(d.pending, d.remove.length + d.add.length);
  return all;
}

/** The edges of triangles given by corners: three segments a triangle (six points). */
export function triangleEdges(rel: Float32Array): Float32Array {
  const out = new Float32Array((rel.length / 9) * 18);
  for (let t = 0, o = 0; t < rel.length; t += 9) {
    for (const [a, b] of [[0, 1], [1, 2], [2, 0]]) {
      for (let k = 0; k < 3; k++) out[o + k] = rel[t + a * 3 + k];
      for (let k = 0; k < 3; k++) out[o + 3 + k] = rel[t + b * 3 + k];
      o += 6;
    }
  }
  return out;
}

/** The edges between the pending vertices (a path). */
export function pathEdges(rel: Float32Array): Float32Array {
  const n = rel.length / 3;
  const out = new Float32Array(Math.max(0, n - 1) * 6);
  for (let i = 0; i + 1 < n; i++) out.set(rel.subarray(i * 3, i * 3 + 6), i * 6);
  return out;
}

function bbox(xyz: Float64Array): number[] | null {
  if (!xyz.length) return null;
  const lo = [Infinity, Infinity, Infinity];
  const hi = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < xyz.length; i += 3) {
    for (let k = 0; k < 3; k++) {
      lo[k] = Math.min(lo[k], xyz[i + k]);
      hi[k] = Math.max(hi[k], xyz[i + k]);
    }
  }
  return [...lo, ...hi];
}

export class TrianglesMark implements SceneLayer {
  readonly object = new THREE.Group();
  origin: Origin;
  bbox: number[] | null;
  private readonly disposables: { geometry: THREE.BufferGeometry; material: THREE.Material | THREE.Material[] }[] = [];

  constructor(data: MarkData, clip: THREE.Plane[], colors: MarkColors, pixelRatio = 1) {
    const all = markPoints(data);
    this.origin = integerOrigin(all);
    this.bbox = bbox(all);
    const common = { clippingPlanes: clip, transparent: true, depthTest: false, depthWrite: false };
    const tris = (xyz: Float64Array, color: string) => {
      if (!xyz.length) return;
      const rel = toRelativeFloat32(xyz, this.origin);
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(rel, 3));
      const fill = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ ...common, color: new THREE.Color(color), opacity: 0.35, side: THREE.DoubleSide }));
      const eg = new THREE.BufferGeometry();
      eg.setAttribute("position", new THREE.BufferAttribute(triangleEdges(rel), 3));
      const edges = new THREE.LineSegments(eg, new THREE.LineBasicMaterial({ ...common, color: new THREE.Color(color) }));
      this.add(fill, ORDER);
      this.add(edges, ORDER + 1);
    };
    tris(data.remove, colors.remove);
    tris(data.add, colors.add);
    if (data.pending.length) {
      const rel = toRelativeFloat32(data.pending, this.origin);
      const pg = new THREE.BufferGeometry();
      pg.setAttribute("position", new THREE.BufferAttribute(rel, 3));
      this.add(new THREE.Points(pg, new THREE.PointsMaterial({ ...common, color: new THREE.Color(colors.pending), size: 9 * pixelRatio, sizeAttenuation: false })), ORDER + 2);
      const lg = new THREE.BufferGeometry();
      lg.setAttribute("position", new THREE.BufferAttribute(pathEdges(rel), 3));
      this.add(new THREE.LineSegments(lg, new THREE.LineBasicMaterial({ ...common, color: new THREE.Color(colors.pending) })), ORDER + 2);
    }
  }

  private add(o: THREE.Mesh | THREE.LineSegments | THREE.Points, order: number): void {
    o.renderOrder = order;
    o.frustumCulled = false;
    this.object.add(o);
    this.disposables.push(o);
  }

  dispose(): void {
    for (const d of this.disposables) {
      d.geometry.dispose();
      for (const m of Array.isArray(d.material) ? d.material : [d.material]) m.dispose();
    }
  }
}
