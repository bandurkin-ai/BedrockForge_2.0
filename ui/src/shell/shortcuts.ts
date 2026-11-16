/** User hotkeys over the defaults of the ribbon (ТЗ 7.0.6). Pure; stored via settings.ts. */
import { RESERVED_SHORTCUTS } from "./keys";

/** Function key → shortcut ("" — the function has none). */
export type ShortcutOverrides = Record<string, string>;

export interface Bindable {
  command: string;
  args?: Record<string, unknown>;
  shortcut?: string;
}

/** A function is stable while its button changes: yes/no arguments are toggles ("Показать/Скрыть сетку"). */
export function functionKey(item: Pick<Bindable, "command" | "args">): string {
  const args = Object.fromEntries(
    Object.entries(item.args ?? {})
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([k, v]) => [k, typeof v === "boolean" ? "*" : v]),
  );
  return `${item.command}:${JSON.stringify(args)}`;
}

/** Effective shortcut of each function: defaults with the user's overrides applied. */
export function effectiveShortcuts(items: Bindable[], overrides: ShortcutOverrides): Record<string, string> {
  const out: Record<string, string> = {};
  for (const item of items) {
    const key = functionKey(item);
    if (!(key in out)) out[key] = key in overrides ? overrides[key] : item.shortcut ?? "";
  }
  return out;
}

export function defaultShortcuts(items: Bindable[]): Record<string, string> {
  return effectiveShortcuts(items, {});
}

/** Who has this shortcut now (other than `except`). */
export function holderOf(current: Record<string, string>, shortcut: string, except?: string): string | null {
  for (const [k, sc] of Object.entries(current)) if (k !== except && sc && sc === shortcut) return k;
  return null;
}

export function isReserved(shortcut: string): boolean {
  return RESERVED_SHORTCUTS.has(shortcut) || shortcut.split("+").includes("meta");
}

/**
 * Gives `key` the shortcut (or none with ""): another function that has it loses it. Entries equal to
 * the built-in hotkey are dropped, so a later change of the defaults reaches the user.
 */
export function assign(
  overrides: ShortcutOverrides,
  defaults: Record<string, string>,
  current: Record<string, string>,
  key: string,
  shortcut: string,
): ShortcutOverrides {
  const next = { ...overrides };
  if (shortcut) {
    for (const [other, sc] of Object.entries(current)) if (other !== key && sc === shortcut) next[other] = "";
  }
  next[key] = shortcut;
  for (const [k, v] of Object.entries(next)) if ((defaults[k] ?? "") === v) delete next[k];
  return next;
}

export function normalizeShortcuts(raw: unknown): ShortcutOverrides {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  return Object.fromEntries(
    Object.entries(raw).filter(([k, v]) => typeof v === "string" && k.length < 200 && !isReserved(v)),
  ) as ShortcutOverrides;
}
