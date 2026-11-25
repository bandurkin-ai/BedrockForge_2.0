/**
 * «Строка пробы → окно „Данные по скважинам“ (7.17) на этом интервале» (ТЗ 7.35 Д-30). The window of 7.17 (`striplog`, `charts/striplog`)
 * takes `{setId, setName, tableHint, holeHint}` and a start state `initial` (`holes`, `from`, `to` — the depth range, m): the hole of the
 * probe, the table the row of the plan took the probes from and the interval with a margin of a few metres around it. No React.
 */
import { openWindow } from "../../../shell/windows";
import { viewport } from "../../../shell/objectRun";
import { tablesStore } from "../../tables/state";
import type { Candidate, InspectResult } from "./api";

/** The margin of depth around the interval of the probe, m: the neighbours are seen. */
export const MARGIN = 5;

export interface IntervalTarget {
  setId: number;
  setName: string;
  tableHint: string | null;
  holeHint: string;
  initial: { holes: string[]; from: string; to: string };
}

/** What to open for a probe of a row; null — the row does not know its set of holes or the probe has no interval. */
export function intervalTarget(r: Pick<InspectResult, "source">, c: Pick<Candidate, "hole" | "from" | "to">): IntervalTarget | null {
  const setId = r.source.set_id;
  if (setId === null || !c.hole) return null;
  const set = tablesStore.get().sets.find((s) => s.id === setId);
  const text = (v: number): string => String(Math.round(v * 1000) / 1000).replace(".", ",");
  const range = c.from !== null && c.to !== null ? { from: text(Math.max(0, c.from - MARGIN)), to: text(c.to + MARGIN) } : { from: "", to: "" };
  return { setId, setName: set?.name ?? `Набор №${setId}`, tableHint: r.source.table, holeHint: c.hole, initial: { holes: [c.hole], ...range } };
}

/** Opens «Данные по скважинам» on the interval of the probe; the id of the window or null. */
export function openInterval(r: Pick<InspectResult, "source">, c: Pick<Candidate, "hole" | "from" | "to">): string | null {
  const t = intervalTarget(r, c);
  if (!t) return null;
  const { setId, setName, tableHint, holeHint, initial } = t;
  return openWindow("striplog", `Данные по скважинам · ${setName}`, "normal", { setId, setName, tableHint, holeHint, initial }, ...viewport());
}
