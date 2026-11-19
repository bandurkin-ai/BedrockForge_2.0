/**
 * Keys of the string editor (ADR 0015 §5; ТЗ 7.22): Enter — finish the line, Backspace — take its last point,
 * Esc, Delete, Ctrl+Z / Ctrl+Y, Ctrl+A, Ctrl+S, and the letters of the tools (V, L, P, T, Y, I — on the Russian
 * layout the same keys). They work while the 3D window or the editor panel was the last place pressed, in the
 * editing mode, not in fields of input and not under a dialog; then they go before the keys of the ribbon (Esc
 * of the editor, not «Снять выбор»). The session runs its keys itself (`session.key()` → the id of the
 * command); `lines.save` and the tools go through the registry.
 */
import type { EditKey, Tool } from "../../edit/tools";
import { TOOLS } from "../../edit/tools";
import type { App } from "../../shell/app";
import { dialogStore } from "../../shell/dialogs";
import { currentScene } from "../scene/sceneHost";
import { choiceOpen } from "./choice";
import { editStore } from "./editState";

export interface KeyContext {
  editing: boolean;
  /** the key went to a field of input */
  typing: boolean;
  /** a dialog, the palette, a question of the editor or the capture of a shortcut */
  modal: boolean;
  /** the 3D window or the panel was the last place pressed */
  focused: boolean;
}

export const editorKeyAllowed = (c: KeyContext): boolean => c.editing && c.focused && !c.typing && !c.modal;

/** The same keys on the Russian layout (ЙЦУКЕН). */
const RU: Record<string, string> = { м: "v", д: "l", з: "p", е: "t", н: "y", ш: "i" };

/** A plain letter of a tool, or null. */
export function toolKey(e: EditKey & { alt?: boolean }): Tool | null {
  if (e.ctrl || e.alt || e.key.length !== 1) return null;
  const k = e.key.toLowerCase();
  const latin = RU[k] ?? k;
  return TOOLS.find((t) => t.keys.toLowerCase() === latin)?.id ?? null;
}

export function isTyping(el: EventTarget | null): boolean {
  const e = el as HTMLElement | null;
  if (!e || typeof e.tagName !== "string") return false;
  return e.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(e.tagName);
}

/** Is a pressed element the 3D window or the panel of the editor. */
function inEditor(el: EventTarget | null): boolean {
  const e = el as Element | null;
  if (!e || typeof e.closest !== "function") return false;
  const host = currentScene()?.canvas.parentElement;
  return !!(host?.contains(e) || e.closest("[data-edit-panel]"));
}

/** The keys and the focus of the editor; gives the detach. */
export function attachEditorKeys(app: App): () => void {
  if (typeof window === "undefined") return () => undefined;
  const onPress = (e: PointerEvent) => {
    const focused = inEditor(e.target);
    if (focused !== editStore.get().focused) editStore.set({ focused });
  };
  const onKey = (e: KeyboardEvent) => {
    const s = editStore.get().session;
    if (!s) return;
    const modal = dialogStore.get().queue.length > 0 || app.palette.get().query !== null || app.store.get().modal > 0 || choiceOpen();
    if (!editorKeyAllowed({ editing: true, typing: isTyping(e.target), modal, focused: editStore.get().focused })) return;
    const k = { key: e.key, ctrl: e.ctrlKey || e.metaKey, shift: e.shiftKey };
    const tool = toolKey({ ...k, alt: e.altKey });
    let id: string | null;
    if (tool) {
      id = "lines.tool";
      if (!e.repeat) void app.run("lines.tool", { tool });
    } else {
      id = s.key(k);
      if (id === "lines.save" && !e.repeat) void app.run("lines.save");
    }
    if (!id) return;
    e.preventDefault();
    e.stopImmediatePropagation();
  };
  window.addEventListener("pointerdown", onPress, true);
  window.addEventListener("keydown", onKey, true);
  return () => {
    window.removeEventListener("pointerdown", onPress, true);
    window.removeEventListener("keydown", onKey, true);
  };
}
