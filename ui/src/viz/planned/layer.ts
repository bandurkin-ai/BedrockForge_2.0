/**
 * The planned holes in the main 3D (ТЗ 7.35 Д-34 ч. 2; ADR 0026 решение 29) — a scene layer (`Scene.addLayer`, key `planned`; образец — `viz/discs/layer.ts`
 * and `viz/lines/layer.ts`): the paths of all the holes in one `LineSegments2` (Copper, the colour of the planned and of the ore in the brand), the hole
 * chosen in the tree in a second, thicker one, the targets as discs with a dark rim (Cobalt) and the collars as small Copper dots. Float32 from the
 * integer origin of the layer (`build.ts`); every material takes the shared clipping planes, so the layer is cut by the section window as the rest
 * of the scene and is seen in the sections (7.14) with no code of their own. Counted in «Вписать» (`bbox`). The layer is not changed in place: a new
 * build is a new layer (the origin may move).
 */
import * as THREE from "three";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import type { Origin } from "../origin";
import type { SceneLayer } from "../sceneTypes";
import type { PlannedBuild } from "./build";

export interface PlannedLayerContext {
  /** the shared clipping planes of the scene (the section window) */
  clip: THREE.Plane[];
  /** the size of the view in px (the `resolution` of every LineMaterial) */
  resolution: THREE.Vector2;
  pixelRatio?: number;
}

/** The look: the brand palette (docs/brand), widths and sizes in px. */
export const LOOK = { color: "#f46a1a", target: "#1f6bff", line: 2.5, chosen: 4.5, targetPx: 10, collarPx: 6 } as const;

/** Points as discs with a dark rim, drawn by the fragment shader (as the points of the layers of lines). */
function dots(positions: Float32Array, color: string, sizePx: number, ctx: PlannedLayerContext, clip: THREE.Plane[]): THREE.Points {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.computeBoundingSphere();
  const material = new THREE.PointsMaterial({ color, size: sizePx * (ctx.pixelRatio ?? 1), sizeAttenuation: false });
  material.clippingPlanes = clip;
  material.onBeforeCompile = (shader) => {
    shader.fragmentShader = shader.fragmentShader.replace(
      "#include <map_particle_fragment>",
      "vec2 bfDisc = gl_PointCoord - vec2(0.5);\n\tfloat bfR2 = dot(bfDisc, bfDisc);\n\tif (bfR2 > 0.25) discard;\n\tif (bfR2 > 0.14) diffuseColor.rgb *= 0.3;\n\t#include <map_particle_fragment>",
    );
  };
  material.customProgramCacheKey = () => "bf-disc";
  const points = new THREE.Points(geometry, material);
  points.frustumCulled = false;
  points.renderOrder = 7;
  return points;
}

export class PlannedLayer implements SceneLayer {
  readonly object = new THREE.Group();
  readonly origin: Origin;
  readonly bbox: number[] | null;
  private readonly disposables: { dispose(): void }[] = [];

  constructor(
    readonly build: PlannedBuild,
    ctx: PlannedLayerContext,
  ) {
    this.origin = { ...build.origin };
    this.bbox = build.bbox;
    const line = (positions: Float32Array, width: number, order: number) => {
      if (!positions.length) return;
      const geometry = new LineSegmentsGeometry();
      geometry.setPositions(positions); // the buffer as it is: pairs of points
      const material = new LineMaterial({ color: LOOK.color, linewidth: width, worldUnits: false });
      material.clippingPlanes = ctx.clip;
      material.uniforms.resolution.value = ctx.resolution;
      const mesh = new LineSegments2(geometry, material);
      mesh.frustumCulled = false;
      mesh.renderOrder = order;
      this.object.add(mesh);
      this.disposables.push(geometry, material);
    };
    line(build.lines, LOOK.line, 5);
    line(build.chosen, LOOK.chosen, 6);
    for (const [positions, color, px] of [
      [build.collars, LOOK.color, LOOK.collarPx],
      [build.targets, LOOK.target, LOOK.targetPx],
    ] as const) {
      if (!positions.length) continue;
      const p = dots(positions, color, px, ctx, ctx.clip);
      this.object.add(p);
      this.disposables.push(p.geometry, p.material as THREE.Material);
    }
  }

  /** For the tests and the E2E hook: what is drawn. */
  stats(): { holes: number; segments: number; chosenSegments: number; targets: number; collars: number; skipped: number; origin: Origin; bbox: number[] | null } {
    const b = this.build;
    return { holes: b.holes, segments: b.segments, chosenSegments: b.chosen.length / 6, targets: b.targets.length / 3, collars: b.collars.length / 3, skipped: b.skipped, origin: { ...this.origin }, bbox: b.bbox ? [...b.bbox] : null };
  }

  dispose(): void {
    for (const d of this.disposables) d.dispose();
    this.disposables.length = 0;
    this.object.clear();
  }
}
