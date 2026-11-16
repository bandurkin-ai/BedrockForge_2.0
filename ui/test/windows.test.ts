import { describe, expect, it } from "vitest";
import { acceptText, askConfirm, askText, dialogStore } from "../src/shell/dialogs";
import { clampToViewport, closeWindow, focusWindow, openWindow, windowStore } from "../src/shell/windows";

describe("own dialogs (ТЗ 7.0.11)", () => {
  it("queue: one at a time, resolves in order", async () => {
    const a = askConfirm("A?");
    const b = askText({ title: "Имя" });
    const q = dialogStore.get().queue;
    expect(q.map((d) => d.kind)).toEqual(["confirm", "text"]);
    (q[0].resolve as (v: boolean) => void)(true);
    (q[1].resolve as (v: string | null) => void)(null);
    expect(await a).toBe(true);
    expect(await b).toBeNull();
  });

  it("text is trimmed; empty only with allowEmpty", () => {
    expect(acceptText("  Профиль 1 ")).toBe("Профиль 1");
    expect(acceptText("   ")).toBeNull();
    expect(acceptText("   ", true)).toBe("");
  });
});

describe("floating windows (ТЗ 7.0.12)", () => {
  it("the title bar never leaves the app window", () => {
    expect(clampToViewport({ x: -2000, y: -50, w: 400, h: 300 }, 1024, 700)).toMatchObject({ x: -340, y: 0 });
    expect(clampToViewport({ x: 5000, y: 5000, w: 400, h: 300 }, 1024, 700)).toMatchObject({ x: 964, y: 670 });
  });

  it("a new window opens on top; the clicked window goes on top; reopening focuses", () => {
    const a = openWindow("histogram", "Гистограмма · Набор 1");
    const b = openWindow("qq", "Q-Q · Набор 1");
    const z = () => Object.fromEntries(windowStore.get().windows.map((w) => [w.id, w.z]));
    expect(z()[b]).toBeGreaterThan(z()[a]);
    focusWindow(a);
    expect(z()[a]).toBeGreaterThan(z()[b]);
    expect(openWindow("qq", "Q-Q · Набор 1")).toBe(b);
    expect(z()[b]).toBeGreaterThan(z()[a]);
    closeWindow(a);
    closeWindow(b);
    expect(windowStore.get().windows).toHaveLength(0);
  });
});
