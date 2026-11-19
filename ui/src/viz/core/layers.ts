/**
 * The layers added to the scene from outside (`Scene.addLayer`, ADR 0007 §7, ADR 0011 §4): each sits in a
 * holder placed at `layer origin − scene origin` (float64 on the CPU), counts in the extents by its `bbox`,
 * gives its labels and answers a click on the CPU. After a lost context the scene drops them all.
 */
import * as THREE from "three";
import { boxFromBbox6, type Extent } from "./bounds";
import type { SceneOrigin } from "./sceneOrigin";
import type { LabelItem } from "../labels/sprites";
import type { FrameContext, LayerHit, PickQuery, SceneLayer } from "../sceneTypes";

interface Held {
  layer: SceneLayer;
  holder: THREE.Group;
}

export class LayerSet {
  private readonly items = new Map<string, Held>();

  constructor(
    private readonly root: THREE.Object3D,
    private readonly origin: SceneOrigin,
  ) {}

  keys(): string[] {
    return [...this.items.keys()];
  }

  add(key: string, layer: SceneLayer): void {
    this.remove(key);
    if (layer.bbox) this.origin.ensure(layer.bbox);
    else this.origin.ensure([layer.origin.x, layer.origin.y, layer.origin.z, layer.origin.x, layer.origin.y, layer.origin.z]);
    const holder = new THREE.Group();
    holder.add(layer.object);
    this.items.set(key, { layer, holder });
    this.placeOne(holder, layer);
    this.root.add(holder);
  }

  remove(key: string): boolean {
    const held = this.items.get(key);
    if (!held) return false;
    this.root.remove(held.holder);
    held.layer.dispose?.();
    this.items.delete(key);
    return true;
  }

  clear(): void {
    for (const key of this.keys()) this.remove(key);
  }

  /** Holder positions after the scene origin was set or moved, or a layer changed its own origin. */
  place(): void {
    for (const { layer, holder } of this.items.values()) this.placeOne(holder, layer);
  }

  private placeOne(holder: THREE.Group, layer: SceneLayer): void {
    const off = this.origin.offset(layer.origin);
    holder.position.set(off[0], off[1], off[2]);
  }

  extents(o: { x: number; y: number; z: number }, out: Extent[]): void {
    for (const { layer, holder } of this.items.values()) if (layer.bbox) out.push({ box: boxFromBbox6(layer.bbox, o), visible: holder.visible && layer.object.visible });
  }

  beforeRender(ctx: FrameContext): void {
    for (const { layer } of this.items.values()) layer.beforeRender?.(ctx);
  }

  labels(out: LabelItem[]): void {
    for (const { layer, holder } of this.items.values()) if (holder.visible && layer.object.visible && layer.labels) out.push(...layer.labels());
  }

  /** The best answer of the visible layers to a click: the smallest score. */
  pick(q: Omit<PickQuery, "offset">): LayerHit | null {
    let best: LayerHit | null = null;
    for (const [key, { layer, holder }] of this.items) {
      if (!layer.pick || !holder.visible || !layer.object.visible) continue;
      const r = layer.pick({ ...q, offset: this.origin.offset(layer.origin) });
      if (r && (!best || r.score < best.score)) best = { key, score: r.score, hit: r.hit };
    }
    return best;
  }
}
