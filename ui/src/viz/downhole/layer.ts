/**
 * The displays along holes in 3D (ADR 0007 §7; BF `scene3d.ts` setDownhole 756–871, sideMaterial 873,
 * updateDownholes 930–1006): one `DownholeBuild` of a part becomes colour tubes along the holes
 * (`LineSegments2`, width in px), panels beside them (bars, ribbons — a mesh) and graph lines, points,
 * event markers and text. A panel vertex is a point of the hole plus `aAmp` metres to the side; the shader
 * finds the side from the camera — across the hole, in the plane of the screen — so panels face the viewer
 * in 3D and in sections alike. Every material takes the shared clipping planes; text joins the common
 * placement of labels (shown once its interval is 18 px long on the screen).
 */
import * as THREE from "three";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import type { DownholeBuild, Label, SideMesh } from "./geometry";
import { cameraBasis } from "../core/views";
import { LABEL_PRIORITY } from "../labels/declutter";
import { DEFAULT_FONT, type LabelCache, type LabelItem } from "../labels/sprites";
import type { MarkerTextures } from "../holes/markers";
import { srgbToLinear } from "../holes/colors";
import type { FrameContext } from "../sceneTypes";

/** A label shows when its interval is at least this long on the screen, px. */
export const LABEL_MIN_PX = 18;
/** Panels are in metres, but the default column is never narrower than this on the screen, px. */
export const PANEL_MIN_PX = 26;

/** The side direction of the camera, shared by every panel material of the scene (updated each frame). */
export interface SideUniforms {
  view: { value: THREE.Vector3 };
  right: { value: THREE.Vector3 };
  vexag: { value: number };
}

export function sideUniforms(): SideUniforms {
  return { view: { value: new THREE.Vector3(0, 0, -1) }, right: { value: new THREE.Vector3(1, 0, 0) }, vexag: { value: 1 } };
}

/** The camera axes of this frame into the shared uniforms (world space = rendered space). */
export function updateSide(side: SideUniforms, ctx: FrameContext): void {
  const b = cameraBasis(ctx.camera);
  side.view.value.set(-b.z[0], -b.z[1], -b.z[2]);
  side.right.value.set(b.x[0], b.x[1], b.x[2]);
  side.vexag.value = ctx.vexag || 1;
}

/** A material whose vertices move `aAmp` metres to the side of the hole, across it and in the plane of the screen. */
function sideMaterial<T extends THREE.Material>(material: T, side: SideUniforms, scale: { value: number }): T {
  material.onBeforeCompile = (shader) => {
    shader.uniforms.uView = side.view;
    shader.uniforms.uRight = side.right;
    shader.uniforms.uVexag = side.vexag;
    shader.uniforms.uScale = scale;
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nattribute vec3 aTan;\nattribute float aAmp;\nuniform vec3 uView;\nuniform vec3 uRight;\nuniform float uVexag;\nuniform float uScale;")
      .replace(
        "#include <begin_vertex>",
        `#include <begin_vertex>
        vec3 tw = normalize(vec3(aTan.xy, aTan.z * uVexag));
        vec3 sw = cross(tw, uView);
        float sl = length(sw);
        sw = sl < 0.05 ? uRight : sw / sl;
        if (dot(sw, uRight) < 0.0) sw = -sw;
        transformed += vec3(sw.xy, sw.z / uVexag) * aAmp * uScale;`,
      );
  };
  material.customProgramCacheKey = () => "downhole-side";
  return material;
}

/** sRGB colours of the geometry (0…1) into the linear working space of three.js, alpha kept. */
function linear(colors: number[], stride: 3 | 4): Float32Array {
  const out = new Float32Array(colors.length);
  for (let i = 0; i < colors.length; i++) out[i] = stride === 4 && i % 4 === 3 ? colors[i] : srgbToLinear(colors[i]);
  return out;
}

function sideGeometry(m: SideMesh): THREE.BufferGeometry {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(m.positions), 3));
  g.setAttribute("aTan", new THREE.BufferAttribute(new Float32Array(m.tangents), 3));
  g.setAttribute("aAmp", new THREE.BufferAttribute(new Float32Array(m.amps), 1));
  g.setAttribute("color", new THREE.BufferAttribute(linear(m.colors, 4), 4));
  if (m.index.length) g.setIndex(m.positions.length / 3 > 65535 ? new THREE.BufferAttribute(new Uint32Array(m.index), 1) : new THREE.BufferAttribute(new Uint16Array(m.index), 1));
  return g;
}

export interface DownholeMeshContext {
  clip: THREE.Plane[];
  resolution: THREE.Vector2;
  side: SideUniforms;
  labels: LabelCache;
  markers: MarkerTextures;
  pixelRatio: number;
}

interface PlacedLabel {
  item: LabelItem;
  label: Label;
}

/** The three.js objects of one build (a part of a display), coordinates from the part origin. */
export class DownholeMesh {
  readonly object = new THREE.Group();
  /** keeps the default column wide enough on the screen: shared by the parts of a display */
  readonly scale: { value: number };
  readonly bytes: number;
  readonly triangles: number;
  private readonly placed: PlacedLabel[] = [];
  private shown: LabelItem[] = [];

  constructor(
    readonly build: DownholeBuild,
    ctx: DownholeMeshContext,
    scale: { value: number },
  ) {
    this.scale = scale;
    let bytes = 0;
    const add = (o: THREE.Object3D, order: number) => {
      o.renderOrder = order;
      o.frustumCulled = false;
      this.object.add(o);
    };
    build.tubes.forEach((t, i) => {
      if (!t.positions.length) return;
      const g = new LineSegmentsGeometry();
      g.setPositions(new Float32Array(t.positions));
      g.setColors(linear(t.colors, 3));
      const m = new LineMaterial({ color: 0xffffff, linewidth: t.width, vertexColors: true, worldUnits: false, transparent: t.opacity < 1, opacity: t.opacity });
      m.clippingPlanes = ctx.clip;
      m.uniforms.resolution.value = ctx.resolution;
      bytes += t.positions.length * 4 * 2;
      add(new LineSegments2(g, m), 2 + i * 0.1);
    });
    let triangles = 0;
    if (build.panels.amps.length) {
      const g = sideGeometry(build.panels);
      const m = sideMaterial(new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide, transparent: true, depthWrite: false }), ctx.side, scale);
      m.clippingPlanes = ctx.clip;
      triangles = build.panels.index.length / 3;
      bytes += build.panels.amps.length * 4 * 11 + build.panels.index.length * 2;
      add(new THREE.Mesh(g, m), 1);
    }
    if (build.lines.amps.length) {
      const m = sideMaterial(new THREE.LineBasicMaterial({ vertexColors: true, transparent: true }), ctx.side, scale);
      m.clippingPlanes = ctx.clip;
      bytes += build.lines.amps.length * 4 * 11;
      add(new THREE.LineSegments(sideGeometry(build.lines), m), 2);
    }
    for (const p of build.points) {
      if (!p.amps.length) continue;
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(p.positions), 3));
      g.setAttribute("aTan", new THREE.BufferAttribute(new Float32Array(p.tangents), 3));
      g.setAttribute("aAmp", new THREE.BufferAttribute(new Float32Array(p.amps), 1));
      g.setAttribute("color", new THREE.BufferAttribute(linear(p.colors, 3), 3));
      const m = sideMaterial(new THREE.PointsMaterial({ size: p.size * ctx.pixelRatio, sizeAttenuation: false, vertexColors: true, map: ctx.markers.get("circle"), transparent: true, alphaTest: 0.3, opacity: p.opacity }), ctx.side, scale);
      m.clippingPlanes = ctx.clip;
      bytes += p.amps.length * 4 * 10;
      add(new THREE.Points(g, m), 3);
    }
    for (const mk of build.markers) {
      if (!mk.positions.length) continue;
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(mk.positions), 3));
      g.setAttribute("color", new THREE.BufferAttribute(linear(mk.colors, 3), 3));
      const m = new THREE.PointsMaterial({ size: mk.size * ctx.pixelRatio, sizeAttenuation: false, vertexColors: true, map: ctx.markers.get(mk.shape), transparent: true, alphaTest: 0.4, depthTest: false, opacity: mk.opacity });
      m.clippingPlanes = ctx.clip;
      bytes += mk.positions.length * 4 * 2;
      add(new THREE.Points(g, m), 4);
    }
    const labels = new THREE.Group();
    labels.renderOrder = 10;
    for (const label of build.labels) {
      const font = label.size || label.color ? { ...DEFAULT_FONT, size: label.size ?? DEFAULT_FONT.size, color: label.color ?? DEFAULT_FONT.color } : DEFAULT_FONT;
      const { material, wpx, hpx } = ctx.labels.material(label.text, font);
      const sprite = new THREE.Sprite(material);
      sprite.renderOrder = 10;
      sprite.frustumCulled = false;
      sprite.visible = false;
      sprite.position.set(...label.position);
      const shift = label.px ? label.px : label.right ? -wpx : 0;
      labels.add(sprite);
      this.placed.push({ item: { sprite, wpx, hpx, shift, priority: LABEL_PRIORITY.tick, clipped: true }, label });
    }
    this.object.add(labels);
    this.bytes = bytes;
    this.triangles = triangles;
  }

  /**
   * Text of this frame: labels whose interval is long enough on the screen, moved beside the hole on the side
   * of the panels (as the shader moves them). `ppu(at)` — pixels per unit at a rendered-space point.
   */
  frame(ctx: FrameContext, offset: readonly [number, number, number], side: SideUniforms): void {
    this.shown = [];
    if (!this.placed.length) return;
    const k = this.scale.value;
    const vexag = ctx.vexag || 1;
    const view = side.view.value;
    const right = side.right.value;
    const tw = new THREE.Vector3();
    const sw = new THREE.Vector3();
    for (const { item, label } of this.placed) {
      const at: [number, number, number] = [label.position[0] + offset[0], label.position[1] + offset[1], (label.position[2] + offset[2]) * vexag];
      const show = label.length * ctx.pixelsPerUnit(at) >= LABEL_MIN_PX;
      item.sprite.visible = false;
      if (!show) continue;
      tw.set(label.tangent[0], label.tangent[1], label.tangent[2] * vexag).normalize();
      sw.crossVectors(tw, view);
      if (sw.length() < 0.05) sw.copy(right);
      else sw.normalize();
      if (sw.dot(right) < 0) sw.negate();
      item.sprite.position.set(label.position[0] + sw.x * label.amp * k, label.position[1] + sw.y * label.amp * k, label.position[2] + (sw.z * label.amp * k) / vexag);
      this.shown.push(item);
    }
  }

  labels(): readonly LabelItem[] {
    return this.shown;
  }

  dispose(): void {
    this.object.traverse((child) => {
      if (child instanceof THREE.Sprite) return; // label materials are shared and stay in the cache
      const m = child as THREE.Mesh;
      m.geometry?.dispose();
      (m.material as THREE.Material | undefined)?.dispose();
    });
    this.object.clear();
  }
}
