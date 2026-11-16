/**
 * Keyboard shortcuts as strings ("ctrl+k", "t", "ctrl+`"). Letters and digits are matched by
 * the physical key (e.code), so hotkeys work in the Russian and English layouts (ТЗ 7.0.6).
 */

export interface KeyLike {
  key: string;
  code: string;
  ctrlKey: boolean;
  metaKey?: boolean;
  altKey: boolean;
  shiftKey: boolean;
}

const CODE_KEYS: Record<string, string> = {
  Backquote: "`",
  BracketLeft: "[",
  BracketRight: "]",
  Escape: "escape",
  Enter: "enter",
  Space: "space",
  Tab: "tab",
  Delete: "delete",
  Backspace: "backspace",
};

export function eventShortcut(e: KeyLike): string {
  let key = e.key.toLowerCase();
  if (/^Key[A-Z]$/.test(e.code)) key = e.code.slice(3).toLowerCase();
  else if (/^Digit\d$/.test(e.code)) key = e.code.slice(5);
  else if (/^F\d{1,2}$/.test(e.code)) key = e.code.toLowerCase();
  else if (e.code in CODE_KEYS) key = CODE_KEYS[e.code];
  return [(e.ctrlKey || e.metaKey) && "ctrl", e.altKey && "alt", e.shiftKey && "shift", key].filter(Boolean).join("+");
}

const NAMES: Record<string, string> = { ctrl: "Ctrl", alt: "Alt", shift: "Shift", escape: "Esc", "`": "Ё", enter: "Enter", space: "Пробел" };

/** "ctrl+`" -> "Ctrl+Ё" (the key above Tab on a Russian keyboard). */
export function formatShortcut(shortcut: string): string {
  return shortcut
    .split("+")
    .map((part) => NAMES[part] ?? part.toUpperCase())
    .join("+");
}

/** A single key without Ctrl/Alt: it must not fire while typing in a field. */
export function isPlainKey(shortcut: string): boolean {
  return !shortcut.includes("ctrl+") && !shortcut.includes("alt+");
}

/** Shortcuts kept by Windows or the shell; they cannot be assigned (ТЗ 7.0.6). */
export const RESERVED_SHORTCUTS = new Set(["alt+f4", "alt+tab", "ctrl+alt+delete", "f5", "f11", "ctrl+shift+i", "ctrl+r", "ctrl+w"]);

const MODIFIERS = new Set(["control", "shift", "alt", "meta", "os", "altgraph", "capslock", "dead", "unidentified", "process"]);

/** Whether the pressed key finishes a shortcut (not just a modifier being held). */
export const isComplete = (shortcut: string) => !MODIFIERS.has(shortcut.split("+").pop() ?? "");
