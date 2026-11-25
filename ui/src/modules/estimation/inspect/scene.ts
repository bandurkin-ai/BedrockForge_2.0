/**
 * The inspector of a block in the main 3D (ТЗ 7.35 Д-30; ADR 0024): while the window «Блок · i, j, k» is open, two scene layers follow the row
 * shown — `inspect:ellipsoid` (the ellipsoid of the pass: `viz/ellipsoid` as it is, Д-14, one structure in Cobalt) and `inspect:segments`
 * (`layer.ts`: the segments to the probes taken coloured by the weight, the probes cut off in Slate, the probe chosen lit). A new row or a
 * new block builds the layers again; the toggles of the window and the probe chosen only change what is drawn. A closed window takes
 * the layers away. After a lost context the layers come again. A lazy chunk with three.js (`bind.ts` attaches it to every scene).
 */
import { bridge } from "../../../bridge";
import { errorText } from "../../../shell/props";
import { windowStore } from "../../../shell/windows";
import { buildEllipsoid } from "../../../viz/ellipsoid/build";
import { EllipsoidLayer } from "../../../viz/ellipsoid/layer";
import type { Scene } from "../../../viz/Scene";
import { sceneUi } from "../../scene/sceneHost";
import type { InspectResult } from "./api";
import { buildSegments, drawable, ellipsoidView } from "./build";
import { SegmentsLayer } from "./layer";
import { watchSelected } from "../watch";
import { activeResult, INSPECT_WINDOW, inspectStore } from "./state";

export const SEGMENTS_KEY = "inspect:segments";
export const ELLIPSOID_KEY = "inspect:ellipsoid";

interface Shown<L> {
  layer: L;
  /** the answer and the row it was built from: another one — a new build */
  result: InspectResult;
  centre: string;
}

export class InspectScene {
  private seg: Shown<SegmentsLayer> | null = null;
  private ell: Shown<EllipsoidLayer> | null = null;
  private readonly stops: (() => void)[] = [];
  private disposed = false;
  /** the toggles and the probe lit the layer was last drawn with: another store event (a sort, a note) redraws nothing */
  private drawn = "";

  constructor(private readonly scene: Scene) {
    const sync = () => this.sync();
    this.stops.push(inspectStore.subscribe(sync), watchSelected(windowStore, () => this.open(), sync), scene.onRestored(() => this.restore()));
    this.sync();
  }

  private open(): boolean {
    return windowStore.get().windows.some((w) => w.kind === INSPECT_WINDOW);
  }

  sync(): void {
    if (this.disposed) return;
    const s = inspectStore.get();
    const r = this.open() && s.answer ? activeResult(s) : null;
    const centre = r && s.answer ? s.answer.block.centre : null;
    let changed = false;
    let redraw = false;

    // the segments: built again for another row or block; the toggles and the probe lit only change the drawing
    if (!r || !centre) {
      changed = this.dropSegments() || changed;
      changed = this.dropEllipsoid() || changed;
    } else {
      const key = centre.join(",");
      if (!this.seg || this.seg.result !== r || this.seg.centre !== key) {
        this.dropSegments();
        this.seg = this.makeSegments(r, centre, key);
        changed = true;
      }
      if (this.seg) {
        const look = `${s.show.segments}|${s.show.excluded}|${s.picked}`;
        if (look !== this.drawn) {
          this.drawn = look;
          this.seg.layer.setShow({ segments: s.show.segments, excluded: s.show.excluded });
          this.seg.layer.setPicked(s.picked);
          redraw = true;
        }
      }
      const wantEll = r.active && drawable(r.ellipsoid) && s.show.ellipsoid;
      if (!wantEll) changed = this.dropEllipsoid() || changed;
      else if (!this.ell || this.ell.result !== r) {
        this.dropEllipsoid();
        this.ell = this.makeEllipsoid(r);
        changed = true;
      }
    }
    if (changed) this.scene.layerChanged?.();
    if (changed || redraw) this.scene.invalidate();
  }

  private makeSegments(r: InspectResult, centre: readonly number[], key: string): Shown<SegmentsLayer> | null {
    try {
      const layer = new SegmentsLayer(buildSegments(r, [centre[0], centre[1], centre[2]]), { clip: this.scene.clipPlanes, resolution: this.scene.resolution });
      this.scene.addLayer(SEGMENTS_KEY, layer);
      return { layer, result: r, centre: key };
    } catch (e) {
      sceneUi.set({ error: `Пробы блока не показаны в 3D: ${errorText(e)}` });
      return null;
    }
  }

  private makeEllipsoid(r: InspectResult): Shown<EllipsoidLayer> | null {
    const e = r.ellipsoid;
    if (!drawable(e)) return null;
    try {
      const layer = new EllipsoidLayer(buildEllipsoid({ centre: e.centre_xyz, structures: [ellipsoidView(e)], selected: 0 }), { clip: this.scene.clipPlanes, resolution: this.scene.resolution });
      this.scene.addLayer(ELLIPSOID_KEY, layer);
      return { layer, result: r, centre: e.centre_xyz.join(",") };
    } catch (err) {
      sceneUi.set({ error: `Эллипсоид прохода не показан в 3D: ${errorText(err)}` });
      return null;
    }
  }

  private dropSegments(): boolean {
    if (!this.seg) return false;
    this.seg = null;
    this.drawn = "";
    this.scene.removeLayer(SEGMENTS_KEY, false); // the scene disposes the layer it removes
    return true;
  }

  private dropEllipsoid(): boolean {
    if (!this.ell) return false;
    this.ell = null;
    this.scene.removeLayer(ELLIPSOID_KEY, false);
    return true;
  }

  /** After a lost context the scene dropped every layer: they are built again from the store. */
  private restore(): void {
    this.seg = null;
    this.ell = null;
    this.sync();
  }

  /** For the E2E hook and the tests: what is drawn now. */
  stats(): { segments: ReturnType<SegmentsLayer["shown"]> | null; ellipsoid: ReturnType<EllipsoidLayer["shown"]> | null; centre: number[] | null } {
    return { segments: this.seg?.layer.shown() ?? null, ellipsoid: this.ell?.layer.shown() ?? null, centre: this.ell ? [...this.ell.layer.build.centre] : null };
  }

  dispose(): void {
    this.disposed = true;
    this.stops.forEach((fn) => fn());
    this.dropSegments();
    this.dropEllipsoid();
    this.scene.invalidate();
  }
}

/** Attaches the inspector to a scene; gives what detaches it. */
export function attachInspectScene(scene: Scene): () => void {
  const s = new InspectScene(scene);
  const hook = bridge()?.flags?.().e2e === true && typeof window !== "undefined";
  // BF_E2E=1: the layers of the inspector as drawn (app/e2e/estimation.spec.ts)
  if (hook) window.__bfInspect = { stats: () => s.stats() };
  return () => {
    s.dispose();
    if (hook) delete window.__bfInspect;
  };
}

declare global {
  interface Window {
    __bfInspect?: unknown;
  }
}
