/**
 * The search ellipsoid, the block and the probes of the preview in the main 3D (ТЗ 8.8.4; docs/progress/e18-api.md §7, §12): three layers of the
 * scene follow `viewOf` (`preview.ts`) — `estimate:ellipsoid` (the ellipsoid of the pass chosen: `viz/ellipsoid` as it is, Д-14, Cobalt),
 * `estimate:probes` (the segments from the centre of the block to the probes taken coloured by the weight λ, Cobalt for a positive one and
 * Copper Orange for a negative one, the others in Slate: the layer of the inspector, `inspect/layer.ts`, one look for both) and
 * `estimate:block` (the box of the block in Light Stone). An edit of step 4 changes only the matrix of the ellipsoid (`setPose`): no new
 * geometry, no blink; a new block of the preview builds the probes and the box again. The windows of the form closed, another project, the
 * layer switched off — the layers go. After a lost context they come again. A lazy chunk with three.js (`previewBind.ts` attaches it to every scene).
 */
import * as THREE from "three";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import { bridge } from "../../bridge";
import { errorText } from "../../shell/props";
import { windowStore } from "../../shell/windows";
import { buildEllipsoid } from "../../viz/ellipsoid/build";
import { EllipsoidLayer } from "../../viz/ellipsoid/layer";
import type { Scene } from "../../viz/Scene";
import type { SceneLayer } from "../../viz/sceneTypes";
import { sceneUi } from "../scene/sceneHost";
import { ellipsoidView } from "./inspect/build";
import { SegmentsLayer } from "./inspect/layer";
import { COLORS } from "./inspect/view";
import { buildBox, buildProbes, previewSceneStore, viewOf, type BoxBuild, type Pose, type PreviewView } from "./preview";
import { formWindowOpen } from "./previewWindows";
import { watchSelected } from "./watch";
import { wizardStore } from "./wizardState";

export const ELLIPSOID_KEY = "estimate:ellipsoid";
export const PROBES_KEY = "estimate:probes";
export const BLOCK_KEY = "estimate:block";

/** The look of the box of the block: the width of the line, px. */
const BOX = { width: 1.6, order: 7 } as const;

/** The block as a box of 12 edges (a lines layer: screen-space width, clipped by the section window like the rest). */
export class BlockBoxLayer implements SceneLayer {
  readonly object = new THREE.Group();
  readonly origin: { x: number; y: number; z: number };
  readonly bbox: number[];
  private readonly geometry = new LineSegmentsGeometry();
  private readonly material: LineMaterial;

  constructor(
    readonly build: BoxBuild,
    ctx: { clip: THREE.Plane[]; resolution: THREE.Vector2 },
  ) {
    this.origin = build.origin;
    this.bbox = [...build.reach];
    this.geometry.setPositions(build.positions);
    this.material = new LineMaterial({ color: new THREE.Color(COLORS.pick).getHex(), linewidth: BOX.width, worldUnits: false });
    this.material.uniforms.resolution.value = ctx.resolution;
    this.material.clippingPlanes = ctx.clip;
    const lines = new LineSegments2(this.geometry, this.material);
    lines.renderOrder = BOX.order;
    this.object.add(lines);
  }

  dispose(): void {
    this.geometry.dispose();
    this.material.dispose();
    this.object.clear();
  }
}

interface Held<L> {
  layer: L;
  /** what it was built from: the answer (probes, box) or the centre (ellipsoid) */
  of: unknown;
}

const poseKey = (p: Pose): string => [p.ang1, p.ang2, p.ang3, p.r1, p.r2, p.r3].join(",");

export interface PreviewStats {
  source: "wizard" | "row" | null;
  ellipsoid: ReturnType<EllipsoidLayer["shown"]> | null;
  probes: ReturnType<SegmentsLayer["shown"]> | null;
  block: boolean;
  /** the centre of the ellipsoid, the radii and the pass drawn */
  pose: (Pose & { centre: number[]; pass: number | null }) | null;
  /** ms of the last change of the layers (the build of the probes and the box, or the new matrix of the ellipsoid) */
  ms: number;
}

export class PreviewScene {
  private ell: (Held<EllipsoidLayer> & { pose: string; drawn: Pose }) | null = null;
  private probes: Held<SegmentsLayer> | null = null;
  private box: Held<BlockBoxLayer> | null = null;
  private view: PreviewView | null = null;
  /** the answer and the centre the layers were tried for (a failure is not tried again at every keystroke) */
  private builtFor: unknown = null;
  private ellFor: string | null = null;
  private ms = 0;
  private revealed = previewSceneStore.get().reveal;
  private readonly stops: (() => void)[] = [];
  private disposed = false;

  constructor(private readonly scene: Scene) {
    const sync = () => this.sync();
    this.stops.push(previewSceneStore.subscribe(sync), wizardStore.subscribe(sync), watchSelected(windowStore, formWindowOpen, sync), scene.onRestored(() => this.restore()));
    this.sync();
  }

  sync(): void {
    if (this.disposed) return;
    const s = previewSceneStore.get();
    const v = viewOf(s, wizardStore.get(), formWindowOpen());
    this.view = v;
    const t0 = performance.now();
    let changed = false;
    if (!v) {
      changed = this.dropAll();
    } else {
      // the block and the probes: of the answer; another answer builds them again (the old ones are replaced in one task)
      if (this.builtFor !== v.answer) {
        this.builtFor = v.answer;
        this.probes = this.makeProbes(v);
        this.box = v.size ? this.makeBox(v) : this.dropBox();
        changed = true;
      }
      // the ellipsoid: a new centre builds it, a new pose only moves the matrix
      const centre = v.centre.join(",");
      if (v.pose) {
        const key = poseKey(v.pose);
        if (!this.ell && this.ellFor === centre) {
          // the build failed for this centre: not again until the block changes
        } else if (!this.ell || this.ell.of !== centre) {
          this.ellFor = centre;
          this.ell = this.makeEllipsoid(v, centre, key);
          changed = true;
        } else if (this.ell.pose !== key) {
          this.ell.layer.setPose(0, ellipsoidView(v.pose));
          this.ell.pose = key;
          this.ell.drawn = v.pose;
          changed = true;
        }
      } else if (this.ell && this.ell.of !== centre) {
        changed = this.dropEllipsoid() || changed;
      }
    }
    if (changed) {
      this.ms = performance.now() - t0;
      this.scene.layerChanged?.();
    }
    if (s.reveal !== this.revealed) {
      this.revealed = s.reveal;
      const boxes = [this.ell?.layer.bbox, this.box?.layer.bbox].filter((b): b is number[] => !!b);
      if (boxes.length) this.scene.reveal?.([union(boxes)]);
      changed = true;
    }
    if (changed) this.scene.invalidate();
  }

  private ctx() {
    return { clip: this.scene.clipPlanes, resolution: this.scene.resolution };
  }

  private makeProbes(v: PreviewView): Held<SegmentsLayer> | null {
    try {
      const layer = new SegmentsLayer(buildProbes(v.answer), this.ctx());
      this.scene.addLayer(PROBES_KEY, layer);
      return { layer, of: v.answer };
    } catch (e) {
      this.scene.removeLayer(PROBES_KEY, false);
      sceneUi.set({ error: `Пробы предпросмотра не показаны в 3D: ${errorText(e)}` });
      // kept as «built» for this answer: a failure is not retried at every keystroke
      return null;
    }
  }

  private makeBox(v: PreviewView): Held<BlockBoxLayer> | null {
    try {
      const layer = new BlockBoxLayer(buildBox(v.centre, v.size!), this.ctx());
      this.scene.addLayer(BLOCK_KEY, layer);
      return { layer, of: v.answer };
    } catch (e) {
      sceneUi.set({ error: `Блок не показан в 3D: ${errorText(e)}` });
      return null;
    }
  }

  private makeEllipsoid(v: PreviewView, centre: string, pose: string): (Held<EllipsoidLayer> & { pose: string; drawn: Pose }) | null {
    try {
      const layer = new EllipsoidLayer(buildEllipsoid({ centre: v.centre, structures: [ellipsoidView(v.pose!)], selected: 0 }), this.ctx());
      this.scene.addLayer(ELLIPSOID_KEY, layer);
      return { layer, of: centre, pose, drawn: v.pose! };
    } catch (e) {
      this.scene.removeLayer(ELLIPSOID_KEY, false);
      sceneUi.set({ error: `Эллипсоид поиска не показан в 3D: ${errorText(e)}` });
      return null;
    }
  }

  private dropBox(): null {
    if (this.box) this.scene.removeLayer(BLOCK_KEY, false);
    return null;
  }

  private dropEllipsoid(): boolean {
    if (!this.ell) return false;
    this.ell = null;
    this.scene.removeLayer(ELLIPSOID_KEY, false); // the scene disposes the layer it removes
    return true;
  }

  private dropAll(): boolean {
    this.builtFor = null;
    this.ellFor = null;
    const had = this.probes !== null || this.box !== null || this.ell !== null;
    if (this.probes) this.scene.removeLayer(PROBES_KEY, false);
    this.probes = null;
    this.dropBox();
    this.box = null;
    this.dropEllipsoid();
    return had;
  }

  /** After a lost context the scene dropped every layer: they are built again from the stores. */
  private restore(): void {
    this.ell = null;
    this.probes = null;
    this.box = null;
    this.builtFor = null;
    this.ellFor = null;
    this.sync();
  }

  /** For the E2E hook and the tests: what is drawn now. */
  stats(): PreviewStats {
    const v = this.view;
    const pose = this.ell ? { ...this.ell.drawn, centre: [...this.ell.layer.build.centre], pass: v?.pass ?? null } : null;
    return { source: v ? v.source : null, ellipsoid: this.ell?.layer.shown() ?? null, probes: this.probes?.layer.shown() ?? null, block: this.box !== null, pose, ms: this.ms };
  }

  dispose(): void {
    this.disposed = true;
    this.stops.forEach((fn) => fn());
    this.dropAll();
    this.scene.invalidate();
  }
}

/** The box of boxes `[xmin, ymin, zmin, xmax, ymax, zmax]`. */
function union(boxes: readonly number[][]): number[] {
  return [0, 1, 2, 3, 4, 5].map((i) => (i < 3 ? Math.min(...boxes.map((b) => b[i])) : Math.max(...boxes.map((b) => b[i]))));
}

/** Attaches the layers of the preview to a scene; gives what detaches them. */
export function attachPreviewScene(scene: Scene): () => void {
  const s = new PreviewScene(scene);
  const hook = bridge()?.flags?.().e2e === true && typeof window !== "undefined";
  // BF_E2E=1: the layers of the preview as drawn (app/e2e/estimation.spec.ts)
  if (hook) window.__bfPreview = { stats: () => s.stats() };
  return () => {
    s.dispose();
    if (hook) delete window.__bfPreview;
  };
}

declare global {
  interface Window {
    __bfPreview?: unknown;
  }
}
