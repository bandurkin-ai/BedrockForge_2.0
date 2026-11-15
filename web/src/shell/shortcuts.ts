import type { RibbonItem, RibbonTab } from "./ribbonConfig";

/** Hotkeys the user set: function key → shortcut ("" — the function has none). */
export type ShortcutOverrides = Record<string, string>;

const STORAGE_KEY = "bedrockforge.shortcuts";

/**
 * A function of the ribbon, stable while its button changes: yes/no arguments are toggles
 * ("Показать сетку" ↔ "Скрыть сетку" is one function).
 */
export function functionKey(item: Pick<RibbonItem, "command" | "args">): string {
  const args = Object.fromEntries(Object.entries(item.args ?? {}).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => [k, typeof v === "boolean" ? "*" : v]));
  return `${item.command}:${JSON.stringify(args)}`;
}

/** The tabs with the user's hotkeys in place of the built-in ones. */
export function applyShortcuts(tabs: RibbonTab[], overrides: ShortcutOverrides): RibbonTab[] {
  if (!Object.keys(overrides).length) return tabs;
  return tabs.map((tab) => ({
    ...tab,
    groups: tab.groups.map((g) => ({
      ...g,
      items: g.items.map((item) => {
        const key = functionKey(item);
        return key in overrides ? { ...item, shortcut: overrides[key] || undefined } : item;
      }),
    })),
  }));
}

/** Shortcuts the browser keeps for itself: a page never gets them. */
export const BROWSER_SHORTCUTS = new Set(["ctrl+w", "ctrl+t", "ctrl+n", "ctrl+shift+n", "ctrl+shift+t", "ctrl+tab", "ctrl+shift+tab", "alt+f4", "f11", "ctrl+l"]);
const MODIFIERS = new Set(["control", "shift", "alt", "meta", "os", "altgraph", "capslock", "dead", "unidentified", "process"]);

/** Whether the pressed key finishes a shortcut (not just a modifier being held). */
export const isComplete = (shortcut: string) => !MODIFIERS.has(shortcut.split("+").pop() ?? "");

export function loadShortcuts(): ShortcutOverrides {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
    return raw && typeof raw === "object" && !Array.isArray(raw)
      ? Object.fromEntries(Object.entries(raw).filter(([, v]) => typeof v === "string")) as ShortcutOverrides
      : {};
  } catch {
    return {};
  }
}

export function saveShortcuts(overrides: ShortcutOverrides): void {
  try {
    if (Object.keys(overrides).length) localStorage.setItem(STORAGE_KEY, JSON.stringify(overrides));
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // private mode: the hotkeys live until the page is closed
  }
}

/**
 * Gives `key` the shortcut (or none with ""): another function that has it loses it. Entries that
 * equal the built-in hotkey are dropped, so a later change of the defaults reaches the user.
 */
export function assign(overrides: ShortcutOverrides, defaults: Record<string, string>, current: Record<string, string>, key: string, shortcut: string): ShortcutOverrides {
  const next = { ...overrides };
  if (shortcut) {
    for (const [other, sc] of Object.entries(current)) if (other !== key && sc === shortcut) next[other] = "";
  }
  next[key] = shortcut;
  for (const [k, v] of Object.entries(next)) if ((defaults[k] ?? "") === v) delete next[k];
  return next;
}
