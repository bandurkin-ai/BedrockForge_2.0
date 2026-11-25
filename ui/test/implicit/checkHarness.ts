// The stub of the core for the windows of the check, the comparison with the holes and «Модель на скважины» (Э19, 19.25): over the stub of
// `harness.ts` it answers with the REAL answers of the core — `check`, `check_csv`, `support`, `support_one`, `support_f4`, `support_mesh`, `compare_holes`,
// `from_model_*` of `core/tests/data/implicit/contract_answers.json` (written by `core/tests/test_implicit_contract.py`, BF_REGEN_IMPLICIT=1):
// the check of the model of the scene of the tests of the core (the model 6 there, the model 7 here: the stub of the models is `harness.ts`).
// The vector of the support (f4) is made from its first numbers; the surface of the 3D window comes in the answer `f4mesh` (the vertices,
// the distances, the triangles of the whole wireframe): a grid of the same number of vertices for the first surface, two far-apart bodies
// of over 64 000 triangles for the second (a surface the part of the 3D window could not paint).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { vi } from "vitest";
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
  support_mesh: FILE.support_mesh as { status: number; body: { type: string; bytes: number; head: Record<string, unknown> } },
  support_bad: FILE.support_bad as Answer,
  compare: FILE.compare_holes as Answer,
  compare_bad: FILE.compare_bad as Answer,
  from_submit: FILE.from_model_submit as Answer,
  from_result: FILE.from_model_result as Answer,
  from_exists: FILE.from_model_exists as Answer,
  from_bad: FILE.from_model_bad as Answer,
};

/** The surfaces of the answer `support`: the first has the vertices of the vector f4, the second has two bodies and over 64 000 triangles. */
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

/** The two bodies of the second surface: a grid of side × side vertices each, the second one far away (a real place of a project). */
export const BODY_SIDE = 200;
export const SECOND_VERTICES = 2 * BODY_SIDE * BODY_SIDE;
export const SECOND_TRIANGLES = 4 * (BODY_SIDE - 1) * (BODY_SIDE - 1);

/** A grid of nx × ny vertices from the origin: the offsets (float32), the triangles (uint32) starting at the vertex `first`. */
function grid(nx: number, ny: number, first: number, xyz: Float32Array, idx: Uint32Array, at: number, tri: number): void {
  for (let i = 0; i < nx * ny; i++) xyz.set([(i % nx) * 10 - 65, Math.floor(i / nx) * 10 - 100, Math.sin(i) * 3], (at + i) * 3);
  let t = tri * 3;
  for (let y = 0; y < ny - 1; y++)
    for (let x = 0; x < nx - 1; x++) {
      const a = first + y * nx + x;
      idx.set([a, a + 1, a + nx, a + 1, a + nx + 1, a + nx], t);
      t += 6;
    }
}

/**
 * The body of the answer `f4mesh` of the core (`bf_core/model/check_support.py: mesh_body`): `vertices × 3` float32 from the origin, `vertices`
 * float32 distances, `triangles × 3` uint32 — and its header: the real one of the contract with the numbers of this surface.
 */
export function meshAnswer(id: number, threshold?: number): { body: ArrayBuffer; head: Record<string, unknown> } {
  const second = id === SECOND;
  const nx = 14; // the first surface: 658 vertices in rows of 14 (the last row is not whole: its triangles are left out)
  const ny = Math.ceil(SURFACE.vertices / nx);
  const n = second ? SECOND_VERTICES : SURFACE.vertices;
  const xyz = new Float32Array(n * 3);
  const tris: number[] = [];
  let t = 0;
  let idx = new Uint32Array(0);
  if (second) {
    idx = new Uint32Array(SECOND_TRIANGLES * 3);
    grid(BODY_SIDE, BODY_SIDE, 0, xyz, idx, 0, 0);
    grid(BODY_SIDE, BODY_SIDE, BODY_SIDE * BODY_SIDE, xyz, idx, BODY_SIDE * BODY_SIDE, 2 * (BODY_SIDE - 1) * (BODY_SIDE - 1));
    for (let i = BODY_SIDE * BODY_SIDE; i < n; i++) xyz[i * 3 + 1] += 2500; // the second body: 2.5 km from the first, one origin
    t = SECOND_TRIANGLES;
  } else {
    for (let i = 0; i < n; i++) xyz.set([(i % nx) * 10 - 65, Math.floor(i / nx) * 10 - 100, Math.sin(i) * 3], i * 3);
    for (let y = 0; y < ny - 1; y++)
      for (let x = 0; x < nx - 1; x++) {
        const a = y * nx + x;
        if (a + nx + 1 < n) tris.push(a, a + 1, a + nx, a + 1, a + nx + 1, a + nx);
      }
    idx = Uint32Array.from(tris);
    t = idx.length / 3;
  }
  const body = new ArrayBuffer(n * 16 + t * 12);
  new Float32Array(body, 0, n * 3).set(xyz);
  new Float32Array(body, n * 12, n).set(distances(n));
  new Uint32Array(body, n * 16, t * 3).set(idx);
  const head = R.support_mesh.body.head;
  return {
    body,
    head: {
      ...head,
      ...(typeof threshold === "number" ? { threshold } : {}),
      surface: { ...(head.surface as Record<string, unknown>), wireframe_id: id, vertices: n },
      mesh: { origin: second ? [1_000_000, 5_000_000, 100] : [200, 200, 250], vertices: n, triangles: t },
    },
  };
}

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
      const mine = /^(GET|POST) \/(implicit\/models\/7\/(check|check\.csv|support|compare_holes)|drillholes\/from_model|blocks$)/.test(route);
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
        if (body.format === "f4mesh") {
          const m = meshAnswer(id, typeof body.threshold === "number" ? body.threshold : undefined);
          return bytesResponse(m.body, { "X-BF-Support": asciiJson(m.head) });
        }
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
      return json({ code: "not_found", message: "Нет", hint: route }, 404);
    }),
  );
  return core;
}
