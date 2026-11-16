import { describe, expect, it } from "vitest";
import { commandTools, EMPTY_CONTEXT, validateArgs, type Command } from "../src/shell/commands";
import { eventShortcut, formatShortcut } from "../src/shell/keys";
import { CommandRegistry, DECLINED, LOG_LIMIT } from "../src/shell/registry";

const view: Command = {
  id: "scene.view",
  title: "Вид",
  description: "Повернуть вид",
  params: { view: { type: "string", enum: ["top", "front"] } },
  required: ["view"],
  run: (a) => `Вид: ${a.view}`,
};
const del: Command = { id: "project.delete", title: "Удалить проект", description: "…", confirm: true, run: () => "Удалён" };
const ui: Command = { id: "palette.open", title: "Палитра", description: "…", uiOnly: true, silent: true, run: () => undefined };
const later: Command = { id: "audit.run", title: "Аудит", description: "…", stage: "Э5" };
const needsSet: Command = { id: "chart.qq", title: "Q-Q", description: "…", unavailable: (c) => (c.hasSet ? null : "Нет активного набора скважин"), run: () => "ok" };

function registry(confirm = false) {
  const r = new CommandRegistry();
  r.register([view, del, ui, later, needsSet]);
  r.hooks = { context: () => EMPTY_CONTEXT, confirm: async () => confirm };
  return r;
}

describe("command registry (ТЗ 7.2 acceptance)", () => {
  it("a command added only to the registry becomes a Claude tool; uiOnly is hidden", () => {
    const names = commandTools(registry().list()).map((t) => t.name);
    expect(names).toContain("ui_scene_view");
    expect(names).not.toContain("ui_palette_open");
  });

  it("wrong arguments give a text, not an exception", async () => {
    const r = registry();
    expect(await r.execute("scene.view", { view: "left" })).toEqual({ ok: false, result: "Параметр «view»: допустимые значения — top, front" });
    expect(await r.execute("scene.view", {})).toMatchObject({ ok: false, result: "Не задан параметр «view»" });
    expect(validateArgs(view, { extra: 1 })).toBe("Неизвестный параметр «extra»");
  });

  it("confirm from Claude runs only after approval", async () => {
    expect(await registry(false).execute("project.delete", {}, "claude")).toEqual({ ok: false, result: DECLINED });
    expect(await registry(true).execute("project.delete", {}, "claude")).toEqual({ ok: true, result: "Удалён" });
    expect(await registry(false).execute("project.delete", {}, "user")).toEqual({ ok: true, result: "Удалён" });
  });

  it("silent commands are not logged; others are, newest first, max 200", async () => {
    const r = registry();
    await r.execute("palette.open");
    expect(r.entries()).toHaveLength(0);
    for (let i = 0; i < LOG_LIMIT + 5; i++) await r.execute("scene.view", { view: "top" });
    expect(r.entries()).toHaveLength(LOG_LIMIT);
    expect(r.entries()[0].id).toBeGreaterThan(r.entries()[1].id);
  });

  it("unavailable commands report the reason", async () => {
    const r = registry();
    expect((await r.execute("audit.run")).result).toBe("Функция появится на этапе Э5");
    expect((await r.execute("chart.qq")).result).toBe("Нет активного набора скважин");
    expect(await r.execute("palette.open", {}, "claude")).toMatchObject({ ok: false });
  });

  it("errors thrown by handlers become results", async () => {
    const r = registry();
    r.bind("chart.qq", () => {
      throw new Error("Сбой");
    }, () => null);
    expect(await r.execute("chart.qq")).toEqual({ ok: false, result: "Сбой" });
  });
});

describe("hotkeys", () => {
  const key = (code: string, k: string, mods: Partial<{ ctrlKey: boolean; altKey: boolean; shiftKey: boolean }> = {}) =>
    eventShortcut({ code, key: k, ctrlKey: false, altKey: false, shiftKey: false, ...mods });

  it("letters work in the Russian layout by physical key", () => {
    expect(key("KeyT", "е")).toBe("t");
    expect(key("KeyK", "л", { ctrlKey: true })).toBe("ctrl+k");
    expect(key("Backquote", "ё", { ctrlKey: true })).toBe("ctrl+`");
    expect(key("F1", "F1", { ctrlKey: true })).toBe("ctrl+f1");
    expect(key("BracketLeft", "х")).toBe("[");
  });

  it("formats for tooltips", () => {
    expect(formatShortcut("ctrl+`")).toBe("Ctrl+Ё");
    expect(formatShortcut("escape")).toBe("Esc");
  });
});
