/**
 * Where a new point falls (ADR 0015 §3, ADR 0016 §7; moved out of `session.ts`): in plan — on the Z of the panel;
 * with a line of 7.14 clipped — on the vertical plane in the middle of its window (as in Э9); with a section of a
 * series clipped — on its plane t = 0. The cursor ray meets the plane in float64 on the CPU from O_k, then the
 * point is put back on the plane, p −= ((p − O_k)·v̂)·v̂ (`projectToPlane`): ≤ 1e−9 m from it on UTM (Т8.1-3).
 * A snap target off the plane is projected into it along v̂ unless «оставить 3D» (`snap.project` off). No three.js.
 */
import type { SectionLine } from "../viz/section/math";
import { planeLocal, type SectionPlane } from "../viz/section/plane";
import { horizontalPlane, projectToPlane, rayPlane, sectionPlane, type Plane, type Ray } from "./coords";
import type { Vec3 } from "./model";
import { gridSnap, PROJECTED_KINDS, SNAP_TITLES, type SnapHit } from "./snap";
import type { SnapSettings } from "./tools";

/** What the drawing plane is made of: the state of the session. */
export interface DrawContext {
  /** the free line of 7.14 when it is the section */
  section: SectionLine | null;
  /** the section of a series (its plane t = 0 and window), null — none */
  plane: SectionPlane | null;
  clip: boolean;
  /** the Z of the panel: the plane in plan */
  zLevel: number;
}

/** A target of the own kinds (vertex, midpoint, on a line) this close to the plane of a series keeps its bits. */
export const ON_PLANE_M = 1e-6;

/** The section of a series new points fall on, null — none (no series, or clipping off). */
export function seriesPlane(c: DrawContext): SectionPlane | null {
  return c.clip && c.plane ? c.plane : null;
}

/** Points fall on a section (of a line or of a series), not on the plan level. */
export function onSection(c: DrawContext): boolean {
  return c.clip && !!(c.plane || c.section);
}

/** t = 0 of a section as a plane of the editor: through O_k, the normal −v̂ (towards the viewer, as of 7.14). */
export function sectionZeroPlane(p: SectionPlane): Plane {
  return { point: [p.origin[0], p.origin[1], p.origin[2]], normal: [-p.v[0] || 0, -p.v[1] || 0, -p.v[2] || 0] };
}

/** The plane new points fall on. */
export function drawingPlane(c: DrawContext): Plane {
  const sp = seriesPlane(c);
  if (sp) return sectionZeroPlane(sp);
  return c.clip && c.section ? sectionPlane(c.section) : horizontalPlane(c.zLevel);
}

/** t of a point from the plane of the section (`planeLocal`): the distance along v̂. */
export function planeDepth(p: SectionPlane, xyz: Vec3): number {
  return planeLocal(p, xyz)[2];
}

/** The point of a snap target: projected into the section plane when it lies off it and «оставить 3D» is off. */
export function snapPoint(hit: SnapHit, c: DrawContext, project: boolean): Vec3 {
  if (!project || !onSection(c)) return hit.point;
  const plane = drawingPlane(c);
  const sp = seriesPlane(c);
  if (!sp) return PROJECTED_KINDS.has(hit.kind) ? projectToPlane(hit.point, plane) : hit.point;
  const p = hit.point;
  // a target without a height cannot be put on a plane that is not vertical
  if (!Number.isFinite(p[2]) && sp.v[2] !== 0) return p;
  if (!PROJECTED_KINDS.has(hit.kind) && !(Math.abs(planeDepth(sp, p)) > ON_PLANE_M)) return p;
  return projectToPlane(p, plane);
}

/**
 * The point a click gives and what it snapped to: the snap target (see `snapPoint`), else the cursor ray on the
 * drawing plane, rounded to the grid node when the grid is on; null — the plane is seen edge-on.
 */
export function placePoint(c: DrawContext, settings: SnapSettings, snap: SnapHit | null, ray: Ray | null): { point: Vec3 | null; snap: SnapHit | null } {
  if (snap) return { point: snapPoint(snap, c, settings.project), snap };
  if (!ray) return { point: null, snap: null };
  const plane = drawingPlane(c);
  let point = rayPlane(ray, plane);
  if (point && settings.enabled && settings.kinds.includes("grid") && settings.grid > 0) {
    point = gridSnap(point, settings.grid, plane);
    // the node of a section of a series is put back on its plane t = 0
    if (seriesPlane(c)) point = projectToPlane(point, plane);
    return { point, snap: { kind: "grid", point, label: SNAP_TITLES.grid, distancePx: 0 } };
  }
  return { point, snap: null };
}
