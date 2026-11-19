// Э7.1, подшаги 7.1.14–7.1.15 (ADR 0014 §2, §6) under software WebGL2: «Главная › Загрузка › Облако точек» opens
// the wizard of a cloud — the card of a LAS written here (1.2, format 0: version, records, CRS, classes of the
// sample), a `.laz` refused with the hint of the core; the import gives a line file with one layer — the cloud —
// in the data tree and in the 3D window; its «Свойства» tell the classes by their ASPRS names, a class hidden and
// another colour mode are kept by the project in the view key `cloud_styles`; a click on a point shows its class.
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { close, holesOnlyDemo, launch, shot, waitFrame } from "./helpers";

const NX = 60;
const NY = 50;
/** Every 10th point is high vegetation (5), every 37th — noise (7), the rest ground (2). */
const classOf = (k: number): number => (k % 37 === 0 ? 7 : k % 10 === 0 ? 5 : 2);

/** A LAS 1.2 of point format 0 (ASPRS LAS 1.4 R15, tables 3 and 7) over the demo holes: x 5000…5400, y 8000…8300. */
function las(): Buffer {
  const n = NX * NY;
  const scale = 0.01;
  const offset = [5000, 8000, 300];
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
      const cls = classOf(k);
      const x = 5000 + (400 * i) / (NX - 1);
      const y = 8000 + (300 * j) / (NY - 1);
      const z = 300 + 0.02 * i + (cls === 5 ? 6 : cls === 7 ? -4 : 0);
      [x, y, z].forEach((v, a) => {
        box[a * 2] = Math.min(box[a * 2], v);
        box[a * 2 + 1] = Math.max(box[a * 2 + 1], v);
      });
      const at = k * 20;
      body.writeInt32LE(Math.round((x - offset[0]) / scale), at);
      body.writeInt32LE(Math.round((y - offset[1]) / scale), at + 4);
      body.writeInt32LE(Math.round((z - offset[2]) / scale), at + 8);
      body.writeUInt8(1 | (1 << 3), at + 14); // return 1 of 1
      body.writeUInt8(cls, at + 15);
    }
  }
  [scale, scale, scale, ...offset].forEach((v, i) => head.writeDoubleLE(v, 131 + i * 8));
  // max x, min x, max y, min y, max z, min z
  [box[1], box[0], box[3], box[2], box[5], box[4]].forEach((v, i) => head.writeDoubleLE(v, 179 + i * 8));
  return Buffer.concat([head, body]);
}

const run = (p: Page, id: string, args: Record<string, unknown>) => p.evaluate(([i, a]) => window.__bfScene!.run(i as string, a as Record<string, unknown>), [id, args] as const);

test("cloud: wizard «Облако точек» (LAS, LAZ refused) → tree and 3D → «Свойства»: classes, colour mode, picked point", async () => {
  test.setTimeout(300_000);
  const { app, page } = await launch({ BF_E2E: "1" });
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-cloud-"));
  const pickNext = (files: string[]) =>
    app.evaluate(({ dialog }, f) => {
      dialog.showOpenDialog = (async () => ({ canceled: false, filePaths: f })) as typeof dialog.showOpenDialog;
    }, files);
  const api = (method: string, url: string) =>
    page.evaluate(
      async ([m, u]) => {
        const c = window.bf!.core()!;
        const r = await fetch(`${c.baseUrl}/api/v1${u}`, { method: m, headers: { "X-BF-Token": c.token } });
        return (await r.json()) as Record<string, unknown>;
      },
      [method, url] as const,
    );
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    await waitFrame(page);
    await holesOnlyDemo(page);

    // ---- 7.1.14: the wizard -------------------------------------------------------------------------------
    const survey = path.join(dir, "survey.las");
    fs.writeFileSync(survey, las());
    const packed = path.join(dir, "packed.laz");
    fs.writeFileSync(packed, las());
    await pickNext([survey, packed]);
    await page.getByRole("button", { name: "Облако точек", exact: true }).click();
    const win = page.getByRole("dialog", { name: "Облако точек" });
    const card = win.getByTestId("objects-file").filter({ hasText: "survey.las" });
    await expect(card.getByTestId("objects-cloud")).toContainText("точек: 3 000", { timeout: 60_000 });
    await expect(card).toContainText("LAS 1.2, формат точек 0 (запись 20 байт)");
    await expect(card).toContainText("BF2 E2E");
    await expect(card.getByTestId("objects-cloud-classes")).toContainText("2 — земля");
    await expect(card.getByTestId("objects-cloud-classes")).toContainText("7 — низкая точка (шум)");
    const lazCard = win.getByTestId("objects-file").filter({ hasText: "packed.laz" });
    await expect(lazCard).toContainText("Сжатые облака LAZ не читаются.");
    await expect(lazCard).toContainText("laszip");
    // «каждая k-я запись»: a wrong one stops the import, a right one is read again by the core
    const every = card.getByLabel("Каждая k-я запись survey.las");
    await every.fill("0");
    await every.press("Enter");
    await expect(card).toContainText("«0» — не целое число");
    await expect(win.getByRole("button", { name: "Импортировать" })).toBeDisabled();
    await every.fill("2");
    await every.press("Enter");
    await expect(card.getByTestId("objects-cloud")).toContainText("точек: 1 500 из 3 000 записей (каждая 2-я)", { timeout: 30_000 });
    await every.fill("");
    await every.press("Enter");
    await expect(card.getByTestId("objects-cloud")).toContainText("точек: 3 000", { timeout: 30_000 });
    await lazCard.getByRole("button", { name: "Убрать" }).click();
    await expect(win.getByTestId("objects-file")).toHaveCount(1);
    await page.screenshot({ path: shot("cloud-wizard.png") });
    await win.getByRole("button", { name: "Импортировать" }).click();
    await expect(win).toHaveCount(0);

    // ---- the tree and the 3D window -------------------------------------------------------------------------
    const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
    const row = tree.getByRole("treeitem", { name: /^survey, 3\s000 точек/ });
    await expect(row).toBeVisible({ timeout: 120_000 });
    await expect.poll(async () => (await page.evaluate(() => window.__bfScene!.state())).lines.layers, { timeout: 60_000 }).toBe(1);
    expect((await run(page, "scene.view", { view: "top" })).ok).toBe(true);
    expect((await run(page, "scene.fit", {})).ok).toBe(true);
    await waitFrame(page);
    const files = (await api("GET", "/lines/files")).files as { name: string; layers: { id: number; cloud: { classes: Record<string, number> } | null }[] }[];
    const layer = files.find((f) => f.name === "survey.las")!.layers[0];
    expect(layer.cloud?.classes).toEqual({ "2": 2627, "5": 291, "7": 82 });

    // ---- 7.1.15: «Свойства» -------------------------------------------------------------------------------
    await row.getByText("survey", { exact: true }).click();
    const props = page.getByLabel("Правая панель").getByTestId("props-cloud");
    await expect(props).toContainText("Облако точек");
    await expect(props).toContainText("LAS 1.2, формат точек 0");
    const classes = props.getByTestId("props-cloud-classes");
    await expect(classes.getByRole("row")).toHaveCount(4); // the head and three classes
    await expect(classes).toContainText("5 — высокая растительность");
    const styleNow = async () => ((await api("GET", "/view")).values as Record<string, { value: unknown }>).cloud_styles?.value as Record<string, { color?: string; hidden?: number[] }> | undefined;
    await classes.getByLabel("Показать класс 7 — низкая точка (шум)").uncheck();
    await expect(classes).toContainText("Показано точек: 2 918 из 3 000");
    await expect.poll(async () => (await styleNow())?.[String(layer.id)]?.hidden, { timeout: 10_000 }).toEqual([7]);
    const mode = props.getByLabel("Режим цвета");
    await expect(mode).toHaveValue("class");
    await expect(mode.locator("option[value=h]")).toHaveAttribute("disabled", ""); // no ground filter yet
    await mode.selectOption("layer");
    await expect.poll(async () => (await styleNow())?.[String(layer.id)], { timeout: 10_000 }).toEqual({ hidden: [7], color: "layer" });
    await mode.selectOption("class");
    await waitFrame(page);

    // a click on a point of the ground (the middle of the grid, away from the holes' collars): its class
    const target = { x: 5000 + (400 * 31) / (NX - 1), y: 8000 + (300 * 3) / (NY - 1), z: 300 + 0.02 * 31 };
    const at = (await page.evaluate((p) => window.__bfScene!.screen(p.x, p.y, p.z), target))!;
    await page.mouse.click(at.x, at.y);
    const point = props.getByTestId("props-cloud-point");
    await expect(point).toContainText("Класс", { timeout: 15_000 });
    await expect(point).toContainText(/2 — земля|5 — высокая растительность/);
    await page.screenshot({ path: shot("cloud-props.png") });
  } finally {
    await close(app);
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
