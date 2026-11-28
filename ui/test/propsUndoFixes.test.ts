// Э27, 27.10б (second review): every kind of the tree reaches `tree.select_*` through the registry; the entry of «Отменить» is not lost
// when nothing could be undone; an object changed in part is undone; no undo runs while the panel applies a change; Ctrl+Z is a key of
// the command `props.undo` that the user can move.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApp } from "../src/shell/app";
import { applyAll, type MultiField, type Lookup } from "../src/shell/multiPropsModel";
import { clearGroup } from "../src/shell/multiSelect";
import { attachUndoKey, beginUndoBusy, clearUndo, isUndoBusy, lastUndo, pushUndo, undoLast, undoShortcut, undoStore } from "../src/shell/propsUndo";
import { groupStore, multiStore, NO_GROUP, NO_MULTI, PICK_KINDS, TREE_KINDS } from "../src/shell/selection";
import { commands } from "../src/modules/shell/commands";

afterEach(() => {
  groupStore.set(NO_GROUP);
  multiStore.set(NO_MULTI);
  clearUndo();
  vi.unstubAllGlobals();
});

describe("the kinds of `tree.select_range` and `tree.select_toggle`", () => {
  it("the enum of both commands is the list of the tree's kinds, no kind is missing", () => {
    for (const id of ["tree.select_range", "tree.select_toggle"]) {
      const kind = commands.find((c) => c.id === id)?.params?.kind;
      expect([...(kind?.enum ?? [])]).toEqual([...PICK_KINDS]);
    }
    for (const k of ["line_file", "implicit_group", "implicit_fault", "implicit_stack", "series_section", ...TREE_KINDS]) expect(PICK_KINDS).toContain(k);
  });

  it.each([...PICK_KINDS])("the registry takes the kind «%s» (the check of the enum does not refuse it)", async (kind) => {
    const app = createApp({ persist: false });
    const r = await app.registry.execute("tree.select_toggle", { kind, id: 1 });
    // there is no project here, so the command is refused for that — never for the kind
    expect(r.result).not.toContain("допустимые значения");
    const range = await app.registry.execute("tree.select_range", { kind, id: 1 });
    expect(range.result).not.toContain("допустимые значения");
  });

  it("with a project the row is chosen through the registry, whatever its kind", async () => {
    const app = createApp({ persist: false });
    app.store.set({ projectName: "П" });
    for (const kind of ["line_file", "implicit_group", "implicit_fault", "implicit_stack", "series_section"] as const) {
      groupStore.set(NO_GROUP);
      const r = await app.registry.execute("tree.select_toggle", { kind, id: 7 });
      expect(r.ok, kind).toBe(true);
      expect(groupStore.get().items.map((x) => `${x.kind}:${x.id}`), kind).toEqual([`${kind}:7`]);
    }
    clearGroup();
  });
});

describe("undoLast when nothing could be undone", () => {
  beforeEach(() => clearUndo());
  const step = (id: number) => ({ command: "layer.show", args: { layer: "lines", id, visible: true } });

  it("returns the entry to the stack when no step was done, and drops it when some were", async () => {
    pushUndo({ text: "первое", steps: [step(1)] });
    pushUndo({ text: "второе", steps: [step(2), step(3)] });
    const none = await undoLast(async () => false);
    expect(none).toMatchObject({ done: 0, failed: 2 });
    expect(undoStore.get().entries.map((e) => e.text)).toEqual(["первое", "второе"]); // the same place, the same order
    const some = await undoLast(async (_c, a) => a.id === 2);
    expect(some).toMatchObject({ done: 1, failed: 1 });
    expect(undoStore.get().entries.map((e) => e.text)).toEqual(["первое"]);
  });

  it("the command `props.undo` that fails keeps the entry for another try", async () => {
    const app = createApp({ persist: false });
    pushUndo({ text: "цвет", steps: [{ command: "no.such.command", args: {} }] });
    expect((await app.registry.execute("props.undo")).ok).toBe(false);
    expect(lastUndo()?.text).toBe("цвет");
    expect(app.store.get().undoDepth).toBe(1);
  });
});

describe("an object changed in part", () => {
  const field: MultiField = {
    key: "color",
    label: "Цвет",
    type: "color",
    command: () => "x",
    kinds: ["lines"],
    steps: (_k, id, value) => [
      { command: "a", args: { id, value } },
      { command: "b", args: { id, value } },
    ],
  };
  const lookup = (exists: (id: number) => boolean = () => true): Lookup => ({
    exists: (_k, id) => exists(id),
    hidden: [],
    blockShown: () => false,
    layerColor: () => "#000000",
    wireframeColor: () => "#000000",
    rasterOpacity: () => 1,
  });

  it("the object whose first step ran and the second failed is in the undo, and the run stops", async () => {
    const sent: string[] = [];
    const r = await applyAll(async (c, a) => (sent.push(`${c}${a.id}`), !(c === "b" && a.id === 2)), field, "lines", [1, 2, 3], lookup(), "#fff");
    expect(sent).toEqual(["a1", "b1", "a2", "b2"]);
    expect([r.done, r.partial, r.total]).toEqual([1, 1, 3]);
    expect(r.undo.map((s) => `${s.command}${s.args.id}`)).toEqual(["a1", "b1", "a2", "b2"]); // both objects come back, all their steps
  });

  it("an object that failed on its first step is not in the undo; one deleted after its first step still is", async () => {
    const first = await applyAll(async (_c, a) => a.id !== 1, field, "lines", [1, 2], lookup(), "#fff");
    expect([first.done, first.partial, first.undo.length]).toEqual([0, 0, 0]);
    const live = new Set([1, 2]);
    const gone = await applyAll(
      async (c, a) => {
        if (c === "b" && a.id === 1) {
          live.delete(1);
          return false;
        }
        return true;
      },
      field,
      "lines",
      [1, 2],
      lookup((id) => live.has(id)),
      "#fff",
    );
    expect(gone.partial).toBe(1); // the first object was changed in part, then it was deleted: it is brought back
    expect(gone.done).toBe(1);
    expect(gone.undo.map((s) => `${s.command}${s.args.id}`)).toContain("a1");
  });
});

describe("no undo while the panel applies a change", () => {
  beforeEach(() => clearUndo());

  it("the busy mark is counted and ended once", () => {
    expect(isUndoBusy()).toBe(false);
    const a = beginUndoBusy();
    const b = beginUndoBusy();
    a();
    a(); // the second end of the same run changes nothing
    expect(isUndoBusy()).toBe(true);
    b();
    expect(isUndoBusy()).toBe(false);
  });

  it("`undoLast` and the command wait; the stack stays whole", async () => {
    const app = createApp({ persist: false });
    pushUndo({ text: "тема", steps: [{ command: "theme.set", args: { theme: "light" } }] });
    const end = beginUndoBusy();
    expect(await undoLast(async () => true)).toBeNull();
    const r = await app.registry.execute("props.undo");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("ещё применяется");
    expect(undoStore.get().entries).toHaveLength(1);
    end();
    expect((await app.registry.execute("props.undo")).ok).toBe(true);
  });

  it("a second undo during a running undo does not take the entry before", async () => {
    pushUndo({ text: "первое", steps: [{ command: "x", args: {} }] });
    pushUndo({ text: "второе", steps: [{ command: "x", args: {} }] });
    let release: () => void = () => undefined;
    const gate = new Promise<void>((r) => (release = r));
    const first = undoLast(async () => (await gate, true));
    expect(await undoLast(async () => true)).toBeNull();
    release();
    expect((await first)?.text).toBe("Отменено: второе");
    expect(undoStore.get().entries.map((e) => e.text)).toEqual(["первое"]);
  });
});

describe("Ctrl+Z is the key of the command `props.undo`", () => {
  const press = (win: EventTarget, patch: Record<string, unknown> = {}, target: Record<string, unknown> = { tagName: "DIV", closest: () => null }) => {
    const e = new Event("keydown", { cancelable: true });
    const props = { key: "z", code: "KeyZ", ctrlKey: true, metaKey: false, shiftKey: false, altKey: false, repeat: false, target, ...patch };
    for (const [k, v] of Object.entries(props)) Object.defineProperty(e, k, { value: v, configurable: true });
    win.dispatchEvent(e);
    return e;
  };
  const settle = () => new Promise((r) => setTimeout(r, 0));
  const push = () => pushUndo({ text: "тема", steps: [{ command: "theme.set", args: { theme: "light" } }] });

  it("the default key is Ctrl+Z, from the key map of the ribbon", () => {
    const app = createApp({ persist: false });
    expect(undoShortcut(app)).toBe("ctrl+z");
  });

  it("works on the Russian layout (the place of the key), not with Shift", async () => {
    const win = new EventTarget();
    vi.stubGlobal("window", win);
    const app = createApp({ persist: false });
    const detach = attachUndoKey(app);
    push();
    expect(press(win, { key: "я", code: "KeyZ", shiftKey: true }).defaultPrevented).toBe(false); // Ctrl+Shift+Z: not ours
    expect(lastUndo()).not.toBeNull();
    expect(press(win, { key: "я" }).defaultPrevented).toBe(true);
    await settle();
    expect(app.store.get().workspace.theme).toBe("light");
    detach();
  });

  it("the key moved by the user: the new key undoes, Ctrl+Z does not", async () => {
    const win = new EventTarget();
    vi.stubGlobal("window", win);
    const app = createApp({ persist: false });
    const detach = attachUndoKey(app);
    app.store.set({ shortcuts: { 'props.undo:{}': "ctrl+u" } });
    expect(undoShortcut(app)).toBe("ctrl+u");
    await app.registry.execute("theme.set", { theme: "dark" });
    push();
    expect(press(win).defaultPrevented).toBe(false);
    expect(lastUndo()).not.toBeNull();
    expect(press(win, { key: "u", code: "KeyU" }).defaultPrevented).toBe(true);
    await settle();
    expect(app.store.get().workspace.theme).toBe("light");
    // a key taken away: no key at all
    app.store.set({ shortcuts: { 'props.undo:{}': "" } });
    push();
    expect(press(win).defaultPrevented).toBe(false);
    expect(press(win, { key: "u", code: "KeyU" }).defaultPrevented).toBe(false);
    detach();
  });

  it("the key does nothing while the panel applies a change", async () => {
    const win = new EventTarget();
    vi.stubGlobal("window", win);
    const app = createApp({ persist: false });
    const detach = attachUndoKey(app);
    await app.registry.execute("theme.set", { theme: "dark" });
    push();
    const end = beginUndoBusy();
    expect(press(win).defaultPrevented).toBe(false);
    await settle();
    expect(app.store.get().workspace.theme).toBe("dark");
    expect(undoStore.get().entries).toHaveLength(1);
    end();
    expect(press(win).defaultPrevented).toBe(true);
    await settle();
    expect(app.store.get().workspace.theme).toBe("light");
    detach();
  });
});
