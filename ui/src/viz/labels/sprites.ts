/**
 * Text labels of the 3D view as sprites (ADR 0007 §7; BF `labelMaterial` 897–928, `updateDownholes`
 * 930–974): a canvas texture per text and font, kept in a cache and shared; the sprite keeps its size in
 * pixels at any zoom and stands to the right of its marker. Every frame the scene places the labels of all
 * layers together by priority (`labels/declutter.ts`): crossings, end depths, names, ticks. Their material
 * takes the shared clipping planes, so a label outside the section window is cut with its object.
 */
import * as THREE from "three";
import { declutter, type LabelBox } from "./declutter";

export interface LabelFont {
  /** px */
  size: number;
  family: "sans-serif" | "serif" | "monospace";
  bold: boolean;
  color: string;
  halo?: string;
}

export const DEFAULT_FONT: LabelFont = { size: 12, family: "sans-serif", bold: false, color: "#f3f6fb" };

/** The brand text face (Inter, ADR 0006) for «sans-serif»; the others as they are. */
const FAMILIES: Record<LabelFont["family"], string> = {
  "sans-serif": 'Inter, "Segoe UI", sans-serif',
  serif: 'Georgia, "Times New Roman", serif',
  monospace: 'Consolas, "Courier New", monospace',
};

interface Cached {
  material: THREE.SpriteMaterial;
  wpx: number;
  hpx: number;
}

export class LabelCache {
  private readonly items = new Map<string, Cached>();

  constructor(private readonly clipPlanes: THREE.Plane[]) {}

  get size(): number {
    return this.items.size;
  }

  material(text: string, f: LabelFont = DEFAULT_FONT): Cached {
    const key = `${f.size}|${f.family}|${f.bold ? "b" : ""}|${f.color}|${f.halo ?? ""}|${text}`;
    const hit = this.items.get(key);
    if (hit) return hit;
    const k = 2;
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d");
    const font = `${f.bold ? "bold " : ""}${f.size * k}px ${FAMILIES[f.family] ?? FAMILIES["sans-serif"]}`;
    if (ctx) ctx.font = font;
    canvas.width = Math.max(2, Math.ceil((ctx?.measureText(text).width ?? text.length * f.size * 0.6 * k) + 6 * k));
    canvas.height = Math.ceil(f.size * k * 1.4);
    if (ctx) {
      ctx.font = font;
      ctx.textBaseline = "middle";
      ctx.lineJoin = "round";
      ctx.lineWidth = 3 * k;
      ctx.strokeStyle = f.halo ?? "rgba(10, 14, 22, 0.85)";
      ctx.strokeText(text, 3 * k, canvas.height / 2);
      ctx.fillStyle = f.color;
      ctx.fillText(text, 3 * k, canvas.height / 2);
    }
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false, clippingPlanes: this.clipPlanes });
    const out = { material, wpx: canvas.width / k, hpx: canvas.height / k };
    this.items.set(key, out);
    return out;
  }

  dispose(): void {
    for (const { material } of this.items.values()) {
      material.map?.dispose();
      material.dispose();
    }
    this.items.clear();
  }
}

export interface LabelItem {
  sprite: THREE.Sprite;
  wpx: number;
  hpx: number;
  /** px from the anchor to the left edge of the text */
  shift: number;
  /** `LABEL_PRIORITY` of declutter */
  priority: number;
  /** cut by the section window with its object (false: the grid) */
  clipped: boolean;
}

/** A label sprite at `position` (local coordinates of its parent). */
export function makeLabel(cache: LabelCache, text: string, font: LabelFont, position: THREE.Vector3, shift: number, priority: number): LabelItem {
  const { material, wpx, hpx } = cache.material(text, font);
  const sprite = new THREE.Sprite(material);
  sprite.renderOrder = 10;
  sprite.frustumCulled = false;
  sprite.position.copy(position);
  sprite.center.set(-shift / wpx, 0.5);
  return { sprite, wpx, hpx, shift, priority, clipped: true };
}

export interface LabelView {
  width: number;
  height: number;
  /** world position → screen px and the pixels per world unit there; null — off screen */
  project(world: THREE.Vector3): { x: number; y: number; ppu: number } | null;
  /** false — outside the section window */
  inWindow(world: THREE.Vector3): boolean;
}

/**
 * Places the labels of a frame: the visible ones that fit, by priority (the caller lists nearer ones
 * first); the others are hidden. Sprites get their size in pixels.
 */
export function placeLabels(items: readonly LabelItem[], view: LabelView): number {
  const world = new THREE.Vector3();
  const boxes: LabelBox[] = [];
  const scale: number[] = [];
  for (const it of items) {
    it.sprite.getWorldPosition(world);
    const p = !it.clipped || view.inWindow(world) ? view.project(world) : null;
    boxes.push(p ? { x: p.x + it.shift, y: p.y, w: it.wpx, h: it.hpx, priority: it.priority } : { x: NaN, y: NaN, w: 0, h: 0, priority: it.priority });
    scale.push(p ? p.ppu : 0);
  }
  const shown = declutter(boxes, { width: view.width, height: view.height });
  let n = 0;
  items.forEach((it, i) => {
    const on = shown[i] === 1 && scale[i] > 0;
    it.sprite.visible = on;
    if (!on) return;
    n++;
    it.sprite.center.set(-it.shift / it.wpx, 0.5);
    it.sprite.scale.set(it.wpx / scale[i], it.hpx / scale[i], 1);
  });
  return n;
}
