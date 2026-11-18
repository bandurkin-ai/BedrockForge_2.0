/**
 * The checks of a wireframe in «Свойства» as data (ТЗ 7.21 «Свойства: … площадь, объём, замкнутость, проверки»;
 * ADR 0013 §3, §6). What was measured when the wireframe was stored — area, volume of a closed solid,
 * closedness, open and non-manifold edges, degenerate triangles — and what the job `wireframe.check` adds: the
 * consistency of the normals, the self-intersections, when it ran and how long. A number that is not known
 * is said so — never 0. «Проверить» starts the job; while it runs the button waits. The triangles that cross
 * are red in the 3D window (`viz/mesh/checks.ts`); the core tells when their list is not in the cache any
 * more. Where an imported wireframe came from. No React, no three.js.
 */
import { api } from "../../shell/api";
import { num, type Fact } from "../../shell/props";
import { Store } from "../../shell/store";
import { count } from "../../shell/treeActions";
import type { Job } from "../jobs/state";
import type { WireframeChecks, WireframeSource, WireframeStats } from "../scene/objects";

/** The job of «Проверить». */
export const CHECK_JOB = "wireframe.check";

/** Wireframes whose red layer the core could not give: checked, but the list of triangles is not in the cache. */
export const checksLayerStore = new Store<{ missing: number[] }>({ missing: [] });

const known = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** The checks that ran to the end; null — not checked, or the check failed. */
export function doneChecks(stats: WireframeStats | null | undefined): WireframeChecks | null {
  const c = stats?.checks;
  return c && c.state === "done" ? c : null;
}

/** «43 200 м²»: four digits of a small number, two of a large one. */
const measure = (v: number, unit: string): string => `${num(v, Math.abs(v) < 10 ? 4 : 2)} ${unit}`;

/** What was measured when the wireframe was stored. */
export function measureFacts(stats: WireframeStats | null | undefined): Fact[] {
  if (!stats) return [];
  const facts: Fact[] = [];
  if (known(stats.area)) facts.push({ label: "Площадь", value: measure(stats.area, "м²") });
  if (known(stats.plan_area)) facts.push({ label: "Площадь в плане", value: measure(stats.plan_area, "м²") });
  if (typeof stats.closed === "boolean") {
    facts.push({ label: "Объём", value: known(stats.volume) ? measure(stats.volume, "м³") : "нет: каркас не замкнут" });
    facts.push({ label: "Замкнутость", value: stats.closed ? "замкнут" : "не замкнут" });
  }
  if (known(stats.open_edges)) facts.push({ label: "Открытых рёбер", value: count(stats.open_edges) });
  if (known(stats.nonmanifold_edges)) facts.push({ label: "Неманифолдных рёбер", value: count(stats.nonmanifold_edges) });
  if (known(stats.degenerate)) facts.push({ label: "Вырожденных треугольников", value: count(stats.degenerate) });
  return facts;
}

const SIDES = { outward: "наружу", inward: "внутрь" } as const;

/** «18.11.2026 14:05» in the time of the computer; an unreadable time — as it is. */
export function checkedText(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const two = (n: number) => String(n).padStart(2, "0");
  return `${two(d.getDate())}.${two(d.getMonth() + 1)}.${d.getFullYear()} ${two(d.getHours())}:${two(d.getMinutes())}`;
}

/** «0,65 с» · «12 с» · «3 мин 05 с» */
export function durationText(seconds: number): string {
  if (seconds < 10) return `${num(seconds, 2)} с`;
  if (seconds < 60) return `${num(seconds, 0)} с`;
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)} мин ${String(whole % 60).padStart(2, "0")} с`;
}

/** What the job found: the normals, the self-intersections, when it ran. */
export function checkFacts(stats: WireframeStats | null | undefined): Fact[] {
  const c = doneChecks(stats);
  const side = stats?.orientation ? SIDES[stats.orientation] : null;
  let normals: string;
  if (!c || !known(c.normal_conflicts)) normals = side ? `смотрят ${side}; согласованность не проверена` : "не проверены";
  else if (c.normal_conflicts === 0) normals = side ? `согласованы, смотрят ${side}` : "согласованы";
  else normals = `конфликтов: ${count(c.normal_conflicts)}`;
  let crossings: string;
  if (!c || !known(c.pairs)) crossings = "не проверены";
  else if (c.pairs === 0) crossings = "нет";
  else crossings = `пар: ${count(c.pairs)}, треугольников: ${count(c.triangles ?? 0)}`;
  const done = stats?.checks ?? null;
  const when = done?.checked_at ? checkedText(done.checked_at) : null;
  const took = c && known(c.seconds) ? `, за ${durationText(c.seconds)}` : "";
  return [
    { label: "Нормали", value: normals },
    { label: "Самопересечения", value: crossings },
    { label: "Проверено", value: when ? `${when}${took}` : "не проверялся" },
  ];
}

/** Why the last check did not run to the end; null — it did, or there was none. */
export function checkError(stats: WireframeStats | null | undefined): string | null {
  const c = stats?.checks;
  if (!c || c.state !== "failed") return null;
  const why = c.error ? [c.error.message, c.error.hint].filter(Boolean).join(" ") : "";
  return `Проверка не выполнена. ${why}`.trim();
}

/** What is to be said under the facts of a checked wireframe. `layerMissing` — the core has no list of triangles to show. */
export function checkNotes(stats: WireframeStats | null | undefined, layerMissing = false): string[] {
  const c = doneChecks(stats);
  if (!c) return stats?.checks ? [] : ["Согласованность нормалей и самопересечения считает задание «Проверка и копии для показа»: нажмите «Проверить»."];
  const notes: string[] = [];
  const crossing = c.triangles ?? 0;
  const listed = c.listed ?? 0;
  if (crossing > 0 && layerMissing) notes.push("Список пересекающихся треугольников для 3D-окна не сохранён в кеше проекта: нажмите «Проверить» — он будет построен заново.");
  else if (crossing > 0 && listed < crossing) notes.push(`Пересекающиеся треугольники показаны в 3D-окне красным: первые ${count(listed)} из ${count(crossing)}.`);
  else if (crossing > 0) notes.push("Пересекающиеся треугольники показаны в 3D-окне красным.");
  notes.push("Проверка находит треугольники, которые протыкают друг друга; наложения в одной плоскости она не находит.");
  return notes;
}

const FORMATS: Record<string, string> = {
  tridb: "Micromine TRIDB",
  dxf: "AutoCAD DXF",
  dwg: "AutoCAD DWG",
  obj: "Wavefront OBJ",
  stl: "STL",
  surpac_dtm: "Surpac DTM",
  datamine: "Datamine",
};

/** Where an imported wireframe came from: the file, its format, the name in the file, the layer, the precision. */
export function sourceFacts(source: WireframeSource | null | undefined): Fact[] {
  if (!source) return [];
  const facts: Fact[] = [];
  if (source.file) facts.push({ label: "Файл", value: source.file });
  if (source.format) facts.push({ label: "Формат", value: FORMATS[source.format] ?? source.format });
  if (source.name) facts.push({ label: "Имя в файле", value: source.name });
  if (source.layer && source.layer !== source.name) facts.push({ label: "Слой", value: source.layer });
  if (source.precision === "float32") facts.push({ label: "Точность", value: "координаты float32 (как в файле)" });
  else if (source.precision) facts.push({ label: "Точность", value: source.precision });
  return facts;
}

/** The job that checks this wireframe now (queued or running); null — none. */
export function activeCheck<J extends Pick<Job, "kind" | "status" | "params">>(jobs: readonly J[], wireframeId: number): J | null {
  return jobs.find((j) => j.kind === CHECK_JOB && (j.status === "queued" || j.status === "running") && Array.isArray(j.params?.wireframe_ids) && j.params.wireframe_ids.includes(wireframeId)) ?? null;
}

/** «В очереди…» · «Проверяется: 45 %» */
export function checkProgress(job: Pick<Job, "status" | "progress">): string {
  if (job.status === "queued") return "В очереди…";
  return `Проверяется: ${Math.round(Math.min(1, Math.max(0, job.progress || 0)) * 100)} %`;
}

/** What the commands say about a checked wireframe. */
export function checkSummary(name: string, stats: WireframeStats | null | undefined): string {
  const failed = checkError(stats);
  if (failed) return `«${name}»: ${failed}`;
  const c = doneChecks(stats);
  if (!c) return `«${name}» не проверялся`;
  const conflicts = c.normal_conflicts ?? 0;
  const pairs = c.pairs ?? 0;
  if (!conflicts && !pairs) return `«${name}»: нормали согласованы, самопересечений нет`;
  const crossings = pairs ? `самопересечений — пар: ${count(pairs)}, треугольников: ${count(c.triangles ?? 0)} (красные в 3D-окне)` : "самопересечений нет";
  return `«${name}»: ${conflicts ? `конфликтов нормалей: ${count(conflicts)}` : "нормали согласованы"}; ${crossings}`;
}

/** `POST /wireframes/{id}/check`: the job «Проверка и копии для показа» of one wireframe. */
export function startCheck(wireframeId: number): Promise<{ source: string; id: number }> {
  return api.post<{ source: string; id: number }>(`/wireframes/${wireframeId}/check`);
}

/** The wireframes of an import in one job (`POST /wireframes/check`, up to 2000 ids). */
export function startChecks(wireframeIds: number[]): Promise<{ source: string; id: number }> {
  return api.post<{ source: string; id: number }>("/wireframes/check", { wireframe_ids: wireframeIds });
}
