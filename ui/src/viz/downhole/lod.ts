/**
 * Levels of detail of the displays along holes (ADR 0007 §7): the full rows, and rows merged into spans of
 * up to 4 m and up to 32 m. A number becomes the mean weighted by length, a category — the one covering the
 * most length; an empty value stays empty (never 0). Events (one depth) are not merged. The level of a
 * part is chosen by pixels per metre on the screen (`stepBoundaries` of core/lod.ts): a merged span shorter
 * than 2 px is not seen. Pure, float64.
 */
import { chooseLevel, DOWNHOLE_STEPS, stepBoundaries } from "../core/lod";
import type { DownholeData, DownholeInterval } from "./types";

export const LOD_STEPS = DOWNHOLE_STEPS;
const BOUNDARIES = stepBoundaries(LOD_STEPS);

/** The level for pixels per metre at a part, with the 20 % hysteresis about the current one. */
export function displayLevel(ppm: number, current: number | null): number {
  return chooseLevel(ppm, BOUNDARIES, current);
}

/**
 * Rows of one hole merged into spans of at most `step` metres (rows sorted by «from»). `numeric(field)` —
 * whether a field holds numbers.
 */
export function mergeRows(rows: readonly DownholeInterval[], step: number, numeric: (field: string) => boolean): DownholeInterval[] {
  if (!(step > 0)) return rows as DownholeInterval[];
  const out: DownholeInterval[] = [];
  let bin: DownholeInterval[] = [];
  let binFrom = 0;
  const flush = () => {
    if (!bin.length) return;
    out.push(bin.length === 1 ? bin[0] : mergeBin(bin, binFrom, numeric));
    bin = [];
  };
  for (const r of rows) {
    if (r.from === null || r.to === null || !(r.to > r.from)) {
      flush();
      out.push(r); // events and rows without depths: as they are
      continue;
    }
    if (bin.length && r.to - binFrom <= step && r.from >= binFrom) {
      bin.push(r);
      continue;
    }
    flush();
    bin = [r];
    binFrom = r.from;
  }
  flush();
  return out;
}

function mergeBin(bin: DownholeInterval[], from: number, numeric: (field: string) => boolean): DownholeInterval {
  let to = from;
  const fields = new Set<string>();
  for (const r of bin) {
    to = Math.max(to, r.to as number);
    for (const k of Object.keys(r.values)) fields.add(k);
  }
  const values: Record<string, number | string | null> = {};
  for (const f of fields) {
    if (numeric(f)) {
      let sum = 0;
      let len = 0;
      for (const r of bin) {
        const v = r.values[f];
        if (typeof v !== "number" || !Number.isFinite(v)) continue;
        const w = (r.to as number) - (r.from as number);
        sum += v * w;
        len += w;
      }
      values[f] = len > 0 ? sum / len : null;
    } else {
      const by = new Map<string, number>();
      for (const r of bin) {
        const v = r.values[f];
        if (v === null || v === undefined || v === "") continue;
        const k = String(v);
        by.set(k, (by.get(k) ?? 0) + (r.to as number) - (r.from as number));
      }
      let best: string | null = null;
      let most = -1;
      for (const [k, l] of by) if (l > most) [best, most] = [k, l];
      values[f] = best;
    }
  }
  return { from, to, values };
}

/** The data with the rows of every hole merged to `step` metres (the tables say which fields are numbers). */
export function mergeData(data: DownholeData, step: number): DownholeData {
  if (!(step > 0)) return data;
  const kinds = new Map<string, Set<string>>();
  for (const t of data.tables) kinds.set(t.key, new Set(t.columns.filter((c) => c.kind === "number").map((c) => c.name)));
  return {
    tables: data.tables,
    holes: data.holes.map((h) => {
      const tables: Record<string, DownholeInterval[]> = {};
      for (const [key, rows] of Object.entries(h.tables)) {
        const nums = kinds.get(key) ?? new Set<string>();
        const event = data.tables.find((t) => t.key === key)?.kind === "event";
        tables[key] = event ? rows : mergeRows(rows, step, (f) => nums.has(f));
      }
      return { ...h, tables };
    }),
  };
}
