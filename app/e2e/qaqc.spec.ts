// Э14, подшаг 14.23 (ТЗ 8.4; ADR 0020 §9–11): QAQC end to end in a new empty project (the demo is not changed).
//  1. 400 control records of a small CSV (written here) through the import of the core: the series of Т8.4-1 (CRM
//     «OREAS 45», LAB1), the blanks of Т8.4-2 (DL = 0,01, k_б = 5), 60 field duplicates, a second laboratory, batches;
//     the CRM reference and the DL — through the core.
//  2. «Анализ → QAQC → Стандарты» → the tab; the statuses of the series on the control chart equal Т8.4-1 and the
//     answer of the core.
//  3. A filter by laboratory changes «записей N из M» (N from the core).
//  4. «Дубликаты»: the share of HARD within the threshold of the type equals the core.
//  5. «Отчёт → Сформировать»: the run ends, the charts are sent, the report is in «Отчёты» of the tree, its XLSX is
//     saved through our «Сохранить XLSX…» (Т8.4-8 — the file).
//  6. `Ctrl+K` finds «QAQC».
// A window of 1024 × 640 with the ribbon folded; the data and the waits — through the core; the limits on the .exe ×3.
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { answerSaveDialog, close, coreApi, importQaqc, launch, runCommand, setWindowSize, shot } from "./helpers";

const EXE = !!process.env.BF_APP_EXE;
const LIMIT = (ms: number) => (EXE ? 3 * ms : ms);

// Т8.4-1: μ = 1,00, σ = 0,05 → предупреждение; отказ (две подряд); отказ; отказ; норма; 9 × 1,01 — серия на 9-й
const T84_1 = [1.11, 1.12, 0.84, 1.16, 0.95, ...Array<number>(9).fill(1.01)];
const T84_1_STATUS = [1, 2, 2, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1];
// Т8.4-2: DL = 0,01, k_б = 5 → норма, норма, отказ
const T84_2 = [0.04, 0.05, 0.06];
const PAIRS = 60;
const ROWS = 400;

const day = (i: number) => new Date(Date.UTC(2024, 0, 1 + i)).toISOString().slice(0, 10);

/** The CSV of the control samples: 400 records, AU in ppm. */
function writeCsv(file: string): { lab1: number } {
  const rows: string[] = ["SAMPLE_ID;HOLE;TYPE;PARENT_ID;CRM_ID;LAB;BATCH;DATE;SEQ;AU_ppm"];
  let n = 0;
  let lab1 = 0;
  const add = (type: string, lab: string, batch: string, date: string, seq: number, au: number, crm = "", parent = "") => {
    const id = `S${String(++n).padStart(4, "0")}`;
    rows.push([id, `DH${String(1 + (n % 30)).padStart(3, "0")}`, type, parent, crm, lab, batch, date, seq, String(Math.round(au * 1e6) / 1e6)].join(";"));
    if (lab === "LAB1") lab1++;
    return id;
  };
  // Т8.4-1 — one CRM a batch day, LAB1
  T84_1.forEach((x, i) => add("CRM", "LAB1", `B${String(i + 1).padStart(2, "0")}`, day(i), 10, x, "OREAS 45"));
  // Т8.4-2 — a sample, then the blank in batches B01…B03
  T84_2.forEach((x, i) => {
    const b = `B${String(i + 1).padStart(2, "0")}`;
    add("ORIG", "LAB1", b, day(i), 1, 0.8 + i);
    add("BLANK", "LAB1", b, day(i), 2, x);
  });
  // 60 field duplicates in LAB1: 54 within 2,4 % HARD, 6 at 33 % (beyond the 30 % of DUP_F) → 90 % of the pairs
  for (let i = 0; i < PAIRS; i++) {
    const b = `B${String(15 + Math.floor(i / 10)).padStart(2, "0")}`;
    const v = 0.5 + i * 0.05;
    const parent = add("ORIG", "LAB1", b, day(20 + Math.floor(i / 10)), 3 + (i % 10) * 2, v);
    add("DUP_F", "LAB1", b, day(20 + Math.floor(i / 10)), 4 + (i % 10) * 2, v * (i < 54 ? 1.05 : 2), "", parent);
  }
  // LAB2: 40 CRM near μ, 20 blanks below DL, the rest ordinary samples
  for (let i = 0; i < 40; i++) add("CRM", "LAB2", `L${String(1 + Math.floor(i / 4)).padStart(2, "0")}`, day(40 + i), 1, 1 + 0.02 * Math.sin(i), "OREAS 45");
  for (let i = 0; i < 20; i++) add("BLANK", "LAB2", `L${String(1 + Math.floor(i / 2)).padStart(2, "0")}`, day(40 + i), 2, 0.005);
  while (n < ROWS) add("ORIG", "LAB2", `L${String(1 + (n % 10)).padStart(2, "0")}`, day(40 + (n % 40)), 5 + (n % 20), 0.3 + (n % 17) * 0.11);
  fs.writeFileSync(file, `${rows.join("\n")}\n`, "utf8");
  return { lab1 };
}

type Calc = { counts: { rows: number; filtered: number }; groups?: { crm: string; lab: string }[]; series?: { status: number[] } | null; share_at_limit?: number | null };
const calc = async (page: Page, section: string, body: Record<string, unknown>) => {
  const r = await coreApi<Calc>(page, "POST", `/qaqc/calc/${section}`, body);
  expect(r.status, JSON.stringify(r.body)).toBe(200);
  return r.body;
};
const pct = (v: number) => `${v.toFixed(1).replace(".", ",")} %`;

test("QAQC: a CSV through the core → «Стандарты» with the statuses of Т8.4-1 → a filter by laboratory → «Дубликаты» HARD → «Отчёт» in the tree and its XLSX saved → Ctrl+K", async () => {
  test.setTimeout(LIMIT(90_000));
  const t0 = Date.now();
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-qaqc-"));
  const csv = path.join(dir, "контроль QAQC.csv");
  const xlsx = path.join(dir, "отчёт QAQC.xlsx");
  const { lab1 } = writeCsv(csv);
  const { app, page } = await launch({ BF_E2E: "1" });
  try {
    await setWindowSize(app, 1024, 640);
    page.on("pageerror", (e) => console.log(`PAGEERROR ${e.message}\n${e.stack}`));
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: LIMIT(60_000) });
    await page.getByLabel("Лента").getByRole("button", { name: "Новый проект" }).click();
    await page.getByRole("dialog").last().getByRole("textbox").fill("Контроль QAQC");
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("project-selector")).toContainText("Контроль QAQC", { timeout: LIMIT(30_000) });
    expect((await runCommand(page, "ribbon.toggle")).ok).toBe(true);

    // ---- 1. the reference, the DL and the records through the core ------------------------------------------------
    const crm = (await coreApi<{ version: number }>(page, "GET", "/qaqc/crm")).body;
    const putCrm = await coreApi(page, "PUT", "/qaqc/crm", { rows: [{ crm_id: "OREAS 45", element: "AU", mean: 1.0, sd: 0.05, unit: "ppm", source: "e2e" }], expected_version: crm.version });
    expect(putCrm.status, JSON.stringify(putCrm.body)).toBe(200);
    const cfg = (await coreApi<{ version: number; types: unknown; limits: Record<string, number>; empty_is_orig: boolean }>(page, "GET", "/qaqc/config")).body;
    expect(cfg.limits.k_blank).toBe(5);
    const putCfg = await coreApi(page, "PUT", "/qaqc/config", { types: cfg.types, dl: [{ element: "AU", lab: null, value: 0.01, unit: "ppm" }], limits: cfg.limits, empty_is_orig: cfg.empty_is_orig, expected_version: cfg.version });
    expect(putCfg.status, JSON.stringify(putCfg.body)).toBe(200);
    const dataId = await importQaqc(page, csv, "Контроль e2e", LIMIT(60_000));
    const t1 = Date.now();

    // ---- 2. «Анализ → QAQC → Стандарты»: Т8.4-1 on the chart ---------------------------------------------------------
    await page.getByRole("tab", { name: "Анализ" }).click();
    await page.getByRole("button", { name: "Стандарты", exact: true }).click();
    const tab = page.getByTestId("qaqc-tab");
    await expect(tab).toBeVisible({ timeout: LIMIT(20_000) });
    const groups = tab.getByRole("table", { name: "Группы стандартов" });
    await groups.getByRole("row").filter({ hasText: "LAB1" }).click();
    const want = await calc(page, "standards", { data_id: dataId, element: "AU", group: { crm: "OREAS 45", lab: "LAB1" } });
    expect(want.series?.status).toEqual(T84_1_STATUS);
    const points = tab.getByTestId("qaqc-control").locator("circle[data-status]");
    await expect(points).toHaveCount(T84_1.length, { timeout: LIMIT(15_000) });
    // the points are drawn in the paint order (the norm first, the failures on top): the same statuses, sorted
    expect(await points.evaluateAll((els) => els.map((e) => Number(e.getAttribute("data-status"))))).toEqual([...T84_1_STATUS].sort((x, y) => x - y));
    await expect(tab.getByTestId("qaqc-control")).toContainText("отказ: 3");
    await page.screenshot({ path: shot("qaqc-standards.png") });

    // ---- 3. a filter by laboratory: «записей N из M» -------------------------------------------------------------
    const counter = tab.getByTestId("qaqc-counter");
    await expect(counter).toContainText(`записей ${ROWS} из ${ROWS}`);
    await tab.getByLabel("Фильтры QAQC").getByRole("checkbox", { name: "LAB1" }).check();
    const filtered = await calc(page, "summary", { data_id: dataId, element: "AU", filter: { v: 1, labs: ["LAB1"] } });
    expect(filtered.counts.filtered).toBe(lab1);
    await expect(counter).toContainText(`записей ${lab1} из ${ROWS}`, { timeout: LIMIT(10_000) });

    // ---- 4. «Дубликаты»: the share of HARD within the threshold of DUP_F --------------------------------------------
    await tab.getByRole("tablist", { name: "Разделы QAQC" }).getByRole("tab", { name: "Дубликаты" }).click();
    const dup = await calc(page, "duplicates", { data_id: dataId, element: "AU", kind: "DUP_F", filter: { v: 1, labs: ["LAB1"] } });
    expect(dup.share_at_limit).toBeCloseTo(90, 9);
    const summary = tab.getByTestId("qaqc-dup-summary");
    await expect(summary.getByRole("row", { name: /порог типа/ })).toContainText(pct(dup.share_at_limit!), { timeout: LIMIT(10_000) });
    await expect(summary.getByRole("row", { name: /^Пар/ }).first()).toContainText(String(PAIRS));

    // ---- 5. «Отчёт → Сформировать» → the tree «Отчёты» → «Сохранить XLSX…» ---------------------------------------
    await tab.getByRole("tablist", { name: "Разделы QAQC" }).getByRole("tab", { name: "Отчёт" }).click();
    await tab.getByRole("button", { name: "Сформировать", exact: true }).click();
    const run = tab.getByTestId("qaqc-report-run");
    await expect(run).toHaveAttribute("data-state", "done", { timeout: LIMIT(30_000) });
    const reports = (await coreApi<{ items: { id: number; name: string; images: number; files: { kind: string }[]; filter: { labs?: string[] } }[] }>(page, "GET", "/qaqc/reports")).body.items;
    expect(reports).toHaveLength(1);
    const report = reports[0];
    expect(report.filter.labs).toEqual(["LAB1"]);
    expect(report.images).toBeGreaterThan(0);
    expect(report.files.map((f) => f.kind)).toContain("xlsx");
    await expect(page.getByTestId(`tree-qaqc-report-${report.id}`)).toBeVisible({ timeout: LIMIT(10_000) });
    await answerSaveDialog(app, xlsx);
    await run.getByRole("button", { name: "Сохранить XLSX…" }).click();
    await expect.poll(() => (fs.existsSync(xlsx) ? fs.statSync(xlsx).size : 0), { timeout: LIMIT(15_000) }).toBeGreaterThan(1000);
    expect(fs.readFileSync(xlsx).subarray(0, 2).toString("latin1")).toBe("PK");
    await page.screenshot({ path: shot("qaqc-report.png") });

    // ---- 6. Ctrl+K finds «QAQC» -----------------------------------------------------------------------------------
    await page.keyboard.press("Control+KeyK");
    await page.getByLabel("Поиск команды").fill("QAQC");
    await expect(page.getByLabel("Палитра команд").getByRole("option").first()).toContainText("QAQC");
    await page.keyboard.press("Escape");
    console.log(`qaqc.spec: launch + import ${((t1 - t0) / 1000).toFixed(1)} s, all ${((Date.now() - t0) / 1000).toFixed(1)} s`);
  } finally {
    await close(app);
  }
});
