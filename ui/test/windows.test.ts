import { describe, expect, it } from "vitest";
import { acceptText, askConfirm, askText, dialogStore } from "../src/shell/dialogs";
import { Store } from "../src/shell/store";
import { clampToViewport, closeWindow, focusWindow, initialRect, openWindow, rememberWindowRect, setWindowMemory, windowStore } from "../src/shell/windows";
import { settingsMemory } from "../src/shell/WindowLayer";
import { DEFAULT_WORKSPACE, normalizeWindows, normalizeWorkspace, rememberWindow, WINDOW_MEMORY_LIMIT, type WorkspaceState } from "../src/shell/workspace";

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

describe("place and size of floating windows are remembered (ADR 0019 decision 9)", () => {
  const rect = { x: 100, y: 80, w: 700, h: 500 };

  it("by window kind, at most 64 kinds: the latest last, the oldest forgotten", () => {
    let ws: WorkspaceState = DEFAULT_WORKSPACE;
    for (let i = 0; i < WINDOW_MEMORY_LIMIT + 6; i++) ws = rememberWindow(ws, `kind-${i}`, { ...rect, x: i });
    expect(Object.keys(ws.windows)).toHaveLength(WINDOW_MEMORY_LIMIT);
    expect(ws.windows["kind-0"]).toBeUndefined();
    ws = rememberWindow(ws, "kind-10", { ...rect, x: 999.6 });
    expect(Object.keys(ws.windows).at(-1)).toBe("kind-10");
    expect(ws.windows["kind-10"].x).toBe(1000);
    expect(rememberWindow(ws, "kind-10", { ...rect, x: 1000 })).toBe(ws); // the same place — no new state to save
    expect(rememberWindow(ws, "bad", { ...rect, w: 0 })).toBe(ws);
  });

  it("a damaged record of the settings file is dropped; the rest stays", () => {
    const w = normalizeWorkspace({ windows: { "chart-histogram": rect, qq: { x: "a", y: 0, w: 1, h: 1 }, box: { ...rect, h: -1 }, "": rect } });
    expect(w.windows).toEqual({ "chart-histogram": rect });
    expect(normalizeWindows([rect])).toEqual({});
    expect(normalizeWorkspace(undefined).windows).toEqual({});
  });

  it("a new window opens where the last of its kind was left, inside the app window; next ones are shifted", () => {
    expect(initialRect("chart-histogram", "normal", [], 1280, 800, rect)).toEqual(rect);
    expect(initialRect("chart-histogram", "normal", [{ kind: "chart-histogram" }, { kind: "qq" }], 1280, 800, rect)).toEqual({ ...rect, x: 124, y: 104 });
    // remembered on a larger screen: the title bar stays reachable, the size fits the viewport
    expect(initialRect("chart-histogram", "normal", [], 1024, 640, { x: 1500, y: 900, w: 1400, h: 900 })).toEqual({ x: 964, y: 610, w: 1024, h: 640 });
    expect(initialRect("chart-histogram", "normal", [], 1280, 800)).toEqual({ x: 210, y: 100, w: 860, h: 600 }); // never left — centered
  });

  it("the window layer keeps the memory in the workspace of the user's settings", () => {
    const store = new Store({ workspace: DEFAULT_WORKSPACE });
    setWindowMemory(settingsMemory({ store } as never));
    try {
      const a = openWindow("chart-histogram", "Гистограмма · Набор 1", "normal", { n: 1 }, 1280, 800);
      const win = windowStore.get().windows.find((w) => w.id === a)!;
      rememberWindowRect({ ...win, x: 40, y: 50, w: 600, h: 420 });
      rememberWindowRect({ ...win, x: 0, y: 0, w: 1280, h: 800, maximized: true }); // maximized: the old place stays
      expect(store.get().workspace.windows["chart-histogram"]).toEqual({ x: 40, y: 50, w: 600, h: 420 });
      closeWindow(a);
      const b = openWindow("chart-histogram", "Гистограмма · Набор 1", "normal", { n: 2 }, 1280, 800);
      expect(windowStore.get().windows.find((w) => w.id === b)).toMatchObject({ x: 40, y: 50, w: 600, h: 420 });
      closeWindow(b);
    } finally {
      setWindowMemory(null);
    }
  });
});
