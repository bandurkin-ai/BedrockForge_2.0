// Э17, подшаг 17.40 (ТЗ 7.35 Д-02, 8.7; ADR 0023 «CI»): the block models from a file to a report. A small synthetic DAT
// (24 × 24 × 12 = 6912 blocks) through the wizard «Импорт файлов» (the step «Блочная модель») → the model in 3D
// (`blocks.show`, `window.__bfBlocks`) → the filter «AU > x И DOMAIN = n» made in the window «Фильтры» → chosen in the
// window «Блочная модель · …» → only the blocks that pass are in 3D (Д-02: fewer faces, the core counts the same) →
// the calculator (a new field) → «Содержание–тоннаж» with the cut-off 0 → a slice → Ctrl+K finds the commands.
// The edges and colours are in the shader of software WebGL: they are read through the hooks and the state, never
// the pixels. A window of 1024 × 640 with the ribbon folded; ≤ 60 s under xvfb, ×1.5 on the .exe (ADR 0022 «CI»).
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { answerOpenDialog, close, coreApi, type CoreJob, launch, newEmptyProject, runCommand, setWindowSize, shot, syntheticBlocks, writeDatBlocks } from "./helpers";

const EXE = !!process.env.BF_APP_EXE;
const LIMIT = (ms: number) => (EXE ? 1.5 * ms : ms);
const NX = 24;
const NY = 24;
const NZ = 12;
const N = NX * NY * NZ;
const MODEL = "Рудное тело";
const FILTER = "Руда: AU > 2, домен 1";
const ore = syntheticBlocks(NX, NY, NZ);

/** «…: прошло 1 234 из 6 912 блоков» → [1234, 6912] (the groups of digits are told apart by spaces, not read as one). */
const passedOf = (text: string): number[] => {
  const m = /прошло\s+([\d\s\u00a0\u202f]+?)\s+из\s+([\d\s\u00a0\u202f]+?)\s+блок/.exec(text);
  return m ? [m[1], m[2]].map((t) => Number(t.replace(/\D/g, ""))) : [];
};

/** «1 234,5» / «−1 234» of the interface → a number. */
const ruNum = (text: string): number => Number(text.replace(/[\s\u00a0\u202f]/g, "").replace(",", ".").replace("−", "-"));

/** The faces of the shell of the blocks `keep` selects: a face of a selected block whose neighbour is not selected (or beyond the grid). */
function exposedFaces(m: { count: number }, nx: number, ny: number, nz: number, keep: (row: number) => boolean): number {
  const at = (i: number, j: number, k: number) => (i < 0 || j < 0 || k < 0 || i >= nx || j >= ny || k >= nz ? -1 : (k * ny + j) * nx + i);
  let faces = 0;
  for (let k = 0; k < nz; k++)
    for (let j = 0; j < ny; j++)
      for (let i = 0; i < nx; i++) {
        if (!keep(at(i, j, k))) continue;
        for (const [di, dj, dk] of [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]]) {
          const q = at(i + di, j + dj, k + dk);
          if (q < 0 || !keep(q)) faces++;
        }
      }
  return faces;
}

type Bm = { id: number; name: string; version: number; cells: number; fields: { name: string; role?: string | null }[] };
const blocksList = async (page: Page) => (await coreApi<{ block_models: Bm[] }>(page, "GET", "/blocks")).body.block_models;

type Shown = { key: string; model: number; version: number; mode: string; faces: number; facesRaw: number; simplified: boolean; legendRows: number; visible: boolean };
type Stats = { requests: number; superseded: number; restyles: number; layers: number };
/** The hook of the blocks of the 3D window (BF_E2E=1): `shown`, `stats`, `pick`, `click`, `styles`. */
const hook = <T>(page: Page, fn: string, ...args: unknown[]) =>
  page.evaluate(([f, a]) => {
    const h = (window as unknown as { __bfBlocks?: Record<string, (...x: unknown[]) => unknown> }).__bfBlocks;
    return (h ? h[f as string](...(a as unknown[])) : null) as never;
  }, [fn, args] as const) as Promise<T | null>;
const shown = async (page: Page) => (await hook<Shown[]>(page, "shown")) ?? [];
const stats = async (page: Page) => (await hook<Stats>(page, "stats")) ?? { requests: 0, superseded: 0, restyles: 0, layers: 0 };

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

test("block models: DAT import, 3D, filter, calculator, content–tonnage, slice, Ctrl+K", async () => {
  test.setTimeout(LIMIT(150_000));
  const started = Date.now(); // the whole file: the start of the app, the project, everything
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-blocks-"));
  const dat = writeDatBlocks(path.join(dir, "Рудное тело.dat"), ore);
  const { app, page } = await launch({ BF_E2E: "1" });
  try {
    await setWindowSize(app, 1024, 640);
    await newEmptyProject(page, "Блоки Э17");
    expect((await runCommand(page, "ribbon.toggle")).ok).toBe(true);

    // ---- the wizard: the step «Блочная модель» --------------------------------------------------------------
    await answerOpenDialog(app, [dat]);
    expect((await runCommand(page, "import.files", { kind: "blocks" })).ok).toBe(true);
    const wiz = page.getByRole("dialog", { name: "Импорт файлов" });
    const step = wiz.getByTestId("blocks-step");
    await expect(step).toBeVisible({ timeout: 60_000 });
    await expect(step.getByTestId("blocks-headline")).toContainText(/блоков 6\s912/);
    await expect(step.getByTestId("blocks-grid")).toBeVisible();
    await expect(step.getByTestId("blocks-check3")).toHaveAttribute("data-ok", "yes");
    // the roles are found by the names: AU is a grade, SG a density; the model is named
    await expect(step.getByLabel("Роль столбца AU")).toHaveValue("grade");
    await expect(step.getByLabel("Роль столбца SG")).toHaveValue("density");
    await step.getByLabel(/^Название модели/).fill(MODEL);
    await step.getByLabel(/^Название модели/).press("Enter");
    await expect(step.getByLabel(/^Название модели/)).toHaveValue(MODEL);
    await page.screenshot({ path: shot("blocks-wizard.png") });
    await wiz.getByRole("button", { name: "Импортировать" }).click();
    await expect(wiz).toHaveCount(0, { timeout: 30_000 });
    const loaded = await lastJob(page, "import.objects", LIMIT(60_000));
    expect(loaded.result).toMatchObject({ block_models: 1, cells: N });
    const model = (await blocksList(page))[0];
    expect([model.name, model.cells, model.fields.map((f) => f.name)]).toEqual([MODEL, N, ["AU", "DOMAIN", "SG"]]);
    const id = model.id;
    const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
    await expect(tree.getByTestId(`tree-block-${id}`)).toBeAttached({ timeout: 30_000 });

    // ---- the model in 3D ---------------------------------------------------------------------------------------
    expect(await shown(page)).toEqual([]);
    expect((await runCommand(page, "blocks.show", { model_id: id })).ok).toBe(true);
    await expect.poll(async () => (await shown(page)).length, { timeout: LIMIT(30_000) }).toBe(1);
    const all = (await shown(page))[0];
    expect([all.model, all.mode, all.visible, all.simplified]).toEqual([id, "shell", true, false]);
    // the outside of a box 24 × 24 × 12: 2 · (24·24 + 2·24·12) faces of cells, merged by the colour into 6 planes
    expect(all.facesRaw).toBe(2 * (NX * NY + 2 * NX * NZ));
    expect(all.faces).toBeLessThanOrEqual(all.facesRaw);
    await page.screenshot({ path: shot("blocks-shown.png") });

    // ---- the filter «AU > 2 И DOMAIN = 1» in the window «Фильтры» ------------------------------------------------
    const expected = ore.columns[6].values.reduce((n, au, r) => n + (au > 2 && ore.columns[7].values[r] === 1 ? 1 : 0), 0);
    expect(expected).toBeGreaterThan(50);
    expect(expected).toBeLessThan(N / 4);
    expect((await runCommand(page, "filters.manage")).ok).toBe(true);
    const filters = page.getByTestId("filters-window");
    await expect(filters).toBeVisible({ timeout: 30_000 });
    await filters.getByTestId("filters-name").fill(FILTER);
    // the constructor: «И» of two conditions «поле — оператор — значение»
    const rules = filters.getByTestId("filter-rules-root");
    await expect(rules.getByTestId("filter-rules-root-op")).toHaveValue("and");
    await rules.getByTestId("filter-rules-0-field").fill("AU");
    await rules.getByTestId("filter-rules-0-cmp").selectOption(">");
    await rules.getByTestId("filter-rules-0-value").fill("2");
    await rules.getByTestId("filter-rules-root-add-cond").click();
    await rules.getByTestId("filter-rules-1-field").fill("DOMAIN");
    await rules.getByTestId("filter-rules-1-cmp").selectOption("=");
    await rules.getByTestId("filter-rules-1-value").fill("1");
    // the same filter as the text of 8.7.4, without loss
    await filters.getByTestId("filters-mode-text").click();
    await expect(filters.getByTestId("filters-text")).toHaveValue(/^AU > 2 AND DOMAIN = 1$/);
    await expect(filters.getByTestId("filters-text-error")).toHaveCount(0);
    await filters.getByTestId("filters-mode-builder").click();
    await filters.getByTestId("filters-model").selectOption(String(id));
    await filters.getByTestId("filters-test").click();
    const tested = filters.getByTestId("filters-test-result");
    await expect(tested).toHaveAttribute("data-kind", "ok", { timeout: LIMIT(30_000) });
    expect(passedOf(await tested.innerText())).toEqual([expected, N]);
    await filters.getByTestId("filters-save").click();
    await expect(filters.getByTestId("filters-state")).toContainText(/^Фильтр №\d+, версия 1/, { timeout: 30_000 });
    const saved = (await coreApi<{ filters: { id: number; name: string }[] }>(page, "GET", "/filters")).body.filters;
    expect(saved.map((f) => f.name)).toEqual([FILTER]);
    const filterId = saved[0].id;
    await filters.getByTestId("filters-close").click();
    await expect(filters).toHaveCount(0);

    // ---- the filter chosen in the window «Блочная модель · …»: only the blocks that pass are in 3D (Д-02) ---------
    const before = await stats(page);
    expect((await runCommand(page, "blocks.settings", { model_id: id })).ok).toBe(true);
    const win = page.getByTestId("model-window");
    await expect(win).toHaveAttribute("data-model", String(id), { timeout: 30_000 });
    await win.getByTestId("model-tab-filter").click();
    await expect(win.getByTestId("filter-select")).toContainText(FILTER);
    await win.getByTestId("filter-select").selectOption(String(filterId));
    await expect(win.getByTestId("filter-title")).toContainText("AU > 2");
    await expect(win.getByTestId("filter-passed")).toBeVisible({ timeout: LIMIT(30_000) });
    await expect.poll(async () => passedOf((await win.getByTestId("filter-passed").innerText()).replace("Сейчас ", "").replace("блоков.", "блоков")), { timeout: LIMIT(30_000) }).toEqual([expected, N]);
    // the shell of the blocks that pass: a face of a block that passes next to a block that does not (or the edge)
    await expect.poll(async () => (await shown(page))[0]?.facesRaw, { timeout: LIMIT(30_000) }).not.toBe(all.facesRaw);
    const only = (await shown(page))[0];
    // the choice is the style of the model in the view of the project (it is saved with the project)
    expect((await hook<Record<string, { filter_id: number | null; show?: boolean }>>(page, "styles"))?.[String(id)]).toMatchObject({ filter_id: filterId });
    expect(only.facesRaw).toBe(exposedFaces(ore, NX, NY, NZ, (r) => ore.columns[6].values[r] > 2 && ore.columns[7].values[r] === 1));
    expect(only.facesRaw).toBeLessThan(all.facesRaw);
    expect((await stats(page)).requests).toBeGreaterThan(before.requests);
    // the block of the core of the ore is shown, a block of the rock is «скрыт фильтром»; a gap in AU is «—», not 0
    const at = (i: number, j: number, k: number) => [5000 + (i + 0.5) * 10, 7000 + (j + 0.5) * 10, 100 + (k + 0.5) * 5];
    const [ox, oy, oz] = at(13, 12, 6);
    const richBlock = await runCommand(page, "blocks.pick", { model_id: id, x: ox, y: oy, z: oz });
    expect(richBlock.ok, richBlock.result).toBe(true);
    expect(richBlock.result).toContain("блок (13, 12, 6)");
    expect(richBlock.result).not.toContain("скрыт фильтром");
    const [rx, ry, rz] = at(0, 0, 0);
    const rockBlock = await runCommand(page, "blocks.pick", { model_id: id, x: rx, y: ry, z: rz });
    expect(rockBlock.ok, rockBlock.result).toBe(true);
    expect(rockBlock.result).toContain("(скрыт фильтром)");
    const [gx, gy, gz] = at(5, 0, 0); // row 5 of the file: AU is empty there
    const gap = await runCommand(page, "blocks.pick", { model_id: id, x: gx, y: gy, z: gz });
    expect(gap.ok, gap.result).toBe(true);
    expect(gap.result).toMatch(/AU —/);
    // a click on the top face of the highest block that passes in the column (13, 12): the block under the cursor
    const keep = (r: number) => ore.columns[6].values[r] > 2 && ore.columns[7].values[r] === 1;
    const top = [...Array(NZ).keys()].filter((k) => keep((k * NY + 12) * NX + 13)).pop()!;
    const [tx, ty] = at(13, 12, top);
    const tz = 100 + (top + 1) * 5;
    const screen = await page.evaluate(([x, y, z]) => window.__bfScene!.screen(x, y, z), [tx, ty, tz] as const);
    expect(screen).not.toBeNull();
    // the hook takes the point in the pixels of the canvas, `screen` gives those of the page
    const host = (await page.getByTestId("scene-host").boundingBox())!;
    const hit = await hook<{ model: number; pick: { kind: string; inside: number[]; normal: number[]; xyz: number[] } }>(page, "pick", screen!.x - host.x, screen!.y - host.y);
    expect([hit?.model, hit?.pick.kind, hit?.pick.normal]).toEqual([id, "blocks", [0, 0, 1]]);
    hit!.pick.xyz.forEach((v, c) => expect(v).toBeCloseTo([tx, ty, tz][c], 6)); // the face is exactly where the top of the block is
    await hook(page, "click", screen!.x - host.x, screen!.y - host.y);
    await win.getByTestId("model-tab-props").click();
    const picked = win.getByTestId("props-picked");
    await expect(picked).toContainText(`13, 12, ${top}`, { timeout: LIMIT(30_000) });
    await expect(picked).toContainText("блок проходит");
    await win.getByTestId("model-tab-filter").click();
    await page.screenshot({ path: shot("blocks-filtered.png") });
    await win.getByTestId("model-tab-show").click();

    // ---- the calculator: a new field AU2 = AU · 2 (a gap stays a gap) ---------------------------------------------
    const opened = await runCommand(page, "blocks.calc");
    expect(opened.ok, opened.result).toBe(true);
    const fieldsWin = page.getByTestId("fields-window");
    await expect(fieldsWin).toBeVisible({ timeout: 30_000 });
    await fieldsWin.getByTestId("fields-model").selectOption(String(id));
    await fieldsWin.getByTestId("calc-expr").fill("AU * 2");
    await fieldsWin.getByTestId("calc-name").fill("AU2");
    await expect(fieldsWin.getByTestId("calc-ok")).toBeVisible({ timeout: LIMIT(30_000) });
    await page.screenshot({ path: shot("blocks-calc.png") });
    await fieldsWin.getByTestId("calc-build").click();
    const result = fieldsWin.getByTestId("fields-result");
    await expect(result).toBeVisible({ timeout: LIMIT(60_000) });
    const gaps = ore.columns[6].values.filter((v) => Number.isNaN(v)).length;
    await expect(result.getByTestId("fields-result-missing")).toContainText(String(gaps));
    const withCalc = (await blocksList(page))[0];
    expect([withCalc.version, withCalc.fields.map((f) => f.name)]).toEqual([2, ["AU", "DOMAIN", "SG", "AU2"]]);
    await fieldsWin.getByTestId("fields-close").click();
    await expect(fieldsWin).toHaveCount(0);
    // the layer of 3D read the new version and still shows the blocks that pass
    await expect.poll(async () => (await shown(page))[0]?.version, { timeout: LIMIT(30_000) }).toBe(2);
    expect((await shown(page))[0].facesRaw).toBe(only.facesRaw);

    // ---- «Содержание–тоннаж», cut-off 0: the blocks with a grade, the tonnes by the density SG ----------------------
    const withAu = ore.columns[6].values.map((au, r) => (Number.isNaN(au) ? -1 : r)).filter((r) => r >= 0);
    const tonnes = withAu.reduce((t, r) => t + 10 * 10 * 5 * ore.columns[8].values[r], 0);
    const rep = await runCommand(page, "blocks.report");
    expect(rep.ok, rep.result).toBe(true);
    const repWin = page.getByTestId("report-window");
    await expect(repWin).toBeVisible({ timeout: 30_000 });
    await repWin.getByTestId("report-model").selectOption(String(id));
    await repWin.getByTestId("report-build").click();
    await expect(repWin.getByTestId("report-table")).toBeVisible({ timeout: LIMIT(60_000) });
    const total = repWin.getByTestId("report-row-0");
    await expect(total).toContainText("Всего");
    expect([ruNum(await repWin.getByTestId("report-cutoff-0").innerText()), ruNum(await repWin.getByTestId("report-tonnes-0").innerText())]).toEqual([0, tonnes]);
    expect(ruNum((await total.getByRole("cell").nth(2).innerText()))).toBe(withAu.length);
    const grade = withAu.reduce((s, r) => s + ore.columns[6].values[r] * ore.columns[8].values[r], 0) / withAu.reduce((s, r) => s + ore.columns[8].values[r], 0);
    expect(ruNum(await repWin.getByTestId("report-grade-0-AU").innerText())).toBeCloseTo(grade, 3);
    await expect(repWin.getByTestId("report-gap-0-AU")).toHaveText("0");
    // the same report with the filter of the library: only the blocks that pass (their tonnes by SG 3,1)
    await repWin.getByTestId("report-filter").selectOption(String(filterId));
    await repWin.getByTestId("report-build").click();
    await expect.poll(async () => ruNum(await repWin.getByTestId("report-tonnes-0").innerText()), { timeout: LIMIT(30_000) }).toBe(expected * 500 * 3.1);
    await page.screenshot({ path: shot("blocks-report.png") });
    await repWin.getByTestId("report-close").click();
    await expect(repWin).toHaveCount(0);

    // ---- a slice: the plane Z = the middle of the layer 6, coloured by AU, with the filter of the model ---------------
    const sliced = await runCommand(page, "blocks.slice", { model_id: id, plane: "z", at: 132.5 });
    expect(sliced.ok, sliced.result).toBe(true);
    expect(sliced.result).toContain("по оси Z, отметка 132,5 м");
    const slices = () => page.evaluate(() => (window as unknown as { __bfBlocksSlice?: { shown(): { model: number; version: number; raster: string; width: number; height: number; withValue: number; visible: boolean }[] } }).__bfBlocksSlice?.shown() ?? []);
    await expect.poll(async () => (await slices()).length, { timeout: LIMIT(30_000) }).toBe(1);
    const slice = (await slices())[0];
    const layer6 = ore.columns[6].values.reduce((n, au, r) => n + (Math.floor(r / (NX * NY)) === 6 && au > 2 && ore.columns[7].values[r] === 1 ? 1 : 0), 0);
    expect([slice.model, slice.raster, slice.width, slice.height, slice.visible]).toEqual([id, "cells", NX, NY, true]);
    expect(slice.withValue).toBe(layer6); // only the blocks that pass the filter are coloured
    expect(layer6).toBeGreaterThan(0);
    await page.screenshot({ path: shot("blocks-slice.png") });
    // ---- Ctrl+K finds the commands of the blocks; one of them runs from the palette -----------------------------------
    const palette = page.getByLabel("Палитра команд");
    // the palette lists the commands by the labels of the ribbon, with the tab and the group («Настройки проекта › Поля»)
    const found: [string, string][] = [["Оболочка", "Вид"], ["Все блоки", "Вид"], ["Срез по разрезу", "Вид"], ["Фильтры", "Блочные модели"], ["Прототип", "Создание"], ["Калькулятор", "Поля"], ["Плотность", "Поля"], ["Содержание–тоннаж", "Поля"], ["Экспорт модели", "Обмен"]];
    for (const [label, group] of found) {
      await page.keyboard.press("Control+KeyK");
      await page.getByLabel("Поиск команды").fill(label);
      await expect(palette.getByRole("option").filter({ hasText: label }).filter({ hasText: `Настройки проекта › ${group}` }).first(), label).toBeVisible();
      await page.keyboard.press(label === "Содержание–тоннаж" ? "Enter" : "Escape");
      await expect(palette).toHaveCount(0);
    }
    await expect(page.getByTestId("report-window")).toBeVisible({ timeout: 30_000 });
    await page.screenshot({ path: shot("blocks-palette.png") });
    const took = Date.now() - started;
    console.log(`blocks: ${(took / 1000).toFixed(1)} s`);
    expect(took).toBeLessThan(LIMIT(60_000));
  } finally {
    await close(app);
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
