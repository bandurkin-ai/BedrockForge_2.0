import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { decodeBfa1 } from "../src/viz/format/bfa1";
import { createApp, type App } from "../src/shell/app";
import { windowStore } from "../src/shell/windows";
import { answerChoice, choiceStore } from "../src/modules/lines/choice";
import { editedLayer, editStore, layerShown } from "../src/modules/lines/editState";
import { finishEditor } from "../src/modules/lines/editor";
import { sectionStore } from "../src/modules/section/state";
import { DRAW_OFF, startDraw } from "../src/viz/section/draw";
import { closeApp, openApp, type server } from "./linesHarness";

let core: ReturnType<typeof server>;

async function open(): Promise<App> {
  const r = await openApp();
  core = r.core;
  return r.app;
}

beforeEach(() => {
  windowStore.set({ windows: [] });
});

afterEach(async () => {
  const s = editStore.get().session;
  if (s) {
    // leave without saving: the question of unsaved changes is answered «Не сохранять»
    const done = finishEditor(createApp({ persist: false }));
    if (s.dirty) {
      await vi.waitFor(() => expect(choiceStore.get().pending.length).toBe(1));
      answerChoice(choiceStore.get().pending[0].id, "discard");
    }
    await done;
  }
  closeApp();
});

describe("the string editor in the application", () => {
  it("saves a body with the vertices of the changed lines only; the layer is hidden while edited and read again after the save", async () => {
    const app = await open();
    const r = await app.registry.execute("lines.edit", { layer_id: 7 });
    expect(r.ok, r.result).toBe(true);
    expect(r.result).toMatch(/Правка слоя «Контуры»: версия 3/);
    expect(app.store.get().editing).toBe(true);
    expect(editedLayer()).toBe(7);
    expect(layerShown(7, new Set(), editedLayer())).toBe(false);
    expect(layerShown(9, new Set(), editedLayer())).toBe(true);
    const s = editStore.get().session!;
    expect(s.zLevel).toBe(6); // the middle of the heights of the layer
    s.setSelection({ lines: [s.layer.lines[1].id], vertices: [] });
    expect((await app.registry.execute("lines.modify", { action: "translate", dx: 1.5 })).ok).toBe(true);
    expect(s.dirty).toBe(true);
    const saved = await app.registry.execute("lines.save", { note: "сдвиг" });
    expect(saved).toEqual({ ok: true, result: "Сохранено: слой «Контуры», версия 4" });
    const put = core.calls.find((c) => c.method === "PUT")!;
    const doc = decodeBfa1<{ base_version: number; note: string }>(put.body as ArrayBuffer);
    expect(doc.header.base_version).toBe(3);
    expect(doc.header.note).toBe("сдвиг");
    expect(Array.from(doc.arrays.touched as Uint8Array)).toEqual([0, 1, 0]);
    expect(Array.from(doc.arrays.src as Int32Array)).toEqual([0, 1, 2]);
    expect(Array.from(doc.arrays.xyz as Float64Array)).toEqual([101.5, 0, 5, 111.5, 0, 5]);
    // read again after the save (`saved(reloaded)`): the base is the new version, nothing is dirty
    const gets = core.calls.filter((c) => c.method === "GET" && c.path === "/lines/layers/7/geometry");
    expect(gets.length).toBe(2);
    expect(s.baseVersion).toBe(4);
    expect(s.dirty).toBe(false);
    expect((await app.registry.execute("lines.save")).result).toBe("Изменений нет");
    expect((await app.registry.execute("lines.finish")).result).toBe("Правка завершена");
    expect(editedLayer()).toBeNull();
    expect(app.store.get().editing).toBe(false);
  });

  it("a 409 opens an own window with three actions; «Отмена» writes nothing", async () => {
    const app = await open();
    await app.registry.execute("lines.edit", { layer_id: 7 });
    const s = editStore.get().session!;
    s.setSelection({ lines: [s.layer.lines[0].id], vertices: [] });
    await app.registry.execute("lines.modify", { action: "reverse" });
    core.state.conflict = { code: "layer_changed", message: "Слой сохранён после начала правки (версия 5, Иванов). Ничего не записано.", hint: "Перечитайте слой.", layer_id: 7, version: 5, base_version: 3, author: "Иванов", at: "2026-11-19T10:15:00" };
    const run = app.registry.execute("lines.save");
    await vi.waitFor(() => expect(choiceStore.get().pending.length).toBe(1));
    const q = choiceStore.get().pending[0];
    expect(q.actions.map((a) => a.label)).toEqual(["Сохранить как новый слой", "Перечитать слой", "Отмена"]);
    expect(q.message).toMatch(/версия 5, Иванов, 19\.11\.2026.*Ничего не записано/);
    expect(windowStore.get().windows.map((w) => w.kind)).toEqual(["lines-choice"]);
    answerChoice(q.id, "cancel");
    const r = await run;
    expect(r.ok).toBe(false);
    expect(r.result).toMatch(/Ничего не записано.*Сохраните правку как новый слой или перечитайте слой/);
    expect(windowStore.get().windows).toEqual([]);
    expect(s.dirty).toBe(true); // the working copy stays
    expect(core.calls.filter((c) => c.method === "PUT").length).toBe(1);
  });

  it("lines.add_line without a session is refused with a hint; a cloud is not editable", async () => {
    const app = await open();
    const r = await app.registry.execute("lines.add_line", { points: "[[0,0,0],[100,0,0]]" }, "claude");
    expect(r.ok).toBe(false);
    expect(r.result).toMatch(/Редактор стрингов не открыт\. Откройте слой на правку/);
    expect(app.registry.reason("lines.undo")).toMatch(/Откройте слой на правку/);
    const c = await app.registry.execute("lines.edit", { layer_id: 8 });
    expect(c.ok).toBe(false);
    expect(c.result).toMatch(/облако точек/);
    await app.registry.execute("lines.edit", { layer_id: 7 });
    const added = await app.registry.execute("lines.add_line", { points: "[[0,0],[10,0],[10,10],[0,10]]", closed: true }, "claude");
    expect(added.ok, added.result).toBe(true);
    expect(added.result).toMatch(/площадь 100 м²/);
    expect(editStore.get().session!.layer.lines.at(-1)!.points[0]).toEqual([0, 0, 6]); // on the Z of the panel
  });

  it("a tool of the editor called while a section line is being drawn calls the line off (Д-47 А8)", async () => {
    const app = await open();
    await app.registry.execute("lines.edit", { layer_id: 7 });
    sectionStore.set({ draw: startDraw() });
    const r = await app.registry.execute("lines.tool", { tool: "line" });
    expect(r.ok, r.result).toBe(true);
    expect(sectionStore.get().draw).toEqual(DRAW_OFF);
    expect(editStore.get().session!.tool).toBe("line");
  });
});
