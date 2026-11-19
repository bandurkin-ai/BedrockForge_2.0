// Э7, подшаги 7.27–7.28 (ADR 0011 §5; ТЗ 7.18–7.20, 7.35 Д-07): «Свойства» of the object chosen in the tree or
// in the 3D window. A raster: «Сдвинуть» is not available without a height, «Высота» takes a comma, a shift of
// −5 lowers it; «На текущий разрез» asks in its own window and stands the raster on the section line — then −5
// lowers every corner. A layer: attributes by pages, the picked point, the colour, the name. «Построить ЦТМ»
// of the ribbon opens its window; the settings of the DTM are at the top of «Свойства» — a vertical raster is
// not offered; attributes of the user; «Вернуть в каркасы». The windows of export write real files.
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { close, launch, shot, waitFrame } from "./helpers";
import { gradientPng } from "./images";

const dxf = (entities: (string | number)[][]) => ["0", "SECTION", "2", "ENTITIES", ...entities.flat().map(String), "0", "ENDSEC", "0", "EOF", ""].join("\r\n");

const GRID = 8; // 8 × 8 points: two pages of attributes (50 rows a page)
const pointAt = (k: number) => ({ x: 5000 + Math.floor(k / GRID) * 50, y: 7500 + (k % GRID) * 50, z: 300 + Math.floor(k / GRID) * 2 - (k % GRID) });

/** Two roads and a closed outline, 64 named points, two images in plan over the points. */
function makeFiles(dir: string): string[] {
  const files = {
    lines: path.join(dir, "roads.dxf"),
    points: path.join(dir, "relief.csv"),
    ortho: path.join(dir, "ortho.png"),
    orthoWorld: path.join(dir, "ortho.pgw"),
    map: path.join(dir, "map.png"),
    mapWorld: path.join(dir, "map.pgw"),
  };
  fs.writeFileSync(
    files.lines,
    dxf([
      [0, "LINE", 8, "ROADS", 10, 4960, 20, 7900, 30, 320, 11, 5440, 21, 7900, 31, 330],
      [0, "LINE", 8, "ROADS", 10, 5440, 20, 7900, 30, 330, 11, 5440, 21, 7860, 31, 331],
      [0, "LWPOLYLINE", 8, "PIT", 90, 4, 70, 1, 38, 300, 10, 4990, 20, 7490, 10, 5360, 20, 7490, 10, 5360, 20, 7860, 10, 4990, 20, 7860],
    ]),
  );
  const rows = Array.from({ length: GRID * GRID }, (_, k) => {
    const p = pointAt(k);
    return `${p.x};${p.y};${String(p.z).replace(".", ",")};Репер ${k + 1}`;
  });
  fs.writeFileSync(files.points, `X;Y;Z;NAME\n${rows.join("\n")}\n`, "utf-8");
  for (const image of [files.ortho, files.map]) fs.writeFileSync(image, gradientPng(256, 192));
  // 2 m pixels: x 4950…5462, y 7526…7910 — over the points; no height in a world file
  for (const world of [files.orthoWorld, files.mapWorld]) fs.writeFileSync(world, ["2.0", "0.0", "0.0", "-2.0", "4951.0", "7909.0", ""].join("\n"));
  return Object.values(files);
}

test("properties: raster shift and section, layer attributes and pick, DTM window and settings, exports", async () => {
  test.setTimeout(420_000);
  const { app, page } = await launch({ BF_E2E: "1" });
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-props-"));
  const api = (method: string, url: string, body?: unknown) =>
    page.evaluate(
      async ([m, u, b]) => {
        const c = window.bf!.core()!;
        const r = await fetch(`${c.baseUrl}/api/v1${u}`, { method: m as string, headers: { "X-BF-Token": c.token, "Content-Type": "application/json" }, body: b === null ? undefined : JSON.stringify(b) });
        return { status: r.status, body: (await r.json()) as Record<string, unknown> };
      },
      [method, url, body ?? null] as const,
    );
  const saveNext = (file: string) =>
    app.evaluate(({ dialog }, f) => {
      dialog.showSaveDialog = (async () => ({ canceled: false, filePath: f })) as typeof dialog.showSaveDialog;
    }, file);
  type Raster = { id: number; name: string; elevation: number | null; vertical: boolean; opacity: number; georef: { corners3d: number[][] } };
  const rasterNow = async (id: number) => (await api("GET", `/rasters/${id}`)).body as unknown as Raster;
  const cornersZ = async (id: number) => (await rasterNow(id)).georef.corners3d.map((c) => c[2]);
  type Wireframe = { id: number; name: string; dtm: { raster_id: number | null; clip: boolean; tint: string; opacity: number; shade: boolean } | null; attrs: { name: string; value: unknown }[] };
  const wireframes = async () => (await api("GET", "/wireframes")).body.wireframes as Wireframe[];
  const run = (p: Page, id: string, args: Record<string, unknown>) => p.evaluate(([i, a]) => window.__bfScene!.run(i as string, a as Record<string, unknown>), [id, args] as const);

  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    // an empty project: only the objects of this test (the demo has its own relief, plan and lines)
    await page.getByLabel("Лента").getByRole("button", { name: "Новый проект" }).click();
    await page.getByRole("dialog").last().getByRole("textbox").fill("Свойства объектов");
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("project-selector")).toContainText("Свойства объектов", { timeout: 60_000 });
    await waitFrame(page);
    const started = await api("POST", "/import/objects", { files: makeFiles(dir) });
    expect(started.status, JSON.stringify(started.body)).toBe(200);
    const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
    const props = page.getByLabel("Правая панель");
    const orthoRow = tree.getByRole("treeitem", { name: /^ortho, / });
    await expect(orthoRow).toBeVisible({ timeout: 90_000 });
    const rasters = (await api("GET", "/rasters")).body.rasters as Raster[];
    const ortho = rasters.find((r) => r.name === "ortho")!;
    const map = rasters.find((r) => r.name === "map")!;

    // ---- 7.27: a raster — «Высота», «Сдвинуть» -------------------------------------------------------------
    await orthoRow.getByText("ortho", { exact: true }).click();
    const rp = props.getByTestId("props-raster");
    await expect(rp).toContainText("World-файл");
    await expect(rp).toContainText("в плане, под самым низким объектом");
    await expect(rp.getByRole("button", { name: "Сдвинуть" })).toBeDisabled(); // no height: nothing to move
    await expect(rp.getByTestId("raster-shift-note")).toContainText("У растра не задана высота");
    await expect(rp.getByRole("button", { name: "На текущий разрез" })).toBeDisabled(); // no section line yet
    await rp.getByLabel("Высота, м").fill("250,5"); // a comma
    await rp.getByRole("button", { name: "Применить" }).click();
    await expect(tree.getByRole("treeitem", { name: "ortho, в плане, высота 250,5 м" })).toBeVisible();
    expect((await rasterNow(ortho.id)).elevation).toBe(250.5);
    await rp.getByLabel("Сдвиг по Z, м").fill("5 м");
    await rp.getByRole("button", { name: "Сдвинуть" }).click();
    await expect(rp.getByRole("alert")).toContainText("«5 м» — не число");
    await rp.getByLabel("Сдвиг по Z, м").fill("−5");
    await rp.getByRole("button", { name: "Сдвинуть" }).click();
    await expect(tree.getByRole("treeitem", { name: "ortho, в плане, высота 245,5 м" })).toBeVisible();
    expect((await rasterNow(ortho.id)).elevation).toBe(245.5);
    await rp.getByLabel("Непрозрачность растра").fill("0.6");
    await expect.poll(async () => (await rasterNow(ortho.id)).opacity).toBe(0.6);
    // an empty height is «not set» — under the lowest object, not 0
    await rp.getByLabel("Высота, м").fill("");
    await rp.getByRole("button", { name: "Применить" }).click();
    await expect(tree.getByRole("treeitem", { name: "ortho, в плане, под самым низким объектом" })).toBeVisible();
    expect((await rasterNow(ortho.id)).elevation).toBeNull();

    // ---- 7.27: «На текущий разрез» — own confirmation, then every corner moves ---------------------------
    expect((await run(page, "section.set", { x0: 5000, y0: 8100, az: 90, length: 400 })).ok).toBe(true);
    const place = rp.getByRole("button", { name: "На текущий разрез" });
    await expect(place).toBeEnabled();
    await rp.getByLabel("Верх Z на разрезе, м").fill("350");
    await place.click();
    const ask = page.getByRole("dialog", { name: "На текущий разрез" });
    await expect(ask).toContainText("Растр «ortho» встанет в плоскость линии разреза");
    await ask.getByRole("button", { name: "Отмена" }).click();
    expect((await rasterNow(ortho.id)).vertical).toBe(false); // declined: nothing changed
    await place.click();
    await ask.getByRole("button", { name: "Поставить" }).click();
    await expect(tree.getByRole("treeitem", { name: "ortho, вертикальный" })).toBeVisible();
    await expect(rp.getByTestId("raster-edges")).toBeVisible();
    await expect(rp.getByLabel("Левый край X")).toHaveValue("5000");
    await expect(rp.getByLabel("Правый край X")).toHaveValue("5400");
    await expect(rp.getByLabel("Верх Z", { exact: true })).toHaveValue("350");
    await expect(rp.getByLabel("Низ Z")).toHaveValue("50"); // 400 m × 192 / 256 px below the top
    expect(await cornersZ(ortho.id)).toEqual([350, 350, 50, 50]);
    await rp.getByLabel("Сдвиг по Z, м").fill("-5");
    await rp.getByRole("button", { name: "Сдвинуть" }).click();
    await expect(rp.getByLabel("Верх Z", { exact: true })).toHaveValue("345");
    expect(await cornersZ(ortho.id)).toEqual([345, 345, 45, 45]); // ТЗ 7.19: −5 lowers all the corners by 5 m
    await rp.getByLabel("Низ Z").fill("100,5");
    await rp.getByTestId("raster-edges").getByRole("button", { name: "Применить" }).click();
    await expect.poll(() => cornersZ(ortho.id)).toEqual([345, 345, 100.5, 100.5]);
    await page.screenshot({ path: shot("props-raster.png") });

    // ---- 7.27: a layer — attributes by pages, colour, name, the point picked in 3D ---------------------------
    const reliefRow = tree.getByRole("treeitem", { name: "relief, 64 точки" });
    await reliefRow.getByText("relief", { exact: true }).click();
    const lp = props.getByTestId("props-layer");
    await expect(lp).toContainText("Слой точек");
    const attrs = lp.getByTestId("props-attrs");
    await expect(attrs.getByRole("columnheader", { name: "NAME" })).toBeVisible();
    await expect(attrs).toContainText("Строки 1–50 из 64");
    await expect(attrs.getByRole("cell", { name: "Репер 1", exact: true })).toBeVisible();
    await attrs.getByRole("button", { name: "Следующая страница" }).click();
    await expect(attrs).toContainText("Строки 51–64 из 64");
    await expect(attrs.getByRole("cell", { name: "Репер 64", exact: true })).toBeVisible();
    await expect(attrs.getByRole("button", { name: "Следующая страница" })).toBeDisabled();
    const files = (await api("GET", "/lines/files")).body.files as { name: string; layers: { id: number; name: string; color: string | null }[] }[];
    const relief = files.find((f) => f.name === "relief.csv")!.layers[0];
    const layerNow = async () => (await api("GET", `/lines/layers/${relief.id}`)).body as { name: string; color: string | null };
    await lp.getByLabel("Цвет слоя").fill("#ff8800");
    await expect.poll(async () => (await layerNow()).color).toBe("#ff8800");
    await lp.getByRole("button", { name: "Без цвета" }).click();
    await expect.poll(async () => (await layerNow()).color).toBeNull();
    // a click on a point in the 3D window: its row of attributes (the north-west of the grid: clear of the mini-plan);
    // the other layers of the demo are hidden meanwhile — on the smaller window of the Windows runner a line of the
    // demo lay within the pick tolerance of the point and was taken instead («Линия № 1»)
    const others: [string, number][] = [
      ...files.flatMap((f) => f.layers.filter((l) => l.id !== relief.id).map((l): [string, number] => ["lines", l.id])),
      ...((await api("GET", "/wireframes")).body.wireframes as { id: number }[]).map((w): [string, number] => ["wireframe", w.id]),
    ];
    for (const [layer, id] of others) expect((await run(page, "layer.show", { layer, id, visible: false })).ok).toBe(true);
    expect((await run(page, "scene.view", { view: "top" })).ok).toBe(true);
    expect((await run(page, "scene.fit", {})).ok).toBe(true);
    await waitFrame(page);
    const target = pointAt(15);
    const at = (await page.evaluate((p) => window.__bfScene!.screen(p.x, p.y, p.z), target))!;
    const host = (await page.getByTestId("scene-host").boundingBox())!;
    expect(at.x > host.x && at.x < host.x + host.width && at.y > host.y && at.y < host.y + host.height, JSON.stringify({ at, host })).toBe(true);
    await page.mouse.click(at.x, at.y);
    const picked = lp.getByTestId("props-picked-line");
    await expect(picked).toContainText("Точка № 16");
    await expect(picked).toContainText("Репер 16");
    for (const [layer, id] of others) expect((await run(page, "layer.show", { layer, id, visible: true })).ok).toBe(true);
    await expect(attrs.locator("tr").filter({ hasText: "Репер 16" })).toHaveClass(/picked/); // its row of the table is marked
    await lp.getByRole("button", { name: "Переименовать…" }).click();
    const rename = page.getByRole("dialog", { name: "Переименовать: слой линий" });
    await rename.getByLabel("Название").fill("Рельеф точки");
    await rename.getByRole("button", { name: "Переименовать" }).click();
    await expect(tree.getByRole("treeitem", { name: "Рельеф точки, 64 точки" })).toBeVisible();
    await page.screenshot({ path: shot("props-layer.png") });

    // ---- 7.28: «Построить ЦТМ» of the ribbon opens its window ---------------------------------------------
    await page.getByRole("tab", { name: "Моделирование" }).click();
    await page.getByRole("button", { name: "Построить ЦТМ", exact: true }).click();
    const build = page.getByRole("dialog", { name: "Построение ЦТМ" });
    await expect(build.getByLabel("Слой «Рельеф точки»")).toBeChecked(); // the selected layer is a source already
    await build.getByLabel("Слой «Рельеф точки»").uncheck();
    await expect(build.getByRole("button", { name: "Построить" })).toBeDisabled();
    await expect(build).toContainText("Не из чего строить поверхность");
    await build.getByLabel("Слой «Рельеф точки»").check();
    await build.getByLabel("Название ЦТМ").fill("Рельеф");
    await build.getByLabel("Слой границы").selectOption({ label: "PIT (замкнутых линий: 1)" });
    await build.getByLabel("Наибольшая длина ребра, м").fill("сто");
    await expect(build).toContainText("положительное число");
    await build.getByLabel("Наибольшая длина ребра, м").fill("250,5");
    await page.screenshot({ path: shot("props-build-dtm.png") });
    await build.getByRole("button", { name: "Построить" }).click();
    await expect(build).toHaveCount(0);
    const dtmRow = tree.getByRole("treeitem", { name: "Рельеф", exact: true });
    await expect(dtmRow).toBeVisible({ timeout: 90_000 }); // «Сразу в раздел ЦТМ»
    await expect(dtmRow).toHaveAttribute("data-testid", /^tree-dtm-\d+$/);
    const dtmId = (await wireframes()).find((w) => w.name === "Рельеф")!.id;
    const dtmNow = async () => (await wireframes()).find((w) => w.id === dtmId)!;

    // ---- 7.27, Д-07: the settings of the DTM at the top of «Свойства» -------------------------------------
    await dtmRow.getByText("Рельеф", { exact: true }).click();
    const dp = props.getByTestId("props-dtm");
    const settings = dp.getByTestId("dtm-settings");
    await expect(settings).toBeVisible();
    expect(await dp.locator("fieldset, dl").first().getAttribute("data-testid")).toBe("dtm-settings"); // above everything else
    await expect(settings.getByLabel("Растр на рельефе").locator("option")).toHaveText(["— без растра —", "map"]); // ortho is vertical
    await expect(settings).toContainText("Вертикальные растры (1 растр) на рельеф не кладутся");
    await settings.getByLabel("Растр на рельефе").selectOption({ label: "map" });
    await expect.poll(async () => (await dtmNow()).dtm?.raster_id).toBe(map.id);
    await expect(settings.getByLabel("Скрыть данные за ЦТМ")).toBeChecked(); // the default of a new DTM
    await settings.getByLabel("Скрыть данные за ЦТМ").uncheck();
    await settings.getByLabel("Тени рельефа").uncheck();
    await settings.getByLabel("Непрозрачность растра на ЦТМ").fill("0.5");
    await settings.getByLabel("Цвет растра").fill("#ffcc00");
    await expect.poll(async () => (await dtmNow()).dtm).toEqual({ raster_id: map.id, clip: false, tint: "#ffcc00", opacity: 0.5, shade: false });
    await waitFrame(page);
    expect((await page.evaluate(() => window.__bfScene!.state())).wireframes.draped).toContain(dtmId); // the 3D window follows
    // a raster a DTM wears is not deleted: the reason of the core
    await tree.getByRole("treeitem", { name: /^map, / }).click({ button: "right" });
    await page.getByRole("menuitem", { name: "Удалить…" }).click();
    await page.getByRole("dialog", { name: "Удалить растр" }).getByRole("button", { name: "Удалить" }).click();
    const refused = page.getByRole("dialog", { name: "Удалить" });
    await expect(refused).toContainText("ЦТМ");
    await refused.getByRole("button", { name: "Закрыть" }).click();
    await expect(tree.getByRole("treeitem", { name: /^map, / })).toBeVisible();

    // attributes of the user: add, change, rename, delete
    const user = dp.getByTestId("props-user-attrs");
    await user.getByLabel("Название нового атрибута").fill("Участок");
    await user.getByLabel("Значение нового атрибута").fill("Северный");
    await user.getByRole("button", { name: "Добавить" }).click();
    await expect.poll(async () => (await dtmNow()).attrs).toEqual([{ name: "Участок", value: "Северный" }]);
    await user.getByLabel("Значение атрибута «Участок»").fill("12,5");
    await user.getByLabel("Значение атрибута «Участок»").press("Enter");
    await expect.poll(async () => (await dtmNow()).attrs).toEqual([{ name: "Участок", value: 12.5 }]); // a number with a comma
    await user.getByRole("button", { name: "Переименовать атрибут «Участок»" }).click();
    const attrName = page.getByRole("dialog", { name: "Переименовать атрибут «Участок»" });
    await attrName.getByLabel("Название").fill("Блок");
    await attrName.getByRole("button", { name: "Переименовать" }).click();
    await expect.poll(async () => (await dtmNow()).attrs).toEqual([{ name: "Блок", value: 12.5 }]);
    await page.screenshot({ path: shot("props-dtm.png") });
    await user.getByRole("button", { name: "Удалить атрибут «Блок»" }).click();
    await page.getByRole("dialog", { name: "Удалить атрибут" }).getByRole("button", { name: "Удалить" }).click();
    await expect.poll(async () => (await dtmNow()).attrs).toEqual([]);

    // «Вернуть в каркасы» resets the settings and takes the raster off; «Перенести в ЦТМ» of «Свойства»
    await settings.getByRole("button", { name: "Вернуть в каркасы" }).click();
    await page.getByRole("dialog", { name: "Вернуть в каркасы" }).getByRole("button", { name: "Вернуть" }).click();
    const wp = props.getByTestId("props-wireframe");
    await expect(wp).toBeVisible();
    expect((await dtmNow()).dtm).toBeNull();
    await wp.getByRole("button", { name: "Перенести в ЦТМ" }).click();
    await expect(props.getByTestId("dtm-settings")).toBeVisible();
    expect((await dtmNow()).dtm).toEqual({ raster_id: null, clip: true, tint: "#ffffff", opacity: 1, shade: true }); // the settings were reset
    // «Настройки ЦТМ…» of the menu is a registry command: it opens «Свойства» again
    await page.getByRole("button", { name: "Закрыть правую панель" }).click();
    await expect(page.getByLabel("Правая панель")).toHaveCount(0);
    await tree.getByTestId(`tree-dtm-${dtmId}`).click({ button: "right" });
    await page.getByRole("menuitem", { name: "Настройки ЦТМ…" }).click();
    await expect(page.getByLabel("Правая панель").getByTestId("dtm-settings")).toBeVisible();

    // ---- 7.28: the windows of export write the files ------------------------------------------------------
    await page.getByRole("button", { name: "В DXF", exact: true }).click();
    const lines = page.getByRole("dialog", { name: "Экспорт линий" });
    await expect(lines.getByLabel("Формат файла")).toHaveValue("dxf");
    await expect(lines.getByRole("button", { name: "Экспортировать" })).toBeDisabled();
    await expect(lines).toContainText("Отметьте слои, которые нужно записать");
    await lines.getByLabel("Слой «Рельеф точки»").check();
    await lines.getByLabel("Формат файла").selectOption("str");
    await expect(lines).toContainText("Micromine STR пишет только линии"); // points do not go to a STR
    await expect(lines.getByRole("button", { name: "Экспортировать" })).toBeDisabled();
    await lines.getByLabel("Формат файла").selectOption("dxf");
    await lines.getByLabel("Все слои файла «roads.dxf»").check();
    await expect(lines.getByTestId("export-totals")).toContainText("3 слоя: 3 линии");
    const dxfOut = path.join(dir, "out", "all.dxf");
    fs.mkdirSync(path.dirname(dxfOut));
    await saveNext(dxfOut);
    await lines.getByRole("button", { name: "Сохранить как…" }).click();
    await expect(lines.getByLabel("Файл экспорта")).toHaveValue(dxfOut);
    await page.screenshot({ path: shot("props-export-lines.png") });
    await lines.getByRole("button", { name: "Экспортировать" }).click();
    await expect(lines).toHaveCount(0);
    await expect.poll(() => fs.existsSync(dxfOut), { timeout: 60_000 }).toBe(true);
    const written = fs.readFileSync(dxfOut, "latin1");
    for (const name of ["ROADS", "PIT", "POINT"]) expect(written).toContain(name);

    await tree.getByRole("treeitem", { name: /^map, / }).click({ button: "right" });
    await page.getByRole("menuitem", { name: "Экспорт…" }).click();
    const rasterWin = page.getByRole("dialog", { name: "Экспорт растра" });
    await expect(rasterWin.getByLabel("Растр", { exact: true })).toHaveValue(String(map.id));
    await rasterWin.getByLabel("GeoTIFF").check();
    const tifOut = path.join(dir, "out", "map.tif");
    await saveNext(tifOut);
    await rasterWin.getByRole("button", { name: "Сохранить как…" }).click();
    await rasterWin.getByRole("button", { name: "Экспортировать" }).click();
    await expect(rasterWin).toHaveCount(0);
    await expect.poll(() => fs.existsSync(tifOut), { timeout: 60_000 }).toBe(true);
    expect(fs.readFileSync(tifOut).subarray(0, 2).toString("latin1")).toBe("II"); // a little-endian TIFF

    // ---- 7.28: delete is a command with its own «Удалить?» ------------------------------------------------
    await tree.getByRole("treeitem", { name: /^map, / }).click({ button: "right" });
    await page.getByRole("menuitem", { name: "Удалить…" }).click();
    await page.getByRole("dialog", { name: "Удалить растр" }).getByRole("button", { name: "Удалить" }).click();
    await expect(tree.getByRole("treeitem", { name: /^map, / })).toHaveCount(0);
    expect(((await api("GET", "/rasters")).body.rasters as Raster[]).map((r) => r.name)).toEqual(["ortho"]);
  } finally {
    await close(app);
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
