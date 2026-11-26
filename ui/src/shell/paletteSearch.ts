/** Palette search (ТЗ 7.0.7). Pure. */
import { matchesQuery } from "./commands";
import type { FlatItem } from "./ribbonConfig";

export const PALETTE_LIMIT = 60;
export const HOLE_PREFIX = "скв ";

export type PaletteRow = { kind: "command"; item: FlatItem } | { kind: "hole"; holeId: string; setName: string };

export interface HoleEntry {
  holeId: string;
  setName: string;
}

/** Commands by label, «tab › group» and hint; «скв …» — holes only; holes also from 2 characters. */
export function searchPalette(query: string, items: FlatItem[], holes: HoleEntry[]): PaletteRow[] {
  const holeOnly = query.toLowerCase().startsWith(HOLE_PREFIX);
  const q = (holeOnly ? query.slice(HOLE_PREFIX.length) : query).trim();
  const out: PaletteRow[] = [];
  if (!holeOnly) {
    const found: { item: FlatItem; rank: number }[] = [];
    const needle = q.toLowerCase();
    for (const item of items) {
      if (q && !matchesQuery(`${item.label} ${item.group} ${item.hint ?? ""}`, q)) continue;
      // the exact label first, then a label that starts with the query, then one that has it; the rest keep the order of the ribbon
      const label = item.label.toLowerCase();
      found.push({ item, rank: !q ? 3 : label === needle ? 0 : label.startsWith(needle) ? 1 : label.includes(needle) ? 2 : 3 });
    }
    found.sort((a, b) => a.rank - b.rank); // stable
    for (const f of found) {
      out.push({ kind: "command", item: f.item });
      if (out.length >= PALETTE_LIMIT) return out;
    }
  }
  if (holeOnly || q.length >= 2) {
    const needle = q.toLowerCase();
    for (const h of holes) {
      if (h.holeId.toLowerCase().includes(needle)) out.push({ kind: "hole", holeId: h.holeId, setName: h.setName });
      if (out.length >= PALETTE_LIMIT) break;
    }
  }
  return out;
}
