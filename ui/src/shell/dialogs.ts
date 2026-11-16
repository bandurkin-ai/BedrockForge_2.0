/**
 * Own dialogs instead of window.confirm/prompt/alert (ТЗ 7.0.11): a queue, one host, Enter/Esc.
 * Pure promise API; DialogHost.tsx draws the head of the queue.
 */
import { Store } from "./store";

export interface ConfirmOptions {
  title?: string;
  message: string;
  okLabel?: string;
  danger?: boolean;
}

export interface TextOptions {
  title: string;
  label?: string;
  initial?: string;
  allowEmpty?: boolean;
  okLabel?: string;
}

export interface MessageOptions {
  title?: string;
  message: string;
  /** Operation log shown with «Скопировать» / «Сохранить .txt» (ТЗ 7.9). */
  log?: string;
}

export type DialogRequest =
  | { id: number; kind: "confirm"; options: ConfirmOptions; resolve: (ok: boolean) => void }
  | { id: number; kind: "text"; options: TextOptions; resolve: (value: string | null) => void }
  | { id: number; kind: "message"; options: MessageOptions; resolve: () => void };

export const dialogStore = new Store<{ queue: DialogRequest[] }>({ queue: [] });
let nextId = 1;

function push(req: DialogRequest): void {
  dialogStore.set((s) => ({ queue: [...s.queue, req] }));
}

export function closeDialog(id: number): void {
  dialogStore.set((s) => ({ queue: s.queue.filter((d) => d.id !== id) }));
}

export function askConfirm(options: ConfirmOptions | string): Promise<boolean> {
  const o = typeof options === "string" ? { message: options } : options;
  return new Promise((resolve) => push({ id: nextId++, kind: "confirm", options: o, resolve }));
}

export function askText(options: TextOptions): Promise<string | null> {
  return new Promise((resolve) => push({ id: nextId++, kind: "text", options, resolve }));
}

export function showMessage(options: MessageOptions | string): Promise<void> {
  const o = typeof options === "string" ? { message: options } : options;
  return new Promise((resolve) => push({ id: nextId++, kind: "message", options: o, resolve }));
}

/** Text of askText: trimmed; empty is allowed only with allowEmpty. null — not acceptable. */
export function acceptText(value: string, allowEmpty = false): string | null {
  const v = value.trim();
  return v || allowEmpty ? v : null;
}
