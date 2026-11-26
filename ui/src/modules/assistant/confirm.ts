/**
 * The confirmation of a command that Claude asks for in the chat (Э23, 23.15; ТЗ 7.2 `confirm`, 7.32 «Панель чата»; ADR 0029 решения 25–26): `registry.hooks.confirm`
 * is replaced, the window of the application does not open. A call from Claude to a command with `confirm` waits for «Разрешить» / «Отклонить» on a card of the chat
 * (`ConfirmCard.tsx`, shown at the end of the feed); the card shows the title and the description of the command and ALL its arguments as they are (an injected
 * text in the data of the project cannot hide what is asked for). Closing the panel, «Новый диалог» and «Стоп» are «Отклонить» (`declineAllConfirms`).
 *
 * The queue is a store of views; the promises live in a map by id. No React.
 */
import type { Command } from "../../shell/commands";
import type { CommandRegistry } from "../../shell/registry";
import { Store } from "../../shell/store";

export interface ConfirmView {
  id: number;
  /** the id of the command in the registry (`project.delete`) */
  commandId: string;
  title: string;
  /** the description of the registry (for people and for the model) */
  description: string;
  /** `key = value` lines of all the arguments, nothing is cut */
  args: string[];
}

export const confirmStore = new Store<{ queue: ConfirmView[] }>({ queue: [] });

const waiting = new Map<number, (allow: boolean) => void>();
let seq = 0;

/** One argument as it is: a string raw, anything else as JSON; a gap is «null», never an empty text. */
export function argText(value: unknown): string {
  if (typeof value === "string") return value;
  return JSON.stringify(value) ?? String(value);
}

export function confirmArgs(args: Record<string, unknown>): string[] {
  return Object.keys(args).map((k) => `${k} = ${argText(args[k])}`);
}

/** The question to the user; resolves with the answer (declined is `false`). */
export function askClaudeConfirm(command: Pick<Command, "id" | "title" | "description">, args: Record<string, unknown>): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    const id = ++seq;
    waiting.set(id, resolve);
    const view: ConfirmView = { id, commandId: command.id, title: command.title, description: command.description, args: confirmArgs(args) };
    confirmStore.set((s) => ({ queue: [...s.queue, view] }));
  });
}

/** The answer of a card; false when the question is already gone (answered, or declined by a stop). */
export function answerConfirm(id: number, allow: boolean): boolean {
  const resolve = waiting.get(id);
  if (!resolve) return false;
  waiting.delete(id);
  confirmStore.set((s) => ({ queue: s.queue.filter((v) => v.id !== id) }));
  resolve(allow);
  return true;
}

/** «Стоп», «Новый диалог», a closed panel: every open question is declined. Returns how many there were. */
export function declineAllConfirms(): number {
  const ids = [...waiting.keys()];
  for (const id of ids) answerConfirm(id, false);
  return ids.length;
}

export const hasOpenConfirm = (): boolean => waiting.size > 0;

/**
 * Replaces `registry.hooks.confirm` (the registry asks only for a call of source «claude»). `onAsk` runs first — it opens the right dock on «Claude» (ТЗ 7.0.3) so the card
 * is in sight. Returns the function that puts the previous hook back (tests, a second `bindAssistant`).
 */
export function installConfirm(registry: Pick<CommandRegistry, "hooks">, onAsk?: () => void): () => void {
  const previous = registry.hooks;
  registry.hooks = {
    ...previous,
    confirm: (command, args) => {
      onAsk?.();
      return askClaudeConfirm(command, args);
    },
  };
  return () => {
    declineAllConfirms();
    registry.hooks = previous;
  };
}
