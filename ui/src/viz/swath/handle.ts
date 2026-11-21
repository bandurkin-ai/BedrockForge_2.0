/**
 * The handles of a swath box in the main 3D (ТЗ 7.35 Д-01; ADR 0019 §6) — a scene layer (`Scene.addLayer`): the
 * ring of the azimuth, the half circle of the dip and the circle of the roll (`drag.ts`), each with a knob at its
 * angle now. Drawn over everything (no depth test, not clipped by the section window: a handle is never hidden),
 * not counted in «Вписать» (no `bbox`). Cobalt; the handle under the cursor and the one dragged — Copper Orange,
 * the others dimmed while a drag lasts (docs/brand). Buffers float32 from the integer origin of the layer (the
 * centre of the cuboid rounded, `viz/origin.ts`); a new position is a new small geometry (≈ 200 segments).
 */
import * as THREE from "three";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import type { Vec3 } from "../core/vec3";
import type { SceneLayer } from "../sceneTypes";
import { HANDLE_KINDS, pointAt, polyline, type Handle, type HandleKind } from "./drag";

const COBALT = 0x1f6bff;
const COPPER = 0xf46a1a;

interface Drawn {
  line: LineSegments2;
  material: LineMaterial;
  knob: THREE.Points;
}

export class SwathHandles implements SceneLayer {
  readonly object = new THREE.Group();
  readonly origin: { x: number; y: number; z: number };
  readonly bbox = null;
  private readonly drawn = new Map<HandleKind, Drawn>();
  /** what the last `update` drew, for the tests and the E2E hook */
  shown: { kind: HandleKind; at: number; lit: boolean }[] = [];

  /** `centre` — absolute, the integer origin is taken from it; `resolution` — the shared size of the view. */
  constructor(centre: Vec3, resolution: THREE.Vector2) {
    this.origin = { x: Math.round(centre[0]), y: Math.round(centre[1]), z: Math.round(centre[2]) };
    this.object.renderOrder = 40;
    for (const kind of HANDLE_KINDS) {
      const material = new LineMaterial({ color: COBALT, linewidth: 2.2, worldUnits: false, transparent: true, opacity: 0.95, depthTest: false, depthWrite: false });
      material.uniforms.resolution.value = resolution;
      const line = new LineSegments2(new LineSegmentsGeometry(), material);
      line.renderOrder = 40;
      line.frustumCulled = false;
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(3), 3));
      const knob = new THREE.Points(g, new THREE.PointsMaterial({ color: COBALT, size: 11, sizeAttenuation: false, transparent: true, depthTest: false, depthWrite: false }));
      knob.renderOrder = 41;
      knob.frustumCulled = false;
      this.object.add(line, knob);
      this.drawn.set(kind, { line, material, knob });
    }
  }

  private local(p: Vec3): [number, number, number] {
    return [p[0] - this.origin.x, p[1] - this.origin.y, p[2] - this.origin.z];
  }

  /** The handles where they are now; `hover` — under the cursor, `active` — being dragged (the others dimmed). */
  update(handles: readonly Handle[], hover: HandleKind | null, active: HandleKind | null): void {
    this.shown = [];
    for (const h of handles) {
      const d = this.drawn.get(h.kind);
      if (!d) continue;
      const pts = polyline(h).map((p) => this.local(p));
      const pos = new Float32Array((pts.length - 1) * 6);
      for (let i = 0; i + 1 < pts.length; i++) pos.set([...pts[i], ...pts[i + 1]], i * 6);
      d.line.geometry.dispose();
      d.line.geometry = new LineSegmentsGeometry().setPositions(pos);
      const knob = d.knob.geometry.getAttribute("position") as THREE.BufferAttribute;
      knob.set(this.local(pointAt(h, h.at)));
      knob.needsUpdate = true;
      d.knob.geometry.computeBoundingSphere();
      const lit = h.kind === active || (active === null && h.kind === hover);
      const color = lit ? COPPER : COBALT;
      const opacity = active !== null && !lit ? 0.35 : 0.95;
      d.material.color.setHex(color);
      d.material.linewidth = lit ? 3.4 : 2.2;
      d.material.opacity = opacity;
      const pm = d.knob.material as THREE.PointsMaterial;
      pm.color.setHex(color);
      pm.opacity = opacity;
      this.shown.push({ kind: h.kind, at: h.at, lit });
    }
  }

  dispose(): void {
    for (const d of this.drawn.values()) {
      d.line.geometry.dispose();
      d.material.dispose();
      d.knob.geometry.dispose();
      (d.knob.material as THREE.Material).dispose();
    }
    this.drawn.clear();
  }
}
