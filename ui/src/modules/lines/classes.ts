/**
 * «Свойства» of a point cloud as data (ADR 0014 §2, §6; ТЗ 7.35 Д-22): the classes of ASPRS by their Russian
 * names (LAS 1.4 R15, table 17), the rows of the table of classes (code, name, points, colour, shown), the colour
 * modes the cloud has (`cloud.modes` of the core: «Класс» always, `h` after «Фильтр земли», `dz` after an
 * «Отклонение от ЦТМ»), the style kept by the project in the view key `cloud_styles` and the facts of the cloud
 * and of its picked point. Pure: no React.
 */
import { extentFacts, num, type Fact } from "../../shell/props";
import { count } from "../../shell/treeActions";
import { classHex, cloudMode, readCloudInfo, type CloudColorMode, type CloudStyle } from "../../viz/lines/cloud";
import type { LineFileInfo, LineLayerInfo } from "../scene/objects";
import type { AttrPage } from "./props";

/** The names of the ASPRS classes (LAS 1.4 R15, table 17; 8 and 12 — as LAS 1.2 used them). */
export const ASPRS_NAMES: Readonly<Record<number, string>> = {
  0: "создана, не классифицирована",
  1: "не классифицирована",
  2: "земля",
  3: "низкая растительность",
  4: "средняя растительность",
  5: "высокая растительность",
  6: "здание",
  7: "низкая точка (шум)",
  8: "резерв (в LAS 1.2 — ключевая точка)",
  9: "вода",
  10: "железная дорога",
  11: "дорожное покрытие",
  12: "резерв (в LAS 1.2 — перекрытие)",
  13: "провод — грозозащитный трос",
  14: "провод — фаза",
  15: "опора ЛЭП",
  16: "соединитель провода",
  17: "настил моста",
  18: "высокий шум",
};

/** «земля» · «резерв ASPRS» (19…63) · «пользовательский» (64…255). */
export function className(code: number): string {
  return ASPRS_NAMES[code] ?? (code >= 19 && code <= 63 ? "резерв ASPRS" : "пользовательский");
}

/** «2 — земля» */
export const classLabel = (code: number): string => `${code} — ${className(code)}`;

/** The cloud part of the properties of a layer (`cloud` of `/lines/files`; null — an ordinary layer). */
export interface CloudSummary {
  parts: number;
  levels: number[];
  bbox: number[] | null;
  /** code → points */
  classes: Record<string, number>;
  /** "class" always; "h" after the ground filter; "dz" after the deviation from a DTM */
  modes: string[];
  source: {
    kind?: string;
    every?: number;
    filename?: string;
    las?: { version?: string; point_format?: number; point_count?: number; software?: string; system_id?: string; epsg?: number | null; crs?: boolean } | null;
    xyz?: { delimiter?: string; columns?: string[]; fields?: Record<string, string | null> } | null;
  } | null;
  ground: unknown;
  dz: { wireframe_id?: number; name?: string } | null;
}

export const cloudOf = (l: Pick<LineLayerInfo, "cloud">): CloudSummary | null => l.cloud ?? null;

// ---- classes ----------------------------------------------------------------------------------------------

export interface ClassRow {
  code: number;
  name: string;
  points: number;
  color: string;
  shown: boolean;
}

/** Every class of the cloud in the order of the codes, with its colour and visibility. */
export function classRows(classes: Record<string, number> | null | undefined, style: CloudStyle | null | undefined): ClassRow[] {
  const hidden = new Set(style?.hidden ?? []);
  return Object.entries(classes ?? {})
    .map(([k, n]) => ({ code: Number(k), points: n }))
    .filter((r) => Number.isInteger(r.code) && r.code >= 0 && r.code <= 255 && typeof r.points === "number" && r.points > 0)
    .sort((a, b) => a.code - b.code)
    .map((r) => ({ ...r, name: className(r.code), color: classHex(r.code), shown: !hidden.has(r.code) }));
}

/** «Показано точек: 1 200 из 1 500» — the classes shown in the modes that draw classes. */
export function shownText(rows: readonly ClassRow[]): string {
  const all = rows.reduce((s, r) => s + r.points, 0);
  const shown = rows.reduce((s, r) => s + (r.shown ? r.points : 0), 0);
  return shown === all ? `Показаны все классы: ${count(all)} точек` : `Показано точек: ${count(shown)} из ${count(all)}`;
}

/** The style with a class shown or hidden (the codes stay sorted; nothing else of the style changes). */
export function withClass(style: CloudStyle | null | undefined, code: number, shown: boolean): CloudStyle {
  const hidden = new Set(style?.hidden ?? []);
  if (shown) hidden.delete(code);
  else hidden.add(code);
  return { ...(style ?? {}), hidden: [...hidden].sort((a, b) => a - b) };
}

/** Every class shown, or every one hidden. */
export function withAllClasses(style: CloudStyle | null | undefined, rows: readonly ClassRow[], shown: boolean): CloudStyle {
  return { ...(style ?? {}), hidden: shown ? [] : rows.map((r) => r.code) };
}

// ---- colour modes ---------------------------------------------------------------------------------------

export const MODE_TITLES: Readonly<Record<CloudColorMode, string>> = {
  class: "Класс",
  layer: "Цвет слоя",
  h: "Высота над землёй (h)",
  dz: "Отклонение от ЦТМ (dz)",
};

export interface ModeOption {
  mode: CloudColorMode;
  title: string;
  /** null — can be chosen; otherwise why not */
  why: string | null;
}

/** The four modes; `h` and `dz` only when the cloud has them. */
export function modeOptions(c: Pick<CloudSummary, "modes">): ModeOption[] {
  const has = new Set(c.modes);
  const why: Partial<Record<CloudColorMode, string>> = {
    h: has.has("h") ? undefined : "появится после «Фильтра земли»",
    dz: has.has("dz") ? undefined : "появится после расчёта отклонения от ЦТМ",
  };
  return (["class", "layer", "h", "dz"] as const).map((mode) => ({ mode, title: MODE_TITLES[mode], why: why[mode] ?? null }));
}

/** The mode in force for a style: the rule of the 3D window (`viz/lines/cloud.ts › cloudMode`). */
export function modeOf(c: Pick<CloudSummary, "classes" | "modes">, style: CloudStyle | null | undefined): CloudColorMode {
  const info = readCloudInfo({ classes: c.classes, modes: c.modes.map((mode) => ({ mode })) });
  return info ? cloudMode(info, style?.color) : "layer";
}

/** The modes `h` and `dz` show every point: the visibility of classes counts in «Класс» and «Цвет слоя». */
export const classesCount = (mode: CloudColorMode): boolean => mode === "class" || mode === "layer";

// ---- facts ----------------------------------------------------------------------------------------------

/** «LAS 1.4, формат точек 6» · «Точки XYZ» */
export function sourceText(c: Pick<CloudSummary, "source">): string {
  const s = c.source;
  if (!s) return "—";
  if (s.kind === "las") {
    const las = s.las ?? {};
    const name = las.version ? `LAS ${las.version}` : "LAS";
    return typeof las.point_format === "number" ? `${name}, формат точек ${las.point_format}` : name;
  }
  return s.kind === "xyz" ? "Точки XYZ" : "—";
}

/** What «Свойства» tell about a cloud. */
export function cloudFacts(l: LineLayerInfo, file?: Pick<LineFileInfo, "name" | "crs_name">): Fact[] {
  const c = cloudOf(l);
  const facts: Fact[] = [];
  if (file) facts.push({ label: "Файл", value: c?.source?.filename && c.source.filename !== file.name ? `${file.name} (${c.source.filename})` : file.name });
  facts.push({ label: "Точек", value: count(l.point_count) });
  if (c) {
    facts.push({ label: "Источник", value: sourceText(c) });
    const every = c.source?.every ?? 1;
    if (every > 1) facts.push({ label: "Прореживание", value: `каждая ${count(every)}-я запись` });
    if (c.source?.kind === "las" && c.source.las?.point_count) facts.push({ label: "Записей в LAS", value: count(c.source.las.point_count) });
    facts.push({ label: "Частей", value: count(c.parts) });
  }
  facts.push(...extentFacts(l.extent ?? c?.bbox ?? null));
  const crs = file?.crs_name ?? (c?.source?.las?.crs ? "задана в LAS" : null);
  facts.push({ label: "Система координат", value: crs ?? "не задана" });
  if (c?.dz?.name) facts.push({ label: "ЦТМ для dz", value: c.dz.name });
  return facts;
}

/** A page of a cloud: the fields, X, Y, Z (float64) and the number of the record of the source. */
export interface CloudPage extends AttrPage {
  xyz?: number[][];
  seq?: number[];
}

const metres = (v: unknown): string => (typeof v === "number" && Number.isFinite(v) ? `${num(v, 3)} м` : "—");

/** The point of a page (row `row`): X, Y, Z, «Класс: 2 — земля», the return, `h`, `dz`, the record of the source. */
export function pointFacts(page: CloudPage, row = 0): Fact[] {
  const values = page.rows[row];
  if (!values) return [];
  const facts: Fact[] = [];
  const xyz = page.xyz?.[row];
  if (xyz) (["X", "Y", "Z"] as const).forEach((axis, i) => facts.push({ label: axis, value: typeof xyz[i] === "number" ? num(xyz[i], 3) : "—" }));
  const at = (name: string): unknown => {
    const k = page.fields.findIndex((f) => f.name === name || f.name.startsWith(`${name},`));
    return k < 0 ? undefined : values[k];
  };
  const cls = at("Класс");
  if (cls !== undefined) facts.push({ label: "Класс", value: typeof cls === "number" ? classLabel(cls) : "—" });
  const ret = at("Возврат");
  const of = at("Число возвратов");
  if (ret !== undefined) facts.push({ label: "Возврат", value: typeof ret === "number" ? (typeof of === "number" && of > 0 ? `${ret} из ${of}` : String(ret)) : "—" });
  const h = at("Высота над землёй");
  if (h !== undefined) facts.push({ label: "Высота над землёй", value: metres(h) });
  const dz = at("Отклонение от ЦТМ");
  if (dz !== undefined) facts.push({ label: "Отклонение от ЦТМ", value: metres(dz) });
  const seq = page.seq?.[row];
  if (typeof seq === "number") facts.push({ label: "Запись источника", value: `№ ${count(seq + 1)}` });
  return facts;
}
