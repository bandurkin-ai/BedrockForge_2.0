/** A fake core for the tests of the string editor in the application: one file of lines, layer 7 under edit. */
import { vi } from "vitest";
import { encodeBfa1 } from "../src/edit/bfa1";
import { createApp, type App } from "../src/shell/app";
import { setCoreTarget } from "../src/shell/api";
import { projectStore, type ProjectInfo } from "../src/modules/projects/state";
import { loadSceneObjects } from "../src/modules/scene/objects";

/** BFA1 `linegeom` of a layer: lines given by their vertices; the closed ones repeat the first vertex. */
export function linegeom(layerId: number, version: number, lines: { pts: number[][]; closed?: boolean }[]): ArrayBuffer {
  const xyz: number[] = [];
  const offsets = [0];
  const closed: number[] = [];
  for (const l of lines) {
    const pts = l.closed ? [...l.pts, l.pts[0]] : l.pts;
    for (const p of pts) xyz.push(...p);
    offsets.push(offsets[offsets.length - 1] + pts.length);
    closed.push(l.closed ? 1 : 0);
  }
  return encodeBfa1({ kind: "linegeom", layer_id: layerId, version, lines: lines.length, points: xyz.length / 3, ties: [] }, [
    ["xyz", "<f8", Float64Array.from(xyz)],
    ["offsets", "<u4", Uint32Array.from(offsets)],
    ["closed", "<u1", Uint8Array.from(closed)],
    ["lid", "<u4", Uint32Array.from(lines.map((_, i) => i))],
  ]);
}

export const LINES = [
  { pts: [[0, 0, 10], [10, 0, 10], [10, 10, 10], [0, 10, 10]], closed: true },
  { pts: [[100, 0, 5], [110, 0, 5]] },
  { pts: [[200, 0, 1], [210, 5, 1], [220, 0, 1]] },
];

export const layer = (id: number, name: string, extra: Record<string, unknown> = {}) => ({
  id, file_id: 1, name, version: 1, color: "#ff8800", points_only: false, line_count: 3, point_count: 9, length: 1, closed_count: 1, closed: "some", flat: false, extent: [0, 0, 1, 220, 10, 10], attr_schema: [], ...extra,
});

export function server() {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const state = { version: 3, conflict: null as null | Record<string, unknown> };
  const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
  const journal = () => ({
    layer_id: 7,
    version: state.version,
    versions: Array.from({ length: state.version }, (_, i) => state.version - i).map((v) => ({
      version: v, action: v === 1 ? "import" : "save", note: "", author: "Петров", at: "2026-11-19T09:00:00", line_count: 3, point_count: 9, prev_line_count: v === 1 ? 0 : 3, prev_point_count: v === 1 ? 0 : 9, current: v === state.version,
    })),
  });
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url).pathname.replace("/api/v1", "");
    const method = init?.method ?? "GET";
    calls.push({ method, path, body: init?.body });
    if (path === "/lines/files") return json({ files: [{ id: 1, name: "Файл", folder: null, version: 1, format: "str", label: null, filename: null, crs_name: null, warnings: [], layers: [layer(7, "Контуры"), layer(8, "Облако", { cloud: { classes: [] } }), layer(9, "Дороги")] }] });
    if (path === "/rasters" || path === "/wireframes") return json(path === "/rasters" ? { rasters: [] } : { wireframes: [] });
    if (path === "/lines/layers/7/geometry" && method === "GET") return new Response(linegeom(7, state.version, LINES));
    if (path === "/lines/layers/7/geometry" && method === "PUT") {
      if (state.conflict) return json(state.conflict, 409);
      state.version++;
      return json({ layer: layer(7, "Контуры"), version: state.version });
    }
    if (path === "/lines/layers/7/versions") return json(journal());
    const restore = /^\/lines\/layers\/7\/versions\/(\d+)\/restore$/.exec(path);
    if (restore && method === "POST") {
      state.version++;
      return json({ layer: layer(7, "Контуры"), version: state.version });
    }
    return json({ code: "not_found", message: "Нет", hint: "" }, 404);
  });
  return { calls, state, fetch };
}

export async function openApp(): Promise<{ app: App; core: ReturnType<typeof server> }> {
  const app = createApp({ persist: false });
  const core = server();
  vi.stubGlobal("fetch", core.fetch);
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  projectStore.set({ current: { uid: "p1" } as ProjectInfo });
  app.store.set({ projectName: "Проект" });
  await loadSceneObjects();
  return { app, core };
}

export function closeApp(): void {
  vi.unstubAllGlobals();
  setCoreTarget(null);
  projectStore.set({ current: null });
}
