/**
 * The exact pick of a wireframe (ТЗ 7.21 «треугольник по клику»; ADR 0013 §5–§6, ADR 0004 §2 «точный выбор в
 * ядре»). A click is answered on the CPU by the level of detail that is drawn (`build.ts::pickMesh`) — the
 * surface is selected at once — and then the same ray goes to the core in project coordinates:
 * `POST /wireframes/pick {origin, direction, wireframe_ids, clip}` tests the float64 triangles of every shown
 * wireframe and gives the nearest one with the point on it and how it lies (dip, dip direction). The planes of
 * the section window go with the ray, so what is clipped away is not picked. Only the answer to the latest
 * click is delivered. Pure: no three.js, the request is a parameter.
 */
import type { Vec3 } from "../core/vec3";
import { VizError } from "../error";
import { clipPlanes, type Ray, type SectionLine } from "../section/math";
import { halfSpaces, type SectionPlane } from "../section/plane";

/** A ray in project coordinates (absolute float64); the direction need not be a unit vector. */
export interface AbsRay {
  origin: Vec3;
  dir: Vec3;
}

/**
 * The ray of a click in project coordinates. `ray` — rendered space (scene coordinates, Z stretched by the
 * exaggeration); `offset` — the origin of the layer minus the scene origin; `origin` — the origin of the layer.
 */
export function absoluteRay(ray: Ray, offset: Vec3, origin: { x: number; y: number; z: number }, vexag: number): AbsRay {
  const k = vexag || 1;
  return {
    origin: [ray.origin[0] - offset[0] + origin.x, ray.origin[1] - offset[1] + origin.y, ray.origin[2] / k - offset[2] + origin.z],
    dir: [ray.dir[0], ray.dir[1], ray.dir[2] / k],
  };
}

/** The planes of the section window in project coordinates, [nx, ny, nz, c]: n·p + c ≥ 0 inside; null — no clipping. */
export function windowPlanes(line: SectionLine | null, clip: boolean): number[][] | null {
  if (!line || !clip) return null;
  return clipPlanes(line, { x: 0, y: 0 }).map((p) => [p.normal[0], p.normal[1], p.normal[2], p.constant]);
}

/** The body of `POST /wireframes/pick`. */
export interface PickRequest {
  origin: Vec3;
  direction: Vec3;
  wireframe_ids: number[];
  clip: number[][] | null;
}

/** `plane` — the section of a series (ADR 0016 §6): with clipping its window clips the pick, not the free line. */
export function pickRequest(ray: AbsRay, wireframeIds: readonly number[], line: SectionLine | null, clip: boolean, plane: SectionPlane | null = null): PickRequest {
  const window = plane ? (clip ? halfSpaces(plane) : null) : windowPlanes(line, clip);
  return { origin: ray.origin, direction: ray.dir, wireframe_ids: [...new Set(wireframeIds)].sort((a, b) => a - b), clip: window };
}

/** The triangle under the click as the core found it. */
export interface ExactPick {
  wireframeId: number;
  name: string;
  /** the number of the triangle in the wireframe as stored */
  triangle: number;
  /** from the origin of the ray, m */
  distance: number;
  /** on the triangle, project coordinates */
  point: Vec3;
  vertices: [Vec3, Vec3, Vec3];
  /** unit, by the order of the corners; null — a triangle without area */
  normal: Vec3 | null;
  /** m² */
  area: number;
  /** the angle of the plane of the triangle from the level one, 0…90°; null — a triangle without area */
  dip: number | null;
  /** the azimuth it descends to, degrees clockwise from north; null — a level triangle */
  dipDirection: number | null;
}

const HINT = "Щёлкните по каркасу ещё раз. Если ошибка повторяется — перезапустите ядро и сообщите разработчику.";

function damaged(what: string): VizError {
  return new VizError("pick_answer", `Ответ точного выбора повреждён: ${what}.`, HINT);
}

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

function vec3(v: unknown, what: string): Vec3 {
  if (!Array.isArray(v) || v.length !== 3 || !v.every(finite)) throw damaged(what);
  return [v[0], v[1], v[2]];
}

const optional = (v: unknown, what: string): number | null => {
  if (v === null || v === undefined) return null;
  if (!finite(v)) throw damaged(what);
  return v;
};

/** The answer of `POST /wireframes/pick`: the triangle, or null when the ray meets no wireframe. */
export function readExactPick(raw: unknown): ExactPick | null {
  if (!raw || typeof raw !== "object") throw damaged("нет ответа");
  const hit = (raw as { hit?: unknown }).hit;
  if (hit === null || hit === undefined) return null;
  if (typeof hit !== "object") throw damaged("поле hit не объект");
  const h = hit as Record<string, unknown>;
  if (!Number.isSafeInteger(h.wireframe_id) || !Number.isSafeInteger(h.triangle) || (h.triangle as number) < 0) throw damaged("нет номера каркаса или треугольника");
  if (!finite(h.distance) || !finite(h.area)) throw damaged("нет расстояния или площади");
  const corners = h.vertices;
  if (!Array.isArray(corners) || corners.length !== 3) throw damaged("у треугольника не три вершины");
  return {
    wireframeId: h.wireframe_id as number,
    name: typeof h.name === "string" ? h.name : "",
    triangle: h.triangle as number,
    distance: h.distance,
    point: vec3(h.point, "нет точки"),
    vertices: [vec3(corners[0], "вершина 1"), vec3(corners[1], "вершина 2"), vec3(corners[2], "вершина 3")],
    normal: h.normal === null || h.normal === undefined ? null : vec3(h.normal, "нормаль"),
    area: h.area,
    dip: optional(h.dip, "угол падения"),
    dipDirection: optional(h.dip_direction, "азимут падения"),
  };
}

/**
 * Asks the core for the triangle under a click. A newer click overtakes the older one: `pick` then resolves to
 * `undefined` and its answer is dropped (the core is not interrupted — a pick takes milliseconds).
 */
export class ExactPicker {
  private seq = 0;

  constructor(private readonly post: (body: PickRequest) => Promise<unknown>) {}

  /** The triangle, null — nothing under the ray, undefined — overtaken by a newer click or cancelled. */
  async pick(body: PickRequest): Promise<ExactPick | null | undefined> {
    const mine = ++this.seq;
    const raw = await this.post(body).catch((e: unknown) => {
      if (mine !== this.seq) return undefined; // a late failure of an old click is nobody's
      throw e;
    });
    if (mine !== this.seq) return undefined;
    return readExactPick(raw);
  }

  /** The click in flight is not wanted any more (the selection was dropped, the scene closed). */
  cancel(): void {
    this.seq++;
  }
}

const RU = "ru-RU";

/** «512 345,679»: a coordinate to the millimetre. */
export function coordText(v: number): string {
  return v.toLocaleString(RU, { minimumFractionDigits: 3, maximumFractionDigits: 3 }).replace("-", "−");
}

/** «26,6°» */
export function degreesText(v: number): string {
  return `${v.toLocaleString(RU, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}°`;
}

/** «падение 26,6° по азимуту 270,0°» · «горизонтальный» · «площадь нулевая» — how the picked triangle lies. */
export function attitudeText(p: Pick<ExactPick, "dip" | "dipDirection">): string {
  if (p.dip === null) return "площадь нулевая";
  if (p.dipDirection === null) return "горизонтальный";
  return `падение ${degreesText(p.dip)} по азимуту ${degreesText(p.dipDirection)}`;
}
