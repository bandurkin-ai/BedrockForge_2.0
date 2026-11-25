// Э18, подшаг 18.26 (ТЗ 8.8.4, 7.35 Д-27, Д-30, Д-35; ADR 0024 «Оценка содержаний», ADR 0025 «Граф зависимостей»): the estimation of
// grades end to end on a synthetic project made through the core (the demo is not touched, no real data): 36 vertical holes with
// samples of AU and a domain DOM, a block model 12 × 12 × 6 (DAT, a domain DOMAIN), a model of variogram of AU (a calc of the axes +
// «Сохранить» through the API of Э16). One app, one project, the scenarios in a row (each ≤ 60 s, the waits — on events and states):
//  1. Ctrl+K «Оценка содержаний» → the wizard, steps 1–6 → the preview of the block (step 6) puts the ellipsoid, the block and the
//     probes in 3D (`window.__bfPreview`) → «Выполнить оценку» → the job is done → the fields `<префикс>EST` and `STATUS` in
//     «Свойства полей» → the model coloured by the field in 3D.
//  2. The cross-validation: the window, «по скважине», «Выполнить» → the summary and the table; «Показать ошибки в 3D».
//  3. The inspector of a block (ТД30-3): a click on a block in 3D → the window «Блок · i, j, k», the table of the probes with the
//     reasons, the layers of 3D (`window.__bfInspect`), the filter by the reason, «К интервалу», «Скопировать снимок».
//  4. The graph of dependencies (Д-35): a sample edited → «устарел» at the row of the estimation and at the field → «Перестроить с
//     зависимыми» (the plan, the job) → «актуален».
//  5. Ctrl+K finds «Инспектор блока», «Эллипсоид поиска в 3D», «Глобальная проверка» and runs them.
// A window of 1024 × 640 with the ribbon folded; the hooks and the colours are read from the state, never from the pixels of
// software WebGL; the limits on the .exe are ×1.5 (ADR 0022 «CI»).
import { expect, test, type ElectronApplication, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { close, coreApi, importFiles, launch, runCommand, setWindowSize, shot, waitFrame, waitJob, writeDatBlocks, type BlockColumns, type CoreJob } from "./helpers";

const EXE = !!process.env.BF_APP_EXE;
const LIMIT = (ms: number) => (EXE ? 1.5 * ms : ms);

// ---- the synthetic world ----------------------------------------------------------------------------------------------------------
const SIDE = 6; // holes a side, 20 m apart
const STEP = 20;
const X0 = 5010;
const Y0 = 7010;
const Z0 = 130; // the collars; the holes are vertical, 30 m
const PER_HOLE = 15; // samples of 2 m
const NX = 12;
const NY = 12;
const NZ = 6;
const CORNER: [number, number, number] = [5000, 7000, 100];
const SIZE: [number, number, number] = [10, 10, 5];
const MODEL = "Модель оценки";
const SET = "Скважины оценки";
const FIELD = "AU";
const PREFIX = "AU_OK_"; // the default prefix of OK of AU
const ROW = [NX / 2, NY / 2, 3]; // the block of the preview: the middle of the model, a layer of the domain 1

/** The same numbers every run: AU from the formulas of the coordinates (a few gaps), the domain by the height of the middle of the sample. */
function world(dir: string) {
  const collar = ["HOLE,X,Y,Z,DEPTH"];
  const survey = ["HOLE,DEPTH,AZIMUTH,DIP"];
  const assay = ["HOLE,FROM,TO,AU,DOM"];
  const au: number[] = [];
  let n = 0;
  for (let j = 0; j < SIDE; j++) {
    for (let i = 0; i < SIDE; i++) {
      const hole = `E${String(j * SIDE + i + 1).padStart(2, "0")}`;
      const x = X0 + i * STEP;
      const y = Y0 + j * STEP;
      collar.push(`${hole},${x},${y},${Z0},${PER_HOLE * 2}`);
      survey.push(`${hole},0,0,-90`, `${hole},${PER_HOLE * 2},0,-90`);
      for (let s = 0; s < PER_HOLE; s++, n++) {
        const z = Z0 - (s * 2 + 1);
        const value = Math.round((1.6 + 1.1 * Math.sin(x / 31) * Math.cos(y / 37) + 0.7 * Math.cos(z / 7) + 0.05 * ((n * 7) % 11)) * 1000) / 1000;
        const gap = n % 29 === 17;
        if (!gap) au.push(value);
        assay.push(`${hole},${s * 2},${s * 2 + 2},${gap ? "" : value},${z > 115 ? 1 : 2}`);
      }
    }
  }
  const write = (name: string, lines: string[]) => {
    const p = path.join(dir, name);
    fs.writeFileSync(p, lines.join("\n") + "\n");
    return p;
  };
  const mean = au.reduce((a, b) => a + b, 0) / au.length;
  const variance = au.reduce((a, b) => a + (b - mean) ** 2, 0) / au.length;
  return { files: [write("collar.csv", collar), write("survey.csv", survey), write("assay.csv", assay)], variance, samples: n, used: au.length };
}

/** The block model of the world: the centres and the sizes, DOMAIN 1 above the height 115 (the upper three layers), 2 below. */
function blocks(): BlockColumns {
  const n = NX * NY * NZ;
  const names = ["XC", "YC", "ZC", "XINC", "YINC", "ZINC", "DOMAIN"];
  const cols = names.map(() => new Float64Array(n));
  let at = 0;
  for (let k = 0; k < NZ; k++)
    for (let j = 0; j < NY; j++)
      for (let i = 0; i < NX; i++, at++) {
        const zc = CORNER[2] + (k + 0.5) * SIZE[2];
        [CORNER[0] + (i + 0.5) * SIZE[0], CORNER[1] + (j + 0.5) * SIZE[1], zc, SIZE[0], SIZE[1], SIZE[2], zc > 115 ? 1 : 2].forEach((v, c) => (cols[c][at] = v));
      }
  return { count: n, columns: names.map((name, c) => ({ name, values: cols[c] })) };
}

type Bm = { id: number; name: string; version: number; cells: number; fields: { name: string; role?: string | null }[] };
type Row = { id: number; name: string; prefix: string; method: string; model_id: number; last_run: unknown; stale: boolean };
type Ids = { set: number; table: string; model: number; vario: number };

const blocksList = async (page: Page) => (await coreApi<{ block_models: Bm[] }>(page, "GET", "/blocks")).body.block_models;
const rowsList = async (page: Page) => (await coreApi<{ estimations: Row[] }>(page, "GET", "/geostat/estimations")).body.estimations;

/** The last job of `kind` once it is over (the jobs list of the core; the waits are polls of the state). */
async function lastJob(page: Page, kind: string, timeout: number, after = 0): Promise<CoreJob> {
  let job: CoreJob | undefined;
  await expect
    .poll(
      async () => {
        const jobs = (await coreApi<{ jobs: CoreJob[] }>(page, "GET", "/jobs")).body.jobs.filter((j) => j.kind === kind && j.id > after);
        job = jobs.sort((a, b) => b.id - a.id)[0];
        return job?.status ?? "none";
      },
      { timeout, intervals: [200, 400, 800] },
    )
    .toMatch(/^(done|failed|cancelled)$/);
  expect(job!.status, JSON.stringify(job!.error)).toBe("done");
  return job!;
}
const lastJobId = async (page: Page, kind: string) => Math.max(0, ...(await coreApi<{ jobs: CoreJob[] }>(page, "GET", "/jobs")).body.jobs.filter((j) => j.kind === kind).map((j) => j.id));

/** The set, the block model and the model of variogram through the core: the data of every scenario. */
async function build(page: Page, dir: string): Promise<Ids> {
  const w = world(dir);
  const specs = await page.evaluate(async (list) => {
    const c = window.bf!.core()!;
    const out: unknown[] = [];
    for (const p of list) {
      const r = await fetch(`${c.baseUrl}/api/v1/drillholes/import/check`, { method: "POST", headers: { "X-BF-Token": c.token, "Content-Type": "application/json" }, body: JSON.stringify({ path: p }) });
      const chk = await r.json();
      // the column DOM of the samples is an attribute (text): the domain of the samples
      out.push({ path: p, kind: chk.kind, mapping: chk.mapping, components: chk.components, ...(chk.kind === "assay" ? { attributes: ["DOM"] } : {}) });
    }
    return out;
  }, w.files);
  const started = await coreApi<{ source: string; id: number }>(page, "POST", "/drillholes/import", { set_name: SET, files: specs });
  expect(started.status, JSON.stringify(started.body)).toBe(200);
  const imported = await waitJob(page, started.body.source, started.body.id, undefined, 120_000);
  expect(imported.status, JSON.stringify(imported.error)).toBe("done");
  const set = (await coreApi<{ sets: { id: number; name: string }[] }>(page, "GET", "/drillholes/sets")).body.sets.find((s) => s.name === SET)!.id;
  const tables = (await coreApi<{ tables: { table: string; name: string; kind: string }[] }>(page, "GET", `/drillholes/sets/${set}/tables`)).body.tables;
  const assay = tables.find((t) => t.kind === "assay") ?? tables[0];

  // the block model: a DAT with the centres and the sizes is a block model by its header
  fs.mkdirSync(path.join(dir, "m"), { recursive: true });
  const dat = writeDatBlocks(path.join(dir, "m", `${MODEL}.dat`), blocks());
  const loaded = await importFiles(page, [dat], 120_000);
  expect(loaded.result).toMatchObject({ block_models: 1, cells: NX * NY * NZ });
  const model = (await blocksList(page)).find((m) => m.name === MODEL)!;
  expect(model.fields.map((f) => f.name)).toContain("DOMAIN");

  // the model of variogram: a calc of the axes (the job of Э16), then the model of its key (a sphere, the sill in the units of the field)
  const exp = await coreApi<{ source: string; id: number }>(page, "POST", "/geostat/variography/experimental", {
    set_id: set, table: assay.table, field: FIELD, axes: { ang1: 0, ang2: 0, ang3: 0 }, nlag: 6, min_pairs: 5,
  });
  expect(exp.status, JSON.stringify(exp.body)).toBe(200);
  const calc = await waitJob(page, exp.body.source, exp.body.id, undefined, 120_000);
  expect(calc.status, JSON.stringify(calc.error)).toBe("done");
  const made = await coreApi<{ id: number }>(page, "POST", "/geostat/variography/models", {
    name: "AU · сфера",
    exp_key: calc.result!.key,
    model: { type: "semivariogram", c0: 0.1 * w.variance, common_axes: true, structures: [{ type: "spherical", c: 0.9 * w.variance, ang1: 0, ang2: 0, ang3: 0, a1: 60, a2: 60, a3: 30 }] },
  });
  expect(made.status, JSON.stringify(made.body)).toBe(200);
  return { set, table: assay.table, model: model.id, vario: made.body.id };
}

/** Ctrl+K → the command by its title → Enter (the palette closes). */
async function palette(page: Page, query: string, title = query): Promise<void> {
  await page.keyboard.press("Control+KeyK");
  await page.getByLabel("Поиск команды").fill(query);
  await expect(page.getByLabel("Палитра команд").getByRole("option").first()).toContainText(title);
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("Палитра команд")).toHaveCount(0);
}

type Probes = { taken: number; cut: number; points: number; excluded: boolean; segments: number; picked: number | null };
type Pose = { ang1: number; ang2: number; ang3: number; r1: number; r2: number; r3: number; centre: number[]; pass: number | null };
type PreviewStats = { source: string | null; ellipsoid: unknown | null; probes: Probes | null; block: boolean; pose: Pose | null; ms: number };
type InspectStats = { segments: Probes | null; ellipsoid: unknown | null; centre: number[] | null };
const preview = (page: Page) => page.evaluate(() => (window as unknown as { __bfPreview?: { stats(): unknown } }).__bfPreview?.stats() as PreviewStats | undefined);
const inspected = (page: Page) => page.evaluate(() => (window as unknown as { __bfInspect?: { stats(): unknown } }).__bfInspect?.stats() as InspectStats | undefined);
const layers = (page: Page) => page.evaluate(() => window.__bfScene!.state().layers);
const blocksHook = <T>(page: Page, fn: string, ...args: unknown[]) =>
  page.evaluate(([f, a]) => {
    const h = (window as unknown as { __bfBlocks?: Record<string, (...x: unknown[]) => unknown> }).__bfBlocks;
    return (h ? h[f as string](...(a as unknown[])) : null) as never;
  }, [fn, args] as const) as Promise<T | null>;

let app: ElectronApplication;
let page: Page;
let dir: string;
let ids: Ids;
let row: Row;

test.describe.configure({ mode: "serial" });

test.beforeAll(async () => {
  test.setTimeout(LIMIT(240_000));
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-estimation-"));
  ({ app, page } = await launch({ BF_E2E: "1" }));
  await setWindowSize(app, 1024, 640); // as small as on the Windows runner
  page.on("pageerror", (e) => console.log(`PAGEERROR ${e.message}\n${e.stack}`));
  await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
  await page.getByLabel("Лента").getByRole("button", { name: "Новый проект" }).click();
  await page.getByRole("dialog").last().getByRole("textbox").fill("Оценка Э18");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("project-selector")).toContainText("Оценка Э18", { timeout: 60_000 });
  await waitFrame(page);
  expect((await runCommand(page, "ribbon.toggle")).ok).toBe(true);
  const t = Date.now();
  ids = await build(page, dir);
  // the interface has read the lists of the core (events): the model and the set are in the tree
  const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
  await expect(tree.getByTestId(`tree-block-${ids.model}`)).toBeAttached({ timeout: 30_000 });
  await expect(tree.getByTestId(`tree-set-${ids.set}`)).toBeAttached({ timeout: 30_000 });
  console.log(`estimation: the project is built in ${((Date.now() - t) / 1000).toFixed(1)} s`);
});

test.afterAll(async () => {
  if (app) await close(app);
  if (dir) fs.rmSync(dir, { recursive: true, force: true });
});

test("1. the wizard: steps 1–6, the preview of a block in 3D, the run, the fields EST and STATUS, the model coloured by EST", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  await palette(page, "Оценка содержаний");
  const wiz = page.getByTestId("estimation-wizard");
  await expect(wiz).toBeVisible({ timeout: 30_000 });
  const tab = (n: number) => wiz.getByTestId(`est-step-${n}-tab`);

  // 1. the model, the domain of the blocks
  await expect(wiz.getByTestId("est-step-1")).toBeVisible();
  await wiz.getByTestId("est-model").selectOption(String(ids.model));
  await expect(wiz.getByTestId("est-model-info")).toContainText("Блоков");
  await wiz.getByTestId("est-domain-field").selectOption("DOMAIN");
  await wiz.getByTestId("est-label").fill("1");

  // 2. the samples: the set, the table, AU, the domain of the samples
  await tab(2).click();
  await expect(wiz.getByTestId("est-step-2")).toBeVisible();
  await expect(wiz.getByTestId("est-set")).toHaveValue(String(ids.set), { timeout: 30_000 });
  await expect(wiz.getByTestId("est-field")).toHaveValue(FIELD, { timeout: 30_000 });
  await wiz.getByTestId("est-domain").selectOption("field");
  await wiz.getByTestId("est-domain-col").selectOption("DOM");
  await expect(wiz.getByTestId("est-domain-values").getByRole("checkbox", { name: "1", exact: true })).toBeChecked({ timeout: 30_000 });
  await expect(wiz.getByTestId("est-samples-summary")).toContainText("Проб:", { timeout: 30_000 });

  // 3. the method and the model of variogram
  await tab(3).click();
  await wiz.getByTestId("est-method-ok").check();
  await expect(wiz.getByTestId("est-variogram").locator(`option[value="${ids.vario}"]`)).toBeAttached({ timeout: 30_000 });
  await wiz.getByTestId("est-variogram").selectOption(String(ids.vario));

  // 4. the search: the ellipsoid of the model, the passes of the core
  await tab(4).click();
  await expect(wiz.getByTestId("est-step-4")).toBeVisible();
  await expect(wiz.getByTestId("est-search-model")).toContainText("Из модели", { timeout: 30_000 });
  await expect(wiz.getByTestId("est-pass-0")).toBeVisible();

  // 5. the output fields: the prefix by default, STATUS and EST are always there
  await tab(5).click();
  await expect(wiz.getByTestId("est-step-5")).toBeVisible();
  await expect(wiz.getByTestId("est-prefix")).toHaveAttribute("placeholder", PREFIX);
  await expect(wiz.getByTestId("est-field-EST")).toBeChecked();
  await expect(wiz.getByTestId("est-field-STATUS")).toBeChecked();

  // 6. the preview of the block: the ellipsoid, the block and the probes in 3D (`window.__bfPreview`)
  await tab(6).click();
  await expect(wiz.getByTestId("est-step-6")).toBeVisible();
  await expect(wiz.getByTestId("est-summary")).toContainText("OK");
  expect(await preview(page)).toMatchObject({ source: null, block: false, pose: null });
  await wiz.getByTestId("est-preview-go").click();
  await expect(wiz.getByTestId("est-preview-result")).toBeVisible({ timeout: LIMIT(30_000) });
  await expect(wiz.getByTestId("est-preview-status")).toHaveAttribute("data-status", "0"); // estimated
  await expect.poll(async () => (await preview(page))?.pose?.pass ?? null, { timeout: LIMIT(30_000) }).not.toBeNull();
  const shown = (await preview(page))!;
  expect(shown.source).toBe("wizard");
  expect(shown.block).toBe(true);
  expect(shown.pose!.r1).toBeGreaterThan(0);
  expect(shown.pose!.centre).toHaveLength(3);
  // the centre of the block 6; 6; 3: the corner + (index + ½) · the size
  [0, 1, 2].forEach((c) => expect(shown.pose!.centre[c]).toBeCloseTo(CORNER[c] + (ROW[c] + 0.5) * SIZE[c], 6));
  expect(shown.probes!.taken).toBeGreaterThan(0);
  const counts = await wiz.getByTestId("est-preview-counts").innerText();
  expect(counts).toContain("Кандидатов в эллипсоиде");
  await expect.poll(async () => (await layers(page)).filter((k) => k.startsWith("estimate:")).sort(), { timeout: 10_000 }).toEqual(["estimate:block", "estimate:ellipsoid", "estimate:probes"]);
  await page.screenshot({ path: shot("estimation-preview.png") });

  // the run: the row is saved, the job is done
  const before = await lastJobId(page, "estimate.run");
  await wiz.getByTestId("est-run-go").click();
  const job = await lastJob(page, "estimate.run", LIMIT(90_000), before);
  expect(job.status).toBe("done");
  await expect(wiz.getByTestId("est-run-done")).toBeVisible({ timeout: LIMIT(30_000) });
  await page.screenshot({ path: shot("estimation-done.png") });
  const rows = await rowsList(page);
  expect(rows).toHaveLength(1);
  row = rows[0];
  expect([row.prefix, row.method, row.model_id]).toEqual([PREFIX, "ok", ids.model]);

  // the fields in the model: EST and STATUS (0 — the estimate; the gaps are NaN, never 0)
  const model = (await blocksList(page)).find((m) => m.id === ids.model)!;
  expect(model.fields.map((f) => f.name)).toEqual(expect.arrayContaining(["DOMAIN", `${PREFIX}STATUS`, `${PREFIX}EST`]));
  await wiz.getByTestId("est-close").click();
  await expect(wiz).toHaveCount(0);

  // «Свойства полей» of the model: the rows of the fields
  const opened = await runCommand(page, "blocks.field.props", { model_id: ids.model });
  expect(opened.ok, opened.result).toBe(true);
  const fieldsWin = page.getByTestId("fields-window");
  await expect(fieldsWin).toBeVisible({ timeout: 30_000 });
  await expect(fieldsWin.getByTestId(`props-row-${PREFIX}EST`)).toBeVisible({ timeout: 30_000 });
  await expect(fieldsWin.getByTestId(`props-row-${PREFIX}STATUS`)).toBeVisible();
  await expect(fieldsWin.getByTestId(`props-source-${PREFIX}EST`)).not.toHaveText("");
  await fieldsWin.getByTestId("fields-close").click();
  await expect(fieldsWin).toHaveCount(0);

  // the model in 3D coloured by the field EST
  expect((await runCommand(page, "blocks.show", { model_id: ids.model })).ok).toBe(true);
  await expect.poll(async () => ((await blocksHook<unknown[]>(page, "shown")) ?? []).length, { timeout: LIMIT(30_000) }).toBe(1);
  const colored = await runCommand(page, "blocks.style", { model_id: ids.model, field: `${PREFIX}EST`, kind: "scale" });
  expect(colored.ok, colored.result).toBe(true);
  await expect.poll(async () => JSON.stringify((await blocksHook<Record<string, unknown>>(page, "styles"))?.[String(ids.model)] ?? {}), { timeout: LIMIT(30_000) }).toContain(`${PREFIX}EST`);
  await page.screenshot({ path: shot("estimation-colored.png") });

  const took = Date.now() - started;
  console.log(`estimation 1: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

test("2. the cross-validation: «по скважине» → the summary and the table, «Показать ошибки в 3D»", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  const opened = await runCommand(page, "estimate.xval", { estimation_id: row.id });
  expect(opened.ok, opened.result).toBe(true);
  const win = page.getByTestId("estimation-xval");
  await expect(win).toBeVisible({ timeout: 30_000 });
  await expect(win.getByTestId("xval-none")).toBeVisible({ timeout: 30_000 }); // no result yet
  await win.getByTestId("xval-by").selectOption("hole");
  const before = await lastJobId(page, "estimate.xval");
  await win.getByTestId("xval-run").click();
  await lastJob(page, "estimate.xval", LIMIT(90_000), before);
  await expect(win.getByTestId("xval-summary")).toBeVisible({ timeout: LIMIT(30_000) });
  await expect(win.getByTestId("xval-table")).toBeVisible({ timeout: LIMIT(30_000) });
  const rows = Number(await win.getByTestId("xval-table").getAttribute("data-rows"));
  expect(rows).toBeGreaterThan(0);
  expect(rows).toBeLessThanOrEqual(SIDE * SIDE * PER_HOLE);
  await expect(win.getByTestId("xval-statuses")).toContainText("оценён");
  await win.getByTestId("xval-tab-scatter").click();
  await expect(win.getByTestId("xval-scatter")).toBeVisible();
  await win.getByTestId("xval-tab-hist").click();
  await expect(win.getByTestId("xval-histogram")).toBeVisible();
  await win.getByTestId("xval-tab-table").click();
  await page.screenshot({ path: shot("estimation-xval.png") });

  // «Показать ошибки в 3D» = the command `estimate.xval_3d`: the layer of the points in the scene, the legend in the window
  expect(await layers(page)).not.toContain("xval-errors");
  await win.getByTestId("xval-3d").click();
  await expect(win.getByTestId("xval-3d-legend")).toBeVisible({ timeout: LIMIT(30_000) });
  await expect.poll(() => layers(page), { timeout: LIMIT(30_000) }).toContain("xval-errors");
  await expect(win.getByTestId("xval-3d")).toHaveText("Скрыть ошибки в 3D");
  // the same through the registry (Ctrl+K, the assistant): the points go away
  const hidden = await runCommand(page, "estimate.xval_3d", { estimation_id: row.id, show: false });
  expect(hidden.ok, hidden.result).toBe(true);
  await expect.poll(() => layers(page), { timeout: 10_000 }).not.toContain("xval-errors");
  await win.getByTestId("xval-close").click();
  await expect(win).toHaveCount(0);

  const took = Date.now() - started;
  console.log(`estimation 2: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

test("3. the inspector of a block (ТД30-3): a click on a block in 3D → «Блок · i, j, k», the probes with the reasons, the layers of 3D, the filter, «К интервалу», «Скопировать снимок»", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  const [bi, bj, bk] = [6, 6, NZ - 1]; // the top layer: its top face is outside, a block of the domain 1
  const tx = CORNER[0] + (bi + 0.5) * SIZE[0];
  const ty = CORNER[1] + (bj + 0.5) * SIZE[1];
  const tz = CORNER[2] + (bk + 1) * SIZE[2];
  const screen = await page.evaluate(([x, y, z]) => window.__bfScene!.screen(x, y, z), [tx, ty, tz] as const);
  expect(screen).not.toBeNull();
  const host = (await page.getByTestId("scene-host").boundingBox())!;
  await blocksHook(page, "click", screen!.x - host.x, screen!.y - host.y);
  const win = page.getByTestId("estimate-inspect");
  await expect(win).toBeVisible({ timeout: LIMIT(30_000) });
  await expect(win).toHaveAttribute("data-state", "ready", { timeout: LIMIT(30_000) });
  await expect(win.getByTestId("inspect-ijk")).toContainText(`${bi}, ${bj}, ${bk}`);
  await expect(win.getByTestId("inspect-status")).toHaveText("оценён");
  await expect(win.getByTestId("inspect-table")).toBeVisible();
  const selectedRows = win.locator('[data-testid^="inspect-row-"][data-selected="true"]');
  const taken = await selectedRows.count();
  expect(taken).toBeGreaterThan(0);
  // the layers of the inspector in 3D: the ellipsoid of the pass and the segments to the probes taken
  await expect.poll(async () => (await inspected(page))?.segments?.taken ?? 0, { timeout: LIMIT(30_000) }).toBe(taken);
  const stats = (await inspected(page))!;
  expect(stats.ellipsoid).not.toBeNull();
  [0, 1, 2].forEach((c) => expect(stats.centre![c]).toBeCloseTo(CORNER[c] + ([bi, bj, bk][c] + 0.5) * SIZE[c], 6));
  await page.screenshot({ path: shot("estimation-inspect.png") });

  // the filter by the reason: the probes taken only
  const all = await win.locator('[data-testid^="inspect-row-"]').count();
  await win.getByTestId("inspect-reason").selectOption("selected");
  await expect.poll(() => win.locator('[data-testid^="inspect-row-"]').count()).toBe(taken);
  await win.getByTestId("inspect-reason").selectOption("");
  await expect.poll(() => win.locator('[data-testid^="inspect-row-"]').count()).toBe(all);

  // «К интервалу»: «Данные по скважинам» of the hole of the probe
  const first = (await selectedRows.first().getAttribute("data-testid"))!.replace("inspect-row-", "");
  await win.getByTestId(`inspect-interval-${first}`).click();
  await expect(page.getByRole("dialog", { name: /Данные по скважинам/ })).toBeVisible({ timeout: LIMIT(30_000) });
  await expect(win.getByTestId("inspect-note")).toContainText("Открыто «Данные по скважинам»");
  await page.getByRole("dialog", { name: /Данные по скважинам/ }).getByRole("button", { name: "Закрыть" }).first().click().catch(() => undefined);

  // «Скопировать снимок»
  await win.getByTestId("inspect-copy").click();
  await expect(win.getByTestId("inspect-note")).toContainText("скопирован", { timeout: 10_000 });
  await win.getByTestId("inspect-close").click();
  await expect(win).toHaveCount(0);
  await expect.poll(async () => (await inspected(page))?.segments ?? null).toBeNull();

  const took = Date.now() - started;
  console.log(`estimation 3: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

test("4. the graph of dependencies (Д-35): a sample edited → «устарел» at the row and at the field → «Перестроить с зависимыми» → «актуален»", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  const rowBadge = page.getByTestId(`tree-est-row-${row.id}`).getByTestId(`lineage-badge-object:estimation-${row.id}`);
  await expect(rowBadge).toHaveAttribute("data-state", "current", { timeout: LIMIT(30_000) });

  // a sample of the table edited through the API of the table (as the panel «Таблица» does)
  const first = (await coreApi<{ columns: { key: string; name: string }[]; rows: { id: number }[] }>(page, "GET", `/drillholes/sets/${ids.set}/tables/${ids.table}?limit=1`)).body;
  const au = first.columns.find((c) => c.name === FIELD)!.key;
  const patched = await coreApi(page, "PATCH", `/drillholes/sets/${ids.set}/tables/${ids.table}/rows/${first.rows[0].id}`, { column: au, value: "9" });
  expect(patched.status, JSON.stringify(patched.body)).toBe(200);
  await expect(rowBadge).toHaveAttribute("data-state", "stale", { timeout: LIMIT(30_000) });
  await expect(rowBadge).toHaveText("устарел");
  // the field in «Свойства полей»
  expect((await runCommand(page, "blocks.field.props", { model_id: ids.model })).ok).toBe(true);
  const fieldsWin = page.getByTestId("fields-window");
  await expect(fieldsWin).toBeVisible({ timeout: 30_000 });
  const fieldBadge = fieldsWin.getByTestId(`lineage-badge-bm_field-${ids.model}-${PREFIX.toLowerCase()}est`);
  await expect(fieldBadge).toHaveAttribute("data-state", "stale", { timeout: LIMIT(30_000) });
  await page.screenshot({ path: shot("estimation-stale.png") });
  await fieldsWin.getByTestId("fields-close").click();

  // «Перестроить с зависимыми» of the model of variogram (a table is a source: it is not rebuilt, its dependents are): the plan (a dry run), then the run
  const impact = await runCommand(page, "lineage.impact", { kind: "object:variogram_model", id: ids.vario });
  expect(impact.ok, impact.result).toBe(true);
  const win = page.getByTestId("lineage-impact-window");
  await expect(win).toBeVisible({ timeout: 30_000 });
  await expect(win.getByTestId("lineage-lead")).toBeVisible({ timeout: 30_000 });
  await win.getByTestId("lineage-rebuild").click();
  await expect(win.getByTestId("lineage-plan")).toBeVisible({ timeout: LIMIT(30_000) });
  await win.getByTestId("lineage-run").click();
  await expect(win.getByTestId("lineage-end")).toBeVisible({ timeout: LIMIT(90_000) });
  await page.screenshot({ path: shot("estimation-rebuilt.png") });
  await win.getByTestId("lineage-close").click();
  await expect(rowBadge).toHaveAttribute("data-state", "current", { timeout: LIMIT(30_000) });
  expect((await rowsList(page))[0].stale).toBe(false);

  const took = Date.now() - started;
  console.log(`estimation 4: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

test("5. Ctrl+K: «Инспектор блока», «Эллипсоид поиска в 3D», «Глобальная проверка» are available and run", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  const palette5 = page.getByLabel("Палитра команд");
  const found = async (query: string) => {
    await page.keyboard.press("Control+KeyK");
    await page.getByLabel("Поиск команды").fill(query);
    const option = palette5.getByRole("option").first();
    await expect(option).toBeVisible();
    await expect(option).not.toHaveAttribute("aria-disabled", "true");
    return option;
  };

  // the inspector: the window, empty, waits for a block
  await found("Инспектор блока");
  await page.keyboard.press("Enter");
  await expect(palette5).toHaveCount(0);
  const inspector = page.getByTestId("estimate-inspect");
  await expect(inspector).toBeVisible({ timeout: 30_000 });
  await inspector.getByTestId("inspect-close").click();
  await expect(inspector).toHaveCount(0);

  // the global check: its window (a row run)
  await found("Глобальная проверка");
  await page.keyboard.press("Enter");
  await expect(palette5).toHaveCount(0);
  const global = page.getByTestId("estimation-global");
  await expect(global).toBeVisible({ timeout: 30_000 });
  await global.getByTestId("global-close").click();
  await expect(global).toHaveCount(0);

  // the ellipsoid: the wizard of the row with its block shown → the command puts the ellipsoid in 3D
  expect((await runCommand(page, "estimate.open", { id: row.id })).ok).toBe(true);
  const wiz = page.getByTestId("estimation-wizard");
  await expect(wiz).toBeVisible({ timeout: 30_000 });
  await wiz.getByTestId("est-step-6-tab").click();
  await wiz.getByTestId("est-preview-go").click();
  await expect(wiz.getByTestId("est-preview-result")).toBeVisible({ timeout: LIMIT(30_000) });
  expect((await runCommand(page, "estimate.ellipsoid", { show: false })).ok).toBe(true);
  await expect.poll(async () => (await layers(page)).filter((k) => k.startsWith("estimate:")), { timeout: 10_000 }).toEqual([]);
  await found("Эллипсоид поиска в 3D");
  await page.keyboard.press("Enter");
  await expect(palette5).toHaveCount(0);
  await expect.poll(async () => (await preview(page))?.pose ?? null, { timeout: LIMIT(30_000) }).not.toBeNull();
  expect((await preview(page))!.ellipsoid).not.toBeNull();
  await wiz.getByTestId("est-close").click();
  await expect(wiz).toHaveCount(0);

  const took = Date.now() - started;
  console.log(`estimation 5: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});
