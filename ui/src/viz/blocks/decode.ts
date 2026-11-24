/**
 * BFA1 `blocks` of the core (ADR 0023 §11, decisions 16–19; docs/progress/e17-api.md §12): the shell of a block
 * model by faces — rectangles, one per face (or per merged run of coplanar faces of one class):
 *
 *     c <f4 [faces·3]  the centre of the face in the local axes of the model, minus `origin`
 *     h <f4 [faces·2]  half sizes along the axes u and v of the face
 *     a <u1 [faces]    2·axis + side (side 0 — the normal is −axis, 1 — +axis); u = axis + 1, v = axis + 2 (mod 3)
 *     k <u2 [faces]    the class: a bucket of the scale (256), a class of the breaks, a category code, 0xFFFF — no value
 *
 * A point of the project: p = corner + R_z(rotation)·(origin + c). `origin` is a whole number of metres (the
 * centre of the local extent), so float32 of `c` and `h` keeps millimetres at kilometres (CLAUDE.md, `viz/origin.ts`).
 * The arrays are views of the response buffer — nothing is copied; the layer takes them as the GPU attributes as
 * they are, so a Worker can decode and transfer the buffer on. A damaged answer is a `VizError` in Russian.
 */
import { VizError } from "../error";
import { bfa1Array, decodeBfa1, type Bfa1 } from "../format/bfa1";

/** A face of a block that has no value or category (a missing value is never drawn as 0). */
export const NO_CLASS = 0xffff;

export type ShowMode = "shell" | "all";
export type ColourKind = "none" | "scale" | "classes" | "categories";

export interface BlocksColour {
  kind: ColourKind;
  from?: number;
  to?: number;
  log?: boolean;
  breaks?: number[];
  hidden?: number[];
}

export interface BlocksHeader {
  kind: "blocks";
  format: 1;
  model: number;
  version: number;
  mode: ShowMode;
  /** the corner of cell (0, 0, 0) in the project, float64 */
  corner: [number, number, number];
  /** degrees about +Z, counter-clockwise seen from above */
  rotation: number;
  /** whole metres, local axes: the zero of `c` */
  origin: [number, number, number];
  faces: number;
  /** the shell before LOD and merge */
  faces_raw: number;
  simplified: boolean;
  /** ℓ: the parents of 2^ℓ a side were drawn (0 — the model as it is) */
  lod: number;
  rows: number;
  passed: number;
  empty: number;
  field: string | null;
  colour: BlocksColour;
  /** the classes of the legend: 256 buckets, the number of classes of the breaks, the codes of the categories, 0 */
  classes: number;
  /** [from, to] of every class (`null` ends of the first and last of the breaks); null — none / categories */
  bins: [number | null, number | null][] | null;
  labels: string[] | null;
}

export interface BlocksData {
  header: BlocksHeader;
  faces: number;
  c: Float32Array;
  h: Float32Array;
  a: Uint8Array;
  k: Uint16Array;
  /** the highest class of the faces that has a value (0xFFFF not counted); −1 — no faces */
  maxClass: number;
}

const HINT = "Повторите запрос показа. Если ошибка повторяется — перезапустите ядро и сообщите разработчику.";

function damaged(code: string, what: string): VizError {
  return new VizError(code, `Данные блочной модели повреждены: ${what}.`, HINT);
}

function triple(v: unknown, name: string): [number, number, number] {
  if (!Array.isArray(v) || v.length !== 3 || !v.every((x) => typeof x === "number" && Number.isFinite(x))) throw damaged("blocks_header", `«${name}» не три числа`);
  return [v[0], v[1], v[2]];
}

/** The header of the answer checked: what the layer relies on is there and finite. */
function checkHeader(raw: Record<string, unknown>): BlocksHeader {
  if (raw.kind !== "blocks") throw damaged("blocks_kind", `ответ не «blocks», а «${String(raw.kind)}»`);
  if (raw.format !== 1) throw damaged("blocks_format", `формат ${String(raw.format)} не поддерживается`);
  const int = (name: string): number => {
    const v = raw[name];
    if (typeof v !== "number" || !Number.isSafeInteger(v) || v < 0) throw damaged("blocks_header", `«${name}» не целое неотрицательное`);
    return v;
  };
  if (raw.mode !== "shell" && raw.mode !== "all") throw damaged("blocks_header", "режим не «shell» и не «all»");
  const rotation = raw.rotation;
  if (typeof rotation !== "number" || !Number.isFinite(rotation)) throw damaged("blocks_header", "«rotation» не число");
  const colour = raw.colour as BlocksColour | undefined;
  if (!colour || !["none", "scale", "classes", "categories"].includes(colour.kind)) throw damaged("blocks_header", "нет описания раскраски");
  const bins = raw.bins ?? null;
  if (bins !== null && !Array.isArray(bins)) throw damaged("blocks_header", "«bins» не список");
  const labels = raw.labels ?? null;
  if (labels !== null && !(Array.isArray(labels) && labels.every((s) => typeof s === "string"))) throw damaged("blocks_header", "«labels» не список строк");
  return {
    kind: "blocks",
    format: 1,
    model: int("model"),
    version: int("version"),
    mode: raw.mode,
    corner: triple(raw.corner, "corner"),
    rotation,
    origin: triple(raw.origin, "origin"),
    faces: int("faces"),
    faces_raw: int("faces_raw"),
    simplified: raw.simplified === true,
    lod: int("lod"),
    rows: int("rows"),
    passed: int("passed"),
    empty: int("empty"),
    field: typeof raw.field === "string" ? raw.field : null,
    colour,
    classes: int("classes"),
    bins: bins as BlocksHeader["bins"],
    labels: labels as string[] | null,
  };
}

/**
 * The shell from the body of the answer. Checked in one pass over the arrays: the sides are 0…5, the centres and
 * half sizes are finite (the layer takes the extent and the matrices from them), the half sizes are positive.
 */
export function decodeBlocks(data: ArrayBufferLike | ArrayBufferView): BlocksData {
  const doc = decodeBfa1<Record<string, unknown>>(data) as Bfa1<Record<string, unknown>>;
  const header = checkHeader(doc.header);
  const n = header.faces;
  const c = bfa1Array(doc, "c", "<f4", 3 * n);
  const h = bfa1Array(doc, "h", "<f4", 2 * n);
  const a = bfa1Array(doc, "a", "<u1", n);
  const k = bfa1Array(doc, "k", "<u2", n);
  let maxClass = -1;
  for (let i = 0; i < n; i++) {
    if (a[i] > 5) throw damaged("blocks_side", `у грани ${i} сторона ${a[i]}`);
    const kk = k[i];
    if (kk !== NO_CLASS && kk > maxClass) maxClass = kk;
  }
  for (let i = 0; i < 3 * n; i++) if (!Number.isFinite(c[i])) throw damaged("blocks_finite", `координата ${i} не конечна`);
  for (let i = 0; i < 2 * n; i++) if (!(h[i] > 0) || !Number.isFinite(h[i])) throw damaged("blocks_finite", `размер ${i} не положителен`);
  return { header, faces: n, c, h, a, k, maxClass };
}

/** The axes of a face: u = axis + 1, v = axis + 2 (mod 3) — the one table of the shader, the extent and the pick. */
export const FACE_UV: readonly (readonly [number, number])[] = [
  [1, 2],
  [2, 0],
  [0, 1],
];

/** [xmin, ymin, zmin, xmax, ymax, zmax] of the faces in the local axes of the model (`origin` added), float64. */
export function localExtent(d: BlocksData): Float64Array {
  const out = Float64Array.of(Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity);
  const { c, h, a } = d;
  const lo = [Infinity, Infinity, Infinity];
  const hi = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < d.faces; i++) {
    const axis = a[i] >> 1;
    const [u, v] = FACE_UV[axis];
    const x = c[3 * i + axis];
    if (x < lo[axis]) lo[axis] = x;
    if (x > hi[axis]) hi[axis] = x;
    const cu = c[3 * i + u];
    const hu = h[2 * i];
    if (cu - hu < lo[u]) lo[u] = cu - hu;
    if (cu + hu > hi[u]) hi[u] = cu + hu;
    const cv = c[3 * i + v];
    const hv = h[2 * i + 1];
    if (cv - hv < lo[v]) lo[v] = cv - hv;
    if (cv + hv > hi[v]) hi[v] = cv + hv;
  }
  const o = d.header.origin;
  for (let j = 0; j < 3; j++) {
    out[j] = lo[j] + o[j];
    out[j + 3] = hi[j] + o[j];
  }
  return out;
}
