/**
 * A surface in the scene — a wireframe or a digital terrain model (ТЗ 7.20, ADR 0011 §4, ADR 0004 §3; BF
 * `addWireframe`, `setWireframeLevel`, `drapeMaterial`, `applyDrape` 1350–1560): one `SceneLayer` per
 * wireframe, a group per part placed at `part origin − layer origin` (whole metres, float64), and in it the
 * levels of detail that are loaded — one is shown. Buffers come from the Worker as they are (float32 from the
 * origin of the part, u2 / u4 indices); there are no normals — the surface is shaded flat, so a coarser level
 * needs nothing more. Every material takes the shared clipping planes: only what is in the section window is
 * seen. The colour is the wireframe's, the opacity — «Непрозрачность каркасов» of the view.
 *
 * A DTM may carry a raster (`drape.ts`): the image coordinates are computed in the vertex shader from the
 * plan position of the vertex, so the image lies the same on every level of detail. Every part has a
 * material of its own — the top-left corner of the image is given from the origin of that part (float64 on
 * the CPU) — and all of them share one program. The tint multiplies the image, the opacity of the drape
 * covers the colour of the surface, and without the shadows of the relief the image keeps its colours.
 * The texture belongs to the layer of the raster. A click is answered on the CPU (`build.ts::pickMesh`) by the
 * level that is drawn; the exact triangle is then the core's (`exact.ts`).
 *
 * A level the core has not made yet comes as a stand-in — the whole part (ADR 0015 §8): it is drawn like any
 * level, the layer counts the parts shown so («готовятся копии для показа»), and the real level replaces it.
 */
import * as THREE from "three";
import type { Vec3 } from "../core/vec3";
import { SELECT_COLOR } from "../holes/colors";
import type { Origin } from "../origin";
import type { LayerPick, PickQuery, SceneLayer } from "../sceneTypes";
import { pickMesh, sampleAt, type BuiltMesh, type MeshManifest, type MeshPartInfo, type MeshPickPart } from "./build";
import { DRAPE_PROGRAM_KEY, drapeUv, drapeUvFloat32, imageAxes, patchDrapeShader, type Corner, type DrapeAxes } from "./drape";
import { absoluteRay, type AbsRay } from "./exact";

/** A wireframe without a colour of its own: Slate of the brand palette. */
export const DEFAULT_MESH_COLOR = "#8a99a8";
/** Scores of surfaces start here, as of rasters: any line or point under the cursor (0…1) wins. */
export const SURFACE_SCORE = 2;
/** How much of the selection colour a picked surface glows with: seen, but the image on a DTM stays readable. */
const SELECT_GLOW = 0.2;

const HEX = /^#[0-9a-f]{6}$/i;

export interface MeshLayerContext {
  clip: THREE.Plane[];
}

export interface MeshStyle {
  /** "#rrggbb"; null — no colour of its own */
  color: string | null;
  /** 0.1…1 */
  opacity: number;
  visible: boolean;
}

/** The raster laid on the surface (ТЗ 7.20). */
export interface MeshDrape {
  /** corners of the image, absolute float64: top-left, top-right, bottom-right, bottom-left */
  corners: ArrayLike<Corner>;
  /** "#rrggbb": the image is multiplied by it (white — as it is) */
  tint: string;
  /** 0…1: how much of the image covers the colour of the surface */
  opacity: number;
  /** light and shadow of the relief on the image */
  shade: boolean;
}

/** What a click on a surface gives (`LayerHit.hit` of the scene). */
export interface MeshPick {
  wireframeId: number;
  k: number;
  /** the level of detail the pick was made on (the exact pick is the core's: `exact.ts`) */
  lod: number;
  triangle: number;
  /** the picked place as shown, absolute float64 */
  xyz: Vec3;
  /** the ray of the click in project coordinates: what the exact pick of the core is asked with (`exact.ts`) */
  ray: AbsRay;
}

/** The surface over a plan point as it is drawn now (the E2E probe of the drape). */
export interface MeshProbe {
  k: number;
  lod: number;
  /** triangles of the level that answered */
  triangles: number;
  /** absolute height of the level there */
  z: number;
  /** image coordinates as the shader gives them (float32), null — no raster on the surface */
  uv: [number, number] | null;
  /** the same from the float64 affine of the raster */
  exact: [number, number] | null;
}

interface Level {
  built: BuiltMesh;
  /** null — the level has no triangle to draw */
  mesh: THREE.Mesh<THREE.BufferGeometry, THREE.MeshLambertMaterial> | null;
  bytes: number;
}

interface PartView {
  part: MeshPartInfo;
  group: THREE.Group;
  material: THREE.MeshLambertMaterial;
  /** the top-left corner of the image from the origin of this part (the uniform `uDrapeO`) */
  o: THREE.Vector2;
  axes: DrapeAxes | null;
  levels: Map<number, Level>;
  shown: number | null;
}

export class MeshLayerView implements SceneLayer {
  readonly object = new THREE.Group();
  readonly origin: Origin;
  readonly bbox = new Float64Array(6);
  private readonly parts: PartView[];
  /** shared by the materials of every part; `uDrapeO` is the part's own */
  private readonly uniforms = {
    uDrapeMap: { value: null as THREE.Texture | null },
    uDrapeU: { value: new THREE.Vector2() },
    uDrapeV: { value: new THREE.Vector2() },
    uDrapeTint: { value: new THREE.Color(0xffffff) },
    uDrapeMix: { value: 0 },
    uDrapeShade: { value: 1 },
  };
  private style: MeshStyle;
  private drape: MeshDrape | null = null;
  private selected = false;

  constructor(
    readonly manifest: MeshManifest,
    private readonly ctx: MeshLayerContext,
    style: MeshStyle,
  ) {
    const first = manifest.parts[0]?.origin ?? [0, 0, 0];
    this.origin = { x: first[0], y: first[1], z: first[2] };
    this.style = style;
    this.object.visible = style.visible;
    const box = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
    this.parts = manifest.parts.map((part) => {
      const group = new THREE.Group();
      group.position.set(part.origin[0] - first[0], part.origin[1] - first[1], part.origin[2] - first[2]);
      this.object.add(group);
      for (let i = 0; i < 3; i++) {
        box[i] = Math.min(box[i], part.bbox[i]);
        box[i + 3] = Math.max(box[i + 3], part.bbox[i + 3]);
      }
      const o = new THREE.Vector2();
      return { part, group, material: this.makeMaterial(o), o, axes: null, levels: new Map(), shown: null };
    });
    this.bbox.set(manifest.bbox ?? (manifest.parts.length ? box : [first[0], first[1], first[2], first[0], first[1], first[2]]));
    this.applyStyle();
  }

  get wireframeId(): number {
    return this.manifest.wireframeId;
  }

  get visible(): boolean {
    return this.object.visible;
  }

  /** Is the image seen on the surface now: a raster is set, its texture came and its opacity is not 0. */
  get draped(): boolean {
    return this.uniforms.uDrapeMix.value > 0;
  }

  private makeMaterial(o: THREE.Vector2): THREE.MeshLambertMaterial {
    // flat shading takes the normal from the derivatives of the position: no normal buffer (ADR 0004 §3)
    const m = new THREE.MeshLambertMaterial({ side: THREE.DoubleSide, flatShading: true });
    m.clippingPlanes = this.ctx.clip;
    // lines and points lying on the surface (the contours it was built from) stay in front of it
    m.polygonOffset = true;
    m.polygonOffsetFactor = 1;
    m.polygonOffsetUnits = 1;
    const uniforms = { ...this.uniforms, uDrapeO: { value: o } };
    m.onBeforeCompile = (shader) => {
      if (patchDrapeShader(shader)) Object.assign(shader.uniforms, uniforms);
    };
    m.customProgramCacheKey = () => DRAPE_PROGRAM_KEY;
    return m;
  }

  // ---------------------------------------------------------------- levels of the parts

  levels(k: number): number[] {
    return [...(this.parts[k]?.levels.keys() ?? [])];
  }

  shown(k: number): number | null {
    return this.parts[k]?.shown ?? null;
  }

  /** Loaded levels of part k that are stand-ins: the whole part until the core makes the level (ADR 0015 §8). */
  standins(k: number): number[] {
    const out: number[] = [];
    for (const [lod, lv] of this.parts[k]?.levels ?? []) if (lv.built.standin) out.push(lod);
    return out;
  }

  /** Triangles of a loaded level of part k (null — not loaded). */
  levelTriangles(k: number, lod: number): number | null {
    return this.parts[k]?.levels.get(lod)?.built.triangles ?? null;
  }

  /** A level of part k built by the Worker (a newer build replaces the older); gives its bytes on the video card. */
  setLevel(k: number, built: BuiltMesh): number {
    const p = this.parts[k];
    if (!p) return 0;
    this.dropLevel(k, built.lod);
    const lv: Level = { built, mesh: null, bytes: built.positions.byteLength + built.index.byteLength };
    if (built.triangles) {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(built.positions, 3)); // the buffer of the Worker as it is
      geometry.setIndex(new THREE.BufferAttribute(built.index, 1));
      const b = built.box;
      geometry.boundingBox = new THREE.Box3(new THREE.Vector3(b[0], b[1], b[2]), new THREE.Vector3(b[3], b[4], b[5]));
      geometry.boundingSphere = geometry.boundingBox.getBoundingSphere(new THREE.Sphere());
      lv.mesh = new THREE.Mesh(geometry, p.material);
      lv.mesh.visible = false;
      p.group.add(lv.mesh);
    }
    p.levels.set(built.lod, lv);
    return lv.bytes;
  }

  dropLevel(k: number, lod: number): void {
    const p = this.parts[k];
    const lv = p?.levels.get(lod);
    if (!p || !lv) return;
    if (lv.mesh) {
      p.group.remove(lv.mesh);
      lv.mesh.geometry.dispose();
    }
    p.levels.delete(lod);
    if (p.shown === lod) p.shown = null;
  }

  /** Shows one loaded level of part k (null — none). True when what is seen changed. */
  show(k: number, lod: number | null): boolean {
    const p = this.parts[k];
    if (!p) return false;
    const next = lod !== null && p.levels.has(lod) ? lod : null;
    if (next === p.shown) return false;
    for (const [l, lv] of p.levels) if (lv.mesh) lv.mesh.visible = l === next;
    p.shown = next;
    return true;
  }

  /**
   * Parts and triangles of the levels now shown, how many shown parts hold fewer triangles than in full and how
   * many are stand-ins (shown whole until the core makes their level).
   */
  stats(): { parts: number; triangles: number; simplified: number; standins: number } {
    const out = { parts: 0, triangles: 0, simplified: 0, standins: 0 };
    for (const p of this.parts) {
      const lv = p.shown === null ? undefined : p.levels.get(p.shown);
      if (!lv) continue;
      out.parts++;
      out.triangles += lv.built.triangles;
      if (lv.built.simplified) out.simplified++;
      if (lv.built.standin) out.standins++;
    }
    return out;
  }

  // ---------------------------------------------------------------- style and drape

  setStyle(style: MeshStyle): void {
    this.style = style;
    this.object.visible = style.visible;
    this.applyStyle();
  }

  setVisible(visible: boolean): void {
    this.style = { ...this.style, visible };
    this.object.visible = visible;
  }

  /** The picked surface glows with the selection colour. */
  select(on: boolean): void {
    if (on === this.selected) return;
    this.selected = on;
    this.applyStyle();
  }

  private applyStyle(): void {
    const color = this.style.color && HEX.test(this.style.color) ? this.style.color : DEFAULT_MESH_COLOR;
    const opacity = Math.min(1, Math.max(0.1, Number.isFinite(this.style.opacity) ? this.style.opacity : 1));
    const blend = opacity < 1;
    for (const { material: m } of this.parts) {
      m.color.set(color);
      m.emissive.set(this.selected ? SELECT_COLOR : "#000000");
      if (this.selected) m.emissive.multiplyScalar(SELECT_GLOW);
      m.opacity = opacity;
      if (m.transparent !== blend) {
        m.transparent = blend;
        m.depthWrite = !blend;
        m.needsUpdate = true;
      }
    }
  }

  /** The raster on the surface, or none: the image coordinates of every part from its own origin. */
  setDrape(drape: MeshDrape | null): void {
    const u = this.uniforms;
    const axes = drape ? imageAxes(drape.corners, [0, 0]) : null;
    this.drape = drape && axes ? drape : null;
    for (const p of this.parts) {
      p.axes = this.drape ? imageAxes(this.drape.corners, p.part.origin) : null;
      if (p.axes) p.o.set(p.axes.o[0], p.axes.o[1]);
    }
    if (this.drape && axes) {
      u.uDrapeU.value.set(axes.u[0], axes.u[1]);
      u.uDrapeV.value.set(axes.v[0], axes.v[1]);
      u.uDrapeTint.value.set(HEX.test(this.drape.tint) ? this.drape.tint : "#ffffff");
      u.uDrapeShade.value = this.drape.shade ? 1 : 0;
    }
    this.applyMix();
  }

  /** The image of the draped raster (the texture is the raster layer's and is not disposed here). True when it changed. */
  setDrapeTexture(texture: THREE.Texture | null): boolean {
    if (this.uniforms.uDrapeMap.value === texture) return false;
    this.uniforms.uDrapeMap.value = texture;
    this.applyMix();
    return true;
  }

  /** The image appears when it is loaded; until then the surface keeps its own colour. */
  private applyMix(): void {
    const d = this.drape;
    const opacity = d && Number.isFinite(d.opacity) ? Math.min(1, Math.max(0, d.opacity)) : 0;
    this.uniforms.uDrapeMix.value = d && this.uniforms.uDrapeMap.value ? opacity : 0;
  }

  // ---------------------------------------------------------------- SceneLayer

  pick(q: PickQuery): LayerPick | null {
    const parts: (MeshPickPart & { lod: number })[] = [];
    for (const p of this.parts) {
      const lv = p.shown === null ? undefined : p.levels.get(p.shown);
      if (!lv?.mesh) continue;
      const g = p.group.position;
      const b = lv.built;
      parts.push({ k: p.part.k, offset: [q.offset[0] + g.x, q.offset[1] + g.y, q.offset[2] + g.z], box: b.box, positions: b.positions, index: b.index, triangles: b.triangles, lod: b.lod });
    }
    const hit = pickMesh(parts, q.ray, q.vexag, q.inWindow);
    if (!hit) return null;
    const o = this.origin;
    const xyz: Vec3 = [hit.point[0] - q.offset[0] + o.x, hit.point[1] - q.offset[1] + o.y, hit.point[2] / (q.vexag || 1) - q.offset[2] + o.z];
    const pick: MeshPick = { wireframeId: this.wireframeId, k: hit.k, lod: parts.find((p) => p.k === hit.k)!.lod, triangle: hit.triangle, xyz, ray: absoluteRay(q.ray, q.offset, o, q.vexag) };
    return { score: SURFACE_SCORE + hit.t / (1 + hit.t), hit: pick };
  }

  /** The surface over an absolute plan point as the shown levels draw it: the highest answer of the parts. */
  probe(x: number, y: number): MeshProbe | null {
    let best: MeshProbe | null = null;
    for (const p of this.parts) {
      const lv = p.shown === null ? undefined : p.levels.get(p.shown);
      const [ox, oy, oz] = p.part.origin;
      const bb = p.part.bbox;
      if (!lv?.mesh || x < bb[0] || x > bb[3] || y < bb[1] || y > bb[4]) continue;
      const s = sampleAt(lv.built.positions, lv.built.index, lv.built.triangles, x - ox, y - oy);
      if (!s || (best && best.z >= s.z + oz)) continue;
      best = { k: p.part.k, lod: lv.built.lod, triangles: lv.built.triangles, z: s.z + oz, uv: p.axes && this.draped ? drapeUvFloat32(p.axes, s.x, s.y) : null, exact: p.axes ? drapeUv(p.axes, x - ox, y - oy) : null };
    }
    return best;
  }

  dispose(): void {
    for (const p of this.parts) {
      for (const lod of [...p.levels.keys()]) this.dropLevel(p.part.k, lod);
      p.material.dispose();
    }
    this.object.clear();
  }
}
