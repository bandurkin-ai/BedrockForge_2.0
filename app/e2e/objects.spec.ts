// Э7, подшаги 7.21–7.23 (ADR 0011 §5; ТЗ 7.18–7.20, 7.35 Д-07, Д-16): «Линии» opens the composition of «Импорт
// файлов» — a real Micromine STR, a table of points, an image with a world file and a file that will not load;
// «Уровень растров по Z» takes a comma; the import fills «Линии» and «Растры» of the data tree (check boxes,
// selection, Esc). «+» of «Скважины» → «Импорт канав…»: polylines of a STR with samples become a set. A DTM
// built from the points stands in «ЦТМ» without a gear: a click opens «Свойства», the menu returns it to «Каркасы».
import { expect, test } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import zlib from "node:zlib";
import { close, holesOnlyDemo, launch, shot } from "./helpers";

const REAL = path.resolve(__dirname, "..", "..", "tests", "data", "real");

function crc32(data: Buffer): number {
  let c = ~0;
  for (const b of data) {
    c ^= b;
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  return ~c >>> 0;
}

/** An 8-bit RGB PNG with a gradient (no image library in the tests). */
function png(w: number, h: number): Buffer {
  const row = w * 3 + 1;
  const raw = Buffer.alloc(row * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) raw.set([(x * 255) / w, (y * 255) / h, 160], y * row + 1 + x * 3);
  const chunk = (type: string, data: Buffer) => {
    const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const out = Buffer.alloc(8 + body.length);
    out.writeUInt32BE(data.length, 0);
    body.copy(out, 4);
    out.writeUInt32BE(crc32(body), 4 + body.length);
    return out;
  };
  const head = Buffer.alloc(13);
  head.writeUInt32BE(w, 0);
  head.writeUInt32BE(h, 4);
  head.set([8, 2, 0, 0, 0], 8);
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk("IHDR", head), chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
}

test("objects: composition and raster level, tree of lines and rasters, trenches, DTM without a gear", async () => {
  test.setTimeout(420_000);
  const { app, page } = await launch();
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-wizard-"));
  const pickNext = (files: string[]) =>
    app.evaluate(({ dialog }, f) => {
      dialog.showOpenDialog = (async () => ({ canceled: false, filePaths: f })) as typeof dialog.showOpenDialog;
    }, files);
  const api = (method: string, url: string, body?: unknown) =>
    page.evaluate(
      async ([m, u, b]) => {
        const c = window.bf!.core()!;
        const r = await fetch(`${c.baseUrl}/api/v1${u}`, { method: m as string, headers: { "X-BF-Token": c.token, "Content-Type": "application/json" }, body: b === null ? undefined : JSON.stringify(b) });
        return { status: r.status, body: (await r.json()) as Record<string, unknown> };
      },
      [method, url, body ?? null] as const,
    );
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
    // the demo has lines, a plan and a DTM «Рельеф» of its own (7.29): the tree of this spec starts without them
    await expect(tree.getByTestId(/^tree-dtm-\d+$/)).toHaveCount(1, { timeout: 60_000 });
    await holesOnlyDemo(page);
    await expect(tree.getByTestId(/^tree-dtm-\d+$/)).toHaveCount(0, { timeout: 60_000 });
    await expect(tree.getByRole("treeitem", { name: /План участка/ })).toHaveCount(0);
    await expect(tree.getByRole("treeitem", { name: /Топосъёмка/ })).toHaveCount(0);

    // ---- 7.21: the composition of «Импорт файлов» -------------------------------------------------------
    const str = path.join(dir, "Восточный Цители-Сопели.STR");
    fs.copyFileSync(path.join(REAL, "Восточный Цители-Сопели.STR"), str);
    const image = path.join(dir, "ortho.png");
    fs.writeFileSync(image, png(256, 192));
    const world = path.join(dir, "ortho.pgw");
    fs.writeFileSync(world, ["2.0", "0.0", "0.0", "-2.0", "4951.0", "8349.0", ""].join("\n"));
    const relief = path.join(dir, "relief.csv");
    const grid = [0, 1, 2, 3].flatMap((i) => [0, 1, 2, 3].map((j) => `${5000 + i * 100},5;${8000 + j * 100};${300 + i * 7 - j * 3},25`));
    fs.writeFileSync(relief, `X;Y;Z\n${grid.join("\n")}\n`, "utf-8");
    const notes = path.join(dir, "notes.md");
    fs.writeFileSync(notes, "не данные");

    await pickNext([str, image, world, relief, notes]);
    await page.getByRole("button", { name: "Линии", exact: true }).click();
    const win = page.getByRole("dialog", { name: "Импорт файлов" });
    const totals = win.getByTestId("objects-totals");
    await expect(totals).toHaveText("Файлов: 3 · слоёв: 6 · линий: 5 · вершин: 37 · точек: 16 · растров: 1", { timeout: 60_000 });
    await expect(win.getByTestId("objects-file")).toHaveCount(3);
    await expect(win.getByTestId("objects-file").filter({ hasText: "Восточный Цители-Сопели.STR" })).toContainText("Стринги Micromine");
    await expect(win.getByTestId("objects-file").filter({ hasText: "ortho.png" })).toContainText("привязка: World-файл (ortho.pgw)");
    await expect(win.getByTestId("objects-rest")).toContainText("notes.md");

    const level = win.getByTestId("raster-z-level");
    await expect(level).toContainText("ortho.png: в плане, под самым низким объектом");
    await level.getByLabel("Высота, м").fill("250,5"); // a comma
    await level.getByLabel("Сдвиг, м").fill("-0.5"); // a point
    await expect(level).toContainText("ortho.png: в плане, высота 250 м");
    await level.getByLabel("Сдвиг, м").fill("5 м");
    await expect(level).toContainText("«5 м» — не число");
    await expect(win.getByRole("button", { name: "Импортировать" })).toBeDisabled();
    await level.getByLabel("Сдвиг, м").fill("−0,5");
    await page.screenshot({ path: shot("objects-wizard.png") });
    await win.getByRole("button", { name: "Импортировать" }).click();
    await expect(win).toHaveCount(0);

    // ---- 7.23: «Линии» and «Растры» of the tree ----------------------------------------------------------
    const file = tree.getByRole("treeitem", { name: "Восточный Цители-Сопели.STR, 5 слоёв" });
    await expect(file).toBeVisible({ timeout: 90_000 });
    const layer = tree.getByRole("treeitem", { name: "TS-1, 1 линия, 8 вершин" });
    await expect(layer).toBeVisible();
    await expect(tree.getByRole("treeitem", { name: "relief, 16 точек" })).toBeVisible();
    const raster = tree.getByRole("treeitem", { name: "ortho, в плане, высота 250 м" }); // 250,5 − 0,5: the level was applied
    await expect(raster).toBeVisible();
    const stored = (await api("GET", "/rasters")).body.rasters as { elevation: number }[];
    expect(stored[0].elevation).toBe(250);

    // the check box of a layer; the one of its file follows and shows every layer again
    await layer.getByRole("checkbox").uncheck();
    await expect(layer.getByRole("checkbox")).not.toBeChecked();
    await expect(file.getByRole("checkbox")).not.toBeChecked();
    await file.getByRole("checkbox").check();
    await expect(layer.getByRole("checkbox")).toBeChecked();
    await raster.getByRole("checkbox").uncheck();
    await expect(raster.getByRole("checkbox")).not.toBeChecked();
    // the arrow of the file collapses its layers; a click on a layer selects it, Esc clears the selection
    await file.getByRole("button", { name: /Свернуть/ }).click();
    await expect(layer).toHaveCount(0);
    await file.getByRole("button", { name: /Развернуть/ }).click();
    await layer.getByText("TS-1", { exact: true }).click();
    await expect(layer).toHaveAttribute("aria-selected", "true");
    await page.locator("canvas").first().focus().catch(() => undefined);
    await page.keyboard.press("Escape");
    await expect(layer).toHaveAttribute("aria-selected", "false");

    // ---- 7.22: «Канавы по полилиниям» -----------------------------------------------------------------
    const lines = path.join(dir, "Без имени.STR");
    fs.copyFileSync(path.join(REAL, "Без имени.STR"), lines);
    const samples = path.join(dir, "samples.csv");
    fs.writeFileSync(samples, "HOLEID;FROM;TO;AU\nБез кода-1;0;5;1,2\nБез кода-1;5;12,5;0,4\nБез кода-2;0;3;2,5\n", "utf-8");
    await tree.getByTestId("tree-section-holes").getByRole("button", { name: "Новый набор, импорт скважин, импорт канав" }).click();
    await page.getByRole("menuitem", { name: "Импорт канав…" }).click();
    const wiz = page.getByRole("dialog", { name: "Импорт канав" });
    await expect(wiz.getByRole("button", { name: /^1\. Канавы по полилиниям/ })).toBeVisible();
    await pickNext([lines]);
    await wiz.getByRole("button", { name: "Выбрать файл…" }).click();
    const preview = wiz.getByTestId("trench-preview");
    await expect(preview).toContainText("Канав: 3 · вершин: 12", { timeout: 60_000 });
    const first = preview.getByRole("row").filter({ hasText: "Без кода-1" });
    const before = await first.innerText();
    await first.getByRole("checkbox").check(); // «Обратить»: the collar moves to the other end
    await expect.poll(() => first.innerText(), { timeout: 30_000 }).not.toBe(before);
    await expect(first.getByRole("checkbox")).toBeChecked();
    await wiz.getByRole("button", { name: "Далее" }).click();
    await pickNext([samples]);
    await wiz.getByRole("button", { name: "Выбрать таблицу проб…" }).click();
    await expect(wiz.getByTestId("import-file")).toContainText("строк: 3", { timeout: 60_000 });
    await wiz.getByRole("button", { name: "Далее" }).click();
    await expect(wiz.getByTestId("import-summary")).toContainText("Без имени.STR: Канав: 3 · вершин: 12");
    await page.screenshot({ path: shot("trenches-wizard.png") });
    await wiz.getByRole("button", { name: "Импортировать" }).click();
    await expect(wiz).toHaveCount(0);
    await expect(tree.getByRole("treeitem", { name: "Канавы, 3 скв." })).toBeVisible({ timeout: 90_000 });

    // ---- 7.23, Д-07: a DTM has no gear ------------------------------------------------------------------
    const files = (await api("GET", "/lines/files")).body.files as { name: string; layers: { id: number }[] }[];
    const points = files.find((f) => f.name === "relief.csv")!.layers[0].id;
    const built = await api("POST", "/wireframes/build-dtm", { name: "Рельеф", layer_ids: [points] });
    expect(built.status, JSON.stringify(built.body)).toBe(200);
    const dtm = tree.getByRole("treeitem", { name: "Рельеф", exact: true });
    await expect(dtm).toBeVisible({ timeout: 90_000 });
    await expect(dtm).toHaveAttribute("data-testid", /^tree-dtm-\d+$/);
    await expect(dtm.getByRole("button")).toHaveCount(0); // no «Настройки», no button at all
    await page.getByRole("button", { name: "Закрыть правую панель" }).click();
    await expect(page.getByLabel("Правая панель")).toHaveCount(0);
    await dtm.getByText("Рельеф", { exact: true }).click(); // the click opens the settings: «Свойства»
    await expect(dtm).toHaveAttribute("aria-selected", "true");
    await expect(page.getByLabel("Правая панель").getByRole("tab", { name: "Свойства" })).toHaveAttribute("aria-selected", "true");
    await dtm.click({ button: "right" });
    await expect(page.getByRole("menuitem", { name: "Настройки ЦТМ…" })).toBeVisible();
    await page.getByRole("menuitem", { name: "Вернуть в каркасы" }).click();
    await page.getByRole("dialog", { name: "Вернуть в каркасы" }).getByRole("button", { name: "Вернуть" }).click();
    const solid = tree.getByTestId(/^tree-wireframe-\d+$/);
    await expect(solid).toBeVisible({ timeout: 30_000 });
    await expect(tree.getByTestId(/^tree-dtm-\d+$/)).toHaveCount(0);
    await solid.getByRole("button", { name: "Перенести в ЦТМ" }).click(); // the sign of the row
    await expect(tree.getByTestId(/^tree-dtm-\d+$/)).toBeVisible({ timeout: 30_000 });
    await expect(tree.getByTestId(/^tree-wireframe-\d+$/)).toHaveCount(0);
    await page.screenshot({ path: shot("objects-tree.png") });
  } finally {
    await close(app);
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
