/**
 * Clipping by the section window (ТЗ 7.14, 8.1; ADR 0007 §4 variant Б, ADR 0016 §4): one shared array of
 * `THREE.Plane` given to the materials of the clipped layers — holes, displays, labels, lines, wireframes. The
 * array is changed in place, so every material follows (three.js recompiles a program when the number of planes
 * changes). Not clipped: the section line, the mini-plan, the gizmo, the grid.
 *
 * The planes of a `SectionPlane` (a line of 7.14, a section of a series, a polyline segment) are computed in
 * float64 from the scene origin (`windowPlanes`) and put into rendered space with the exaggeration
 * (`renderedPlane`: a vertical plane stays the same bit for bit). `inWindow(x, y, z)` answers on the CPU in
 * scene coordinates (relative to the scene origin, Z not stretched), the same window as the video card.
 */
import * as THREE from "three";
import type { Box3, WindowTest } from "../core/bounds";
import type { Plane } from "../core/vec3";
import { insidePlanes, renderedPlane, windowPlanes, type PlaneOrigin, type SectionPlane } from "./plane";

export class SharedClip {
  readonly planes: THREE.Plane[] = [];
  /** scene coordinates */
  private math: Plane[] = [];
  /** the faces and the ends, without the limits of u (as BF windowTest: «Вписать» of a drawn line) */
  private faces: Plane[] = [];

  /** The planes of the window when clipping is on, none otherwise. */
  update(plane: SectionPlane | null, clip: boolean, origin: PlaneOrigin | null, vexag = 1): void {
    this.math = clip && plane && origin ? windowPlanes(plane, origin) : [];
    this.faces = plane && !plane.ends.length && plane.u ? this.math.slice(0, 2) : this.math;
    this.planes.length = 0;
    for (const p of this.math) {
      const r = renderedPlane(p, vexag);
      this.planes.push(new THREE.Plane(new THREE.Vector3(r.normal[0], r.normal[1], r.normal[2]), r.constant));
    }
  }

  get active(): boolean {
    return this.math.length > 0;
  }

  /** Is a scene point inside the window (float64, NaN — not)? Everything is, without clipping. */
  inWindow(x: number, y: number, z: number): boolean {
    return insidePlanes(this.math, x, y, z);
  }

  /** Does a scene box reach into the window (every face holds one of its corners); null — nothing is clipped. */
  boxTest(): WindowTest | null {
    const faces = this.faces;
    if (!faces.length) return null;
    return (box: Box3) => faces.every((p) => {
      let max = -Infinity;
      for (let i = 0; i < 8; i++) {
        const x = i & 1 ? box.max[0] : box.min[0];
        const y = i & 2 ? box.max[1] : box.min[1];
        const z = i & 4 ? box.max[2] : box.min[2];
        max = Math.max(max, p.normal[0] * x + p.normal[1] * y + p.normal[2] * z + p.constant);
      }
      return max >= 0;
    });
  }
}
