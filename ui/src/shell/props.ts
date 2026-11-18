/**
 * Shared pieces of the «Свойства» panels of objects (ТЗ 7.18–7.20; ADR 0011 §5): a fact is a label with its
 * value; numbers are written the Russian way; a missing number stays missing — never 0. Pure: no React.
 */

/** One line of «Свойства»: «Длина — 1 520,5 м». */
export interface Fact {
  label: string;
  value: string;
}

/** «1 520,5» — thousands by a space, a decimal comma. */
export const num = (v: number, digits = 2): string => v.toLocaleString("ru-RU", { maximumFractionDigits: digits });

/** A number for an input field: every digit that matters, a decimal comma, no thousands («5000,5», «−12,25»). */
export function fieldNumber(v: number | null | undefined, digits = 3): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "";
  return v.toLocaleString("ru-RU", { maximumFractionDigits: digits, useGrouping: false }).replace("-", "−");
}

/** «5 000 … 5 300»; a side without a number is «—». */
export function range(lo: number | null | undefined, hi: number | null | undefined, digits = 2): string {
  const one = (v: number | null | undefined) => (typeof v === "number" && Number.isFinite(v) ? num(v, digits) : "—");
  return `${one(lo)} … ${one(hi)}`;
}

/** The extent [xmin, ymin, zmin, xmax, ymax, zmax] as «X», «Y», «Z»; an axis without numbers is left out. */
export function extentFacts(extent: readonly (number | null)[] | null | undefined): Fact[] {
  if (!extent || extent.length < 6) return [];
  return (["X", "Y", "Z"] as const).flatMap((axis, i) =>
    typeof extent[i] === "number" || typeof extent[i + 3] === "number" ? [{ label: axis, value: range(extent[i], extent[i + 3]) }] : [],
  );
}

/** Shown in the colour field of an object without a colour of its own (Slate of the brand palette). */
export const NO_COLOR = "#8a99a8";

/** "#rrggbb" in lower case; anything else — null. */
export function normalizeColor(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const text = value.trim().toLowerCase();
  return /^#[0-9a-f]{6}$/.test(text) ? text : null;
}

/** The text of a failed call for a panel or a command: the message of the core with its hint. */
export function errorText(e: unknown): string {
  const full = (e as { full?: unknown } | null)?.full; // CoreError
  return typeof full === "string" ? full : e instanceof Error ? e.message : String(e);
}

/** Adds an id to a list of checked ones or takes it away; the order of checking is kept. */
export const toggleId = (ids: readonly number[], id: number, on: boolean): number[] => (on ? (ids.includes(id) ? [...ids] : [...ids, id]) : ids.filter((x) => x !== id));
