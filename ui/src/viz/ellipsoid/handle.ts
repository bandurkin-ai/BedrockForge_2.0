/**
 * The six handles of an anisotropy ellipsoid in the main 3D (ТЗ 7.35 Д-14; ADR 0022 §9, решение 18; образец —
 * `viz/swath/handle.ts`, Д-01) — a scene layer (`Scene.addLayer`): the ring of the azimuth (ang1), the half circle
 * of the plunge (ang2), the circle of the turn about the major axis at its end (ang3), each with a knob at its angle
 * now; the knobs at the ends of the axes a1, a2, a3 on thin lines of the axes from the centre (`drag.handlesOf`).
 * Drawn over everything (no depth test, not clipped by the section window: a handle is never hidden), not counted
 * in «Вписать» (no `bbox`). Cobalt; the handle under the cursor and the one dragged — Copper Orange, the others
 * dimmed while a drag lasts (docs/brand). Buffers float32 from the integer origin of the layer (the centre rounded,
 * `viz/origin.ts`); a new position is a new small geometry (≈ 250 segments).
 */
import * as THREE from "three";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import type { Vec3 } from "../core/vec3";
import type { SceneLayer } from "../sceneTypes";
import { BRAND } from "./build";
import { HANDLE_KINDS, isArc, pointAt, polyline, type Handle, type HandleKind } from "./drag";

const COBALT = new THREE.Color(BRAND.cobalt).getHex();
const COPPER = new THREE.Color(BRAND.copper).getHex();

/** Widths, px, and opacities of the handles. */
export const HANDLE_LOOK = { arc: 2.2, arcLit: 3.4, axis: 1.2, axisLit: 2, knob: 11, knobLit: 13, opacity: 0.95, dimmed: 0.35 } as const;

interface Drawn {
  line: LineSegments2;
  material: LineMaterial;
  knob: THREE.Points;
}

/** The segment pairs of a handle as drawn, from an origin: the polyline of an arc, the axis line of a knob. */
export function handleSegments(h: Handle, origin: Vec3): Float32Array {
  const local = (p: Vec3): number[] => [p[0] - origin[0], p[1] - origin[1], p[2] - origin[2]];
  const pts = isArc(h) ? polyline(h) : [h.centre, h.at];
  const pos = new Float32Array((pts.length - 1) * 6);
  for (let i = 0; i + 1 < pts.length; i++) pos.set([...local(pts[i]), ...local(pts[i + 1])], i * 6);
  return pos;
}

/** The point of the knob of a handle (absolute): the angle now on an arc, the end of an axis. */
export const knobOf = (h: Handle): Vec3 => (isArc(h) ? pointAt(h, h.at) : h.at);

export class EllipsoidHandles implements SceneLayer {
  readonly object = new THREE.Group();
  readonly origin: { x: number; y: number; z: number };
  readonly bbox = null;
  private readonly drawn = new Map<HandleKind, Drawn>();
  /** what the last `update` drew, for the tests and the E2E hook */
  shown: { kind: HandleKind; at: number | Vec3; lit: boolean; dimmed: boolean }[] = [];

  /** `centre` — absolute, the integer origin is taken from it; `resolution` — the shared size of the view. */
  constructor(centre: Vec3, resolution: THREE.Vector2) {
    this.origin = { x: Math.round(centre[0]), y: Math.round(centre[1]), z: Math.round(centre[2]) };
    this.object.renderOrder = 40;
    for (const kind of HANDLE_KINDS) {
      const arc = kind.startsWith("ang");
      const material = new LineMaterial({ color: COBALT, linewidth: arc ? HANDLE_LOOK.arc : HANDLE_LOOK.axis, worldUnits: false, transparent: true, opacity: HANDLE_LOOK.opacity, depthTest: false, depthWrite: false });
      material.uniforms.resolution.value = resolution;
      const line = new LineSegments2(new LineSegmentsGeometry(), material);
      line.renderOrder = 40;
      line.frustumCulled = false;
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(3), 3));
      const knob = new THREE.Points(g, new THREE.PointsMaterial({ color: COBALT, size: HANDLE_LOOK.knob, sizeAttenuation: false, transparent: true, depthTest: false, depthWrite: false }));
      knob.renderOrder = 41;
      knob.frustumCulled = false;
      line.visible = knob.visible = false;
      this.object.add(line, knob);
      this.drawn.set(kind, { line, material, knob });
    }
  }

  private get base(): Vec3 {
    return [this.origin.x, this.origin.y, this.origin.z];
  }

  /**
   * The handles where they are now (`drag.handlesOf`); `hover` — under the cursor, `active` — being dragged (the
   * others dimmed). A kind not given is hidden.
   */
  update(handles: readonly Handle[], hover: HandleKind | null, active: HandleKind | null): void {
    this.shown = [];
    const given = new Set<HandleKind>();
    for (const h of handles) {
      const d = this.drawn.get(h.kind);
      if (!d) continue;
      given.add(h.kind);
      const arc = isArc(h);
      d.line.geometry.dispose();
      d.line.geometry = new LineSegmentsGeometry().setPositions(handleSegments(h, this.base));
      const k = knobOf(h);
      const attr = d.knob.geometry.getAttribute("position") as THREE.BufferAttribute;
      attr.set([k[0] - this.origin.x, k[1] - this.origin.y, k[2] - this.origin.z]);
      attr.needsUpdate = true;
      d.knob.geometry.computeBoundingSphere();
      const lit = h.kind === active || (active === null && h.kind === hover);
      const dimmed = active !== null && !lit;
      const color = lit ? COPPER : COBALT;
      const opacity = dimmed ? HANDLE_LOOK.dimmed : HANDLE_LOOK.opacity;
      d.material.color.setHex(color);
      d.material.linewidth = arc ? (lit ? HANDLE_LOOK.arcLit : HANDLE_LOOK.arc) : lit ? HANDLE_LOOK.axisLit : HANDLE_LOOK.axis;
      d.material.opacity = opacity;
      const pm = d.knob.material as THREE.PointsMaterial;
      pm.color.setHex(color);
      pm.opacity = opacity;
      pm.size = lit ? HANDLE_LOOK.knobLit : HANDLE_LOOK.knob;
      d.line.visible = d.knob.visible = true;
      this.shown.push({ kind: h.kind, at: arc ? h.at : [...h.at] as Vec3, lit, dimmed });
    }
    for (const [kind, d] of this.drawn) if (!given.has(kind)) d.line.visible = d.knob.visible = false;
  }

  setVisible(on: boolean): void {
    this.object.visible = on;
  }

  dispose(): void {
    for (const d of this.drawn.values()) {
      d.line.geometry.dispose();
      d.material.dispose();
      d.knob.geometry.dispose();
      (d.knob.material as THREE.Material).dispose();
    }
    this.drawn.clear();
    this.object.clear();
  }
}
