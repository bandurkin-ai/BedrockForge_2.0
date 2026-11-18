/**
 * A layer of lines or points in the scene (ТЗ 7.18, ADR 0011 §4; BF `setLines` 1587–1659): one `SceneLayer`
 * per layer of the project, a group per part placed at `part origin − layer origin` (whole metres, float64),
 * and in it the levels of detail that are loaded — one is shown. Lines are `LineSegments2` (width in px) or
 * plain `LineSegments` when the scene is heavy (ADR 0007 §7), points and one-vertex lines are markers drawn
 * by the shader (a disc with a dark rim, no texture), texts are labels placed with everyone else's. Every
 * material takes the shared clipping planes, so only what is in the section window is seen. A part without
 * heights is flat at the «floor» — under the lowest object of the scene. Buffers come from the Worker as
 * they are. The picked line is drawn over in orange.
 */
import * as THREE from "three";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import type { Box3 } from "../core/bounds";
import type { Vec3 } from "../core/vec3";
import { SELECT_COLOR } from "../holes/colors";
import { LABEL_PRIORITY } from "../labels/declutter";
import { DEFAULT_FONT, makeLabel, type LabelCache, type LabelItem } from "../labels/sprites";
import type { Origin } from "../origin";
import type { LayerPick, PickQuery, SceneLayer } from "../sceneTypes";
import { bboxAt, type BuiltLines, type LinesManifest, type LinesPart } from "./build";
import { pickLines, type LineHit, type PickPart } from "./pick";

/** A layer without a colour of its own: Slate of the brand palette. */
export const DEFAULT_LINE_COLOR = "#8a99a8";
export const LINE_WIDTH_PX = 2;
/** Markers: the points of a point layer and the one-vertex lines of a line layer, px. */
export const POINT_SIZE_PX = 5;
export const DOT_SIZE_PX = 8;
/** Texts are not shown from this level on (≥ 32 m in a pixel: they would be a heap). */
export const LABEL_MAX_LOD = 2;
export const MAX_PART_LABELS = 1000;
export const MAX_LAYER_LABELS = 3000;

export interface LineLayerContext {
  clip: THREE.Plane[];
  /** the size of the view, px: shared by every LineMaterial */
  resolution: THREE.Vector2;
  labels: LabelCache;
  pixelRatio: number;
}

/** What a click on the layer gives (`LayerHit.hit` of the scene). */
export interface LinePick extends LineHit {
  layerId: number;
  /** the level of detail the pick was made on */
  lod: number;
  /** the picked place as shown, absolute float64 */
  xyz: Vec3;
}

type LineObject = LineSegments2 | THREE.LineSegments;

function makeLine(positions: Float32Array, color: THREE.ColorRepresentation, width: number, fat: boolean, ctx: LineLayerContext): LineObject {
  if (fat) {
    const geometry = new LineSegmentsGeometry();
    geometry.setPositions(positions); // the buffer of the Worker as it is
    const material = new LineMaterial({ color, linewidth: width, worldUnits: false });
    material.clippingPlanes = ctx.clip;
    material.uniforms.resolution.value = ctx.resolution;
    return new LineSegments2(geometry, material);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.computeBoundingSphere();
  const material = new THREE.LineBasicMaterial({ color });
  material.clippingPlanes = ctx.clip;
  return new THREE.LineSegments(geometry, material);
}

/** Points as discs with a dark rim, drawn by the fragment shader. */
function makeDots(positions: Float32Array, color: THREE.ColorRepresentation, sizePx: number, ctx: LineLayerContext): THREE.Points {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.computeBoundingSphere();
  const material = new THREE.PointsMaterial({ color, size: sizePx * ctx.pixelRatio, sizeAttenuation: false });
  material.clippingPlanes = ctx.clip;
  material.onBeforeCompile = (shader) => {
    shader.fragmentShader = shader.fragmentShader.replace(
      "#include <map_particle_fragment>",
      "vec2 bfDisc = gl_PointCoord - vec2(0.5);\n\tfloat bfR2 = dot(bfDisc, bfDisc);\n\tif (bfR2 > 0.25) discard;\n\tif (bfR2 > 0.14) diffuseColor.rgb *= 0.3;\n\t#include <map_particle_fragment>",
    );
  };
  material.customProgramCacheKey = () => "bf-disc";
  return new THREE.Points(geometry, material);
}

function disposeObject(o: THREE.Object3D | null): void {
  if (!o) return;
  const m = o as THREE.Mesh;
  m.geometry?.dispose();
  (m.material as THREE.Material | undefined)?.dispose();
}

interface Level {
  built: BuiltLines;
  group: THREE.Group;
  line: LineObject | null;
  dots: THREE.Points | null;
  labels: LabelItem[];
  bytes: number;
}

interface PartView {
  part: LinesPart;
  group: THREE.Group;
  levels: Map<number, Level>;
  shown: number | null;
  /** no vertex of the part has a height: it stands at the floor */
  flat: boolean;
}

export class LineLayerView implements SceneLayer {
  readonly object = new THREE.Group();
  readonly origin: Origin;
  /** absolute extent; empty heights at the floor */
  readonly bbox = new Float64Array(6);
  private readonly parts: PartView[];
  private color: string;
  private fat = true;
  private floor: number;
  private selected: number | null = null;
  private highlight: THREE.Object3D[] = [];
  private stale = false;

  constructor(
    readonly manifest: LinesManifest,
    private readonly ctx: LineLayerContext,
    style: { color: string | null; visible: boolean },
    floor: number,
  ) {
    const first = manifest.parts[0]?.origin ?? [0, 0, 0];
    this.origin = { x: first[0], y: first[1], z: first[2] };
    this.color = style.color ?? DEFAULT_LINE_COLOR;
    this.object.visible = style.visible;
    this.floor = floor;
    this.parts = manifest.parts.map((part) => {
      const group = new THREE.Group();
      this.object.add(group);
      return { part, group, levels: new Map(), shown: null, flat: !part.bbox || part.bbox[2] === null || part.bbox[2] === undefined };
    });
    this.place();
  }

  get layerId(): number {
    return this.manifest.layerId;
  }

  get visible(): boolean {
    return this.object.visible;
  }

  /** The layer has heights of its own (it counts as an object others may lie under). */
  get zmin(): number | null {
    return this.manifest.bbox?.[2] ?? null;
  }

  /** Segments and markers of the levels now shown, and how many shown parts are simplified. */
  stats(): { segments: number; points: number; parts: number; simplified: number } {
    const out = { segments: 0, points: 0, parts: 0, simplified: 0 };
    for (const p of this.parts) {
      const lv = p.shown === null ? undefined : p.levels.get(p.shown);
      if (!lv) continue;
      out.parts++;
      out.segments += lv.built.segments;
      out.points += lv.built.dots.length / 3;
      if (lv.built.simplified) out.simplified++;
    }
    return out;
  }

  /** The level for what has no height: parts and the extent follow (the scene is told by the caller). */
  setFloor(z: number): boolean {
    if (z === this.floor) return false;
    this.floor = z;
    this.place();
    return this.parts.some((p) => p.flat);
  }

  /** Absolute extent of part k as numbers (an empty height is the floor). */
  partBbox(k: number): number[] {
    const p = this.parts[k].part;
    return bboxAt(p.bbox, this.floor, p.origin);
  }

  private place(): void {
    const o = this.origin;
    let z0 = Infinity;
    let z1 = -Infinity;
    for (const p of this.parts) {
      const [x, y, z] = p.part.origin;
      p.group.position.set(x - o.x, y - o.y, (p.flat ? this.floor : z) - o.z);
      const b = this.partBbox(p.part.k);
      z0 = Math.min(z0, b[2]);
      z1 = Math.max(z1, b[5]);
    }
    const b = bboxAt(this.manifest.bbox, this.floor, [o.x, o.y, o.z]);
    this.bbox.set([b[0], b[1], Math.min(b[2], z0), b[3], b[4], Math.max(b[5], z1)]);
  }

  // ---------------------------------------------------------------- levels of the parts

  levels(k: number): number[] {
    return [...(this.parts[k]?.levels.keys() ?? [])];
  }

  shown(k: number): number | null {
    return this.parts[k]?.shown ?? null;
  }

  /** A level of part k built by the Worker (a newer build replaces the older); gives its bytes on the video card. */
  setLevel(k: number, built: BuiltLines): number {
    const p = this.parts[k];
    if (!p) return 0;
    this.dropLevel(k, built.lod);
    const group = new THREE.Group();
    group.visible = false;
    const lv: Level = { built, group, line: null, dots: null, labels: [], bytes: built.positions.byteLength + built.dots.byteLength };
    if (built.segments) group.add((lv.line = makeLine(built.positions, this.color, LINE_WIDTH_PX, this.fat, this.ctx)));
    if (built.dots.length) group.add((lv.dots = makeDots(built.dots, this.color, built.pointsOnly ? POINT_SIZE_PX : DOT_SIZE_PX, this.ctx)));
    if (built.labels && built.lod < LABEL_MAX_LOD) {
      const at = new THREE.Vector3();
      const shift = built.pointsOnly ? POINT_SIZE_PX / 2 + 3 : 2;
      const n = Math.min(built.labels.text.length, MAX_PART_LABELS);
      for (let i = 0; i < n; i++) {
        at.fromArray(built.labels.xyz, i * 3);
        const item = makeLabel(this.ctx.labels, built.labels.text[i], DEFAULT_FONT, at, shift, LABEL_PRIORITY.tick);
        item.sprite.visible = false;
        group.add(item.sprite);
        lv.labels.push(item);
      }
    }
    p.levels.set(built.lod, lv);
    p.group.add(group);
    return lv.bytes;
  }

  dropLevel(k: number, lod: number): void {
    const p = this.parts[k];
    const lv = p?.levels.get(lod);
    if (!p || !lv) return;
    p.group.remove(lv.group);
    disposeObject(lv.line);
    disposeObject(lv.dots);
    lv.group.clear(); // label materials are shared and stay in the cache
    p.levels.delete(lod);
    if (p.shown === lod) {
      p.shown = null;
      this.stale = true;
    }
  }

  /** Shows one loaded level of part k (null — none). True when what is seen changed. */
  show(k: number, lod: number | null): boolean {
    const p = this.parts[k];
    if (!p) return false;
    const next = lod !== null && p.levels.has(lod) ? lod : null;
    if (next === p.shown && (next === null || p.levels.get(next)!.group.visible)) return false;
    for (const [l, lv] of p.levels) lv.group.visible = l === next;
    p.shown = next;
    this.stale = true;
    return true;
  }

  // ---------------------------------------------------------------- style

  setColor(color: string | null): void {
    const next = color ?? DEFAULT_LINE_COLOR;
    if (next === this.color) return;
    this.color = next;
    for (const p of this.parts) {
      for (const lv of p.levels.values()) {
        (lv.line?.material as THREE.LineBasicMaterial | undefined)?.color.set(next);
        (lv.dots?.material as THREE.PointsMaterial | undefined)?.color.set(next);
      }
    }
  }

  setVisible(visible: boolean): void {
    this.object.visible = visible;
  }

  /** Lines with a width in px, or one pixel wide when the scene holds too many segments. */
  setFat(fat: boolean): void {
    if (fat === this.fat) return;
    this.fat = fat;
    for (const p of this.parts) {
      for (const lv of p.levels.values()) {
        if (!lv.line) continue;
        lv.group.remove(lv.line);
        disposeObject(lv.line);
        lv.group.add((lv.line = makeLine(lv.built.positions, this.color, LINE_WIDTH_PX, fat, this.ctx)));
      }
    }
  }

  // ---------------------------------------------------------------- SceneLayer

  labels(): readonly LabelItem[] {
    const out: LabelItem[] = [];
    for (const p of this.parts) {
      const lv = p.shown === null ? undefined : p.levels.get(p.shown);
      if (!lv) continue;
      for (const item of lv.labels) {
        if (out.length >= MAX_LAYER_LABELS) return out;
        out.push(item);
      }
    }
    return out;
  }

  beforeRender(): void {
    if (this.stale) this.drawSelection();
  }

  pick(q: PickQuery): LayerPick | null {
    const parts: (PickPart & { view: PartView; lod: number })[] = [];
    for (const p of this.parts) {
      const lv = p.shown === null ? undefined : p.levels.get(p.shown);
      if (!lv) continue;
      const g = p.group.position;
      parts.push({ k: p.part.k, offset: [q.offset[0] + g.x, q.offset[1] + g.y, q.offset[2] + g.z], box: this.localBox(p), built: lv.built, view: p, lod: lv.built.lod });
    }
    const hit = pickLines(parts, q);
    if (!hit) return null;
    const src = parts.find((p) => p.k === hit.k)!;
    const g = src.view.group.position;
    const xyz: Vec3 = [this.origin.x + g.x + hit.at[0], this.origin.y + g.y + hit.at[1], this.origin.z + g.z + hit.at[2]];
    const pick: LinePick = { ...hit, layerId: this.layerId, lod: src.lod, xyz };
    return { score: hit.score, hit: pick };
  }

  /** The extent of a part in its own coordinates (a flat part: Z 0). */
  private localBox(p: PartView): Box3 | null {
    const b = p.part.bbox;
    if (!b || b[0] === null || b[1] === null || b[3] === null || b[4] === null) return null;
    const [ox, oy, oz] = p.part.origin;
    const z0 = b[2] === null || b[2] === undefined ? 0 : b[2] - oz;
    const z1 = b[5] === null || b[5] === undefined ? 0 : b[5] - oz;
    return { min: [b[0] - ox, b[1] - oy, z0], max: [b[3] - ox, b[4] - oy, z1] };
  }

  // ---------------------------------------------------------------- the picked line

  /** The line (point) of the layer to draw over in orange; null — none. */
  select(line: number | null): void {
    if (line === this.selected) return;
    this.selected = line;
    this.stale = true;
  }

  private clearSelection(): void {
    for (const o of this.highlight) {
      o.parent?.remove(o);
      disposeObject(o);
    }
    this.highlight = [];
  }

  /** Every polyline of the selected line in the shown levels (a line may be cut over several parts). */
  private drawSelection(): void {
    this.stale = false;
    this.clearSelection();
    const sel = this.selected;
    if (sel === null) return;
    for (const p of this.parts) {
      const b = p.shown === null ? undefined : p.levels.get(p.shown)?.built;
      if (!b) continue;
      const runs: Float32Array[] = [];
      for (let piece = 0; piece < b.n; piece++) if (b.line[piece] === sel && b.segStart[piece + 1] > b.segStart[piece]) runs.push(b.positions.subarray(b.segStart[piece] * 6, b.segStart[piece + 1] * 6));
      if (runs.length) {
        const positions = new Float32Array(runs.reduce((s, r) => s + r.length, 0));
        let at = 0;
        for (const r of runs) {
          positions.set(r, at);
          at += r.length;
        }
        this.addHighlight(p, makeLine(positions, SELECT_COLOR, LINE_WIDTH_PX + 3, true, this.ctx));
      }
      const dots: number[] = [];
      const count = b.dots.length / 3;
      for (let d = 0; d < count; d++) if (b.line[b.dotPiece ? b.dotPiece[d] : d] === sel) dots.push(b.dots[d * 3], b.dots[d * 3 + 1], b.dots[d * 3 + 2]);
      if (dots.length) {
        const o = makeDots(new Float32Array(dots), SELECT_COLOR, (b.pointsOnly ? POINT_SIZE_PX : DOT_SIZE_PX) + 6, this.ctx);
        (o.material as THREE.PointsMaterial).depthTest = false;
        this.addHighlight(p, o);
      }
    }
  }

  private addHighlight(p: PartView, o: THREE.Object3D): void {
    o.renderOrder = 7;
    p.group.add(o);
    this.highlight.push(o);
  }

  dispose(): void {
    this.clearSelection();
    for (const p of this.parts) for (const lod of [...p.levels.keys()]) this.dropLevel(p.part.k, lod);
    this.object.clear();
  }
}
