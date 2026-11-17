/**
 * Picking and the selected hole (ТЗ 7.13, ADR 0007 §7; BF `nearestCollar` 2512, `applySelected` 2384):
 * a click takes the nearest collar within 8 px, else the nearest trace segment within 6 px, over the visible
 * sets and parts near the cursor, on the CPU in float64 (≤ 100 ms). Points outside the section window do
 * not count. The selected hole is drawn over its trace in orange, with a ring at the collar.
 */
import * as THREE from "three";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import { SELECT_COLOR } from "./colors";
import type { HoleSetLayer } from "./holeSet";
import { boxFromBbox6 } from "../core/bounds";
import { boxNear, COLLAR_PICK_PX, nearestPoint, nearestSegment, TRACE_PICK_PX } from "../pick/holes";
import { multiply, partModel, type Mat4 } from "../pick/ray";

export interface HoleHit {
  setId: number;
  /** index in the manifest of the set */
  index: number;
  name: string;
  on: "collar" | "trace";
}

/** `inWindow(x, y)` — rendered-space plan coordinates (the clipping planes are vertical). */
export function pickHoles(
  layers: Iterable<HoleSetLayer>,
  vp: Mat4,
  vexag: number,
  px: number,
  py: number,
  w: number,
  h: number,
  inWindow: (x: number, y: number) => boolean,
): HoleHit | null {
  const list = [...layers].filter((l) => l.visibility.set);
  let best: { hit: HoleHit; d: number } | null = null;
  for (const layer of list) {
    if (!layer.visibility.collars) continue;
    for (const p of layer.parts) {
      const m = multiply(vp, partModel(p.offset, vexag));
      const pos = p.collars.positions;
      const hit = nearestPoint(m, pos, p.collars.count, px, py, w, h, best ? best.d : COLLAR_PICK_PX, (s) => inWindow(pos[s * 3] + p.offset[0], pos[s * 3 + 1] + p.offset[1]));
      if (hit) {
        const index = layer.manifest.byPart[p.part.k][hit.index];
        best = { hit: { setId: layer.setId, index, name: layer.manifest.names[index], on: "collar" }, d: hit.distance };
      }
    }
  }
  if (best) return best.hit;
  for (const layer of list) {
    if (!layer.visibility.paths) continue;
    for (const p of layer.parts) {
      if (!p.traces) continue;
      const m = multiply(vp, partModel(p.offset, vexag));
      const o = p.part.origin;
      const local = boxFromBbox6(p.part.bbox, { x: o[0], y: o[1], z: o[2] });
      if (!boxNear(m, local, px, py, w, h, TRACE_PICK_PX)) continue;
      for (const g of p.traces.built.groups) {
        const pos = g.positions;
        const hit = nearestSegment(m, pos, g.slots.length, px, py, w, h, best ? best.d : TRACE_PICK_PX, (s, t) => {
          const x = pos[s * 6] + (pos[s * 6 + 3] - pos[s * 6]) * t + p.offset[0];
          const y = pos[s * 6 + 1] + (pos[s * 6 + 4] - pos[s * 6 + 1]) * t + p.offset[1];
          return inWindow(x, y);
        });
        if (hit) {
          const index = layer.manifest.byPart[p.part.k][g.slots[hit.segment]];
          best = { hit: { setId: layer.setId, index, name: layer.manifest.names[index], on: "trace" }, d: hit.distance };
        }
      }
    }
  }
  return best?.hit ?? null;
}

/** The highlight of the selected hole: an orange line over its trace and a ring at the collar. */
export class SelectedHole {
  private objects: THREE.Object3D[] = [];
  private parent: THREE.Object3D | null = null;

  constructor(
    private readonly clip: THREE.Plane[],
    private readonly resolution: THREE.Vector2,
  ) {}

  /** Shows the hole `index` of `layer` (null — nothing selected). */
  update(layer: HoleSetLayer | null, index: number, pixelRatio: number, collarSize: number, marker: THREE.Texture): void {
    this.clear();
    if (!layer || index < 0) return;
    const m = layer.manifest;
    const k = m.part[index];
    const slot = m.slot[index];
    const p = layer.parts[k];
    if (!p) return;
    const objects: THREE.Object3D[] = [];
    const built = p.traces?.built;
    if (built) {
      const a = built.offsets[slot];
      const b = built.offsets[slot + 1];
      if (b - a >= 2) {
        const positions = new Float32Array((b - a - 1) * 6);
        for (let i = a; i + 1 < b; i++) positions.set(built.xyz.subarray(i * 3, i * 3 + 6), (i - a) * 6);
        const geometry = new LineSegmentsGeometry();
        geometry.setPositions(positions);
        const material = new LineMaterial({ color: SELECT_COLOR, linewidth: 5, worldUnits: false });
        material.clippingPlanes = this.clip;
        material.uniforms.resolution.value = this.resolution;
        const line = new LineSegments2(geometry, material);
        line.renderOrder = 1;
        objects.push(line);
      }
    }
    const ring = new THREE.BufferGeometry();
    ring.setAttribute("position", new THREE.BufferAttribute(p.collars.positions.slice(slot * 3, slot * 3 + 3), 3));
    const mat = new THREE.PointsMaterial({ color: SELECT_COLOR, size: (collarSize + 8) * pixelRatio, sizeAttenuation: false, map: marker, transparent: true, alphaTest: 0.4, depthTest: false });
    mat.clippingPlanes = this.clip;
    const pt = new THREE.Points(ring, mat);
    pt.renderOrder = 2;
    objects.push(pt);
    for (const o of objects) p.group.add(o);
    this.objects = objects;
    this.parent = p.group;
  }

  clear(): void {
    for (const o of this.objects) {
      this.parent?.remove(o);
      const mesh = o as THREE.Mesh;
      mesh.geometry?.dispose();
      (mesh.material as THREE.Material | undefined)?.dispose();
    }
    this.objects = [];
    this.parent = null;
  }
}
