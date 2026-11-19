/**
 * Surfaces — wireframes and digital terrain models — for the 3D window (ТЗ 7.20, ADR 0011 §3–§4, ADR 0004 §3):
 * the manifest of a wireframe (`GET /viz/wireframes/{id}`, BFA1 `wireframe`), the buffers of one part at one
 * level of detail (`/viz/wireframes/{id}/parts/{k}?lod=`, BFA1 `mesh`; the encoder is
 * core/bf_core/viz/meshes.py), the level every part is wanted at and the click on a surface.
 * Pure: no three.js, no DOM — the buffers are made in the Worker.
 *
 * A part: `xyz f4×3` from the integer origin of the part and `idx` — three vertices per triangle, `u2` or `u4`.
 * No normals: the window shades flat. The arrays of the answer are used as they are, nothing is copied.
 * A vertex without coordinates is never drawn at zero: its triangles are left out and counted.
 * Levels: 0 — every triangle, 1 and 2 — about 1/8 and 1/64 of them; the border of a part is the same at every
 * level, so neighbours at different levels join without cracks.
 */
import { applyTriangleBudget } from "../core/budget";
import type { Box3 } from "../core/bounds";
import { chooseLevel, triangleBoundaries } from "../core/lod";
import type { Vec3 } from "../core/vec3";
import { VizError } from "../error";
import { decodeBfa1 } from "../format/bfa1";
import type { Ray } from "../section/math";

/** The settings of a DTM (`wireframes.dtm` of the core); `raster_id` — the raster draped on it. */
export interface DtmSettings {
  raster_id: number | null;
  /** «Скрыть данные за ЦТМ»: the image is seen on the surface only */
  clip: boolean;
  /** "#rrggbb": the colour the image is multiplied by */
  tint: string;
  /** 0…1: how much of the image covers the colour of the surface */
  opacity: number;
  /** light and shadow of the relief on the image */
  shade: boolean;
}

export interface MeshPartInfo {
  k: number;
  /** triangles of the part at level 0 */
  n: number;
  /** absolute [xmin, ymin, zmin, xmax, ymax, zmax], float64 */
  bbox: number[];
  /** absolute integer origin of the part's float32 vertices */
  origin: [number, number, number];
  /** ETag of the part at every level: the geometry, not the version of the object */
  etags: string[];
}

export interface MeshManifest {
  wireframeId: number;
  name: string;
  version: number;
  /** "#rrggbb"; null — no colour of its own */
  color: string | null;
  /** null — a wireframe of «Каркасы» */
  dtm: DtmSettings | null;
  points: number;
  triangles: number;
  bbox: number[] | null;
  /** how many times fewer triangles a level holds than the full one: [1, 8, 64] */
  ratios: number[];
  parts: MeshPartInfo[];
}

const HINT = "Обновите вид. Если ошибка повторяется — загрузите каркас заново.";

function damaged(what: string): VizError {
  return new VizError("mesh_damaged", `Данные поверхности повреждены: ${what}.`, HINT);
}

interface ManifestHeader {
  kind: string;
  wireframe: { id: number; name: string };
  version: number;
  color: string | null;
  dtm: DtmSettings | null;
  points: number;
  triangles: number;
  bbox: number[] | null;
  lods: { lod: number; ratio: number }[];
  parts: MeshPartInfo[];
}

export function readMeshManifest(buf: ArrayBuffer): MeshManifest {
  const h = decodeBfa1<ManifestHeader>(buf).header;
  if (h.kind !== "wireframe" || !Array.isArray(h.parts) || !Array.isArray(h.lods) || !h.wireframe) throw new VizError("mesh_manifest", "Ответ ядра — не описание каркаса.", HINT);
  return {
    wireframeId: h.wireframe.id,
    name: h.wireframe.name,
    version: h.version,
    color: typeof h.color === "string" ? h.color : null,
    dtm: h.dtm ?? null,
    points: h.points,
    triangles: h.triangles,
    bbox: Array.isArray(h.bbox) && h.bbox.length === 6 ? h.bbox : null,
    ratios: h.lods.map((l) => l.ratio),
    parts: h.parts,
  };
}

/** The same parts with the same ETags: a new name, colour or DTM setting — what is built stays as it is. */
export function sameMeshGeometry(a: MeshManifest, b: MeshManifest): boolean {
  if (a.parts.length !== b.parts.length || a.ratios.join() !== b.ratios.join()) return false;
  return a.parts.every((p, i) => p.etags.join() === b.parts[i].etags.join() && p.origin.join() === b.parts[i].origin.join());
}

// --- one part -----------------------------------------------------------------------------------------

/** The buffers of one part at one level, as the Worker gives them back. */
export interface BuiltMesh {
  k: number;
  lod: number;
  origin: [number, number, number];
  /** xyz of the vertices, float32 from the part origin: the array of the answer */
  positions: Float32Array;
  /** three vertices per triangle: the array of the answer (shortened when triangles were left out) */
  index: Uint16Array | Uint32Array;
  triangles: number;
  vertices: number;
  /** triangles of the part at level 0 */
  sourceTriangles: number;
  /** fewer triangles than the part has (a coarser level) */
  simplified: boolean;
  /**
   * the whole part answering for a level the core has not made yet (`standin` of the header, no ETag, ADR 0015
   * §8): kept only until the event `wireframes.lod`, then the level is asked again
   */
  standin: boolean;
  /** triangles left out: a vertex of theirs has no coordinates */
  dropped: number;
  /** the extent of the vertices in the coordinates of the part: [xmin, ymin, zmin, xmax, ymax, zmax] */
  box: number[];
}

export interface MeshPartHeader {
  kind?: string;
  k: number;
  lod: number;
  origin: [number, number, number];
  triangles?: number;
  source_triangles?: number;
  standin?: boolean;
}

/** The part of a BFA1 `mesh` answer: the header and its two arrays, views of the same buffer. */
export function decodeMeshPart(buffer: ArrayBuffer): { header: MeshPartHeader; xyz: Float32Array; idx: Uint16Array | Uint32Array } {
  const doc = decodeBfa1<MeshPartHeader>(buffer);
  const h = doc.header;
  if (h.kind !== "mesh" || !Array.isArray(h.origin) || h.origin.length !== 3) throw new VizError("mesh_part", "Ответ ядра — не часть каркаса.", HINT);
  const xyz = doc.arrays.xyz;
  const idx = doc.arrays.idx;
  if (!(xyz instanceof Float32Array)) throw damaged("нет координат вершин (xyz, float32)");
  if (!(idx instanceof Uint16Array) && !(idx instanceof Uint32Array)) throw damaged("нет номеров вершин треугольников (idx, u2 или u4)");
  return { header: h, xyz, idx };
}

/**
 * The buffers of a part from its decoded arrays: checked, the extent found, triangles with an empty vertex
 * left out (the index is shortened in place — nothing is copied).
 */
export function buildMeshBuffers(h: MeshPartHeader, xyz: Float32Array, idx: Uint16Array | Uint32Array): BuiltMesh {
  if (xyz.length % 3 !== 0) throw damaged(`координат ${xyz.length} — не тройки xyz (часть ${h.k})`);
  if (idx.length % 3 !== 0) throw damaged(`номеров вершин ${idx.length} — не тройки (часть ${h.k})`);
  const vertices = xyz.length / 3;
  const box = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
  let empty: Uint8Array | null = null;
  for (let i = 0; i < vertices; i++) {
    const x = xyz[i * 3];
    const y = xyz[i * 3 + 1];
    const z = xyz[i * 3 + 2];
    // a finite float32 minus itself is 0; NaN and ±Infinity give NaN
    if (x - x !== 0 || y - y !== 0 || z - z !== 0) {
      (empty ??= new Uint8Array(vertices))[i] = 1;
      continue;
    }
    if (x < box[0]) box[0] = x;
    if (y < box[1]) box[1] = y;
    if (z < box[2]) box[2] = z;
    if (x > box[3]) box[3] = x;
    if (y > box[4]) box[4] = y;
    if (z > box[5]) box[5] = z;
  }
  let kept = 0;
  for (let t = 0; t < idx.length; t += 3) {
    const a = idx[t];
    const b = idx[t + 1];
    const c = idx[t + 2];
    if (a >= vertices || b >= vertices || c >= vertices) throw damaged(`треугольник ${t / 3} части ${h.k} ссылается на вершину, которой нет`);
    if (empty && (empty[a] || empty[b] || empty[c])) continue;
    if (kept !== t) {
      idx[kept] = a;
      idx[kept + 1] = b;
      idx[kept + 2] = c;
    }
    kept += 3;
  }
  const triangles = kept / 3;
  const source = typeof h.source_triangles === "number" ? h.source_triangles : idx.length / 3;
  return {
    k: h.k,
    lod: h.lod,
    origin: h.origin,
    positions: xyz,
    index: kept === idx.length ? idx : idx.subarray(0, kept),
    triangles,
    vertices,
    sourceTriangles: source,
    simplified: idx.length / 3 < source,
    standin: h.standin === true,
    dropped: (idx.length - kept) / 3,
    box: triangles ? box : [0, 0, 0, 0, 0, 0],
  };
}

// --- level of detail ----------------------------------------------------------------------------------

/** Triangles of every level of a part before the levels are loaded: its `n` over the ratios of the manifest. */
export function levelTriangles(n: number, ratios: readonly number[]): number[] {
  return ratios.map((r) => Math.max(1, Math.ceil(n / Math.max(r, 1))));
}

export interface LevelPart {
  /** radius of the part on the screen, px (Infinity — the camera is inside it); null — out of view */
  screen: number | null;
  /** triangles of every level, finest first: loaded levels — as they are, the others — `levelTriangles` */
  triangles: readonly number[];
  /** the level wanted before (hysteresis); null — none yet */
  current: number | null;
  /** the finest level worth asking: a coarser level that came back whole is the full part */
  floor: number;
  /** the part reaches into the section window while clipping is on: the cut is drawn from every triangle */
  exact?: boolean;
}

/**
 * The level every part is wanted at (ADR 0004 §3): the finest with at most 2 triangles per pixel of its disc
 * on the screen, 20 % hysteresis; then the triangle budget of the frame — the smallest parts on the screen go
 * coarser first («упрощено»). Null — the part is out of view: nothing more is asked for it.
 */
export function wantedLevels(parts: readonly LevelPart[], budget: number): { levels: (number | null)[]; triangles: number; budgeted: boolean; over: boolean } {
  const inView: number[] = [];
  const chosen = parts.map((p, i) => {
    if (p.screen === null) return null;
    inView.push(i);
    const level = p.exact ? 0 : chooseLevel(p.screen, triangleBoundaries(p.triangles), p.current);
    return Math.min(Math.max(level, p.floor), p.triangles.length - 1);
  });
  const r = applyTriangleBudget(
    inView.map((i) => ({ key: String(i), screen: parts[i].screen ?? 0, triangles: parts[i].triangles, level: chosen[i] ?? 0 })),
    budget,
  );
  const levels: (number | null)[] = chosen.slice();
  inView.forEach((i, at) => (levels[i] = r.levels[at]));
  return { levels, triangles: r.triangles, budgeted: r.simplified, over: r.over };
}

/**
 * Does a box (scene coordinates) reach into the section window: not entirely behind one of the clipping
 * planes (their normals point inside). Without planes everything does.
 */
export function reachesClip(box: Box3, planes: readonly { normal: { x: number; y: number; z: number }; constant: number }[]): boolean {
  for (const pl of planes) {
    const n = pl.normal;
    const x = n.x > 0 ? box.max[0] : box.min[0];
    const y = n.y > 0 ? box.max[1] : box.min[1];
    const z = n.z > 0 ? box.max[2] : box.min[2];
    if (n.x * x + n.y * y + n.z * z + pl.constant < 0) return false;
  }
  return true;
}

// --- a click on a surface -----------------------------------------------------------------------------

export interface MeshPickPart {
  k: number;
  /** float64: part origin − scene origin (scene units, Z not stretched) */
  offset: Vec3;
  /** the extent of the vertices in the coordinates of the part */
  box: ArrayLike<number>;
  positions: ArrayLike<number>;
  index: ArrayLike<number>;
  triangles: number;
}

export interface MeshHit {
  k: number;
  triangle: number;
  /** along the ray, in the units of its direction (rendered space) */
  t: number;
  /** the place, rendered space */
  point: Vec3;
}

/** Does the ray o + d·t (t ≥ 0, t ≤ `far`) pass through the box, grown by a millimetre. */
function rayInBox(o: Vec3, d: Vec3, box: ArrayLike<number>, far: number): boolean {
  let t0 = 0;
  let t1 = far;
  for (let a = 0; a < 3; a++) {
    const lo = box[a] - 1e-3;
    const hi = box[a + 3] + 1e-3;
    if (d[a] === 0) {
      if (o[a] < lo || o[a] > hi) return false;
      continue;
    }
    const ta = (lo - o[a]) / d[a];
    const tb = (hi - o[a]) / d[a];
    t0 = Math.max(t0, Math.min(ta, tb));
    t1 = Math.min(t1, Math.max(ta, tb));
    if (t0 > t1) return false;
  }
  return true;
}

/**
 * The nearest triangle of the shown parts along a ray of the screen (rendered space, float64), on the CPU
 * without an index: a part off the ray is dropped by its box, the rest is searched triangle by triangle
 * (64 000 of them take about a millisecond). What the section window cuts off is not picked — the next
 * triangle along the ray answers. The exact pick on float64 is the core's (Э8).
 */
export function pickMesh(parts: Iterable<MeshPickPart>, ray: Ray, vexag: number, inWindow: (x: number, y: number) => boolean): MeshHit | null {
  const v = vexag || 1;
  // the ray in scene units (Z not stretched): its parameter stays the distance in rendered space
  const d: Vec3 = [ray.dir[0], ray.dir[1], ray.dir[2] / v];
  let best: MeshHit | null = null;
  let bestT = Infinity;
  for (const p of parts) {
    if (!p.triangles) continue;
    const o: Vec3 = [ray.origin[0] - p.offset[0], ray.origin[1] - p.offset[1], ray.origin[2] / v - p.offset[2]];
    if (!rayInBox(o, d, p.box, bestT)) continue;
    const pos = p.positions;
    const idx = p.index;
    const [ox, oy, oz] = o;
    const [dx, dy, dz] = d;
    for (let i = 0, n = p.triangles * 3; i < n; i += 3) {
      const a = idx[i] * 3;
      const b = idx[i + 1] * 3;
      const c = idx[i + 2] * 3;
      const ax = pos[a];
      const ay = pos[a + 1];
      const az = pos[a + 2];
      const e1x = pos[b] - ax;
      const e1y = pos[b + 1] - ay;
      const e1z = pos[b + 2] - az;
      const e2x = pos[c] - ax;
      const e2y = pos[c + 1] - ay;
      const e2z = pos[c + 2] - az;
      const px = dy * e2z - dz * e2y;
      const py = dz * e2x - dx * e2z;
      const pz = dx * e2y - dy * e2x;
      const det = e1x * px + e1y * py + e1z * pz;
      if (det === 0) continue;
      const sx = ox - ax;
      const sy = oy - ay;
      const sz = oz - az;
      const u = (sx * px + sy * py + sz * pz) / det;
      if (!(u >= 0 && u <= 1)) continue;
      const qx = sy * e1z - sz * e1y;
      const qy = sz * e1x - sx * e1z;
      const qz = sx * e1y - sy * e1x;
      const w = (dx * qx + dy * qy + dz * qz) / det;
      if (!(w >= 0 && u + w <= 1)) continue;
      const t = (e2x * qx + e2y * qy + e2z * qz) / det;
      if (!(t > 0 && t < bestT)) continue;
      const hx = ray.origin[0] + ray.dir[0] * t;
      const hy = ray.origin[1] + ray.dir[1] * t;
      if (!inWindow(hx, hy)) continue;
      bestT = t;
      best = { k: p.k, triangle: i / 3, t, point: [hx, hy, ray.origin[2] + ray.dir[2] * t] };
    }
  }
  return best;
}

/**
 * The surface over a plan point given in the coordinates of the part: the triangle that holds it (the
 * highest of several) and the point as the rasteriser interpolates it from the float32 vertices.
 */
export function sampleAt(positions: ArrayLike<number>, index: ArrayLike<number>, triangles: number, x: number, y: number): { triangle: number; x: number; y: number; z: number } | null {
  let best: { triangle: number; x: number; y: number; z: number } | null = null;
  for (let i = 0, n = triangles * 3; i < n; i += 3) {
    const a = index[i] * 3;
    const b = index[i + 1] * 3;
    const c = index[i + 2] * 3;
    const ax = positions[a];
    const ay = positions[a + 1];
    const bx = positions[b];
    const by = positions[b + 1];
    const cx = positions[c];
    const cy = positions[c + 1];
    if (x < Math.min(ax, bx, cx) || x > Math.max(ax, bx, cx) || y < Math.min(ay, by, cy) || y > Math.max(ay, by, cy)) continue;
    const det = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy);
    if (det === 0) continue; // a vertical triangle has no place in plan
    const wa = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / det;
    const wb = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / det;
    const wc = 1 - wa - wb;
    if (wa < -1e-9 || wb < -1e-9 || wc < -1e-9) continue;
    const z = wa * positions[a + 2] + wb * positions[b + 2] + wc * positions[c + 2];
    if (!best || z > best.z) best = { triangle: i / 3, x: wa * ax + wb * bx + wc * cx, y: wa * ay + wb * by + wc * cy, z };
  }
  return best;
}
