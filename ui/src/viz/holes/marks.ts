/**
 * Marks where the holes of a set cross wireframes (ТЗ 7.15, ADR 0007 §5; ADR 0013 §6 «метки ◆ пересечений»):
 * the core finds the crossings exactly by the triangles — `GET /viz/sets/{id}/intersections?wireframes=`, BFA1
 * `intersections`: `objects [{kind, id, name, color}]`, and per mark `hole u4`, `depth f8`, `xyz f8` (project
 * coordinates, the point of the trace), `obj u4`, `event u1` (0 a crossing, 1 into and 2 out of a closed
 * solid) — and the window draws a diamond at every one, in the colour of the wireframe or the colour of the
 * style, with «глубина · объект» next to it. The depth is written to the millimetre from the float64 of the
 * core. The labels have the highest priority of the frame (crossings first). The points go to the video card
 * as float32 from a whole-metre origin at the middle of the marks; they take the shared clipping planes.
 */
import * as THREE from "three";
import type { MarkerShape } from "../../charts/striplog/model";
import { VizError } from "../error";
import { bfa1Array, decodeBfa1 } from "../format/bfa1";
import { LABEL_PRIORITY } from "../labels/declutter";
import { makeLabel, type LabelCache, type LabelItem } from "../labels/sprites";
import { integerOrigin, toRelativeFloat32, type Origin } from "../origin";
import type { SceneLayer } from "../sceneTypes";
import { hexToLinear } from "./colors";
import type { TraceStyle } from "./traceStyle";

export const EVENT_CROSS = 0;
export const EVENT_ENTER = 1;
export const EVENT_LEAVE = 2;
/** Labels of crossings per set at most (as the names of the collars). */
export const MAX_MARK_LABELS = 3000;
/** A wireframe without a colour of its own (as its surface: Slate of the brand palette). */
export const MARK_DEFAULT_COLOR = "#8a99a8";

const HEX = /^#[0-9a-f]{6}$/i;

export interface MarkObject {
  kind: string;
  id: number;
  name: string;
  /** "#rrggbb"; null — no colour of its own */
  color: string | null;
}

/** The crossings of the holes of a set with wireframes, as the core gives them: sorted by hole, depth, object. */
export interface HoleMarks {
  setId: number;
  version: number;
  objects: MarkObject[];
  n: number;
  /** dh_holes.id of every mark */
  hole: Uint32Array;
  /** along the hole, m */
  depth: Float64Array;
  /** project coordinates, float64 */
  xyz: Float64Array;
  /** the place of the crossed object in `objects` */
  obj: Uint32Array;
  event: Uint8Array;
}

/** The query of `GET /viz/sets/{id}/intersections`: the wireframes to test, by id (an empty list — none). */
export function marksQuery(wireframeIds: readonly number[]): string {
  return `wireframes=${[...new Set(wireframeIds)].sort((a, b) => a - b).join(",")}`;
}

function damaged(what: string): VizError {
  return new VizError("marks_answer", `Пересечения стволов с каркасами повреждены: ${what}.`, "Выключите и включите пересечения в настройке стволов. Если ошибка повторяется — сообщите разработчику.");
}

/** BFA1 `intersections` of the core. */
export function readHoleMarks(buf: ArrayBuffer): HoleMarks {
  const doc = decodeBfa1<{ kind?: string; set?: number; version?: number; objects?: unknown; n?: number }>(buf);
  const h = doc.header;
  if (h.kind !== "intersections" || !Number.isSafeInteger(h.set) || !Number.isSafeInteger(h.n) || (h.n as number) < 0) throw damaged("это не список пересечений");
  const n = h.n as number;
  if (!Array.isArray(h.objects)) throw damaged("нет списка объектов");
  const objects = h.objects.map((o): MarkObject => {
    const r = (o && typeof o === "object" ? o : {}) as Record<string, unknown>;
    if (!Number.isSafeInteger(r.id)) throw damaged("у объекта нет номера");
    return { kind: typeof r.kind === "string" ? r.kind : "wireframe", id: r.id as number, name: typeof r.name === "string" ? r.name : "", color: typeof r.color === "string" && HEX.test(r.color) ? r.color : null };
  });
  const marks: HoleMarks = {
    setId: h.set as number,
    version: Number.isSafeInteger(h.version) ? (h.version as number) : 0,
    objects,
    n,
    hole: bfa1Array(doc, "hole", "<u4", n),
    depth: bfa1Array(doc, "depth", "<f8", n),
    xyz: bfa1Array(doc, "xyz", "<f8", n * 3),
    obj: bfa1Array(doc, "obj", "<u4", n),
    event: bfa1Array(doc, "event", "<u1", n),
  };
  for (let i = 0; i < n; i++) if (marks.obj[i] >= objects.length) throw damaged("метка ссылается на объект, которого нет");
  return marks;
}

/** «64,850 м · Разлом»: the depth to the millimetre (ТЗ 7.15) and the crossed object. */
export function markText(depth: number, name: string): string {
  const d = `${depth.toFixed(3).replace(".", ",")} м`;
  return name ? `${d} · ${name}` : d;
}

/** What of the style of the traces the marks use. */
export type MarkStyle = Pick<TraceStyle, "markerSize" | "wireframeColor" | "markerLabels" | "labelSize" | "labelColor">;

/** "#rrggbb" of every mark: the colour of the style, or — "object" — of its wireframe (`colorOf`, else the answer's). */
export function markColors(marks: Pick<HoleMarks, "objects" | "obj" | "n">, style: Pick<MarkStyle, "wireframeColor">, colorOf?: (objectId: number) => string | null | undefined): string[] {
  const byObject = marks.objects.map((o) => {
    if (style.wireframeColor !== "object") return style.wireframeColor;
    const now = colorOf?.(o.id);
    const c = now === undefined ? o.color : now;
    return c && HEX.test(c) ? c : MARK_DEFAULT_COLOR;
  });
  return Array.from({ length: marks.n }, (_, i) => byObject[marks.obj[i]]);
}

/** What the marks share with the scene; read when a style is applied, so its owner may replace the label cache. */
export interface HoleMarksContext {
  clip: THREE.Plane[];
  /** the shapes of the markers (`holes/markers.ts`); a diamond is taken */
  textures: { get(shape: MarkerShape): THREE.Texture };
  labels: LabelCache;
  pixelRatio: number;
}

/** The marks of one set in the scene (`Scene.addLayer`, key `xm:<set>`). */
export class HoleMarksView implements SceneLayer {
  readonly object = new THREE.Group();
  readonly origin: Origin;
  /** the marks lie on the traces: the set counts in «Вписать», the grid and the depth range */
  readonly bbox = null;
  private readonly points: THREE.Points;
  private readonly labelGroup = new THREE.Group();
  private readonly positions: Float32Array;
  private items: LabelItem[] = [];
  private shown = false;

  constructor(
    readonly marks: HoleMarks,
    private readonly ctx: HoleMarksContext,
    style: MarkStyle,
    colorOf?: (objectId: number) => string | null | undefined,
  ) {
    this.origin = integerOrigin(marks.xyz);
    this.positions = toRelativeFloat32(marks.xyz, this.origin); // float64 minus a whole number, then float32
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(this.positions, 3));
    g.setAttribute("color", new THREE.BufferAttribute(new Float32Array(this.positions.length), 3));
    g.computeBoundingSphere();
    const material = new THREE.PointsMaterial({ size: 9, sizeAttenuation: false, vertexColors: true, transparent: true, alphaTest: 0.4, clippingPlanes: ctx.clip });
    material.map = ctx.textures.get("diamond");
    this.points = new THREE.Points(g, material);
    this.points.renderOrder = 5;
    this.labelGroup.renderOrder = 10;
    this.object.add(this.points, this.labelGroup);
    this.apply(style, colorOf);
  }

  get setId(): number {
    return this.marks.setId;
  }

  /** Marks drawn and labels made for them. */
  stats(): { marks: number; labels: number } {
    return { marks: this.marks.n, labels: this.items.length };
  }

  /** Size, colours and labels by the style of the traces; `colorOf` — the colour a wireframe has now. */
  apply(style: MarkStyle, colorOf?: (objectId: number) => string | null | undefined): void {
    const material = this.points.material as THREE.PointsMaterial;
    material.size = style.markerSize * this.ctx.pixelRatio;
    const colors = this.points.geometry.getAttribute("color") as THREE.BufferAttribute;
    const array = colors.array as Float32Array;
    const hex = markColors(this.marks, style, colorOf);
    const linear = new Map<string, [number, number, number]>();
    for (let i = 0; i < hex.length; i++) {
      let c = linear.get(hex[i]);
      if (!c) linear.set(hex[i], (c = hexToLinear(hex[i])));
      array.set(c, i * 3);
    }
    colors.needsUpdate = true;
    this.labelGroup.clear(); // label materials are shared and stay in the cache
    this.items = [];
    this.shown = style.markerLabels;
    if (!style.markerLabels) return;
    const font = { size: style.labelSize, family: "sans-serif" as const, bold: false, color: style.labelColor };
    const shift = style.markerSize / 2 + 3;
    const at = new THREE.Vector3();
    const n = Math.min(this.marks.n, MAX_MARK_LABELS);
    for (let i = 0; i < n; i++) {
      at.set(this.positions[i * 3], this.positions[i * 3 + 1], this.positions[i * 3 + 2]);
      const item = makeLabel(this.ctx.labels, markText(this.marks.depth[i], this.marks.objects[this.marks.obj[i]].name), font, at, shift, LABEL_PRIORITY.crossing);
      this.labelGroup.add(item.sprite);
      this.items.push(item);
    }
  }

  labels(): readonly LabelItem[] {
    return this.shown ? this.items : [];
  }

  dispose(): void {
    this.labelGroup.clear();
    this.points.geometry.dispose();
    (this.points.material as THREE.Material).dispose();
    this.object.clear();
  }
}
