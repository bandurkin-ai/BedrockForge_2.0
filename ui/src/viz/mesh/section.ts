/**
 * The lines a wireframe is shown by when the view is clipped by a section (ТЗ 7.14 «Каркасы — линиями
 * сечения»; ADR 0013 §5–§6): the core cuts the float64 triangles by the plane of the section line —
 * `GET /viz/wireframes/{id}/section?x0&y0&az[&length]`, BFA1 `section`: `xyz <f8` in project coordinates,
 * `offsets <u4`, `closed <u1` — and the window draws them as thick lines of the colour of the wireframe. A
 * closed solid gives closed lines. The points go to the video card as float32 from a whole-metre origin at the
 * middle of the lines (the subtraction is done in float64), so a point is within a fraction of a millimetre
 * of the core's. The lines are not clipped by the window: they lie in its plane, and the core has cut them at
 * the ends of a line that has a length. The surface is pushed back by its polygon offset, so the lines on it
 * stay in front.
 */
import * as THREE from "three";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import type { Vec3 } from "../core/vec3";
import { VizError } from "../error";
import { bfa1Array, decodeBfa1 } from "../format/bfa1";
import { integerOrigin, type Origin } from "../origin";
import type { SceneLayer } from "../sceneTypes";
import { sectionNormal, type SectionLine } from "../section/math";

/** Thick lines of a section, px. */
export const SECTION_WIDTH_PX = 3;
/** A wireframe without a colour of its own (as its surface: Slate of the brand palette). */
export const SECTION_DEFAULT_COLOR = "#8a99a8";

const HEX = /^#[0-9a-f]{6}$/i;

/** The section of one wireframe by a plane, as the core gives it. */
export interface WireframeSection {
  wireframeId: number;
  /** a point of the plane and its unit normal, project coordinates */
  point: Vec3;
  normal: Vec3;
  /** project coordinates, float64: x, y, z of every point */
  xyz: Float64Array;
  /** line i is the points offsets[i]…offsets[i + 1] */
  offsets: Uint32Array;
  /** 1 — the line is closed (its first point is not repeated) */
  closed: Uint8Array;
}

/** The plane of a section line as the query of `GET /viz/wireframes/{id}/section` (numbers in full). */
export function sectionQuery(line: SectionLine): string {
  const q = `x0=${line.x0}&y0=${line.y0}&az=${line.az}`;
  return line.length === null ? q : `${q}&length=${line.length}`;
}

/** Does the plane of a section line pass through an extent [xmin, ymin, zmin, xmax, ymax, zmax]? Unknown — yes. */
export function planeReaches(extent: ArrayLike<number | null> | null | undefined, line: SectionLine): boolean {
  if (!extent || extent.length < 6) return true;
  const e = Array.from({ length: 6 }, (_, i) => extent[i]);
  if (!e.every((v): v is number => typeof v === "number" && Number.isFinite(v))) return true;
  const n = sectionNormal(line.az);
  const centre = n[0] * ((e[0] + e[3]) / 2 - line.x0) + n[1] * ((e[1] + e[4]) / 2 - line.y0);
  const half = (Math.abs(n[0]) * (e[3] - e[0]) + Math.abs(n[1]) * (e[4] - e[1])) / 2;
  return Math.abs(centre) <= half + 1e-6;
}

function damaged(what: string): VizError {
  return new VizError("section_answer", `Сечение каркаса повреждено: ${what}.`, "Сдвиньте линию разреза или включите отсечение заново. Если ошибка повторяется — сообщите разработчику.");
}

/** BFA1 `section` of the core. */
export function readSection(buf: ArrayBuffer): WireframeSection {
  const doc = decodeBfa1<{ kind?: string; wireframe?: number; point?: number[]; normal?: number[]; lines?: number; points?: number }>(buf);
  const h = doc.header;
  if (h.kind !== "section" || !Number.isSafeInteger(h.wireframe)) throw damaged("это не сечение каркаса");
  const closed = bfa1Array(doc, "closed", "<u1");
  const offsets = bfa1Array(doc, "offsets", "<u4", closed.length + 1);
  const xyz = bfa1Array(doc, "xyz", "<f8");
  if (xyz.length % 3 !== 0 || offsets[0] !== 0 || offsets[offsets.length - 1] * 3 !== xyz.length) throw damaged("границы линий не сходятся с точками");
  for (let i = 0; i < closed.length; i++) if (offsets[i + 1] < offsets[i]) throw damaged("границы линий идут не по порядку");
  const vec = (v: unknown): Vec3 => (Array.isArray(v) && v.length === 3 && v.every((x) => typeof x === "number") ? [v[0], v[1], v[2]] : [0, 0, 0]);
  return { wireframeId: h.wireframe as number, point: vec(h.point), normal: vec(h.normal), xyz, offsets, closed };
}

/** The section of a series (ADR 0016 §3, §6): the lines of every wireframe asked on one section (a polyline — a segment). */
export interface SeriesSectionPacket {
  series: number;
  /** the key of the geometry of the series (the cache of the core) */
  key: string;
  k: number;
  seg: number | null;
  point: Vec3;
  normal: Vec3;
  /** the wireframes asked, the lines of each (none — the plane misses it) */
  sections: Map<number, Pick<WireframeSection, "xyz" | "offsets" | "closed">>;
  /** asked but gone from the project */
  missing: number[];
}

/**
 * BFA1 `series_section` of `GET /section-series/{id}/sections/{k}/wireframes?ids=`: `xyz <f8`, `offsets <u4`,
 * `closed <u1` of all the lines, `wf <u4` — the wireframe of each line; split here per wireframe (copies of
 * slices, float64 kept).
 */
export function readSeriesSection(buf: ArrayBuffer): SeriesSectionPacket {
  const doc = decodeBfa1<{ kind?: string; series?: number; key?: string; k?: number; seg?: number | null; point?: number[]; normal?: number[]; wireframes?: number[]; missing?: number[] }>(buf);
  const h = doc.header;
  if (h.kind !== "series_section" || !Number.isSafeInteger(h.series)) throw damaged("это не сечение разреза серии");
  const closed = bfa1Array(doc, "closed", "<u1");
  const offsets = bfa1Array(doc, "offsets", "<u4", closed.length + 1);
  const xyz = bfa1Array(doc, "xyz", "<f8");
  const wf = bfa1Array(doc, "wf", "<u4", closed.length);
  if (xyz.length % 3 !== 0 || offsets[0] !== 0 || offsets[offsets.length - 1] * 3 !== xyz.length) throw damaged("границы линий не сходятся с точками");
  const vec = (v: unknown): Vec3 => (Array.isArray(v) && v.length === 3 && v.every((x) => typeof x === "number") ? [v[0], v[1], v[2]] : [0, 0, 0]);
  const ids = Array.isArray(h.wireframes) ? h.wireframes.filter((i): i is number => Number.isSafeInteger(i)) : [];
  const sections = new Map<number, Pick<WireframeSection, "xyz" | "offsets" | "closed">>();
  let i = 0;
  for (const id of ids) {
    const first = i;
    while (i < wf.length && wf[i] === id) {
      if (offsets[i + 1] < offsets[i]) throw damaged("границы линий идут не по порядку");
      i++;
    }
    const p0 = offsets[first];
    const p1 = offsets[i];
    const offs = new Uint32Array(i - first + 1);
    for (let j = first; j <= i; j++) offs[j - first] = offsets[j] - p0;
    sections.set(id, { xyz: xyz.slice(p0 * 3, p1 * 3), offsets: offs, closed: closed.slice(first, i) });
  }
  if (i !== wf.length) throw damaged("линии не по порядку каркасов");
  const seg = typeof h.seg === "number" ? h.seg : null;
  return { series: h.series as number, key: String(h.key ?? ""), k: Number(h.k ?? 0), seg, point: vec(h.point), normal: vec(h.normal), sections, missing: Array.isArray(h.missing) ? h.missing.filter((x): x is number => typeof x === "number") : [] };
}

/** The segments of a section for the video card. */
export interface SectionSegments {
  /** whole metres: the centre of the extent of the lines */
  origin: Origin;
  /** x0, y0, z0, x1, y1, z1 of every segment from the origin, float32 */
  positions: Float32Array;
  segments: number;
  /** absolute [xmin, ymin, zmin, xmax, ymax, zmax]; null — no lines */
  bbox: Float64Array | null;
}

/** Every line as pairs of points from a whole-metre origin; a closed line gets the segment that closes it. */
export function sectionSegments(s: Pick<WireframeSection, "xyz" | "offsets" | "closed">): SectionSegments {
  const origin = integerOrigin(s.xyz);
  let segments = 0;
  for (let i = 0; i < s.closed.length; i++) {
    const n = s.offsets[i + 1] - s.offsets[i];
    if (n >= 2) segments += n - 1 + (s.closed[i] && n > 2 ? 1 : 0);
  }
  const positions = new Float32Array(segments * 6);
  const o = [origin.x, origin.y, origin.z];
  let at = 0;
  const put = (a: number, b: number) => {
    for (let c = 0; c < 3; c++) {
      positions[at + c] = s.xyz[a * 3 + c] - o[c]; // float64 minus a whole number, then float32
      positions[at + 3 + c] = s.xyz[b * 3 + c] - o[c];
    }
    at += 6;
  };
  for (let i = 0; i < s.closed.length; i++) {
    const first = s.offsets[i];
    const n = s.offsets[i + 1] - first;
    if (n < 2) continue;
    for (let j = 0; j + 1 < n; j++) put(first + j, first + j + 1);
    if (s.closed[i] && n > 2) put(first + n - 1, first);
  }
  let bbox: Float64Array | null = null;
  if (s.xyz.length) {
    bbox = new Float64Array([Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity]);
    for (let i = 0; i < s.xyz.length; i++) {
      const c = i % 3;
      if (s.xyz[i] < bbox[c]) bbox[c] = s.xyz[i];
      if (s.xyz[i] > bbox[c + 3]) bbox[c + 3] = s.xyz[i];
    }
  }
  return { origin, positions, segments, bbox };
}

export interface SectionLinesContext {
  /** the size of the view, px: shared by every LineMaterial */
  resolution: THREE.Vector2;
}

interface Shown {
  group: THREE.Group;
  line: LineSegments2 | null;
  built: SectionSegments;
  lines: number;
  closed: number;
  color: string;
}

const hex = (color: string | null | undefined): string => (color && HEX.test(color) ? color : SECTION_DEFAULT_COLOR);

/**
 * The section lines of every wireframe in the scene: one layer (`Scene.addLayer`, key `sec`), in it a group
 * per wireframe placed at `origin of its lines − origin of the layer` (whole metres, float64).
 */
export class SectionLinesView implements SceneLayer {
  readonly object = new THREE.Group();
  /** the lines do not count in «Вписать», the grid and the depth range: their wireframes do */
  readonly bbox = null;
  private readonly shown = new Map<number, Shown>();

  /** `origin` — whole metres near the section (a point of its line). */
  constructor(
    readonly origin: Origin,
    private readonly ctx: SectionLinesContext,
  ) {
    this.object.renderOrder = 4;
  }

  has(wireframeId: number): boolean {
    return this.shown.has(wireframeId);
  }

  /** Per wireframe: lines, closed lines among them, segments drawn, the colour and the extent of the section. */
  stats(): { wireframe: number; lines: number; closed: number; segments: number; color: string; bbox: number[] | null }[] {
    return [...this.shown].map(([wireframe, s]) => ({ wireframe, lines: s.lines, closed: s.closed, segments: s.built.segments, color: s.color, bbox: s.built.bbox ? Array.from(s.built.bbox) : null }));
  }

  /** The section of a wireframe (a newer one replaces the one shown) in its colour ("#rrggbb"; null — none of its own). */
  set(wireframeId: number, section: Pick<WireframeSection, "xyz" | "offsets" | "closed">, color: string | null): void {
    this.remove(wireframeId);
    const built = sectionSegments(section);
    const group = new THREE.Group();
    group.position.set(built.origin.x - this.origin.x, built.origin.y - this.origin.y, built.origin.z - this.origin.z);
    const s: Shown = { group, line: null, built, lines: section.closed.length, closed: section.closed.reduce((n, c) => n + (c ? 1 : 0), 0), color: hex(color) };
    if (built.segments) {
      const geometry = new LineSegmentsGeometry();
      geometry.setPositions(built.positions);
      const material = new LineMaterial({ color: s.color, linewidth: SECTION_WIDTH_PX, worldUnits: false });
      material.uniforms.resolution.value = this.ctx.resolution; // shared: a resize updates every line
      s.line = new LineSegments2(geometry, material);
      s.line.renderOrder = 4;
      group.add(s.line);
    }
    this.shown.set(wireframeId, s);
    this.object.add(group);
  }

  setColor(wireframeId: number, color: string | null): void {
    const s = this.shown.get(wireframeId);
    if (!s || s.color === hex(color)) return;
    s.color = hex(color);
    s.line?.material.color.set(s.color);
  }

  remove(wireframeId: number): boolean {
    const s = this.shown.get(wireframeId);
    if (!s) return false;
    this.object.remove(s.group);
    s.line?.geometry.dispose();
    s.line?.material.dispose();
    this.shown.delete(wireframeId);
    return true;
  }

  dispose(): void {
    for (const id of [...this.shown.keys()]) this.remove(id);
    this.object.clear();
  }
}
