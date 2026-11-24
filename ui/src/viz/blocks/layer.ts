/**
 * The shell of a block model in the main 3D (ТЗ 8.7.6, 6.8; ADR 0023 §11, decisions 16–19; образец —
 * `viz/ellipsoid/layer.ts`) — a scene layer (`Scene.addLayer`): one instanced rectangle per face of the answer
 * `blocks` (`decode.ts`), coloured by the legend texture (`legend.ts`), edged by the fragment shader (`material.ts`),
 * clipped by the section window as the rest, counted in «Вписать» (`bbox`). The matrix of the layer is O + R_z(φ):
 * the faces stay in the local axes of the model, float32 from the whole-metre `origin` of the answer; the layer's own
 * integer origin is the centre of the model in the project, so the matrix of the layer holds a few metres, never
 * the 10⁶ of a UTM coordinate (`viz/origin.ts`, CLAUDE.md).
 *
 * New colours (`setStyle`) refill the legend — 256·H texels, no request, no new geometry; the field, the range of
 * the scale, the breaks, the hidden categories, the filter and «Оболочка / Все блоки» (`header.mode`) are a new
 * request and a new layer (`classKey`). A click (`pick`) answers the face under the cursor — the point on it and the
 * normal; the block itself (the row, the values) is `POST /viz/blocks/{id}/at` with `inside`.
 */
import * as THREE from "three";
import { integerOrigin } from "../origin";
import type { LayerPick, PickQuery, SceneLayer } from "../sceneTypes";
import { localExtent, type BlocksData, type BlocksHeader, type ShowMode } from "./decode";
import { fillLegend, legendBytes, legendTexture, NO_VALUE_COLOR, type LegendStyle } from "./legend";
import { applyLook, createBlocksMaterial, defaultLook, faceGeometry, type BlocksLook, type BlocksMaterialContext } from "./material";
import { PickIndex, type Vec3 } from "./pickIndex";

/** Like the other surfaces: below the lines and points of the scene (0…1), above them by the distance. */
export const BLOCKS_SCORE = 2;

export type BlocksLayerContext = BlocksMaterialContext;

export interface BlocksLayerOptions {
  style?: LegendStyle;
  look?: Partial<BlocksLook>;
  /** an index built in a Worker; otherwise the first click builds it */
  index?: PickIndex;
}

/** The answer to a click on a face (`LayerHit.hit`). */
export interface BlocksPick {
  kind: "blocks";
  model: number;
  version: number;
  face: number;
  /** 2·axis + side of the face; the class `k` (0xFFFF — no value) */
  a: number;
  k: number;
  /** the point on the face, the project, float64 */
  xyz: Vec3;
  /** the same point a hair inside the block (the slack of float32), for `/viz/blocks/{id}/at` */
  inside: Vec3;
  /** the unit normal out of the block, the project axes */
  normal: Vec3;
  /** the point in the local axes of the model (the origin added) */
  local: Vec3;
}

/** What the layer shows now, for the tests and the E2E hook. */
export interface BlocksShown {
  model: number;
  version: number;
  mode: ShowMode;
  faces: number;
  facesRaw: number;
  simplified: boolean;
  lod: number;
  legendRows: number;
  look: BlocksLook;
  visible: boolean;
}

/**
 * How far a point of a face is moved into the block before `/at`: the float32 centre of a plane (from the origin)
 * is off by up to 6·10⁻⁸ of itself; four times that and a micrometre, always more than the step of the core.
 */
export function insideSlack(planeFromOrigin: number): number {
  return 1e-6 + 2.4e-7 * Math.abs(planeFromOrigin);
}

export class BlocksLayer implements SceneLayer {
  readonly object = new THREE.Group();
  readonly origin: { x: number; y: number; z: number };
  readonly bbox: number[] | null;
  readonly mesh: THREE.Mesh;
  private readonly geometry: THREE.InstancedBufferGeometry;
  private readonly material: THREE.ShaderMaterial;
  private texture: THREE.DataTexture;
  private style: LegendStyle;
  private ownLook: BlocksLook;
  private index: PickIndex | null;
  /** cos φ, sin φ */
  private readonly cs: [number, number];
  /** the translation of the matrix: corner + R·origin − layer origin, float64 */
  private readonly shift: Vec3;

  constructor(
    readonly data: BlocksData,
    ctx: BlocksLayerContext,
    opts: BlocksLayerOptions = {},
  ) {
    const h = data.header;
    const rad = (h.rotation * Math.PI) / 180;
    this.cs = [Math.cos(rad), Math.sin(rad)];
    this.style = { ...opts.style };
    this.index = opts.index ?? null;
    this.ownLook = { ...defaultLook(h.mode), ...opts.look };

    // The extent of the faces in the project: the corners of the local box, turned; the layer's integer origin is its centre.
    const corners = new Float64Array(24);
    if (data.faces > 0) {
      const e = localExtent(data);
      for (let i = 0; i < 8; i++) corners.set(this.toProject([i & 1 ? e[3] : e[0], i & 2 ? e[4] : e[1], i & 4 ? e[5] : e[2]]), 3 * i);
      const box = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
      for (let i = 0; i < 24; i++) {
        box[i % 3] = Math.min(box[i % 3], corners[i]);
        box[3 + (i % 3)] = Math.max(box[3 + (i % 3)], corners[i]);
      }
      this.bbox = box;
    } else {
      this.bbox = null;
      corners.set(h.corner, 0);
    }
    this.origin = integerOrigin(this.bbox ? corners : corners.subarray(0, 3));
    const at = this.toProject(h.origin);
    this.shift = [at[0] - this.origin.x, at[1] - this.origin.y, at[2] - this.origin.z];

    const [c, s] = this.cs;
    this.object.matrixAutoUpdate = false;
    this.object.matrix.set(c, -s, 0, this.shift[0], s, c, 0, this.shift[1], 0, 0, 1, this.shift[2], 0, 0, 0, 1);
    this.object.matrixWorldNeedsUpdate = true;

    this.texture = this.makeTexture();
    this.material = createBlocksMaterial(ctx, this.texture, this.ownLook, this.style.noValue ?? NO_VALUE_COLOR);
    this.geometry = faceGeometry(data);
    this.mesh = new THREE.Mesh(this.geometry, this.material);
    this.mesh.frustumCulled = false; // one draw call; the extent is the layer's `bbox`
    this.mesh.renderOrder = this.ownLook.opacity < 1 ? 4 : 0;
    this.object.add(this.mesh);
  }

  /** The local axes (origin added) → the project, float64 — the same arithmetic as `GridV2.to_project`. */
  private toProject(q: ArrayLike<number>): Vec3 {
    const [c, s] = this.cs;
    const o = this.data.header.corner;
    return [o[0] + (c * q[0] - s * q[1]), o[1] + (s * q[0] + c * q[1]), o[2] + q[2]];
  }

  private makeTexture(): THREE.DataTexture {
    const h = this.data.header;
    return legendTexture(legendBytes(h.colour.kind, h.classes, this.data.maxClass, this.style));
  }

  // ---------------------------------------------------------------- colours and look: no new geometry

  /**
   * New colours of the classes (the palette of the window, «Взять раскраску»): the texels of the legend only. The
   * buffers of the faces are not touched; nothing is requested from the core.
   */
  setStyle(style: LegendStyle): void {
    this.style = { ...style };
    const h = this.data.header;
    const bytes = legendBytes(h.colour.kind, h.classes, this.data.maxClass, this.style);
    if (!fillLegend(this.texture, bytes)) {
      this.texture.dispose();
      this.texture = legendTexture(bytes);
      this.material.uniforms.uLegendMap.value = this.texture;
      (this.material.uniforms.uLegend.value as THREE.Vector2).set(bytes.width, bytes.height);
    }
    (this.material.uniforms.uNoValue.value as THREE.Color).set(this.style.noValue ?? NO_VALUE_COLOR);
  }

  setLook(look: Partial<BlocksLook>): void {
    this.ownLook = { ...this.ownLook, ...look };
    applyLook(this.material, this.ownLook);
    this.mesh.renderOrder = this.ownLook.opacity < 1 ? 4 : 0;
  }

  get look(): BlocksLook {
    return this.ownLook;
  }

  get header(): BlocksHeader {
    return this.data.header;
  }

  setVisible(on: boolean): void {
    this.object.visible = on;
  }

  // ---------------------------------------------------------------- pick

  setPickIndex(index: PickIndex): void {
    this.index = index;
  }

  /** The index of the faces; built on the first click when no Worker gave one (a few hundred ms at 2.5 million). */
  pickIndex(): PickIndex {
    return (this.index ??= PickIndex.build(this.data));
  }

  /**
   * A ray of the scene (rendered space, Z stretched) → the local axes of the model → the nearest face that is inside
   * the section window. The units of `t` are those of the ray.
   */
  pick(q: PickQuery): LayerPick | null {
    if (this.data.faces === 0 || !this.object.visible) return null;
    const hit = this.hit(q);
    return hit ? { score: BLOCKS_SCORE + hit.t / (1 + hit.t), hit: hit.pick } : null;
  }

  private hit(q: PickQuery): { t: number; pick: BlocksPick } | null {
    const [c, s] = this.cs;
    const [tx, ty, tz] = this.shift;
    const v = q.vexag || 1;
    const off = q.offset;
    const lx = q.ray.origin[0] - off[0] - tx;
    const ly = q.ray.origin[1] - off[1] - ty;
    const o: Vec3 = [c * lx + s * ly, -s * lx + c * ly, q.ray.origin[2] / v - off[2] - tz];
    const dz = q.ray.dir[2] / v;
    const d: Vec3 = [c * q.ray.dir[0] + s * q.ray.dir[1], -s * q.ray.dir[0] + c * q.ray.dir[1], dz];
    const accept = (p: Vec3): boolean => {
      // the point in the scene (relative to its origin, Z not stretched)
      const x = tx + c * p[0] - s * p[1] + off[0];
      const y = ty + s * p[0] + c * p[1] + off[1];
      return q.inWindow(x, y, tz + p[2] + off[2]);
    };
    const r = this.pickIndex().ray(o, d, accept);
    if (!r) return null;
    const h = this.data.header;
    const axis = r.a >> 1;
    const local: Vec3 = [r.point[0] + h.origin[0], r.point[1] + h.origin[1], r.point[2] + h.origin[2]];
    const slack = insideSlack(r.point[axis]);
    const inLocal: Vec3 = [local[0] - r.normal[0] * slack, local[1] - r.normal[1] * slack, local[2] - r.normal[2] * slack];
    const normal: Vec3 = [c * r.normal[0] - s * r.normal[1], s * r.normal[0] + c * r.normal[1], r.normal[2]];
    const pick: BlocksPick = { kind: "blocks", model: h.model, version: h.version, face: r.face, a: r.a, k: r.k, xyz: this.toProject(local), inside: this.toProject(inLocal), normal, local };
    return { t: r.t, pick };
  }

  shown(): BlocksShown {
    const h = this.data.header;
    return {
      model: h.model,
      version: h.version,
      mode: h.mode,
      faces: this.data.faces,
      facesRaw: h.faces_raw,
      simplified: h.simplified,
      lod: h.lod,
      legendRows: this.texture.image.height,
      look: { ...this.ownLook },
      visible: this.object.visible,
    };
  }

  dispose(): void {
    this.geometry.dispose();
    this.material.dispose();
    this.texture.dispose();
    this.object.clear();
  }
}
