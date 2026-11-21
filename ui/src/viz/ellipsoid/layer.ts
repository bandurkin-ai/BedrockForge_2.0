/**
 * An anisotropy ellipsoid in the main 3D (ТЗ 8.6, 7.35 Д-14; ADR 0022 §9, решения 17–19) — a scene layer
 * (`Scene.addLayer`; образец — `viz/swath/layer.ts`): the selected structure as a translucent surface, its three
 * principal sections and the disk of the plane a1–a2 with the samples in its tolerance; the other structures as
 * thin sections. Every structure is one shared unit sphere (circles, disk) under its own matrix (`build.poseMatrix`):
 * a drag of a handle calls `setPose` — a new matrix, no new geometry (the samples of the disk are filtered again,
 * a small buffer). Clipped by the section window as the rest; counted in «Вписать» (`bbox`, updated in place).
 * Colours — of the model (by default Cobalt), the disk Slate (docs/brand). Float32 from the integer origin of the
 * layer (the centre rounded).
 */
import * as THREE from "three";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import type { SceneLayer } from "../sceneTypes";
import { BRAND, poseMatrix, poseOf, reachOf, sampleScale, samplesInTolerance, unitCircle, unitSections, type EllipsoidBuild, type SampleSelection, type Samples, type StructurePose, type StructureView } from "./build";

/** sRGB 0–1 → the linear working space of three.js (vertex colours are not converted by the renderer). */
function linear(srgb: Float32Array): Float32Array {
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

type SampleScale = Parameters<typeof samplesInTolerance>[4];

export interface EllipsoidLayerContext {
  /** the shared clipping planes of the scene (the section window) */
  clip: THREE.Plane[];
  /** the size of the view in px (the `resolution` of every LineMaterial) */
  resolution: THREE.Vector2;
}

/** The look of the layer: shares of opacity and line widths, px. */
export const LOOK = {
  surface: 0.16,
  disk: 0.22,
  sections: 1.8,
  thin: 1,
  rim: 1.4,
  thinOpacity: 0.55,
  sample: 6,
} as const;

interface Drawn {
  group: THREE.Group;
  surface: THREE.Mesh;
  sections: LineSegments2;
  thin: LineSegments2;
  disk: THREE.Mesh;
  rim: LineSegments2;
}

/** What the layer shows now, for the tests and the E2E hook. */
export interface EllipsoidShown {
  selected: number;
  /** the matrices over the unit sphere, rows (float64) */
  bases: number[][];
  samples: number;
  visible: boolean;
}

export class EllipsoidLayer implements SceneLayer {
  readonly object = new THREE.Group();
  readonly origin: { x: number; y: number; z: number };
  readonly bbox: number[];
  private readonly poses: StructurePose[];
  private readonly drawn: Drawn[] = [];
  private readonly materials: THREE.Material[] = [];
  private readonly sphere = new THREE.SphereGeometry(1, 48, 24);
  private readonly circles = new LineSegmentsGeometry().setPositions(unitSections(96));
  private readonly diskFace = new THREE.CircleGeometry(1, 96);
  private readonly diskRim = new LineSegmentsGeometry().setPositions(unitCircle("a1a2", 96));
  private readonly points: THREE.Points;
  private chosen: number;
  private samples: { data: Samples; thickness: number } | null = null;
  private picked: SampleSelection | null = null;
  private scale: SampleScale = null;

  constructor(
    readonly build: EllipsoidBuild,
    private readonly ctx: EllipsoidLayerContext,
  ) {
    this.origin = build.origin;
    this.bbox = [...build.reach];
    this.poses = build.structures.map((p) => ({ ...p }));
    this.chosen = build.selected;
    for (const pose of this.poses) this.drawn.push(this.draw(pose));
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(0), 3));
    g.setAttribute("color", new THREE.BufferAttribute(new Float32Array(0), 3));
    const pm = new THREE.PointsMaterial({ size: LOOK.sample, sizeAttenuation: false, vertexColors: true, clippingPlanes: ctx.clip });
    this.materials.push(pm);
    this.points = new THREE.Points(g, pm);
    this.points.renderOrder = 8;
    this.object.add(this.points);
    this.poses.forEach((_, i) => this.place(i));
    this.showSelected();
  }

  private lineMaterial(color: THREE.ColorRepresentation, width: number, opacity: number): LineMaterial {
    const m = new LineMaterial({ color: new THREE.Color(color).getHex(), linewidth: width, worldUnits: false, transparent: opacity < 1, opacity });
    m.uniforms.resolution.value = this.ctx.resolution;
    m.clippingPlanes = this.ctx.clip;
    this.materials.push(m);
    return m;
  }

  private basic(color: THREE.ColorRepresentation, opacity: number, side: THREE.Side): THREE.MeshBasicMaterial {
    const m = new THREE.MeshBasicMaterial({ color, transparent: true, opacity, side, depthWrite: false, clippingPlanes: this.ctx.clip });
    this.materials.push(m);
    return m;
  }

  private draw(pose: StructurePose): Drawn {
    const group = new THREE.Group();
    group.matrixAutoUpdate = false;
    const surface = new THREE.Mesh(this.sphere, this.basic(pose.color, LOOK.surface, THREE.FrontSide));
    surface.renderOrder = 6;
    const sections = new LineSegments2(this.circles, this.lineMaterial(pose.color, LOOK.sections, 0.95));
    sections.renderOrder = 7;
    const thin = new LineSegments2(this.circles, this.lineMaterial(pose.color, LOOK.thin, LOOK.thinOpacity));
    thin.renderOrder = 7;
    const disk = new THREE.Mesh(this.diskFace, this.basic(BRAND.slate, LOOK.disk, THREE.DoubleSide));
    disk.renderOrder = 5;
    const rim = new LineSegments2(this.diskRim, this.lineMaterial(BRAND.slate, LOOK.rim, 0.9));
    rim.renderOrder = 7;
    group.add(surface, sections, thin, disk, rim);
    this.object.add(group);
    return { group, surface, sections, thin, disk, rim };
  }

  private place(i: number): void {
    const g = this.drawn[i].group;
    const m = poseMatrix(this.build.offset, this.poses[i].basis);
    g.matrix.set(m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7], m[8], m[9], m[10], m[11], m[12], m[13], m[14], m[15]);
    g.matrixWorldNeedsUpdate = true;
  }

  private showSelected(): void {
    this.drawn.forEach((d, i) => {
      const full = i === this.chosen;
      d.surface.visible = full;
      d.sections.visible = full;
      d.disk.visible = full;
      d.rim.visible = full;
      d.thin.visible = !full;
    });
  }

  private refreshBbox(): void {
    const r = reachOf(this.build.centre, this.poses);
    for (let k = 0; k < 6; k++) this.bbox[k] = r[k];
  }

  private refreshSamples(): void {
    const g = this.points.geometry;
    this.picked = this.samples ? samplesInTolerance(this.build, this.poses[this.chosen], this.samples.data, this.samples.thickness, this.scale) : null;
    g.setAttribute("position", new THREE.BufferAttribute(this.picked?.position ?? new Float32Array(0), 3));
    g.setAttribute("color", new THREE.BufferAttribute(linear(this.picked?.color ?? new Float32Array(0)), 3));
    g.computeBoundingSphere();
  }

  /** The structure drawn in full (the others — thin sections); its disk takes the samples. */
  select(index: number): void {
    if (!Number.isInteger(index) || index < 0 || index >= this.poses.length) throw new RangeError("Нет такой структуры");
    if (index === this.chosen) return;
    this.chosen = index;
    this.showSelected();
    if (this.samples) this.refreshSamples();
  }

  get selected(): number {
    return this.chosen;
  }

  /**
   * A new pose of the structure `index` (a drag, the table, «общие оси»): only its matrix, no new geometry; the
   * colour stays unless given. Returns the pose drawn.
   */
  setPose(index: number, view: StructureView): StructurePose {
    if (!Number.isInteger(index) || index < 0 || index >= this.poses.length) throw new RangeError("Нет такой структуры");
    const old = this.poses[index];
    const pose = poseOf({ ...view, color: view.color ?? old.color });
    if (pose.color !== old.color) {
      const d = this.drawn[index];
      for (const o of [d.surface, d.sections, d.thin]) {
        const m = o.material as THREE.MeshBasicMaterial | LineMaterial;
        m.color.set(pose.color);
      }
    }
    this.poses[index] = pose;
    this.place(index);
    this.refreshBbox();
    if (index === this.chosen && this.samples) this.refreshSamples();
    return pose;
  }

  /** Every structure at once (as many as built). */
  setPoses(views: readonly StructureView[]): void {
    if (views.length !== this.poses.length) throw new RangeError("Число структур не совпадает с построенным слоем");
    views.forEach((v, i) => this.setPose(i, v));
  }

  pose(index: number): StructurePose {
    return this.poses[index];
  }

  /**
   * The samples of the preview (float64 from their integer origin) and the thickness of the layer at the disk, m;
   * null — none. `scale` — the colours of the values (by default linear over them).
   */
  setSamples(samples: Samples | null, thickness = 0, scale?: SampleScale): void {
    this.samples = samples ? { data: samples, thickness } : null;
    this.scale = scale !== undefined ? scale : samples ? sampleScale(samples.values) : null;
    this.refreshSamples();
  }

  /** The samples drawn now (their indices in the input), or null. */
  get sampled(): SampleSelection | null {
    return this.picked;
  }

  setVisible(on: boolean): void {
    this.object.visible = on;
  }

  shown(): EllipsoidShown {
    return { selected: this.chosen, bases: this.poses.map((p) => [...p.basis]), samples: this.picked?.indices.length ?? 0, visible: this.object.visible };
  }

  dispose(): void {
    this.sphere.dispose();
    this.circles.dispose();
    this.diskFace.dispose();
    this.diskRim.dispose();
    this.points.geometry.dispose();
    for (const m of this.materials) m.dispose();
    this.materials.length = 0;
    this.object.clear();
  }
}
