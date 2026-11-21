/**
 * A swath plot in the main 3D (ТЗ 7.30 «3D»; ADR 0019 §6; перенос `viz/swathLayer.ts` BF) — a scene layer
 * (`Scene.addLayer`): the cuboid cut into swaths (clipped by the section window with the rest), the plot of means
 * standing over it — it hangs on the axis of the cuboid and turns about it toward the viewer before every frame
 * (a matrix, no new geometry), so it reads the same in plan, on a section and in between; the swath under the
 * cursor of the window is lit (Т7.30-3). The integer origin of the layer is near the centre of the cuboid; the
 * buffers are float32 from that centre. `reach` — the extent «Вписать» and the standard views take.
 */
import * as THREE from "three";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import { cameraBasis } from "../core/views";
import type { Vec3 } from "../core/vec3";
import { LabelCache } from "../labels/sprites";
import type { FrameContext, SceneLayer } from "../sceneTypes";
import { placeChart, slabOf, type ChartLabel, type SwathBuild } from "./build";

/** Copper Orange of the brand (docs/brand): the swath lit. */
const HIGHLIGHT = 0xf46a1a;

export interface SwathLayerContext {
  /** the shared clipping planes of the scene (the section window) */
  clip: THREE.Plane[];
  /** the size of the view in px (the `resolution` of every LineMaterial) */
  resolution: THREE.Vector2;
  /** the background of the scene: the plate under the plot, light or dark ink */
  background?: string;
  /** the texts of the plot (tests give one without a canvas); by default the layer's own */
  labels?: Pick<LabelCache, "material" | "dispose">;
}

/** t, h pairs as x, y, 0. */
function flat(pairs: Float32Array): Float32Array {
  const out = new Float32Array((pairs.length / 2) * 3);
  for (let i = 0, j = 0; i < pairs.length; i += 2, j += 3) {
    out[j] = pairs[i];
    out[j + 1] = pairs[i + 1];
  }
  return out;
}

function quads(rects: number[][]): THREE.BufferGeometry {
  const position = new Float32Array(rects.length * 18);
  rects.forEach(([x0, x1, y0, y1], i) => position.set([x0, y0, 0, x1, y0, 0, x1, y1, 0, x0, y0, 0, x1, y1, 0, x0, y1, 0], i * 18));
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(position, 3));
  return g;
}

/** A small round dot for the points of the plot. */
function dotTexture(): THREE.Texture | null {
  if (typeof document === "undefined") return null;
  const c = document.createElement("canvas");
  c.width = c.height = 32;
  const ctx = c.getContext("2d");
  if (!ctx) return null;
  ctx.fillStyle = "#ffffff";
  ctx.beginPath();
  ctx.arc(16, 16, 14, 0, Math.PI * 2);
  ctx.fill();
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

export class SwathLayer implements SceneLayer {
  readonly object = new THREE.Group();
  readonly origin: { x: number; y: number; z: number };
  readonly bbox: number[];
  /** the plot: its matrix is set before every frame */
  private readonly panel = new THREE.Group();
  private readonly texts: { sprite: THREE.Sprite; label: ChartLabel; wpx: number; hpx: number }[] = [];
  private readonly cache: Pick<LabelCache, "material" | "dispose">;
  private readonly dot = dotTexture();
  private lit: THREE.Object3D[] = [];
  private litIndex: number | null = null;
  private readonly materials: LineMaterial[] = [];
  private readonly u: THREE.Vector3;

  constructor(
    readonly build: SwathBuild,
    private readonly ctx: SwathLayerContext,
  ) {
    const b = build;
    this.cache = ctx.labels ?? new LabelCache([]);
    this.origin = b.origin;
    this.bbox = [...b.reach];
    this.u = new THREE.Vector3(...b.u);
    this.object.position.set(...b.offset);
    const bg = new THREE.Color(ctx.background ?? "#11161d");
    const hsl = { h: 0, s: 0, l: 0 };
    bg.getHSL(hsl);
    const dark = hsl.l < 0.5;
    const ink = dark ? 0xe6e9ee : 0x1b222b; // Light Stone / Anthracite
    const soft = 0x8a99a8; // Slate

    // the swaths lie on the faces of a model: drawn a little toward the viewer so they do not flicker
    const edges = (positions: Float32Array, color: number, opacity: number, width = 1) => {
      if (!positions.length) return;
      const m = this.lineMaterial(color, width, opacity, ctx.clip);
      m.polygonOffset = true;
      m.polygonOffsetFactor = -2;
      m.polygonOffsetUnits = -2;
      const lines = new LineSegments2(new LineSegmentsGeometry().setPositions(positions), m);
      lines.renderOrder = 10;
      this.object.add(lines);
    };
    edges(b.dim, soft, 0.3);
    edges(b.inner, ink, 0.45);
    edges(b.lines, ink, 0.95, 1.6);

    const chart = b.chart;
    if (!chart) return;
    this.panel.matrixAutoUpdate = false;
    this.object.add(this.panel);
    const over = <T extends THREE.Object3D>(o: T, order: number): T => {
      o.renderOrder = order;
      o.frustumCulled = false;
      this.panel.add(o);
      return o;
    };
    const basic = (color: THREE.ColorRepresentation, opacity: number) => new THREE.MeshBasicMaterial({ color, transparent: true, opacity, side: THREE.DoubleSide, depthTest: false, depthWrite: false });
    over(new THREE.Mesh(quads([[chart.t0, chart.t1, 0, 1]]), basic(bg, 0.72)), 30);
    for (const bar of chart.bars) {
      const rects: number[][] = [];
      for (let i = 0; i < bar.rects.length; i += 3) rects.push([bar.rects[i], bar.rects[i + 1], 0, bar.rects[i + 2]]);
      if (rects.length) over(new THREE.Mesh(quads(rects), basic(bar.color, 0.4)), 31);
    }
    const line = (pairs: Float32Array, width: number, color: THREE.ColorRepresentation, order: number) => {
      if (!pairs.length) return;
      const m = this.lineMaterial(new THREE.Color(color).getHex(), width, 1, []);
      m.depthTest = false;
      over(new LineSegments2(new LineSegmentsGeometry().setPositions(flat(pairs)), m), order);
    };
    line(chart.grid, 1, soft, 32);
    for (const s of chart.lines) line(s.segments, 2.6, s.color, 33);
    for (const s of chart.points) {
      if (!s.at.length) continue;
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(flat(s.at), 3));
      over(new THREE.Points(g, new THREE.PointsMaterial({ color: s.color, size: 8, sizeAttenuation: false, map: this.dot, alphaTest: 0.4, transparent: true, depthTest: false })), 34);
    }
    for (const label of chart.labels) {
      const { material, wpx, hpx } = this.cache.material(label.text, { size: label.bold ? 12 : 11, family: "sans-serif", bold: !!label.bold, color: label.color ?? (dark ? "#f3f6fb" : "#1b222b"), halo: dark ? undefined : "rgba(255, 255, 255, 0.92)" });
      const sprite = new THREE.Sprite(material);
      sprite.center.set(label.align === "left" ? 0 : label.align === "right" ? 1.08 : 0.5, 0.5 + label.rows);
      sprite.renderOrder = 36;
      sprite.frustumCulled = false;
      this.object.add(sprite);
      this.texts.push({ sprite, label, wpx, hpx });
    }
  }

  private lineMaterial(color: number, width: number, opacity: number, clip: THREE.Plane[]): LineMaterial {
    const m = new LineMaterial({ color, linewidth: width, worldUnits: false, transparent: opacity < 1, opacity });
    m.uniforms.resolution.value = this.ctx.resolution;
    m.clippingPlanes = clip;
    this.materials.push(m);
    return m;
  }

  /** Lights the swath `index` (null — none) without drawing the rest again. */
  setHighlight(index: number | null): void {
    if (index === this.litIndex) return;
    this.litIndex = index;
    for (const o of this.lit) {
      o.parent?.remove(o);
      this.free(o);
    }
    this.lit = [];
    const s = index === null ? null : slabOf(this.build, index);
    if (!s) return;
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(s.faces, 3));
    const slab = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: HIGHLIGHT, transparent: true, opacity: 0.28, side: THREE.DoubleSide, depthTest: false, depthWrite: false, clippingPlanes: this.ctx.clip }));
    slab.renderOrder = 12;
    const m = this.lineMaterial(HIGHLIGHT, 2.4, 1, this.ctx.clip);
    m.depthTest = false;
    const rim = new LineSegments2(new LineSegmentsGeometry().setPositions(s.rim), m);
    rim.renderOrder = 13;
    this.object.add(slab, rim);
    this.lit.push(slab, rim);
    if (s.strip) {
      const strip = new THREE.Mesh(quads([[s.strip[0], s.strip[1], 0, 1]]), new THREE.MeshBasicMaterial({ color: HIGHLIGHT, transparent: true, opacity: 0.3, side: THREE.DoubleSide, depthTest: false, depthWrite: false }));
      strip.renderOrder = 31;
      strip.frustumCulled = false;
      this.panel.add(strip);
      this.lit.push(strip);
    }
  }

  get highlighted(): number | null {
    return this.litIndex;
  }

  private pose: readonly number[] | null = null;

  /**
   * A drag of a handle (Д-01): the cuboid and its swaths turned by `m` (3 × 3, rows; `drag.rotationBetween`) about
   * the centre — a matrix, no new geometry; the plot over it is hidden until the answer for the new angles comes
   * (it stands for the old ones). Null — as built.
   */
  setPose(m: readonly number[] | null): void {
    this.pose = m;
    if (m) this.object.quaternion.setFromRotationMatrix(new THREE.Matrix4().set(m[0], m[1], m[2], 0, m[3], m[4], m[5], 0, m[6], m[7], m[8], 0, 0, 0, 0, 1));
    else this.object.quaternion.identity();
    this.panel.visible = !m;
    for (const t of this.texts) t.sprite.visible = !m;
  }

  get posed(): readonly number[] | null {
    return this.pose;
  }

  /** Turns the plot toward the viewer and sizes its labels: before every frame. */
  beforeRender(ctx: FrameContext): void {
    if (!this.build.chart) return;
    const basis = cameraBasis(ctx.camera);
    const view: Vec3 = [-basis.z[0], -basis.z[1], -basis.z[2]];
    const { base, rise } = placeChart(this.build, view, basis.x, basis.y, ctx.vexag);
    const b = new THREE.Vector3(...base);
    const r = new THREE.Vector3(...rise);
    const normal = new THREE.Vector3().crossVectors(this.u, r).normalize();
    this.panel.matrix.makeBasis(this.u, r, normal).setPosition(b);
    this.panel.matrixWorldNeedsUpdate = true;
    const world = new THREE.Vector3();
    for (const { sprite, label, wpx, hpx } of this.texts) {
      sprite.position.copy(b).addScaledVector(this.u, label.t).addScaledVector(r, label.h);
      sprite.getWorldPosition(world);
      const px = Math.max(ctx.pixelsPerUnit([world.x, world.y, world.z]), 1e-9);
      sprite.scale.set(wpx / px, hpx / px, 1);
    }
  }

  private free(o: THREE.Object3D): void {
    o.traverse((x) => {
      const mesh = x as THREE.Mesh;
      mesh.geometry?.dispose();
      const mats = Array.isArray(mesh.material) ? mesh.material : mesh.material ? [mesh.material] : [];
      for (const m of mats) {
        m.dispose();
        const i = this.materials.indexOf(m as LineMaterial);
        if (i >= 0) this.materials.splice(i, 1);
      }
    });
  }

  dispose(): void {
    this.free(this.object);
    for (const m of this.materials) m.dispose();
    this.materials.length = 0;
    this.cache.dispose();
    this.dot?.dispose();
  }
}
