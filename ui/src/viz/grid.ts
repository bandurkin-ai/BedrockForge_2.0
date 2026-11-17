/**
 * «Координатная сетка с подписями» (ТЗ 7.13; BF `rebuildGrid` 2343): a horizontal grid just under the
 * content, a third wider than it, on round project coordinates (a 1-2-5 step), with the X values along its
 * south edge and the Y values along its west edge. Lines are float32 from the scene origin; the values are
 * written from the float64 coordinates. Not clipped by the section: it is a reference, like the gizmo.
 */
import * as THREE from "three";
import { boxCenter, boxSize, type Box3 } from "./core/bounds";
import type { Origin } from "./origin";
import { niceTicks, niceStep } from "./section/math";
import { LABEL_PRIORITY } from "./labels/declutter";
import { makeLabel, type LabelCache, type LabelItem } from "./labels/sprites";

export interface GridTheme {
  line: string;
  text: string;
}

const MAX_LINES = 60;

/** "X 512 300" — a grid value without the fraction (the step is at least 1 m on real data). */
export function gridText(axis: "X" | "Y", v: number): string {
  const r = Math.abs(v) >= 100 ? Math.round(v) : Number(v.toPrecision(6));
  return `${axis} ${r.toLocaleString("ru-RU")}`;
}

/** Lines of the grid in project coordinates: ticks along X and Y over the span around the content. */
export function gridLines(content: Box3, origin: Origin): { xs: number[]; ys: number[]; x0: number; x1: number; y0: number; y1: number; z: number } {
  const size = boxSize(content);
  const c = boxCenter(content);
  const span = Math.max(size[0], size[1], 100) * 1.3;
  const cx = c[0] + origin.x;
  const cy = c[1] + origin.y;
  const step = Math.max(niceStep(span, 10), span / MAX_LINES);
  const x0 = cx - span / 2;
  const y0 = cy - span / 2;
  return { xs: niceTicks(x0, x0 + span, span / step), ys: niceTicks(y0, y0 + span, span / step), x0, x1: x0 + span, y0, y1: y0 + span, z: content.min[2] + origin.z - 2 };
}

export class Grid {
  readonly group = new THREE.Group();
  labels: LabelItem[] = [];
  /** the grid in scene coordinates, for near / far */
  box: Box3 | null = null;
  private lines: THREE.LineSegments | null = null;

  constructor(private readonly cache: LabelCache) {
    this.group.renderOrder = -1;
  }

  update(content: Box3 | null, origin: Origin | null, theme: GridTheme): void {
    this.clear();
    if (!content || !origin) return;
    const g = gridLines(content, origin);
    const z = g.z - origin.z;
    const pts: number[] = [];
    for (const x of g.xs) pts.push(x - origin.x, g.y0 - origin.y, z, x - origin.x, g.y1 - origin.y, z);
    for (const y of g.ys) pts.push(g.x0 - origin.x, y - origin.y, z, g.x1 - origin.x, y - origin.y, z);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(pts), 3));
    this.lines = new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ color: theme.line, transparent: true, opacity: 0.35, depthWrite: false }));
    this.lines.renderOrder = -1;
    this.group.add(this.lines);
    const font = { size: 10, family: "sans-serif" as const, bold: false, color: theme.text };
    const at = new THREE.Vector3();
    for (const x of g.xs) {
      at.set(x - origin.x, g.y0 - origin.y, z);
      this.addLabel(gridText("X", x), at, font);
    }
    for (const y of g.ys) {
      at.set(g.x0 - origin.x, y - origin.y, z);
      this.addLabel(gridText("Y", y), at, font);
    }
    this.box = { min: [g.x0 - origin.x, g.y0 - origin.y, z], max: [g.x1 - origin.x, g.y1 - origin.y, z] };
  }

  labelItems(out: LabelItem[]): void {
    if (this.group.visible) for (const l of this.labels) out.push(l);
  }

  private addLabel(text: string, at: THREE.Vector3, font: { size: number; family: "sans-serif"; bold: boolean; color: string }): void {
    const item = makeLabel(this.cache, text, font, at, 4, LABEL_PRIORITY.tick);
    item.sprite.material = item.sprite.material.clone(); // not clipped: a material of its own
    item.sprite.material.clippingPlanes = [];
    item.clipped = false;
    this.group.add(item.sprite);
    this.labels.push(item);
  }

  private clear(): void {
    for (const l of this.labels) {
      this.group.remove(l.sprite);
      l.sprite.material.dispose(); // the clone only; the texture stays in the cache
    }
    this.labels = [];
    if (this.lines) {
      this.group.remove(this.lines);
      this.lines.geometry.dispose();
      (this.lines.material as THREE.Material).dispose();
      this.lines = null;
    }
    this.box = null;
  }

  dispose(): void {
    this.clear();
  }
}
