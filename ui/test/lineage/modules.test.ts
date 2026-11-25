// The graph of dependencies in the trees of the modules (Э18, 18.Г4; Д-35; ADR 0025 §7, §10): the badge of the state at the rows of
// the computed objects (the model of variography, the data and the reports of QAQC) — nothing until the core has answered the state
// of the node, then the word and the chain of the causes; and the window «Влияние изменения» before the import replaces a table or
// the collars of a set that has dependents.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setCoreTarget } from "../../src/shell/api";
import { AppContext } from "../../src/shell/AppContext";
import { createApp, type App } from "../../src/shell/app";
import { windowStore } from "../../src/shell/windows";
import { settleAsk } from "../../src/modules/lineage/impactAsk";
import { lineageStore } from "../../src/modules/lineage/state";
import { DataRows } from "../../src/modules/qaqc/DataRows";
import { ReportRows } from "../../src/modules/qaqc/ReportsTree";
import { reportsStore } from "../../src/modules/qaqc/reportRun";
import { dataStore } from "../../src/modules/qaqc/setup/dataStore";
import { ModelsTree } from "../../src/modules/variography/ModelsTree";
import { variographyStore } from "../../src/modules/variography/state";
import { askReplaced, initialState, replacedBy, type FileEntry, type WizardState } from "../../src/modules/importing/wizard";

let app: App;
beforeEach(() => {
  app = createApp({ persist: false });
  windowStore.set({ windows: [] });
});
afterEach(() => {
  lineageStore.set({ statuses: {}, version: 0 });
  vi.unstubAllGlobals();
  setCoreTarget(null);
});

const render = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));
const stale = (kind: string, id: number, reason: string) => [`${kind}:${id}:`, { kind, id, sub: "", state: "stale", name: "", reason, changed: false }] as const;

describe("the badge at the rows of the computed objects", () => {
  it("the model of variography: nothing until the state comes; «устарел» with the chain; the title of the row keeps the reason of the module", () => {
    variographyStore.set({
      models: [{ id: 5, name: "Au_D1", set_id: 1, table: "Композиты", field: "AU", transform: "none", type: "spherical", color: null, sill: 1, structures: 2, stale: true, stale_reason: "изменена таблица «Композиты»", source_lost: false, created_at: null, updated_at: null }],
      modelsLoaded: true,
    });
    const before = render(createElement(ModelsTree));
    expect(before).toContain('data-testid="tree-vario-model-5"');
    expect(before).not.toContain("lineage-badge");
    expect(before).toContain("Устарел: изменена таблица «Композиты»"); // the tooltip of the row stays
    lineageStore.set({ statuses: Object.fromEntries([stale("object:variogram_model", 5, "модель вариограммы «Au_D1» ← результат «Композиты» ← изменено: таблица «Пробы»")]), version: 1 });
    const after = render(createElement(ModelsTree));
    expect(after).toContain('data-testid="lineage-badge-object:variogram_model-5"');
    expect(after).toContain('data-state="stale"');
    expect(after).toContain("← результат «Композиты»"); // the chain of the causes, not only the direct one
    variographyStore.set({ models: [], modelsLoaded: false });
  });

  it("QAQC: the data and the reports have the badge; the word «устарел» is no longer in the label", () => {
    dataStore.set({ list: [{ id: 4, name: "Контроль", version: 1, rows: 10, elements: [], stale: true }] } as never);
    reportsStore.set({ list: [{ id: 7, name: "Отчёт", version: 1, state: "ready", stale: "data", data_name: "Контроль" }], error: null, selected: null } as never);
    lineageStore.set({ statuses: Object.fromEntries([stale("object:qaqc_data", 4, "данные QAQC «Контроль» ← изменено: таблица «Пробы»"), stale("object:qaqc_report", 7, "отчёт QAQC «Отчёт» ← данные QAQC «Контроль»")]), version: 1 });
    const data = render(createElement(DataRows));
    expect(data).toContain('data-testid="lineage-badge-object:qaqc_data-4"');
    expect(data).not.toContain("· устарели");
    const reports = render(createElement(ReportRows));
    expect(reports).toContain('data-testid="lineage-badge-object:qaqc_report-7"');
    expect(reports).toContain("← данные QAQC «Контроль»");
    expect(reports).not.toContain("Отчёт · устарел");
    dataStore.set({ list: [] } as never);
    reportsStore.set({ list: null, error: null, selected: null });
  });
});

const file = (p: Partial<FileEntry>): FileEntry => ({
  id: 1, path: "a.csv", name: "a.csv", options: { header_row: 1 }, inspect: null, check: null, kind: null,
  mapping: {}, components: [], attributes: [], force: false, tableName: "a", busy: false, error: null, ...p,
});
const SET = { id: 7, name: "Н", holes: 3, tables: [{ name: "assay_2023", kind: "assay", rows: 1200 }, { name: "Литология", kind: "interval", rows: 5 }] };
const existing = (files: FileEntry[]): WizardState => ({ ...initialState(), setMode: "existing", setId: 7, sets: [SET], files });

/** The core: the tables of the set 7 (the number of a table is its key) and the dependents of the nodes (`kind:id` → how many). */
function core(impact: Record<string, number>) {
  const asked: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const path = String(url).replace(/^.*\/api\/v1/, "");
      const json = (v: unknown) => new Response(JSON.stringify(v), { status: 200 });
      if (path === "/drillholes/sets/7/tables") return json({ tables: [{ table: "collar", kind: "collar", name: "Устья" }, { table: "31", kind: "assay", name: "assay_2023" }, { table: "32", kind: "interval", name: "Литология" }] });
      if (path.startsWith("/lineage/impact")) {
        const q = new URLSearchParams(path.split("?")[1]);
        const key = `${q.get("kind")}:${q.get("id")}`;
        asked.push(key);
        const n = impact[key] ?? 0;
        return json({ total: n, by_kind: n ? { "object:variogram_model": n } : {}, nodes: Array.from({ length: n }, (_, i) => ({ kind: "object:variogram_model", id: 100 + i, sub: "", name: `Модель ${i + 1}`, depth: 1 })), truncated: false });
      }
      return json({});
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  return asked;
}

describe("the import asks «Влияние изменения» before it replaces (Д-35)", () => {
  it("what is replaced: a table by its kind and name, the collars and the surveys — the set; a new set — nothing", () => {
    expect(replacedBy(existing([file({ kind: "assay", tableName: "assay_2024" }), file({ id: 2, kind: "interval", tableName: "RQD" }), file({ id: 3, kind: "collar" })]))).toEqual([
      { what: "таблицу «assay_2023» (1 200 строк)", table: { kind: "assay", name: "assay_2024" } },
      { what: "устья набора", table: null },
    ]);
    expect(replacedBy({ ...existing([file({ kind: "assay" })]), setMode: "new" })).toEqual([]);
  });

  it("no dependents — no window; dependents of the table — the window with the number of the table; «Отмена» stops the import, «Продолжить» goes on", async () => {
    const asked = core({});
    const s = existing([file({ kind: "assay", tableName: "assay_2024" })]);
    expect(await askReplaced(s)).toBe(true);
    expect(asked).toEqual(["dh_table:31"]);
    expect(windowStore.get().windows).toHaveLength(0);

    core({ "dh_table:31": 2 });
    const refused = askReplaced(s);
    await vi.waitFor(() => expect(windowStore.get().windows).toHaveLength(1));
    expect(windowStore.get().windows[0].props).toMatchObject({ kind: "dh_table", id: 31, ask: "заменить таблицу «assay_2023» (1 200 строк) набора «Н»" });
    settleAsk(windowStore.get().windows[0].props!.token as number, false);
    expect(await refused).toBe(false);

    windowStore.set({ windows: [] });
    const go = askReplaced(s);
    await vi.waitFor(() => expect(windowStore.get().windows).toHaveLength(1));
    settleAsk(windowStore.get().windows[0].props!.token as number, true);
    expect(await go).toBe(true);
  });

  it("the collars are the set: the question is about the node of the set; nothing replaced — the core is not even asked", async () => {
    const asked = core({ "dh_set:7": 1 });
    const run = askReplaced(existing([file({ kind: "collar" })]));
    await vi.waitFor(() => expect(windowStore.get().windows).toHaveLength(1));
    expect(windowStore.get().windows[0].props).toMatchObject({ kind: "dh_set", id: 7, ask: "заменить устья набора" });
    settleAsk(windowStore.get().windows[0].props!.token as number, true);
    expect(await run).toBe(true);
    asked.length = 0;
    expect(await askReplaced({ ...existing([file({ kind: "assay" })]), setMode: "new" })).toBe(true);
    expect(asked).toEqual([]);
  });
});
