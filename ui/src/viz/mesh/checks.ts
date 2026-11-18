/**
 * The red layer of a checked wireframe (ТЗ 7.21 «самопересечения»; ADR 0013 §3, §5–§6): the triangles that
 * cross another triangle of the same wireframe, as the core found them on its float64 geometry —
 * `GET /wireframes/{id}/checks`, BFA1 `checks`: `xyz <f4`, nine numbers a triangle — its three corners from the
 * whole-metre origin of its group — and `tri <u4`, the number of the triangle in the wireframe as stored. A
 * group is a run of triangles no wider than 16 km, so a corner is within a quarter of a millimetre of the
 * core's; a wireframe within 16 km is one group, one draw call. `state` tells what the answer is: `done` — the
 * triangles (none when nothing crosses), `none` — the wireframe was not checked, `failed` — it could not be,
 * `missing` — checked, but the list of triangles is not in the cache of the project any more («Проверить»
 * makes it again).
 *
 * The triangles are drawn in plain red over the surface they belong to: the surface is pushed back by its
 * polygon offset, the red ones are pulled forward, so they win where both lie in one plane — at the full level
 * of detail exactly, at a coarser one nearly. They take the shared clipping planes: under a section only what
 * is in its window is seen. The layer does not count in «Вписать» — its wireframe does.
 */
import * as THREE from "three";
import { VizError } from "../error";
import { bfa1Array, decodeBfa1 } from "../format/bfa1";
import type { Origin } from "../origin";
import type { SceneLayer } from "../sceneTypes";

/** The red of defects (the errors of the audit are the same). */
export const CHECKS_COLOR = "#c0392b";

export type ChecksState = "done" | "none" | "failed" | "missing";
const STATES: readonly string[] = ["done", "none", "failed", "missing"];

export interface ChecksGroup {
  /** whole metres, absolute */
  origin: Origin;
  /** triangles first … first + n of the answer */
  first: number;
  n: number;
}

/** The crossing triangles of one wireframe, as the core gives them. */
export interface MeshChecks {
  state: ChecksState;
  /** triangles in this answer (at most 200 000) */
  triangles: number;
  /** triangles of the wireframe that cross another one */
  crossing: number;
  groups: ChecksGroup[];
  /** x, y, z of the three corners of every triangle from the origin of its group */
  positions: Float32Array;
  /** the number of every triangle in the wireframe as stored */
  tri: Uint32Array;
  /** absolute [xmin, ymin, zmin, xmax, ymax, zmax] of the triangles; null — none */
  bbox: number[] | null;
}

function damaged(what: string): VizError {
  return new VizError("checks_answer", `Слой самопересечений повреждён: ${what}.`, "Нажмите «Проверить» в свойствах каркаса. Если ошибка повторяется — сообщите разработчику.");
}

const whole = (v: unknown): v is number => typeof v === "number" && Number.isSafeInteger(v);

/** BFA1 `checks` of the core. */
export function readChecks(buf: ArrayBuffer): MeshChecks {
  const doc = decodeBfa1<{ kind?: string; state?: string; triangles?: number; crossing?: number; groups?: unknown; bbox?: unknown }>(buf);
  const h = doc.header;
  if (h.kind !== "checks" || typeof h.state !== "string" || !STATES.includes(h.state)) throw damaged("это не слой проверок каркаса");
  if (!whole(h.triangles) || h.triangles < 0) throw damaged("нет числа треугольников");
  const positions = bfa1Array(doc, "xyz", "<f4", h.triangles * 9);
  const tri = bfa1Array(doc, "tri", "<u4", h.triangles);
  if (!Array.isArray(h.groups)) throw damaged("нет групп треугольников");
  const groups: ChecksGroup[] = [];
  let at = 0;
  for (const raw of h.groups as unknown[]) {
    const g = raw as { origin?: unknown; first?: unknown; n?: unknown } | null;
    const o = g?.origin;
    if (!g || !whole(g.first) || !whole(g.n) || g.first !== at || g.n <= 0) throw damaged("группы треугольников идут не подряд");
    if (!Array.isArray(o) || o.length !== 3 || !o.every(whole)) throw damaged("начало группы — не целые метры");
    groups.push({ origin: { x: o[0], y: o[1], z: o[2] }, first: g.first, n: g.n });
    at += g.n;
  }
  if (at !== h.triangles) throw damaged("группы не покрывают все треугольники");
  const bbox = Array.isArray(h.bbox) && h.bbox.length === 6 && h.bbox.every((v) => typeof v === "number" && Number.isFinite(v)) ? (h.bbox as number[]) : null;
  return { state: h.state as ChecksState, triangles: h.triangles, crossing: whole(h.crossing) && h.crossing >= 0 ? h.crossing : h.triangles, groups, positions, tri, bbox };
}

export interface ChecksContext {
  /** the clipping planes of the scene: shared by every material */
  clip: THREE.Plane[];
}

/** The crossing triangles of one wireframe in the scene: a layer of its own (`Scene.addLayer`, key `chk:<id>`). */
export class ChecksView implements SceneLayer {
  readonly object = new THREE.Group();
  readonly origin: Origin;
  /** the wireframe counts in «Вписать», the grid and the depth range — not its defects */
  readonly bbox = null;
  readonly triangles: number;
  private readonly material: THREE.MeshBasicMaterial;
  private readonly meshes: THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>[] = [];

  constructor(
    readonly wireframeId: number,
    checks: Pick<MeshChecks, "groups" | "positions" | "triangles">,
    ctx: ChecksContext,
  ) {
    this.triangles = checks.triangles;
    this.origin = checks.groups[0]?.origin ?? { x: 0, y: 0, z: 0 };
    // unlit: a defect is as red in the shade of a relief as in the light
    this.material = new THREE.MeshBasicMaterial({ color: CHECKS_COLOR, side: THREE.DoubleSide });
    this.material.clippingPlanes = ctx.clip;
    this.material.polygonOffset = true; // in front of the surface the triangles belong to (pushed back by +1)
    this.material.polygonOffsetFactor = -2;
    this.material.polygonOffsetUnits = -2;
    this.object.renderOrder = 3;
    for (const g of checks.groups) {
      const geometry = new THREE.BufferGeometry();
      // the buffer of the answer as it is: three corners a triangle, no index
      geometry.setAttribute("position", new THREE.BufferAttribute(checks.positions.subarray(g.first * 9, (g.first + g.n) * 9), 3));
      geometry.computeBoundingSphere();
      const mesh = new THREE.Mesh(geometry, this.material);
      mesh.position.set(g.origin.x - this.origin.x, g.origin.y - this.origin.y, g.origin.z - this.origin.z);
      mesh.renderOrder = 3;
      this.meshes.push(mesh);
      this.object.add(mesh);
    }
  }

  get visible(): boolean {
    return this.object.visible;
  }

  /** The red triangles follow their wireframe: hidden with it. True when what is seen changed. */
  setVisible(visible: boolean): boolean {
    if (this.object.visible === visible) return false;
    this.object.visible = visible;
    return true;
  }

  stats(): { wireframe: number; triangles: number; groups: number; visible: boolean } {
    return { wireframe: this.wireframeId, triangles: this.triangles, groups: this.meshes.length, visible: this.object.visible };
  }

  dispose(): void {
    for (const m of this.meshes) m.geometry.dispose();
    this.material.dispose();
    this.meshes.length = 0;
    this.object.clear();
  }
}
