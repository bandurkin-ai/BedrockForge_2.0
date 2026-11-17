/**
 * Undo history of the display form (ТЗ 7.16, «Ctrl+Z / Ctrl+Y»; ported from BF DownholeForm). Pure: no React.
 * A change records the state before it; quick changes of one control (the same `key` within `joinMs`) join
 * into one step, so dragging a slider is undone at once.
 */
export class EditHistory<T> {
  private past: T[] = [];
  private future: T[] = [];
  private key: string | null = null;
  private at = 0;

  constructor(
    private readonly limit = 100,
    private readonly joinMs = 1500,
  ) {}

  /** Before a change of `current`. */
  record(current: T, key?: string, now = Date.now()): void {
    if (!(key && this.key === key && now - this.at < this.joinMs)) {
      this.past.push(current);
      if (this.past.length > this.limit) this.past.shift();
    }
    this.future = [];
    this.key = key ?? null;
    this.at = now;
  }

  /** The state before the last step, or null; `current` goes to the redo list. */
  undo(current: T): T | null {
    if (!this.past.length) return null;
    const prev = this.past.pop() as T;
    this.future.push(current);
    this.key = null;
    return prev;
  }

  redo(current: T): T | null {
    if (!this.future.length) return null;
    const next = this.future.pop() as T;
    this.past.push(current);
    this.key = null;
    return next;
  }

  get canUndo(): boolean {
    return this.past.length > 0;
  }

  get canRedo(): boolean {
    return this.future.length > 0;
  }

  clear(): void {
    this.past = [];
    this.future = [];
    this.key = null;
  }
}

/** Ctrl+Z / Ctrl+Y (and Ctrl+Shift+Z, the Russian layout «я» / «н»): "undo", "redo" or null. */
export function historyKey(e: { key: string; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean }): "undo" | "redo" | null {
  if (!(e.ctrlKey || e.metaKey)) return null;
  const k = e.key.toLowerCase();
  if ((k === "z" && !e.shiftKey) || k === "я") return "undo";
  if (k === "y" || (k === "z" && e.shiftKey) || k === "н") return "redo";
  return null;
}
