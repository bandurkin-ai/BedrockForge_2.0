/**
 * What the section draws over the content (BF `updateSectionLine` 2404, `updateDraft` 2575): the trace of the
 * section and the edges of its window in Cobalt while clipping is off, and the orange rubber band of a line being
 * drawn with crosses at its ends, sized in pixels. Drawn on top, never clipped. Scene coordinates (under the
 * scaled root). A vertical plane (a line of 7.14, a vertical series, a polyline segment) — its line and the two
 * faces of the window just above the content, as in 7.14; a plan or an inclined section — the outlines of the
 * plane and of the faces over the extent of the content.
 */
import * as THREE from "three";
import { DRAFT_COLOR, SECTION_COLOR } from "../holes/colors";
import { boxSize, type Box3 } from "../core/bounds";
import { isVertical, planePoint, type PlaneOrigin, type SectionPlane } from "./plane";
import type { Vec3 } from "../core/vec3";

function segments(points: number[], color: string, opacity: number): THREE.LineSegments {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(points), 3));
  const line = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color, depthTest: false, transparent: true, opacity }));
  line.renderOrder = 10;
  line.frustumCulled = false;
  return line;
}

export class SectionOverlay {
  readonly group = new THREE.Group();
  private line: THREE.LineSegments | null = null;
  private draft: THREE.LineSegments | null = null;

  /** The trace of the section with its window (shown while clipping is off). */
  setLine(plane: SectionPlane | null, clip: boolean, origin: PlaneOrigin | null, content: Box3 | null): void {
    this.drop("line");
    if (!plane || clip || !origin || !content) return;
    const pts = isVertical(plane) ? verticalTrace(plane, origin, content) : planeOutlines(plane, origin, content);
    this.line = segments(pts, SECTION_COLOR, 0.9);
    this.group.add(this.line);
  }

  /**
   * The rubber band from `start` to `end` (scene coordinates, the end at the height of the start) with
   * crosses `mark` scene units wide; null clears it.
   */
  setDraft(start: Vec3 | null, end: Vec3 | null, mark: number): void {
    this.drop("draft");
    if (!start) return;
    const ends: Vec3[] = end ? [start, [end[0], end[1], start[2]]] : [start];
    const pts: number[] = [];
    if (end) pts.push(...ends[0], ...ends[1]);
    for (const p of ends) {
      pts.push(p[0] - mark, p[1] - mark, p[2], p[0] + mark, p[1] + mark, p[2]);
      pts.push(p[0] - mark, p[1] + mark, p[2], p[0] + mark, p[1] - mark, p[2]);
    }
    this.draft = segments(pts, DRAFT_COLOR, 1);
    this.draft.renderOrder = 11;
    this.group.add(this.draft);
  }

  private drop(which: "line" | "draft"): void {
    const obj = this[which];
    if (!obj) return;
    this.group.remove(obj);
    obj.geometry.dispose();
    (obj.material as THREE.Material).dispose();
    this[which] = null;
  }

  dispose(): void {
    this.drop("line");
    this.drop("draft");
  }
}

/** The line and the faces of a vertical window over the content (7.14): ±reach, or the limits of u. */
function verticalTrace(p: SectionPlane, origin: PlaneOrigin, content: Box3): number[] {
  const size = boxSize(content);
  const reach = Math.max(size[0], size[1]) * 0.75 + 50;
  // the centre of the content along the line: a series starts at P1, which may be far from the data
  const cx = (content.min[0] + content.max[0]) / 2 + origin.x - p.origin[0];
  const cy = (content.min[1] + content.max[1]) / 2 + origin.y - p.origin[1];
  const cu = Math.abs(cx * p.s[0] + cy * p.s[1]) > reach ? cx * p.s[0] + cy * p.s[1] : 0;
  const [start, end] = p.u ? [p.u[0] - p.u0, p.u[1] - p.u0] : [cu - reach, cu + reach];
  const n: Vec3 = [-p.v[0], -p.v[1], 0];
  const x0 = p.origin[0] - origin.x;
  const y0 = p.origin[1] - origin.y;
  const z = content.max[2] + 2;
  const pts: number[] = [];
  for (const d of [0, -p.away, p.towards]) {
    for (const u of [start, end]) pts.push(x0 + p.s[0] * u + n[0] * d, y0 + p.s[1] * u + n[1] * d, z);
  }
  return pts;
}

/** Rectangles of the plane t = 0 and of the faces t = −towards, t = away over the extent of the content (u, w). */
function planeOutlines(p: SectionPlane, origin: PlaneOrigin, content: Box3): number[] {
  const oz = origin.z ?? 0;
  let u0 = Infinity;
  let u1 = -Infinity;
  let w0 = Infinity;
  let w1 = -Infinity;
  for (let i = 0; i < 8; i++) {
    const d: Vec3 = [(i & 1 ? content.max[0] : content.min[0]) + origin.x - p.origin[0], (i & 2 ? content.max[1] : content.min[1]) + origin.y - p.origin[1], (i & 4 ? content.max[2] : content.min[2]) + oz - p.origin[2]];
    const u = d[0] * p.s[0] + d[1] * p.s[1] + d[2] * p.s[2];
    const w = d[0] * p.w[0] + d[1] * p.w[1] + d[2] * p.w[2];
    u0 = Math.min(u0, u);
    u1 = Math.max(u1, u);
    w0 = Math.min(w0, w);
    w1 = Math.max(w1, w);
  }
  const m = Math.max(u1 - u0, w1 - w0) * 0.05 + 5;
  const pts: number[] = [];
  for (const t of [0, -p.towards, p.away]) {
    const corner = (u: number, w: number): Vec3 => {
      const q = planePoint(p, u + p.u0, w);
      return [q[0] + t * p.v[0] - origin.x, q[1] + t * p.v[1] - origin.y, q[2] + t * p.v[2] - oz];
    };
    const c = [corner(u0 - m, w0 - m), corner(u1 + m, w0 - m), corner(u1 + m, w1 + m), corner(u0 - m, w1 + m)];
    for (let i = 0; i < 4; i++) pts.push(...c[i], ...c[(i + 1) % 4]);
  }
  return pts;
}
