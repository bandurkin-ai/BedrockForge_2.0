// The world of the E2E of the stack, the faults, the systems of veins and the planned holes (Э19б, 19.40; ТЗ 7.35 Д-24, Д-31, Д-34 ч. 2, Д-39; ADR 0026):
// the scene of `tools/gen/implicit.py` (the committed fixtures `tests/fixtures/implicit/`: 100 holes, the layers ALV / SS / SH / GR, the sphere INT, the vein
// VN) with a SECOND vein VN2 that crosses VN along a line (as `core/tests/stack_world.py` does it: the same rock and the same slab, the contacts are found
// by bisection), imported through the core as a set of holes. The three models of the specs are stored through the core as the wizard would store them
// (`POST /implicit/models`), the hand data of a fault — three layers of strings made through the core. No demo, no real data; the answers are the core's own.
import { expect, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { coreApi, newLineLayer, saveLines, waitJob } from "./helpers";
import { BOX, FIXTURES, LITHO, SET } from "./implicitWorld";

export { BOX, SET };

/** A coarse step for the speed of the E2E (the cells 21 × 21 × 11). */
export const STEP = 20;
/** The codes of the table of the scene (the second vein is VN2). */
export const CODES = ["ALV", "SS", "SH", "GR", "INT", "VN", "VN2"] as const;

// ---- the rock of the scene: the same as `tools/gen/implicit.py` + the slab VN2 of `core/tests/stack_world.py` ----

const FOLD = { a: 260, b: 15, lx: 300, ly: 400, t: 40 };
const SPHERE = { c: [200, 200, 200], r: 55 };
const SLAB = { p: [320, 200, 200], n: [0.8, 0, 0.6], half: 1.5, y: [100, 300] };
const SLAB2 = { p: [320, 200, 200], n: [0.8, 0, -0.6], half: 2, y: [60, 340] };

function slab(s: typeof SLAB, x: number, y: number, z: number): boolean {
  const d = (x - s.p[0]) * s.n[0] + (y - s.p[1]) * s.n[1] + (z - s.p[2]) * s.n[2];
  return Math.abs(d) <= s.half && y > s.y[0] && y < s.y[1];
}

/** The index in `CODES` of the rock at a point. */
export function rock(x: number, y: number, z: number): number {
  const z1 = FOLD.a + FOLD.b * Math.sin((2 * Math.PI * x) / FOLD.lx) * Math.cos((2 * Math.PI * y) / FOLD.ly);
  let code = z >= z1 ? 0 : z >= z1 - FOLD.t ? 1 : z >= z1 - 2 * FOLD.t ? 2 : 3;
  if ((x - SPHERE.c[0]) ** 2 + (y - SPHERE.c[1]) ** 2 + (z - SPHERE.c[2]) ** 2 <= SPHERE.r ** 2) code = 4;
  if (slab(SLAB, x, y, z)) code = 5;
  if (slab(SLAB2, x, y, z)) code = 6;
  return code;
}

/** The table of the lithology of the holes of `collars` (HOLE,X,Y,Z,TD,AZIMUTH,DIP): the stretches of every code, the contacts bisected to a centimetre. */
export function lithology(collars: string): string {
  const rows = ["HOLE,FROM,TO,CODE"];
  for (const line of fs.readFileSync(collars, "utf8").trim().split("\n").slice(1)) {
    const [hole, X, Y, Z, TD, AZ, DIP] = line.split(",");
    const az = (Number(AZ) * Math.PI) / 180;
    const dip = (Number(DIP) * Math.PI) / 180;
    const d = [Math.sin(az) * Math.cos(dip), Math.cos(az) * Math.cos(dip), Math.sin(dip)];
    const p0 = [Number(X), Number(Y), Number(Z)];
    const td = Number(TD);
    const at = (depth: number) => rock(p0[0] + depth * d[0], p0[1] + depth * d[1], p0[2] + depth * d[2]);
    let prev = 0;
    let code = at(0);
    let depth = 0;
    const flush = (to: number, c: number) => {
      if (to > prev) {
        const last = rows[rows.length - 1];
        if (last.startsWith(`${hole},`) && last.endsWith(`,${CODES[c]}`)) rows[rows.length - 1] = `${hole},${last.split(",")[1]},${to.toFixed(2)},${CODES[c]}`;
        else rows.push(`${hole},${prev.toFixed(2)},${to.toFixed(2)},${CODES[c]}`);
        prev = to;
      }
    };
    while (depth < td) {
      const next = Math.min(depth + 0.25, td);
      const c = at(next);
      if (c !== code) {
        let lo = depth;
        let hi = next;
        for (let k = 0; k < 30; k++) {
          const mid = 0.5 * (lo + hi);
          if (at(mid) === code) lo = mid;
          else hi = mid;
        }
        flush(Math.round(0.5 * (lo + hi) * 100) / 100, code);
        code = c;
      }
      depth = next;
    }
    flush(td, code);
  }
  return rows.join("\n") + "\n";
}

// ---- the project ----

export type Ids = { set: number; table: string; field: string; firstHole: string };

/** The holes of the fixtures (the collars) and the lithology with VN2 → the set «Скважины неявных» (through the core, as the wizard of the import does). */
export async function buildSet(page: Page, dir: string): Promise<Ids> {
  const collars = path.join(FIXTURES, "collars.csv");
  const litho = path.join(dir, `${LITHO}.csv`);
  fs.writeFileSync(litho, lithology(collars));
  const specs = await page.evaluate(async (list) => {
    const c = window.bf!.core()!;
    const out: unknown[] = [];
    for (const p of list) {
      const r = await fetch(`${c.baseUrl}/api/v1/drillholes/import/check`, { method: "POST", headers: { "X-BF-Token": c.token, "Content-Type": "application/json" }, body: JSON.stringify({ path: p }) });
      const chk = await r.json();
      out.push({ path: p, mapping: chk.mapping, kind: chk.kind, components: chk.components, ...(chk.kind === "interval" ? { attributes: ["CODE"] } : {}) });
    }
    return out;
  }, [collars, litho]);
  const started = await coreApi<{ source: string; id: number }>(page, "POST", "/drillholes/import", { set_name: SET, files: specs });
  expect(started.status, JSON.stringify(started.body)).toBe(200);
  const job = await waitJob(page, started.body.source, started.body.id, undefined, 120_000);
  expect(job.status, JSON.stringify(job.error)).toBe("done");
  const sets = (await coreApi<{ sets: { id: number; name: string }[] }>(page, "GET", "/drillholes/sets")).body.sets;
  const set = sets.find((s) => s.name === SET)!.id;
  const tables = (await coreApi<{ tables: { table: string; kind: string }[] }>(page, "GET", `/drillholes/sets/${set}/tables`)).body.tables;
  const interval = tables.find((t) => t.kind === "interval");
  if (!interval) throw new Error(`no table of intervals in ${JSON.stringify(tables)}`);
  return { set, table: interval.table, field: "CODE", firstHole: fs.readFileSync(collars, "utf8").split("\n")[1].split(",")[0] };
}

export type Group = { id: number; name: string; type: "domain"; codes: string[] };
export const domain = (id: number, name: string, code: string): Group => ({ id, name, type: "domain", codes: [code] });

/** A model of the scene stored through the core (`POST /implicit/models`): the boundary `BOX`, a coarse step, the table of the codes. → the id. */
export async function createModel(page: Page, ids: Ids, name: string, groups: Group[], manual: unknown[] = []): Promise<number> {
  const made = await coreApi<{ id: number; version: number }>(page, "POST", "/implicit/models", {
    name,
    boundary: { box: BOX },
    h: STEP,
    data: { set_id: ids.set, table: ids.table, field: ids.field, d_max: 30, step: 10 },
    groups,
    manual,
  });
  expect(made.status, JSON.stringify(made.body)).toBe(200);
  return made.body.id;
}

/** The hand data of a fault: three layers of strings of the plane x = `x0` over the box (the contact, 15 m on each side: inside — west, outside — east). → the ids of the layers. */
export async function faultLayers(page: Page, x0 = 200): Promise<number[]> {
  const ys: number[] = [];
  for (let y = 0; y <= 420; y += 35) ys.push(y);
  const zs = [120, 155, 190, 225, 260, 295, 330];
  const out: number[] = [];
  for (const [name, dx] of [["Разлом: контакт", 0], ["Разлом: внутри", -15], ["Разлом: снаружи", 15]] as const) {
    const layer = await newLineLayer(page, name);
    await saveLines(
      page,
      layer.id,
      layer.version,
      ys.map((y) => ({ points: zs.map((z) => [x0 + dx, y, z]) })),
      "e2e: разлом",
    );
    out.push(layer.id);
  }
  return out;
}
