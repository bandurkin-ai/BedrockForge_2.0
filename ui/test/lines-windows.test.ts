import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { closeDialog, dialogStore } from "../src/shell/dialogs";
import { windowStore } from "../src/shell/windows";
import { answerChoice, choiceStore } from "../src/modules/lines/choice";
import { editStore } from "../src/modules/lines/editState";
import { MODIFY, parseNumber, readFields } from "../src/modules/lines/modify";
import { moveTargets } from "../src/modules/lines/MoveLinesWindow";
import { layerMenu } from "../src/modules/lines/tree";
import { change, versionLines } from "../src/modules/lines/versions";
import type { Journal } from "../src/modules/lines/editApi";
import { closeApp, layer, openApp } from "./linesHarness";

beforeEach(() => windowStore.set({ windows: [] }));
afterEach(() => {
  editStore.set({ session: null });
  closeApp();
});

describe("windows of the string editor", () => {
  it("numbers with a comma or a dot; the fields of «Правка выбранного» are checked", () => {
    expect(parseNumber("1,5")).toBe(1.5);
    expect(parseNumber(" -2.25 ")).toBe(-2.25);
    expect(parseNumber("1 000,5")).toBe(1000.5);
    expect(parseNumber("1e3")).toBe(1000);
    for (const bad of ["", "абв", "1,2,3", "1.2.3", "--1"]) expect(parseNumber(bad), bad).toBeNull();
    const vector = MODIFY.find((m) => m.id === "translate")!.fields!;
    expect(readFields(vector, { dx: "1,5", dz: "-0,25" })).toEqual({ values: { dx: 1.5, dy: 0, dz: -0.25 } });
    expect(readFields(vector, { dy: "x" })).toEqual({ error: "«ΔY, м»: введите число (десятичный знак — запятая или точка)" });
    const tol = MODIFY.find((m) => m.id === "simplify")!.fields!;
    expect(readFields(tol, {})).toEqual({ values: { tolerance: 0.5 } }); // the initial «0,5»
    expect(readFields(tol, { tolerance: "0" })).toEqual({ error: "«Допуск, м»: число должно быть больше 0" });
    const passes = MODIFY.find((m) => m.id === "smooth")!.fields!;
    expect(readFields(passes, { iterations: "1,5" })).toEqual({ error: "«Проходов»: нужно целое число" });
  });

  it("the journal: «было → стало» of lines and vertices, who, when, the action", () => {
    const j: Journal = {
      layer_id: 7,
      version: 3,
      versions: [
        { version: 3, action: "restore", note: "Возврат к версии 1", author: "Иванов", at: "2026-11-19T11:30:00", line_count: 12, point_count: 340, prev_line_count: 14, prev_point_count: 400, current: true },
        { version: 2, action: "save", note: "", author: null, at: "2026-11-19T10:00:00", line_count: 14, point_count: 400, prev_line_count: 12, prev_point_count: 340, current: false },
        { version: 1, action: "import", note: "", author: "Иванов", at: "2026-11-18T08:05:00", line_count: 12, point_count: 340, prev_line_count: 0, prev_point_count: 0, current: false },
      ],
    };
    const rows = versionLines(j);
    expect(rows.map((r) => [r.version, r.action, r.lines, r.vertices, r.who])).toEqual([
      [3, "Возврат", "14 → 12", "400 → 340", "Иванов"],
      [2, "Сохранение", "12 → 14", "340 → 400", "—"],
      [1, "Импорт", "12", "340", "Иванов"],
    ]);
    expect(rows[0].current).toBe(true);
    expect(rows[2].when).toMatch(/18\.11\.2026/);
    expect(change(5, 5)).toBe("5");
    expect(change(1200, 1500)).toBe("1 200 → 1 500");
  });

  it("a return to a version and leaving with unsaved changes are asked in own windows", async () => {
    const { app, core } = await openApp();
    await app.registry.execute("lines.edit", { layer_id: 7 });
    const s = editStore.get().session!;
    s.setSelection({ lines: [s.layer.lines[2].id], vertices: [] });
    await app.registry.execute("lines.modify", { action: "delete" });
    expect(s.dirty).toBe(true);
    // the return: the own dialog warns that the unsaved changes go
    const run = app.registry.execute("lines.restore", { layer_id: 7, version: 1 });
    await vi.waitFor(() => expect(dialogStore.get().queue.length).toBe(1));
    const d = dialogStore.get().queue[0];
    expect(d.kind).toBe("confirm");
    expect(d.kind === "confirm" && d.options.message).toMatch(/к версии 1\? Будет создана версия 4.*Несохранённые правки этого слоя будут потеряны/s);
    closeDialog(d.id);
    if (d.kind === "confirm") d.resolve(true);
    const r = await run;
    expect(r).toEqual({ ok: true, result: "Слой «Контуры» возвращён к версии 1: новая версия 4" });
    expect(core.calls.some((c) => c.method === "POST" && c.path === "/lines/layers/7/versions/1/restore")).toBe(true);
    const now = editStore.get().session!;
    expect(now.baseVersion).toBe(4); // read anew
    expect(now.dirty).toBe(false);
    // leaving with unsaved changes: the own window «Завершить правку»
    now.setSelection({ lines: [now.layer.lines[0].id], vertices: [] });
    await app.registry.execute("lines.modify", { action: "reverse" });
    const fin = app.registry.execute("lines.finish");
    await vi.waitFor(() => expect(choiceStore.get().pending.length).toBe(1));
    const q = choiceStore.get().pending[0];
    expect(q.title).toBe("Завершить правку");
    expect(q.actions.map((a) => a.label)).toEqual(["Сохранить", "Не сохранять", "Отмена"]);
    answerChoice(q.id, "cancel");
    expect((await fin).result).toBe("Правка продолжается");
    expect(editStore.get().session).toBe(now);
    const again = app.registry.execute("lines.finish");
    await vi.waitFor(() => expect(choiceStore.get().pending.length).toBe(1));
    answerChoice(choiceStore.get().pending[0].id, "discard");
    expect((await again).result).toBe("Правка завершена");
    expect(editStore.get().session).toBeNull();
  });

  it("an action with numbers and none given opens «Правка выбранного»; the targets of a move", async () => {
    const { app } = await openApp();
    await app.registry.execute("lines.edit", { layer_id: 7 });
    const s = editStore.get().session!;
    const r0 = await app.registry.execute("lines.modify", { action: "offset" });
    expect(r0.ok).toBe(false);
    expect(r0.result).toMatch(/Сначала выберите линию/);
    s.setSelection({ lines: [s.layer.lines[0].id], vertices: [] });
    const r = await app.registry.execute("lines.modify", { action: "offset" });
    expect(r).toEqual({ ok: true, result: "Открыто окно «Копия со смещением»" });
    expect(windowStore.get().windows[0]).toMatchObject({ kind: "lines-modify", props: { action: "offset" } });
    expect((await app.registry.execute("lines.modify", { action: "offset", distance: 2.5 })).ok).toBe(true);
    expect(s.layer.lines.length).toBe(4);
    expect(moveTargets([layer(7, "Контуры"), layer(8, "Облако", { cloud: {} }), layer(9, "Дороги"), layer(10, "Скважины")], 7, "дор")).toEqual([9]);
    const finish = app.registry.execute("lines.finish");
    await vi.waitFor(() => expect(choiceStore.get().pending.length).toBe(1));
    answerChoice(choiceStore.get().pending[0].id, "discard");
    await finish;
  });

  it("the menu of a layer offers «Редактировать» and «Версии…»; the layer under edit — «Завершить правку»", () => {
    const menu = layerMenu(layer(7, "Контуры"), true);
    expect(menu).toContainEqual({ label: "Редактировать", command: "lines.edit", args: { layer_id: 7 } });
    expect(menu).toContainEqual({ label: "Версии…", command: "lines.versions", args: { layer_id: 7 } });
    expect(layerMenu(layer(7, "Контуры"), true, true)).toContainEqual({ label: "Завершить правку", command: "lines.finish" });
    expect(layerMenu(layer(8, "Облако", { cloud: {} }) as never, true).some((a) => a.command === "lines.edit")).toBe(false);
  });

  it("no window.confirm, prompt, alert or open in the editor", () => {
    const roots = ["src/modules/lines", "src/viz/edit", "src/edit", "src/viz/core"];
    const files: string[] = [];
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const p = join(dir, name);
        if (statSync(p).isDirectory()) walk(p);
        else if (/\.tsx?$/.test(name)) files.push(p);
      }
    };
    roots.forEach((r) => walk(join(__dirname, "..", r)));
    expect(files.length).toBeGreaterThan(20);
    for (const f of files) {
      const text = readFileSync(f, "utf8");
      expect(text, f).not.toMatch(/\bwindow\.(confirm|prompt|alert|open)\s*\(|(^|[^.\w])(confirm|prompt|alert)\s*\(/m);
    }
  });
});
