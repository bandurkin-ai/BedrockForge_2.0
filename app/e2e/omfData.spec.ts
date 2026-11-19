// Э11, подшаг 11.19 (ТЗ 7.24, 7.35 Д-17; ADR 0017 §1–§5): the demo with its block model «Блочная модель Au»
// (core `demo/blocks.py`) — the row of the tree and «Свойства»; «Экспорт проекта › Объекты в файлы» into a folder
// (a folder with a subfolder per type, «Показать в папке» stubbed); «Экспорт OMF» of every wireframe, line file
// and the model → a file; a new project, the file through the wizard «Импорт файлов» into the folder «Участок 1»:
// the composition, then the counts (wireframes, layers, model, cells) equal those written; «Менеджер данных» moves
// the model out of the folder and back by dragging its row. One spec (CI economy of Э11): a window of 1024 × 640
// with the ribbon folded; the data and the waits for lists — through the core (the lesson of the races of Э10);
// the limits on the .exe are ×3.
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { answerOpenDialog, answerSaveDialog, close, coreApi, launch, runCommand, setWindowSize, shot, shownInFolder, stubShowInFolder, type CoreJob } from "./helpers";

const EXE = !!process.env.BF_APP_EXE;
const LIMIT = (ms: number) => (EXE ? 3 * ms : ms);
const DEMO_MODEL = "Блочная модель Au";
const CELLS = 40 * 40 * 20;
const FOLDER = "Участок 1";

type Bm = { id: number; name: string; folder: string | null; cells: number; fields: { name: string }[] };
type Wf = { id: number; name: string; folder: string | null };
type LineFile = { id: number; folder: string | null; layers: unknown[] };
type OmfResult = { path: string; wireframes: number; line_layers: number; block_models: number; cells: number };

/** What the project holds now, as the core lists it. */
async function counts(page: Page) {
  const [wf, lf, bm] = await Promise.all([
    coreApi<{ wireframes: Wf[] }>(page, "GET", "/wireframes"),
    coreApi<{ files: LineFile[] }>(page, "GET", "/lines/files"),
    coreApi<{ block_models: Bm[] }>(page, "GET", "/blocks"),
  ]);
  const files = lf.body.files;
  return {
    wireframes: wf.body.wireframes,
    files,
    layers: files.reduce((n, f) => n + f.layers.length, 0),
    models: bm.body.block_models,
  };
}

/** The last job of `kind` once it is over. */
async function lastJob(page: Page, kind: string, timeout: number): Promise<CoreJob> {
  let job: CoreJob | undefined;
  await expect
    .poll(
      async () => {
        const jobs = (await coreApi<{ jobs: CoreJob[] }>(page, "GET", "/jobs")).body.jobs.filter((j) => j.kind === kind);
        job = jobs.sort((a, b) => b.id - a.id)[0];
        return job?.status ?? "none";
      },
      { timeout, intervals: [200, 400, 800] },
    )
    .toMatch(/^(done|failed|cancelled)$/);
  expect(job!.status, JSON.stringify(job!.error)).toBe("done");
  return job!;
}

test("OMF and data: the block model of the demo, objects to files, OMF out and back through the wizard, the data manager", async () => {
  test.setTimeout(LIMIT(150_000));
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-omf-"));
  const { app, page } = await launch({ BF_E2E: "1" });
  try {
    await setWindowSize(app, 1024, 640); // as small as on the Windows runner
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await stubShowInFolder(app);
    const started = Date.now();
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    expect((await runCommand(page, "ribbon.toggle")).ok).toBe(true);

    // ---- the block model of the demo: the row of the tree and «Свойства» ------------------------------------
    const demo = await counts(page);
    expect(demo.models.map((b) => [b.name, b.cells, b.fields.map((f) => f.name)])).toEqual([[DEMO_MODEL, CELLS, ["AU", "DOMAIN"]]]);
    const bmId = demo.models[0].id;
    const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
    const row = tree.getByTestId(`tree-block-${bmId}`);
    await expect(row).toBeVisible({ timeout: 30_000 });
    await expect(tree.getByRole("treeitem", { name: /^Блочная модель Au, 40 × 40 × 20, 32\s000 ячеек, 2 поля$/ })).toBeVisible();
    await row.scrollIntoViewIfNeeded();
    await row.dblclick();
    const props = page.getByTestId("props-block");
    await expect(props).toHaveAttribute("data-state", "ready", { timeout: 30_000 });
    await expect(props.getByTestId("props-block-grid")).toContainText("40 × 40 × 20");
    await expect(props.getByTestId("props-block-grid")).toContainText("10 × 10 × 10 м");
    const fields = props.getByTestId("props-block-fields");
    await expect(fields).toHaveAttribute("data-fields", "2");
    const info = (await coreApi<{ fields: { name: string; stats: { missing: number } }[] }>(page, "GET", `/blocks/${bmId}`)).body;
    const missing = info.fields[0].stats.missing;
    expect(missing).toBeGreaterThan(0); // AU has gaps: «Пусто», never 0
    await expect(fields.getByRole("row").filter({ hasText: "AU" })).toContainText("г/т");
    await expect(fields.getByRole("row").filter({ hasText: "AU" }).getByRole("cell").nth(4)).toHaveText(new RegExp(`^${String(missing).replace(/\B(?=(\d{3})+$)/g, "\\s")}$`));
    await expect(fields.getByRole("row").filter({ hasText: "DOMAIN" })).toContainText("руда");
    await page.screenshot({ path: shot("omfData-props.png") });

    // ---- «Экспорт проекта › Объекты в файлы»: a folder with a subfolder per type -------------------------------
    const out = path.join(dir, "Выгрузка");
    fs.mkdirSync(out);
    expect((await runCommand(page, "project.export")).ok).toBe(true);
    const exp = page.getByRole("dialog", { name: "Экспорт проекта" });
    await exp.getByTestId("export-mode-objects").check();
    await expect(exp.getByTestId("export-type-block_model")).toContainText("1");
    await expect(exp.getByLabel("Формат: Блочные модели")).toHaveValue("omf");
    await answerOpenDialog(app, [out]);
    await exp.getByRole("button", { name: "Выбрать папку…" }).click();
    await expect(exp.getByTestId("export-objects-folder")).toHaveValue(out);
    await exp.getByTestId("export-objects-start").click();
    await expect(exp.getByTestId("export-objects-report")).toBeVisible({ timeout: LIMIT(60_000) });
    const made = fs.readdirSync(out);
    expect(made).toHaveLength(1); // one export folder, no temporary one left
    const folder = path.join(out, made[0]);
    expect(made[0]).toMatch(/^Демо_ участок Северный — экспорт \d{4}-\d{2}-\d{2}$/);
    expect(fs.readdirSync(folder)).toEqual(expect.arrayContaining(["Скважины", "Каркасы", "ЦТМ", "Линии", "Блочные модели"]));
    expect(fs.readdirSync(path.join(folder, "Блочные модели"))).toEqual([`${DEMO_MODEL}.omf`]);
    await exp.getByTestId("export-objects-show").click();
    await expect.poll(() => shownInFolder(app)).toEqual([folder]);
    await exp.getByTestId("project-export").getByRole("button", { name: "Закрыть", exact: true }).click();
    await expect(exp).toHaveCount(0);

    // ---- «Экспорт OMF»: every wireframe, line file and the model into one file ---------------------------------
    const offered = (await coreApi<{ wireframes: { available: boolean }[]; line_files: { available: boolean }[]; block_models: unknown[] }>(page, "GET", "/omf/exportable")).body;
    expect((await runCommand(page, "omf.export")).ok).toBe(true);
    const omfWin = page.getByRole("dialog", { name: "Экспорт OMF" });
    await expect(omfWin.getByTestId(`omf-export-row-${bmId}`)).toHaveAttribute("data-available", "yes", { timeout: 30_000 });
    // «Все» of each list: checked by the window (initialChosen) — `check()` keeps it so, or checks it
    for (const list of ["Каркасы и ЦТМ", "Файлы линий", "Блочные модели"]) await omfWin.getByLabel(`Все: ${list}`).check();
    await expect(omfWin.getByTestId(`omf-export-row-${bmId}`).getByRole("checkbox")).toBeChecked();
    await expect(omfWin.getByTestId("omf-export-chosen")).not.toBeEmpty();
    const omfFile = path.join(dir, "Демо.omf");
    await answerSaveDialog(app, omfFile);
    await omfWin.getByRole("button", { name: "Сохранить как…" }).click();
    await expect(omfWin.getByLabel("Файл OMF")).toHaveValue(omfFile);
    await omfWin.getByRole("button", { name: "Экспортировать" }).click();
    await expect(omfWin.getByTestId("omf-export-status")).toHaveAttribute("data-status", "done", { timeout: LIMIT(60_000) });
    const written = (await lastJob(page, "export.omf", 10_000)).result as unknown as OmfResult;
    expect(written.wireframes).toBe(offered.wireframes.filter((w) => w.available).length);
    expect(written.block_models).toBe(1);
    expect(written.cells).toBe(CELLS);
    expect(written.line_layers).toBeGreaterThan(0);
    await expect(omfWin.getByTestId("omf-export-result")).toContainText(`Записан Демо.omf: каркасов ${written.wireframes}`);
    expect(fs.statSync(omfFile).size).toBeGreaterThan(0);
    await omfWin.getByTestId("omf-export").getByRole("button", { name: "Закрыть", exact: true }).click();
    await expect(omfWin).toHaveCount(0);

    // ---- a new project: the file through «Импорт файлов» into «Участок 1» -------------------------------------
    expect((await runCommand(page, "project.new", { name: "Обратно из OMF" })).ok).toBe(true);
    await expect(page.getByTestId("project-selector")).toContainText("Обратно из OMF", { timeout: 30_000 });
    await expect.poll(async () => (await counts(page)).models.length + (await counts(page)).wireframes.length).toBe(0);
    await answerOpenDialog(app, [omfFile]);
    expect((await runCommand(page, "import.files", { kind: "omf", folder: FOLDER })).ok).toBe(true);
    const wiz = page.getByRole("dialog", { name: "Импорт файлов" });
    await expect(wiz.getByTestId("objects-folder")).toContainText(`«${FOLDER}»`);
    const omfCard = wiz.getByTestId("objects-omf");
    await expect(omfCard).toBeVisible({ timeout: 60_000 });
    const elements = omfCard.getByTestId("objects-omf-element");
    await expect(elements.first()).toBeVisible();
    expect(await elements.count()).toBeGreaterThanOrEqual(written.wireframes + 1);
    await expect(omfCard.locator('[data-testid="objects-omf-element"][data-loads="no"]')).toHaveCount(0);
    await expect(omfCard.getByRole("list", { name: /^Блочные модели / })).toContainText(DEMO_MODEL);
    await page.screenshot({ path: shot("omfData-wizard.png") });
    await wiz.getByRole("button", { name: "Импортировать" }).click();
    await expect(wiz).toHaveCount(0);
    const loaded = (await lastJob(page, "import.objects", LIMIT(60_000))).result as Record<string, number>;
    expect([loaded.wireframes, loaded.layers, loaded.block_models, loaded.cells]).toEqual([written.wireframes, written.line_layers, 1, CELLS]);
    // the lists of the core: the same counts, everything in «Участок 1»
    const back = await counts(page);
    expect([back.wireframes.length, back.layers, back.models.length, back.models[0].cells]).toEqual([written.wireframes, written.line_layers, 1, CELLS]);
    expect(back.models[0].name).toBe(DEMO_MODEL);
    // the folder of the wizard; the wireframes of a file — in a subfolder named after it (ADR 0013, decision А)
    expect([...back.files, ...back.models].map((o) => o.folder)).toEqual(Array(back.files.length + 1).fill(FOLDER));
    expect(back.wireframes.every((o) => o.folder === `${FOLDER}/Демо.omf`)).toBe(true);
    const newBm = back.models[0].id;
    const newRow = tree.getByTestId(`tree-block-${newBm}`);
    await expect(newRow).toBeAttached({ timeout: 30_000 });
    const inFolder = Number(await newRow.getAttribute("aria-level"));

    // ---- «Менеджер данных»: the model out of the folder and back, by dragging its row -------------------------
    expect((await runCommand(page, "data.manager")).ok).toBe(true);
    const dm = page.getByTestId("data-manager");
    const total = back.wireframes.length + back.files.length + 1;
    await expect(dm.getByTestId("dm-status")).toContainText(new RegExp(`Объектов: ${total}(\\D|$)`), { timeout: 30_000 });
    const folderNode = dm.getByTestId(`dm-folder-folder:${FOLDER}`);
    await expect(folderNode).toContainText(String(total));
    const modelFolder = async () => (await coreApi<Bm>(page, "GET", `/blocks/${newBm}`)).body.folder;
    await dm.getByTestId(`dm-row-${newBm}`).dragTo(dm.getByTestId("dm-folder-all"));
    await expect.poll(modelFolder, { timeout: 15_000 }).toBeNull();
    await expect(folderNode).toContainText(String(total - 1));
    await expect.poll(async () => Number(await newRow.getAttribute("aria-level")), { timeout: 15_000 }).toBeLessThan(inFolder);
    await dm.getByTestId(`dm-row-${newBm}`).dragTo(folderNode);
    await expect.poll(modelFolder, { timeout: 15_000 }).toBe(FOLDER);
    await expect(folderNode).toContainText(String(total));
    await expect.poll(async () => Number(await newRow.getAttribute("aria-level")), { timeout: 15_000 }).toBe(inFolder);
    await page.screenshot({ path: shot("omfData-manager.png") });

    const took = Date.now() - started;
    console.log(`omfData: ${(took / 1000).toFixed(1)} s`);
    expect(took).toBeLessThan(LIMIT(90_000));
  } finally {
    await close(app);
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
