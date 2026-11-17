/**
 * The holes of one set in the scene (ТЗ 7.15, ADR 0007 §1, §7): a group per part placed at
 * `part origin − scene origin` (float64 on the CPU), its collars, labels and traces. Collars come with the
 * manifest (the first frame), traces part by part as the Worker builds them. Visibility follows the hidden
 * keys `set:<id>`, `set:<id>:collars`, `set:<id>:paths`; styles — the set's CollarStyle and TraceStyle.
 */
import * as THREE from "three";
import type { CollarStyle } from "./collarStyle";
import { CollarPoints, MAX_COLLAR_LABELS } from "./collars";
import type { HolesManifest, HolesPart } from "./manifest";
import type { MarkerTextures } from "./markers";
import { TraceLines } from "./paths";
import type { TraceStyle } from "./traceStyle";
import type { BuiltPaths } from "../worker/paths";
import { boxFromBbox6, union, emptyBox, type Box3 } from "../core/bounds";
import type { SceneOrigin } from "../core/sceneOrigin";
import type { Vec3 } from "../core/vec3";
import type { LabelCache, LabelItem } from "../labels/sprites";

export interface HoleSetVisibility {
  set: boolean;
  collars: boolean;
  paths: boolean;
}

export interface HoleSetStyle {
  collar: CollarStyle;
  trace: TraceStyle;
}

/** What the layers of a set share with the scene. */
export interface HoleSetContext {
  clip: THREE.Plane[];
  resolution: THREE.Vector2;
  textures: MarkerTextures;
  labels: LabelCache;
  pixelRatio: number;
}

export interface PartView {
  part: HolesPart;
  group: THREE.Group;
  collars: CollarPoints;
  traces: TraceLines | null;
  /** float64: part origin − scene origin */
  offset: Vec3;
}

export class HoleSetLayer {
  readonly group = new THREE.Group();
  readonly parts: PartView[];
  style: HoleSetStyle;
  visibility: HoleSetVisibility;
  private fat = false;

  constructor(
    readonly manifest: HolesManifest,
    private readonly ctx: HoleSetContext,
    style: HoleSetStyle,
    visibility: HoleSetVisibility,
  ) {
    this.style = style;
    this.visibility = visibility;
    this.parts = manifest.parts.map((part) => {
      const group = new THREE.Group();
      const collars = new CollarPoints(manifest, part, ctx.clip);
      group.add(collars.points, collars.labelGroup);
      this.group.add(group);
      return { part, group, collars, traces: null, offset: [0, 0, 0] as Vec3 };
    });
    this.applyCollars();
    this.applyVisibility();
  }

  get setId(): number {
    return this.manifest.setId;
  }

  get segments(): number {
    return this.parts.reduce((s, p) => s + (p.traces?.segments ?? 0), 0);
  }

  /** Positions of the parts after the scene origin was set or moved. */
  place(origin: SceneOrigin): void {
    for (const p of this.parts) {
      p.offset = origin.offset({ x: p.part.origin[0], y: p.part.origin[1], z: p.part.origin[2] });
      p.group.position.set(p.offset[0], p.offset[1], p.offset[2]);
    }
  }

  /** The traces of part k (a newer build replaces the older). */
  setPaths(k: number, built: BuiltPaths, fat: boolean): void {
    const p = this.parts[k];
    if (!p) return;
    if (p.traces) {
      p.group.remove(p.traces.group);
      p.traces.dispose();
    }
    p.traces = new TraceLines(built, this.ctx.clip, this.ctx.resolution);
    p.traces.apply(this.style.trace, fat);
    this.fat = fat;
    p.traces.group.visible = this.visibility.paths;
    p.group.add(p.traces.group);
  }

  hasPaths(k: number): boolean {
    return this.parts[k]?.traces !== null && this.parts[k]?.traces !== undefined;
  }

  builtPaths(k: number): BuiltPaths | null {
    return this.parts[k]?.traces?.built ?? null;
  }

  setStyle(style: HoleSetStyle, fat: boolean): void {
    const collarChanged = JSON.stringify(style.collar) !== JSON.stringify(this.style.collar);
    this.style = style;
    if (collarChanged) this.applyCollars();
    this.fat = fat;
    for (const p of this.parts) p.traces?.apply(style.trace, fat);
  }

  /** Fat or thin lines for the whole set (the scene crossed the million segments). */
  setFat(fat: boolean): void {
    if (fat === this.fat) return;
    this.fat = fat;
    for (const p of this.parts) p.traces?.apply(this.style.trace, fat);
  }

  setVisibility(v: HoleSetVisibility): void {
    this.visibility = v;
    this.applyVisibility();
  }

  get visible(): boolean {
    return this.visibility.set;
  }

  /** The extent of the set in scene coordinates (parts' boxes from the manifest). */
  extent(origin: { x: number; y: number; z: number }): Box3 {
    let box = emptyBox();
    for (const p of this.parts) box = union(box, boxFromBbox6(p.part.bbox, origin));
    return box;
  }

  /** Labels to place this frame (the visible ones). */
  labelItems(out: LabelItem[]): void {
    if (!this.visibility.set || !this.visibility.collars) return;
    for (const p of this.parts) for (const l of p.collars.labels) out.push(l);
  }

  beforeRender(ppu: number): void {
    for (const p of this.parts) p.traces?.setDashScale(ppu);
  }

  dispose(): void {
    for (const p of this.parts) {
      p.collars.dispose();
      p.traces?.dispose();
    }
    this.group.clear();
  }

  private applyCollars(): void {
    let budget = MAX_COLLAR_LABELS;
    for (const p of this.parts) budget -= p.collars.apply(this.style.collar, this.ctx.textures, this.ctx.pixelRatio, this.ctx.labels, budget);
  }

  private applyVisibility(): void {
    this.group.visible = this.visibility.set;
    for (const p of this.parts) {
      p.collars.points.visible = this.visibility.collars;
      p.collars.labelGroup.visible = this.visibility.collars;
      if (p.traces) p.traces.group.visible = this.visibility.paths;
    }
  }
}
