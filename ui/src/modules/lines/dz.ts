/**
 * «Карта разностей» of a point cloud as data (ADR 0014 §5; ТЗ 7.35 Д-22): `dz` = Z of every point minus a
 * wireframe or a DTM under or over it — the job `cloud.dz` (`POST /lines/layers/{id}/dz {wireframe_id}`), or the
 * last step of «Построение ЦТМ» from the cloud. What the core keeps of it (`cloud.dz` of `/lines/files`: with what
 * surface, when, the points with a difference and those outside the surface, |dz| by the ground — СКО, P95, the
 * largest), the surfaces offered (DTMs, then wireframes), the one taken by default (the last compared, else the
 * last DTM built from this cloud, else the only surface), the texts of «Свойства» and of the window, and the job
 * a command started for a cloud (`dzJobs`: the window follows it). Pure but for that store: no React.
 */
import { num, type Fact } from "../../shell/props";
import { Store } from "../../shell/store";
import { count, counted } from "../../shell/treeActions";
import type { Job } from "../jobs/state";
import type { LineLayerInfo, WireframeInfo } from "../scene/objects";

/** The measures of a map of differences: as `cloud.dz` keeps them and as the job `cloud.dz` returns them. */
export interface DzMeasures {
  /** points that have a difference */
  points: number;
  /** points with no surface under or over them */
  outside: number;
  /** the measures are by the ground (class 2), or by every point when there is no ground under the surface */
  by: "ground" | "all";
  /** points the measures are taken by */
  count: number;
  /** |dz|, m: root mean square, the 95th percentile, the largest; null — no point to measure */
  rms: number | null;
  p95: number | null;
  max: number | null;
}

/** `cloud.dz` of the core: the measures, with what surface (its id is null once it is deleted) and when. */
export interface DzRecord extends DzMeasures {
  wireframeId: number | null;
  name: string;
  /** ISO time of the core (UTC) */
  at: string | null;
}

const int = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : 0);
const metres = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

function measuresOf(o: Record<string, unknown>, points: unknown): DzMeasures {
  return { points: int(points), outside: int(o.outside), by: o.by === "all" ? "all" : "ground", count: int(o.count), rms: metres(o.rms), p95: metres(o.p95), max: metres(o.max) };
}

/** The map of differences of a cloud; null — it has none. */
export function dzRecord(l: Pick<LineLayerInfo, "cloud"> | null | undefined): DzRecord | null {
  const d = l?.cloud?.dz as Record<string, unknown> | null | undefined;
  if (!d || typeof d !== "object") return null;
  const id = d.wireframe_id;
  return { ...measuresOf(d, d.points), wireframeId: typeof id === "number" ? id : null, name: typeof d.name === "string" ? d.name : "", at: typeof d.at === "string" ? d.at : null };
}

/** The measures of the result of a job `cloud.dz` ({with_dz, outside, by, count, rms, p95, max, points}). */
export const dzResult = (r: Record<string, unknown> | null | undefined): DzMeasures | null => (r ? measuresOf(r, r.with_dz) : null);

// ---- the surfaces ---------------------------------------------------------------------------------------

export interface SurfaceOption {
  id: number;
  name: string;
  dtm: boolean;
  triangles: number;
}

type Surface = Pick<WireframeInfo, "id" | "name" | "dtm" | "triangle_count"> & { built?: unknown };

/** DTMs, then wireframes, each by name. */
export function surfaceOptions(list: readonly Surface[]): SurfaceOption[] {
  const all = list.map((w) => ({ id: w.id, name: w.name, dtm: w.dtm !== null, triangles: w.triangle_count }));
  return all.sort((a, b) => Number(b.dtm) - Number(a.dtm) || a.name.localeCompare(b.name, "ru") || a.id - b.id);
}

/** The surface was built from the cloud: `meta.built.sources` of `/wireframes` names it. */
export function builtFrom(w: Surface, cloudId: number): boolean {
  const sources = (w.built as { sources?: unknown } | null | undefined)?.sources;
  return Array.isArray(sources) && sources.some((s) => !!s && typeof s === "object" && (s as { kind?: unknown }).kind === "line_layer" && (s as { id?: unknown }).id === cloudId);
}

/** The surface the window takes: the last compared (if still there), else the last DTM built from the cloud,
 * else the only surface of the project; null — the person chooses. */
export function defaultSurface(cloud: Pick<LineLayerInfo, "id" | "cloud"> | null | undefined, list: readonly Surface[]): number | null {
  if (!cloud) return list.length === 1 ? list[0].id : null;
  const last = dzRecord(cloud)?.wireframeId ?? null;
  if (last !== null && list.some((w) => w.id === last)) return last;
  const mine = list.filter((w) => w.dtm !== null && builtFrom(w, cloud.id)).map((w) => w.id);
  if (mine.length) return Math.max(...mine);
  return list.length === 1 ? list[0].id : null;
}

// ---- texts ----------------------------------------------------------------------------------------------

const m = (v: number | null): string => (v === null ? "—" : `${num(v, 3)} м`);

/** «19.11.2026, 10:00» of an ISO time of the core (UTC when it has no zone), in local time. */
export function atText(at: string | null): string | null {
  if (!at) return null;
  const t = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(at) ? at : `${at}Z`);
  return Number.isNaN(t.getTime()) ? null : t.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** «СКО 0,012 м, P95 0,02 м, наибольшее 0,051 м (по земле, 31 000 точек)» */
export function measuresText(d: DzMeasures): string {
  if (!d.count) return "мерить не по чему: под поверхностью нет точек";
  const by = d.by === "ground" ? "по земле" : "по всем точкам — земли под поверхностью нет";
  return `СКО ${m(d.rms)}, P95 ${m(d.p95)}, наибольшее ${m(d.max)} (${by}, ${counted(d.count, "точка", "точки", "точек")})`;
}

/** The line of «Свойства»: with what, when, the measures and the points outside the surface. */
export function dzSummary(d: DzRecord | null): string {
  if (!d) return "Карта разностей не считалась: режим цвета «Отклонение от ЦТМ» появится после неё или после «Построить ЦТМ из облака».";
  const what = d.name ? `«${d.name}»${d.wireframeId === null ? " (удалена)" : ""}` : "поверхности";
  const at = atText(d.at);
  const outside = d.outside ? `; вне поверхности ${counted(d.outside, "точка", "точки", "точек")}` : "";
  return `Карта разностей относительно ${what}${at ? ` от ${at}` : ""}: ${measuresText(d)}${outside}.`;
}

/** The facts of the result in the window. */
export function dzFacts(d: DzMeasures): Fact[] {
  const facts: Fact[] = [
    { label: "СКО", value: m(d.rms) },
    { label: "P95", value: m(d.p95) },
    { label: "Наибольшее по модулю", value: m(d.max) },
    { label: "Мерено", value: d.count ? `${d.by === "ground" ? "по земле (класс 2)" : "по всем точкам"}: ${count(d.count)}` : "не по чему" },
    { label: "Точек с отклонением", value: count(d.points) },
    { label: "Вне поверхности", value: count(d.outside) },
  ];
  return facts;
}

// ---- the job --------------------------------------------------------------------------------------------

export type JobRef = Pick<Job, "source" | "id">;

/** The last job `cloud.dz` a command started, by cloud: the window «Карта разностей» shows its state and result. */
export const dzJobs = new Store<Record<string, JobRef>>({});

/** The state of the job of the window, in words; null — it is done (its result is shown) or there is none. */
export function jobText(job: Pick<Job, "status" | "progress" | "message" | "error"> | undefined): string | null {
  if (!job) return null;
  if (job.status === "queued") return "Задание в очереди…";
  if (job.status === "running") return `Считается: ${Math.round(100 * Math.max(0, Math.min(1, job.progress)))} %${job.message ? ` — ${job.message}` : ""}`;
  if (job.status === "cancelled") return "Задание отменено: отклонения облака остались прежними.";
  if (job.status === "failed") return job.error ? `${job.error.message} ${job.error.hint}`.trim() : "Задание завершилось ошибкой.";
  return null;
}
