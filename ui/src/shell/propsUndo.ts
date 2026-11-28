/**
 * The stack of «Отменить» for the properties changed on several objects at once (Д-44 А5, 27.11): one entry is one change of
 * the panel «Свойства» (a field set on every chosen object) and the commands of the registry that bring the old values back.
 * The command `props.undo`, the button of the panel and Ctrl+Z take the last entry. The editors keep their own undo: the
 * string editor (`lines.undo`, Ctrl+Z while its window has the focus), the table (`table.undo`, the journal of edits) and the
 * forms with their own history — `ctrlZTarget` leaves the key to them. Pure but for the keys: `attachUndoKey`.
 */
import type { App } from "./app";
import { dialogStore } from "./dialogs";
import { Store } from "./store";

export interface UndoStep {
  command: string;
  args: Record<string, unknown>;
}

/** One change of properties: what the button says and the commands that take it back. */
export interface UndoEntry {
  text: string;
  steps: readonly UndoStep[];
}

/** How many changes are kept: the older ones are forgotten. */
export const UNDO_LIMIT = 50;

export const undoStore = new Store<{ entries: readonly UndoEntry[] }>({ entries: [] });

/** Puts a change on the stack (an entry without steps changes nothing and is not kept). */
export function pushUndo(entry: UndoEntry): void {
  if (!entry.steps.length) return;
  undoStore.set((s) => ({ entries: [...s.entries, entry].slice(-UNDO_LIMIT) }));
}

export const lastUndo = (): UndoEntry | null => undoStore.get().entries.at(-1) ?? null;

export const clearUndo = (): void => {
  if (undoStore.get().entries.length) undoStore.set({ entries: [] });
};

/** The result of `props.undo`: the text for the log and the panel. */
export interface UndoResult {
  /** how many objects got their old values back */
  done: number;
  /** objects whose values could not be brought back (deleted, unavailable) */
  failed: number;
  text: string;
}

/**
 * Takes the last change back: every step runs on its own, so an object that is gone (its step is refused) does not keep
 * the others from getting their values back. The entry leaves the stack at once — a second «Отменить» takes the one before.
 */
export async function undoLast(run: (command: string, args: Record<string, unknown>) => Promise<boolean>): Promise<UndoResult | null> {
  const entry = lastUndo();
  if (!entry) return null;
  undoStore.set((s) => ({ entries: s.entries.slice(0, -1) }));
  let done = 0;
  let failed = 0;
  for (const step of entry.steps) {
    if (await run(step.command, step.args)) done++;
    else failed++;
  }
  const tail = failed ? `; не возвращено: ${failed} (объект удалён или недоступен)` : "";
  return { done, failed, text: `Отменено: ${entry.text}${tail}` };
}

/** `props.undo`, the mirror of the depth in `AppState` (the availability of the button) and the end of the stack with the project. */
export function bindPropsUndo(app: App): void {
  app.registry.bind(
    "props.undo",
    async () => {
      const r = await undoLast(async (command, args) => (await app.registry.execute(command, args)).ok);
      if (!r) return "Нечего отменять: изменений свойств нет";
      if (!r.done) throw new Error(`Не отменено: ${r.failed} шагов не выполнены (объекты удалены или недоступны)`);
      return r.text;
    },
    (ctx) => (ctx.canUndo ? null : "Нечего отменять: изменений свойств нет"),
  );
  undoStore.subscribe(() => {
    const n = undoStore.get().entries.length;
    if (app.store.get().undoDepth !== n) app.store.set({ undoDepth: n });
  });
  let project = app.store.get().projectName;
  app.store.subscribe(() => {
    const p = app.store.get().projectName;
    if (p !== project) {
      project = p;
      clearUndo(); // the objects of another project are not these
    }
  });
}

// ---- Ctrl+Z ---------------------------------------------------------------------------------------------------------

export interface KeyTarget {
  /** the key went to a field where text is typed: the browser undoes the text there */
  typing: boolean;
  /** a dialog, the palette or a window of the shell with a question is open */
  modal: boolean;
  /** the key went to a window of its own (a form with its own history, the wizard of import…) */
  inWindow: boolean;
  /** the key went to the bottom panel: the table has the journal of edits and no key */
  inBottom: boolean;
  /** the event was handled already (the string editor, a form with a history) */
  handled: boolean;
}

/** Who answers Ctrl+Z: "props" — the stack of properties, "other" — another editor or the browser keeps the key. */
export function ctrlZTarget(t: KeyTarget): "props" | "other" {
  return t.handled || t.typing || t.modal || t.inWindow || t.inBottom ? "other" : "props";
}

/** Ctrl+Z and the same key on the Russian layout («я»); Ctrl+Shift+Z and Ctrl+Y are the redo of the editors. */
export function isUndoKey(e: { key: string; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean; altKey?: boolean }): boolean {
  if (!(e.ctrlKey || e.metaKey) || e.shiftKey || e.altKey) return false;
  const k = e.key.toLowerCase();
  return k === "z" || k === "я";
}

const TEXT_INPUTS = new Set(["text", "search", "number", "email", "url", "password", "tel", "date", "datetime-local", "time", "month", "week"]);

/** A field where the browser undoes the typed text (check boxes, colours and ranges are not such). */
export function isTextField(el: EventTarget | null): boolean {
  const e = el as HTMLElement | null;
  if (!e || typeof e.tagName !== "string") return false;
  if (e.tagName === "INPUT") return TEXT_INPUTS.has(((e as HTMLInputElement).type || "text").toLowerCase());
  return e.tagName === "TEXTAREA" || e.isContentEditable === true;
}

/** Ctrl+Z of the window: the last change of properties is taken back unless an editor keeps the key. Gives the detach. */
export function attachUndoKey(app: App): () => void {
  if (typeof window === "undefined") return () => undefined;
  const onKey = (e: KeyboardEvent) => {
    if (!isUndoKey(e) || e.repeat) return;
    const el = e.target as Element | null;
    const target = ctrlZTarget({
      typing: isTextField(e.target),
      modal: dialogStore.get().queue.length > 0 || app.palette.get().query !== null || app.store.get().modal > 0,
      inWindow: !!el?.closest?.('[role="dialog"]'),
      inBottom: !!el?.closest?.('[aria-label="Нижняя панель"]'),
      handled: e.defaultPrevented,
    });
    if (target !== "props" || !lastUndo()) return;
    e.preventDefault();
    void app.run("props.undo");
  };
  window.addEventListener("keydown", onKey);
  return () => window.removeEventListener("keydown", onKey);
}
