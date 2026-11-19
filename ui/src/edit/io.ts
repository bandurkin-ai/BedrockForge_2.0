/**
 * The stored form of a line layer and the editor's working copy (ADR 0015 §2, §3; BF `edit/io.ts`).
 *
 * In the store (and in STR / DXF files) a closed line repeats its first vertex at the end; the working copy
 * keeps a closed line without the repeat. Coordinates pass through untouched (float64, NaN Z and −0.0 too).
 *
 * A line is "changed" when its points (or its closed flag) are not the objects loaded: an untouched line is
 * sent by reference only, and the core keeps its vertices as the base version has them, bit for bit (a closed
 * line from a file without the repeated vertex stays so).
 */
import { decodeBfa1 } from "../viz/format/bfa1";
import { encodeBfa1, type Bfa1Entry } from "./bfa1";
import { cleanAttrs, fromArrays, NEW_LINE, toArrays, type AttrValue, type EditLayer, type EditLine, type Vec3 } from "./model";

export interface StoredLines {
  vertices: Float64Array;
  /** start of every line, plus the end */
  offsets: Uint32Array;
  closed: Uint8Array;
  attributes: Record<string, AttrValue>[];
  src: Int32Array;
}

/** An error of the editor in the form of the core: `{code, message, hint}` in Russian. */
export class EditError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly hint: string,
  ) {
    super(message);
    this.name = "EditError";
  }
}

/** Stored arrays → working copy: the repeated closing vertex of closed lines is dropped. */
export function fromStored(
  vertices: Float64Array,
  offsets: ArrayLike<number>,
  closed: ArrayLike<number>,
  attributes: Record<string, unknown>[],
  lid?: ArrayLike<number>,
): EditLayer {
  const lines = closed.length;
  const out = new Float64Array(vertices.length);
  const starts = new Uint32Array(lines + 1);
  let n = 0;
  for (let l = 0; l < lines; l++) {
    const a = offsets[l];
    let b = offsets[l + 1];
    if (closed[l] && b - a > 1 && sameBits(vertices, a, b - 1)) b -= 1;
    out.set(vertices.subarray(a * 3, b * 3), n * 3);
    n += b - a;
    starts[l + 1] = n;
  }
  return fromArrays(out.subarray(0, n * 3), starts, closed, attributes, lid);
}

/** Vertices i and j are the same bit for bit (NaN equals NaN, −0.0 differs from 0.0). */
function sameBits(v: Float64Array, i: number, j: number): boolean {
  for (let c = 0; c < 3; c++) if (!Object.is(v[i * 3 + c], v[j * 3 + c])) return false;
  return true;
}

/** Points of a line in the storage form: a closed line of 3+ points repeats its first one. */
function storedCount(line: EditLine): number {
  return line.points.length + (repeatsFirst(line) ? 1 : 0);
}

function repeatsFirst(line: EditLine): boolean {
  return line.closed && line.points.length >= 3;
}

/** Working copy → stored arrays: closed lines get their first vertex repeated. A closed line with fewer than 3 points is stored open. */
export function toStored(layer: EditLayer): StoredLines {
  const a = toArrays(layer);
  const lines = a.closed.length;
  let extra = 0;
  for (const line of layer.lines) if (repeatsFirst(line)) extra++;
  const vertices = new Float64Array(a.vertices.length + extra * 3);
  const offsets = new Uint32Array(lines + 1);
  const closed = new Uint8Array(lines);
  let n = 0;
  for (let l = 0; l < lines; l++) {
    const s = a.offsets[l];
    const e = a.offsets[l + 1];
    vertices.set(a.vertices.subarray(s * 3, e * 3), n * 3);
    n += e - s;
    if (a.closed[l] && e - s >= 3) {
      vertices.set(a.vertices.subarray(s * 3, s * 3 + 3), n * 3);
      n += 1;
      closed[l] = 1;
    }
    offsets[l + 1] = n;
  }
  return { vertices, offsets, closed, attributes: a.attributes, src: a.src };
}

/** A line of the base version as it was loaded. */
export interface BaseLine {
  line: EditLine;
  /** vertices in the storage form */
  count: number;
  /** the stored flag */
  closed: number;
}

/** A layer read from `GET /lines/layers/{id}/geometry` (BFA1 `linegeom`). */
export interface LoadedLayer {
  layerId: number;
  version: number;
  pointsOnly: boolean;
  /** the working copy as loaded */
  layer: EditLayer;
  /** the loaded lines by `lid` */
  base: Map<number, BaseLine>;
}

export const ROLE_FIELD = "ROLE";
export const TIE = "TIE";

const READ_HINT = "Перечитайте слой. Если ошибка повторяется — перезапустите ядро и сообщите разработчику.";

function bad(what: string): EditError {
  return new EditError("bad_linegeom", `Геометрия слоя повреждена: ${what}.`, READ_HINT);
}

/** Reads the BFA1 `linegeom` of a layer. Tie lines get `ROLE = TIE` (the core marks them in `ties`). */
export function readLineGeometry(data: ArrayBufferLike | ArrayBufferView): LoadedLayer {
  const { header, arrays } = decodeBfa1<Record<string, unknown>>(data);
  if (header.kind !== "linegeom") throw bad("это не геометрия слоя линий");
  const { xyz, offsets, closed, lid } = arrays;
  if (!(xyz instanceof Float64Array) || !(offsets instanceof Uint32Array) || !(closed instanceof Uint8Array) || !(lid instanceof Uint32Array)) {
    throw bad("нет массивов xyz, offsets, closed или lid нужного типа");
  }
  const lines = closed.length;
  if (offsets.length !== lines + 1 || lid.length !== lines || xyz.length % 3 !== 0) throw bad("длины массивов не согласованы");
  if (offsets[0] !== 0 || offsets[lines] !== xyz.length / 3) throw bad("начала линий не охватывают вершины");
  for (let l = 0; l < lines; l++) if (offsets[l + 1] < offsets[l]) throw bad("начала линий не по порядку");
  const ties = new Set(Array.isArray(header.ties) ? (header.ties as unknown[]).filter((t): t is number => typeof t === "number") : []);
  const attributes: Record<string, unknown>[] = new Array(lines);
  for (let l = 0; l < lines; l++) attributes[l] = ties.has(l) ? { [ROLE_FIELD]: TIE } : {};
  const layer = fromStored(xyz, offsets, closed, attributes, lid);
  const base = new Map<number, BaseLine>();
  layer.lines.forEach((line, l) => base.set(line.src, { line, count: offsets[l + 1] - offsets[l], closed: closed[l] }));
  return {
    layerId: Number(header.layer_id),
    version: Number(header.version),
    pointsOnly: header.points_only === true,
    layer,
    base,
  };
}

/** A line whose geometry is the one loaded (it is sent by reference). */
export function isUntouched(line: EditLine, loaded: LoadedLayer): boolean {
  const b = line.src >= 0 ? loaded.base.get(line.src) : undefined;
  return !!b && b.line.points === line.points && b.line.closed === line.closed;
}

/** "Changed" = the line object is not the one loaded (geometry or attributes). */
export function isChanged(line: EditLine, loaded: LoadedLayer): boolean {
  const b = line.src >= 0 ? loaded.base.get(line.src) : undefined;
  return !b || b.line !== line;
}

/** Attributes to set on the line at saving: the differences from its source (a removed field → null). */
function attrsDiff(attrs: Record<string, AttrValue>, from: Record<string, AttrValue> | undefined): Record<string, AttrValue> | null {
  const out: Record<string, AttrValue> = {};
  let any = false;
  for (const key of Object.keys(attrs)) {
    if (!from || !Object.is(from[key], attrs[key])) {
      out[key] = attrs[key];
      any = true;
    }
  }
  if (from) {
    for (const key of Object.keys(from)) {
      if (!(key in attrs) && from[key] !== null) {
        out[key] = null;
        any = true;
      }
    }
  }
  return any ? out : null;
}

export interface EditBody {
  src: Int32Array;
  touched: Uint8Array;
  count: Uint32Array;
  closed: Uint8Array;
  xyz: Float64Array;
  newAttrs: Record<string, Record<string, AttrValue>>;
}

export interface SaveSummary {
  lines: number;
  /** lines sent with their vertices (changed and new) */
  touched: number;
  /** lines without a source */
  created: number;
  /** loaded lines not in the new layer */
  removed: number;
  vertices: number;
}

/** The body of one layer: every non-empty line in its order, vertices of touched and new lines only. */
export function editBody(lines: readonly EditLine[], loaded: LoadedLayer): EditBody {
  const kept = lines.filter((l) => l.points.length > 0);
  const n = kept.length;
  const src = new Int32Array(n);
  const touched = new Uint8Array(n);
  const count = new Uint32Array(n);
  const closed = new Uint8Array(n);
  const newAttrs: Record<string, Record<string, AttrValue>> = {};
  let sent = 0;
  kept.forEach((line, i) => {
    const b = line.src >= 0 ? loaded.base.get(line.src) : undefined;
    src[i] = b ? line.src : NEW_LINE;
    if (b && isUntouched(line, loaded)) {
      count[i] = b.count;
      closed[i] = b.closed;
    } else {
      touched[i] = 1;
      count[i] = storedCount(line);
      closed[i] = repeatsFirst(line) ? 1 : 0;
      sent += count[i];
    }
    const diff = attrsDiff(line.attrs, b?.line.attrs);
    if (diff) newAttrs[String(i)] = diff;
  });
  const xyz = new Float64Array(sent * 3);
  let k = 0;
  kept.forEach((line, i) => {
    if (!touched[i]) return;
    for (const p of line.points) k = put(xyz, k, p);
    if (repeatsFirst(line)) k = put(xyz, k, line.points[0]);
  });
  return { src, touched, count, closed, xyz, newAttrs };
}

function put(xyz: Float64Array, k: number, p: Vec3): number {
  xyz[k] = p[0];
  xyz[k + 1] = p[1];
  xyz[k + 2] = p[2];
  return k + 3;
}

function entries(body: EditBody, prefix = ""): Bfa1Entry[] {
  return [
    [prefix + "src", "<i4", body.src],
    [prefix + "touched", "<u1", body.touched],
    [prefix + "count", "<u4", body.count],
    [prefix + "closed", "<u1", body.closed],
    [prefix + "xyz", "<f8", body.xyz],
  ];
}

export function summarize(layer: EditLayer, loaded: LoadedLayer): SaveSummary {
  const present = new Set<number>();
  const out: SaveSummary = { lines: 0, touched: 0, created: 0, removed: 0, vertices: 0 };
  for (const line of layer.lines) {
    if (line.points.length === 0) continue;
    out.lines++;
    out.vertices += line.points.length;
    if (line.src >= 0 && loaded.base.has(line.src)) present.add(line.src);
    else out.created++;
    if (!isUntouched(line, loaded)) out.touched++;
  }
  for (const lid of loaded.base.keys()) if (!present.has(lid)) out.removed++;
  return out;
}

/** `PUT /lines/layers/{id}/geometry`: the body of a save of the working copy over the loaded version. */
export function saveBody(layer: EditLayer, loaded: LoadedLayer, note = ""): ArrayBuffer {
  const body = editBody(layer.lines, loaded);
  return encodeBfa1({ base_version: loaded.version, note, new_attrs: body.newAttrs }, entries(body));
}

/**
 * `POST /lines/layers/{id}/move-lines` (`lines.move_selected`): the source saved without the lines `ids`,
 * the lines appended to the target layer. Only stored lines move: a new line has to be saved first.
 */
export function moveBody(layer: EditLayer, loaded: LoadedLayer, ids: readonly number[], targetId: number, targetBase: number, note = ""): ArrayBuffer {
  const chosen = new Set(ids);
  const moved = layer.lines.filter((l) => chosen.has(l.id) && l.points.length > 0);
  if (moved.length === 0) throw new EditError("nothing_to_move", "Не выбраны линии для переноса.", "Выберите линии и повторите перенос.");
  if (moved.some((l) => !(l.src >= 0 && loaded.base.has(l.src)))) {
    throw new EditError("move_unsaved", "Среди выбранных есть новые линии — их ещё нет в слое.", "Сохраните слой и повторите перенос.");
  }
  const source = editBody(layer.lines.filter((l) => !chosen.has(l.id)), loaded);
  const lines = editBody(moved, loaded);
  return encodeBfa1(
    { base_version: loaded.version, target_id: targetId, target_base: targetBase, note, new_attrs: source.newAttrs, moved_attrs: lines.newAttrs },
    [...entries(source), ...entries(lines, "moved_")],
  );
}

/**
 * The working copy after a save (or a move of `moved` lines out of it) over the layer the core gives back: the
 * lines keep their ids and points (so they are untouched against the new base) and take their new stable numbers.
 * Null when the answer does not line up with the working copy line by line.
 */
export function rebaseOnSaved(layer: EditLayer, reloaded: LoadedLayer, moved: readonly number[] = []): { layer: EditLayer; loaded: LoadedLayer } | null {
  const gone = new Set(moved);
  const present = layer.lines.filter((l) => l.points.length > 0 && !gone.has(l.id));
  const back = reloaded.layer.lines;
  if (present.length !== back.length || present.some((l, i) => l.points.length !== back[i].points.length || l.closed !== back[i].closed)) return null;
  const base = new Map(reloaded.base);
  const lines = present.map((l, i) => {
    const line = l.src === back[i].src ? l : { ...l, src: back[i].src };
    const b = reloaded.base.get(line.src);
    if (b) base.set(line.src, { ...b, line });
    return line;
  });
  const next: EditLayer = { lines, nextId: layer.nextId };
  return { layer: next, loaded: { ...reloaded, layer: next, base } };
}

/**
 * A layer as the core would give it back after a save of `layer` (`lid` for new lines after the greatest one):
 * a new empty layer (`lines.new_layer`) and tests. Untouched closed lines are stored with the repeated vertex.
 */
export function asLoaded(layerId: number, version: number, layer: EditLayer, pointsOnly = false): LoadedLayer {
  let next = 0;
  for (const line of layer.lines) if (line.src >= next) next = line.src + 1;
  const seen = new Set<number>();
  const lines = layer.lines
    .filter((l) => l.points.length > 0)
    .map((l) => {
      const src = l.src >= 0 && !seen.has(l.src) ? l.src : next++;
      seen.add(src);
      return src === l.src ? l : { ...l, src };
    });
  const base = new Map<number, BaseLine>();
  for (const line of lines) base.set(line.src, { line, count: storedCount(line), closed: repeatsFirst(line) ? 1 : 0 });
  return { layerId, version, pointsOnly, layer: { lines, nextId: layer.nextId }, base };
}

/** A working copy made from plain stored data (tests, `lines.add_line` of the assistant). */
export function layerOf(lines: { points: Vec3[]; closed?: boolean; attrs?: Record<string, unknown> }[]): EditLayer {
  return {
    lines: lines.map((l, i) => ({ id: i + 1, points: l.points, closed: !!l.closed, attrs: cleanAttrs(l.attrs), src: NEW_LINE })),
    nextId: lines.length + 1,
  };
}
