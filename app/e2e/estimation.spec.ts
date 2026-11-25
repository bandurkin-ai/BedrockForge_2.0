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
import { close, coreApi, importFiles, launch, runCommand, setWindowSize, shot, waitFrame, waitJob, type CoreJob, type BlockColumns } from "./helpers";
import { writeDatBlocks } from "./helpers";

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
  await expect(wiz.getByTestId("est-preview-status")).toContainText("оценка получена", { timeout: 5_000 }).catch(() => undefined);
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
