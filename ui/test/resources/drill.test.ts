// The transition from a cell of the report to its blocks (Э20, 20.15; ТЗ 7.35 Д-36, ТД36-1; ADR 0027 решения 22–24, вопрос № 111): a cell of the table → the body of `…/drill`, the rules of the
// answer → the temporary restrictor of the model in the 3D (not the library of filters; «Сбросить», the closed window, another project put the old one back), the numbers of the window = the
// cell, the table of the blocks by pages of 1000, the CSV of a page, the stale report, the refusals in Russian, a block → the inspector of Д-30. The core is a stub that answers with the real
// answers of `core/tests/data/resources/contract_answers.json` (`drill`, `drill_rows`).
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { restrictOf, BLOCK_STYLES_KEY, styleIn } from "../../src/modules/blocks/display";
import { checkDraft, slabTitle, type Rule } from "../../src/modules/blocks/filters/constructor";
import { projectStore } from "../../src/modules/projects/state";
import { viewStore } from "../../src/modules/scene/bind";
import { readDrill, readReportResult } from "../../src/modules/resources/answers";
import { cellOfForm, cellOfRow, emitCellClick, type CellRef } from "../../src/modules/resources/cells";
import { bodyOfCell, cellTitle, clearFilter, columnsOf, csvName, DRILL_WINDOW, drillStore, inspectRow, loadPage, pagesOf, readDrillPage, refuseCell, resetDrill, saveCsv, showCell, targetOfCell, windowOfDrill, PAGE_SIZE, type DrillTarget } from "../../src/modules/resources/drill";
import { DrillWindow, renderDrillWindow } from "../../src/modules/resources/DrillWindow";
import { drillText } from "../../src/modules/resources/format";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { closeWindow, windowStore } from "../../src/shell/windows";
import { answer, ID, installCore, json, newApp, teardown, waitFor, type Core } from "./harness";

let app: App;
let core: Core;
let unbind: () => void;
let rowsSent: Record<string, unknown>[] = [];
let rowsAnswer: () => Response;
const STALE = "изменена таблица проб";

/** The answer of the core with the model of the project (the sample of the file names model 1). */
const drillBody = (patch: Record<string, unknown> = {}) => ({ ...(answer("drill").body as object), model_id: ID.model, ...patch });
const pageBody = (patch: Record<string, unknown> = {}) => ({ ...(answer("drill_rows").body as object), model_id: ID.model, ...patch });
const manyRows = (n: number, page: number) => {
  const one = (answer("drill_rows").body as { rows: Record<string, unknown>[] }).rows[0];
  const from = (page - 1) * PAGE_SIZE;
  return Array.from({ length: Math.min(PAGE_SIZE, n - from) }, (_v, i) => ({ ...one, i: from + i }));
};

beforeEach(() => {
  core = installCore();
  ({ app, unbind } = newApp());
  resetDrill();
  rowsSent = [];
  rowsAnswer = () => json(pageBody());
  const base = globalThis.fetch;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = new URL(url);
      const path = u.pathname.replace("/api/v1", "");
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      if (path.startsWith("/view/")) return json({ version: 1, value: body.value });
      if (path === `/resources/reports/${ID.report}/drill`) {
        core.sent.push({ method: init?.method ?? "GET", path, query: u.search, body });
        return json(drillBody());
      }
      if (path === `/resources/reports/${ID.report}/drill/rows`) {
        rowsSent.push(body);
        return rowsAnswer();
      }
      return base(url, init);
    }),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  clearFilter();
  resetDrill();
  windowStore.set({ windows: [] });
  teardown(unbind);
});

const result = () => readReportResult(answer("report_get").body)!;
const name = (id: number) => `Отчёт №${id}`;
const target = (patch: Partial<DrillTarget> = {}): DrillTarget => ({ reportId: ID.report, reportName: "Р", label: "Измеренные", body: { group: ["Измеренные"], cutoff: 1 }, ...patch });
const style = () => styleIn(viewStore.get(), ID.model);
const html = () => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(DrillWindow)));
const sentDrill = () => core.sent.filter((r) => r.path === `/resources/reports/${ID.report}/drill`);

describe("the cell → the body of the request", () => {
  it("a cell of the table is one cell of the core: the labels of the keys and the cut-off; `kind` only when the cut-off does not tell it", () => {
    const r = result();
    const row = r.rows.find((x) => x.g[0] === "Измеренные" && x.cutoff === 1)!;
    const cell = cellOfRow(r, row, "tonnes");
    expect(cell).toMatchObject({ exact: true, group: ["Измеренные"], cutoff: 1, kind: "cutoff", reportId: ID.report });
    const t = targetOfCell(cell, name);
    expect(t).toMatchObject({ ok: true });
    if (t.ok) expect(t.target).toEqual({ reportId: ID.report, reportName: "Отчёт №2", label: "Измеренные", body: { group: ["Измеренные"], cutoff: 1 } });
    // the total: no group
    const total = result().rows.find((x) => x.total)!;
    expect(bodyOfCell(cellOfRow(r, total, "blocks"))).toEqual({ group: null, cutoff: 1 });
    // balance / off-balance rows go with their kind (the cut-offs of the report)
    expect(bodyOfCell({ group: ["A"], cutoff: 1.5, kind: "balance" })).toEqual({ group: ["A"], cutoff: 1.5, kind: "balance" });
    expect(bodyOfCell({ group: ["A"], cutoff: null, kind: "all" })).toEqual({ group: ["A"], cutoff: null });
    expect(bodyOfCell({ group: [], cutoff: null, kind: "offbalance" })).toEqual({ group: null, cutoff: null, kind: "offbalance" });
  });

  it("a cell that is a sum of cells (a section, a subtotal) or the preview is not drilled: the person is told why", () => {
    const r = result();
    const sub = { ...cellOfRow(r, r.rows[1], "tonnes"), exact: false, section: "Измеренные", members: null, group: null } as CellRef;
    const a = targetOfCell(sub, name);
    expect(a.ok).toBe(false);
    if (!a.ok) expect(a.refusal).toMatchObject({ code: "drill_not_exact", message: expect.stringContaining("не одна ячейка таблицы"), hint: expect.stringContaining("ячейке строки таблицы") });
    const b = targetOfCell({ ...cellOfRow(r, r.rows[1], "tonnes"), reportId: null }, name);
    expect(b.ok).toBe(false);
    if (!b.ok) expect(b.refusal).toMatchObject({ code: "drill_preview", hint: expect.stringContaining("Сохраните отчёт") });
    refuseCell({ code: "drill_not_exact", message: "Эта ячейка не одна ячейка таблицы.", hint: "Щёлкните по строке.", param: null });
    expect(windowOfDrill()).not.toBeNull();
    expect(html()).toContain("Эта ячейка не одна ячейка таблицы. Щёлкните по строке.");
  });

  it("a cell of the form of one key without sections is exact; the title of a cell says the row", () => {
    const r = result();
    expect(r.form).not.toBeNull();
    const form = r.form!;
    const section = form.sections[0];
    const row = section.rows.find((x) => x.kind === "row");
    if (row) {
      const col = form.columns[0];
      const cell = cellOfForm(r, form, null, row, col);
      expect(cell.exact).toBe(r.keys.length === 1);
    }
    expect(cellTitle("Итого", { group: null, cutoff: null })).toBe("Итого, без борта");
    expect(cellTitle("A", { group: ["A"], cutoff: 1.5 })).toBe("A, борт ≥ 1,5");
    expect(cellTitle("A", { group: ["A"], cutoff: 1, kind: "balance" })).toBe("A, балансовые ≥ 1");
    expect(cellTitle("A", { group: ["A"], cutoff: 0.5, kind: "offbalance" })).toBe("A, забалансовые 0,5");
  });
});

describe("the answers of the core", () => {
  it("the real `drill_rows` is read: the block, its centre, sizes, share, volume, tonnes, elements and keys; the numbers are the cell's", () => {
    const p = readDrillPage(answer("drill_rows").body)!;
    expect(p).toMatchObject({ page: 1, size: 1000, total: 1, csv: null });
    expect(p.cell).toEqual({ blocks: 1, volume: 1000, tonnes: 2500 });
    expect(p.rows[0]).toEqual({ i: 1, j: 1, k: 0, x: 15, y: 15, z: 5, dx: 10, dy: 10, dz: 10, f: 1, volume: 1000, tonnes: 2500, keys: { CLASS: "Измеренные" }, elements: { AU: 1 } });
    // Σ of the rows of the set = the cell (ТД36-1)
    expect(p.rows.reduce((s, r) => s + (r.volume ?? 0), 0)).toBe(p.cell.volume);
    expect(p.rows.reduce((s, r) => s + (r.tonnes ?? 0), 0)).toBe(p.cell.tonnes);
    expect(readDrillPage({})).toBeNull();
    expect(columnsOf(p.rows)).toEqual({ keys: ["CLASS"], elements: ["AU"] });
    const gap = readDrillPage({ ...(answer("drill_rows").body as object), rows: [{ i: 0, j: 0, k: 0, x: null, f: 1, volume: null, tonnes: null, keys: { CLASS: null }, elements: { AU: null } }] })!;
    expect(gap.rows[0]).toMatchObject({ x: null, volume: null, tonnes: null, keys: { CLASS: null }, elements: { AU: null } });
    expect(gap.rows[0].elements.AU).toBeNull(); // a gap is not 0
  });

  it("pages of 1000: the count of the pages, at least one", () => {
    expect(pagesOf(0)).toBe(1);
    expect(pagesOf(1000)).toBe(1);
    expect(pagesOf(1001)).toBe(2);
    expect(pagesOf(2500)).toBe(3);
  });

  it("the warnings of the core are in the answer and in the text of the command", () => {
    const d = readDrill({ ...(answer("drill").body as object), warnings: [{ code: "wireframe_volume", message: "Объём каркаса не равен сумме блоков.", hint: "Проверьте сетку.", param: null }] })!;
    expect(d.warnings).toHaveLength(1);
    expect(drillText(d, "Р")).toContain("Предупреждение: Объём каркаса не равен сумме блоков. Проверьте сетку.");
    expect(readDrill(answer("drill").body)!.warnings).toEqual([]);
  });
});

describe("the temporary filter of the 3D", () => {
  it("the rules of the answer go to the restrictor of the model (`quick.rule`), the model is shown; the filters of the library and the quick restrictor stay", async () => {
    const a = await showCell(target());
    expect(a).not.toBeNull();
    expect(sentDrill().at(-1)?.body).toEqual({ group: ["Измеренные"], cutoff: 1 });
    expect(style().visible).toBe(true);
    expect(style().quick.rule).toEqual(a!.rules);
    expect(style().filter_id).toBeNull();
    expect(restrictOf(style().quick)).toEqual(a!.rules);
    expect(drillStore.get()).toMatchObject({ applied: true, load: "ready", notes: [] });
    // not written to the library of filters
    expect(core.sent.some((r) => r.path.startsWith("/filters") && r.method !== "GET")).toBe(false);
    // the quick restrictor of the person is joined by «И»
    const quick = { items: [{ field: "AU", op: ">" as const, value: 1 }], join: "and" as const, invert: true, rule: a!.rules as unknown as Rule };
    expect(restrictOf(quick)).toEqual({ op: "and", items: [{ op: "not", items: [{ cond: { field: "AU", op: ">", value: 1 } }] }, a!.rules] });
  });

  it("«Сбросить» puts back the restrictor that was; closing the window does the same; a second cell keeps the first `before`", async () => {
    await showCell(target());
    clearFilter();
    expect(style().quick.rule).toBeUndefined();
    expect(drillStore.get().applied).toBe(false);
    // two cells in a row: the restrictor of before the first one comes back
    await showCell(target());
    await showCell(target({ label: "Итого", body: { group: null, cutoff: 1 } }));
    expect(style().quick.rule).toBeDefined();
    clearFilter();
    expect(style().quick.rule).toBeUndefined();
  });

  it("the filter goes with the window: with the window closed the 3D is as it was (the binding of the app)", async () => {
    const r = await app.registry.execute("resources.drill", { report_id: ID.report, group: "Измеренные", cutoff: 1 }, "user");
    expect(r.ok).toBe(true);
    expect(style().quick.rule).toBeDefined();
    const win = windowOfDrill()!;
    expect(win).not.toBeNull();
    closeWindow(win);
    expect(style().quick.rule).toBeUndefined();
    expect(drillStore.get().applied).toBe(false);
  });

  it("another project forgets the transition", async () => {
    await showCell(target());
    projectStore.set({ current: { uid: "p2", name: "Другой" } as never, version: 2 });
    expect(drillStore.get()).toMatchObject({ target: null, applied: false, answer: null });
    projectStore.set({ current: { uid: "p1", name: "П" } as never, version: 3 });
  });

  it("the style written by the view keeps the rule across a read (the view of the project)", async () => {
    const a = await showCell(target());
    const raw = (viewStore.get()[BLOCK_STYLES_KEY] as Record<string, unknown>)[String(ID.model)];
    expect(styleIn({ [BLOCK_STYLES_KEY]: { [ID.model]: raw } }, ID.model).quick.rule).toEqual(a!.rules);
    expect(styleIn({ [BLOCK_STYLES_KEY]: { [ID.model]: { quick: { items: [], join: "and", invert: false } } } }, ID.model).quick).toEqual({ items: [], join: "and", invert: false });
  });
});

describe("the window: the numbers, the warnings, the table, the pages", () => {
  it("the numbers of the window are the numbers of the cell; the window is ours (one, titled)", async () => {
    await showCell(target());
    await waitFor(() => drillStore.get().rows.load === "ready");
    const w = windowStore.get().windows.filter((x) => x.kind === DRILL_WINDOW);
    expect(w).toHaveLength(1);
    expect(w[0].title).toContain("Переход к блокам");
    expect(w[0].title).toContain("Измеренные, борт ≥ 1");
    const text = html();
    expect(text).toContain('data-testid="drill-blocks">1<');
    expect(text).toMatch(/data-testid="drill-volume">1\s000</);
    expect(text).toMatch(/data-testid="drill-tonnes">2\s500</);
    expect(text).not.toContain("Отчёт устарел");
    // a second cell uses the same window
    await showCell(target({ label: "Итого", body: { group: null, cutoff: null } }));
    expect(windowStore.get().windows.filter((x) => x.kind === DRILL_WINDOW)).toHaveLength(1);
    expect(renderDrillWindow(w[0])).not.toBeNull();
    expect(renderDrillWindow({ ...w[0], kind: "other" })).toBeNull();
  });

  it("the table: the first page is read with `page: 1, size: 1000`; the block row has the centre, the sizes, the share, the volume, the tonnes, the grades, the keys; a gap is «—»", async () => {
    rowsAnswer = () => json(pageBody({ rows: [...((pageBody() as unknown as { rows: object[] }).rows), { i: 2, j: 1, k: 0, x: null, y: null, z: null, dx: 10, dy: 10, dz: 10, f: 0.5, volume: null, tonnes: null, keys: { CLASS: "Измеренные" }, elements: { AU: null } }], total: 2 }));
    await showCell(target());
    await waitFor(() => drillStore.get().rows.load === "ready");
    expect(rowsSent).toEqual([{ group: ["Измеренные"], cutoff: 1, page: 1, size: 1000 }]);
    const text = html();
    expect(text).toContain("Центр X; Y; Z, м");
    expect(text).toContain("15; 15; 5");
    expect(text).toContain("10 × 10 × 10");
    expect(text).toContain("Измеренные");
    expect(text).toContain("AU");
    expect(text).toContain('data-testid="drill-inspect-1"');
    const row2 = text.split('data-testid="drill-row-1"')[1].split("</tr>")[0];
    expect(row2).toContain("—");
    expect(row2.match(/gap[^"]*"[^>]*>—</g)).toHaveLength(3); // the volume, the tonnes and the grade are gaps, not 0
    expect(text).toContain("Страница 1 из 1, блоков 2");
  });

  it("2500 blocks are three pages: the next page asks `page: 2`, the last one has 500 rows", async () => {
    rowsAnswer = () => {
      const page = (rowsSent.at(-1)?.page as number) ?? 1;
      return json(pageBody({ page, total: 2500, rows: manyRows(2500, page) }));
    };
    await showCell(target());
    await waitFor(() => drillStore.get().rows.load === "ready");
    expect(drillStore.get().rows.data?.rows).toHaveLength(1000);
    await loadPage(3);
    expect(rowsSent.at(-1)).toMatchObject({ page: 3, size: 1000 });
    expect(drillStore.get()).toMatchObject({ page: 3 });
    expect(drillStore.get().rows.data?.rows).toHaveLength(500);
    expect(html()).toContain("Страница 3 из 3, блоков 2");
  });

  it("an out-of-date report: the warning «набор блоков может отличаться» with the reason; the numbers stay", async () => {
    const base = globalThis.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => (new URL(url).pathname.endsWith(`/${ID.report}/drill`) ? json(drillBody({ stale: true, stale_reason: STALE })) : base(url, init))),
    );
    await showCell(target());
    const text = html();
    expect(text).toContain(`Отчёт устарел (${STALE}): набор блоков может отличаться от показанного в таблице. Пересчитайте отчёт.`);
    expect(text).toContain('data-testid="drill-blocks">1<');
    const r = await app.registry.execute("resources.drill", { report_id: ID.report, cutoff: 1 }, "claude");
    expect(r.result).toContain("Отчёт устарел");
  });

  it("the warnings of the core are under the numbers", async () => {
    const base = globalThis.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) =>
        new URL(url).pathname.endsWith(`/${ID.report}/drill`) ? json(drillBody({ warnings: [{ code: "no_density", message: "У модели нет плотности.", hint: "Тоннаж посчитан по SG.", param: null }] })) : base(url, init),
      ),
    );
    await showCell(target());
    expect(html()).toContain("У модели нет плотности. Тоннаж посчитан по SG.");
  });
});

describe("the refusals are in Russian", () => {
  it("the core does not know the report: the words and the hint of the core are in the window; the numbers are not shown", async () => {
    const base = globalThis.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) =>
        new URL(url).pathname.endsWith("/drill") ? json({ code: "resource_report_not_found", message: "Отчёт по ресурсам №2 не найден.", hint: "Номера отчётов — в дереве." }, 404) : base(url, init),
      ),
    );
    const a = await showCell(target());
    expect(a).toBeNull();
    expect(drillStore.get()).toMatchObject({ load: "failed", error: { code: "resource_report_not_found" }, applied: false });
    const text = html();
    expect(text).toContain("Отчёт по ресурсам №2 не найден. Номера отчётов — в дереве.");
    expect(text).not.toContain("drill-blocks");
    const r = await app.registry.execute("resources.drill", { report_id: ID.report }, "claude");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Отчёт по ресурсам №2 не найден.");
  });

  it("the blocks of the cell are not read: the numbers stay, the table says why", async () => {
    rowsAnswer = () => json({ code: "bad_param", message: "Страница вне диапазона.", hint: "Страницы — от 1." }, 422);
    await showCell(target());
    await waitFor(() => drillStore.get().rows.load === "failed");
    const text = html();
    expect(text).toContain('data-testid="drill-blocks">1<');
    expect(text).toContain("Страница вне диапазона. Страницы — от 1.");
  });

  it("a model the project does not have: told under the numbers, the window works", async () => {
    // a model the project does not have
    const base = globalThis.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => (new URL(url).pathname.endsWith(`/${ID.report}/drill`) ? json(drillBody({ model_id: 777 })) : base(url, init))),
    );
    clearFilter();
    await showCell(target());
    expect(drillStore.get().notes[0]).toContain("Блочная модель №777 не найдена в проекте: фильтр в 3D не применён.");
    expect(drillStore.get().applied).toBe(false);
    expect(html()).toContain("фильтр в 3D не применён");
  });
});

describe("the CSV of a page", () => {
  const stubSave = (saved: unknown) => {
    const saveFile = vi.fn(async () => saved);
    vi.stubGlobal("window", { bf: { saveFile } });
    return saveFile;
  };

  it("the page is asked again with `csv: true` and goes to the save window of the application with a name that Windows takes", async () => {
    await showCell(target({ reportName: "Ресурсы: Q1/2026" }));
    await waitFor(() => drillStore.get().rows.load === "ready");
    const text = "i;j;k;объём\r\n1;1;0;1000,0\r\n";
    rowsAnswer = () => json(pageBody({ csv: text }));
    const save = stubSave("C:\\Отчёты\\блоки.csv");
    await saveCsv();
    expect(rowsSent.at(-1)).toEqual({ group: ["Измеренные"], cutoff: 1, page: 1, size: 1000, csv: true });
    expect(save).toHaveBeenCalledWith("Блоки ячейки — Ресурсы_ Q1_2026 — Измеренные, борт ≥ 1 — стр. 1.csv", "csv", text);
    expect(drillStore.get().csvNote).toEqual({ text: "Страница 1 записана в файл C:\\Отчёты\\блоки.csv", error: false });
    expect(csvName(target(), 2)).toBe("Блоки ячейки — Р — Измеренные, борт ≥ 1 — стр. 2.csv");
  });

  it("a closed window writes nothing; a refusal of the file is told; no bridge is told", async () => {
    await showCell(target());
    rowsAnswer = () => json(pageBody({ csv: "a;b\r\n" }));
    stubSave(null);
    await saveCsv();
    expect(drillStore.get().csvNote).toBeNull();
    stubSave({ error: "Нет доступа к папке." });
    await saveCsv();
    expect(drillStore.get().csvNote).toEqual({ text: "Нет доступа к папке.", error: true });
    vi.stubGlobal("window", {});
    await saveCsv();
    expect(drillStore.get().csvNote).toEqual({ text: "Сохранение файлов доступно только в приложении.", error: true });
    stubSave("x.csv");
    rowsAnswer = () => json(pageBody({ csv: null }));
    await saveCsv();
    expect(drillStore.get().csvNote).toEqual({ text: "Ядро не вернуло текст страницы.", error: true });
  });
});

describe("a block → the inspector of Д-30", () => {
  it("the row goes to `estimate.inspect {model_id, block: «i; j; k»}`; an unavailable inspector or its refusal is told in the window", async () => {
    await showCell(target());
    await waitFor(() => drillStore.get().rows.load === "ready");
    const row = drillStore.get().rows.data!.rows[0];
    app.registry.bind("estimate.inspect", async () => "", () => "Нет оценки блоков");
    await inspectRow(app, row);
    expect(drillStore.get().inspectNote).toBe("Инспектор блока недоступен: Нет оценки блоков.");
    const seen: Record<string, unknown>[] = [];
    app.registry.bind("estimate.inspect", async (a) => (seen.push(a), "Открыт блок"), () => null);
    await inspectRow(app, row);
    expect(seen).toEqual([{ model_id: ID.model, block: "1; 1; 0" }]);
    expect(drillStore.get().inspectNote).toBeNull();
    app.registry.bind("estimate.inspect", async () => {
      throw new Error("Блок вне домена оценки.");
    }, () => null);
    await inspectRow(app, row);
    expect(drillStore.get().inspectNote).toContain("Блок вне домена оценки.");
  });
});

describe("the click on a cell of the report and the command", () => {
  it("a click on a cell of the table runs `resources.drill` with the labels and the cut-off: the window, the filter, the table", async () => {
    const r = result();
    const row = r.rows.find((x) => x.g[0] === "Измеренные" && x.cutoff === 1)!;
    expect(emitCellClick(cellOfRow(r, row, "tonnes"))).toBe(true);
    await waitFor(() => sentDrill().length > 0 && drillStore.get().rows.load === "ready");
    expect(sentDrill().at(-1)?.body).toEqual({ group: ["Измеренные"], cutoff: 1 });
    expect(windowOfDrill()).not.toBeNull();
    expect(style().quick.rule).toBeDefined();
  });

  it("a click on a cell of the form with sections or on the preview opens the window with the reason, nothing goes to the core", async () => {
    const r = result();
    emitCellClick({ ...cellOfRow(r, r.rows[1], "tonnes"), exact: false, group: null, section: "Раздел" });
    expect(sentDrill()).toHaveLength(0);
    expect(drillStore.get().error?.code).toBe("drill_not_exact");
    emitCellClick({ ...cellOfRow(r, r.rows[1], "tonnes"), reportId: null });
    expect(drillStore.get().error?.code).toBe("drill_preview");
    expect(sentDrill()).toHaveLength(0);
  });

  it("a label with «;» is asked directly (the command's string cannot say it)", async () => {
    const r = result();
    emitCellClick({ ...cellOfRow(r, r.rows[1], "tonnes"), group: ["А; Б"] });
    await waitFor(() => sentDrill().length > 0);
    expect(sentDrill().at(-1)?.body).toEqual({ group: ["А; Б"], cutoff: 1 });
  });

  it("the command answers with the numbers, the rules (for the assistant), the window and the filter; the declaration says the window and the temporary filter", async () => {
    const r = await app.registry.execute("resources.drill", { report_id: ID.report, group: "Измеренные", cutoff: 1 }, "claude");
    expect(r.ok).toBe(true);
    expect(r.result).toMatch(/блоков 1, объём 1\s000 м³, тоннаж 2\s500 т/);
    expect(r.result).toContain("Открыто окно «Переход к блокам»");
    expect(r.result).toContain("временный фильтр");
    expect(r.result).toContain('"cond"');
    expect(app.registry.get("resources.drill")?.description).toContain("Переход к блокам");
  });
});

describe("the layer `slab` in the constructor of the filters (read-only)", () => {
  const slab = { slab: { axis: "u" as const, from: 0, to: 25, by: "centre" as const, frame: { azimuth: 30, dip: -10, roll: 0 } } };
  it("a rule with a layer passes the check as it came, `frame` kept; its title is in words", () => {
    const rule: Rule = { op: "and", items: [{ cond: { field: "CLASS", op: "=", value: "Измеренные" } }, slab, { slab: { axis: "z", from: 0, to: 10, by: "share" } }] };
    const checked = checkDraft(rule as never);
    expect(checked).toEqual({ ok: true, rule });
    expect(slabTitle(slab.slab)).toBe("U от 0 до 25, по центру, система полосы: азимут 30°, падение -10°, поворот 0°");
    expect(slabTitle({ axis: "z", from: 0, to: 10, by: "share" })).toBe("Z от 0 до 10, по доле высоты");
    expect(slabTitle({ axis: "z", from: 1.5, to: 2.25, by: "centre" })).toBe("Z от 1,5 до 2,25, по центру");
  });
});
