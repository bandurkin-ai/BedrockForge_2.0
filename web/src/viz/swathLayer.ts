/**
 * A swath plot in the 3D view: the cuboid cut into swaths, and the plot of means standing over
 * them. The plot hangs on the cuboid's axis and turns about it toward the viewer, so it reads the
 * same in plan, in a section and in between; its points stay over the swaths they belong to.
 */
import * as THREE from "three";
import type { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import type { SwathBuild, SwathLabel } from "../stats/swath";

export interface SwathHost {
  /** Line segments of a width in pixels (the scene keeps their materials sized to the view). */
  makeLines(positions: Float32Array, width: number, color: number): LineSegments2;
  labelMaterial(text: string, font: { size: number; family: string; bold: boolean; color: string; halo?: string }): { material: THREE.SpriteMaterial; wpx: number; hpx: number };
  pointTexture: THREE.Texture;
  /** The scene background: the plate under the plot, and whether lines are light or dark. */
  background: THREE.Color;
}

const HIGHLIGHT = 0xffc23d;

/** t, h pairs as x, y, 0. */
function flat(pairs: Float32Array): Float32Array {
  const out = new Float32Array((pairs.length / 2) * 3);
  for (let i = 0, j = 0; i < pairs.length; i += 2, j += 3) {
    out[j] = pairs[i];
    out[j + 1] = pairs[i + 1];
  }
  return out;
}

function quads(rects: number[][]): THREE.BufferGeometry {
  const position = new Float32Array(rects.length * 18);
  rects.forEach(([x0, x1, y0, y1], i) => position.set([x0, y0, 0, x1, y0, 0, x1, y1, 0, x0, y0, 0, x1, y1, 0, x0, y1, 0], i * 18));
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(position, 3));
  return geometry;
}

export class SwathLayer {
  /** Child of the scene root, at the centre of the cuboid. */
  readonly group = new THREE.Group();
  /** The cuboid, relative to the scene origin. */
  readonly extent = new THREE.Box3();
  /** The cuboid with room for the plot on any side of it (the depth range of the camera must hold it). */
  readonly reach = new THREE.Box3();
  private panel = new THREE.Group();
  private labels: { sprite: THREE.Sprite; label: SwathLabel; wpx: number; hpx: number }[] = [];
  private u: THREE.Vector3;
  private v: THREE.Vector3;
  private w: THREE.Vector3;

  constructor(readonly build: SwathBuild, origin: THREE.Vector3, host: SwathHost) {
    const b = build;
    this.u = new THREE.Vector3(...b.u);
    this.v = new THREE.Vector3(...b.v);
    this.w = new THREE.Vector3(...b.w);
    this.group.position.set(b.centre[0] - origin.x, b.centre[1] - origin.y, b.centre[2] - origin.z);
    for (const su of [-1, 1]) for (const sv of [-1, 1]) for (const sw of [-1, 1]) {
      this.extent.expandByPoint(new THREE.Vector3()
        .addScaledVector(this.u, su * b.half[0]).addScaledVector(this.v, sv * b.half[1]).addScaledVector(this.w, sw * b.half[2]).add(this.group.position));
    }
    this.reach.copy(this.extent).expandByScalar(b.chart ? 1.6 * b.chart.height * 2 * Math.max(...b.half) : 0);
    const hsl = { h: 0, s: 0, l: 0 };
    host.background.getHSL(hsl);
    const dark = hsl.l < 0.5;
    const ink = dark ? 0xeef2f8 : 0x1c2230;
    const soft = dark ? 0x8793a8 : 0x6c7686;

    // The swaths lie on the faces of the model: drawn a little toward the viewer, so they do not flicker.
    const edges = (positions: Float32Array, color: number, opacity: number, width = 1) => {
      if (!positions.length) return;
      const lines = host.makeLines(positions, width, color);
      const m = lines.material;
      m.polygonOffset = true;
      m.polygonOffsetFactor = -2;
      m.polygonOffsetUnits = -2;
      m.transparent = opacity < 1;
      m.opacity = opacity;
      lines.renderOrder = 10;
      this.group.add(lines);
    };
    edges(b.dim, soft, 0.3);
    edges(b.inner, ink, 0.4);
    edges(b.lines, ink, 0.95, 1.6);

    if (b.highlight) {
      const [t0, t1] = b.highlight;
      const corner = (t: number, sv: number, sw: number) => new THREE.Vector3()
        .addScaledVector(this.u, t).addScaledVector(this.v, sv * b.half[1]).addScaledVector(this.w, sw * b.half[2]);
      const c = [t0, t1].flatMap((t) => [corner(t, -1, -1), corner(t, 1, -1), corner(t, 1, 1), corner(t, -1, 1)]);
      const faces = [[0, 1, 2, 3], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]];
      const position = new Float32Array(faces.length * 18);
      faces.forEach((f, i) => [f[0], f[1], f[2], f[0], f[2], f[3]].forEach((k, j) => c[k].toArray(position, i * 18 + j * 3)));
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(position, 3));
      const slab = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color: HIGHLIGHT, transparent: true, opacity: 0.3, side: THREE.DoubleSide, depthTest: false, depthWrite: false }));
      slab.renderOrder = 12;
      this.group.add(slab);
      const rim: number[] = [];
      for (const [i, j] of [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4], [0, 4], [1, 5], [2, 6], [3, 7]]) rim.push(...c[i].toArray(), ...c[j].toArray());
      const outline = host.makeLines(Float32Array.from(rim), 2.4, HIGHLIGHT);
      outline.material.depthTest = false;
      outline.renderOrder = 13;
      this.group.add(outline);
    }

    const chart = b.chart;
    if (!chart) return;
    this.panel.matrixAutoUpdate = false;
    this.group.add(this.panel);
    const over = <T extends THREE.Object3D>(object: T, order: number): T => {
      object.renderOrder = order;
      object.frustumCulled = false;
      this.panel.add(object);
      return object;
    };
    const basic = (color: THREE.ColorRepresentation, opacity: number) =>
      new THREE.MeshBasicMaterial({ color, transparent: true, opacity, side: THREE.DoubleSide, depthTest: false, depthWrite: false });
    const g = chart.grid;
    // The first segment of the grid is the base of the frame: from the first swath shown to the last.
    const [t0, t1] = [g[0], g[2]];
    over(new THREE.Mesh(quads([[t0, t1, 0, 1]]), basic(host.background, 0.72)), 30);
    // The swath under the cursor of the printed plot: the same strip of this one.
    if (b.highlight && b.highlight[0] >= t0 - 1e-6 && b.highlight[1] <= t1 + 1e-6) over(new THREE.Mesh(quads([[b.highlight[0], b.highlight[1], 0, 1]]), basic(HIGHLIGHT, 0.3)), 31);
    for (const bar of chart.bars) {
      const rects: number[][] = [];
      for (let i = 0; i < bar.rects.length; i += 3) rects.push([bar.rects[i], bar.rects[i + 1], 0, bar.rects[i + 2]]);
      if (rects.length) over(new THREE.Mesh(quads(rects), basic(bar.color, 0.4)), 31);
    }
    const line = (pairs: Float32Array, width: number, color: number, order: number) => {
      if (!pairs.length) return;
      const lines = host.makeLines(flat(pairs), width, color);
      lines.material.depthTest = false;
      over(lines, order);
    };
    line(g, 1, soft, 32);
    for (const s of chart.lines) line(s.segments, 2.6, new THREE.Color(s.color).getHex(), 33);
    for (const s of chart.points) {
      if (!s.at.length) continue;
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(flat(s.at), 3));
      over(new THREE.Points(geometry, new THREE.PointsMaterial({
        color: s.color, size: 9, sizeAttenuation: false, map: host.pointTexture, alphaTest: 0.4, transparent: true, depthTest: false,
      })), 34);
    }
    for (const label of chart.labels) {
      // Light text ringed with dark on a dark background; on a light one, the other way round.
      const { material, wpx, hpx } = host.labelMaterial(label.text, {
        size: label.bold ? 12 : 11, family: "sans-serif", bold: !!label.bold,
        color: label.color ?? (dark ? "#f3f6fb" : "#1c2230"), halo: dark ? undefined : "rgba(255, 255, 255, 0.92)",
      });
      const sprite = new THREE.Sprite(material);
      sprite.center.set(label.align === "left" ? 0 : label.align === "right" ? 1.08 : 0.5, 0.5 + label.rows);
      sprite.renderOrder = 36;
      this.group.add(sprite);
      this.labels.push({ sprite, label, wpx, hpx });
    }
  }

  /**
   * Where the plot stands for a view: beside the cuboid, across the line of sight, beyond its
   * outline toward the top of the screen (swaths along Z: toward the right). `base` and `rise` are
   * in the group's own space: the foot of the plot under the cuboid's centre, and its full height.
   */
  private place(view: THREE.Vector3, right: THREE.Vector3, up: THREE.Vector3, vexag: number): { base: THREE.Vector3; rise: THREE.Vector3 } {
    const b = this.build;
    // The axes as drawn (the vertical one stretched by the exaggeration).
    const drawn = (a: THREE.Vector3) => new THREE.Vector3(a.x, a.y, a.z * vexag);
    const [ur, vr, wr] = [drawn(this.u), drawn(this.v), drawn(this.w)];
    const p = new THREE.Vector3().crossVectors(view, ur.clone().normalize());
    if (p.length() < 0.08) p.copy(b.vertical ? right : new THREE.Vector3(0, 0, 1));
    p.normalize();
    // Up the screen, or to its right when the axis itself points up the screen.
    const toward = b.vertical ? right.clone().addScaledVector(up, 0.5) : up.clone().addScaledVector(right, 0.5);
    if (p.dot(toward) < 0) p.negate();
    const outline = Math.abs(p.dot(vr)) * b.half[1] + Math.abs(p.dot(wr)) * b.half[2];
    const height = (b.chart?.height ?? 0) * 2 * Math.max(b.half[0] * ur.length(), b.half[1] * vr.length(), b.half[2] * wr.length());
    const local = new THREE.Vector3(p.x, p.y, p.z / vexag);
    return { base: local.clone().multiplyScalar(outline + 0.14 * height), rise: local.multiplyScalar(height) };
  }

  /** The corners of the plot (with room for the names over it) as drawn for a view, from the scene origin. */
  corners(view: THREE.Vector3, right: THREE.Vector3, up: THREE.Vector3, vexag: number): THREE.Vector3[] {
    const chart = this.build.chart;
    if (!chart) return [];
    const { base, rise } = this.place(view, right, up, vexag);
    const [t0, t1] = [chart.grid[0], chart.grid[2]];
    // The names are sized in pixels, the plot in metres: room for them at the usual size of a fitted view.
    const room = 1 + 0.18 * (chart.lines.length + 1);
    return [[t0, -0.1], [t1, -0.1], [t0, room], [t1, room]].map(([t, h]) => {
      const p = base.clone().addScaledVector(this.u, t).addScaledVector(rise, h).add(this.group.position);
      p.z *= vexag;
      return p;
    });
  }

  /** Turns the plot toward the viewer and sizes its labels. Called before every frame. */
  update(camera: THREE.Camera, vexag: number, pixelsPerUnit: (at: THREE.Vector3) => number): void {
    if (!this.build.chart) return;
    const view = camera.getWorldDirection(new THREE.Vector3());
    const right = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 0).normalize();
    const up = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 1).normalize();
    const { base, rise } = this.place(view, right, up, vexag);
    const normal = new THREE.Vector3().crossVectors(this.u, rise).normalize();
    this.panel.matrix.makeBasis(this.u, rise, normal).setPosition(base);
    this.panel.matrixWorldNeedsUpdate = true;
    const world = new THREE.Vector3();
    for (const { sprite, label, wpx, hpx } of this.labels) {
      sprite.position.copy(base).addScaledVector(this.u, label.t).addScaledVector(rise, label.h);
      world.copy(sprite.position).add(this.group.position);
      world.z *= vexag;
      const px = Math.max(pixelsPerUnit(world), 1e-9);
      sprite.scale.set(wpx / px, hpx / px, 1);
    }
  }
}
