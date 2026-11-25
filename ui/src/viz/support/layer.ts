/**
 * The map of the support of a surface in the 3D window (ТЗ 7.35 Д-32 п. 2; `SceneLayer`, key `implicit-support`): the surface of an implicit
 * model painted by the distance of its vertices to the nearest data point. The geometry is that of the part of the wireframe (float32 from
 * the integer origin of the part, `viz/mesh/build.ts`), the colours — one per vertex, in the order of the vertices; `setColors` paints it
 * again without touching the geometry (the threshold D_оп moved). Drawn unlit in front of the surface of the wireframe (the polygon offset
 * of the red layer of the checks), clipped like the rest of the scene. Display only: the numbers are the core's float64.
 */
import * as THREE from "three";
import type { Origin } from "../origin";
import type { SceneLayer } from "../sceneTypes";

export interface SupportMeshData {
  /** absolute integer origin of `positions` */
  origin: Origin;
  /** xyz of the vertices from the origin */
  positions: Float32Array;
  /** three vertices per triangle */
  index: Uint16Array | Uint32Array;
  /** absolute [xmin, ymin, zmin, xmax, ymax, zmax]; null — none */
  bbox: number[] | null;
}

export class SupportView implements SceneLayer {
  readonly object = new THREE.Group();
  readonly origin: Origin;
  readonly bbox: number[] | null;
  /** the vertices painted */
  readonly vertices: number;
  private readonly geometry = new THREE.BufferGeometry();
  private readonly material: THREE.MeshBasicMaterial;
  private readonly color: THREE.BufferAttribute;

  constructor(data: SupportMeshData, rgb: Float32Array, clip: THREE.Plane[]) {
    this.origin = data.origin;
    this.bbox = data.bbox;
    this.vertices = data.positions.length / 3;
    if (rgb.length !== data.positions.length) throw new Error("support: a colour for every vertex");
    this.geometry.setAttribute("position", new THREE.BufferAttribute(data.positions, 3));
    this.color = new THREE.BufferAttribute(rgb, 3);
    this.color.setUsage(THREE.DynamicDrawUsage);
    this.geometry.setAttribute("color", this.color);
    this.geometry.setIndex(new THREE.BufferAttribute(data.index, 1));
    this.material = new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide });
    this.material.clippingPlanes = clip;
    this.material.polygonOffset = true; // in front of the surface of the wireframe itself (pushed back by +1)
    this.material.polygonOffsetFactor = -2;
    this.material.polygonOffsetUnits = -2;
    const mesh = new THREE.Mesh(this.geometry, this.material);
    mesh.frustumCulled = false;
    mesh.renderOrder = 3;
    this.object.add(mesh);
  }

  /** The vertices painted again: `rgb` is the array of the layer or a new one of its length. */
  setColors(rgb: Float32Array): void {
    if (rgb.length !== this.color.array.length) throw new Error("support: a colour for every vertex");
    if (rgb !== this.color.array) (this.color.array as Float32Array).set(rgb);
    this.color.needsUpdate = true;
  }

  dispose(): void {
    this.geometry.dispose();
    this.material.dispose();
    this.object.clear();
  }
}
