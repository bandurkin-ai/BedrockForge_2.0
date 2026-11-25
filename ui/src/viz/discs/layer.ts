/**
 * The discs of a structural trend in the main 3D (ТЗ 7.35 Д-15; ADR 0026 решение 27) — a scene layer (`Scene.addLayer`,
 * after `viz/ellipsoid/layer.ts`): one `InstancedMesh` of a unit circle under the matrix of every disc (`build.ts`),
 * translucent, seen from both sides, clipped by the section window as the rest. The colour — the trend's. Float32 from the
 * integer origin of the layer. Counted in «Вписать» (`bbox`).
 */
import * as THREE from "three";
import type { SceneLayer } from "../sceneTypes";
import type { DiscBuild } from "./build";

export interface DiscLayerContext {
  /** the shared clipping planes of the scene (the section window) */
  clip: THREE.Plane[];
}

/** The look of the layer. */
export const LOOK = { opacity: 0.55, segments: 20 } as const;

export class DiscLayer implements SceneLayer {
  readonly object = new THREE.Group();
  readonly origin: { x: number; y: number; z: number };
  readonly bbox: number[] | null;
  private readonly mesh: THREE.InstancedMesh;
  private readonly material: THREE.MeshBasicMaterial;
  private readonly geometry = new THREE.CircleGeometry(1, LOOK.segments);

  constructor(
    readonly build: DiscBuild,
    color: string,
    ctx: DiscLayerContext,
  ) {
    this.origin = build.origin;
    this.bbox = build.bbox;
    this.material = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: LOOK.opacity, side: THREE.DoubleSide, depthWrite: false, clippingPlanes: ctx.clip });
    this.mesh = new THREE.InstancedMesh(this.geometry, this.material, Math.max(build.count, 1));
    this.mesh.count = build.count;
    (this.mesh.instanceMatrix.array as Float32Array).set(build.matrices);
    this.mesh.instanceMatrix.needsUpdate = true;
    this.mesh.frustumCulled = false; // the bounding sphere of the unit circle is not the extent of the instances
    this.mesh.renderOrder = 6;
    this.object.add(this.mesh);
  }

  setColor(color: string): void {
    this.material.color.set(color);
  }

  /** For the tests and the E2E hook: what is drawn. */
  stats(): { count: number; skipped: number; radius: number; color: string; opacity: number } {
    return { count: this.mesh.count, skipped: this.build.skipped, radius: this.build.radius, color: `#${this.material.color.getHexString()}`, opacity: this.material.opacity };
  }

  dispose(): void {
    this.mesh.dispose();
    this.geometry.dispose();
    this.material.dispose();
  }
}
