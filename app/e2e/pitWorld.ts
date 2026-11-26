// The synthetic world of the E2E of the pit optimisation (Э21, 21.20): a block model 30 × 30 × 12 (cells 10 × 10 × 5 m, 10 800 blocks; DAT: AU with a few
// gaps, the density DENS) with an ellipsoidal ore body under a rolling relief, and the relief itself as a DTM built from points («Построить ЦТМ») — all
// made through the core, no real data, no mocks. Shared by `pit.spec.ts` (one spec = one app, one project).
import { expect, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { coreApi, importFiles, waitJob, writeDatBlocks, type BlockColumns } from "./helpers";

export const NX = 30;
export const NY = 30;
export const NZ = 12;
export const CORNER: [number, number, number] = [5000, 7000, 100];
export const SIZE: [number, number, number] = [10, 10, 5];
export const MODEL = "Рудное тело карьера";
export const RELIEF = "Рельеф карьера";
export const CELLS = NX * NY * NZ;
/** The ore body: the centre, the semi-axes (m). */
const BODY = { c: [5150, 7150, 128], a: [60, 50, 15] };

/** The relief (m): rolling, 140…156 over a model 100…160 high — the upper blocks are cut by it or are «воздух». */
export const relief = (x: number, y: number): number => 148 + 5 * Math.sin((x - 5000) / 60) + 3 * Math.cos((y - 7000) / 45);

/** The same numbers every run: AU of an ellipsoid (a few blocks away from the body without a value), a density that follows the height. */
export function blocks(): BlockColumns {
  const names = ["XC", "YC", "ZC", "XINC", "YINC", "ZINC", "AU", "DENS"];
  const cols = names.map(() => new Float64Array(CELLS));
  let at = 0;
  for (let k = 0; k < NZ; k++)
    for (let j = 0; j < NY; j++)
      for (let i = 0; i < NX; i++, at++) {
        const x = CORNER[0] + (i + 0.5) * SIZE[0];
        const y = CORNER[1] + (j + 0.5) * SIZE[1];
        const z = CORNER[2] + (k + 0.5) * SIZE[2];
        const r = ((x - BODY.c[0]) / BODY.a[0]) ** 2 + ((y - BODY.c[1]) / BODY.a[1]) ** 2 + ((z - BODY.c[2]) / BODY.a[2]) ** 2;
        const noise = 0.01 * ((at * 7) % 11);
        const gap = r > 6 && at % 97 === 5; // the blocks far from the body that have no value: never a zero
        const au = gap ? NaN : Math.round((0.05 + 4 * Math.exp(-r) + noise) * 1000) / 1000;
        const dens = Math.round((2.6 + 0.004 * (160 - z) + 0.01 * ((at * 3) % 7)) * 1000) / 1000;
        [x, y, z, SIZE[0], SIZE[1], SIZE[2], au, dens].forEach((v, c) => (cols[c][at] = v));
      }
  return { count: CELLS, columns: names.map((name, c) => ({ name, values: cols[c] })) };
}

/** The points of the relief: a grid 10 m a step over the plan of the model and 50 m round it (X;Y;Z, the decimal comma — as the wizard of the points reads it). */
export function writeRelief(file: string): string {
  const rows = ["X;Y;Z"];
  for (let y = CORNER[1] - 50; y <= CORNER[1] + NY * SIZE[1] + 50; y += 10)
    for (let x = CORNER[0] - 50; x <= CORNER[0] + NX * SIZE[0] + 50; x += 10) rows.push(`${x};${y};${relief(x, y).toFixed(3).replace(".", ",")}`);
  fs.writeFileSync(file, `${rows.join("\n")}\n`, "utf-8");
  return file;
}

export type Wf = {
  id: number;
  name: string;
  folder: string | null;
  version: number;
  triangle_count: number;
  extent: number[];
  dtm: unknown;
  stats: { area: number; volume: number | null; closed: boolean; degenerate: number; nonmanifold_edges: number; open_edges: number };
};
export type Bm = { id: number; name: string; version: number; cells: number; fields: { name: string; role?: string | null; unit?: string | null }[] };

export const wireframes = async (page: Page) => (await coreApi<{ wireframes: Wf[] }>(page, "GET", "/wireframes")).body.wireframes;
export const models = async (page: Page) => (await coreApi<{ block_models: Bm[] }>(page, "GET", "/blocks")).body.block_models;

/** The model (a DAT) and the relief (points → «Построить ЦТМ») through the core → their numbers. */
export async function build(page: Page, dir: string): Promise<{ model: number; terrain: number }> {
  fs.mkdirSync(path.join(dir, "m"), { recursive: true });
  const dat = writeDatBlocks(path.join(dir, "m", `${MODEL}.dat`), blocks());
  const csv = writeRelief(path.join(dir, "m", "relief.csv"));
  const loaded = await importFiles(page, [dat, csv], 120_000);
  expect(loaded.result).toMatchObject({ block_models: 1 });
  const model = (await models(page)).find((m) => m.name === MODEL)!;
  expect(model.cells).toBe(CELLS);
  expect(model.fields.map((f) => f.name)).toEqual(expect.arrayContaining(["AU", "DENS"]));

  const files = (await coreApi<{ files: { name: string; layers: { id: number; point_count: number }[] }[] }>(page, "GET", "/lines/files")).body.files;
  const layer = files.find((f) => f.name === "relief.csv")!.layers[0];
  expect(layer.point_count).toBeGreaterThan(1000);
  const started = await coreApi<{ source: string; id: number }>(page, "POST", "/wireframes/build-dtm", { name: RELIEF, layer_ids: [layer.id] });
  expect(started.status, JSON.stringify(started.body)).toBe(200);
  const job = await waitJob(page, started.body.source, started.body.id, undefined, 120_000);
  expect(job.status, JSON.stringify(job.error)).toBe("done");
  const dtm = (await wireframes(page)).find((w) => w.name === RELIEF)!;
  expect(dtm.dtm).not.toBeNull();
  expect(dtm.triangle_count).toBeGreaterThan(1000);
  return { model: model.id, terrain: dtm.id };
}

/** Ctrl+K → the command by its title → Enter (the palette closes). */
export async function palette(page: Page, query: string, title = query): Promise<void> {
  await page.keyboard.press("Control+KeyK");
  await page.getByLabel("Поиск команды").fill(query);
  await expect(page.getByLabel("Палитра команд").getByRole("option").first()).toContainText(title);
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("Палитра команд")).toHaveCount(0);
}
