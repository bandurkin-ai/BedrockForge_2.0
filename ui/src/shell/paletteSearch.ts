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
    for (const item of items) {
      if (!q || matchesQuery(`${item.label} ${item.group} ${item.hint ?? ""}`, q)) out.push({ kind: "command", item });
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
