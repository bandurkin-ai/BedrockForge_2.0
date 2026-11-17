/**
 * The holes manifest of a set (`GET /viz/sets/{id}/holes`, ADR 0007 §1; encoder `core/bf_core/viz/holes.py`):
 * parts with their absolute extents and integer origins, and per hole — id, absolute collar (float64), TD,
 * length, azimuth in plan, kind (0 hole, 1 trench, 2 design), audit level (0–4), part and slot. Decoded on
 * the main thread: the arrays are views of the response, nothing is copied.
 */
import { bfa1Array, decodeBfa1 } from "../format/bfa1";

export interface HolesPart {
  k: number;
  n: number;
  /** absolute [xmin, ymin, zmin, xmax, ymax, zmax] */
  bbox: number[];
  /** absolute integer origin of the part's float32 points */
  origin: [number, number, number];
  etag: string;
}

export interface AuditStamp {
  run_id: number;
  stale: boolean;
  at: string;
}

export interface HolesManifest {
  setId: number;
  setName: string;
  version: number;
  method: string;
  n: number;
  parts: HolesPart[];
  names: string[];
  skipped: string[];
  audit: AuditStamp | null;
  id: Uint32Array;
  /** absolute collar xyz, float64 × 3 */
  collar: Float64Array;
  td: Float64Array;
  length: Float64Array;
  /** degrees clockwise from north, NaN — vertical */
  planAz: Float32Array;
  kind: Uint8Array;
  auditLevel: Uint8Array;
  part: Uint16Array;
  slot: Uint16Array;
  /** manifest index of every slot, by part */
  byPart: Uint32Array[];
}

interface Header {
  set: { id: number; name: string };
  version: number;
  method: string;
  n: number;
  parts: HolesPart[];
  names: string[];
  skipped?: string[];
  audit: AuditStamp | null;
}

export function readHolesManifest(buf: ArrayBuffer): HolesManifest {
  const doc = decodeBfa1<Header>(buf);
  const h = doc.header;
  const n = h.n;
  const part = bfa1Array(doc, "part", "<u2", n);
  const slot = bfa1Array(doc, "slot", "<u2", n);
  const byPart = h.parts.map((p) => new Uint32Array(p.n));
  for (let i = 0; i < n; i++) byPart[part[i]][slot[i]] = i;
  return {
    setId: h.set.id,
    setName: h.set.name,
    version: h.version,
    method: h.method,
    n,
    parts: h.parts,
    names: h.names,
    skipped: h.skipped ?? [],
    audit: h.audit ?? null,
    id: bfa1Array(doc, "id", "<u4", n),
    collar: bfa1Array(doc, "collar", "<f8", n * 3),
    td: bfa1Array(doc, "td", "<f8", n),
    length: bfa1Array(doc, "length", "<f8", n),
    planAz: bfa1Array(doc, "plan_az", "<f4", n),
    kind: bfa1Array(doc, "kind", "<u1", n),
    auditLevel: bfa1Array(doc, "audit", "<u1", n),
    part,
    slot,
    byPart,
  };
}

/** Kinds of the holes of part k, by slot (for the Worker). */
export function partKinds(m: HolesManifest, k: number): Uint8Array {
  const idx = m.byPart[k];
  const out = new Uint8Array(idx.length);
  for (let s = 0; s < idx.length; s++) out[s] = m.kind[idx[s]];
  return out;
}

const indexCache = new WeakMap<HolesManifest, Map<string, number>>();

/** Manifest index of a hole by name, or −1. */
export function holeIndex(m: HolesManifest, name: string): number {
  let map = indexCache.get(m);
  if (!map) {
    map = new Map(m.names.map((x, i) => [x, i]));
    indexCache.set(m, map);
  }
  return map.get(name) ?? -1;
}
