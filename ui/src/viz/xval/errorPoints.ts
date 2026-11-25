/**
 * The errors of the cross-validation in the 3D window (ТЗ 8.8.5; `SceneLayer`, key `xval-errors`): a disc at every sample that has
 * an error, coloured by it (Cobalt — the estimate is below the true value, Light Stone — none, Copper Orange — above; the colours
 * come from `modules/estimation/xvalModel.ts`). The places come in project coordinates float64 and are drawn as float32 offsets from
 * an integer origin (`viz/origin.ts`, ТЗ 5.4); clipped like the rest of the scene.
 */
import * as THREE from "three";
import { integerOrigin, toRelativeFloat32, type Origin } from "../origin";
import type { SceneLayer } from "../sceneTypes";

export interface ErrorPointsData {
  /** project coordinates, three numbers a point */
  xyz: Float64Array;
  /** 0…1, three numbers a point */
  rgb: Float32Array;
}

/** A point of the layer on the screen, px. */
export const ERROR_POINT_PX = 8;

export function extentOf(xyz: Float64Array): number[] | null {
  if (!xyz.length) return null;
  const lo = [Infinity, Infinity, Infinity];
  const hi = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < xyz.length; i += 3) {
    for (let k = 0; k < 3; k++) {
      lo[k] = Math.min(lo[k], xyz[i + k]);
      hi[k] = Math.max(hi[k], xyz[i + k]);
    }
  }
  return [...lo, ...hi];
}

export class ErrorPoints implements SceneLayer {
  readonly object = new THREE.Group();
  readonly origin: Origin;
  readonly bbox: number[] | null;
  /** the points drawn */
  readonly count: number;
  private readonly points: THREE.Points;

  constructor(data: ErrorPointsData, clip: THREE.Plane[], pixelRatio = 1) {
    this.origin = integerOrigin(data.xyz);
    this.bbox = extentOf(data.xyz);
    this.count = data.xyz.length / 3;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(toRelativeFloat32(data.xyz, this.origin), 3));
    geometry.setAttribute("color", new THREE.BufferAttribute(data.rgb, 3));
    const material = new THREE.PointsMaterial({ vertexColors: true, size: ERROR_POINT_PX * pixelRatio, sizeAttenuation: false });
    material.clippingPlanes = clip;
    // a disc with a dark rim, as the points of the layers of lines
    material.onBeforeCompile = (shader) => {
      shader.fragmentShader = shader.fragmentShader.replace(
        "#include <map_particle_fragment>",
        "vec2 bfDisc = gl_PointCoord - vec2(0.5);\n\tfloat bfR2 = dot(bfDisc, bfDisc);\n\tif (bfR2 > 0.25) discard;\n\tif (bfR2 > 0.14) diffuseColor.rgb *= 0.3;\n\t#include <map_particle_fragment>",
      );
    };
    material.customProgramCacheKey = () => "bf-xval-disc";
    this.points = new THREE.Points(geometry, material);
    this.points.frustumCulled = false;
    this.points.renderOrder = 60;
    this.object.add(this.points);
  }

  dispose(): void {
    this.points.geometry.dispose();
    (this.points.material as THREE.Material).dispose();
  }
}
