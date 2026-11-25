// The world of the E2E of the implicit modelling (Э19, 19.28; ТЗ 8.9, 7.35 Д-15, Д-32…Д-34; ADR 0026): the small scene of `tools/gen/implicit.py`
// (the committed fixtures `tests/fixtures/implicit/`: 100 holes through folded layers, a sphere INT, a vein VN) imported through the core as a set
// of holes — the collars, the table of the codes «Литология» (CODE), the assays (AU) — and a table of structural measurements (ALPHA, DIPDIR) made
// here. No demo, no real data; the answers are the core's own.
import { expect, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { coreApi, waitJob } from "./helpers";

export const FIXTURES = path.resolve(__dirname, "..", "..", "tests", "fixtures", "implicit");
export const SET = "Скважины неявных";
export const LITHO = "Литология";
export const SET_STRUCT = "Замеры структуры";
export const MEASURES = "Замеры";
/** The boundary of the model: the extent of the scene (the fixture holes are 20…~400 m in X, Y; the collars at 300 m, the holes ~120 m deep). */
export const BOX = [0, 0, 120, 420, 420, 330];
/** A coarse step for the speed of the E2E (the cells 21 × 21 × 11 = 4 851). */
export const STEP_H = 20;
/** The codes of the domains of the scene, the youngest first (a domain per code; the rest is «Прочее»). */
export const CODES = ["INT", "SS"];

export type Tab = { table: string; name: string; kind: string; rows: number };
export type Ids = { set: number; litho: Tab; assay: Tab; structSet: number; measures: Tab };

/** The structural measurements: a plane dipping 28° towards 90° (east) with a gentle scatter, at 3 depths of each of 36 holes → a CSV in `dir`. */
export function writeMeasures(dir: string): string {
  const lines = ["HOLE,FROM,TO,ALPHA,DIPDIR"];
  for (let h = 1; h <= 36; h++) {
    for (let k = 0; k < 3; k++) {
      const from = 10 + 20 * k;
      lines.push(`H${String(h).padStart(3, "0")},${from},${from + 1},${28 + ((h * 7 + k) % 5) - 2},${90 + ((h * 3 + k * 5) % 7) - 3}`);
    }
  }
  const p = path.join(dir, `${MEASURES}.csv`);
  fs.writeFileSync(p, lines.join("\n") + "\n");
  return p;
}

/** A job of the import of holes: the files (checked by the core, as the wizard does) into a new set (`set_name`) or one that is (`set_id`). */
async function importHoles(page: Page, paths: string[], target: { set_name: string } | { set_id: number }): Promise<void> {
  const specs = await page.evaluate(async (list) => {
    const c = window.bf!.core()!;
    const out: unknown[] = [];
    for (const p of list) {
      const r = await fetch(`${c.baseUrl}/api/v1/drillholes/import/check`, { method: "POST", headers: { "X-BF-Token": c.token, "Content-Type": "application/json" }, body: JSON.stringify({ path: p }) });
      const chk = await r.json();
      // the column CODE of the lithology is an attribute (text): the field of the codes of the model
      // the lithology: CODE is an attribute (text); the measurements: ALPHA and DIPDIR are numbers of an assay table (the numeric fields of the trend)
      const spec = p.includes("Замеры") ? { kind: "assay", components: [{ column: "ALPHA", unit: "°" }, { column: "DIPDIR", unit: "°" }] } : { kind: chk.kind, components: chk.components, ...(chk.kind === "interval" ? { attributes: ["CODE"] } : {}) };
      out.push({ path: p, mapping: chk.mapping, ...spec });
    }
    return out;
  }, paths);
  const started = await coreApi<{ source: string; id: number }>(page, "POST", "/drillholes/import", { ...target, files: specs });
  expect(started.status, JSON.stringify(started.body)).toBe(200);
  const imported = await waitJob(page, started.body.source, started.body.id, undefined, 120_000);
  expect(imported.status, JSON.stringify(imported.error)).toBe("done");
}

/** The fixtures of the scene → the set of holes «Скважины неявных» (the codes, the assays) and the set «Замеры структуры» (the same collars, the measurements). */
export async function build(page: Page, dir: string): Promise<Ids> {
  // the names of the tables are the names of the files: a copy with the name the interface shows
  const litho = path.join(dir, `${LITHO}.csv`);
  fs.copyFileSync(path.join(FIXTURES, "lithology.csv"), litho);
  const collars = path.join(FIXTURES, "collars.csv");
  await importHoles(page, [collars, litho, path.join(FIXTURES, "assays.csv")], { set_name: SET });
  // a table of a kind per load (the second one of the kind replaces the first): the measurements go to a set of their own
  await importHoles(page, [collars, writeMeasures(dir)], { set_name: SET_STRUCT });
  const sets = (await coreApi<{ sets: { id: number; name: string }[] }>(page, "GET", "/drillholes/sets")).body.sets;
  const tablesOf = async (set: number) => (await coreApi<{ tables: Tab[] }>(page, "GET", `/drillholes/sets/${set}/tables`)).body.tables;
  const set = sets.find((s) => s.name === SET)!.id;
  const structSet = sets.find((s) => s.name === SET_STRUCT)!.id;
  const of = (tables: Tab[], kind: string) => {
    const t = tables.find((x) => x.kind === kind);
    if (!t) throw new Error(`no table of the kind ${kind} in ${JSON.stringify(tables)}`);
    return t;
  };
  const main = await tablesOf(set);
  return { set, litho: of(main, "interval"), assay: of(main, "assay"), structSet, measures: of(await tablesOf(structSet), "assay") };
}
