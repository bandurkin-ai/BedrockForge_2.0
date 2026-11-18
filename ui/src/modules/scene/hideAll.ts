/**
 * «Очистить окно» (ТЗ 7.35 Д-04, ADR 0008): the view key `hide_all = {on, shown}`. While `on`, only the
 * objects in `shown` are visible — any number of objects, while the stored lists stay ≤ 2000 keys.
 * The rest of the interface keeps reading one list of hidden keys: `effectiveHidden` builds it.
 * Normalization is the core's (viewstate/keys.py), checked on the shared fixtures core/tests/data/view.
 */
import type { ViewOp } from "./viewSync";

export interface HideAll {
  on: boolean;
  shown: string[];
}

export const HIDDEN_MAX = 2000;
const KEY_RE = /^(set|wf|ln|rs|bm):(\d{1,12})(?::(collars|paths|intervals))?$/;

/** Keys as the core keeps them: known form, duplicates once (the newest place), the newest 2000. */
export function normalizeKeys(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  const out: string[] = [];
  const seen = new Set<string>();
  for (let i = v.length - 1; i >= 0 && out.length < HIDDEN_MAX; i--) {
    const k = v[i];
    if (typeof k !== "string" || !KEY_RE.test(k) || seen.has(k)) continue;
    seen.add(k);
    out.push(k);
  }
  return out.reverse();
}

export function normalizeHideAll(v: unknown): HideAll {
  if (!v || typeof v !== "object" || Array.isArray(v)) return { on: false, shown: [] };
  const r = v as Record<string, unknown>;
  const on = r.on === true;
  return { on, shown: on ? normalizeKeys(r.shown) : [] };
}

/** The object key of a layer key: `set:3:collars` → `set:3`. */
export const topKey = (key: string): string => key.split(":").slice(0, 2).join(":");

/** Hidden keys the scene and the tree read: while `on`, every object not shown, plus the hidden sub-layers. */
export function effectiveHidden(hidden: readonly string[], hideAll: HideAll, objects: readonly string[]): string[] {
  if (!hideAll.on) return [...hidden];
  const shown = new Set(hideAll.shown);
  const out = new Set(hidden);
  for (const k of objects) if (!shown.has(k)) out.add(k);
  return [...out];
}

/** Show or hide an object while `on`: its object key joins or leaves `shown`. */
export function showUnderHideAll(key: string, visible: boolean): ViewOp {
  const top = topKey(key);
  return (v) => {
    const h = normalizeHideAll(v);
    if (!h.on) return h;
    const shown = h.shown.filter((k) => k !== top);
    if (visible) shown.push(top);
    return { on: true, shown: shown.slice(-HIDDEN_MAX) };
  };
}

/** New objects (an import) are shown: ТЗ 17.4.7 — what was just loaded is on the screen. */
export function showNew(keys: readonly string[]): ViewOp {
  return (v) => {
    const h = normalizeHideAll(v);
    if (!h.on || !keys.length) return h;
    const shown = h.shown.filter((k) => !keys.includes(k));
    return { on: true, shown: [...shown, ...keys].slice(-HIDDEN_MAX) };
  };
}

/** The new values of the keys «Очистить окно» changes (camera, section, grid and saved views stay). */
export function clearedKeys(displays: unknown): { hide_all: HideAll; hidden: string[]; displays: unknown } {
  const list = Array.isArray(displays)
    ? displays.map((d) => (d && typeof d === "object" ? { ...(d as Record<string, unknown>), visible: false } : d))
    : [];
  return { hide_all: { on: true, shown: [] }, hidden: [], displays: list };
}
