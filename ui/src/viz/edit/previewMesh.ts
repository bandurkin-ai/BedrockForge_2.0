/**
 * The preview of «Собрать из контуров» in the 3D window (ADR 0015 §6 «Предпросмотр»; `SceneLayer`, key
 * `assemble`): the triangles the core stitched from the working copy — a translucent fill and its edges, so the
 * wireframe is seen growing from contour to contour. The coordinates come from the core already as float32
 * offsets from its whole-metre origin (`POST /geometry/stitch/preview`, BFA1 `mesh`: `xyz` <f4, `idx` <u4; ТЗ 5.4,
 * `viz/origin.ts`) and are drawn as they are; nothing here measures. Clipped like the rest of the scene.
 */
import * as THREE from "three";
import type { Origin } from "../origin";
import type { SceneLayer } from "../sceneTypes";

/** One preview: float32 offsets from `origin` (whole metres) and the corners of the triangles. */
export interface PreviewMeshData {
  origin: Origin;
  xyz: Float32Array;
  idx: Uint32Array;
}

export interface PreviewColors {
  fill: string;
  edge: string;
}

const ORDER = 40;

/** [xmin, ymin, zmin, xmax, ymax, zmax] in project coordinates; null — empty. */
export function previewBbox(d: PreviewMeshData): number[] | null {
  if (d.xyz.length < 3) return null;
  const lo = [Infinity, Infinity, Infinity];
  const hi = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < d.xyz.length; i += 3) {
    for (let k = 0; k < 3; k++) {
      const v = d.xyz[i + k];
      if (v < lo[k]) lo[k] = v;
      if (v > hi[k]) hi[k] = v;
    }
  }
  const o = [d.origin.x, d.origin.y, d.origin.z];
  return [lo[0] + o[0], lo[1] + o[1], lo[2] + o[2], hi[0] + o[0], hi[1] + o[1], hi[2] + o[2]];
}

export class PreviewMesh implements SceneLayer {
  readonly object = new THREE.Group();
  origin: Origin;
  bbox: number[] | null;
  private readonly fill: THREE.Mesh;
  private readonly edges: THREE.LineSegments;

  constructor(data: PreviewMeshData, clip: THREE.Plane[], colors: PreviewColors) {
    this.origin = { ...data.origin };
    this.bbox = previewBbox(data);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(data.xyz, 3));
    geometry.setIndex(new THREE.BufferAttribute(data.idx, 1));
    const common = { clippingPlanes: clip, transparent: true, depthWrite: false };
    this.fill = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ ...common, color: new THREE.Color(colors.fill), opacity: 0.28, side: THREE.DoubleSide }));
    this.fill.renderOrder = ORDER;
    this.fill.frustumCulled = false;
    this.edges = new THREE.LineSegments(new THREE.WireframeGeometry(geometry), new THREE.LineBasicMaterial({ ...common, color: new THREE.Color(colors.edge), opacity: 0.9 }));
    this.edges.renderOrder = ORDER + 1;
    this.edges.frustumCulled = false;
    this.object.add(this.fill, this.edges);
  }

  dispose(): void {
    this.fill.geometry.dispose();
    (this.fill.material as THREE.Material).dispose();
    this.edges.geometry.dispose();
    (this.edges.material as THREE.Material).dispose();
  }
}
