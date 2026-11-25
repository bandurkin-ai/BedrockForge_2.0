/**
 * The window «Влияние изменения» as a question (ADR 0025 §7): before a replacement, a deletion or an edit of a source the
 * module asks `askImpact(node, "заменить таблицу")`; with no dependents it answers `true` at once, else the window opens
 * with «Продолжить» and «Отмена» (the cross is «Отмена») and the answer is what the person chose. The change is never blocked
 * by the graph itself — the person decides. No React.
 */
import { openWindow, windowStore } from "../../shell/windows";
import { getImpact, type LNode } from "./api";

export const IMPACT_WINDOW = "lineage.impact";
export const IMPACT_TITLE = "Влияние изменения";

const waiting = new Map<number, (ok: boolean) => void>();
let nextToken = 1;

/** The answer of the person to the question of a window (the buttons and the unmount of the window). */
export function settleAsk(token: number, ok: boolean): void {
  const done = waiting.get(token);
  if (!done) return;
  waiting.delete(token);
  done(ok);
}

/**
 * The window of the question was taken away: the cross is «Отмена». The cleanup of an effect also runs while the window stays
 * (StrictMode of the development build, a new token) — then the question is not cancelled: only a window that is out of the store is gone.
 */
export function abandonAsk(token: number, winId: string): void {
  if (!windowStore.get().windows.some((w) => w.id === winId)) settleAsk(token, false);
}

export const isAsking = (token: number): boolean => waiting.has(token);

const viewport = (): [number, number] => (typeof window === "undefined" ? [1280, 800] : [window.innerWidth, window.innerHeight]);

/** The window without a question (the command `lineage.impact`): the same window opens once for a node. */
export function openImpact(node: LNode): string {
  return openWindow(IMPACT_WINDOW, IMPACT_TITLE, "form", { kind: node.kind, id: node.id, sub: node.sub ?? "" }, ...viewport());
}

/**
 * `true` — go on (nothing is built from the node, the core did not answer, or the person pressed «Продолжить»); `false` — the
 * person refused. `action` — what is going to be done, in the infinitive («заменить таблицу «Пробы»»).
 */
export async function askImpact(node: LNode, action: string): Promise<boolean> {
  let total = 0;
  try {
    total = (await getImpact(node)).total;
  } catch {
    return true; // no answer is no reason to stop an edit (the node may not be in the graph)
  }
  if (total === 0) return true;
  const token = nextToken++;
  return new Promise<boolean>((resolve) => {
    waiting.set(token, resolve);
    openWindow(IMPACT_WINDOW, IMPACT_TITLE, "form", { kind: node.kind, id: node.id, sub: node.sub ?? "", ask: action, token }, ...viewport());
  });
}
