/**
 * The chain of the handles of the main 3D (ADR 0022 §9, решение 18): the box of a swath (Д-01), the ellipsoid of a
 * variogram model (Д-14), the ellipsoid of a trend (Д-15) — every one a `ToolHandler`, while `Scene` keeps a single
 * one (`Scene.setHandles`, the slot after the editor's tool; `viz/Scene.ts` is not changed). The chain is that one:
 * one taker in the slot is set as it is (the old behaviour, nothing in between); two and more — the chain is set and
 * hands the events out in order, the handler whose window is in focus first (`focused`), then in the order added.
 *
 * - `down` — the first that takes it owns the press: its moves, the release and the click after it; the others drop
 *   their hover (`leave`).
 * - `move` without a press — each in order; a handler that says the cursor is on its handle (`hits`) gets it, the
 *   ones after it get `leave` (one handle lit at a time); a handler without `hits` always gets the move (the swath
 *   box and both ellipsoids say `hits`, so two of their handles are never lit at once).
 * - `click`, `dblclick` without a press — the first that takes it; `leave` — to every one.
 * - `cancel` (the editor's tool came in the middle of a drag) — to the owner of the press.
 *
 * Pure: no three.js; vitest.
 */
import type { ToolHandler, ToolPointer } from "./toolInput";

/** A handler in the chain: `focused` — its window is the one in focus (it goes first); `hits` — a handle under (x, y). */
export interface ChainHandler extends ToolHandler {
  cancel?(): void;
  focused?(): boolean;
  hits?(x: number, y: number): boolean;
}

/** What the chain needs of the scene: its one slot of handles. */
export interface HandleSlot {
  setHandles(handles: ToolHandler | null): void;
}

const LEAVE: ToolPointer = { type: "leave", x: 0, y: 0, clientX: 0, clientY: 0, button: -1, shift: false, ctrl: false };

export class HandleChain implements ToolHandler {
  private readonly list: ChainHandler[] = [];
  private taker: ChainHandler | null = null;
  /** what the slot of the scene holds now */
  private installed: ToolHandler | null = null;

  constructor(private readonly slot: HandleSlot) {}

  /** The handlers, the focused one first, then in the order added. */
  ordered(): ChainHandler[] {
    const focused = this.list.filter((h) => h.focused?.() === true);
    return focused.length ? [...focused, ...this.list.filter((h) => !focused.includes(h))] : [...this.list];
  }

  get size(): number {
    return this.list.length;
  }

  add(h: ChainHandler): () => void {
    if (!this.list.includes(h)) this.list.push(h);
    this.install();
    return () => this.remove(h);
  }

  remove(h: ChainHandler): void {
    const i = this.list.indexOf(h);
    if (i < 0) return;
    this.list.splice(i, 1);
    if (this.taker === h) this.taker = null;
    this.install();
  }

  /** One handler — itself in the slot; more — the chain; none — the slot empty. */
  private install(): void {
    const want: ToolHandler | null = this.list.length === 0 ? null : this.list.length === 1 ? this.list[0] : this;
    if (want === this.installed) return;
    this.installed = want;
    this.slot.setHandles(want);
  }

  cancel(): void {
    const t = this.taker;
    this.taker = null;
    t?.cancel?.();
  }

  pointer(e: ToolPointer): boolean {
    const all = this.ordered();
    switch (e.type) {
      case "down": {
        this.taker = all.find((h) => h.pointer(e)) ?? null;
        if (this.taker) for (const h of all) if (h !== this.taker) h.pointer(LEAVE);
        return this.taker !== null;
      }
      case "up":
        return this.taker ? this.taker.pointer(e) || true : false;
      case "click": {
        const t = this.taker;
        this.taker = null;
        if (t) return t.pointer(e) || true;
        return all.some((h) => h.pointer(e));
      }
      case "dblclick":
        return all.some((h) => h.pointer(e));
      case "move": {
        if (this.taker && e.button === 0) return this.taker.pointer(e) || true;
        let lit = false;
        let taken = false;
        for (const h of all) {
          if (lit && h.hits) {
            h.pointer(LEAVE);
            continue;
          }
          if (h.hits?.(e.x, e.y)) lit = true;
          taken = h.pointer(e) || taken;
        }
        return taken;
      }
      default:
        for (const h of all) h.pointer(e);
        return false;
    }
  }
}

const chains = new WeakMap<HandleSlot, HandleChain>();

/** The chain of a scene (made at the first use). */
export function handleChain(scene: HandleSlot): HandleChain {
  let c = chains.get(scene);
  if (!c) {
    c = new HandleChain(scene);
    chains.set(scene, c);
  }
  return c;
}

/** A handler into the chain of a scene; gives what takes it out. */
export const addHandles = (scene: HandleSlot, h: ChainHandler): (() => void) => handleChain(scene).add(h);

/** A handler out of the chain of a scene. */
export const removeHandles = (scene: HandleSlot, h: ChainHandler): void => chains.get(scene)?.remove(h);
