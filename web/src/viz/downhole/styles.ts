import type { IntervalColumn } from "../../api";
import type { StripLogSettings, StyleRef, Track } from "../../charts/stripLog";

/**
 * The library of styles of a project. Three levels:
 * - palette: a colouring (classes, a gradient with bounds, or colours of categories);
 * - layer: everything about how a layer is drawn, without its table and field;
 * - display: a list of layers with the names of their fields.
 * A layer either holds a copy of a style or is linked to it: then the style's settings are used.
 */
export type StyleLevel = "palette" | "layer" | "display";
export type DataKind = "number" | "text" | "event" | "mixed";

export interface StyleRec {
  id: number;
  name: string;
  level: StyleLevel;
  data_kind: DataKind;
  body: Record<string, unknown>;
  default_for: string[];
  /** built into the application: read-only, can be copied */
  builtin?: boolean;
}

export const LEVEL_TITLES: Record<StyleLevel, string> = { palette: "Раскраска", layer: "Стиль слоя", display: "Шаблон отображения" };

/** What binds a layer to its data and its place in the display: never a part of a style. */
const BINDING: (keyof Track)[] = ["id", "table", "field", "style", "visible"];

export type LayerBody = Partial<Omit<Track, "id" | "table" | "field" | "style" | "visible">>;
export interface TemplateTrack extends LayerBody { field: string }
export interface TemplateBody { tracks: TemplateTrack[]; tube?: number; labels?: boolean; form?: StripLogSettings["form"] }
export type PaletteBody =
  | { kind: "classes"; classes: NonNullable<Track["classes"]> }
  | { kind: "gradient"; gradient: NonNullable<Track["gradient"]> }
  | { kind: "categories"; catColors: Record<string, string>; hidden?: string[]; catOrder?: string[] };

export const dataKind = (t: Pick<Track, "type">, column?: Pick<IntervalColumn, "kind">): DataKind =>
  t.type === "event" ? "event" : column ? (column.kind === "number" ? "number" : "text") : t.type === "color_text" || t.type === "text" ? "text" : "number";

/** The settings of a layer as a style: everything but the binding to data. */
export function layerBody(t: Track): LayerBody {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(t)) if (!BINDING.includes(k as keyof Track) && v !== undefined) out[k] = v;
  return out as LayerBody;
}

const ref = (s: StyleRec, linked: boolean, extra: Partial<StyleRef> = {}): StyleRef => ({ id: s.id, name: s.name, linked, ...extra });

/** A layer with the settings of a style; its table, field, id and visibility stay. */
export function applyLayerStyle(t: Track, style: StyleRec, linked = false, extra: Partial<StyleRef> = {}): Track {
  const body = style.body as LayerBody;
  return {
    id: t.id, table: t.table, field: t.field, type: "bars", width: 90, color: t.color, min: "", max: "", log: false, boundaries: false,
    ...body,
    ...(t.visible === false ? { visible: false } : {}),
    style: ref(style, linked && !style.builtin, extra),
  };
}

/** The colouring of a layer as a palette; null when the layer has none worth saving (one colour). */
export function paletteBody(t: Track, numeric: boolean): PaletteBody | null {
  if (!numeric) return { kind: "categories", catColors: t.catColors ?? {}, ...(t.hidden?.length ? { hidden: t.hidden } : {}), ...(t.catOrder?.length ? { catOrder: t.catOrder } : {}) };
  if (t.colorBy === "classes" && t.classes?.length) return { kind: "classes", classes: t.classes };
  if (t.colorBy === "scale" && t.gradient) return { kind: "gradient", gradient: t.gradient };
  return null;
}

function paletteFields(body: PaletteBody): Partial<Track> {
  if (body.kind === "classes") return { colorBy: "classes", classes: body.classes.map((c) => ({ ...c })) };
  if (body.kind === "gradient") return { colorBy: "scale", gradient: { ...body.gradient } };
  return { catColors: { ...body.catColors }, hidden: body.hidden, catOrder: body.catOrder };
}

export function applyPalette(t: Track, style: StyleRec, linked = false): Track {
  return { ...t, ...paletteFields(style.body as PaletteBody), palette: ref(style, linked && !style.builtin) };
}

/** Does a palette fit a field of this kind? Classes and gradients colour numbers, categories colour text. */
export const paletteFits = (style: StyleRec, numeric: boolean): boolean =>
  ((style.body as PaletteBody).kind === "categories") !== numeric;

/** The layers of a display as a template: settings with the names of the fields. */
export function templateBody(s: StripLogSettings): TemplateBody {
  return { tracks: s.tracks.map((t) => ({ ...layerBody(t), field: t.field })), tube: s.tube, labels: s.labels, form: s.form };
}

export interface FieldMatch {
  /** index of the layer in the template */
  layer: number;
  templateField: string;
  /** the field of the table it goes to; null: not found (to choose or to skip) */
  field: string | null;
}

/** Fields of a table for the layers of a template, by name without regard to case. */
export function matchFields(template: TemplateBody, columns: Pick<IntervalColumn, "name">[]): FieldMatch[] {
  return template.tracks.map((t, layer) => ({
    layer, templateField: t.field,
    field: columns.find((c) => c.name.trim().toUpperCase() === String(t.field).trim().toUpperCase())?.name ?? null,
  }));
}

/** Layers of a template on a table; the layers whose field is null are left out. */
export function applyTemplate(template: TemplateBody, table: string, matches: FieldMatch[], newId: () => string): Track[] {
  const out: Track[] = [];
  for (const m of matches) {
    const src = template.tracks[m.layer];
    if (!src || !m.field) continue;
    const { field: _field, ...body } = src;
    out.push({ type: "bars", width: 90, color: "#4f9dde", min: "", max: "", log: false, boundaries: false, ...body, id: newId(), table, field: m.field });
  }
  return out;
}

/** "AU*" matches AU, AU_PPM; without a star the name must be equal; case does not matter. */
export function nameMatches(pattern: string, name: string): boolean {
  const p = pattern.trim().toUpperCase();
  const n = name.trim().toUpperCase();
  if (!p) return false;
  if (!p.includes("*")) return p === n;
  const re = new RegExp(`^${p.split("*").map((x) => x.replace(/[.+?^${}()|[\]\\]/g, "\\$&")).join(".*")}$`);
  return re.test(n);
}

/** The layer style that applies by itself to a field with this name. */
export function defaultStyleFor(field: string, kind: DataKind, styles: StyleRec[]): StyleRec | null {
  return styles.find((s) => s.level === "layer" && !s.builtin && (s.data_kind === kind || s.data_kind === "mixed") && s.default_for.some((p) => nameMatches(p, field))) ?? null;
}

/**
 * The settings a display is drawn with: linked layers, colourings and the template take their settings
 * from the library. A link to a style that is gone falls back to the copy kept in the layer.
 */
export function resolveTracks(settings: StripLogSettings, styles: StyleRec[]): StripLogSettings {
  const byId = new Map(styles.map((s) => [s.id, s]));
  let tracks = settings.tracks;
  let changed = false;
  const template = settings.template?.linked ? byId.get(settings.template.id) : undefined;
  if (template && template.level === "display") {
    const body = template.body as unknown as TemplateBody;
    const used = new Set<number>();
    tracks = tracks.map((t) => {
      const i = (body.tracks ?? []).findIndex((x, j) => !used.has(j) && String(x.field).toUpperCase() === t.field.toUpperCase());
      if (i < 0) return t;
      used.add(i);
      const { field: _field, ...rest } = body.tracks[i];
      changed = true;
      return { ...t, ...rest, id: t.id, table: t.table, field: t.field };
    });
  }
  tracks = tracks.map((t) => {
    let out = t;
    if (t.style?.linked) {
      const s = byId.get(t.style.id);
      if (s && s.level === "layer") out = applyLayerStyle(t, s, true);
      else out = { ...t, style: { ...t.style, linked: false } };
      changed = true;
    }
    if (out.palette?.linked) {
      const p = byId.get(out.palette.id);
      out = p && p.level === "palette" ? applyPalette(out, p, true) : { ...out, palette: { ...out.palette, linked: false } };
      changed = true;
    }
    return out;
  });
  return changed ? { ...settings, tracks } : settings;
}

/** Ids of the styles a display uses, linked or as a source. */
export function usedStyles(settings: StripLogSettings): { id: number; linked: boolean }[] {
  const out: { id: number; linked: boolean }[] = [];
  if (settings.template) out.push({ id: settings.template.id, linked: settings.template.linked });
  for (const t of settings.tracks) {
    if (t.style) out.push({ id: t.style.id, linked: t.style.linked });
    if (t.palette) out.push({ id: t.palette.id, linked: t.palette.linked });
  }
  return out;
}

/** A display without links to a style (before the style is deleted): the layers keep a copy of its settings. */
export function unlinkStyle(settings: StripLogSettings, styleId: number, styles: StyleRec[]): StripLogSettings {
  const resolved = resolveTracks(settings, styles);
  const drop = (r: StyleRef | undefined) => (r && r.id === styleId ? undefined : r);
  return {
    ...resolved, template: drop(resolved.template),
    tracks: resolved.tracks.map((t) => ({ ...t, style: drop(t.style), palette: drop(t.palette) })),
  };
}

// ---- the file of styles

export const STYLE_FILE_FORMAT = "bedrockforge-styles";
export const STYLE_FILE_VERSION = 1;
export const STYLE_FILE_EXT = ".bfstyle.json";

export interface StyleFileEntry { name: string; level: StyleLevel; data_kind: DataKind; body: Record<string, unknown>; default_for: string[]; [key: string]: unknown }
export interface StyleFile { format: string; version: number; styles: StyleFileEntry[]; [key: string]: unknown }

export function toStyleFile(styles: StyleRec[]): StyleFile {
  return {
    format: STYLE_FILE_FORMAT, version: STYLE_FILE_VERSION,
    styles: styles.map((s) => ({ name: s.name, level: s.level, data_kind: s.data_kind, body: s.body, default_for: s.default_for })),
  };
}

/** Reads the text of a style file; throws with a message for the user when it is not one. */
export function parseStyleFile(text: string): StyleFile {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw new Error("Файл не читается: это не JSON");
  }
  const f = raw as Partial<StyleFile> | null;
  if (!f || typeof f !== "object" || f.format !== STYLE_FILE_FORMAT) throw new Error("Это не файл стилей BedrockForge (.bfstyle.json)");
  if (f.version !== STYLE_FILE_VERSION) throw new Error(`Неизвестная версия файла стилей: ${String(f.version)}. Поддерживается версия ${STYLE_FILE_VERSION}`);
  if (!Array.isArray(f.styles)) throw new Error("В файле нет списка стилей");
  for (const s of f.styles) {
    if (!s || typeof s.name !== "string" || !s.name.trim() || !["palette", "layer", "display"].includes(s.level) || !s.body || typeof s.body !== "object") {
      throw new Error(`Стиль «${String(s?.name ?? "?")}» в файле записан неверно`);
    }
  }
  return f as StyleFile;
}

export const conflictKey = (s: { level: string; name: string }) => `${s.level}:${s.name}`;

/** Entries of a file whose (level, name) are already in the library. */
export function fileConflicts(file: StyleFile, styles: StyleRec[]): StyleFileEntry[] {
  const have = new Set(styles.filter((s) => !s.builtin).map(conflictKey));
  return file.styles.filter((s) => have.has(conflictKey(s)));
}

// ---- built-in styles (read-only; the ids are negative)

const builtin = (id: number, name: string, data_kind: DataKind, body: LayerBody): StyleRec => ({ id, name, level: "layer", data_kind, body: body as Record<string, unknown>, default_for: [], builtin: true });

export const BUILTIN_STYLES: StyleRec[] = [
  builtin(-1, "Столбцы, один цвет", "number", { type: "bars", color: "#4f9dde", colorBy: "single", width: 90 }),
  builtin(-2, "Столбцы по градациям", "number", { type: "bars", colorBy: "classes", width: 90 }),
  builtin(-3, "Ступени с заливкой", "number", { type: "steps", fill: true, colorBy: "single", color: "#e8873a", opacity: 0.85, width: 90 }),
  builtin(-4, "Линия", "number", { type: "line", colorBy: "single", color: "#5fbf6a", width: 90 }),
  builtin(-5, "Трубка по шкале", "number", { type: "color", form: "tube", colorBy: "scale", gradient: { name: "classic", reverse: false, stretch: "log", min: "", max: "" } }),
  builtin(-6, "Лента по градациям", "number", { type: "color", form: "panel", colorBy: "classes", width: 45 }),
  builtin(-7, "Литология: трубка с подписями", "text", { type: "color_text", form: "tube", width: 110, boundaries: true }),
  builtin(-8, "События: значки", "event", { type: "event", marker: "diamond", size: 12, color: "#e8873a" }),
];
