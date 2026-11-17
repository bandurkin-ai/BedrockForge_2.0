/**
 * Clipping by the section window (ТЗ 7.14, ADR 0007 §4, variant Б): one shared array of `THREE.Plane`
 * given to the materials of the clipped layers — holes, displays, labels, later lines, blocks and
 * wireframes. The array is changed in place, so every material follows (three.js recompiles a program
 * when the number of planes changes). Not clipped: the section line, the mini-plan, the gizmo, the grid.
 * The planes are vertical, computed in float64 from the scene origin by the pure `clipPlanes`.
 */
import * as THREE from "three";
import { clipPlanes, type SectionLine } from "./math";
import type { Plane } from "../core/vec3";

export class SharedClip {
  readonly planes: THREE.Plane[] = [];
  private math: Plane[] = [];

  /** The planes of the window when clipping is on, none otherwise. */
  update(line: SectionLine | null, clip: boolean, origin: { x: number; y: number } | null): void {
    this.math = clip && line && origin ? clipPlanes(line, origin) : [];
    this.planes.length = 0;
    for (const p of this.math) this.planes.push(new THREE.Plane(new THREE.Vector3(p.normal[0], p.normal[1], p.normal[2]), p.constant));
  }

  get active(): boolean {
    return this.math.length > 0;
  }

  /** Is a rendered-space plan point inside the window (float64)? Everything is, without clipping. */
  inWindow(x: number, y: number): boolean {
    for (const p of this.math) if (p.normal[0] * x + p.normal[1] * y + p.constant < 0) return false;
    return true;
  }
}
