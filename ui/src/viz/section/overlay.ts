/**
 * What the section draws over the content (BF `updateSectionLine` 2404, `updateDraft` 2575): the line and
 * the edges of its window in Cobalt while clipping is off, and the orange rubber band of a line being drawn
 * with crosses at its ends, sized in pixels. Drawn on top, never clipped. Scene coordinates (under the
 * scaled root): the overlay lies just above the content.
 */
import * as THREE from "three";
import { DRAFT_COLOR, SECTION_COLOR } from "../holes/colors";
import { boxSize, type Box3 } from "../core/bounds";
import { sectionDirection, sectionNormal, type SectionLine } from "./math";
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
  setLine(line: SectionLine | null, clip: boolean, origin: { x: number; y: number } | null, content: Box3 | null): void {
    this.drop("line");
    if (!line || clip || !origin || !content) return;
    const along = sectionDirection(line.az);
    const n = sectionNormal(line.az);
    const size = boxSize(content);
    const reach = line.length ?? Math.max(size[0], size[1]) * 0.75 + 50;
    const start = line.length === null ? -reach : 0;
    const x0 = line.x0 - origin.x;
    const y0 = line.y0 - origin.y;
    const z = content.max[2] + 2;
    const pts: number[] = [];
    for (const d of [0, -line.away, line.towards]) {
      for (const u of [start, reach]) pts.push(x0 + along[0] * u + n[0] * d, y0 + along[1] * u + n[1] * d, z);
    }
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
