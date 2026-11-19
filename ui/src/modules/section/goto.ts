/**
 * Moving along a series (ТЗ 8.1.3; ADR 0016 §4): `]` / `[` — k ± 1 (a polyline — its segments), `Home` / `End` —
 * the first and the last, «Перейти» (`Ctrl+G`) — a section by its name or its number (from 1) with suggestions.
 * At an edge k stays and the result says so. Pure: the handlers are in `seriesBind.ts`.
 */
import type { SeriesSectionRow } from "./seriesApi";

export type Move = 1 | -1 | "first" | "last";

/** The position after a move of `index` in `steps`; `edge` — it could not move (k stays). */
export function moveIndex(index: number, steps: number, move: Move): { index: number; edge: boolean } {
  const last = Math.max(steps - 1, 0);
  const to = move === "first" ? 0 : move === "last" ? last : index + move;
  const clamped = Math.min(Math.max(to, 0), last);
  return { index: clamped, edge: clamped === index && (move === 1 || move === -1) };
}

/** «Последний разрез серии: дальше разрезов нет» — the message at an edge. */
export function edgeText(move: 1 | -1, polyline: boolean): string {
  if (polyline) return move === 1 ? "Последний сегмент ломаного разреза: дальше сегментов нет" : "Первый сегмент ломаного разреза: раньше сегментов нет";
  return move === 1 ? "Последний разрез серии: дальше разрезов нет" : "Первый разрез серии: раньше разрезов нет";
}

const key = (s: string) => s.trim().toLocaleLowerCase("ru-RU").replace(/\s+/g, " ");

/**
 * A section by what was typed: its name (case and spaces aside), otherwise a whole number from 1 to K. A name
 * that looks like a number wins over the number (a plan «100» is a name). Null — none.
 */
export function resolveGoto(sections: readonly Pick<SeriesSectionRow, "k" | "name">[], query: string | number): number | null {
  if (typeof query === "number") return Number.isInteger(query) && query >= 1 && query <= sections.length ? query - 1 : null;
  const q = key(query);
  if (!q) return null;
  const byName = sections.find((s) => key(s.name) === q);
  if (byName) return byName.k;
  if (/^\d+$/.test(q)) {
    const n = Number(q);
    return n >= 1 && n <= sections.length ? n - 1 : null;
  }
  return null;
}

export interface GotoHint {
  k: number;
  label: string;
}

/** Up to `limit` suggestions: names starting with the text, then containing it, then the number typed. */
export function gotoHints(sections: readonly Pick<SeriesSectionRow, "k" | "name">[], query: string, limit = 10): GotoHint[] {
  const q = key(query);
  const label = (s: Pick<SeriesSectionRow, "k" | "name">) => `${s.k + 1}. ${s.name}`;
  if (!q) return sections.slice(0, limit).map((s) => ({ k: s.k, label: label(s) }));
  const out: GotoHint[] = [];
  const seen = new Set<number>();
  const add = (s: Pick<SeriesSectionRow, "k" | "name">) => {
    if (out.length < limit && !seen.has(s.k)) {
      seen.add(s.k);
      out.push({ k: s.k, label: label(s) });
    }
  };
  for (const s of sections) if (key(s.name).startsWith(q)) add(s);
  for (const s of sections) if (out.length < limit && key(s.name).includes(q)) add(s);
  if (/^\d+$/.test(q)) {
    const n = Number(q);
    if (n >= 1 && n <= sections.length) add(sections[n - 1]);
  }
  return out;
}
