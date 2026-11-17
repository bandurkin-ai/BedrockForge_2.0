/**
 * Data of the display form: the tables of a set keyed by `dh_tables.name_key` (ADR 0007 §3 — the key of a
 * layer survives a replaced table and `.bfggis`), the names of the holes and the width of a default column.
 */
import { loadHoleNames, loadTables } from "../../charts/striplog/data";
import { autoPanelWidth } from "../../viz/downhole/geometry";
import { fetchBuffer, loadManifest, readPaths } from "../../viz/downhole/parts";
import type { IntervalTableInfo } from "../../viz/downhole/types";

/** The key of a table name as the core makes it (NFC, trimmed, casefold). */
export const nameKey = (name: string): string => name.normalize("NFC").trim().toLowerCase();

/** Interval, assay and event tables of a set with their fields; `key` — the name key, `id` — the table id. */
export async function formTables(setId: number): Promise<IntervalTableInfo[]> {
  return (await loadTables(setId)).map((t) => ({ ...t, key: nameKey(t.name) }));
}

export async function holeNames(setId: number): Promise<{ hole_id: string }[]> {
  return (await loadHoleNames(setId)).map((hole_id) => ({ hole_id }));
}

/** Width of the default column from the paths of the first part (about a twentieth of a typical hole); 10 m without paths. */
export async function panelWidthOf(setId: number): Promise<number> {
  try {
    const m = await loadManifest(setId);
    const p = m.parts[0];
    if (!p) return 10;
    const buf = await fetchBuffer(`/viz/sets/${setId}/paths/${p.k}?method=${encodeURIComponent(m.method)}`);
    const holes = readPaths(buf, p, m.partNames[0] ?? []);
    return holes.length ? autoPanelWidth(holes) : 10;
  } catch {
    return 10;
  }
}
