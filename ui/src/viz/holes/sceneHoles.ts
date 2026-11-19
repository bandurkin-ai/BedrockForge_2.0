/**
 * The hole sets of the 3D window (moved out of `Scene.ts`): a layer per set placed from the scene origin, fat or
 * thin traces by the total of segments and the class of graphics of the scene (software rendering — 200 thousand,
 * `budget.ts` `THIN_LINE_SEGMENTS`), the selected hole drawn over its set. No camera and no frame here: the
 * scene gives what a layer draws with and follows the changes (`HolesHost`).
 */
import type * as THREE from "three";
import type { Extent } from "../core/bounds";
import type { GpuClass } from "../core/budget";
import type { SceneOrigin } from "../core/sceneOrigin";
import type { BuiltPaths } from "../worker/paths";
import { HoleSetLayer, type HoleSetContext, type HoleSetStyle, type HoleSetVisibility } from "./holeSet";
import { holeIndex, type HolesManifest } from "./manifest";
import { useFatLines } from "./paths";
import { SelectedHole } from "./select";

export interface HolesHost {
  readonly root: THREE.Group;
  readonly origin: SceneOrigin;
  /** what a layer draws with now: the clipping planes, the textures, the labels, the pixel ratio */
  context(): HoleSetContext;
  /** a set came or went: the grid, the section and the first view follow */
  contentChanged(): void;
  /** the visibility of a set changed: the grid follows */
  gridChanged(): void;
  invalidate(): void;
  /** the class of graphics of the scene: the threshold of thin traces; none — real graphics */
  gpu?(): GpuClass;
}

export class SceneHoles {
  private readonly sets = new Map<number, HoleSetLayer>();
  private readonly selection: SelectedHole;
  private selected: { setId: number; name: string } | null = null;

  constructor(
    private readonly host: HolesHost,
    clip: THREE.Plane[],
    resolution: THREE.Vector2,
  ) {
    this.selection = new SelectedHole(clip, resolution);
  }

  layers(): IterableIterator<HoleSetLayer> {
    return this.sets.values();
  }

  ids(): number[] {
    return [...this.sets.keys()];
  }

  get(setId: number): HoleSetLayer | null {
    return this.sets.get(setId) ?? null;
  }

  set(manifest: HolesManifest, style: HoleSetStyle, visibility: HoleSetVisibility): void {
    this.remove(manifest.setId, false);
    if (!manifest.parts.length) return void this.host.contentChanged();
    this.host.origin.ensure(manifest.parts[0].bbox);
    const layer = new HoleSetLayer(manifest, this.host.context(), style, visibility);
    layer.place(this.host.origin);
    this.sets.set(manifest.setId, layer);
    this.host.root.add(layer.group);
    this.applySelection();
    this.host.contentChanged();
  }

  setPaths(setId: number, k: number, built: BuiltPaths): void {
    const layer = this.sets.get(setId);
    if (!layer) return;
    // fat or thin chosen before the part is built, with its segments counted (the set's built parts follow first)
    const fat = useFatLines(layer.style.trace, this.segments() + built.segments, this.host.gpu?.());
    layer.setFat(fat);
    layer.setPaths(k, built, fat);
    this.updateFat();
    if (this.selected?.setId === setId) this.applySelection();
    this.host.invalidate();
  }

  remove(setId: number, update = true): void {
    const layer = this.sets.get(setId);
    if (!layer) return;
    if (this.selected?.setId === setId) this.selection.clear();
    this.host.root.remove(layer.group);
    layer.dispose();
    this.sets.delete(setId);
    if (update) this.host.contentChanged();
  }

  setStyle(setId: number, style: HoleSetStyle): void {
    const layer = this.sets.get(setId);
    if (!layer) return;
    layer.setStyle(style, useFatLines(style.trace, this.segments(), this.host.gpu?.()));
    if (this.selected?.setId === setId) this.applySelection();
    this.host.invalidate();
  }

  setVisibility(setId: number, v: HoleSetVisibility): void {
    const layer = this.sets.get(setId);
    if (!layer) return;
    layer.setVisibility(v);
    this.host.gridChanged();
    this.host.invalidate();
  }

  select(hole: { setId: number; name: string } | null): void {
    this.selected = hole;
    this.applySelection();
    this.host.invalidate();
  }

  /** The selected hole drawn again (a new pixel ratio, a restyled set). */
  applySelection(): void {
    const s = this.selected;
    const layer = s ? (this.sets.get(s.setId) ?? null) : null;
    const index = layer && s ? holeIndex(layer.manifest, s.name) : -1;
    const ctx = this.host.context();
    this.selection.update(layer, index, ctx.pixelRatio, layer?.style.collar.size ?? 10, ctx.textures.get("circle"));
  }

  /** Segments of the traces of every set (fat lines only below a total). */
  segments(): number {
    let n = 0;
    for (const l of this.sets.values()) n += l.segments;
    return n;
  }

  extents(o: { x: number; y: number; z: number }, out: Extent[]): void {
    for (const l of this.sets.values()) out.push({ box: l.extent(o), visible: l.visible });
  }

  /** The scene origin moved: every part is placed again. */
  place(): void {
    for (const l of this.sets.values()) l.place(this.host.origin);
  }

  /** Every set goes (a lost context, the scene closes). */
  clear(): void {
    for (const id of [...this.sets.keys()]) this.remove(id, false);
    this.selection.clear();
  }

  private updateFat(): void {
    const total = this.segments();
    const gpu = this.host.gpu?.();
    for (const l of this.sets.values()) l.setFat(useFatLines(l.style.trace, total, gpu));
  }
}
