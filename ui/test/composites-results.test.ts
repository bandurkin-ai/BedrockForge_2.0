// Results of sample processing in the tree and «Свойства» (ТЗ 8.0.2, 8.2.3; ADR 0018 §2, §10–11; Э12 12.27): the rows
// «Обработка проб» under a set with «устарел» and the reason, the menu (Открыть таблицу, Параметры…, Перестроить,
// Свойства, Удалить) — delete asks our own question `danger`, «Параметры…» opens the tool with the values the core
// ran with, the keyboard of the tree follows WAI-ARIA, `composites.changed` reads the results again, the mark on a
// table tab. The core is a fetch stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ResultRow } from "../src/modules/composites/api";
import { bindComposites } from "../src/modules/composites/bind";
import { ResultFacts } from "../src/modules/composites/ResultProps";
import { eventSet, findResult, loadSetResults, paramsArgs, rebuildQuestion, rebuildWarning, recordFacts, resetResults, resultLabel, resultMenu, resultsStore, sourceFacts, staleText, tabMark, toolOfOp, writtenColumns } from "../src/modules/composites/results";
import { settleAsk } from "../src/modules/lineage/impactAsk";
import { lineageStore } from "../src/modules/lineage/state";
import { openResultProps, RESULT_WINDOW, ResultRows, runResultAction, TabMark } from "../src/modules/composites/ResultsTree";
import { renderCompositesWindow } from "../src/modules/composites/ToolWindow";
import { toolValues } from "../src/modules/composites/bind";
import { sourceFromArgs } from "../src/modules/composites/params";
import { composite } from "../src/modules/composites/tools/composite";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { AppContext } from "../src/shell/AppContext";
import { closeDialog, dialogStore } from "../src/shell/dialogs";
import { treeKey, type KeyRow } from "../src/shell/tree";
import { windowStore } from "../src/shell/windows";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

function result(over: Partial<ResultRow> = {}): ResultRow {
  return {
    id: 9,
    set_id: 3,
    table_id: 12,
    table: "Композиты 2 м",
    op: "length",
    op_title: "Композиты по длине",
    makes_table: true,
    name: "Композиты 2 м",
    params: { table: "5", fields: ["AU", "CU"], length: 2, remainder: "attach", f_min: 0.5, p_min: 0.5, origin: "collar", domain: { table: "6", field: "LITH" } },
    inputs: { tables: [{ key: "пробы", name: "Пробы", token: "a", rev: 3, fields: ["AU", "CU"] }], set: { version: 7 }, meshes: [] },
    sha256: "ab12cd",
    algo: "length-1",
    report: { summary: { before: { count: 4, length: 6, fields: { AU: { mean: 2.6, metal: 13 } } }, after: { count: 3, length: 6, fields: { AU: { mean: 2.5, metal: 15 } } } }, seconds: 1.5 },
    written: [],
    job_id: 41,
    created_at: "2026-11-20T10:00:00",
    stale: false,
    reason: null,
    ...over,
  };
}

const STALE = result({ id: 10, name: "Композиты по доменам", op: "domain", op_title: "Композиты по доменам", table: "Композиты по доменам", table_id: 13, stale: true, reason: "изменена таблица «Пробы»" });
const TOPCUT = result({ id: 11, name: "", op: "topcut", op_title: "Урезка", table: "Пробы", table_id: 5, makes_table: false, params: { table: "5", field: "AU", percentile: 99 }, written: [{ table: "пробы", columns: ["AU_CUT", "AU_CUT_F"] }] as unknown as string[], report: { edits: [] } });
const CONDITIONS = result({ id: 12, name: "Анализ кондиций", op: "conditions", op_title: "Анализ кондиций", table: null, table_id: null, makes_table: false });

let app: App;
let list: ResultRow[] = [];
let listStatus = 200;
/** the dependents the graph names for the result (`GET /lineage/impact`) */
let dependents = 0;
const sent: { method: string; path: string }[] = [];

beforeEach(() => {
  sent.length = 0;
  list = [result(), STALE, TOPCUT, CONDITIONS];
  listStatus = 200;
  dependents = 0;
  resetResults();
  windowStore.set({ windows: [] });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = new URL(url);
      const path = u.pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      sent.push({ method, path: `${path}${u.search}` });
      if (path === "/composites/results") return listStatus === 200 ? json({ results: list }) : json({ code: "not_found", message: "Нет", hint: "" }, listStatus);
      if (path.startsWith("/composites/results/") && method === "DELETE") return json({ deleted: 9 });
      if (path.endsWith("/rebuild")) return json({ source: "core", id: 50 });
      if (path === "/jobs") return json({ jobs: [] });
      if (path === "/lineage/impact") return json({ total: dependents, by_kind: dependents ? { "object:variogram_model": dependents } : {}, nodes: Array.from({ length: dependents }, (_, i) => ({ kind: "object:variogram_model", id: 7 + i, sub: "", name: `Модель ${i + 1}`, depth: 1 })), truncated: false });
      return json({ code: "not_found", message: "Нет", hint: "" }, 404);
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  app = createApp({ persist: false });
  bindComposites(app);
  app.store.set({ projectName: "Проект", projectReadOnly: false, setName: "Набор" });
});

afterEach(() => {
  lineageStore.set({ statuses: {}, version: 0 });
  vi.unstubAllGlobals();
  setCoreTarget(null);
  for (const d of dialogStore.get().queue) closeDialog(d.id);
});

const render = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));

describe("the results of a set", () => {
  it("are read by set and kept; a missing route (404) — none, not an error", async () => {
    await loadSetResults(3);
    expect(resultsStore.get().bySet[3].map((r) => r.id)).toEqual([9, 10, 11, 12]);
    expect(findResult(10)?.reason).toBe("изменена таблица «Пробы»");
    expect(sent.at(-1)?.path).toBe("/composites/results?set_id=3");
    listStatus = 404;
    await loadSetResults(4);
    expect(resultsStore.get().bySet[4]).toEqual([]);
    expect(resultsStore.get().errors[4]).toBeUndefined();
  });

  it("an event of the set is what reads them again; a new project forgets them", async () => {
    expect(eventSet("composites.changed", '{"set_id": 3}')).toBe(3);
    expect(eventSet("drillholes.changed", '{"set_id": 3, "table": "5", "rev": 4}')).toBe(3);
    expect(eventSet("objects.changed", '{"set_id": 3}')).toBeNull();
    expect(eventSet("composites.changed", "not json")).toBeNull();
    await loadSetResults(3);
    resetResults();
    expect(resultsStore.get().bySet).toEqual({});
  });

  it("asked again while a read is running — read once more after it, the newer answer stays", async () => {
    const answers: ((r: Response) => void)[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        sent.push({ method: "GET", path: new URL(url).pathname.replace("/api/v1", "") });
        return new Promise<Response>((res) => answers.push(res));
      }),
    );
    const first = loadSetResults(3);
    const again = loadSetResults(3); // an event during the read
    const third = loadSetResults(3); // one more — the same queued read
    expect(third).toBe(again);
    await vi.waitFor(() => expect(answers).toHaveLength(1));
    answers[0](json({ results: [result()] })); // read before the change: fresh
    await first;
    await vi.waitFor(() => expect(answers).toHaveLength(2));
    expect(resultsStore.get().bySet[3][0].stale).toBe(false);
    answers[1](json({ results: [result({ stale: true, reason: "изменена таблица «Пробы»" })] }));
    await again;
    expect(resultsStore.get().bySet[3][0].stale).toBe(true);
    expect(sent.filter((r) => r.path === "/composites/results")).toHaveLength(2);
    expect(answers).toHaveLength(2);
  });

  it("a read queued for the previous project is not made", async () => {
    const answers: ((r: Response) => void)[] = [];
    vi.stubGlobal("fetch", vi.fn(async () => new Promise<Response>((res) => answers.push(res))));
    const first = loadSetResults(3);
    const again = loadSetResults(3);
    resetResults();
    await vi.waitFor(() => expect(answers).toHaveLength(1));
    answers[0](json({ results: [result()] }));
    await first;
    await again;
    expect(answers).toHaveLength(1);
    expect(resultsStore.get().bySet).toEqual({});
  });

  it("show «устарел» with the reason, the name or the written columns", () => {
    expect(staleText(STALE)).toBe("устарел: изменена таблица «Пробы»");
    expect(staleText(result())).toBeNull();
    expect(writtenColumns(TOPCUT)).toEqual(["AU_CUT", "AU_CUT_F"]);
    expect(writtenColumns(result({ written: ["DECL_W"] }))).toEqual(["DECL_W"]);
    expect(resultLabel(TOPCUT)).toBe("Урезка · AU_CUT, AU_CUT_F");
  });
});

describe("the tree", () => {
  it("rows «Обработка проб» under the set: a group and a row per result with «устарел»", () => {
    const html = render(createElement(ResultRows, { setId: 3, results: [result(), STALE] }));
    expect(html).toContain('data-testid="tree-results-3"');
    expect(html).toMatch(/aria-level="3"[^>]*aria-label="Обработка проб, результатов 2, устарели 1"/);
    expect(html).toMatch(/aria-level="4"[^>]*aria-label="Композиты по доменам, устарел: изменена таблица «Пробы»"/);
    // the badge is the one of the graph (Д-35): nothing until the state of the node has come, then the word and the chain of the causes
    expect(html).not.toContain("lineage-badge-dh_result");
    lineageStore.set({ statuses: { "dh_result:10:": { kind: "dh_result", id: 10, sub: "", state: "stale", name: "Композиты по доменам", reason: "результат «Композиты по доменам» ← изменена таблица «Пробы»", changed: false } }, version: 1 });
    const warm = render(createElement(ResultRows, { setId: 3, results: [result(), STALE] }));
    expect(warm).toContain('data-testid="lineage-badge-dh_result-10"');
    expect(warm).toContain("устарел");
    expect(warm).not.toContain('data-testid="lineage-badge-dh_result-9"');
    expect(render(createElement(ResultRows, { setId: 3, results: [] }))).toBe("");
  });

  it("the keyboard of the tree is WAI-ARIA on these rows (levels, expanded)", () => {
    const html = render(createElement(ResultRows, { setId: 3, results: [result(), STALE] }));
    const rows: KeyRow[] = [{ level: 2, expanded: true }];
    for (const [tag] of html.matchAll(/<div role="treeitem"[^>]*>/g)) {
      const expanded = /data-expanded="(true|false)"/.exec(tag)?.[1];
      rows.push({ level: Number(/aria-level="(\d)"/.exec(tag)?.[1]), expanded: expanded === undefined ? undefined : expanded === "true" });
    }
    expect(rows.map((r) => r.level)).toEqual([2, 3, 4, 4]);
    expect(rows[1].expanded).toBe(true);
    expect(rows[2].expanded).toBeUndefined();
    expect(treeKey("ArrowRight", rows, 1)).toEqual({ kind: "focus", index: 2 }); // into the results
    expect(treeKey("ArrowLeft", rows, 3)).toEqual({ kind: "focus", index: 1 }); // a result → «Обработка проб»
    expect(treeKey("ArrowLeft", rows, 1)).toEqual({ kind: "collapse", index: 1 });
    expect(treeKey("ArrowDown", rows, 2)).toEqual({ kind: "focus", index: 3 });
  });

  it("the menu: five items, the reasons of what cannot be done", () => {
    expect(resultMenu(result(), false).map((i) => [i.label, i.reason])).toEqual([
      ["Открыть таблицу", null],
      ["Параметры…", null],
      ["Перестроить", null],
      ["Свойства", null],
      ["Удалить…", null],
    ]);
    expect(resultMenu(CONDITIONS, false)[0].reason).toContain("нет таблицы");
    const ro = resultMenu(result(), true);
    expect(ro.filter((i) => i.reason === "Проект открыт только для чтения").map((i) => i.action)).toEqual(["params", "rebuild", "delete"]);
  });

  it("«Удалить» asks our own question (danger); «Отмена» deletes nothing, «Удалить» deletes and reads again", async () => {
    const run = runResultAction(app, result(), "delete");
    await vi.waitFor(() => expect(dialogStore.get().queue).toHaveLength(1));
    const d = dialogStore.get().queue[0];
    expect(d.kind === "confirm" && d.options.danger).toBe(true);
    if (d.kind === "confirm") d.resolve(false);
    closeDialog(d.id);
    await run;
    expect(sent.filter((r) => r.method === "DELETE")).toHaveLength(0);

    const again = runResultAction(app, result(), "delete");
    await vi.waitFor(() => expect(dialogStore.get().queue).toHaveLength(1));
    const d2 = dialogStore.get().queue[0];
    if (d2.kind === "confirm") d2.resolve(true);
    closeDialog(d2.id);
    await again;
    expect(sent.filter((r) => r.method === "DELETE").map((r) => r.path)).toEqual(["/composites/results/9"]);
    expect(sent.at(-1)?.path).toBe("/composites/results?set_id=3");
  });

  it("«Удалить» with dependents: after the question the window «Влияние изменения» (Д-35); «Отмена» deletes nothing, «Продолжить» deletes", async () => {
    dependents = 2;
    const confirm = async () => {
      await vi.waitFor(() => expect(dialogStore.get().queue).toHaveLength(1));
      const d = dialogStore.get().queue[0];
      if (d.kind === "confirm") d.resolve(true);
      closeDialog(d.id);
    };
    const refused = runResultAction(app, result(), "delete");
    await confirm();
    await vi.waitFor(() => expect(windowStore.get().windows).toHaveLength(1));
    expect(windowStore.get().windows[0].props).toMatchObject({ kind: "dh_result", id: 9, ask: "удалить результат обработки проб №9" });
    settleAsk(windowStore.get().windows[0].props!.token as number, false);
    await refused;
    expect(sent.filter((r) => r.method === "DELETE")).toHaveLength(0);
    windowStore.set({ windows: [] });
    const go = runResultAction(app, result(), "delete");
    await confirm();
    await vi.waitFor(() => expect(windowStore.get().windows).toHaveLength(1));
    settleAsk(windowStore.get().windows[0].props!.token as number, true);
    await go;
    expect(sent.filter((r) => r.method === "DELETE").map((r) => r.path)).toEqual(["/composites/results/9"]);
  });

  it("«Перестроить» always asks our own question (decision 11); manual edits are warned of in its text", async () => {
    const answer = async (ok: boolean) => {
      await vi.waitFor(() => expect(dialogStore.get().queue).toHaveLength(1));
      const d = dialogStore.get().queue[0];
      if (d.kind !== "confirm") throw new Error("not a confirm");
      d.resolve(ok);
      closeDialog(d.id);
      return d.options;
    };
    const run = runResultAction(app, result(), "rebuild");
    const asked = await answer(true);
    await run;
    expect(asked).toMatchObject({ title: "Перестроить результат", okLabel: "Перестроить", danger: true });
    expect(asked.message).toBe("Перестроить «Композиты 2 м»? Таблица-результат будет пересчитана по текущим данным.");
    expect(sent.filter((r) => r.method === "POST").map((r) => r.path)).toEqual(["/composites/results/9/rebuild"]);

    const cut = runResultAction(app, TOPCUT, "rebuild");
    expect((await answer(false)).message).toContain("Записанные поля будут пересчитаны");
    await cut;
    expect(sent.filter((r) => r.path === "/composites/results/11/rebuild")).toHaveLength(0);

    const ore = result({ id: 20, op: "ore", report: { edits: [{ action: "split" }, { action: "move" }] } });
    expect(rebuildWarning(ore)).toBe("Ручных правок: 2. При перестроении они будут потеряны.");
    expect(rebuildQuestion(ore)).toContain("\n\nРучных правок: 2. При перестроении они будут потеряны.");
    const edited = runResultAction(app, ore, "rebuild");
    expect((await answer(false)).message).toContain("Ручных правок: 2");
    await edited;
    expect(sent.filter((r) => r.path === "/composites/results/20/rebuild")).toHaveLength(0);
  });

  it("«Параметры…» opens the tool window with the values the core ran with", async () => {
    const args = paramsArgs(result());
    expect(args).toMatchObject({ set_id: 3, table: "5", fields: "AU, CU", length: 2, method: "length", origin: "collar", domain: "LITH", domain_table: "6" });
    const values = toolValues(composite, args);
    expect(values).toMatchObject({ method: "length", length: "2", remainder: "attach", origin: "collar" });
    const src = sourceFromArgs(args);
    expect(src).toMatchObject({ setId: 3, table: "5", fields: ["AU", "CU"], domain: { kind: "field", table: "6", field: "LITH" } });
    expect(paramsArgs(TOPCUT)).toMatchObject({ table: "5", field: "AU", percentile: 99 });
    expect(paramsArgs(result({ params: { domain: { wireframe_ids: [4, 2] } } }))).toMatchObject({ domain_wireframes: "4, 2", table: "Пробы" });
    expect(toolOfOp("bench")).toBe("composite");
    expect(toolOfOp("coding")).toBe("coding");
    await runResultAction(app, result(), "params");
    const w = windowStore.get().windows.find((x) => x.kind === "composites-composite");
    expect((w?.props?.args as Record<string, unknown>).length).toBe(2);
  });
});

describe("«Свойства» and the tab", () => {
  it("source, parameters, hash, version, time, summary and «устарел»", () => {
    expect(sourceFacts(result())).toEqual([
      { label: "Таблица", value: "Пробы (правка 3) · AU, CU" },
      { label: "Устья и инклинометрия", value: "версия 7" },
    ]);
    const rec = recordFacts(STALE);
    expect(rec.find((f) => f.label === "Состояние")?.value).toBe("устарел: изменена таблица «Пробы»");
    expect(rec.find((f) => f.label === "SHA-256 входов")?.value).toBe("ab12cd");
    const html = renderToStaticMarkup(createElement(ResultFacts, { r: STALE }));
    expect(html).toContain("Длина L, м");
    expect(html).toContain("length-1");
    expect(html).toContain('data-testid="result-props-summary"');
    expect(html).toContain("Результат устарел: изменена таблица «Пробы»");
    expect(html).not.toMatch(/[a-z]{3,} [a-z]{3,} [a-z]{3,}/); // no English sentences
  });

  it("opens its own window; the shell's renderer finds it", () => {
    const id = openResultProps(result());
    const w = windowStore.get().windows.find((x) => x.id === id)!;
    expect(w.kind).toBe(RESULT_WINDOW);
    expect(w.props).toEqual({ resultId: 9, setId: 3 });
    expect(renderCompositesWindow(w)).not.toBeNull();
  });

  it("the mark of a result table on its tab: «устарел» with the reason", () => {
    expect(tabMark(null)).toBeNull();
    expect(tabMark({ result_id: 10, op: "domain", stale: true, reason: "изменена таблица «Пробы»" })).toMatchObject({ text: "устарел", stale: true });
    const html = renderToStaticMarkup(createElement(TabMark, { origin: { result_id: 10, op: "domain", stale: true, reason: "каркас удалён" } }));
    expect(html).toContain("устарел");
    expect(html).toContain("каркас удалён");
    expect(renderToStaticMarkup(createElement(TabMark, { origin: { result_id: 9, op: "length", stale: false, reason: null } }))).toContain("Результат обработки проб");
  });

  it("the tab of a table of the graph: «◆» and the badge of the node `dh_table` (the quiet dot of «актуален» is not shown)", () => {
    const origin = { result_id: 10, op: "domain", stale: true, reason: "каркас удалён" };
    const plain = renderToStaticMarkup(createElement(TabMark, { origin, table: "13" }));
    expect(plain).toContain("◆");
    expect(plain).not.toContain("lineage-badge");
    lineageStore.set({ statuses: { "dh_table:13:": { kind: "dh_table", id: 13, sub: "", state: "stale", name: "Композиты", reason: "таблица «Композиты» ← результат «Композиты» ← изменено: каркас «Руда»", changed: false } }, version: 1 });
    const stale = renderToStaticMarkup(createElement(TabMark, { origin, table: "13" }));
    expect(stale).toContain('data-testid="lineage-badge-dh_table-13"');
    expect(stale).toContain("устарел");
    lineageStore.set({ statuses: { "dh_table:13:": { kind: "dh_table", id: 13, sub: "", state: "current", name: "Композиты", reason: null, changed: false } }, version: 2 });
    expect(renderToStaticMarkup(createElement(TabMark, { origin, table: "13" }))).not.toContain("lineage-badge");
  });
});
