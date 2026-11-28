/**
 * The stack of «Отменить» for the properties changed on several objects at once (Д-44 А5, 27.11): one entry is one change of
 * the panel «Свойства» (a field set on every chosen object) and the commands of the registry that bring the old values back.
 * The command `props.undo`, the button of the panel and Ctrl+Z take the last entry. The editors keep their own undo: the
 * string editor (`lines.undo`, Ctrl+Z while its window has the focus), the table (`table.undo`, the journal of edits) and the
 * forms with their own history — `ctrlZTarget` leaves the key to them. Pure but for the keys: `attachUndoKey`.
 */
import { projectStore } from "../modules/projects/state";
import type { App } from "./app";
import { dialogStore } from "./dialogs";
import { buildRibbon, flattenRibbon } from "./ribbonConfig";
import { eventShortcut } from "./keys";
import { effectiveShortcuts, functionKey, type ShortcutOverrides } from "./shortcuts";
import { Store } from "./store";
import type { ViewState, WorkspaceState } from "./workspace";

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

/** How many runs of steps are going now: the panel applies a change, or an undo runs. While it is not 0 no undo starts. */
let busyCount = 0;

/** Marks the start of a run that must not meet an undo; gives the end of it (call it in `finally`). */
export function beginUndoBusy(): () => void {
  busyCount++;
  let ended = false;
  return () => {
    if (!ended) {
      ended = true;
      busyCount--;
    }
  };
}

export const isUndoBusy = (): boolean => busyCount > 0;

export const BUSY_REASON = "Изменение свойств ещё применяется: дождитесь окончания";

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
 * the others from getting their values back. The entry leaves the stack at once — a second «Отменить» takes the one before —
 * but if no step was done at all it goes back on the stack: nothing was lost, the user may try again. Null while a change
 * is being applied or another undo runs (the stack is not touched).
 */
export async function undoLast(run: (command: string, args: Record<string, unknown>) => Promise<boolean>): Promise<UndoResult | null> {
  const entry = lastUndo();
  if (!entry || isUndoBusy()) return null;
  const end = beginUndoBusy();
  undoStore.set((s) => ({ entries: s.entries.slice(0, -1) }));
  let done = 0;
  let failed = 0;
  try {
    for (const step of entry.steps) {
      if (await run(step.command, step.args)) done++;
      else failed++;
    }
  } finally {
    if (done === 0) undoStore.set((s) => ({ entries: [...s.entries, entry].slice(-UNDO_LIMIT) }));
    end();
  }
  const tail = failed ? `; не возвращено: ${failed} (объект удалён или недоступен)` : "";
  return { done, failed, text: `Отменено: ${entry.text}${tail}` };
}

/** `props.undo`, the mirror of the depth in `AppState` (the availability of the button) and the end of the stack with the project. */
export function bindPropsUndo(app: App): void {
  app.registry.bind(
    "props.undo",
    async () => {
      if (isUndoBusy()) throw new Error(BUSY_REASON);
      const r = await undoLast(async (command, args) => (await app.registry.execute(command, args)).ok);
      if (!r) return "Нечего отменять: изменений свойств нет";
      if (!r.done) throw new Error(`Не отменено: ${r.failed} шагов не выполнены (объекты удалены или недоступны)`);
      return r.text;
    },
    (ctx) => (ctx.canUndo ? (isUndoBusy() ? BUSY_REASON : null) : "Нечего отменять: изменений свойств нет"),
  );
  undoStore.subscribe(() => {
    const n = undoStore.get().entries.length;
    if (app.store.get().undoDepth !== n) app.store.set({ undoDepth: n });
  });
  // the objects of another project are not these: the stack goes with the project, by its uid (a rename keeps it)
  let project = projectStore.get().current?.uid ?? null;
  projectStore.subscribe(() => {
    const uid = projectStore.get().current?.uid ?? null;
    if (uid !== project) {
      project = uid;
      clearUndo();
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

const TEXT_INPUTS = new Set(["text", "search", "number", "email", "url", "password", "tel", "date", "datetime-local", "time", "month", "week"]);

/** A field where the browser undoes the typed text (check boxes, colours and ranges are not such). */
export function isTextField(el: EventTarget | null): boolean {
  const e = el as HTMLElement | null;
  if (!e || typeof e.tagName !== "string") return false;
  if (e.tagName === "INPUT") return TEXT_INPUTS.has(((e as HTMLInputElement).type || "text").toLowerCase());
  return e.tagName === "TEXTAREA" || e.isContentEditable === true;
}

/** The command of the key, the one the registry's key map knows («Настройка → клавиши», `shortcuts.ts`). */
export const UNDO_COMMAND = "props.undo";

/** The key of `props.undo` now: the default of the ribbon («Ctrl+Z») or the one the user gave it ("" — none). The ribbon is built once per change of its inputs. */
let keyCache: { ws: WorkspaceState; view: ViewState; overrides: ShortcutOverrides; key: string } | null = null;
export function undoShortcut(app: App): string {
  const { workspace: ws, view, shortcuts: overrides } = app.store.get();
  if (keyCache && keyCache.ws === ws && keyCache.view === view && keyCache.overrides === overrides) return keyCache.key;
  const item = flattenRibbon(buildRibbon(ws, view)).find((i) => i.command === UNDO_COMMAND);
  const key = item ? (effectiveShortcuts([item], overrides)[functionKey(item)] ?? "") : "";
  keyCache = { ws, view, overrides, key };
  return key;
}

/**
 * The key of `props.undo` in the window (default Ctrl+Z, also on the Russian layout: the key is matched by its place on the
 * keyboard, as every hotkey of the shell). The editors, the text fields, the windows and the tables go first — they keep
 * the key (`ctrlZTarget`); while the panel applies a change the key does nothing. Gives the detach.
 */
export function attachUndoKey(app: App): () => void {
  if (typeof window === "undefined") return () => undefined;
  const onKey = (e: KeyboardEvent) => {
    if (e.repeat) return;
    const key = undoShortcut(app);
    if (!key || eventShortcut(e) !== key) return;
    const el = e.target as Element | null;
    const target = ctrlZTarget({
      typing: isTextField(e.target),
      modal: dialogStore.get().queue.length > 0 || app.palette.get().query !== null || app.store.get().modal > 0,
      inWindow: !!el?.closest?.('[role="dialog"]'),
      inBottom: !!el?.closest?.('[aria-label="Нижняя панель"]'),
      handled: e.defaultPrevented,
    });
    if (target !== "props" || !lastUndo() || isUndoBusy()) return;
    e.preventDefault();
    void app.run(UNDO_COMMAND);
  };
  window.addEventListener("keydown", onKey);
  return () => window.removeEventListener("keydown", onKey);
}
