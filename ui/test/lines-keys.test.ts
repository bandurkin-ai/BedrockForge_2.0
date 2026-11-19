/** Э9, подшаг 9.38 (ревью интерфейса, finding 5; правило «команда = запись в реестре»): Delete, Ctrl+A, Ctrl+Z,
 * Ctrl+Y of the string editor run commands of the registry; Esc, Enter and Backspace stay in the session. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { createApp } from "../src/shell/app";
import { answerChoice, choiceStore } from "../src/modules/lines/choice";
import { editStore } from "../src/modules/lines/editState";
import { finishEditor } from "../src/modules/lines/editor";
import { keyRun } from "../src/modules/lines/editKeys";
import { closeApp, openApp } from "./linesHarness";

/** A window enough for the keys of the editor (the tests run in Node). */
function fakeWindow(): EventTarget {
  const w = new EventTarget();
  vi.stubGlobal("window", w);
  return w;
}

function press(w: EventTarget, key: string, ctrl = false): void {
  const e = Object.assign(new Event("keydown", { cancelable: true }), { key, ctrlKey: ctrl, metaKey: false, shiftKey: false, altKey: false, repeat: false });
  w.dispatchEvent(e);
}

afterEach(async () => {
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
});

describe("keys of the string editor go through the registry", () => {
  it("names the command of each key", () => {
    expect(keyRun("lines.undo")).toMatchObject({ id: "lines.undo", repeat: true });
    expect(keyRun("lines.redo")).toMatchObject({ id: "lines.redo" });
    expect(keyRun("lines.select_all")).toMatchObject({ id: "lines.select_all", args: {} });
    expect(keyRun("lines.delete")).toEqual({ id: "lines.modify", args: { action: "delete" }, repeat: false });
    expect(keyRun("lines.escape")).toBeNull();
    expect(keyRun("lines.finish")).toBeNull();
  });

  it("Ctrl+A, Delete, Ctrl+Z, Ctrl+Y run lines.select_all, lines.modify {delete}, lines.undo, lines.redo", async () => {
    const w = fakeWindow();
    const { app } = await openApp();
    expect((await app.registry.execute("lines.edit", { layer_id: 7 })).ok).toBe(true);
    const s = editStore.get().session!;
    const run = vi.spyOn(app.registry, "execute");
    const step = async (key: string, ctrl = false) => {
      press(w, key, ctrl);
      await vi.waitFor(() => expect(run.mock.results.length).toBeGreaterThan(0));
      await run.mock.results.at(-1)!.value;
    };
    await step("a", true);
    expect(run).toHaveBeenLastCalledWith("lines.select_all", {});
    expect(s.selection.lines).toHaveLength(3);
    await step("Delete");
    expect(run).toHaveBeenLastCalledWith("lines.modify", { action: "delete" });
    expect(s.layer.lines).toHaveLength(0);
    await step("z", true);
    expect(run).toHaveBeenLastCalledWith("lines.undo", {});
    expect(s.layer.lines).toHaveLength(3);
    await step("y", true);
    expect(run).toHaveBeenLastCalledWith("lines.redo", {});
    expect(s.layer.lines).toHaveLength(0);
    await step("z", true);
    // a refusal of the registry is said in the panel
    await step("y", true);
    await step("y", true);
    expect(s.message).toBe("Возвращать нечего");
    // Esc stays in the session: no command of the registry
    const calls = run.mock.calls.length;
    press(w, "Escape");
    expect(run.mock.calls.length).toBe(calls);
  });
});
