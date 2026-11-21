// True thickness and ore intercepts (ТЗ 8.5.4; ADR 0021 §9–10; Э15 15.14): the two commands of the registry with
// their flags, the buttons of «Анализ → Пересечения» found by the palette, a command without its required arguments
// opens its window, with them starts the job under `/intercepts`; a value out of the ranges of 8.5.4 never reaches
// the core; 409 `table_exists` — the assistant takes the free name; `composite.rebuild` of a result `tt` goes to
// `/intercepts/results/{id}/rebuild`; the prefix of the routes of Э12 stays `/composites`. The core is a fetch stub.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiOfOp, loadResults, preview, startTool } from "../src/modules/composites/api";
import { bindComposites } from "../src/modules/composites/bind";
import { toolApi } from "../src/modules/composites/params";
import { composite } from "../src/modules/composites/tools/composite";
import { bindIntercepts, interceptJobBody, TT_WINDOW, RUN_WINDOW } from "../src/modules/intercepts/bind";
import { commands, INTERCEPT_BUTTONS, INTERCEPT_GROUP_TITLE } from "../src/modules/intercepts/commands";
import { RUN_TOOL, TT_TOOL } from "../src/modules/intercepts/params";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { closeDialog, dialogStore } from "../src/shell/dialogs";
import { searchPalette } from "../src/shell/paletteSearch";
import { flattenRibbon, type RibbonTab } from "../src/shell/ribbonConfig";
import { windowStore } from "../src/shell/windows";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const TABLES = {
  tables: [
    { table: "collar", kind: "collar", name: "Устья", rows: 2, rev: 1 },
    { table: "5", kind: "assay", name: "Пробы", rows: 10, rev: 4 },
    { table: "6", kind: "interval", name: "Литология", rows: 4, rev: 1 },
    { table: "7", kind: "event", name: "Керн", rows: 3, rev: 1 },
  ],
};

let app: App;
const sent: { method: string; path: string; body: Record<string, unknown> }[] = [];
let exists = 0;

beforeEach(() => {
  sent.length = 0;
  exists = 0;
  windowStore.set({ windows: [] });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = new URL(url);
      const path = u.pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      sent.push({ method, path, body });
      if (path === "/drillholes/sets/3/tables") return json(TABLES);
      if (path === "/jobs") return json({ jobs: [] });
      if (path === "/composites/results") return json({ results: [{ id: 21, set_id: 3, op: "tt", name: "" }, { id: 22, set_id: 3, op: "topcut", name: "" }] });
      if (path === "/intercepts/run" && method === "POST" && exists-- > 0) return json({ code: "table_exists", message: "Таблица уже есть.", hint: "", name: "Рудные пересечения", suggested: "Рудные пересечения (2)" }, 409);
      if (method === "POST") return json({ source: "core", id: 51 });
      return json({ code: "not_found", message: "Нет", hint: "" }, 404);
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  app = createApp({ persist: false });
  bindComposites(app);
  bindIntercepts(app);
  app.store.set({ projectName: "Проект", projectReadOnly: false, setName: "Набор" });
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
  for (const d of dialogStore.get().queue) closeDialog(d.id);
});

const posts = (prefix: string) => sent.filter((r) => r.method !== "GET" && r.path.startsWith(prefix));

describe("registry", () => {
  it("has the two commands of ADR 0021 §9: `intercept.tt` with confirm, `intercept.run` without", () => {
    expect(commands.map((c) => c.id)).toEqual(["intercept.tt", "intercept.run"]);
    expect(commands.filter((c) => c.confirm).map((c) => c.id)).toEqual(["intercept.tt"]);
    expect(commands.some((c) => c.uiOnly)).toBe(false);
    for (const c of commands) {
      expect(app.registry.get(c.id)?.run, c.id).toBeTypeOf("function");
      expect(app.registry.get(c.id)?.stage).toBeUndefined();
      expect(c.title).toMatch(/[А-Яа-яЁё]/);
      expect(c.description).toMatch(/[А-Яа-яЁё]/);
    }
    const tt = commands[0].params ?? {};
    for (const k of ["set_id", "table", "source", "alpha", "dipdir", "domain", "domain_table", "domain_wireframes", "attitudes", "wireframe_ids", "d_max", "r_n", "core_table", "core_alpha", "core_beta", "core_line", "d_core", "fallback_alpha", "fallback_dipdir", "eps", "step", "angles"])
      expect(tt, k).toHaveProperty(k);
    const run = commands[1].params ?? {};
    for (const k of ["field", "cutoff", "l_min", "min_by", "waste_max", "include_cutoff", "density", "missing", "fields", "name", "source"]) expect(run, k).toHaveProperty(k);
  });

  it("the buttons of «Анализ → Пересечения» are bound and found by the palette", () => {
    expect(INTERCEPT_GROUP_TITLE).toBe("Пересечения");
    expect(INTERCEPT_BUTTONS.map((b) => b.command)).toEqual([TT_TOOL.command, RUN_TOOL.command]);
    expect(INTERCEPT_BUTTONS.map((b) => b.icon)).toEqual(["thickness", "intercepts"]);
    const tab: RibbonTab = { id: "analysis", title: "Анализ", groups: [{ title: INTERCEPT_GROUP_TITLE, items: INTERCEPT_BUTTONS.map((b) => ({ ...b })) }] };
    const items = flattenRibbon([tab]);
    for (const b of INTERCEPT_BUTTONS) {
      expect(app.registry.reason(b.command), b.command).toBeNull();
      expect(searchPalette(b.label.toLowerCase(), items, []).some((r) => r.kind === "command" && r.item.command === b.command), b.label).toBe(true);
    }
  });

  it("is unavailable without a project, in a read-only project and without a set", () => {
    app.store.set({ projectName: null });
    expect(app.registry.reason("intercept.tt")).toBe("Откройте или создайте проект");
    app.store.set({ projectName: "Проект", projectReadOnly: true });
    expect(app.registry.reason("intercept.run")).toBe("Проект открыт только для чтения");
    app.store.set({ projectReadOnly: false, setName: null });
    expect(app.registry.reason("intercept.tt")).toBe("Нет активного набора скважин");
  });
});

describe("window or job", () => {
  it("without the required arguments opens the window with what is given", async () => {
    const run = app.registry.execute("intercept.tt", { set_id: 3, eps: 0.2 }, "claude");
    await vi.waitFor(() => expect(dialogStore.get().queue).toHaveLength(1)); // `confirm`: the assistant asks first
    const d = dialogStore.get().queue[0];
    if (d.kind === "confirm") d.resolve(true);
    closeDialog(d.id);
    const r = await run;
    expect(r.ok).toBe(true);
    expect(r.result).toContain("Открыто окно «Истинная мощность»: заданы не все обязательные параметры — set_id, table, source");
    expect(windowStore.get().windows.find((w) => w.kind === TT_WINDOW)?.props).toEqual({ args: { set_id: 3, eps: 0.2 } });
    await app.registry.execute("intercept.run", { set_id: 3 }, "user");
    expect(windowStore.get().windows.some((w) => w.kind === RUN_WINDOW)).toBe(true);
    expect(posts("/intercepts")).toHaveLength(0);
  });

  it("with them starts `POST /intercepts/tt`: the table by name, the orientation list, the defaults of 8.5.4", async () => {
    const r = await app.registry.execute("intercept.tt", { set_id: 3, table: "Пробы", source: "global", alpha: 30, dipdir: 360 }, "user");
    expect(r.ok, r.result).toBe(true);
    expect(r.result).toContain("задание №51");
    const [post] = posts("/intercepts");
    expect(post.path).toBe("/intercepts/tt");
    expect(post.body).toEqual({ set_id: 3, table: "5", eps: 0.1, step: 1, angles: true, orientation: [{ source: "global", alpha: 30, dipdir: 0 }] });
  });

  it("domain by a field of another table, mesh and core with the fallback: the bodies of ADR 0021 §10", async () => {
    const dom = await interceptJobBody(TT_TOOL, { set_id: 3, table: "5", source: "domain", domain: "LITH", domain_table: "Литология", attitudes: "QZ=60/120; SH=45/90", fallback_alpha: 10, fallback_dipdir: 20 });
    expect(dom.orientation).toEqual([
      { source: "domain", field: "LITH", table: "6", attitudes: [{ code: "QZ", alpha: 60, dipdir: 120 }, { code: "SH", alpha: 45, dipdir: 90 }] },
      { source: "global", alpha: 10, dipdir: 20 },
    ]);
    const mesh = await interceptJobBody(TT_TOOL, { set_id: 3, table: "Пробы", source: "mesh", wireframe_ids: "4, 9", r_n: 5 });
    expect(mesh.orientation).toEqual([{ source: "mesh", wireframe_ids: [4, 9], d_max: 50, r_n: 5 }]);
    const core = await interceptJobBody(TT_TOOL, { set_id: 3, table: "Пробы", source: "core", core_table: "Керн", core_alpha: "ALPHA_K", core_beta: "BETA_K", core_line: "toh" });
    expect(core.orientation).toEqual([{ source: "core", table: "7", alpha: "ALPHA_K", beta: "BETA_K", line: "toh", d_core: 10 }]);
  });

  it("a value out of the ranges of 8.5.4 never reaches the core; empty is not zero", async () => {
    for (const args of [{ alpha: 95, dipdir: 10 }, { alpha: 30, dipdir: 400 }, { alpha: 30, dipdir: 10, eps: 0.6 }, { alpha: 30, dipdir: 10, step: 0.05 }, { alpha: 30 }]) {
      const r = await app.registry.execute("intercept.tt", { set_id: 3, table: "Пробы", source: "global", ...args }, "user");
      expect(r.ok, JSON.stringify(args)).toBe(false);
    }
    const r = await app.registry.execute("intercept.tt", { set_id: 3, table: "Пробы", source: "mesh", wireframe_ids: "4", d_max: 2000 }, "user");
    expect(r.result).toContain("«Поиск ближайшего d_max»: допустимо 0–1000 м");
    const e = await app.registry.execute("intercept.tt", { set_id: 3, table: "Пробы", source: "global", alpha: 30 }, "user");
    expect(e.result).toContain("«Азимут падения Aп»: введите значение");
    expect(sent).toHaveLength(0); // checked before any request
  });

  it("an unknown table is refused; a table of events is not a source", async () => {
    const r = await app.registry.execute("intercept.tt", { set_id: 3, table: "Керн", source: "global", alpha: 1, dipdir: 1 }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Таблица «Керн» не найдена");
    expect(posts("/intercepts")).toHaveLength(0);
  });

  it("`intercept.run`: the rule and no orientation by default; 409 — the free name", async () => {
    exists = 1;
    const r = await app.registry.execute("intercept.run", { set_id: 3, table: "Пробы", field: "AU", cutoff: 1, include_cutoff: 1.5, fields: "CU, AG" }, "claude");
    expect(r.ok, r.result).toBe(true);
    const runs = posts("/intercepts/run");
    expect(runs).toHaveLength(2);
    expect(runs[0].body).toMatchObject({ set_id: 3, table: "5", field: "AU", cutoff: 1, l_min: 2, min_by: "length", waste_max: 2, include_cutoff: 1.5, missing: "waste", name: "Рудные пересечения", fields: ["CU", "AG"] });
    expect(runs[0].body).not.toHaveProperty("orientation");
    expect(runs[1].body.name).toBe("Рудные пересечения (2)");
    const bad = await app.registry.execute("intercept.run", { set_id: 3, table: "Пробы", field: "AU", cutoff: 1, include_cutoff: 0.5 }, "user");
    expect(bad.result).toContain("должен быть больше c");
    const l = await app.registry.execute("intercept.run", { set_id: 3, table: "Пробы", field: "AU", cutoff: 1, l_min: 150 }, "user");
    expect(l.result).toContain("допустимо 0–100 м");
    // c > 0 (the core: 422 `bad_param` {param: "cutoff"}); nothing is started
    const zero = await app.registry.execute("intercept.run", { set_id: 3, table: "Пробы", field: "AU", cutoff: 0 }, "user");
    expect(zero.result).toContain("должно быть больше нуля");
    expect(runs).toHaveLength(2);
  });
});

describe("the prefix of the routes (ADR 0021 §1)", () => {
  it("a tool names its prefix; Э12 stays under `/composites`", async () => {
    expect(toolApi(composite)).toBe("/composites");
    expect(toolApi(TT_TOOL)).toBe("/intercepts");
    await startTool("/run", { a: 1 }, async () => "cancel");
    await startTool("/tt", { a: 1 }, async () => "cancel", "/intercepts");
    await preview("length", { a: 1 }, 7);
    expect(posts("/").map((r) => r.path)).toEqual(["/composites/run", "/intercepts/tt", "/composites/length/preview"]);
  });

  it("`composite.rebuild` goes to the module of the operation of the result", async () => {
    expect(apiOfOp("tt")).toBe("/intercepts");
    expect(apiOfOp("intercepts")).toBe("/intercepts");
    expect(apiOfOp("topcut")).toBe("/composites");
    expect(apiOfOp(null)).toBe("/composites");
    await loadResults(3);
    await app.registry.execute("composite.rebuild", { result_id: 21 }, "user");
    await app.registry.execute("composite.rebuild", { result_id: 22 }, "user");
    expect(posts("/").map((r) => r.path)).toEqual(["/intercepts/results/21/rebuild", "/composites/results/22/rebuild"]);
  });
});
