// The stub of the core for the windows of the check, the comparison with the holes and «Модель на скважины» (Э19, 19.25): over the stub of
// `harness.ts` it answers with the REAL answers of the core — `check`, `check_csv`, `support`, `support_one`, `support_f4`, `compare_holes`,
// `from_model_*` of `core/tests/data/implicit/contract_answers.json` (written by `core/tests/test_implicit_contract.py`, BF_REGEN_IMPLICIT=1):
// the check of the model of the scene of the tests of the core (the model 6 there, the model 7 here: the stub of the models is `harness.ts`).
// The vector of the support (f4) is made from its first numbers; the surface of the 3D window is a grid of the same number of vertices (a part
// of a wireframe as BFA1), a second wireframe has two parts.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { vi } from "vitest";
import { build } from "../bfa1-build";
import { ID, installCore, json, type Answer, type Core } from "./harness";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const FILE: any = JSON.parse(readFileSync(resolve(__dirname, "../../../core/tests/data/implicit/contract_answers.json"), "utf-8"));

export const R = {
  check: FILE.check as Answer,
  check_csv: FILE.check_csv as { status: number; body: { type: string; bom: boolean; lines: number; head: string[] } },
  check_bad: FILE.check_bad as Answer,
  support: FILE.support as Answer,
  support_one: FILE.support_one as Answer,
  support_f4: FILE.support_f4 as { status: number; body: { type: string; bytes: number; head: Record<string, unknown> } },
  support_bad: FILE.support_bad as Answer,
  compare: FILE.compare_holes as Answer,
  compare_bad: FILE.compare_bad as Answer,
  from_submit: FILE.from_model_submit as Answer,
  from_result: FILE.from_model_result as Answer,
  from_exists: FILE.from_model_exists as Answer,
  from_bad: FILE.from_model_bad as Answer,
};

/** The surfaces of the answer `support`: the first has the vertices of the vector f4 and one part, the second is not drawn (two parts). */
export const SURFACE = { id: (R.support_one.body.surfaces as { wireframe_id: number }[])[0].wireframe_id, vertices: (R.support_f4.body.head.surface as { vertices: number }).vertices };
export const SECOND = (R.support.body.surfaces as { wireframe_id: number }[])[1].wireframe_id;

/** The distances of the vertices of the surface: the first numbers of the real answer, then a ramp; one vertex has no data points (NaN). */
export function distances(n = SURFACE.vertices): Float32Array {
  const known = (R.support_one.body.values as number[]) ?? [];
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) out[i] = i < known.length ? known[i] : 20 + ((i * 7) % 90);
  out[3] = Number.NaN;
  return out;
}

/** A grid of `n` vertices (the part of a wireframe as the core gives it): the vertices from the origin of the part, `n − 1` squares of triangles. */
function gridPart(n: number, k = 0, origin: [number, number, number] = [200, 200, 250]) {
  const nx = 14;
  const ny = Math.ceil(n / nx);
  const xyz = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) xyz.set([(i % nx) * 10 - 65, Math.floor(i / nx) * 10 - 100, Math.sin(i) * 3], i * 3);
  const idx: number[] = [];
  for (let y = 0; y < ny - 1; y++)
    for (let x = 0; x < nx - 1; x++) {
      const a = y * nx + x;
      if (a + nx + 1 >= n) continue;
      idx.push(a, a + 1, a + nx, a + 1, a + nx + 1, a + nx);
    }
  return { xyz, idx: Uint16Array.from(idx), origin, k, triangles: idx.length / 3 };
}

const manifestOf = (id: number, parts: number, vertices: number): ArrayBuffer => {
  const g = gridPart(vertices);
  return build(
    {
      kind: "wireframe",
      wireframe: { id, name: `Поверхность ${id}` },
      version: 1,
      color: null,
      dtm: null,
      points: vertices,
      triangles: g.triangles * parts,
      bbox: [135, 100, 247, 265, 300, 253],
      lods: [{ lod: 0, ratio: 1 }],
      parts: Array.from({ length: parts }, (_x, k) => ({ k, n: g.triangles, bbox: [135, 100, 247, 265, 300, 253], origin: g.origin, etags: ['"a"', '"b"', '"c"'] })),
    },
    [],
  );
};

const partOf = (vertices: number): ArrayBuffer => {
  const g = gridPart(vertices);
  return build({ kind: "mesh", k: 0, lod: 0, origin: g.origin, triangles: g.triangles, vertices, source_triangles: g.triangles, simplified: false, index: "<u2" }, [
    ["xyz", "<f4", g.xyz],
    ["idx", "<u2", g.idx],
  ]);
};

export interface CheckCore extends Core {
  /** the names of the tables the set already has (the write of a table of one of them is a 409) */
  taken: string[];
}

/** JSON as the core writes the header `X-BF-Support`: ASCII only (`ensure_ascii`), the letters are `\uXXXX`. */
export const asciiJson = (v: unknown): string => JSON.stringify(v).replace(/[\u0080-\uffff]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, "0")}`);

export const bytesResponse = (body: BodyInit, headers: Record<string, string> = {}, status = 200) => new Response(body, { status, headers: { "Content-Type": "application/octet-stream", ...headers } });

export function installCheckCore(): CheckCore {
  const core = installCore() as CheckCore;
  core.taken = [];
  core.job = { status: "done", result: R.from_result.body, error: null };
  const prev = globalThis.fetch;
  const forced = new Map<string, Answer>();
  core.force = (route, a) => void forced.set(route, a);
  const csv = `﻿${R.check_csv.body.head.join("\r\n")}\r\n`;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = new URL(url);
      const path = u.pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      const route = `${method} ${path}`;
      const mine = /^(GET|POST) \/(implicit\/models\/7\/(check|check\.csv|support|compare_holes)|drillholes\/from_model|viz\/wireframes\/|blocks$)/.test(route);
      if (!mine) return prev(url, init);
      core.sent.push({ method, path, query: u.search, body });
      const f = forced.get(route);
      if (f) {
        forced.delete(route);
        return json(f.body, f.status);
      }
      if (route === `GET /implicit/models/${ID.model}/check`) return json(R.check.body);
      if (route === `GET /implicit/models/${ID.model}/check.csv`) return bytesResponse(new TextEncoder().encode(csv), { "Content-Type": R.check_csv.body.type });
      if (route === `POST /implicit/models/${ID.model}/support`) {
        const id = body.wireframe_id as number | undefined;
        if (id === undefined) return json(R.support.body);
        if (body.format === "f4") {
          const head = { ...R.support_f4.body.head, ...(typeof body.threshold === "number" ? { threshold: body.threshold } : {}) };
          return bytesResponse(distances(), { "X-BF-Support": asciiJson(head) });
        }
        return json(R.support_one.body);
      }
      if (route === `POST /implicit/models/${ID.model}/compare_holes`) return json(R.compare.body);
      if (route === "POST /drillholes/from_model") {
        if (core.taken.includes(String(body.name))) return json(R.from_exists.body, R.from_exists.status);
        return json(ID.job);
      }
      if (route === "GET /blocks") return json({ block_models: [{ id: 21, name: "Блоки", fields: [{ name: "AU", kind: "number", unit: "г/т" }, { name: "ROCK", kind: "category", unit: null }] }] });
      const wf = /^GET \/viz\/wireframes\/(\d+)(\/parts\/0)?$/.exec(route);
      if (wf) {
        const id = Number(wf[1]);
        const parts = id === SECOND ? 2 : 1;
        return bytesResponse(wf[2] ? partOf(SURFACE.vertices) : manifestOf(id, parts, SURFACE.vertices));
      }
      return json({ code: "not_found", message: "Нет", hint: route }, 404);
    }),
  );
  return core;
}
