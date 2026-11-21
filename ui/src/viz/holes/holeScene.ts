/**
 * The hole sets in the facade of the 3D window (`Scene` extends it; moved out of `Scene.ts`): a set's manifest,
 * its traces, style and visibility, the selected hole, the mini-plan of the visible sets. What they do is
 * `sceneHoles.ts`; the scene makes them in its constructor with what they draw with.
 */
import { buildOverview, type PlanOverview } from "../section/miniPlan";
import type { BuiltPaths } from "../worker/paths";
import type { HoleSetLayer, HoleSetStyle, HoleSetVisibility } from "./holeSet";
import type { HolesManifest } from "./manifest";
import type { SceneHoles } from "./sceneHoles";
import { planSources } from "./screen";

export abstract class HoleScene {
  /** the hole sets and the selected hole; set by the constructor of the scene */
  protected sets!: SceneHoles;

  setHoles(manifest: HolesManifest, style: HoleSetStyle, visibility: HoleSetVisibility): void {
    this.sets.set(manifest, style, visibility);
  }

  setPaths(setId: number, k: number, built: BuiltPaths): void {
    this.sets.setPaths(setId, k, built);
  }

  holes(setId: number): HoleSetLayer | null {
    return this.sets.get(setId);
  }

  removeHoles(setId: number, update = true): void {
    this.sets.remove(setId, update);
  }

  setHoleStyle(setId: number, style: HoleSetStyle): void {
    this.sets.setStyle(setId, style);
  }

  setHoleVisibility(setId: number, v: HoleSetVisibility): void {
    this.sets.setVisibility(setId, v);
  }

  select(hole: { setId: number; name: string } | null): void {
    this.sets.select(hole);
  }

  /** Collars and traces of the visible sets for the mini-plan (≤ 20 000 points). */
  planOverview(): PlanOverview {
    return buildOverview(planSources(this.sets.layers()));
  }
}
