/**
 * A question with several answers in an own window (ТЗ 7.0.11: never window.confirm): «Слой сохранён после
 * начала правки» with «Сохранить как новый слой», «Перечитать слой», «Отмена»; «Сохранить изменения?» on
 * leaving the editor. The window `lines-choice` draws the request (`ChoiceWindow.tsx`); closing it by the cross
 * is the answer `cancel`. No React.
 */
import { closeWindow, openWindow, setCloseGuard } from "../../shell/windows";
import { Store } from "../../shell/store";

export interface ChoiceAction {
  id: string;
  label: string;
  /** what the action does (the tooltip) */
  hint?: string;
  primary?: boolean;
  danger?: boolean;
}

export interface ChoiceRequest {
  id: number;
  title: string;
  message: string;
  actions: ChoiceAction[];
  winId: string;
}

export const CANCEL = "cancel";

export const choiceStore = new Store<{ pending: ChoiceRequest[] }>({ pending: [] });
const resolvers = new Map<number, (answer: string) => void>();
let nextId = 1;

function viewport(): [number, number] {
  return typeof window === "undefined" ? [1280, 800] : [window.innerWidth, window.innerHeight];
}

/** Asks and waits for the answer: the id of an action, or `cancel` (also when the window is closed). */
export function askChoice(title: string, message: string, actions: ChoiceAction[]): Promise<string> {
  const id = nextId++;
  const winId = openWindow("lines-choice", title, "compact", { choice: id }, ...viewport());
  choiceStore.set((s) => ({ pending: [...s.pending, { id, title, message, actions, winId }] }));
  return new Promise((resolve) => {
    resolvers.set(id, resolve);
    setCloseGuard(winId, async () => {
      answerChoice(id, CANCEL, false);
      return true;
    });
  });
}

/** The answer of the window; `close` — the window goes too. */
export function answerChoice(id: number, answer: string, close = true): void {
  const req = choiceStore.get().pending.find((r) => r.id === id);
  const resolve = resolvers.get(id);
  resolvers.delete(id);
  choiceStore.set((s) => ({ pending: s.pending.filter((r) => r.id !== id) }));
  if (req && close) closeWindow(req.winId);
  resolve?.(answer);
}

/** A question is open: the keys of the editor wait. */
export const choiceOpen = (): boolean => choiceStore.get().pending.length > 0;
