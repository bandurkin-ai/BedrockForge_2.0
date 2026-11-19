import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { encodeBfa1 } from "../src/edit/bfa1";
import { decodeBfa1 } from "../src/viz/format/bfa1";
import { createApp, type App } from "../src/shell/app";
import { setCoreTarget } from "../src/shell/api";
import { windowStore } from "../src/shell/windows";
import { projectStore, type ProjectInfo } from "../src/modules/projects/state";
import { loadSceneObjects } from "../src/modules/scene/objects";
import { answerChoice, choiceStore } from "../src/modules/lines/choice";
import { editedLayer, editStore, layerShown } from "../src/modules/lines/editState";
import { finishEditor } from "../src/modules/lines/editor";

/** BFA1 `linegeom` of a layer: lines given by their vertices; the closed ones repeat the first vertex. */
function linegeom(layerId: number, version: number, lines: { pts: number[][]; closed?: boolean }[]): ArrayBuffer {
  const xyz: number[] = [];
  const offsets = [0];
  const closed: number[] = [];
  for (const l of lines) {
    const pts = l.closed ? [...l.pts, l.pts[0]] : l.pts;
    for (const p of pts) xyz.push(...p);
    offsets.push(offsets[offsets.length - 1] + pts.length);
    closed.push(l.closed ? 1 : 0);
  }
  return encodeBfa1({ kind: "linegeom", layer_id: layerId, version, lines: lines.length, points: xyz.length / 3, ties: [] }, [
    ["xyz", "<f8", Float64Array.from(xyz)],
    ["offsets", "<u4", Uint32Array.from(offsets)],
    ["closed", "<u1", Uint8Array.from(closed)],
    ["lid", "<u4", Uint32Array.from(lines.map((_, i) => i))],
  ]);
}

const LINES = [
  { pts: [[0, 0, 10], [10, 0, 10], [10, 10, 10], [0, 10, 10]], closed: true },
  { pts: [[100, 0, 5], [110, 0, 5]] },
  { pts: [[200, 0, 1], [210, 5, 1], [220, 0, 1]] },
];

const layer = (id: number, name: string, extra: Record<string, unknown> = {}) => ({
  id, file_id: 1, name, version: 1, color: "#ff8800", points_only: false, line_count: 3, point_count: 9, length: 1, closed_count: 1, closed: "some", flat: false, extent: [0, 0, 1, 220, 10, 10], attr_schema: [], ...extra,
});

function server() {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const state = { version: 3, conflict: null as null | Record<string, unknown> };
  const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url).pathname.replace("/api/v1", "");
    const method = init?.method ?? "GET";
    calls.push({ method, path, body: init?.body });
    if (path === "/lines/files") return json({ files: [{ id: 1, name: "Файл", folder: null, version: 1, format: "str", label: null, filename: null, crs_name: null, warnings: [], layers: [layer(7, "Контуры"), layer(8, "Облако", { cloud: { classes: [] } })] }] });
    if (path === "/rasters" || path === "/wireframes") return json(path === "/rasters" ? { rasters: [] } : { wireframes: [] });
    if (path === "/lines/layers/7/geometry" && method === "GET") return new Response(linegeom(7, state.version, LINES));
    if (path === "/lines/layers/7/geometry" && method === "PUT") {
      if (state.conflict) return json(state.conflict, 409);
      state.version++;
      return json({ layer: layer(7, "Контуры"), version: state.version });
    }
    return json({ code: "not_found", message: "Нет", hint: "" }, 404);
  });
  return { calls, state, fetch };
}

let core: ReturnType<typeof server>;

async function open(): Promise<App> {
  const app = createApp({ persist: false });
  core = server();
  vi.stubGlobal("fetch", core.fetch);
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  projectStore.set({ current: { uid: "p1" } as ProjectInfo });
  app.store.set({ projectName: "Проект" });
  await loadSceneObjects();
  return app;
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
  vi.unstubAllGlobals();
  setCoreTarget(null);
  projectStore.set({ current: null });
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
});
