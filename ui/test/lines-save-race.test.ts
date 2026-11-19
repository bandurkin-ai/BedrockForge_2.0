/** Э9, подшаг 9.38 (ревью интерфейса): a save does not block the edits — an edit, an undo or a new line made while
 * `PUT …/geometry` is on its way stays and stays unsaved; a reading of the layer that fails after a save that went
 * through is tried again, and the next save reads first instead of sending over the old base. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { decodeBfa1 } from "../src/viz/format/bfa1";
import { createApp, type App } from "../src/shell/app";
import { windowStore } from "../src/shell/windows";
import { answerChoice, choiceStore } from "../src/modules/lines/choice";
import { editStore } from "../src/modules/lines/editState";
import { finishEditor, rereadTiming } from "../src/modules/lines/editor";
import { projectStore, type ProjectInfo } from "../src/modules/projects/state";
import { closeApp, openApp, type server } from "./linesHarness";

let core: ReturnType<typeof server>;
/** PUT waits for `release()`; GET of the layer fails while `failGets` > 0. */
let gate: { wait: Promise<void>; release: () => void; held: number } | null = null;
let failGets = 0;

function hold(): void {
  let release = () => {};
  const wait = new Promise<void>((r) => (release = r));
  gate = { wait, release, held: 0 };
}

async function open(): Promise<App> {
  const r = await openApp();
  core = r.core;
  const inner = core.fetch;
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const geometry = new URL(url).pathname.endsWith("/lines/layers/7/geometry");
    if (geometry && method === "PUT" && gate) {
      gate.held++;
      await gate.wait;
    }
    if (geometry && method === "GET" && failGets > 0) {
      failGets--;
      inner(url, init); // counted as a call
      throw new TypeError("Failed to fetch");
    }
    return inner(url, init);
  });
  const app = r.app;
  expect((await app.registry.execute("lines.edit", { layer_id: 7 })).ok).toBe(true);
  return app;
}

const puts = () => core.calls.filter((c) => c.method === "PUT");
const gets = () => core.calls.filter((c) => c.method === "GET" && c.path === "/lines/layers/7/geometry");
const body = (i: number) => decodeBfa1<{ base_version: number }>(puts()[i].body as ArrayBuffer);
const session = () => editStore.get().session!;

/** Ctrl+S: the save, held at its PUT (the promise in a box: an async function would wait for it). */
async function startSave(app: App) {
  hold();
  const run = app.registry.execute("lines.save");
  await vi.waitFor(() => expect(gate!.held).toBe(1));
  return { run };
}

beforeEach(() => {
  windowStore.set({ windows: [] });
  rereadTiming.pauses = [0, 0];
  gate = null;
  failGets = 0;
});

afterEach(async () => {
  gate?.release();
  gate = null;
  failGets = 0;
  const s = editStore.get().session;
  if (s) {
    const done = finishEditor(createApp({ persist: false }));
    if (s.dirty) {
      await vi.waitFor(() => expect(choiceStore.get().pending.length).toBe(1));
      answerChoice(choiceStore.get().pending[0].id, "discard");
    }
    await done;
  }
  closeApp();
  rereadTiming.pauses = [300, 1000];
});

describe("an edit while the layer is being saved (9.38, finding 1)", () => {
  it("a vertex moved during the PUT stays changed: the flag of changes stays, the next save sends that line", async () => {
    const app = await open();
    const s = session();
    s.setSelection({ lines: [s.layer.lines[1].id], vertices: [] });
    await app.registry.execute("lines.modify", { action: "translate", dx: 1 });
    const { run } = await startSave(app);
    // during the PUT: line 3 is moved (it was not in the body as changed)
    s.setSelection({ lines: [s.layer.lines[2].id], vertices: [] });
    await app.registry.execute("lines.modify", { action: "translate", dy: 2 });
    gate!.release();
    const r = await run;
    expect(r.ok, r.result).toBe(true);
    expect(r.result).toMatch(/версия 4\. Правки, сделанные во время сохранения, ещё не записаны/);
    expect(s.baseVersion).toBe(4);
    expect(s.dirty).toBe(true);
    expect(s.summary()).toMatchObject({ touched: 1, created: 0, removed: 0 });
    gate = null;
    expect((await app.registry.execute("lines.save")).ok).toBe(true);
    const second = body(1);
    expect(second.header.base_version).toBe(4);
    expect(Array.from(second.arrays.touched as Uint8Array)).toEqual([0, 0, 1]);
    expect(Array.from(second.arrays.xyz as Float64Array)).toEqual([200, 2, 1, 210, 7, 1, 220, 2, 1]);
    expect(s.dirty).toBe(false);
  });

  it("Ctrl+Z during the PUT: the present is unsaved; redo returns the saved state, clean", async () => {
    const app = await open();
    const s = session();
    s.setSelection({ lines: [s.layer.lines[1].id], vertices: [] });
    await app.registry.execute("lines.modify", { action: "translate", dx: 1 });
    const { run } = await startSave(app);
    expect((await app.registry.execute("lines.undo")).ok).toBe(true);
    gate!.release();
    expect((await run).ok).toBe(true);
    expect(s.layer.lines[1].points[0]).toEqual([100, 0, 5]);
    expect(s.dirty).toBe(true); // the store has the moved line, the working copy has it back in place
    expect(s.summary().touched).toBe(1);
    await app.registry.execute("lines.redo");
    expect(s.layer.lines[1].points[0]).toEqual([101, 0, 5]);
    expect(s.dirty).toBe(false);
  });

  it("a line added during the PUT is not lost: the history stays, the line goes as new in the next save", async () => {
    const app = await open();
    const s = session();
    s.setSelection({ lines: [s.layer.lines[0].id], vertices: [] });
    await app.registry.execute("lines.modify", { action: "reverse" });
    const { run } = await startSave(app);
    expect((await app.registry.execute("lines.add_line", { points: "[[300,0,1],[310,0,1]]" })).ok).toBe(true);
    gate!.release();
    expect((await run).ok).toBe(true);
    expect(s.layer.lines).toHaveLength(4);
    expect(s.dirty).toBe(true);
    expect(s.canUndo).toBe(true);
    expect(s.summary()).toMatchObject({ lines: 4, touched: 1, created: 1 });
    gate = null;
    await app.registry.execute("lines.save");
    expect(Array.from(body(1).arrays.src as Int32Array)).toEqual([0, 1, 2, -1]);
    expect(Array.from(body(1).arrays.touched as Uint8Array)).toEqual([0, 0, 0, 1]);
  });
});

describe("the layer not read after a save (9.38, finding 3)", () => {
  it("a failed reading is tried again: two failures, the third reading takes", async () => {
    const app = await open();
    const s = session();
    s.setSelection({ lines: [s.layer.lines[1].id], vertices: [] });
    await app.registry.execute("lines.modify", { action: "translate", dx: 1 });
    const before = gets().length;
    failGets = 2;
    const r = await app.registry.execute("lines.save");
    expect(r.result).toBe("Сохранено: слой «Контуры», версия 4");
    expect(gets().length - before).toBe(3);
    expect(s.dirty).toBe(false);
    expect(s.unsynced).toBeNull();
  });

  it("all readings fail: a clear message, and the next save reads first instead of a PUT over the old base", async () => {
    const app = await open();
    const s = session();
    s.setSelection({ lines: [s.layer.lines[1].id], vertices: [] });
    await app.registry.execute("lines.modify", { action: "translate", dx: 1 });
    failGets = 3;
    const r = await app.registry.execute("lines.save");
    expect(r.ok).toBe(true);
    expect(r.result).toMatch(/версия 4\. Слой не удалось перечитать — перед следующим сохранением он будет перечитан автоматически/);
    expect(s.baseVersion).toBe(3);
    expect(s.unsynced).not.toBeNull();
    // the core is still down: the next save refuses with a hint, no PUT is sent
    failGets = 3;
    const again = await app.registry.execute("lines.save");
    expect(again.ok).toBe(false);
    expect(again.result).toMatch(/сохранён, но после сохранения его не удалось перечитать.*Ctrl\+S/);
    expect(puts()).toHaveLength(1);
    // the core answers: a new edit is saved over version 4, not 3 (no 409 on its own save)
    s.setSelection({ lines: [s.layer.lines[2].id], vertices: [] });
    await app.registry.execute("lines.modify", { action: "translate", dz: 1 });
    const ok = await app.registry.execute("lines.save");
    expect(ok.ok, ok.result).toBe(true);
    expect(puts()).toHaveLength(2);
    expect(body(1).header.base_version).toBe(4);
    expect(Array.from(body(1).arrays.touched as Uint8Array)).toEqual([0, 0, 1]);
    expect(s.baseVersion).toBe(5);
    expect(s.dirty).toBe(false);
  });

  it("nothing edited after the failed reading: the next save only reads", async () => {
    const app = await open();
    const s = session();
    s.setSelection({ lines: [s.layer.lines[1].id], vertices: [] });
    await app.registry.execute("lines.modify", { action: "translate", dx: 1 });
    failGets = 3;
    await app.registry.execute("lines.save");
    expect(s.dirty).toBe(true);
    const r = await app.registry.execute("lines.save");
    expect(r.result).toBe("Слой «Контуры» перечитан: версия 4, других изменений нет");
    expect(puts()).toHaveLength(1);
    expect(s.dirty).toBe(false);
  });
});

describe("the editor ends (9.38, finding 7)", () => {
  it("another project ends the session: its hover and its pending probe of the core go", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      await open();
      const s = session();
      const probe = vi.fn(() => new Promise<null>(() => {}));
      s.probe = probe;
      s.view = { transform: () => ({ matrix: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], width: 100, height: 100 }), ray: () => ({ origin: [0, 0, 100], dir: [0, 0, -1] }) };
      s.snap = { ...s.snap, kinds: [...s.snap.kinds, "wf-vertex"] };
      s.pointer({ type: "move", x: 50, y: 50, clientX: 50, clientY: 50, button: -1, shift: false, ctrl: false });
      expect(s.hover).not.toBeNull();
      projectStore.set({ current: { uid: "p2" } as ProjectInfo });
      expect(editStore.get().session).toBeNull();
      expect(s.hover).toBeNull();
      vi.advanceTimersByTime(100);
      expect(probe).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});
