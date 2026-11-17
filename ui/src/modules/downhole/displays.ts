/**
 * The view key `displays` of the project (ADR 0007 §2): `[{chart, visible}]` — saved displays switched on in
 * the view, in their order (≤ 200). Operations, not copies: ViewSync applies them again after a 409.
 */
import type { ViewOp } from "../scene/viewSync";

export interface DisplayEntry {
  chart: number;
  visible: boolean;
}

export const MAX_DISPLAYS = 200;

/** The entries of the key as stored (anything else is dropped). */
export function displayEntries(value: unknown): DisplayEntry[] {
  if (!Array.isArray(value)) return [];
  const out: DisplayEntry[] = [];
  for (const v of value) {
    const e = v as Partial<DisplayEntry> | null;
    if (e && typeof e.chart === "number" && Number.isInteger(e.chart) && !out.some((x) => x.chart === e.chart)) out.push({ chart: e.chart, visible: e.visible === true });
  }
  return out;
}

export const isShown = (value: unknown, chart: number): boolean => displayEntries(value).some((e) => e.chart === chart && e.visible);

/** Shows or hides a display; a new one goes to the end, the oldest hidden ones go first past the limit. */
export function showDisplay(chart: number, visible: boolean): ViewOp {
  return (value) => {
    const list = displayEntries(value);
    const i = list.findIndex((e) => e.chart === chart);
    if (i >= 0) list[i] = { chart, visible };
    else list.push({ chart, visible });
    while (list.length > MAX_DISPLAYS) {
      const hidden = list.findIndex((e) => !e.visible && e.chart !== chart);
      list.splice(hidden >= 0 ? hidden : 0, 1);
    }
    return list;
  };
}

/** A deleted display leaves the key. */
export function dropDisplay(chart: number): ViewOp {
  return (value) => displayEntries(value).filter((e) => e.chart !== chart);
}
