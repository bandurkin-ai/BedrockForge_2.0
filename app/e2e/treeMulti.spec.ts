// Э27, 27.7 (Д-44 А5): several objects of the data tree. Ctrl+click adds or takes away a row, Shift+click takes the range of
// the section, Esc clears; «Свойства» of layers of lines show the colour («разные» while they differ) and a colour set there
// goes to every chosen layer; «Отменить» takes the whole change back; a layer and a raster together have no properties.
import { expect, test } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { close, holesOnlyDemo, launch } from "./helpers";

const dxf = (entities: (string | number)[][]) => ["0", "SECTION", "2", "ENTITIES", ...entities.flat().map(String), "0", "ENDSEC", "0", "EOF", ""].join("\r\n");
const road = (layer: string, y: number) => [0, "LINE", 8, layer, 10, 5000, 20, y, 30, 300, 11, 5400, 21, y, 31, 300];

test("tree: Ctrl and Shift choose several layers; «Свойства» change them all, «разные» for what differs", async () => {
  test.setTimeout(300_000);
  const { app, page } = await launch({ BF_E2E: "1" });
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-multi-"));
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    await holesOnlyDemo(page);

    const api = (method: string, url: string, body?: unknown) =>
      page.evaluate(
        async ([m, u, b]) => {
          const c = window.bf!.core()!;
          const r = await fetch(`${c.baseUrl}/api/v1${u}`, { method: m as string, headers: { "X-BF-Token": c.token, "Content-Type": "application/json" }, body: b === null ? undefined : JSON.stringify(b) });
          return { status: r.status, body: (await r.json()) as Record<string, unknown> };
        },
        [method, url, body ?? null] as const,
      );
    const file = path.join(dir, "roads.dxf");
    fs.writeFileSync(file, dxf([road("A_ROAD", 7900), road("B_ROAD", 7880), road("C_ROAD", 7860), road("D_ROAD", 7840)]));
    expect((await api("POST", "/import/objects", { files: [file] })).status).toBe(200);
    type Layer = { id: number; name: string; color: string | null };
    const layers = async () => ((await api("GET", "/lines/files")).body.files as { layers: Layer[] }[]).flatMap((f) => f.layers).sort((a, b) => a.name.localeCompare(b.name));
    await expect.poll(async () => (await layers()).length, { timeout: 60_000 }).toBe(4);
    const [a, b, c, d] = await layers();

    const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
    const row = (l: Layer) => tree.getByTestId(`tree-layer-${l.id}`);
    const click = (l: Layer, modifiers: ("Control" | "Shift")[] = []) => row(l).getByText(l.name, { exact: true }).click({ modifiers });
    const chosen = async (...ls: Layer[]) => {
      for (const l of [a, b, c, d]) await expect(row(l)).toHaveAttribute("aria-selected", String(ls.includes(l)));
    };
    const props = page.getByLabel("Правая панель");
    await expect(row(a)).toBeVisible({ timeout: 30_000 });

    // a click chooses one; Ctrl adds and takes away; Shift takes the range from the anchor
    await click(a);
    await chosen(a);
    await click(c, ["Control"]);
    await chosen(a, c);
    await click(a, ["Control"]);
    await chosen(c);
    await click(b);
    await click(d, ["Shift"]);
    await chosen(b, c, d);
    await click(c, ["Control"]);
    await chosen(b, d);

    // «Свойства»: the layers came with colours of their own (they differ: «разные»); a colour set there goes to both
    const first = await layers();
    const multi = props.getByTestId("props-multi");
    await expect(multi).toContainText("2 слоя линий");
    const field = multi.getByTestId("props-multi-color");
    await expect(field).toHaveAttribute("data-mixed", "true");
    await expect(field).toContainText("разные");
    await multi.getByLabel("Цвет выбранных объектов").fill("#ff8800");
    await expect.poll(async () => (await layers()).filter((l) => l.color === "#ff8800").map((l) => l.id).sort()).toEqual([b.id, d.id].sort());
    expect((await layers()).find((l) => l.id === c.id)!.color).toBe(first.find((l) => l.id === c.id)!.color); // not chosen: not touched
    await expect(field).toHaveAttribute("data-mixed", "false");

    // a third layer with another colour: «разные» again; one colour for all three
    await click(c, ["Control"]);
    await chosen(b, c, d);
    await expect(field).toHaveAttribute("data-mixed", "true");
    await multi.getByLabel("Цвет выбранных объектов").fill("#1f6bff");
    await expect.poll(async () => (await layers()).filter((l) => l.color === "#1f6bff").length).toBe(3);

    // one «Отменить» brings all three back
    await multi.getByRole("button", { name: "Отменить" }).click();
    await expect.poll(async () => (await layers()).map((l) => l.color)).toEqual([first[0].color, "#ff8800", first[2].color, "#ff8800"]);

    // a layer and a raster of the project: different kinds, no properties
    const png = path.join(dir, "map.png");
    fs.writeFileSync(png, Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64"));
    fs.writeFileSync(path.join(dir, "map.pgw"), ["2.0", "0.0", "0.0", "-2.0", "5000.0", "7900.0", ""].join("\n"));
    expect((await api("POST", "/import/objects", { files: [png, path.join(dir, "map.pgw")] })).status).toBe(200);
    let raster = 0;
    await expect.poll(async () => (raster = ((await api("GET", "/rasters")).body.rasters as { id: number }[])[0]?.id ?? 0), { timeout: 60_000 }).toBeGreaterThan(0);
    await tree.getByTestId(`tree-raster-${raster}`).getByText("map", { exact: true }).click({ modifiers: ["Control"] });
    await expect(props.getByTestId("props-multi-mixed")).toContainText("Выбраны объекты разных типов: 4");
    await expect(props.getByTestId("props-multi-color")).toHaveCount(0);

    // Esc clears the choice
    await row(b).focus();
    await page.keyboard.press("Escape");
    await chosen();
    await expect(props.getByTestId("props-multi-mixed")).toHaveCount(0);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
    await close(app);
  }
});
