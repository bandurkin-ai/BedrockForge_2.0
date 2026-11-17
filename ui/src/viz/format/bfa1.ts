/**
 * BFA1 decoder: the binary container of 3D data from the core (ADR 0007 §1, ADR 0004 §4; the reference
 * encoder is core/bf_core/viz/binary.py).
 *
 *     "BFA1"  u32 LE L  header (L bytes of UTF-8 JSON)  zeros up to a multiple of 8  arrays…
 *
 * The header holds `arrays: [[name, dtype, count, offset], …]`; `offset` is from the start of the
 * container and a multiple of 8, `count` is the number of elements. Arrays come back as typed views of the
 * same buffer — nothing is copied, so a Worker can transfer the buffer on as it is.
 */
import { VizError } from "../error";

export type Bfa1Dtype = "<f8" | "<f4" | "<i4" | "<u4" | "<u2" | "<u1";
export type Bfa1Array = Float64Array | Float32Array | Int32Array | Uint32Array | Uint16Array | Uint8Array;

interface TypedCtor {
  new (buffer: ArrayBufferLike, byteOffset: number, length: number): Bfa1Array;
  readonly BYTES_PER_ELEMENT: number;
}

const CTORS: Record<Bfa1Dtype, TypedCtor> = {
  "<f8": Float64Array,
  "<f4": Float32Array,
  "<i4": Int32Array,
  "<u4": Uint32Array,
  "<u2": Uint16Array,
  "<u1": Uint8Array,
};

export const BFA1_MAGIC = "BFA1";
/** A longer header is a damaged buffer (as MAX_HEADER of the core). */
export const BFA1_MAX_HEADER = 64 << 20;

export interface Bfa1<H = Record<string, unknown>> {
  /** the header without `arrays` */
  header: H;
  arrays: Record<string, Bfa1Array>;
  dtypes: Record<string, Bfa1Dtype>;
}

/** Typed arrays are platform-endian; every platform of BF2 (x86-64, arm64) is little-endian. */
const LITTLE_ENDIAN = new Uint8Array(new Uint16Array([1]).buffer)[0] === 1;

const HINT = "Повторите загрузку вида. Если ошибка повторяется — перезапустите ядро и сообщите разработчику.";

function damaged(code: string, what: string): VizError {
  return new VizError(code, `Данные 3D повреждены: ${what}.`, HINT);
}

function align8(n: number): number {
  return Math.ceil(n / 8) * 8;
}

/** A JSON error of the core `{code, message, hint}` passed where BFA1 was expected. */
function coreErrorIn(bytes: Uint8Array): VizError | null {
  if (bytes.length === 0 || bytes[0] !== 0x7b) return null; // "{"
  try {
    const e = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as Record<string, unknown>;
    if (typeof e.code !== "string" || typeof e.message !== "string") return null;
    return new VizError(e.code, e.message, typeof e.hint === "string" ? e.hint : HINT);
  } catch {
    return null;
  }
}

/**
 * Header and arrays of a BFA1 container. `data` is the whole response body: an ArrayBuffer, or a
 * view of one (a view not aligned to 8 bytes is copied once — typed views need aligned offsets).
 */
export function decodeBfa1<H = Record<string, unknown>>(data: ArrayBufferLike | ArrayBufferView): Bfa1<H> {
  if (!LITTLE_ENDIAN) throw new VizError("bfa1_endian", "Платформа с обратным порядком байт не поддерживается.", "Нужен компьютер x86-64 или arm64.");
  let buffer: ArrayBufferLike;
  let base = 0;
  let size: number;
  if (ArrayBuffer.isView(data)) {
    if (data.byteOffset % 8 === 0) {
      buffer = data.buffer;
      base = data.byteOffset;
    } else {
      buffer = new Uint8Array(data.buffer, data.byteOffset, data.byteLength).slice().buffer;
    }
    size = data.byteLength;
  } else {
    buffer = data;
    size = data.byteLength;
  }
  const bytes = new Uint8Array(buffer, base, size);
  if (size < 8 || bytes[0] !== 0x42 || bytes[1] !== 0x46 || bytes[2] !== 0x41 || bytes[3] !== 0x31) {
    const err = coreErrorIn(bytes);
    if (err) throw err;
    throw size < 8 ? damaged("bfa1_truncated", `ответ короче заголовка (${size} байт)`) : damaged("bfa1_magic", "нет метки BFA1 в начале");
  }
  const length = new DataView(buffer, base, 8).getUint32(4, true);
  if (length > BFA1_MAX_HEADER || 8 + length > size) throw damaged("bfa1_header_length", `длина заголовка ${length} байт выходит за ответ (${size} байт)`);
  let parsed: unknown;
  try {
    parsed = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes.subarray(8, 8 + length)));
  } catch {
    throw damaged("bfa1_header_json", "заголовок не читается как JSON");
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) throw damaged("bfa1_header", "заголовок не объект");
  const { arrays: table, ...header } = parsed as Record<string, unknown>;
  if (!Array.isArray(table)) throw damaged("bfa1_header", "в заголовке нет списка массивов");
  const start = align8(8 + length);
  for (let i = 8 + length; i < Math.min(start, size); i++) {
    if (bytes[i] !== 0) throw damaged("bfa1_padding", "после заголовка не нули");
  }

  const arrays: Record<string, Bfa1Array> = {};
  const dtypes: Record<string, Bfa1Dtype> = {};
  const spans: [number, number, string][] = [];
  for (const entry of table) {
    if (!Array.isArray(entry) || entry.length !== 4) throw damaged("bfa1_table", "запись массива не из четырёх полей");
    const [name, dtype, count, offset] = entry as unknown[];
    if (typeof name !== "string" || name === "") throw damaged("bfa1_table", "у массива нет имени");
    if (name in arrays) throw damaged("bfa1_table", `массив «${name}» повторяется`);
    if (typeof dtype !== "string" || !(dtype in CTORS)) throw damaged("bfa1_dtype", `тип «${String(dtype)}» массива «${name}» неизвестен`);
    if (!Number.isSafeInteger(count) || (count as number) < 0) throw damaged("bfa1_table", `число элементов массива «${name}» неверно`);
    if (!Number.isSafeInteger(offset) || (offset as number) % 8 !== 0) throw damaged("bfa1_offset", `смещение массива «${name}» не кратно 8`);
    const ctor = CTORS[dtype as Bfa1Dtype];
    const at = offset as number;
    const end = at + (count as number) * ctor.BYTES_PER_ELEMENT;
    if (at < start || end > size) throw damaged("bfa1_bounds", `массив «${name}» выходит за ответ`);
    arrays[name] = new ctor(buffer, base + at, count as number);
    dtypes[name] = dtype as Bfa1Dtype;
    spans.push([at, end, name]);
  }
  spans.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  for (let i = 1; i < spans.length; i++) {
    if (spans[i][0] < spans[i - 1][1]) throw damaged("bfa1_overlap", `массивы «${spans[i - 1][2]}» и «${spans[i][2]}» перекрываются`);
  }
  return { header: header as H, arrays, dtypes };
}

/** The array `name` of the expected dtype (and element count, when given), or a clear error. */
export function bfa1Array<D extends Bfa1Dtype>(doc: Bfa1<unknown>, name: string, dtype: D, count?: number): DtypeArray<D> {
  const arr = doc.arrays[name];
  if (!arr) throw damaged("bfa1_missing", `нет массива «${name}»`);
  if (doc.dtypes[name] !== dtype) throw damaged("bfa1_dtype", `массив «${name}» типа ${doc.dtypes[name]}, ожидался ${dtype}`);
  if (count !== undefined && arr.length !== count) throw damaged("bfa1_count", `в массиве «${name}» ${arr.length} элементов, ожидалось ${count}`);
  return arr as DtypeArray<D>;
}

export type DtypeArray<D extends Bfa1Dtype> = D extends "<f8"
  ? Float64Array
  : D extends "<f4"
    ? Float32Array
    : D extends "<i4"
      ? Int32Array
      : D extends "<u4"
        ? Uint32Array
        : D extends "<u2"
          ? Uint16Array
          : Uint8Array;
