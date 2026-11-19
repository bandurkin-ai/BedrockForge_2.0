/**
 * The shadow of a series of sections in the 3D window (ТЗ 8.1.3; ADR 0016 §5; `SceneLayer`, key `shadow`): the
 * closed contours of the neighbouring sections (k − 1, k + 1 or both) as grey dashes ~8 px long whatever the
 * zoom — not clipped by the window of the section (they lie outside it), not picked (no `pick`), not counted in
 * «Вписать» (no `bbox`), never snap targets (the snap of the editor knows nothing of this layer). float32 from an
 * integer origin near the contours (`viz/origin.ts`). A lazy chunk with three.js.
 */
import * as THREE from "three";
import type { Origin } from "../origin";
import type { FrameContext, SceneLayer } from "../sceneTypes";

/** The contours of the shadow (BFA1 `series_contours`): closed lines without a repeated vertex. */
export interface ShadowLines {
  xyz: Float64Array;
  /** lines + 1 values */
  offsets: ArrayLike<number>;
}

export const SHADOW_ORDER = 5;
const DASH_PX = 8;

/** The integer origin of the contours: the rounded first vertex with a height (none — null). */
export function shadowOrigin(lines: ShadowLines): Origin | null {
  const xyz = lines.xyz;
  for (let i = 0; i + 2 < xyz.length; i += 3) {
    if (Number.isFinite(xyz[i]) && Number.isFinite(xyz[i + 1]) && Number.isFinite(xyz[i + 2])) return { x: Math.round(xyz[i]), y: Math.round(xyz[i + 1]), z: Math.round(xyz[i + 2]) };
  }
  return null;
}

/**
 * Segments of the closed contours from `origin` (6 floats each, the closing one included) and the distance along
 * each (0 at its start: the dash restarts at every segment). A vertex without a height is left out with its two
 * segments (a contour of a section always has one).
 */
export function shadowSegments(lines: ShadowLines, origin: Origin): { positions: Float32Array; lengths: Float32Array; count: number } {
  const { xyz, offsets } = lines;
  let n = 0;
  for (let l = 0; l + 1 < offsets.length; l++) {
    const c = offsets[l + 1] - offsets[l];
    n += c >= 3 ? c : c === 2 ? 1 : 0;
  }
  const positions = new Float32Array(n * 6);
  const lengths = new Float32Array(n * 2);
  let k = 0;
  const ok = (i: number) => Number.isFinite(xyz[i * 3]) && Number.isFinite(xyz[i * 3 + 1]) && Number.isFinite(xyz[i * 3 + 2]);
  for (let l = 0; l + 1 < offsets.length; l++) {
    const a = offsets[l];
    const b = offsets[l + 1];
    const c = b - a;
    const segs = c >= 3 ? c : c === 2 ? 1 : 0;
    for (let s = 0; s < segs; s++) {
      const i = a + s;
      const j = s + 1 < c ? i + 1 : a;
      if (!ok(i) || !ok(j)) continue;
      const dx = xyz[j * 3] - xyz[i * 3];
      const dy = xyz[j * 3 + 1] - xyz[i * 3 + 1];
      const dz = xyz[j * 3 + 2] - xyz[i * 3 + 2];
      positions.set([xyz[i * 3] - origin.x, xyz[i * 3 + 1] - origin.y, xyz[i * 3 + 2] - origin.z, xyz[j * 3] - origin.x, xyz[j * 3 + 1] - origin.y, xyz[j * 3 + 2] - origin.z], k * 6);
      lengths[k * 2] = 0;
      lengths[k * 2 + 1] = Math.hypot(dx, dy, dz);
      k++;
    }
  }
  return { positions: positions.subarray(0, k * 6), lengths: lengths.subarray(0, k * 2), count: k };
}

export class ShadowLayer implements SceneLayer {
  readonly object = new THREE.Group();
  origin: Origin = { x: 0, y: 0, z: 0 };
  /** not counted in «Вписать» */
  readonly bbox = null;
  private readonly lines: THREE.LineSegments;
  private readonly material: THREE.LineDashedMaterial;
  segments = 0;

  constructor(color: string) {
    // no clipping planes: the shadow is what lies outside the window
    this.material = new THREE.LineDashedMaterial({ color: new THREE.Color(color), dashSize: 1, gapSize: 1, transparent: true, opacity: 0.7, depthWrite: false });
    this.lines = new THREE.LineSegments(new THREE.BufferGeometry(), this.material);
    this.lines.renderOrder = SHADOW_ORDER;
    this.lines.frustumCulled = false;
    this.lines.visible = false;
    this.object.add(this.lines);
  }

  /** New contours (null — none); true — the origin moved (the scene places the layer again). */
  show(lines: ShadowLines | null): boolean {
    const o = lines ? shadowOrigin(lines) : null;
    const moved = !!o && (o.x !== this.origin.x || o.y !== this.origin.y || o.z !== this.origin.z);
    if (o) this.origin = o;
    const built = lines && o ? shadowSegments(lines, o) : null;
    this.lines.geometry.dispose();
    const g = new THREE.BufferGeometry();
    if (built?.count) {
      g.setAttribute("position", new THREE.BufferAttribute(built.positions, 3));
      g.setAttribute("lineDistance", new THREE.BufferAttribute(built.lengths, 1));
    }
    this.lines.geometry = g;
    this.segments = built?.count ?? 0;
    this.lines.visible = this.segments > 0;
    return moved;
  }

  setColor(color: string): void {
    this.material.color = new THREE.Color(color);
  }

  beforeRender(ctx: FrameContext): void {
    const ppu = ctx.pixelsPerUnit(ctx.camera.target);
    const dash = DASH_PX / Math.max(ppu, 1e-9);
    this.material.dashSize = dash;
    this.material.gapSize = dash * 0.75;
  }

  dispose(): void {
    this.lines.geometry.dispose();
    this.material.dispose();
  }
}
