/**
 * What the display of a block model in the main 3D knows (ТЗ 8.7.6, 6.8; ADR 0023 §11, decisions 16–19;
 * docs/progress/e17-api.md §12, §14): the style of a model in the view of the project (`block_styles["<id>"]` — shown
 * or not, «Оболочка / Все блоки», the filter of the library, the quick restrictor, the colouring, the edges, the
 * opacity, the limit of faces), its reading and changing (`styleOp` merges into the stored entry, so settings made
 * elsewhere survive), the request to the core made of it (`shellRequest`) and the key that tells whether a change of
 * the style is a new request or only new colours (`requestKey`), the state of the display of every model (loading,
 * faces, «упрощено, LOD», the resolved range of the scale, warnings, a failure) and the block a click found. A change
 * of the colours or the look never reaches the core; a change of the field, the range, the breaks, the hidden
 * categories, the filter, the restrictor, the mode or the limit — does. A missing value stays «—», never 0. Pure: no
 * React, no three.js (`scene.ts` is the lazy chunk that draws).
 */
import { fieldNumber, normalizeColor, num, type Fact } from "../../shell/props";
import { Store } from "../../shell/store";
import { count, counted } from "../../shell/treeActions";
import type { BlocksHeader, ColourKind, ShowMode } from "../../viz/blocks/decode";
import type { LegendStyle } from "../../viz/blocks/legend";
import type { BlocksLook } from "../../viz/blocks/material";
import type { ViewOp } from "../scene/viewSync";
import type { BlockField, BlockModelInfo } from "./api";
import { checkDraft, COND_OPS, ruleToDraft, type CondBody, type Rule } from "./filters/constructor";
import type { AtAnswer, ColourBody, ShellBody, Triple } from "./showApi";

export type { ShowMode };

export const BLOCK_STYLES_KEY = "block_styles";
/** Faces: the core takes 500 000 – 10 000 000, 5 000 000 by default (ТЗ 6.8). */
export const MIN_LIMIT = 500_000;
export const MAX_LIMIT = 10_000_000;
export const DEFAULT_LIMIT = 5_000_000;

// ---------------------------------------------------------------------------------------------------- the style

export interface QuickRestrict {
  items: CondBody[];
  join: "and" | "or";
  invert: boolean;
  /** a tree of the rules put by the transition from a cell of the report (20.15, `resources.drill`): joined to the items by «И», not inverted; absent — none */
  rule?: Rule | null;
}

export interface CategoryStyle {
  /** `#rrggbb`; null — the colour of the brand's cycle */
  colour: string | null;
  /** false — the category is hidden (its blocks do not pass: `colour.hidden` of the request) */
  on: boolean;
}

export interface ColourStyle {
  field: string | null;
  kind: ColourKind;
  /** the range of the scale; null — the core takes the minimum / maximum of the field */
  from: number | null;
  to: number | null;
  log: boolean;
  /** the name of the palette of the window «Блочная модель»; the colours themselves are `colours` */
  palette: string;
  breaks: number[];
  /** the colour of every class (scale — 256, breaks — one per class); null — the default of the kind */
  colours: (string | null)[];
  categories: Record<string, CategoryStyle>;
}

export interface EdgesStyle {
  /** null — by the mode (the shell has none, «все блоки» have them) */
  on: boolean | null;
  colour: string | null;
  width: number | null;
}

export type SlicePlane = "section" | { origin: Triple; normal: Triple };

export interface SliceStyle {
  on: boolean;
  plane: SlicePlane;
}

export interface BlockStyle {
  visible: boolean;
  mode: ShowMode;
  filter_id: number | null;
  quick: QuickRestrict;
  colour: ColourStyle;
  edges: EdgesStyle;
  opacity: number;
  limit: number;
  slice: SliceStyle;
  /** [xmin, ymin, zmin, xmax, ymax, zmax] of the part to show; null — all */
  extent: number[] | null;
}

export interface StylePatch {
  visible?: boolean;
  mode?: ShowMode;
  filter_id?: number | null;
  quick?: Partial<QuickRestrict>;
  colour?: Partial<ColourStyle>;
  edges?: Partial<EdgesStyle>;
  opacity?: number;
  limit?: number;
  slice?: Partial<SliceStyle>;
  extent?: number[] | null;
}

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const triple = (v: unknown): Triple | null => (Array.isArray(v) && v.length === 3 && v.every(isNum) ? [v[0], v[1], v[2]] : null);
const KINDS: readonly ColourKind[] = ["none", "scale", "classes", "categories"];

/** A number as the user writes it: «0,5», «1e-3», a number. */
export function numberOf(v: unknown): number | null {
  if (isNum(v)) return v;
  if (typeof v !== "string" || !v.trim()) return null;
  const n = Number(v.trim().replace(/\s/g, "").replace(",", "."));
  return Number.isFinite(n) ? n : null;
}

export const EMPTY_STYLE: BlockStyle = {
  visible: false,
  mode: "shell",
  filter_id: null,
  quick: { items: [], join: "and", invert: false },
  colour: { field: null, kind: "none", from: null, to: null, log: false, palette: "viridis", breaks: [], colours: [], categories: {} },
  edges: { on: null, colour: null, width: null },
  opacity: 1,
  limit: DEFAULT_LIMIT,
  slice: { on: false, plane: "section" },
  extent: null,
};

const condOf = (v: unknown): CondBody | null => {
  if (!isObj(v) || typeof v.field !== "string" || !v.field.trim()) return null;
  const op = COND_OPS.find((o) => o.op === v.op)?.op;
  if (!op) return null;
  const cond: CondBody = { field: v.field.trim(), op };
  if (Array.isArray(v.values)) cond.values = v.values.filter((x): x is string | number => isNum(x) || typeof x === "string");
  if (isNum(v.value) || typeof v.value === "string") cond.value = v.value;
  return cond;
};

/** The entry of `block_styles` as stored, tolerant: anything wrong is the default (the core keeps what it is given). */
export function readStyle(raw: unknown): BlockStyle {
  const d = EMPTY_STYLE;
  const o = isObj(raw) ? raw : {};
  const q = isObj(o.quick) ? o.quick : {};
  const c = isObj(o.colour) ? o.colour : {};
  const e = isObj(o.edges) ? o.edges : {};
  const s = isObj(o.slice) ? o.slice : {};
  const plane = isObj(s.plane) && triple(s.plane.origin) && triple(s.plane.normal) ? { origin: triple(s.plane.origin)!, normal: triple(s.plane.normal)! } : "section";
  const categories: Record<string, CategoryStyle> = {};
  if (isObj(c.categories)) {
    for (const [code, v] of Object.entries(c.categories)) {
      if (/^\d{1,5}$/.test(code) && isObj(v)) categories[code] = { colour: normalizeColor(v.colour), on: v.on !== false };
    }
  }
  const fid = o.filter_id;
  const extent = Array.isArray(o.extent) && o.extent.length === 6 && o.extent.every(isNum) ? (o.extent as number[]) : null;
  return {
    visible: o.visible === true,
    mode: o.mode === "all" ? "all" : "shell",
    filter_id: typeof fid === "number" && Number.isInteger(fid) && fid > 0 ? fid : null,
    quick: {
      items: Array.isArray(q.items) ? q.items.map(condOf).filter((x): x is CondBody => x !== null) : [],
      join: q.join === "or" ? "or" : "and",
      invert: q.invert === true,
      ...(isObj(q.rule) && { rule: q.rule as unknown as Rule }),
    },
    colour: {
      field: typeof c.field === "string" && c.field ? c.field : null,
      kind: KINDS.includes(c.kind as ColourKind) ? (c.kind as ColourKind) : d.colour.kind,
      from: isNum(c.from) ? c.from : null,
      to: isNum(c.to) ? c.to : null,
      log: c.log === true,
      palette: typeof c.palette === "string" && c.palette ? c.palette : d.colour.palette,
      breaks: Array.isArray(c.breaks) ? c.breaks.filter(isNum) : [],
      colours: Array.isArray(c.colours) ? c.colours.map((x) => normalizeColor(x)) : [],
      categories,
    },
    edges: { on: typeof e.on === "boolean" ? e.on : null, colour: normalizeColor(e.colour), width: isNum(e.width) && e.width > 0 ? e.width : null },
    opacity: isNum(o.opacity) ? Math.min(1, Math.max(0.05, o.opacity)) : 1,
    limit: typeof o.limit === "number" && Number.isInteger(o.limit) && o.limit >= MIN_LIMIT && o.limit <= MAX_LIMIT ? o.limit : DEFAULT_LIMIT,
    slice: { on: s.on === true, plane },
    extent,
  };
}

/** The style of a model in the keys of the view (`viewStore.get()`): the entry, or the default (not shown). */
export function styleIn(keys: Record<string, unknown>, id: number): BlockStyle {
  const map = keys[BLOCK_STYLES_KEY];
  return readStyle(isObj(map) ? map[String(id)] : undefined);
}

/** The models shown now, by the view: id → style. */
export function shownStyles(keys: Record<string, unknown>): Map<number, BlockStyle> {
  const map = keys[BLOCK_STYLES_KEY];
  const out = new Map<number, BlockStyle>();
  if (!isObj(map)) return out;
  for (const [k, v] of Object.entries(map)) {
    const style = readStyle(v);
    if (/^\d+$/.test(k) && style.visible) out.set(Number(k), style);
  }
  return out;
}

const write = (s: BlockStyle): Record<string, unknown> => ({
  visible: s.visible,
  mode: s.mode,
  filter_id: s.filter_id,
  quick: { items: s.quick.items, join: s.quick.join, invert: s.quick.invert, ...(s.quick.rule && { rule: s.quick.rule }) },
  colour: { ...s.colour },
  edges: { ...s.edges },
  opacity: s.opacity,
  limit: s.limit,
  slice: { on: s.slice.on, plane: s.slice.plane },
  extent: s.extent,
});

/** The style with a patch on top: the parts (`quick`, `colour`, `edges`, `slice`) are merged key by key. */
export function mergeStyle(base: BlockStyle, patch: StylePatch): BlockStyle {
  return {
    ...base,
    ...(patch.visible !== undefined && { visible: patch.visible }),
    ...(patch.mode !== undefined && { mode: patch.mode }),
    ...(patch.filter_id !== undefined && { filter_id: patch.filter_id }),
    ...(patch.opacity !== undefined && { opacity: patch.opacity }),
    ...(patch.limit !== undefined && { limit: patch.limit }),
    ...(patch.extent !== undefined && { extent: patch.extent }),
    quick: { ...base.quick, ...patch.quick },
    colour: { ...base.colour, ...patch.colour },
    edges: { ...base.edges, ...patch.edges },
    slice: { ...base.slice, ...patch.slice },
  };
}

/**
 * The change of one entry of `block_styles` as an operation of the view (`updateView`): it reads the entry that is
 * stored when it runs, so a change made in another window is merged. Keys of the entry this module does not know are
 * kept (the order of the entries is that of an object: numbers ascending).
 */
export function styleOp(id: number, patch: StylePatch): ViewOp {
  return (v) => {
    const map: Record<string, unknown> = isObj(v) ? { ...v } : {};
    const key = String(id);
    const raw = isObj(map[key]) ? (map[key] as Record<string, unknown>) : {};
    const next = { ...raw, ...write(mergeStyle(readStyle(raw), patch)) };
    delete map[key];
    map[key] = next;
    return map;
  };
}

// ----------------------------------------------------------------------------------------- the style → the colours

/** The hidden categories of the style: the codes that are switched off. */
export const hiddenCodes = (c: ColourStyle): number[] =>
  Object.entries(c.categories)
    .filter(([, v]) => !v.on)
    .map(([k]) => Number(k))
    .sort((a, b) => a - b);

/** The colours of the classes the legend texture takes (`BlocksLayer.setStyle`): the list of the style, the categories on top. */
export function legendStyleOf(c: ColourStyle): LegendStyle {
  const colors: (string | null)[] = c.colours.slice();
  for (const [code, v] of Object.entries(c.categories)) if (v.colour) colors[Number(code)] = v.colour;
  return colors.some((x) => x !== null && x !== undefined) ? { colors } : {};
}

/** The look of the layer: only what the style says (the rest is the default of the mode of the answer). */
export function lookOf(s: BlockStyle): Partial<BlocksLook> {
  return {
    opacity: s.opacity,
    ...(s.edges.on !== null && { edges: s.edges.on }),
    ...(s.edges.colour !== null && { edgeColor: s.edges.colour }),
    ...(s.edges.width !== null && { edgeWidth: s.edges.width }),
  };
}

// ----------------------------------------------------------------------------------------- the style → the request

const NO_FIELD = (field: string): string => `Поля «${field}» нет в этой модели.`;

/** The kind the field can be coloured by: a number — a scale or breaks, a category — its codes; none — as it is. */
export function kindFor(kind: ColourKind, field: BlockField | undefined): ColourKind {
  if (kind === "none" || !field) return kind;
  if (field.kind === "category") return "categories";
  return kind === "categories" ? "scale" : kind;
}

/** The restrictor of the window as the rules of the core: the conditions joined by И / ИЛИ, «обратный» — НЕ. Null — none. */
export function restrictOf(q: QuickRestrict): Rule | null {
  const items: Rule[] = q.items.map((cond) => ({ cond }));
  const base: Rule | null = !items.length ? null : (() => {
    const rule: Rule = items.length === 1 ? items[0] : { op: q.join, items };
    return q.invert ? { op: "not", items: [rule] } : rule;
  })();
  if (!q.rule) return base;
  return base ? { op: "and", items: [base, q.rule] } : q.rule;
}

/** The first thing wrong in the restrictor, in Russian (the same check as the window «Фильтры»); null — fine. */
export function quickIssue(q: QuickRestrict): string | null {
  const rule = restrictOf(q);
  if (!rule) return null;
  const r = checkDraft(ruleToDraft(rule));
  return r.ok ? null : `${r.issue.message} ${r.issue.hint}`.trim();
}

/** What is wrong in the colouring of the style before it goes to the core (the same words as the core's), or null. */
export function colourIssue(c: ColourStyle, model: Pick<BlockModelInfo, "fields"> | undefined): string | null {
  if (c.kind === "none") return null;
  if (!c.field) return "Для раскраски нужно поле. Выберите поле в окне блочной модели.";
  const field = model?.fields.find((f) => f.name === c.field);
  if (model && !field) return `${NO_FIELD(c.field)} Выберите поле из списка.`;
  const kind = kindFor(c.kind, field);
  if (kind === "classes") {
    if (c.breaks.length < 1 || c.breaks.length > 255 || c.breaks.some((b, i) => i > 0 && !(b > c.breaks[i - 1]))) return "Границы градаций: от 1 до 255 чисел строго по возрастанию. Например 0,5; 1; 2.";
  }
  if (kind === "scale") {
    if (c.from !== null && c.to !== null && c.to <= c.from) return "Граница шкалы «до» должна быть больше границы «от».";
    if (c.log && c.from !== null && c.from <= 0) return "Логарифмическая шкала требует границы «от» больше нуля. Задайте границу больше нуля или выключите логарифмическую шкалу.";
  }
  return null;
}

/** The colour of the request: kind by the field, the range only for a scale, the breaks for the classes, the hidden codes for the categories. */
export function colourBody(c: ColourStyle, fields: readonly BlockField[] | undefined): ColourBody {
  const field = c.field ? fields?.find((f) => f.name === c.field) : undefined;
  const kind = c.field ? kindFor(c.kind, field) : "none";
  if (kind === "none" || !c.field) return { field: null, kind: "none" };
  const out: ColourBody = { field: c.field, kind };
  if (kind === "scale") {
    if (c.from !== null) out.from = c.from;
    if (c.to !== null) out.to = c.to;
    if (c.log) out.log = true;
  } else if (kind === "classes") out.breaks = [...c.breaks];
  else if (kind === "categories") {
    const hidden = hiddenCodes(c);
    if (hidden.length) out.hidden = hidden;
  }
  return out;
}

/** The keys every request of the display has: the filter of the library and the quick restrictor (the slice and the point take them too). */
export function commonRequest(s: BlockStyle): { filter_id?: number; restrict?: Rule } {
  const restrict = restrictOf(s.quick);
  return { ...(s.filter_id !== null && { filter_id: s.filter_id }), ...(restrict && { restrict }) };
}

/** The body of `POST /viz/blocks/{id}/shell` (without `channel` and `seq`), e17-api §12. */
export function shellRequest(s: BlockStyle, fields?: readonly BlockField[]): ShellBody {
  return {
    mode: s.mode,
    limit: s.limit,
    ...commonRequest(s),
    colour: colourBody(s.colour, fields),
    ...(s.extent && { extent: s.extent }),
  };
}

/**
 * What decides the faces: the same key — the same answer. The version of the model and of the filter are in it (an
 * edited filter or a recalculated field is a new request); colours and look are not.
 */
export function requestKey(model: number, version: number, filterVersion: number | null, body: ShellBody): string {
  return JSON.stringify([model, version, filterVersion, body]);
}

// ------------------------------------------------------------------------------------------- the state of the display

/** What a model shows now (the answer of the core) or why not. */
export interface ShowInfo {
  model: number;
  status: "loading" | "ready" | "error";
  /** the last answer; null before the first one */
  header: BlocksHeader | null;
  /** the highest class of the faces that has a value (for the legend) */
  maxClass: number;
  /** the warnings of the restrictor the core made (a leaf that did not apply) */
  warnings: string[];
  error: string | null;
  /** ms the last request took, from the send to the layer in the scene */
  ms: number | null;
  /** requests sent for this model (the E2E and the tests count them) */
  requests: number;
}

export interface ShowState {
  models: Record<number, ShowInfo>;
}

export const blocksShowStore = new Store<ShowState>({ models: {} });

export const showInfo = (id: number): ShowInfo | undefined => blocksShowStore.get().models[id];

export function setShowInfo(id: number, patch: Partial<ShowInfo>): void {
  blocksShowStore.set((s) => {
    const cur = s.models[id] ?? { model: id, status: "loading" as const, header: null, maxClass: -1, warnings: [], error: null, ms: null, requests: 0 };
    return { models: { ...s.models, [id]: { ...cur, ...patch } } };
  });
}

export function dropShowInfo(id: number): void {
  blocksShowStore.set((s) => {
    if (!(id in s.models)) return {};
    const models = { ...s.models };
    delete models[id];
    return { models };
  });
}

export const resetShow = (): void => blocksShowStore.set({ models: {} });

/** Models whose first answer after «Показать в 3D» brings the camera to them (`blocks.show`; a project that opens does not). */
export const revealOnShow = new Set<number>();

/** Some model is drawn coarser than it is (LOD): the status bar says «упрощено». */
export const blocksSimplified = (s: ShowState = blocksShowStore.get()): boolean => Object.values(s.models).some((m) => m.status !== "error" && m.header?.simplified === true);

/** «Ячейки 1 234 грани; прошло 211 из 225 блоков; упрощено: родители 4 × 4 × 4 (LOD 2), граней до упрощения 120 000 000». */
export function showText(info: ShowInfo, name: string): string {
  if (info.status === "error") return `Блоки «${name}»: ${info.error ?? "не показаны"}`;
  const h = info.header;
  if (!h) return `Блоки «${name}»: запрос показа…`;
  const parts = [counted(h.faces, "грань", "грани", "граней")];
  if (h.rows > 0) parts.push(`прошло ${count(h.passed)} из ${counted(h.rows, "блока", "блоков", "блоков")}`);
  if (h.simplified) {
    const side = 2 ** h.lod;
    parts.push(`упрощено: родители ${side} × ${side} × ${side} (LOD ${h.lod}), граней до упрощения ${count(h.faces_raw)}`);
  }
  if (info.status === "loading") parts.push("обновляется…");
  return `Блоки «${name}»: ${parts.join("; ")}`;
}

/** The lines of the status bar, one per model shown; none — null. */
export function showStatus(models: readonly Pick<BlockModelInfo, "id" | "name">[], s: ShowState = blocksShowStore.get()): string | null {
  const lines = Object.values(s.models).map((m) => showText(m, models.find((b) => b.id === m.model)?.name ?? `№${m.model}`));
  return lines.length ? lines.join(" · ") : null;
}

// ---------------------------------------------------------------------------------------------------- the block clicked

/** The block under a click (`POST /viz/blocks/{id}/at`) or why there is none; the newest click only. */
export interface PickedBlock {
  model: number;
  /** the click, project coordinates float64 */
  point: Triple;
  status: "loading" | "found" | "missing" | "error";
  answer: AtAnswer | null;
  error: string | null;
}

export const pickedBlockStore = new Store<{ picked: PickedBlock | null }>({ picked: null });

export const setPicked = (picked: PickedBlock | null): void => pickedBlockStore.set({ picked });

const AXES = ["X", "Y", "Z"] as const;

/** A value of a field as the panel writes it: a number with its unit, a label, «—» for no value (never 0). */
export function valueText(v: number | string | null | undefined, field?: Pick<BlockField, "unit" | "kind">): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  const unit = field?.unit ? ` ${field.unit}` : "";
  return `${num(v, 4)}${unit}`;
}

/** «Свойства» → «Выбранный блок»: the cell, the centre, the size, the sub-block, the filter and the values of the fields. */
export function pickedFacts(p: PickedBlock | null, model: Pick<BlockModelInfo, "name" | "fields"> | undefined): Fact[] {
  if (!p) return [];
  const where: Fact = { label: "Точка щелчка", value: p.point.map((v, i) => `${AXES[i]} ${fieldNumber(v, 2)}`).join(", ") };
  if (p.status === "loading") return [{ label: "Блок", value: "ищется…" }, where];
  if (p.status === "error") return [{ label: "Блок", value: p.error ?? "не найден" }, where];
  const a = p.answer;
  if (p.status === "missing" || !a || !a.found) return [{ label: "Блок", value: "в этой точке блока нет" }, where];
  const facts: Fact[] = [
    { label: "Ячейка (I, J, K с нуля)", value: a.ijk.join(", ") },
    { label: "Центр", value: a.centre.map((v, i) => `${AXES[i]} ${fieldNumber(v, 3)}`).join(", ") },
    { label: "Размер", value: `${a.size.map((v) => num(v, 3)).join(" × ")} м` },
  ];
  if (a.sub) facts.push({ label: "Субблок", value: `смещение ${a.sub.slice(0, 3).join(", ")}; размер ${a.sub.slice(3).join(" × ")} подъячеек` });
  facts.push({ label: "Фильтр", value: a.passed ? "блок проходит" : "блок скрыт фильтром" });
  for (const [name, v] of Object.entries(a.values)) facts.push({ label: name, value: valueText(v, model?.fields.find((f) => f.name === name)) });
  facts.push(where);
  return facts;
}

/** One line for the status bar and the answer of `blocks.pick`. */
export function pickedText(p: PickedBlock, model: Pick<BlockModelInfo, "name" | "fields"> | undefined): string {
  const name = model?.name ?? `№${p.model}`;
  if (p.status === "loading") return `Модель «${name}»: блок ищется…`;
  if (p.status === "error") return `Модель «${name}»: ${p.error ?? "блок не найден"}`;
  const a = p.answer;
  if (p.status === "missing" || !a || !a.found) return `Модель «${name}»: в этой точке блока нет.`;
  const vals = Object.entries(a.values)
    .map(([k, v]) => `${k} ${valueText(v, model?.fields.find((f) => f.name === k))}`)
    .join("; ");
  const hidden = a.passed ? "" : " (скрыт фильтром)";
  return `Модель «${name}»: блок (${a.ijk.join(", ")}), центр ${a.centre.map((v) => fieldNumber(v, 3)).join(", ")}, размер ${a.size.map((v) => num(v, 3)).join(" × ")} м${hidden}${vals ? `; ${vals}` : ""}.`;
}

/** The kinds of colouring as the window words them. */
export const COLOUR_KINDS_TEXT: Record<ColourKind, string> = { none: "без раскраски", scale: "шкала", classes: "градации", categories: "категории" };
