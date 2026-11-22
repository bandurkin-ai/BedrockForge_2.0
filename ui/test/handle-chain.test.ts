// The chain of the handles of the main 3D (ADR 0022 §9, решение 18; Э16 16.19): `Scene.setHandles` keeps one taker,
// the chain is that one when there are two or more (the box of a swath, the ellipsoid of a model, of a trend); one is
// set as it is; the handler of the window in focus first; a press belongs to its taker; one handle lit at a time.
import { describe, expect, it, vi } from "vitest";
import { addHandles, HandleChain, handleChain, removeHandles, type ChainHandler } from "../src/viz/core/handleChain";
import type { ToolHandler, ToolPointer } from "../src/viz/core/toolInput";

const ev = (type: ToolPointer["type"], x = 10, y = 10, button = type === "move" ? -1 : 0): ToolPointer => ({ type, x, y, clientX: x, clientY: y, button, shift: false, ctrl: false });

function handler(name: string, got: string[], opts: { takes?: boolean; focused?: () => boolean; hits?: (x: number, y: number) => boolean } = {}): ChainHandler & { cancel: ReturnType<typeof vi.fn> } {
  return {
    pointer: (e) => (got.push(`${name}:${e.type}`), e.type === "down" && opts.takes === true),
    cancel: vi.fn(),
    ...(opts.focused ? { focused: opts.focused } : {}),
    ...(opts.hits ? { hits: opts.hits } : {}),
  };
}

function slot() {
  const s = { held: null as ToolHandler | null, calls: 0, setHandles: (h: ToolHandler | null) => void ((s.held = h), s.calls++) };
  return s;
}

describe("the chain of handles", () => {
  it("one handler is set in the slot as it is, two — the chain, none — the slot empty", () => {
    const s = slot();
    const got: string[] = [];
    const a = handler("a", got);
    const b = handler("b", got);
    const offA = addHandles(s, a);
    expect(s.held).toBe(a);
    addHandles(s, b);
    expect(s.held).toBe(handleChain(s));
    expect(handleChain(s).size).toBe(2);
    offA();
    expect(s.held).toBe(b);
    removeHandles(s, b);
    expect(s.held).toBeNull();
    removeHandles(s, b); // twice: nothing
    expect(s.calls).toBe(4);
  });

  it("the handler of the window in focus is asked first; the taker of a press has its drag, the click; the others drop their hover", () => {
    const s = slot();
    const got: string[] = [];
    let focus = false;
    const swath = handler("swath", got, { takes: true });
    const ell = handler("ell", got, { takes: true, focused: () => focus });
    const chain = new HandleChain(s);
    chain.add(swath);
    chain.add(ell);
    expect(chain.ordered()).toEqual([swath, ell]);
    focus = true;
    expect(chain.ordered()).toEqual([ell, swath]);
    expect(chain.pointer(ev("down"))).toBe(true);
    expect(got).toEqual(["ell:down", "swath:leave"]);
    got.length = 0;
    expect(chain.pointer(ev("move", 20, 20, 0))).toBe(true);
    expect(chain.pointer(ev("up"))).toBe(true);
    expect(chain.pointer(ev("click"))).toBe(true);
    expect(got).toEqual(["ell:move", "ell:up", "ell:click"]);
    // no taker: a press nobody takes is the scene's
    got.length = 0;
    const none = new HandleChain(slot());
    none.add(handler("x", got));
    none.add(handler("y", got));
    expect(none.pointer(ev("down"))).toBe(false);
    expect(none.pointer(ev("click"))).toBe(false);
    expect(got).toEqual(["x:down", "y:down", "x:click", "y:click"]);
  });

  it("a move lights one handle: after the one hit the others get `leave`; without `hits` a handler always gets the move", () => {
    const got: string[] = [];
    const chain = new HandleChain(slot());
    chain.add(handler("a", got, { hits: (x) => x < 50 }));
    chain.add(handler("b", got, { hits: () => true }));
    chain.add(handler("c", got));
    chain.pointer(ev("move", 10));
    expect(got).toEqual(["a:move", "b:leave", "c:move"]);
    got.length = 0;
    chain.pointer(ev("move", 90));
    expect(got).toEqual(["a:move", "b:move", "c:move"]);
    got.length = 0;
    chain.pointer(ev("leave"));
    expect(got).toEqual(["a:leave", "b:leave", "c:leave"]);
  });

  it("`cancel` (the editor's tool came) goes to the taker of the press only; a handler removed mid-drag is forgotten", () => {
    const got: string[] = [];
    const chain = new HandleChain(slot());
    const a = handler("a", got, { takes: true });
    const b = handler("b", got);
    chain.add(a);
    chain.add(b);
    chain.pointer(ev("down"));
    chain.cancel();
    expect(a.cancel).toHaveBeenCalledTimes(1);
    expect(b.cancel).not.toHaveBeenCalled();
    chain.pointer(ev("down"));
    chain.remove(a);
    got.length = 0;
    expect(chain.pointer(ev("up"))).toBe(false);
    expect(got).toEqual([]);
  });
});
