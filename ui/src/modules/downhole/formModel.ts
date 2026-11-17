/**
 * Pure parts of the display form (ТЗ 7.16; ported from BF DownholeForm): the first layer of a new display,
 * a layer for a field with its default style, a template on a table, «Разложить», the settings to save.
 */
import { newTrack, newTrackId, type DisplayForm, type StripLogSettings, type StyleRef, type Track } from "../../charts/striplog/model";
import { isGrade } from "../../viz/downhole/context";
import { tubeTracks } from "../../viz/downhole/geometry";
import { applyLayerStyle, dataKind, defaultStyleFor, type StyleRec, type TemplateBody } from "../../viz/downhole/styles";
import type { IntervalTableInfo } from "../../viz/downhole/types";

export interface NewLayer {
  track: Track;
  /** the default style applied by itself (its mask matched the field) */
  auto: StyleRec | null;
}

/** A new layer for a field of a table: grades on a log scale, the default style of the field when the library has one. */
export function layerFor(tables: IntervalTableInfo[], tableKey: string, field: string, index: number, styles: StyleRec[]): NewLayer | null {
  const tb = tables.find((t) => t.key === tableKey);
  const c = tb?.columns.find((x) => x.name === field);
  if (!tb || !c) return null;
  const event = tb.kind === "event";
  let t = newTrack(tb.key, c, index, event);
  if (c.kind === "number" && !event) t = { ...t, log: isGrade(c.name, c.unit), colorBy: "single" };
  const style = defaultStyleFor(c.name, dataKind(t, c), styles);
  if (style) t = applyLayerStyle(t, style, false, { auto: true });
  return { track: t, auto: style };
}

/**
 * The first layer of a new display of a form: the asked field (holes.show_data), else the first suitable
 * table and field. Grades — bars in one colour, lithology — a tube with its text, events — markers.
 */
export function firstLayer(form: DisplayForm, tables: IntervalTableInfo[], styles: StyleRec[], hint?: { table?: string | null; field?: string | null }): NewLayer | null {
  if (hint?.field) {
    const want = hint.field.trim().toUpperCase();
    const q = (hint.table ?? "").trim().toLowerCase();
    const pool = q ? tables.filter((t) => t.key === q || t.name.toLowerCase() === q || String(t.id) === q) : tables;
    const tb = pool.find((t) => t.columns.some((c) => c.name.toUpperCase() === want));
    const c = tb?.columns.find((x) => x.name.toUpperCase() === want);
    if (tb && c) {
      const l = layerFor(tables, tb.key, c.name, 0, styles);
      if (l && !l.auto && form === "lith" && c.kind === "text") l.track = { ...l.track, type: "color_text", form: "tube" };
      return l;
    }
    return null;
  }
  if (form === "event") {
    const t = tables.find((x) => x.kind === "event" && x.columns.length);
    return t ? layerFor(tables, t.key, t.columns[0].name, 0, styles) : null;
  }
  const kind = form === "grade" ? "number" : "text";
  const t = tables.find((x) => x.kind !== "event" && x.columns.some((c) => c.kind === kind));
  const c = t?.columns.find((x) => x.kind === kind);
  if (!t || !c) return null;
  const l = layerFor(tables, t.key, c.name, 0, styles);
  if (!l) return null;
  if (!l.auto && form === "lith") l.track = { ...l.track, type: "color_text", form: "tube" };
  return l;
}

/** The form a saved display opens in: as it was made, else by its first layer. */
export function formOf(s: StripLogSettings): DisplayForm {
  if (s.form) return s.form;
  const t = s.tracks[0]?.type;
  return t === "event" ? "event" : t === "color" || t === "color_text" ? "lith" : "grade";
}

/**
 * The layers of a display template on a table of the set: the first table with a field of the template
 * (by name, without regard to case), else `fallback`. Fields the table lacks are left out.
 */
export function templateLayers(style: StyleRec, tables: IntervalTableInfo[], fallback?: IntervalTableInfo): { table: IntervalTableInfo; tracks: Track[] } | null {
  const body = style.body as unknown as TemplateBody;
  const fields = (body.tracks ?? []).map((x) => String(x.field).toUpperCase());
  const tb = tables.find((x) => x.columns.some((c) => fields.includes(c.name.toUpperCase()))) ?? fallback;
  if (!tb) return null;
  const tracks = (body.tracks ?? [])
    .map((x) => {
      const c = tb.columns.find((y) => y.name.toUpperCase() === String(x.field).toUpperCase());
      const { field: _f, ...rest } = x;
      return c ? { ...newTrack(tb.key, c, 0), ...rest, id: newTrackId(), table: tb.key, field: c.name } : null;
    })
    .filter((x): x is Track => !!x);
  return { table: tb, tracks };
}

/** «Разложить»: a tube on the hole, the other layers by turns to the right and to the left, offsets automatic. */
export function arrange(s: StripLogSettings): StripLogSettings {
  const tubes = tubeTracks(s.tracks);
  let k = 0;
  return {
    ...s,
    tracks: s.tracks.map((t) => {
      if (tubes.has(t.id) || t.type === "event") return { ...t, offsetM: undefined };
      const side = k++ % 2 === 0 ? "right" : "left";
      return { ...t, offsetM: undefined, split: null, side };
    }),
  };
}

/** What is saved: the mark «applied by itself» is not kept. */
export function cleanForSave(s: StripLogSettings): StripLogSettings {
  return { ...s, tracks: s.tracks.map((t) => (t.style?.auto ? { ...t, style: { ...t.style, auto: undefined } } : t)) };
}

/** A name not taken yet: «AU · столбцы», «AU · столбцы (2)»… (names of displays of a set are unique, without regard to case). */
export function uniqueName(name: string, taken: string[]): string {
  const have = new Set(taken.map((x) => x.trim().toLowerCase()));
  if (!have.has(name.trim().toLowerCase())) return name;
  for (let n = 2; ; n++) {
    const next = `${name} (${n})`;
    if (!have.has(next.toLowerCase())) return next;
  }
}

/** Every link to a style in a display (template, layer styles, colourings) through `fn`. */
export function mapRefs(s: StripLogSettings, fn: (r: StyleRef | undefined) => StyleRef | undefined): StripLogSettings {
  return { ...s, template: fn(s.template), tracks: s.tracks.map((t) => ({ ...t, style: fn(t.style), palette: fn(t.palette) })) };
}

/** Is this display the same as the saved one (the form is clean)? */
export const sameSettings = (a: StripLogSettings | null, b: StripLogSettings | null): boolean => !!a && !!b && JSON.stringify(a) === JSON.stringify(b);
