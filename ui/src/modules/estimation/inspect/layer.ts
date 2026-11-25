/**
 * The layer of the inspector of a block in the main 3D (ТЗ 7.35 Д-30; a scene layer like `viz/ellipsoid/layer.ts`): the segments from the centre
 * of the block to the probes taken coloured by the weight (positive — Cobalt, negative — Copper Orange), the ends of them, the probes cut off in
 * Slate (a point; a thin line to those cut off by a limit) and the probe chosen in the table lit. Lines of `LineSegments2` with the
 * colours of the points (screen-space width), clipped by the section window as the rest; counted in «Вписать» (`bbox`). Float32 from the
 * integer origin (`build.ts`). Three.js: a lazy chunk (`scene.ts`).
 */
import * as THREE from "three";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import type { SceneLayer } from "../../../viz/sceneTypes";
import { COLORS } from "./view";
import type { SegmentsBuild } from "./build";

export interface SegmentsContext {
  clip: THREE.Plane[];
  resolution: THREE.Vector2;
}

/** The look: widths of the lines and sizes of the points, px. */
export const LOOK = { taken: 2.6, cut: 1, point: 7, cutPoint: 5, pick: 14, cutOpacity: 0.5 } as const;

/** sRGB 0–1 → the linear working space of three.js (vertex colours are not converted by the renderer). */
function linear(srgb: ArrayLike<number>): Float32Array {
  const out = new Float32Array(srgb.length);
  const c = new THREE.Color();
  for (let i = 0; i < srgb.length; i += 3) {
    c.setRGB(srgb[i], srgb[i + 1], srgb[i + 2], THREE.SRGBColorSpace);
    out[i] = c.r;
    out[i + 1] = c.g;
    out[i + 2] = c.b;
  }
  return out;
}

export interface SegmentsShown {
  taken: number;
  cut: number;
  points: number;
  excluded: boolean;
  segments: boolean;
  picked: number | null;
}

export class SegmentsLayer implements SceneLayer {
  readonly object = new THREE.Group();
  readonly origin: { x: number; y: number; z: number };
  readonly bbox: number[];
  private readonly materials: THREE.Material[] = [];
  private readonly geometries: { dispose(): void }[] = [];
  private readonly takenLines: LineSegments2 | null;
  private readonly cutLines: LineSegments2 | null;
  private readonly takenPoints: THREE.Points;
  private readonly cutPoints: THREE.Points;
  private readonly ring: THREE.Points;
  private ringId: number | null = null;
  private segments = true;
  private excluded = true;

  constructor(
    readonly build: SegmentsBuild,
    private readonly ctx: SegmentsContext,
  ) {
    this.origin = build.origin;
    this.bbox = [...build.reach];
    this.takenLines = build.taken.ids.length ? this.lines(build.taken.positions, linear(build.taken.colors), LOOK.taken, 0.95, 9) : null;
    this.cutLines = build.cut.ids.length ? this.lines(build.cut.positions, null, LOOK.cut, LOOK.cutOpacity, 8) : null;
    const { points } = build;
    const split = points.taken * 3;
    this.takenPoints = this.points(points.positions.subarray(0, split), linear(points.colors.subarray(0, split)), LOOK.point, 10);
    this.cutPoints = this.points(points.positions.subarray(split), linear(points.colors.subarray(split)), LOOK.cutPoint, 8);
    this.ring = this.points(new Float32Array(0), new Float32Array(0), LOOK.pick, 11);
    this.object.add(...[this.takenLines, this.cutLines].filter((x): x is LineSegments2 => x !== null), this.takenPoints, this.cutPoints, this.ring);
    this.apply();
  }

  private lines(positions: Float32Array, colors: Float32Array | null, width: number, opacity: number, order: number): LineSegments2 {
    const g = new LineSegmentsGeometry();
    g.setPositions(positions);
    if (colors) g.setColors(colors);
    const m = new LineMaterial({ linewidth: width, worldUnits: false, vertexColors: colors !== null, transparent: opacity < 1, opacity });
    if (!colors) m.color = new THREE.Color(COLORS.excluded);
    m.uniforms.resolution.value = this.ctx.resolution;
    m.clippingPlanes = this.ctx.clip;
    this.materials.push(m);
    this.geometries.push(g);
    const l = new LineSegments2(g, m);
    l.renderOrder = order;
    return l;
  }

  private points(positions: Float32Array, colors: Float32Array, size: number, order: number): THREE.Points {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(positions), 3));
    g.setAttribute("color", new THREE.BufferAttribute(new Float32Array(colors), 3));
    const m = new THREE.PointsMaterial({ size, sizeAttenuation: false, vertexColors: true, clippingPlanes: this.ctx.clip });
    this.materials.push(m);
    this.geometries.push(g);
    const p = new THREE.Points(g, m);
    p.renderOrder = order;
    return p;
  }

  private apply(): void {
    if (this.takenLines) this.takenLines.visible = this.segments;
    this.takenPoints.visible = this.segments;
    if (this.cutLines) this.cutLines.visible = this.excluded;
    this.cutPoints.visible = this.excluded;
    this.ring.visible = this.ringId !== null;
  }

  /** What is drawn: the segments and the probes taken, the probes cut off. */
  setShow(show: { segments: boolean; excluded: boolean }): void {
    this.segments = show.segments;
    this.excluded = show.excluded;
    this.apply();
  }

  /** Lights the probe `id` (`Candidate.i`) with a ring in Light Stone; null — none; a probe that has no point is ignored. */
  setPicked(id: number | null): void {
    const { points } = this.build;
    const k = id === null ? -1 : points.ids.indexOf(id);
    this.ringId = k >= 0 ? id : null;
    const g = this.ring.geometry;
    const at = k >= 0 ? points.positions.slice(3 * k, 3 * k + 3) : new Float32Array(0);
    g.setAttribute("position", new THREE.BufferAttribute(at, 3));
    g.setAttribute("color", new THREE.BufferAttribute(k >= 0 ? linear(hex(COLORS.pick)) : new Float32Array(0), 3));
    g.computeBoundingSphere();
    this.apply();
  }

  shown(): SegmentsShown {
    return { taken: this.build.taken.ids.length, cut: this.build.cut.ids.length, points: this.build.points.ids.length, excluded: this.excluded, segments: this.segments, picked: this.ringId };
  }

  dispose(): void {
    for (const g of this.geometries) g.dispose();
    for (const m of this.materials) m.dispose();
    this.geometries.length = 0;
    this.materials.length = 0;
    this.object.clear();
  }
}

const hex = (s: string): [number, number, number] => [1, 3, 5].map((i) => parseInt(s.slice(i, i + 2), 16) / 255) as [number, number, number];
