/**
 * Data of the displays along holes from the core (ADR 0007 §1): the holes manifest, the paths and the
 * intervals of a part (BFA1), turned into the shapes the model works with — TrajectoryHole (float64:
 * part origin + float32 offset, ≤ 0,5 mm) and DownholeData (null — empty, never 0).
 */
import { coreTarget } from "../../shell/api";
import { apiUrl, authHeaders } from "../../shell/coreClient";
import { bfa1Array, decodeBfa1 } from "../format/bfa1";
import type { DownholeData, DownholeInterval, IntervalColumn, TrajectoryHole } from "./types";

export interface PartInfo {
  k: number;
  n: number;
  bbox: number[];
  origin: [number, number, number];
  etag: string;
}

export interface Manifest {
  setId: number;
  version: number;
  method: string;
  parts: PartInfo[];
  names: string[];
  /** per hole of `names`: its part and its slot inside the part */
  part: Uint16Array;
  slot: Uint16Array;
  td: Float64Array;
  /** names of the holes of a part, by slot */
  partNames: string[][];
}

export async function fetchBuffer(path: string, f: typeof fetch = fetch): Promise<ArrayBuffer> {
  const target = coreTarget();
  if (!target) throw new Error("Ядро недоступно. Подождите перезапуска ядра.");
  const r = await f(apiUrl(target, path), { headers: authHeaders(target) });
  const buf = await r.arrayBuffer();
  if (!r.ok) {
    // the core answers {code, message, hint}; decodeBfa1 turns it into a VizError with the Russian text
    decodeBfa1(buf);
    throw new Error(`Ядро ответило ${r.status}`);
  }
  return buf;
}

export function readManifest(buf: ArrayBuffer): Manifest {
  const doc = decodeBfa1<{ set: { id: number }; version: number; method: string; parts: PartInfo[]; names: string[] }>(buf);
  const part = bfa1Array(doc, "part", "<u2");
  const slot = bfa1Array(doc, "slot", "<u2");
  const partNames = doc.header.parts.map((p) => new Array<string>(p.n));
  doc.header.names.forEach((name, i) => {
    partNames[part[i]][slot[i]] = name;
  });
  return {
    setId: doc.header.set.id,
    version: doc.header.version,
    method: doc.header.method,
    parts: doc.header.parts,
    names: doc.header.names,
    part,
    slot,
    td: bfa1Array(doc, "td", "<f8"),
    partNames,
  };
}

/** Paths of one part as absolute float64 points [x, y, z, md] per hole (by slot). */
export function readPaths(buf: ArrayBuffer, info: PartInfo, names: string[], td?: (slot: number) => number | null): TrajectoryHole[] {
  const doc = decodeBfa1(buf);
  const offsets = bfa1Array(doc, "offsets", "<u4");
  const xyz = bfa1Array(doc, "xyz", "<f4");
  const md = bfa1Array(doc, "md", "<f8");
  const [ox, oy, oz] = info.origin;
  const out: TrajectoryHole[] = [];
  for (let s = 0; s + 1 < offsets.length; s++) {
    const path: number[][] = [];
    for (let i = offsets[s]; i < offsets[s + 1]; i++) path.push([ox + xyz[3 * i], oy + xyz[3 * i + 1], oz + xyz[3 * i + 2], md[i]]);
    out.push({ hole_id: names[s] ?? String(s), td: td?.(s) ?? null, source: "survey", path });
  }
  return out;
}

export interface PartIntervals {
  table: { name: string; kind: string };
  fields: IntervalColumn[];
  /** per slot of the part: its rows */
  rows: DownholeInterval[][];
}

/** Intervals of one part: numbers keep NaN as null; text codes −1 as null. */
export function readIntervals(buf: ArrayBuffer, holes: number): PartIntervals {
  const doc = decodeBfa1<{ table: { name: string; kind: string }; fields: { name: string; kind: string; unit: string | null }[]; cats: Record<string, string[]> }>(buf);
  const slot = bfa1Array(doc, "slot", "<u2");
  const from = bfa1Array(doc, "from", "<f8");
  const to = bfa1Array(doc, "to", "<f8");
  const event = doc.header.table.kind === "event";
  const cols = doc.header.fields.map((f) => ({
    name: f.name,
    values: f.kind === "number" ? bfa1Array(doc, `v:${f.name}`, "<f8") : bfa1Array(doc, `c:${f.name}`, "<i4"),
    cats: f.kind === "number" ? null : (doc.header.cats[f.name] ?? []),
  }));
  const rows: DownholeInterval[][] = Array.from({ length: holes }, () => []);
  for (let i = 0; i < slot.length; i++) {
    const values: Record<string, number | string | null> = {};
    for (const c of cols) {
      const v = c.values[i];
      values[c.name] = c.cats ? (v < 0 ? null : (c.cats[v] ?? null)) : Number.isNaN(v) ? null : v;
    }
    const f = Number.isNaN(from[i]) ? null : from[i];
    const t = event || Number.isNaN(to[i]) ? f : to[i];
    rows[slot[i]]?.push({ from: f, to: t, values });
  }
  return {
    table: doc.header.table,
    fields: doc.header.fields.map((f) => ({ name: f.name, kind: f.kind === "number" ? "number" : "text", unit: f.unit ?? "" })),
    rows,
  };
}

export async function loadManifest(setId: number, method?: string): Promise<Manifest> {
  return readManifest(await fetchBuffer(`/viz/sets/${setId}/holes${method ? `?method=${encodeURIComponent(method)}` : ""}`));
}

/**
 * Every row of the asked fields over the whole set (the form's histogram, categories and ranges). The
 * parts come in turn: the core reads them from the table snapshot.
 */
export async function loadDownholeAll(setId: number, fields: Record<string, string[]>, manifest?: Manifest): Promise<DownholeData> {
  const m = manifest ?? (await loadManifest(setId));
  const data: DownholeData = { tables: [], holes: m.names.map((name, i) => ({ hole_id: name, found: true, td: Number.isNaN(m.td[i]) ? null : m.td[i], max_depth: Number.isNaN(m.td[i]) ? 0 : m.td[i], tables: {} })) };
  const index = new Map(m.names.map((_n, i) => [`${m.part[i]}:${m.slot[i]}`, i]));
  for (const [table, list] of Object.entries(fields)) {
    if (!list.length) continue;
    for (const p of m.parts) {
      const q = `?table=${encodeURIComponent(table)}&fields=${list.map(encodeURIComponent).join(",")}&method=${encodeURIComponent(m.method)}`;
      const part = readIntervals(await fetchBuffer(`/viz/sets/${setId}/intervals/${p.k}${q}`), p.n);
      if (!data.tables.some((t) => t.key === table)) data.tables.push({ key: table, name: part.table.name, columns: part.fields, kind: part.table.kind === "event" ? "event" : "interval" });
      part.rows.forEach((rows, s) => {
        const i = index.get(`${p.k}:${s}`);
        if (i === undefined) return;
        const h = data.holes[i];
        h.tables[table] = rows;
        for (const r of rows) if (r.to !== null && r.to > h.max_depth) h.max_depth = r.to;
      });
    }
  }
  return data;
}
