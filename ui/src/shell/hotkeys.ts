/** Resolving a key press to a ribbon function (ТЗ 7.0.6). Pure; Shell.tsx listens to keydown. */
import { isPlainKey } from "./keys";
import type { FlatItem } from "./ribbonConfig";
import { effectiveShortcuts, functionKey, type ShortcutOverrides } from "./shortcuts";

export interface KeyTarget {
  /** The key went to a text field (input, textarea, contenteditable). */
  typing: boolean;
  /** Palette or an import wizard is open. */
  modal: boolean;
}

/** Shortcut → button that has it (first one wins; same function keeps the same key). */
export function shortcutMap(items: FlatItem[], overrides: ShortcutOverrides): Map<string, FlatItem> {
  const effective = effectiveShortcuts(items, overrides);
  const map = new Map<string, FlatItem>();
  for (const item of items) {
    const sc = effective[functionKey(item)];
    if (sc && !map.has(sc)) map.set(sc, item);
  }
  return map;
}

/** Which button a key press runs, or null. Ctrl+K always opens the palette unless it is open. */
export function resolveKey(shortcut: string, map: Map<string, FlatItem>, t: KeyTarget): FlatItem | null {
  const item = map.get(shortcut);
  if (!item) return null;
  if (t.modal) return null;
  if (t.typing && isPlainKey(shortcut)) return null;
  return item;
}
