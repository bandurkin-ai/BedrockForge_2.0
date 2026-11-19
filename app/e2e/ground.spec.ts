// Э7.1, подшаг 7.1.16 (ADR 0014 §4–§6) under software WebGL2: a LAS without classes (every point «1 — не
// классифицирована») → «Свойства» of the cloud → «Фильтр земли…» (the profile «Равнина»; «Свой» with a number out of
// its range is refused in the window) → the job → class 2 «земля» in «Свойства», the colour mode «Высота над
// землёй» → «Моделирование › Поверхности › Построить ЦТМ из облака» → the window «Построение ЦТМ» with the cloud and
// its ground → the DTM in the tree («ЦТМ») and in the 3D window.
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { close, holesOnlyDemo, launch, shot, waitFrame } from "./helpers";

const NX = 240;
const NY = 200;
const STEP = 0.5;
const X0 = 5100;
const Y0 = 8100;

/** A LAS 1.2 of point format 0 (ASPRS LAS 1.4 R15, tables 3 and 7): a gentle slope sampled every 0.5 m, every
 * 10th point a crown 6 m above it, every 37th a low point 4 m below; no point is classified (class 1). */
function las(): Buffer {
  const n = NX * NY;
  const scale = 0.01;
  const offset = [X0, Y0, 300];
  const head = Buffer.alloc(227);
  head.write("LASF", 0, "ascii");
  head.writeUInt8(1, 24);
  head.writeUInt8(2, 25);
  head.write("BF2 E2E", 58, "ascii");
  head.writeUInt16LE(1, 90);
  head.writeUInt16LE(2026, 92);
  head.writeUInt16LE(227, 94);
  head.writeUInt32LE(227, 96);
  head.writeUInt32LE(0, 100);
  head.writeUInt8(0, 104);
  head.writeUInt16LE(20, 105);
  head.writeUInt32LE(n, 107);
  head.writeUInt32LE(n, 111);
  const body = Buffer.alloc(20 * n);
  const box = [Infinity, -Infinity, Infinity, -Infinity, Infinity, -Infinity];
  for (let j = 0; j < NY; j++) {
    for (let i = 0; i < NX; i++) {
      const k = j * NX + i;
      const x = X0 + STEP * i;
      const y = Y0 + STEP * j;
      const z = 300 + 0.05 * STEP * i + 0.02 * STEP * j + (k % 37 === 0 ? -4 : k % 10 === 0 ? 6 : 0);
      [x, y, z].forEach((v, a) => {
        box[a * 2] = Math.min(box[a * 2], v);
        box[a * 2 + 1] = Math.max(box[a * 2 + 1], v);
      });
      const at = k * 20;
      body.writeInt32LE(Math.round((x - offset[0]) / scale), at);
      body.writeInt32LE(Math.round((y - offset[1]) / scale), at + 4);
      body.writeInt32LE(Math.round((z - offset[2]) / scale), at + 8);
      body.writeUInt8(1 | (1 << 3), at + 14); // return 1 of 1
      body.writeUInt8(1, at + 15); // «не классифицирована»
    }
  }
  [scale, scale, scale, ...offset].forEach((v, i) => head.writeDoubleLE(v, 131 + i * 8));
  [box[1], box[0], box[3], box[2], box[5], box[4]].forEach((v, i) => head.writeDoubleLE(v, 179 + i * 8));
  return Buffer.concat([head, body]);
}

const state = (page: Page) => page.evaluate(() => window.__bfScene!.state());
const run = (p: Page, id: string, args: Record<string, unknown>) => p.evaluate(([i, a]) => window.__bfScene!.run(i as string, a as Record<string, unknown>), [id, args] as const);

test("ground: LAS → «Фильтр земли» («Равнина») → class 2 → «Построить ЦТМ из облака» → the DTM in the tree and in 3D", async () => {
  test.setTimeout(420_000);
  const { app, page } = await launch({ BF_E2E: "1" });
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-ground-"));
  const api = (url: string) =>
    page.evaluate(async (u) => {
      const c = window.bf!.core()!;
      const r = await fetch(`${c.baseUrl}/api/v1${u}`, { headers: { "X-BF-Token": c.token } });
      return (await r.json()) as Record<string, unknown>;
    }, url);
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    await waitFrame(page);
    await holesOnlyDemo(page);
    const surfaces = (await state(page)).wireframes.layers;

    // ---- the cloud ------------------------------------------------------------------------------------------
    const survey = path.join(dir, "ground.las");
    fs.writeFileSync(survey, las());
    await app.evaluate(({ dialog }, f) => {
      dialog.showOpenDialog = (async () => ({ canceled: false, filePaths: f })) as typeof dialog.showOpenDialog;
    }, [survey]);
    await page.getByRole("button", { name: "Облако точек", exact: true }).click();
    const wizard = page.getByRole("dialog", { name: "Облако точек" });
    await expect(wizard.getByTestId("objects-cloud")).toContainText("точек: 48 000", { timeout: 60_000 });
    await wizard.getByRole("button", { name: "Импортировать" }).click();
    await expect(wizard).toHaveCount(0);
    const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
    const row = tree.getByRole("treeitem", { name: /^ground, 48\s000 точек/ });
    await expect(row).toBeVisible({ timeout: 120_000 });
    expect((await run(page, "scene.view", { view: "top" })).ok).toBe(true);
    expect((await run(page, "scene.fit", {})).ok).toBe(true);

    // ---- «Фильтр земли…» from «Свойства» ---------------------------------------------------------------------
    await row.getByText("ground", { exact: true }).click();
    const props = page.getByLabel("Правая панель").getByTestId("props-cloud");
    await expect(props.getByTestId("props-cloud-classes")).toContainText("1 — не классифицирована");
    await expect(props.getByTestId("props-cloud-ground")).toContainText("Фильтр земли не запускался");
    await props.getByRole("button", { name: "Фильтр земли…" }).click();
    const filter = page.getByRole("dialog", { name: "Фильтр земли" });
    await expect(filter.getByLabel("Облако точек")).toHaveValue(/\d+/);
    await expect(filter.getByLabel("Жёсткость ткани RI")).toHaveValue("2"); // «Карьер» — the default
    await expect(filter.getByLabel("Шаг ткани CR, м")).toBeDisabled(); // the numbers of a profile
    await filter.getByRole("radio", { name: "Свой" }).check();
    await filter.getByLabel("Шаг ткани CR, м").fill("0,05");
    await expect(filter).toContainText("«Шаг ткани CR» — число от 0,1 до 50 м");
    await expect(filter.getByRole("button", { name: "Запустить" })).toBeDisabled();
    await filter.getByRole("radio", { name: "Равнина" }).check();
    await expect(filter.getByLabel("Шаг ткани CR, м")).toHaveValue("1");
    await expect(filter.getByLabel("Жёсткость ткани RI")).toHaveValue("3");
    await expect(filter.getByLabel("Искать землю у вертикальных граней")).not.toBeChecked(); // «Равнина» has no faces
    await page.screenshot({ path: shot("ground-window.png") });
    await filter.getByRole("button", { name: "Запустить" }).click();
    await expect(filter).toHaveCount(0);

    // ---- the classes of the filter -----------------------------------------------------------------------------
    const classes = props.getByTestId("props-cloud-classes");
    await expect(classes).toContainText("2 — земля", { timeout: 180_000 });
    await expect(props.getByTestId("props-cloud-ground")).toContainText("профиль «Равнина»");
    await expect(props.getByLabel("Режим цвета").locator("option[value=h]")).not.toHaveAttribute("disabled", "");
    const files = (await api("/lines/files")).files as { name: string; layers: { id: number; cloud: { classes: Record<string, number>; modes: string[] } | null }[] }[];
    const cloud = files.find((f) => f.name === "ground.las")!.layers[0];
    expect(cloud.cloud!.classes["2"]).toBeGreaterThan(30_000); // most of the slope is ground
    expect(cloud.cloud!.modes).toContain("h");
    await waitFrame(page);
    await page.screenshot({ path: shot("ground-classes.png") });

    // ---- «Моделирование › Поверхности › Построить ЦТМ из облака» --------------------------------------------
    await page.getByRole("tab", { name: "Моделирование" }).click();
    await page.getByRole("button", { name: "Построить ЦТМ из облака", exact: true }).click();
    const build = page.getByRole("dialog", { name: "Построение ЦТМ" });
    await expect(build.getByLabel("Облако «ground»")).toBeChecked(); // the selected cloud
    await expect(build.getByLabel("Класс 2 — земля облака «ground»")).toBeChecked(); // its ground
    const picked = build.getByRole("group", { name: "Классы облака «ground»" });
    await expect(picked.getByRole("checkbox")).not.toHaveCount(1); // the other classes of the filter are offered…
    await expect(picked.getByRole("checkbox", { checked: true })).toHaveCount(1); // …and only the ground is taken
    await build.getByLabel("Шаг прореживания облака «ground», м").fill("0");
    await expect(build).toContainText("от 0,01 до 1000 м");
    await build.getByLabel("Шаг прореживания облака «ground», м").fill("1,5");
    await build.getByLabel("Название ЦТМ").fill("Земля облака");
    await build.getByRole("button", { name: "Построить" }).click();
    await expect(build).toHaveCount(0);
    const dtmRow = tree.getByRole("treeitem", { name: "Земля облака", exact: true });
    await expect(dtmRow).toBeVisible({ timeout: 180_000 });
    await expect(dtmRow).toHaveAttribute("data-testid", /^tree-dtm-\d+$/); // «Сразу в раздел ЦТМ»
    await expect.poll(async () => (await state(page)).wireframes.layers, { timeout: 60_000 }).toBe(surfaces + 1);
    expect((await run(page, "scene.fit", {})).ok).toBe(true);
    await waitFrame(page);
    await page.screenshot({ path: shot("ground-dtm.png") });
  } finally {
    await close(app);
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
