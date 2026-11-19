/**
 * «Облако точек» in «Импорт файлов» (ADR 0014 §6; ТЗ 7.35 Д-22): the card of a cloud file as the composition
 * gives it (`POST /import/objects/inspect`, `kind: "cloud"`) — a LAS (version, format of points, records, scale,
 * offset, extent, CRS, classes of a sample, what is not loaded, the proposed «каждая k-я запись») or a table XYZ
 * (encoding, delimiter, decimal mark, header, columns, the fields X / Y / Z / class, the first rows, the estimate);
 * «каждая k-я запись» as it is typed; a `.laz` refused with the hint of the core. Pure: no React.
 */
import { count } from "../../shell/treeActions";
import type { FileEntry, FileOk } from "./objects";

/** «Облако точек» of the ribbon: LAS, or a table of points loaded as a cloud. */
export type CloudMode = "las" | "xyz";

/** The title of the window and of its operation log, whatever the mode (a LAS and a table load alike). */
export const CLOUD_TITLE = "Облако точек";

export interface LasCard {
  format: "las";
  version: string;
  point_format: number;
  record_length: number;
  /** records of the file */
  point_count: number;
  declared_count: number;
  scale: number[];
  offset: number[];
  /** xmin, ymin, zmin, xmax, ymax, zmax */
  bbox: number[] | null;
  bbox_exact: boolean;
  crs: { wkt: string | null; epsg: number | null; geographic: boolean; name: string | null } | null;
  /** code → points among `sampled` records */
  classes: Record<string, number>;
  sampled: number;
  not_loaded: string[];
  /** the proposed «каждая k-я запись» (1 — every record) */
  every: number;
  software: string;
  system_id: string;
  summary: string;
  warnings: string[];
  /** records the import takes with `every_chosen` */
  points: number;
  every_chosen: number;
}

export interface XyzCard {
  format: "xyz";
  encoding: string;
  /** ",", ";", "\t", "|" or "whitespace" */
  delimiter: string;
  decimal: string;
  header: boolean;
  columns: string[];
  fields: { x: string | null; y: string | null; z: string | null; class: string | null };
  preview: string[][];
  /** data rows, by the size of the file */
  estimate: number;
  not_loaded: string[];
  warnings: string[];
  summary: string;
  points: number;
  every_chosen: number;
}

export type CloudCard = LasCard | XyzCard;

/** The largest «каждая k-я запись» the core takes. */
export const MAX_EVERY = 1_000_000;

/** «каждая k-я запись» as typed: empty — 1 (every record); `error` — not an integer 1…1 000 000. */
export function parseEvery(text: string | undefined): { every: number; error: string | null } {
  const t = (text ?? "").replace(/[\s  ]/g, "");
  if (!t) return { every: 1, error: null };
  if (!/^\+?\d+$/.test(t) || Number(t) < 1 || Number(t) > MAX_EVERY) {
    return { every: 1, error: `«${(text ?? "").trim()}» — не целое число от 1 до ${count(MAX_EVERY)}. 1 — каждая запись, 2 — каждая вторая и т. д.` };
  }
  return { every: Number(t), error: null };
}

/** The card of a cloud file; null — the file is not a cloud. */
export const cloudCard = (f: FileEntry): CloudCard | null => (f.ok && f.kind === "cloud" ? (f.cloud ?? null) : null);

/** A `.laz` (or a LAS whose points are compressed): the core refuses it with the way to unpack it. */
export const isLaz = (f: FileEntry): boolean => !f.ok && f.error.code === "laz_not_supported";

/** The text of a file the wizard cannot load as a cloud: the message and the hint of the core. */
export function refusalText(f: FileEntry): string | null {
  return f.ok ? null : `${f.error.message} ${f.error.hint}`.trim();
}

const n = (v: number): string => count(v);
const DELIMITERS: Record<string, string> = { ",": "запятая", ";": "точка с запятой", "\t": "табуляция", "|": "вертикальная черта", whitespace: "пробелы" };
export const delimiterTitle = (d: string): string => DELIMITERS[d] ?? `«${d}»`;

/** «точек 1 500 000 из 3 000 000 записей (каждая 2-я)» */
export function pointsText(c: CloudCard): string {
  const all = c.format === "las" ? c.point_count : c.estimate;
  const about = c.format === "xyz" ? "около " : "";
  if (c.every_chosen <= 1) return `точек: ${about}${n(c.points)}`;
  return `точек: ${about}${n(c.points)} из ${about}${n(all)} ${c.format === "las" ? "записей" : "строк"} (каждая ${n(c.every_chosen)}-я)`;
}

/** The proposal of the core to thin a big LAS out (ADR 0014 §1: > 50 млн точек); null — none, or taken. */
export function everyProposal(c: CloudCard): { every: number; text: string } | null {
  if (c.format !== "las" || c.every <= 1 || c.every === c.every_chosen) return null;
  return { every: c.every, text: `В файле ${n(c.point_count)} записей. Предлагается каждая ${n(c.every)}-я запись: точек около ${n(Math.ceil(c.point_count / c.every))}.` };
}

const coord = (v: number): string => v.toLocaleString("ru-RU", { maximumFractionDigits: 3 });
const triple = (v: readonly number[]): string => v.map((x) => x.toLocaleString("ru-RU", { maximumFractionDigits: 9, useGrouping: false })).join("; ");

/** Name — value of a LAS card. */
export function lasFacts(c: LasCard): { label: string; value: string }[] {
  const facts = [
    { label: "Версия", value: `LAS ${c.version}, формат точек ${c.point_format} (запись ${c.record_length} байт)` },
    { label: "Записей", value: c.declared_count !== c.point_count ? `${n(c.point_count)} (в заголовке ${n(c.declared_count)})` : n(c.point_count) },
    { label: "Масштаб", value: triple(c.scale) },
    { label: "Смещение", value: triple(c.offset) },
  ];
  if (c.bbox && c.bbox.length >= 6) {
    const b = c.bbox;
    facts.push({ label: "Охват", value: `X ${coord(b[0])} … ${coord(b[3])}; Y ${coord(b[1])} … ${coord(b[4])}; Z ${coord(b[2])} … ${coord(b[5])}${c.bbox_exact ? "" : " (по выборке)"}` });
  }
  facts.push({ label: "Система координат", value: c.crs?.name ? `${c.crs.name}${c.crs.epsg ? ` (EPSG:${c.crs.epsg})` : ""}${c.crs.geographic ? " — географическая, градусы" : ""}` : "не задана" });
  const by = [c.software, c.system_id].filter((s) => s.trim()).join(", ");
  if (by) facts.push({ label: "Создан", value: by });
  return facts;
}

/** Name — value of a table XYZ. */
export function xyzFacts(c: XyzCard): { label: string; value: string }[] {
  return [
    { label: "Кодировка", value: c.encoding },
    { label: "Разделитель", value: delimiterTitle(c.delimiter) },
    { label: "Десятичный знак", value: c.decimal === "," ? "запятая" : "точка" },
    { label: "Заголовок", value: c.header ? "первая строка — названия колонок" : "нет" },
    { label: "Строк", value: `около ${n(c.estimate)}` },
  ];
}

/** Classes of the card by code: those of the sample of a LAS (the table XYZ tells none before the import). */
export function sampleClasses(c: CloudCard): { code: number; points: number }[] {
  if (c.format !== "las") return [];
  return Object.entries(c.classes)
    .map(([k, v]) => ({ code: Number(k), points: v }))
    .filter((r) => Number.isInteger(r.code) && r.points > 0)
    .sort((a, b) => a.code - b.code);
}

/** «по выборке 1 000 000 записей» when the classes are of a sample. */
export const sampleNote = (c: LasCard): string => (c.sampled < c.point_count ? `по выборке ${n(c.sampled)} записей из ${n(c.point_count)}` : `по всем ${n(c.point_count)} записям`);

/** A file of points (not a cloud yet) that may be loaded as one (a checkbox in «Импорт файлов»). */
export const mayBeCloud = (f: FileEntry): f is FileOk => f.ok && (f.format === "points" || (f.kind === "cloud" && f.format === "points_csv"));
