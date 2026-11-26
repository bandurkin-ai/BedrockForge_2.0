// Э20, подшаг 20.17 (ТЗ 8.10 «Подсчёт ресурсов», 7.35 Д-36, Д-37; ТД36-1; ADR 0027 «Подсчёт ресурсов»): the resource report end to end on a
// synthetic project made through the core (no real data, no mocks — only the real answers of the core): 36 vertical holes with AU and a
// domain DOM, a block model 12 × 12 × 6 (DAT: DOMAIN, the density DENS), a model of variogram, two estimations of the model (ordinary
// kriging «AU_OK_», inverse distance «AU_IDW_» — the wizard of Э18). One app, one project, the scenarios in a row (each ≤ 60 s, the waits
// are on events and states, never on the pixels):
//  1. Ctrl+K «Отчёт по ресурсам» → the window (the model, the element, the cut-offs, the balance, the density, the key «Уступ», the template
//     JORC) → «Сохранить» → the panel «Ресурсы» (the form of the template, the table, the curve, the control) → «Сохранить XLSX…» → the file
//     is a zip with the sheets «Отчёт», «Таблица», «Кривая», «Контроль», «Параметры».
//  2. ТД36-1: a cell of the table → the window «Переход к блокам» (the blocks, the volume and the tonnes are those of the cell; the sums of
//     the table of the blocks equal the cell; the same cell again — the same set) → a block → the inspector of Д-30 → a probe → the interval
//     of the hole of 7.17.
//  3. The template ГКЗ: «Шаблоны форм отчёта» (the sample with «Балансовые» / «Забалансовые») and the form of the saved report in ГКЗ.
//  4. The comparison of the reports (A against A — every difference is 0; A against another report) and of the estimations (two fields EST →
//     the field Δ, the statistics, the table of the domains).
//  5. Ctrl+K finds the commands of the resources; the ribbon «Блочные модели» → «Ресурсы».
// Not covered (no point in the interface yet): the strip of «Вариография → Swath» → `slab` of the filter (20.15: `modules/swath` has no click on a
// strip), the grouping by a category field (the field DOMAIN of the synthetic model is a number, not a category — only «Уступ» is grouped).
// A window of 1024 × 640 with the ribbon folded; the limits on the .exe are ×1.5 (ADR 0022 «CI»).
import { expect, test, type ElectronApplication, type Locator, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import zlib from "node:zlib";
import { answerSaveDialog, close, coreApi, launch, runCommand, setWindowSize, shot, waitFrame, waitJob } from "./helpers";
import { blocksList, rowsList, build, FIELD, lastJob, lastJobId, palette, type Ids } from "./estimationWorld";

const EXE = !!process.env.BF_APP_EXE;
const LIMIT = (ms: number) => (EXE ? 1.5 * ms : ms);

const OK = "AU_OK_EST";
const IDW = "AU_IDW_EST";
const REPORT = "Ресурсы Э20";
const REPORT_B = "Ресурсы Э20 · IDW";

type Row = {
  g: (string | null)[];
  total: boolean;
  kind: string;
  cutoff: number | null;
  blocks: number;
  volume: number | null;
  tonnes: number | null;
  elements: Record<string, { grade: number | null; metal: number | null }>;
};
type Report = { stale: boolean; stale_reason: string | null; graph: unknown; inputs: unknown; report_id: number; name: string; rows: Row[]; rows_total: number; curve: { cutoffs: number[] } | null; form: { template: unknown } | null;
  params: { template_id: string | null }; elements: { field: string }[] };
type DrillRows = { cell: { blocks: number; volume: number; tonnes: number }; total: number; rows: { i: number; j: number; k: number; volume: number | null; tonnes: number | null; elements: Record<string, number | null> }[] };
type Cells = { elements: Record<string, { grade: number | null; metal: number | null }> };
type CompareRow = { only_in: string | null; a: Cells | null; b: Cells | null; delta: { blocks: number; volume: number | null; tonnes: number | null; elements: Record<string, { grade: number | null; metal: number | null }> } | null };
type Compared = { rows: CompareRow[]; warnings: { code: string }[] };

let app: ElectronApplication;
let page: Page;
let dir: string;
let ids: Ids;
let reportId = 0;
let reportB = 0;
let benchRow = -1; // the row of the table the scenario 2 clicks: a bench, «без борта»

test.describe.configure({ mode: "serial" });

/** The wizard of Э18: the first estimation of AU of the model (ordinary kriging, the model of variogram) → the job is done. */
async function estimateOk(): Promise<void> {
  await palette(page, "Оценка содержаний");
  const wiz = page.getByTestId("estimation-wizard");
  await expect(wiz).toBeVisible({ timeout: 30_000 });
  const tab = (n: number) => wiz.getByTestId(`est-step-${n}-tab`);
  await wiz.getByTestId("est-model").selectOption(String(ids.model));
  await expect(wiz.getByTestId("est-model-info")).toContainText("Блоков");
  await wiz.getByTestId("est-domain-field").selectOption("DOMAIN");
  await wiz.getByTestId("est-label").fill("1");
  await tab(2).click();
  await expect(wiz.getByTestId("est-set")).toHaveValue(String(ids.set), { timeout: 30_000 });
  await expect(wiz.getByTestId("est-field")).toHaveValue(FIELD, { timeout: 30_000 });
  await wiz.getByTestId("est-domain").selectOption("field");
  await wiz.getByTestId("est-domain-col").selectOption("DOM");
  await expect(wiz.getByTestId("est-domain-values").getByRole("checkbox", { name: "1", exact: true })).toBeChecked({ timeout: 30_000 });
  await tab(3).click();
  await wiz.getByTestId("est-method-ok").check();
  await expect(wiz.getByTestId("est-variogram").locator(`option[value="${ids.vario}"]`)).toBeAttached({ timeout: 30_000 });
  await wiz.getByTestId("est-variogram").selectOption(String(ids.vario));
  await tab(4).click();
  await expect(wiz.getByTestId("est-search-model")).toContainText("Из модели", { timeout: 30_000 }); // the ellipsoid of the model of variogram
  await expect(wiz.getByTestId("est-pass-0")).toBeVisible();
  await tab(5).click();
  await expect(wiz.getByTestId("est-field-EST")).toBeChecked();
  await tab(6).click();
  await expect(wiz.getByTestId("est-summary")).toContainText("OK");
  await expect(wiz.getByTestId("est-run-go")).toBeEnabled({ timeout: 30_000 });
  const before = await lastJobId(page, "estimate.run");
  await wiz.getByTestId("est-run-go").click();
  await lastJob(page, "estimate.run", LIMIT(90_000), before);
  await expect(wiz.getByTestId("est-run-done")).toBeVisible({ timeout: LIMIT(30_000) });
  await wiz.getByTestId("est-close").click();
  await expect(wiz).toHaveCount(0);
}

/**
 * The second estimation (inverse distance, the prefix «AU_IDW_»): a row of «Параметры оценки» made from the first one through the API of the core
 * (the wizard reopens the row of its last run — a second row from the same wizard is not what the window does), then the job `estimate.run`.
 */
async function estimateIdw(): Promise<void> {
  const first = (await rowsList(page))[0];
  const meta = (await coreApi<{ meta: Record<string, unknown> }>(page, "GET", `/geostat/estimations/${first.id}`)).body.meta;
  const body: Record<string, unknown> = { ...meta, name: "AU · IDW · 1", method: { kind: "idw", power: 2, mean: null, ik: null }, outputs: { prefix: "AU_IDW_", fields: ["STATUS", "EST"] } };
  for (const k of ["v", "origin", "inputs", "algo", "versions", "created_at", "last_run"]) delete body[k];
  const made = await coreApi<{ id: number }>(page, "POST", "/geostat/estimations", body);
  expect(made.status, JSON.stringify(made.body)).toBe(200);
  const version = (await blocksList(page)).find((m) => m.id === ids.model)!.version;
  const started = await coreApi<{ source: string; id: number }>(page, "POST", "/geostat/estimate/run", { estimation_ids: [made.body.id], expected_version: version, replace: false });
  expect(started.status, JSON.stringify(started.body)).toBe(200);
  const job = await waitJob(page, started.body.source, started.body.id, undefined, LIMIT(90_000));
  expect(job.status, JSON.stringify(job.error)).toBe("done");
}

/** The entries of a zip (an XLSX is one) read without a library: the central directory, the deflated or stored data. */
function unzip(file: string): Map<string, Buffer> {
  const buf = fs.readFileSync(file);
  let end = buf.length - 22;
  while (end >= 0 && buf.readUInt32LE(end) !== 0x06054b50) end--;
  expect(end, "the end of the central directory of the zip").toBeGreaterThanOrEqual(0);
  const count = buf.readUInt16LE(end + 10);
  let at = buf.readUInt32LE(end + 16);
  const out = new Map<string, Buffer>();
  for (let n = 0; n < count; n++) {
    expect(buf.readUInt32LE(at)).toBe(0x02014b50);
    const method = buf.readUInt16LE(at + 10);
    const size = buf.readUInt32LE(at + 20);
    const nameLen = buf.readUInt16LE(at + 28);
    const extraLen = buf.readUInt16LE(at + 30);
    const commentLen = buf.readUInt16LE(at + 32);
    const local = buf.readUInt32LE(at + 42);
    const name = buf.toString("utf8", at + 46, at + 46 + nameLen);
    const start = local + 30 + buf.readUInt16LE(local + 26) + buf.readUInt16LE(local + 28);
    const raw = buf.subarray(start, start + size);
    out.set(name, method === 8 ? zlib.inflateRawSync(raw) : raw);
    at += 46 + nameLen + extraLen + commentLen;
  }
  return out;
}

/** The bottom dock higher by `by` px (the splitter «Высота нижней панели» dragged up, as a hand does it): the table of the panel is a few rows tall in the default dock. */
async function raiseDock(by: number): Promise<void> {
  const b = (await page.getByRole("separator", { name: "Высота нижней панели" }).boundingBox())!;
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2 - by, { steps: 6 });
  await page.mouse.up();
}

/** Every floating window closed by the button «Закрыть» of its bar (the windows of a scenario cover the bottom dock of the next one). */
async function closeWindows(): Promise<void> {
  const dialogs = page.getByRole("dialog");
  for (let n = 0; n < 12 && (await dialogs.count()) > 0; n++) {
    await dialogs.last().getByRole("button", { name: "Закрыть", exact: true }).first().click();
    await page.waitForTimeout(50);
  }
  await expect(dialogs).toHaveCount(0);
}

/** A click on a cell of the table of the panel: the row is brought to the middle of the dock first (the sticky head of the table covers its top edge). */
async function clickCell(cell: Locator): Promise<void> {
  await cell.evaluate((el) => el.scrollIntoView({ block: "center", inline: "nearest" }));
  await cell.click();
}

const report = async (id: number, query = "") => (await coreApi<Report>(page, "GET", `/resources/reports/${id}?size=500${query}`)).body;
const near = (a: number, b: number, tol = 1e-9) => expect(Math.abs(a - b)).toBeLessThanOrEqual(tol * Math.max(1, Math.abs(a), Math.abs(b)));

test.beforeAll(async () => {
  test.setTimeout(LIMIT(300_000));
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-resources-"));
  ({ app, page } = await launch({ BF_E2E: "1" }));
  await setWindowSize(app, 1024, 640); // as small as on the Windows runner
  page.on("pageerror", (e) => console.log(`PAGEERROR ${e.message}\n${e.stack}`));
  await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
  await page.getByLabel("Лента").getByRole("button", { name: "Новый проект" }).click();
  await page.getByRole("dialog").last().getByRole("textbox").fill("Ресурсы Э20");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("project-selector")).toContainText("Ресурсы Э20", { timeout: 60_000 });
  await waitFrame(page);
  expect((await runCommand(page, "ribbon.toggle")).ok).toBe(true);
  const t = Date.now();
  ids = await build(page, dir);
  const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
  await expect(tree.getByTestId(`tree-block-${ids.model}`)).toBeAttached({ timeout: 30_000 });
  await estimateOk();
  await estimateIdw();
  const fields = (await blocksList(page)).find((m) => m.id === ids.model)!.fields.map((f) => f.name);
  expect(fields).toEqual(expect.arrayContaining([OK, IDW, "DENS", "DOMAIN"]));
  expect((await rowsList(page)).map((r) => r.prefix).sort()).toEqual(["AU_IDW_", "AU_OK_"]);
  console.log(`resources: the project and two estimations are built in ${((Date.now() - t) / 1000).toFixed(1)} s`);
});

test.afterAll(async () => {
  if (app) await close(app);
  if (dir) fs.rmSync(dir, { recursive: true, force: true });
});

test("1. the report: Ctrl+K → the window → «Сохранить» → the panel «Ресурсы» (the form, the table, the curve, the control) → XLSX", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  await palette(page, "Отчёт по ресурсам");
  const win = page.getByTestId("res-window");
  await expect(win).toBeVisible({ timeout: 30_000 });
  await expect(win.getByTestId("res-idle")).toBeVisible();

  // the model → its fields; the element AU_OK_EST in g/t; the cut-offs; the balance; the density from the field DENS; the key «Уступ» (H = 10 m)
  await win.getByTestId("res-model").selectOption(String(ids.model));
  await expect(win.getByTestId(`res-element-${OK}`)).toBeVisible({ timeout: 30_000 });
  await win.getByTestId(`res-element-${OK}`).check();
  await win.getByTestId(`res-unit-${OK}`).fill("г/т");
  await win.getByTestId("res-cutoffs").fill("без борта; 1,5; 2");
  await win.getByTestId("res-balance-on").check();
  await win.getByTestId("res-balance-cutoff").fill("1,5");
  await win.getByTestId("res-balance-off").fill("1");
  await win.getByTestId("res-density-field-on").check();
  await win.getByTestId("res-density-field").selectOption("DENS");
  await win.getByTestId("res-key-available-bench-add").click();
  await expect(win.getByTestId("res-key-chosen-bench")).toBeVisible();
  await win.getByTestId("res-bench-h").fill("10");
  await win.getByTestId("res-template").selectOption("jorc");
  await win.getByTestId("res-name").fill(REPORT);

  // «Сохранить» = the command `resources.report` with save → the job → the object «Отчёт по ресурсам»
  await win.getByTestId("res-save").click();
  await expect(win.getByTestId("res-summary")).toContainText("записан в проект", { timeout: LIMIT(60_000) });
  await expect(win.getByTestId("res-total")).toContainText("блоков");
  await expect(win.getByTestId(`res-element-total-${OK}`)).toContainText("г/т");
  const listed = (await coreApi<{ reports: { id: number; name: string }[] }>(page, "GET", "/resources/reports")).body.reports;
  expect(listed.map((r) => r.name)).toEqual([REPORT]);
  reportId = listed[0].id;
  const saved = await report(reportId);
  expect(saved.params.template_id).toBe("jorc");
  expect(saved.form).not.toBeNull();
  expect(saved.curve!.cutoffs.length).toBe(100);
  await expect(page.getByTestId(`tree-resource-report-${reportId}`)).toBeAttached({ timeout: 30_000 }); // the tree of the project follows the events

  // «Показать в панели «Ресурсы»»: the bottom tab; the window is closed so that it does not cover the panel
  await win.getByTestId("res-show-panel").click();
  await win.getByTestId("res-close").click();
  await expect(win).toHaveCount(0);
  const panel = page.getByTestId("res-panel");
  await expect(panel).toBeVisible({ timeout: 30_000 });
  await raiseDock(220);
  await expect(panel.getByTestId("res-panel-title")).toContainText(REPORT);
  await expect(panel).toHaveAttribute("data-saved", "true");
  // the form of the template: the cells are the text of the core
  await panel.getByTestId("res-tab-form").click();
  await expect(panel.getByTestId("res-form-table")).toHaveAttribute("data-template", "jorc");
  expect(await panel.locator('[data-testid^="res-form-row-"]').count()).toBeGreaterThan(0);
  // the table: the rows by the benches and the cut-offs; «без борта» of a bench is the cell of scenario 2
  await panel.getByTestId("res-tab-table").click();
  await expect(panel.getByTestId("res-rows-table")).toBeVisible();
  await expect(panel.getByTestId("res-table-count")).toContainText(`Строк: ${saved.rows_total}`);
  // the curve and the control
  await panel.getByTestId("res-tab-curve").click();
  await expect(panel.getByTestId("res-curve")).toHaveAttribute("data-points", "100");
  await panel.getByTestId("res-tab-control").click();
  await expect(panel.getByTestId("res-control")).toBeVisible();
  await expect(panel.getByTestId("res-control-sum-ok")).toBeVisible();

  // «Сохранить XLSX…»: the file of our own save window (answered at once), the command `resources.export` writes it through the core
  const xlsx = path.join(dir, "ресурсы.xlsx");
  await answerSaveDialog(app, xlsx);
  await panel.getByTestId("res-export-xlsx").click();
  await expect.poll(() => (fs.existsSync(xlsx) ? fs.statSync(xlsx).size : 0), { timeout: LIMIT(30_000) }).toBeGreaterThan(2000);
  expect(fs.readFileSync(xlsx).subarray(0, 2).toString("latin1")).toBe("PK");
  await expect(panel.getByTestId("res-panel-note")).toContainText("записан", { timeout: LIMIT(30_000) });
  const entries = unzip(xlsx);
  expect([...entries.keys()]).toEqual(expect.arrayContaining(["[Content_Types].xml", "xl/workbook.xml"]));
  const sheets = [...entries.get("xl/workbook.xml")!.toString("utf8").matchAll(/<sheet [^>]*name="([^"]+)"/g)].map((m) => m[1]);
  expect(sheets).toEqual(expect.arrayContaining(["Отчёт", "Таблица", "Кривая", "Контроль", "Параметры"]));
  const strings = entries.get("xl/sharedStrings.xml")?.toString("utf8") ?? "";
  expect(strings).toContain("Итого");
  // the XLSX is not half written: no `.part` file is left
  expect(fs.readdirSync(dir).filter((f) => f.endsWith(".part"))).toEqual([]);

  // a bench row «без борта» that has blocks and a grade: the cell of scenario 2 (the order of the table is the order of the core)
  const i = saved.rows.findIndex((r) => !r.total && r.kind === "all" && r.blocks > 0 && r.elements[OK]?.grade !== null && r.elements[OK]?.grade !== undefined);
  expect(i, JSON.stringify(saved.rows.map((r) => [r.g, r.kind, r.blocks]))).toBeGreaterThanOrEqual(0);
  benchRow = i;
  await page.screenshot({ path: shot("resources-report.png") });
  const took = Date.now() - started;
  console.log(`resources 1: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

test("2. ТД36-1: a cell → «Переход к блокам» (= the cell) → a block → the inspector of Д-30 → a probe → the interval of 7.17; again — the same set", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  const saved = await report(reportId);
  const row = saved.rows[benchRow];
  const body = { group: row.g, cutoff: null, kind: "all" };
  // the numbers of the core: the cell, and the sums of the blocks of the cell (ТД36-1: 1e-12)
  const cell = (await coreApi<{ cell: { blocks: number; volume: number; tonnes: number }; stale: boolean }>(page, "POST", `/resources/reports/${reportId}/drill`, body)).body;
  expect(cell.stale).toBe(false);
  expect(cell.cell.blocks).toBe(row.blocks);
  near(cell.cell.volume, row.volume!, 1e-12);
  near(cell.cell.tonnes, row.tonnes!, 1e-12);
  const rows = (await coreApi<DrillRows>(page, "POST", `/resources/reports/${reportId}/drill/rows`, { ...body, page: 1, size: 1000 })).body;
  expect(rows.total).toBe(row.blocks);
  near(rows.rows.reduce((s, r) => s + (r.volume ?? 0), 0), cell.cell.volume, 1e-12);
  near(rows.rows.reduce((s, r) => s + (r.tonnes ?? 0), 0), cell.cell.tonnes, 1e-12);
  const withGrade = rows.rows.findIndex((r) => r.elements[OK] !== null && r.elements[OK] !== undefined);
  expect(withGrade).toBeGreaterThanOrEqual(0);

  // the click on a cell of the table of the panel (the volume of the row)
  expect((await runCommand(page, "resources.open", { report_id: reportId })).ok).toBe(true);
  const panel = page.getByTestId("res-panel");
  await panel.getByTestId("res-tab-table").click();
  const cellText = async () => ({
    blocks: await panel.getByTestId(`res-row-${benchRow}-blocks`).innerText(),
    volume: await panel.getByTestId(`res-row-${benchRow}-volume`).innerText(),
    tonnes: await panel.getByTestId(`res-row-${benchRow}-tonnes`).innerText(),
  });
  const inTable = await cellText();
  await clickCell(panel.getByTestId(`res-row-${benchRow}-volume`));
  const drill = page.getByTestId("drill-window");
  await expect(drill).toBeVisible({ timeout: 30_000 });
  await expect(drill.getByTestId("drill-facts")).toBeVisible({ timeout: 30_000 });
  // the window shows the numbers of the cell, the same text as the cell of the table
  await expect(drill.getByTestId("drill-blocks")).toHaveText(inTable.blocks);
  await expect(drill.getByTestId("drill-volume")).toHaveText(inTable.volume);
  await expect(drill.getByTestId("drill-tonnes")).toHaveText(inTable.tonnes);
  await expect(drill.getByTestId("drill-stale")).toHaveCount(0);
  await expect(drill.getByTestId("drill-table")).toBeVisible({ timeout: 30_000 });
  const shown = drill.getByTestId("drill-table").locator("tbody tr");
  await expect(shown).toHaveCount(row.blocks);
  const first = await drill.getByTestId("drill-table").innerText();
  // the 3D shows only the blocks of the cell (the temporary filter), «Сбросить» takes it away, «Показать в 3D» puts it back
  await expect(drill.getByTestId("drill-reset")).toBeVisible();
  await drill.getByTestId("drill-reset").click();
  await expect(drill.getByTestId("drill-apply")).toBeVisible();
  await drill.getByTestId("drill-apply").click();
  await expect(drill.getByTestId("drill-reset")).toBeVisible();

  // the same cell again (the command of the registry, as Ctrl+K and the assistant run it) → the same set of the blocks
  await drill.getByTestId("drill-reset").click();
  const again = await coreApi<DrillRows>(page, "POST", `/resources/reports/${reportId}/drill/rows`, { ...body, page: 1, size: 1000 });
  expect(again.body.rows.map((r) => [r.i, r.j, r.k])).toEqual(rows.rows.map((r) => [r.i, r.j, r.k]));
  expect(again.body.cell).toEqual(rows.cell);
  const repeated = await runCommand(page, "resources.drill", { report_id: reportId, group: row.g.join("; ") });
  expect(repeated.ok, repeated.result).toBe(true);
  await expect(drill.getByTestId("drill-tonnes")).toHaveText(inTable.tonnes);
  await expect(drill.getByTestId("drill-table").locator("tbody tr")).toHaveCount(row.blocks);
  expect(await drill.getByTestId("drill-table").innerText()).toBe(first);

  // a block of the table → the inspector of Д-30 (the probes of the estimate) → a probe → «Данные по скважинам» (7.17)
  await drill.getByTestId(`drill-inspect-${withGrade}`).click();
  const inspector = page.getByTestId("estimate-inspect");
  await expect(inspector).toBeVisible({ timeout: LIMIT(30_000) });
  await expect(inspector).toHaveAttribute("data-state", "ready", { timeout: LIMIT(30_000) });
  const taken = inspector.locator('[data-testid^="inspect-row-"][data-selected="true"]');
  expect(await taken.count()).toBeGreaterThan(0);
  const probe = (await taken.first().getAttribute("data-testid"))!.replace("inspect-row-", "");
  await inspector.getByTestId(`inspect-interval-${probe}`).click();
  await expect(page.getByRole("dialog", { name: /Данные по скважинам/ })).toBeVisible({ timeout: LIMIT(30_000) });
  await expect(inspector.getByTestId("inspect-note")).toContainText("Открыто «Данные по скважинам»");
  await closeWindows();
  await expect(page.getByTestId("drill-reset")).toHaveCount(0); // the temporary filter of the 3D goes with the window

  const took = Date.now() - started;
  console.log(`resources 2: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

test("3. the template ГКЗ: «Шаблоны форм отчёта» (the sample) and the form of the saved report in ГКЗ — «Балансовые» / «Забалансовые»", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  await palette(page, "Шаблоны форм");
  const win = page.getByTestId("tpl-window");
  await expect(win).toBeVisible({ timeout: 30_000 });
  // JORC and ГКЗ are the templates of the box: the list, the sample of the form by the order of the rows
  await expect(win.getByTestId("tpl-item-jorc")).toBeVisible({ timeout: 30_000 });
  await win.getByTestId("tpl-item-gkz").click();
  await expect(win.getByTestId("tpl-builtin")).toBeVisible({ timeout: 30_000 });
  await expect(win.getByTestId("tpl-save")).toBeDisabled(); // a template of the box is viewed and copied, not changed
  const sample = win.getByTestId("tpl-preview");
  await expect(sample).toContainText("Балансовые");
  await expect(sample).toContainText("Забалансовые");
  for (const label of ["A", "B", "C1", "C2", "Итого"]) await expect(sample).toContainText(label);
  await win.getByTestId("tpl-close").click();
  await expect(win).toHaveCount(0);

  // the form of the saved report in ГКЗ: the core makes it again (a template is the show of the report: nothing is recalculated)
  expect((await runCommand(page, "resources.open", { report_id: reportId })).ok).toBe(true);
  const panel = page.getByTestId("res-panel");
  await panel.getByTestId("res-tab-form").click();
  await panel.getByTestId("res-template").selectOption("gkz");
  const table = panel.getByTestId("res-form-table");
  await expect(table).toHaveAttribute("data-template", "gkz", { timeout: 30_000 });
  await expect(table).toContainText("Балансовые");
  await expect(table).toContainText("Забалансовые");
  const gkz = (await coreApi<{ form: { columns: { group?: string | null }[]; sections: { rows: unknown[] }[] } }>(page, "GET", `/resources/reports/${reportId}?template=gkz&size=1`)).body.form;
  expect(new Set(gkz.columns.map((c) => c.group))).toEqual(new Set(["Балансовые", "Забалансовые"]));
  const rowsOfForm = gkz.sections.reduce((n, s) => n + s.rows.length, 0);
  expect(rowsOfForm).toBeGreaterThan(0);
  expect(await panel.locator('[data-testid^="res-form-row-"]').count()).toBe(rowsOfForm);
  // the report itself is as it was: not stale (the form is not an input of the report), the template of the report is the same
  expect((await report(reportId)).stale).toBe(false);
  await panel.getByTestId("res-template").selectOption("jorc");
  await expect(table).toHaveAttribute("data-template", "jorc", { timeout: 30_000 });

  const took = Date.now() - started;
  console.log(`resources 3: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

test("4. the comparison of the reports (A against A — 0; A against a report with another density) and of the estimations (two fields EST → Δ, the domains)", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  // the second report: the same model, the same rows, a constant density — only the tonnes (and the metal) differ
  const made = await runCommand(page, "resources.report", { model_id: ids.model, elements: `${OK}:г/т`, cutoffs: "без борта; 1,5; 2", density: "2,7", groups: "уступ", bench_h: 10, template_id: "jorc", save: true, name: REPORT_B });
  expect(made.ok, made.result).toBe(true);
  const listed = (await coreApi<{ reports: { id: number; name: string }[] }>(page, "GET", "/resources/reports")).body.reports;
  reportB = listed.find((r) => r.name === REPORT_B)!.id;
  expect(reportB).toBeGreaterThan(0);

  // A against A through the core: every difference is 0, no row is only in one report
  const same = (await coreApi<Compared>(page, "POST", "/resources/compare", { report_a: reportId, report_b: reportId })).body;
  expect(same.rows.length).toBeGreaterThan(0);
  for (const r of same.rows) {
    expect(r.only_in).toBeNull();
    expect(r.delta!.blocks).toBe(0);
    expect(r.delta!.volume).toBe(0);
    expect(r.delta!.tonnes).toBe(0);
    // a row where no block has the grade (the domain 2 is not estimated) has no difference of the grade: «нет значения», not 0
    for (const [name, e] of Object.entries(r.delta!.elements)) expect([e.grade, e.metal]).toEqual(r.a!.elements[name].grade === null ? [null, null] : [0, 0]);
  }
  // the window: A = A → «Только различия» leaves no rows; A against B → the rows that differ
  await palette(page, "Сравнить отчёты", "Сравнить отчёты");
  const win = page.getByTestId("cmp-window");
  await expect(win).toBeVisible({ timeout: 30_000 });
  await expect(win.locator(`[data-testid="cmp-a"] option[value="${reportB}"]`)).toBeAttached({ timeout: 30_000 });
  await win.getByTestId("cmp-a").selectOption(String(reportId));
  await win.getByTestId("cmp-b").selectOption(String(reportId));
  await win.getByTestId("cmp-run").click();
  await expect(win.getByTestId("cmp-table")).toBeVisible({ timeout: LIMIT(30_000) });
  expect(Number(await win.getByTestId("cmp-table").getAttribute("data-rows"))).toBe(same.rows.length);
  await expect(win.getByTestId("cmp-only")).toHaveCount(0);
  await expect(win.getByTestId("cmp-grids-differ")).toHaveCount(0);
  await win.getByTestId("cmp-only-diff").check();
  await expect(win.getByTestId("cmp-no-rows")).toBeVisible();
  await win.getByTestId("cmp-b").selectOption(String(reportB));
  await win.getByTestId("cmp-run").click();
  await expect(win.getByTestId("cmp-table")).toBeVisible({ timeout: LIMIT(30_000) });
  await expect(win.getByTestId("cmp-no-rows")).toHaveCount(0, { timeout: LIMIT(30_000) });
  expect(Number(await win.getByTestId("cmp-table").getAttribute("data-rows"))).toBeGreaterThan(0);
  const differ = (await coreApi<Compared>(page, "POST", "/resources/compare", { report_a: reportId, report_b: reportB })).body;
  expect(differ.rows.some((r) => r.delta && r.delta.tonnes !== 0)).toBe(true);
  // the rows of both reports differ in the tonnes only (the density does not change the volume); the balance rows are in A only — a gap, not 0
  const both = differ.rows.filter((r) => r.only_in === null);
  expect(both.length).toBeGreaterThan(0);
  expect(both.every((r) => r.delta!.volume === 0 && r.delta!.blocks === 0)).toBe(true);
  const onlyA = differ.rows.filter((r) => r.only_in === "a");
  expect(onlyA.length).toBeGreaterThan(0);
  expect(onlyA.every((r) => r.delta === null)).toBe(true);
  expect(differ.warnings.map((w) => w.code)).toContain("density_differs");
  await expect(win.getByTestId("cmp-only").first()).toBeVisible();
  await expect(win.getByTestId("cmp-warnings")).toBeVisible();
  await win.getByTestId("cmp-close").click();
  await expect(win).toHaveCount(0);

  // the comparison of the estimations: the field Δ = A − B is written into the model; the statistics, the histogram, the domains
  await palette(page, "Сравнить оценки");
  const est = page.getByTestId("ecmp-window");
  await expect(est).toBeVisible({ timeout: 30_000 });
  await est.getByTestId("ecmp-model").selectOption(String(ids.model));
  await expect(est.locator(`[data-testid="ecmp-a"] option[value="${OK}"]`)).toBeAttached({ timeout: 30_000 });
  await est.getByTestId("ecmp-a").selectOption(OK);
  await est.getByTestId("ecmp-b").selectOption(IDW);
  const before = await lastJobId(page, "estimate.compare");
  await est.getByTestId("ecmp-run").click();
  const job = await lastJob(page, "estimate.compare", LIMIT(60_000), before);
  const result = job.result as { field: string; field_written: boolean; stats: { n: number }; domains: { blocks: number; mean_a: number | null; mean_b: number | null; mean_delta: number | null }[] };
  expect(result.field_written).toBe(true);
  expect(result.field).toBe(`${OK}_${IDW}_D`);
  expect(result.stats.n).toBeGreaterThan(0);
  await expect(est.getByTestId("ecmp-stats")).toBeVisible({ timeout: LIMIT(30_000) });
  await expect(est.getByTestId("ecmp-hist")).toBeVisible();
  await expect(est.getByTestId("ecmp-domains")).toHaveAttribute("data-rows", String(result.domains.length));
  expect(result.domains.length).toBeGreaterThan(0);
  // the mean of Δ of a domain is the difference of the means of A and B (the weights are the volumes of the same blocks)
  for (const d of result.domains) if (d.mean_a !== null && d.mean_b !== null && d.mean_delta !== null) near(d.mean_delta, d.mean_a - d.mean_b, 1e-9);
  const model = (await blocksList(page)).find((m) => m.id === ids.model)!;
  expect(model.fields.map((f) => f.name)).toContain(result.field);
  await est.getByTestId("ecmp-close").click();
  await expect(est).toHaveCount(0);

  const took = Date.now() - started;
  console.log(`resources 4: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

test("5. Ctrl+K finds the commands of the resources (and runs one); the ribbon: «Настройки проекта» → «Ресурсы»", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  const palette5 = page.getByLabel("Палитра команд");
  // The palette is the ribbon: a command without a button («Показать отчёт по ресурсам», «Переименовать…», «Ячейка отчёта → блоки» — the tree, the click on a cell, the
  // assistant) is not in it; they run through the registry (scenarios 1–3). The palette lists a command by the label of its button of the ribbon with the tab and the group («Шаблоны форм — Настройки проекта › Ресурсы»), else by its title
  const find = async (query: string, title: RegExp) => {
    await page.keyboard.press("Control+KeyK");
    await page.getByLabel("Поиск команды").fill(query);
    const option = palette5.getByRole("option").filter({ hasText: title }).first();
    await expect(option, `Ctrl+K «${query}»`).toBeVisible({ timeout: 10_000 });
    await expect(option).not.toHaveAttribute("aria-disabled", "true");
    return option;
  };
  const commands: [string, RegExp][] = [
    ["Отчёт по ресурсам", /Отчёт по ресурсам/],
    ["Кривая", /Кривая.*Ресурсы/],
    ["Шаблоны форм", /Шаблоны форм.*Ресурсы/],
    ["Экспорт отчёта", /Экспорт отчёта.*Ресурсы/],
    ["Удалить отчёт", /Удалить отчёт.*Ресурсы/],
    ["Сравнить отчёты", /Сравнить отчёты.*Ресурсы/],
    ["Сравнить оценки", /Сравнить оценки/],
  ];
  for (const [query, title] of commands) {
    await find(query, title);
    await page.keyboard.press("Escape");
    await expect(palette5).toHaveCount(0);
  }
  // one of them runs from the palette: the window of the templates
  const option = await find("Шаблоны форм", /Шаблоны форм.*Ресурсы/);
  await option.click();
  await expect(palette5).toHaveCount(0);
  const tpl = page.getByTestId("tpl-window");
  await expect(tpl).toBeVisible({ timeout: 30_000 });
  await tpl.getByTestId("tpl-close").click();
  await expect(tpl).toHaveCount(0);

  // the ribbon unfolded: the group «Ресурсы» of the tab «Настройки проекта»
  expect((await runCommand(page, "ribbon.toggle")).ok).toBe(true);
  const ribbon = page.getByLabel("Лента");
  await ribbon.getByRole("tab", { name: "Настройки проекта" }).click();
  for (const label of ["Отчёт по ресурсам", "Шаблоны форм", "Сравнить отчёты"]) await expect(ribbon.getByRole("button", { name: label, exact: true }), `the button «${label}»`).toBeVisible({ timeout: 10_000 });
  expect((await runCommand(page, "ribbon.toggle")).ok).toBe(true);

  const took = Date.now() - started;
  console.log(`resources 5: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});
