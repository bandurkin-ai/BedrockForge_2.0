/**
 * The holes of the scene seen from outside it: the sources of the mini-plan (absolute collars and the trace
 * points of the visible sets) and the collars on the screen (the E2E hook clicks them).
 */
import type { HoleSetLayer } from "./holeSet";
import { projectToScreen, type Mat4 } from "../pick/ray";
import type { PlanSource } from "../section/miniPlan";

export function planSources(layers: Iterable<HoleSetLayer>): PlanSource[] {
  const sources: PlanSource[] = [];
  for (const layer of layers) {
    if (!layer.visible) continue;
    const m = layer.manifest;
    for (const p of layer.parts) {
      const idx = m.byPart[p.part.k];
      const collars = new Float64Array(idx.length * 3);
      for (let s = 0; s < idx.length; s++) collars.set(m.collar.subarray(idx[s] * 3, idx[s] * 3 + 3), s * 3);
      const b = p.traces?.built ?? null;
      sources.push({ origin: p.part.origin, collars, offsets: b?.offsets ?? null, xyz: b?.xyz ?? null });
    }
  }
  return sources;
}

export interface ScreenCollar {
  setId: number;
  name: string;
  /** client px */
  x: number;
  y: number;
}

/** Visible collars inside the window and the section window: client px of the page. */
export function collarsOnScreen(
  layers: Iterable<HoleSetLayer>,
  vp: Mat4,
  vexag: number,
  view: { w: number; h: number; left: number; top: number },
  inWindow: (x: number, y: number) => boolean,
  limit: number,
): ScreenCollar[] {
  const out: ScreenCollar[] = [];
  for (const layer of layers) {
    if (!layer.visible || !layer.visibility.collars) continue;
    for (const p of layer.parts) {
      const pos = p.collars.positions;
      for (let s = 0; s < p.collars.count && out.length < limit; s++) {
        const x = pos[s * 3] + p.offset[0];
        const y = pos[s * 3 + 1] + p.offset[1];
        if (!inWindow(x, y)) continue;
        const sp = projectToScreen(vp, x, y, (pos[s * 3 + 2] + p.offset[2]) * vexag, view.w, view.h);
        if (sp && sp[0] >= 0 && sp[1] >= 0 && sp[0] <= view.w && sp[1] <= view.h) {
          out.push({ setId: layer.setId, name: layer.manifest.names[layer.manifest.byPart[p.part.k][s]], x: view.left + sp[0], y: view.top + sp[1] });
        }
      }
    }
  }
  return out;
}
