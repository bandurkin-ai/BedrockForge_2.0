/**
 * The stored form of a line layer and the editor's working copy.
 *
 * In the store (and in STR / DXF files) a closed line repeats its first vertex at the end; the
 * working copy keeps a closed line without the repeat. Coordinates pass through untouched (float64).
 */
import { fromArrays, toArrays, type AttrValue, type EditLayer } from "./model";

export interface StoredLines {
  vertices: Float64Array;
  /** start of every line, plus the end */
  offsets: Uint32Array;
  closed: Uint8Array;
  attributes: Record<string, AttrValue>[];
}

/** Stored arrays → working copy: the repeated closing vertex of closed lines is dropped. */
export function fromStored(vertices: Float64Array, offsets: ArrayLike<number>, closed: ArrayLike<number>, attributes: Record<string, unknown>[]): EditLayer {
  const lines = closed.length;
  const out = new Float64Array(vertices.length);
  const starts = new Uint32Array(lines + 1);
  let n = 0;
  for (let l = 0; l < lines; l++) {
    const a = offsets[l];
    let b = offsets[l + 1];
    if (closed[l] && b - a > 1
      && vertices[a * 3] === vertices[(b - 1) * 3] && vertices[a * 3 + 1] === vertices[(b - 1) * 3 + 1] && vertices[a * 3 + 2] === vertices[(b - 1) * 3 + 2]) b -= 1;
    out.set(vertices.subarray(a * 3, b * 3), n * 3);
    n += b - a;
    starts[l + 1] = n;
  }
  return fromArrays(out.subarray(0, n * 3), starts, closed, attributes);
}

/** Working copy → stored arrays: closed lines get their first vertex repeated. A closed line with fewer than 3 points is stored open. */
export function toStored(layer: EditLayer): StoredLines {
  const a = toArrays(layer);
  const lines = a.closed.length;
  let extra = 0;
  for (let l = 0; l < lines; l++) if (a.closed[l] && a.offsets[l + 1] - a.offsets[l] >= 3) extra++;
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
  return { vertices, offsets, closed, attributes: a.attributes };
}
