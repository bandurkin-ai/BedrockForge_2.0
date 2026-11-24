/**
 * BFA1 `blocks_slice` of the core (ADR 0023 §11, decision 19; docs/progress/e17-api.md §12): the section of a block
 * model by a plane as a raster of pixels —
 *
 *     value <f4 [width·height]  the value of the field (a category — its code); NaN where there is none
 *     m     <u1 [width·height]  0 — no block or the filter / a hidden category did not let it pass (not drawn),
 *                               1 — a value, 2 — a block without a value (drawn grey, never as 0)
 *
 * Row j counts from the bottom (along v), column i along u. The frame is in the project (float64): `origin` is the
 * corner of pixel (0, 0), `u` and `v` the unit axes of the raster, `du`, `dv` the size of a pixel. A plane that misses
 * the model is a raster of 0 × 0 (`empty`). The arrays are views of the response buffer — nothing is copied. A damaged
 * answer is a `VizError` in Russian.
 */
import { VizError } from "../error";
import { bfa1Array, decodeBfa1 } from "../format/bfa1";

export type SliceRaster = "cells" | "subcells" | "sampled";
export type SliceFieldKind = "number" | "category" | null;
export type Vec3 = [number, number, number];

/** Pixel values of `m`. */
export const M_NONE = 0;
export const M_VALUE = 1;
export const M_EMPTY = 2;

export interface SliceFrame {
  /** the corner of pixel (0, 0) in the project, float64 */
  origin: Vec3;
  u: Vec3;
  v: Vec3;
  du: number;
  dv: number;
}

export interface SliceHeader {
  kind: "blocks_slice";
  format: 1;
  model: number;
  version: number;
  /** the plane as the core took it (the normal is a unit vector) */
  plane: { origin: Vec3; normal: Vec3 };
  raster: SliceRaster;
  width: number;
  height: number;
  frame: SliceFrame;
  /** rows of the model the plane crosses that pass the filter */
  passed: number;
  field: string | null;
  field_kind: SliceFieldKind;
  labels: string[] | null;
  warnings: string[];
}

export interface SliceData {
  header: SliceHeader;
  width: number;
  height: number;
  /** the plane misses the model: nothing to draw */
  empty: boolean;
  value: Float32Array;
  m: Uint8Array;
  /** pixels with a value / without one (never counted as 0) */
  withValue: number;
  withoutValue: number;
  /** the highest code of the pixels with a value of a category field; −1 — none */
  maxCode: number;
}

const HINT = "Повторите запрос среза. Если ошибка повторяется — перезапустите ядро и сообщите разработчику.";

function damaged(code: string, what: string): VizError {
  return new VizError(code, `Данные среза блочной модели повреждены: ${what}.`, HINT);
}

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

function triple(v: unknown, name: string): Vec3 {
  if (!Array.isArray(v) || v.length !== 3 || !v.every(finite)) throw damaged("slice_header", `«${name}» не три числа`);
  return [v[0], v[1], v[2]];
}

function unit(v: Vec3, name: string): Vec3 {
  const len = Math.hypot(v[0], v[1], v[2]);
  if (!(len > 0.999999 && len < 1.000001)) throw damaged("slice_header", `ось «${name}» не единичная`);
  return v;
}

function checkHeader(raw: Record<string, unknown>): SliceHeader {
  if (raw.kind !== "blocks_slice") throw damaged("slice_kind", `ответ не «blocks_slice», а «${String(raw.kind)}»`);
  if (raw.format !== 1) throw damaged("slice_format", `формат ${String(raw.format)} не поддерживается`);
  const int = (name: string): number => {
    const v = raw[name];
    if (typeof v !== "number" || !Number.isSafeInteger(v) || v < 0) throw damaged("slice_header", `«${name}» не целое неотрицательное`);
    return v;
  };
  const plane = raw.plane as { origin?: unknown; normal?: unknown } | undefined;
  if (!plane || typeof plane !== "object") throw damaged("slice_header", "нет плоскости");
  const raster = raw.raster;
  if (raster !== "cells" && raster !== "subcells" && raster !== "sampled") throw damaged("slice_header", "вид растра не «cells», «subcells» и не «sampled»");
  const width = int("width");
  const height = int("height");
  if ((width === 0) !== (height === 0)) throw damaged("slice_header", "ширина и высота растра: одна из них нуль");
  const fr = raw.frame as Record<string, unknown> | undefined;
  if (!fr || typeof fr !== "object") throw damaged("slice_header", "нет рамки растра");
  const frame: SliceFrame = { origin: triple(fr.origin, "frame.origin"), u: triple(fr.u, "frame.u"), v: triple(fr.v, "frame.v"), du: fr.du as number, dv: fr.dv as number };
  if (!(finite(frame.du) && frame.du > 0 && finite(frame.dv) && frame.dv > 0)) throw damaged("slice_header", "размер пикселя не положителен");
  if (width > 0) {
    unit(frame.u, "u");
    unit(frame.v, "v");
  }
  const kind = raw.field_kind ?? null;
  if (kind !== null && kind !== "number" && kind !== "category") throw damaged("slice_header", "вид поля не «number» и не «category»");
  const labels = raw.labels ?? null;
  if (labels !== null && !(Array.isArray(labels) && labels.every((s) => typeof s === "string"))) throw damaged("slice_header", "«labels» не список строк");
  const warnings = Array.isArray(raw.warnings) ? raw.warnings.filter((x): x is string => typeof x === "string") : [];
  return {
    kind: "blocks_slice",
    format: 1,
    model: int("model"),
    version: int("version"),
    plane: { origin: triple(plane.origin, "plane.origin"), normal: triple(plane.normal, "plane.normal") },
    raster,
    width,
    height,
    frame,
    passed: int("passed"),
    field: typeof raw.field === "string" ? raw.field : null,
    field_kind: kind,
    labels: labels as string[] | null,
    warnings,
  };
}

/** The raster from the body of the answer. One pass over `m` (a value 0…2) and over the values that count. */
export function decodeSlice(data: ArrayBufferLike | ArrayBufferView): SliceData {
  const doc = decodeBfa1<Record<string, unknown>>(data);
  const header = checkHeader(doc.header);
  const n = header.width * header.height;
  const value = bfa1Array(doc, "value", "<f4", n) as Float32Array;
  const m = bfa1Array(doc, "m", "<u1", n) as Uint8Array;
  let withValue = 0;
  let withoutValue = 0;
  let maxCode = -1;
  const category = header.field_kind === "category";
  for (let i = 0; i < n; i++) {
    const mi = m[i];
    if (mi === M_VALUE) {
      withValue++;
      if (category) {
        const c = value[i];
        if (c > maxCode) maxCode = c;
      }
    } else if (mi === M_EMPTY) withoutValue++;
    else if (mi !== M_NONE) throw damaged("slice_mask", `у пикселя ${i} маска ${mi}`);
  }
  if (category && maxCode > 65534) throw damaged("slice_code", `код категории ${maxCode} больше 65534`);
  return { header, width: header.width, height: header.height, empty: n === 0, value, m, withValue, withoutValue, maxCode: Math.floor(maxCode) };
}

/** The corners of the raster in the project, float64, in the order (0, 0), (W, 0), (W, H), (0, H) of pixels. */
export function sliceCorners(h: Pick<SliceHeader, "width" | "height" | "frame">): Vec3[] {
  const { origin: o, u, v, du, dv } = h.frame;
  const w = h.width * du;
  const hh = h.height * dv;
  const at = (a: number, b: number): Vec3 => [o[0] + a * u[0] + b * v[0], o[1] + a * u[1] + b * v[1], o[2] + a * u[2] + b * v[2]];
  return [at(0, 0), at(w, 0), at(w, hh), at(0, hh)];
}

/** The project point of the centre of pixel (i, j), float64 (the click, the tests). */
export function pixelCentre(h: Pick<SliceHeader, "frame">, i: number, j: number): Vec3 {
  const { origin: o, u, v, du, dv } = h.frame;
  const a = (i + 0.5) * du;
  const b = (j + 0.5) * dv;
  return [o[0] + a * u[0] + b * v[0], o[1] + a * u[1] + b * v[1], o[2] + a * u[2] + b * v[2]];
}
