// The context of the screen (Э23, 23.15; ТЗ 7.32; docs/progress/e23-api.md §6): the shape of the contract, ≤ 4 KB, strings ≤ 200, windows ≤ 20, no secrets, a gap is not a zero.
import { describe, expect, it } from "vitest";
import { createApp } from "../../src/shell/app";
import { DEFAULT_VIEW, DEFAULT_WORKSPACE } from "../../src/shell/workspace";
import { windowStore } from "../../src/shell/windows";
import { auditStore } from "../../src/modules/audit/state";
import { projectStore } from "../../src/modules/projects/state";
import { tablesStore } from "../../src/modules/tables/state";
import { viewStore } from "../../src/modules/scene/bind";
import { auditText, buildContext, contextOf, CONTEXT_MAX_BYTES, panelsOf, viewModeOf, WINDOWS_MAX, type ContextSource } from "../../src/modules/assistant/context";

const source = (over: Partial<ContextSource> = {}, state: Partial<ContextSource["state"]> = {}): ContextSource => ({
  state: { projectName: "Демо", setName: "Демо", setHoles: 48, holeId: "DH-012", selected: null, view: { ...DEFAULT_VIEW, perspective: true, vexag: 2 }, workspace: DEFAULT_WORKSPACE, ...state },
  projectUid: "p-1",
  setId: 3,
  windows: ["Гистограмма · Демо"],
  section: { x0: 1000, y0: 2000, az: 45, length: 500, towards: 25, away: 25 },
  selectionName: null,
  audit: { error: 3, warning: 12, info: 40, stale: false },
  viewMode: "iso",
  ...over,
});

const bytes = (v: unknown) => new TextEncoder().encode(JSON.stringify(v)).length;

describe("buildContext", () => {
  it("has the shape of the contract", () => {
    const c = buildContext(source({}, { selected: { kind: "wireframe", id: 12 } }));
    expect(c).toEqual({
      project: { uid: "p-1", name: "Демо" },
      set: { id: 3, name: "Демо", holes: 48 },
      hole: { id: "DH-012", set_id: 3 },
      panels: panelsOf(DEFAULT_WORKSPACE),
      windows: ["Гистограмма · Демо"],
      view: { mode: "iso", perspective: true, vexag: 2 },
      section: { x0: 1000, y0: 2000, az: 45, length: 500, towards: 25, away: 25 },
      selection: { kind: "wireframe", id: 12, name: "№12" },
      audit: "Аудит: 3 / 12 / 40",
    });
  });

  it("a gap stays a gap: no project, no set, no hole, no section, no selection are null, not zeros", () => {
    const c = buildContext(source({ projectUid: null, setId: null, section: null, audit: null }, { projectName: null, setName: null, setHoles: 0, holeId: null }));
    expect(c).toMatchObject({ project: null, set: null, hole: null, section: null, selection: null, audit: null });
  });

  it("a hole without a set, and a set without an id, are not invented", () => {
    expect(buildContext(source({ setId: null })).hole).toBeNull();
    expect(buildContext(source({ setId: null })).set).toBeNull();
  });

  it("a section without a length limit is left out (the contract asks for a number there, 0 would be a lie)", () => {
    expect(buildContext(source({ section: { x0: 1, y0: 2, az: 3, length: null, towards: 25, away: 25 } })).section).toBeNull();
  });

  it("the audit says what it knows: numbers, stale, never run", () => {
    expect(auditText({ error: 0, warning: 1, info: 2, stale: true })).toBe("Аудит: 0 / 1 / 2 (устарел)");
    expect(auditText("none")).toBe("Аудит не запускался");
    expect(auditText(null)).toBeNull();
  });

  it("the panels are as the people see them", () => {
    expect(panelsOf({ ...DEFAULT_WORKSPACE, left: true, right: true, rightTab: "assistant", bottom: true, bottomTab: "log" })).toEqual(["дерево", "Claude", "нижняя (Журнал команд)"]);
    expect(panelsOf({ ...DEFAULT_WORKSPACE, left: false, right: true, rightTab: "properties", bottom: false })).toEqual(["свойства"]);
  });

  it("strings are cut to 200 characters, windows to 20", () => {
    const long = "ж".repeat(500);
    const c = buildContext(source({ windows: Array.from({ length: 30 }, (_, i) => `Окно ${i}`), selectionName: long }, { projectName: long, selected: { kind: "lines", id: 1 } }));
    expect(c.project?.name).toHaveLength(200);
    expect(c.selection?.name).toHaveLength(200);
    expect(c.windows).toHaveLength(WINDOWS_MAX);
  });

  it("stays within 4 KB: the windows, then the panels, go first", () => {
    const wide = (i: number) => `${i}`.padEnd(200, "Ж");
    const c = buildContext(source({ windows: Array.from({ length: 20 }, (_, i) => wide(i)) }, { projectName: wide(1), holeId: wide(2) }));
    expect(bytes(c)).toBeLessThanOrEqual(CONTEXT_MAX_BYTES);
    expect(c.windows.length).toBeLessThan(20);
    expect(c.project?.name).toHaveLength(200); // what the user works on stays
  });

  it("a typical context is far below the limit", () => {
    expect(bytes(buildContext(source()))).toBeLessThan(1024);
  });

  it("has no secrets: nothing but the names of the contract is in it", () => {
    const text = JSON.stringify(buildContext(source())).toLowerCase();
    for (const word of ["sk-ant", "token", "secret", "password", "api_key", "authorization"]) expect(text).not.toContain(word);
    expect(Object.keys(buildContext(source())).sort()).toEqual(["audit", "hole", "panels", "project", "section", "selection", "set", "view", "windows"]);
  });

  it("non-finite numbers never reach the JSON", () => {
    const c = buildContext(source({ section: { x0: Number.NaN, y0: 1, az: 2, length: 3, towards: 4, away: 5 } }, { view: { ...DEFAULT_VIEW, vexag: Number.NaN } }));
    expect(c.section?.x0).toBe(0);
    expect(c.view.vexag).toBe(1);
  });
});

describe("viewModeOf", () => {
  const entry = (view: string, ok = true) => ({ commandId: "scene.view", ok, args: { view } });
  it("is the last good `scene.view` of the log (newest first), else the clip, else isometry", () => {
    expect(viewModeOf([entry("top"), entry("front")], false)).toBe("top");
    expect(viewModeOf([entry("side", false), entry("front")], false)).toBe("front");
    expect(viewModeOf([{ commandId: "scene.fit", ok: true, args: {} }], true)).toBe("section");
    expect(viewModeOf([], false)).toBe("iso");
    expect(viewModeOf([entry("bogus")], false)).toBe("iso");
  });
});

describe("contextOf (the stores of the window)", () => {
  it("reads the project, the set, the hole, the audit, the windows and the section of the open window", () => {
    const app = createApp({ persist: false });
    projectStore.set({ current: { uid: "p-9", name: "Демо" } as never });
    tablesStore.set({ setId: 3 });
    app.store.set({ projectName: "Демо", setName: "Набор", setHoles: 48, holeId: "DH-012" });
    auditStore.set({ setId: 3, result: { totals: { error: 1, warning: 2, info: 3 }, stale: false } as never });
    viewStore.set({ section: { line: { x0: 10, y0: 20, az: 90, length: 300, towards: 5, away: 6 }, clip: true } });
    windowStore.set({ windows: [{ id: "w1", kind: "histogram", title: "Гистограмма · Набор", x: 0, y: 0, w: 1, h: 1, z: 1, maximized: false }] });
    try {
      const c = contextOf(app);
      expect(c.project).toEqual({ uid: "p-9", name: "Демо" });
      expect(c.set).toEqual({ id: 3, name: "Набор", holes: 48 });
      expect(c.hole).toEqual({ id: "DH-012", set_id: 3 });
      expect(c.audit).toBe("Аудит: 1 / 2 / 3");
      expect(c.windows).toEqual(["Гистограмма · Набор"]);
      expect(c.section).toEqual({ x0: 10, y0: 20, az: 90, length: 300, towards: 5, away: 6 });
    } finally {
      projectStore.set({ current: null });
      tablesStore.set({ setId: null });
      auditStore.set({ setId: null, result: null });
      viewStore.set({ section: undefined });
      windowStore.set({ windows: [] });
    }
  });

  it("an audit that never ran for the set says so; without a set there is no audit text", () => {
    const app = createApp({ persist: false });
    tablesStore.set({ setId: 4 });
    app.store.set({ setName: "Набор", setHoles: 1 });
    auditStore.set({ setId: 4, result: null });
    try {
      expect(contextOf(app).audit).toBe("Аудит не запускался");
      app.store.set({ setName: null });
      expect(contextOf(app).audit).toBeNull();
    } finally {
      tablesStore.set({ setId: null });
      auditStore.set({ setId: null, result: null });
    }
  });
});
