/**
 * The keys of the group of the report as the window edits them (ADR 0027 решение 4, 29): up to 3 keys, each once; a key comes from the list
 * of the available ones (the category fields of the model and the bench «Уступ») by dragging or by a button, goes up or down, leaves.
 * Pure: every operation answers a new list (a refused one — the same list).
 */
import { keyLabel, sameKey, type KeyChoice } from "./form";

/** The keys the model offers: its category fields and the bench; the chosen ones are not offered again. */
export function availableKeys(categories: readonly string[], chosen: readonly KeyChoice[]): KeyChoice[] {
  const all: KeyChoice[] = [...categories.map((field): KeyChoice => ({ kind: "field", field })), { kind: "bench" }];
  return all.filter((k) => !chosen.some((c) => sameKey(c, k)));
}

/** Puts `key` at `at` (default — the end); a key that is there, or a fourth key, is refused (the same list). */
export function addKey(keys: readonly KeyChoice[], key: KeyChoice, max: number, at: number = keys.length): KeyChoice[] {
  if (keys.length >= max || keys.some((k) => sameKey(k, key))) return [...keys];
  const i = Math.max(0, Math.min(at, keys.length));
  return [...keys.slice(0, i), key, ...keys.slice(i)];
}

/** Moves the key at `from` to the place `to` (the index in the list as it is after the move). */
export function moveKey(keys: readonly KeyChoice[], from: number, to: number): KeyChoice[] {
  if (from < 0 || from >= keys.length) return [...keys];
  const out = keys.filter((_, i) => i !== from);
  out.splice(Math.max(0, Math.min(to, out.length)), 0, keys[from]);
  return out;
}

export const removeKey = (keys: readonly KeyChoice[], at: number): KeyChoice[] => keys.filter((_, i) => i !== at);

/** The text of a drag: where the key comes from and which key it is (`dataTransfer` takes text only). */
export interface DragKey {
  from: "available" | "chosen";
  key: KeyChoice;
  index: number;
}

export const encodeDrag = (d: DragKey): string => JSON.stringify(d);

export function decodeDrag(text: string): DragKey | null {
  try {
    const v = JSON.parse(text) as Partial<DragKey>;
    const k = v.key;
    const key: KeyChoice | null = k && k.kind === "bench" ? { kind: "bench" } : k && k.kind === "field" && typeof k.field === "string" ? { kind: "field", field: k.field } : null;
    if ((v.from !== "available" && v.from !== "chosen") || !key || typeof v.index !== "number") return null;
    return { from: v.from, key, index: v.index };
  } catch {
    return null;
  }
}

/** What a drop of `drag` at the place `at` of the chosen list does (a key from the list moves, a key from the available ones is added). */
export function dropOnChosen(keys: readonly KeyChoice[], drag: DragKey, at: number, max: number): KeyChoice[] {
  if (drag.from === "chosen") return moveKey(keys, drag.index, at > drag.index ? at - 1 : at);
  return addKey(keys, drag.key, max, at);
}

/** A drop on the available ones: the key leaves the chosen list. */
export const dropOnAvailable = (keys: readonly KeyChoice[], drag: DragKey): KeyChoice[] => (drag.from === "chosen" ? removeKey(keys, drag.index) : [...keys]);

export const keysSummary = (keys: readonly KeyChoice[]): string => (keys.length ? keys.map(keyLabel).join(" → ") : "без групп");
