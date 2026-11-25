// The synthetic world of the E2E of the geostatistics (Э18, 18.26; Э18б, 18.36): 36 vertical holes with samples of AU and a domain DOM, a block
// model 12 × 12 × 6 (DAT: DOMAIN, the density DENS), a model of variogram of AU — all made through the core, no real data. Shared by
// `estimation.spec.ts` and `simulation.spec.ts` (one spec = one app, one project).
import { expect, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { coreApi, importFiles, waitJob, writeDatBlocks, type BlockColumns, type CoreJob } from "./helpers";

// ---- the synthetic world ----------------------------------------------------------------------------------------------------------
export const SIDE = 6; // holes a side, 20 m apart
export const STEP = 20;
export const X0 = 5010;
export const Y0 = 7010;
export const Z0 = 130; // the collars; the holes are vertical, 30 m
export const PER_HOLE = 15; // samples of 2 m
export const NX = 12;
export const NY = 12;
export const NZ = 6;
export const CORNER: [number, number, number] = [5000, 7000, 100];
export const SIZE: [number, number, number] = [10, 10, 5];
export const MODEL = "Модель оценки";
export const SET = "Скважины оценки";
export const FIELD = "AU";
export const PREFIX = "AU_OK_"; // the default prefix of OK of AU
export const ROW = [NX / 2, NY / 2, 3]; // the block of the preview: the middle of the model, a layer of the domain 1

/** The same numbers every run: AU from the formulas of the coordinates (a few gaps), the domain by the height of the middle of the sample. */
export function world(dir: string) {
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
export function blocks(): BlockColumns {
  const n = NX * NY * NZ;
  const names = ["XC", "YC", "ZC", "XINC", "YINC", "ZINC", "DOMAIN", "DENS"];
  const cols = names.map(() => new Float64Array(n));
  let at = 0;
  for (let k = 0; k < NZ; k++)
    for (let j = 0; j < NY; j++)
      for (let i = 0; i < NX; i++, at++) {
        const zc = CORNER[2] + (k + 0.5) * SIZE[2];
        const xc = CORNER[0] + (i + 0.5) * SIZE[0];
        const yc = CORNER[1] + (j + 0.5) * SIZE[1];
        // the density follows the same trend as AU (so ρ of the co-SGS is clearly positive) with a little of its own noise
        const dens = Math.round((2.6 + 0.25 * Math.sin(xc / 31) * Math.cos(yc / 37) + 0.15 * Math.cos((Z0 - zc) / 7) + 0.01 * ((at * 7) % 11)) * 1000) / 1000;
        [xc, yc, zc, SIZE[0], SIZE[1], SIZE[2], zc > 115 ? 1 : 2, dens].forEach((v, c) => (cols[c][at] = v));
      }
  return { count: n, columns: names.map((name, c) => ({ name, values: cols[c] })) };
}

export type Bm = { id: number; name: string; version: number; cells: number; fields: { name: string; role?: string | null }[] };
export type Row = { id: number; name: string; prefix: string; method: string; model_id: number; last_run: unknown; stale: boolean };
export type Ids = { set: number; table: string; model: number; vario: number; /** simulation: the model in normal scores and the indicator models of the thresholds of SIS (`indicator:t`) */ nscore?: number; indicators?: number[] };
/** The thresholds of the indicators of SIS in the scenarios (the field of the wizard: «0,7; 1; 1,5»). */
export const THRESHOLDS = [0.7, 1, 1.5];

export const blocksList = async (page: Page) => (await coreApi<{ block_models: Bm[] }>(page, "GET", "/blocks")).body.block_models;
export const rowsList = async (page: Page) => (await coreApi<{ estimations: Row[] }>(page, "GET", "/geostat/estimations")).body.estimations;

/** The last job of `kind` once it is over (the jobs list of the core; the waits are polls of the state). */
export async function lastJob(page: Page, kind: string, timeout: number, after = 0): Promise<CoreJob> {
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
export const lastJobId = async (page: Page, kind: string) => Math.max(0, ...(await coreApi<{ jobs: CoreJob[] }>(page, "GET", "/jobs")).body.jobs.filter((j) => j.kind === kind).map((j) => j.id));

/** The set, the block model and the model of variogram through the core: the data of every scenario. */
export async function build(page: Page, dir: string, simulation = false): Promise<Ids> {
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
  if (!simulation) return { set, table: assay.table, model: model.id, vario: made.body.id };

  // the models of the simulation: in normal scores (SGS, co-SGS; the sill ≈ 1) and one per threshold of SIS (the indicator, σ² = p (1 − p) ≤ ¼)
  const stored = async (name: string, transform: string, sill: number): Promise<number> => {
    const e = await coreApi<{ source: string; id: number }>(page, "POST", "/geostat/variography/experimental", {
      set_id: set, table: assay.table, field: FIELD, transform, axes: { ang1: 0, ang2: 0, ang3: 0 }, nlag: 6, min_pairs: 5,
    });
    expect(e.status, JSON.stringify(e.body)).toBe(200);
    const done = await waitJob(page, e.body.source, e.body.id, undefined, 120_000);
    expect(done.status, JSON.stringify(done.error)).toBe("done");
    const m = await coreApi<{ id: number }>(page, "POST", "/geostat/variography/models", {
      name,
      exp_key: done.result!.key,
      model: { type: "semivariogram", c0: 0.1 * sill, common_axes: true, structures: [{ type: "spherical", c: 0.9 * sill, ang1: 0, ang2: 0, ang3: 0, a1: 60, a2: 60, a3: 30 }] },
    });
    expect(m.status, JSON.stringify(m.body)).toBe(200);
    return m.body.id;
  };
  const nscore = await stored("AU · Y · сфера", "nscore", 1);
  const indicators: number[] = [];
  for (const t of THRESHOLDS) indicators.push(await stored(`AU · индикатор ${t}`, `indicator:${t}`, 0.2));
  return { set, table: assay.table, model: model.id, vario: made.body.id, nscore, indicators };
}

/** Ctrl+K → the command by its title → Enter (the palette closes). */
export async function palette(page: Page, query: string, title = query): Promise<void> {
  await page.keyboard.press("Control+KeyK");
  await page.getByLabel("Поиск команды").fill(query);
  await expect(page.getByLabel("Палитра команд").getByRole("option").first()).toContainText(title);
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("Палитра команд")).toHaveCount(0);
}
