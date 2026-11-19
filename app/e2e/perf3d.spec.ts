// The frame of the 3D window under software rendering (ADR 0007 §9, «Замеры»): 2000 synthetic holes and one
// display along them, 60 frames turning a full circle → bench/out/frame_sw.json. Budgets only on the
// deterministic counters (draw calls, triangles, video memory: bench/budgets.json → external.frame_sw); the ms
// per frame of SwiftShader go to the report (`python -m bench.run --check` reads the file when it is there).
// Э7 (ADR 0011 «Замеры», ТЗ 5.3), before the benchmark: the first frame of the whole demo — holes, lines,
// survey points, the plan on the DTM — after a lost context (≤ 3 s) and a click on a line and on the surface
// (≤ 50 ms on the CPU); then the lines, the plan and the DTM of the demo are removed.
// Э8, подшаг 8.27 (долг Э7; ADR 0011 «Замеры», ADR 0013 «Замеры»): the heavy scene — the whole demo, one layer of
// 1 million line segments, a raster of 4096 px and a DTM of about 200 thousand triangles, the same 60 frames →
// bench/out/frame_sw_heavy.json; budgets on the counters only (budgets.json → external.frame_sw_heavy).
// Э7.1, подшаг 7.1.17 (ADR 0014 «Замеры»): the scene with a point cloud — the holes of the demo, a cloud of
// 10 million points (a LAS written here), «Фильтр земли» and the DTM built from its ground → the same 60 frames
// → bench/out/frame_sw_cloud.json; budgets: draw calls, the points of the frame, video memory
// (budgets.json → external.frame_sw_cloud); import, filter, DTM, a click on a point and the ms — to the report.
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import zlib from "node:zlib";
import { close, holesOnlyDemo, launch, setWindowSize, waitFrame } from "./helpers";

const ROOT = path.resolve(__dirname, "..", "..");
const HOLES = 2000;
const SAMPLES = 50; // 2 m each: 100 000 rows

function files(dir: string): string[] {
  const collar = ["HOLE,X,Y,Z,DEPTH"];
  const survey = ["HOLE,DEPTH,AZIMUTH,DIP"];
  const assay = ["HOLE,FROM,TO,AU"];
  for (let i = 0; i < HOLES; i++) {
    const h = `S${String(i).padStart(4, "0")}`;
    collar.push(`${h},${600_000 + (i % 50) * 25},${7_000_000 + Math.floor(i / 50) * 25},${300 + (i % 7)},100`);
    survey.push(`${h},0,90,-60`, `${h},100,95,-55`);
    for (let s = 0; s < SAMPLES; s++) assay.push(`${h},${s * 2},${s * 2 + 2},${(((i * 31 + s * 17) % 97) / 20).toFixed(3)}`);
  }
  const out = [["collar.csv", collar], ["survey.csv", survey], ["assay.csv", assay]].map(([name, lines]) => {
    const p = path.join(dir, name as string);
    fs.writeFileSync(p, (lines as string[]).join("\n") + "\n");
    return p;
  });
  return out;
}

/** The import of the wizard through the API of the core: the detected mapping of each file, one job. */
async function importSet(page: Page, paths: string[]): Promise<void> {
  const job = await page.evaluate(async (list) => {
    const c = window.bf!.core()!;
    const call = async (method: string, url: string, body?: unknown) => {
      const r = await fetch(`${c.baseUrl}/api/v1${url}`, { method, headers: { "X-BF-Token": c.token, "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
      if (!r.ok) throw new Error(`${url}: ${r.status} ${await r.text()}`);
      return r.json();
    };
    const specs = [];
    for (const p of list) {
      const chk = await call("POST", "/drillholes/import/check", { path: p });
      specs.push({ path: p, kind: chk.kind, mapping: chk.mapping, components: chk.components });
    }
    return (await call("POST", "/drillholes/import", { set_name: "Синтетика 2000", files: specs })) as { source: string; id: number };
  }, paths);
  await expect
    .poll(
      () =>
        page.evaluate(async ([source, id]) => {
          const c = window.bf!.core()!;
          return (await (await fetch(`${c.baseUrl}/api/v1/jobs/${source}/${id}`, { headers: { "X-BF-Token": c.token } })).json()).status as string;
        }, [job.source, job.id] as const),
      { timeout: 120_000, intervals: [500] },
    )
    .toBe("done");
}

test("perf3d: 2000 holes and a display, 60 frames under software rendering → bench/out/frame_sw.json", async () => {
  test.setTimeout(420_000);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-perf3d-"));
  const paths = files(dir);
  const { app, page } = await launch({ BF_E2E: "1" });
  try {
    await setWindowSize(app, 1280, 800);
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    await waitFrame(page);
    const demo = (await page.evaluate(() => window.__bfScene!.state())).sets[0];

    // Э7 (ADR 0011 «Замеры», ТЗ 5.3): the demo as it is — holes, lines, survey points, the plan on the DTM —
    // comes again from the core after a lost context: the time to its first frame; then a click on the road
    // and a click beside it (the surface with the plan, or a survey point) are answered on the CPU.
    const whole = await page.evaluate(() => window.__bfScene!.state());
    expect(whole.lines.layers).toBe(3);
    expect(whole.wireframes.draped).toHaveLength(1);
    await page.evaluate(() => {
      const canvas = document.querySelector<HTMLCanvasElement>('[data-testid="scene-host"] canvas')!;
      window.__bfLose = canvas.getContext("webgl2")!.getExtension("WEBGL_lose_context");
      window.__bfLose!.loseContext();
    });
    await expect.poll(() => page.evaluate(() => window.__bfScene!.state().lost)).toBe(true);
    const restored = Date.now();
    await page.evaluate(() => window.__bfLose!.restoreContext());
    await waitFrame(page);
    const firstFrameMs = Date.now() - restored;
    const again = await page.evaluate(() => window.__bfScene!.state());
    expect(again.lines.layers).toBe(3);
    expect(again.wireframes.draped).toEqual(whole.wireframes.draped);
    expect(firstFrameMs).toBeLessThanOrEqual(3000);
    await page.keyboard.press("t"); // from above the height of a point does not move it on the screen
    await waitFrame(page);
    const pick = async (x: number, y: number) => {
      const at = (await page.evaluate(([px, py]) => window.__bfScene!.screen(px, py, 420), [x, y] as const))!;
      await page.mouse.click(at.x, at.y);
      await waitFrame(page);
      return page.evaluate(() => window.__bfScene!.state());
    };
    const onRoad = await pick(5200, 7947.81); // a vertex of the road (core demo/geo.py)
    expect(onRoad.picked).toMatchObject({ kind: "lines", on: "line" });
    expect(onRoad.pickMs).toBeLessThan(50);
    const beside = await pick(5437.5, 8312.5);
    expect(beside.picked).not.toBeNull();
    expect(beside.pickMs).toBeLessThan(50);
    await page.keyboard.press("Escape");
    // the frame below is measured as before: the synthetic set alone fills the view
    await holesOnlyDemo(page);

    await importSet(page, paths);
    await expect.poll(() => page.evaluate(() => window.__bfScene!.state().sets.length), { timeout: 120_000 }).toBe(2);
    const set = (await page.evaluate(() => window.__bfScene!.state())).sets.find((s) => s !== demo)!;
    const run = (id: string, args: Record<string, unknown>) => page.evaluate(([i, a]) => window.__bfScene!.run(i as string, a as Record<string, unknown>), [id, args] as const);
    expect((await run("layer.show", { layer: "set", id: demo, visible: false })).ok).toBe(true);

    // one saved display: AU as bars
    expect((await run("holes.show_data", { field: "AU", set_id: set })).ok).toBe(true);
    const form = page.getByRole("form", { name: /Отображение: Содержания/ });
    await form.getByRole("button", { name: "Сохранить", exact: true }).click();
    const win = page.getByRole("dialog", { name: /AU · столбцы/ });
    await win.getByRole("button", { name: "Закрыть" }).click();
    await expect(win).toHaveCount(0);
    await page.keyboard.press("f");
    await waitFrame(page, 180_000);
    const state = await page.evaluate(() => window.__bfScene!.state());
    expect(state.displays.legend[0]?.intervals).toBe(HOLES * SAMPLES);

    const r = await page.evaluate(() => window.__bfScene!.bench(60));
    const out = {
      holes: HOLES,
      intervals: HOLES * SAMPLES,
      frames: r.frames,
      ms_per_frame: Math.round(r.ms_per_frame * 10) / 10,
      ms_median: Math.round(r.ms_median * 10) / 10,
      calls: r.calls,
      triangles: r.triangles,
      gpu_bytes: r.gpu_bytes,
      gpu_mb: Math.round((r.gpu_bytes / 1024 / 1024) * 10) / 10,
      simplified: state.displays.simplified,
      first_frame_ms: firstFrameMs,
      pick_line_ms: Math.round(onRoad.pickMs * 10) / 10,
      pick_surface_ms: Math.round(beside.pickMs * 10) / 10,
      renderer: r.gpu.renderer,
      platform: process.platform,
    };
    fs.mkdirSync(path.join(ROOT, "bench", "out"), { recursive: true });
    fs.writeFileSync(path.join(ROOT, "bench", "out", "frame_sw.json"), JSON.stringify(out, null, 2));
    console.log(`frame_sw: ${JSON.stringify(out)}`);
    const limits = JSON.parse(fs.readFileSync(path.join(ROOT, "bench", "budgets.json"), "utf8")).external.frame_sw.limits as Record<string, number>;
    for (const [key, limit] of Object.entries(limits)) expect(out[key as keyof typeof out] as number, key).toBeLessThanOrEqual(limit);
  } finally {
    await close(app);
  }
});

// ---------------------------------------------------------------- the heavy scene (8.27)

// A square of 1.1 km around the demo (its holes: x 4994…5356, y 7994…8256, collars at 420 m).
const HEAVY = { x0: 4650, y0: 7575, side: 1100 };
// One layer of lines: 20 000 polylines of 50 segments on 100 levels — 1 020 000 vertices, four parts of the layer.
// A saw of 20 m steps and 120 m teeth: the simplification of the coarser levels of detail (Douglas–Peucker, 4 and
// 32 m) drops none of its vertices, so the frame holds every segment whatever the size of the window.
const SAW = { levels: 100, rows: 200, segments: 50, dx: 20, tooth: 120, rowStep: 4.9, z0: 200, dz: 2 };
const SEGMENTS = SAW.levels * SAW.rows * SAW.segments;
// The points of the lower surface: a grid of 317 × 317 at 3.5 m → 2 · 316² = 199 712 triangles.
const GRID = { n: 317, step: 3.5 };
const IMAGE_PX = 4096;

function heavyFiles(dir: string): string[] {
  const lines = path.join(dir, "horizons.dxf");
  const out = fs.openSync(lines, "w");
  fs.writeSync(out, "0\r\nSECTION\r\n2\r\nENTITIES\r\n");
  for (let h = 0; h < SAW.levels; h++) {
    const level: string[] = [];
    for (let r = 0; r < SAW.rows; r++) {
      const y = HEAVY.y0 + r * SAW.rowStep;
      let e = `0\r\nLWPOLYLINE\r\n8\r\nHORIZONS\r\n90\r\n${SAW.segments + 1}\r\n70\r\n0\r\n38\r\n${SAW.z0 + h * SAW.dz}\r\n`;
      for (let j = 0; j <= SAW.segments; j++) e += `10\r\n${HEAVY.x0 + 50 + j * SAW.dx}\r\n20\r\n${y + (j % 2) * SAW.tooth}\r\n`;
      level.push(e);
    }
    fs.writeSync(out, level.join(""));
  }
  fs.writeSync(out, "0\r\nENDSEC\r\n0\r\nEOF\r\n");
  fs.closeSync(out);

  const points = path.join(dir, "bedrock.csv");
  const rows = ["X;Y;Z"];
  for (let j = 0; j < GRID.n; j++) {
    for (let i = 0; i < GRID.n; i++) {
      const x = HEAVY.x0 + i * GRID.step;
      const y = HEAVY.y0 + j * GRID.step;
      rows.push(`${x};${y};${(170 + 14 * Math.sin(x / 90) * Math.cos(y / 120) + 4 * Math.sin(y / 31)).toFixed(3)}`);
    }
  }
  fs.writeFileSync(points, `${rows.join("\n")}\n`);

  // an 8-bit RGB PNG of 4096 × 4096 written by hand (no image library in the tests): red grows to the right,
  // green downwards
  const image = path.join(dir, "ortho-heavy.png");
  const row = IMAGE_PX * 3 + 1;
  const raw = Buffer.alloc(row * IMAGE_PX);
  for (let y = 0; y < IMAGE_PX; y++) {
    const at = y * row + 1;
    const green = (y * 255) / IMAGE_PX;
    for (let x = 0; x < IMAGE_PX; x++) {
      raw[at + x * 3] = (x * 255) / IMAGE_PX;
      raw[at + x * 3 + 1] = green;
      raw[at + x * 3 + 2] = 160;
    }
  }
  const chunk = (type: string, data: Buffer) => {
    const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const out4 = Buffer.alloc(8 + body.length);
    out4.writeUInt32BE(data.length, 0);
    body.copy(out4, 4);
    out4.writeUInt32BE(zlib.crc32(body), 4 + body.length);
    return out4;
  };
  const head = Buffer.alloc(13);
  head.writeUInt32BE(IMAGE_PX, 0);
  head.writeUInt32BE(IMAGE_PX, 4);
  head.set([8, 2, 0, 0, 0], 8);
  fs.writeFileSync(image, Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk("IHDR", head), chunk("IDAT", zlib.deflateSync(raw, { level: 1 })), chunk("IEND", Buffer.alloc(0))]));
  const pixel = HEAVY.side / IMAGE_PX;
  const world = path.join(dir, "ortho-heavy.pgw"); // the centre of the top-left pixel
  fs.writeFileSync(world, [pixel, 0, 0, -pixel, HEAVY.x0 + pixel / 2, HEAVY.y0 + HEAVY.side - pixel / 2, ""].join("\n"));
  return [lines, points, image, world];
}

test("perf3d heavy: the demo, 1 million line segments, a raster and a DTM of 200 thousand triangles → bench/out/frame_sw_heavy.json", async () => {
  test.setTimeout(900_000);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-perf3d-heavy-"));
  const paths = heavyFiles(dir);
  const { app, page } = await launch({ BF_E2E: "1" });
  try {
    await setWindowSize(app, 1280, 800);
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    await waitFrame(page);
    const demo = await page.evaluate(() => window.__bfScene!.state());
    expect(demo.lines.layers).toBe(3);
    expect(demo.wireframes.layers).toBe(4); // the relief, the two ore bodies and the fault (Э8)

    const api = (method: string, url: string, body?: unknown) =>
      page.evaluate(
        async ([m, u, b]) => {
          const c = window.bf!.core()!;
          const r = await fetch(`${c.baseUrl}/api/v1${u}`, { method: m as string, headers: { "X-BF-Token": c.token, "Content-Type": "application/json" }, body: b === null ? undefined : JSON.stringify(b) });
          return { status: r.status, body: (await r.json()) as Record<string, unknown> };
        },
        [method, url, body ?? null] as const,
      );
    type Layer = { id: number; line_count: number; point_count: number };
    type Wf = { id: number; name: string; triangle_count: number };
    const layers = async () => ((await api("GET", "/lines/files")).body.files as { layers: Layer[] }[]).flatMap((f) => f.layers);
    // the scene measured is the one of the budget (ADR 0013 «Замеры»): the demo with its relief, without the
    // ore bodies and the fault of Э8 — a few small meshes would only add draw calls to the 38 of 40
    for (const w of ((await api("GET", "/wireframes")).body.wireframes as { id: number; dtm: unknown }[]).filter((x) => x.dtm === null)) {
      expect((await api("DELETE", `/wireframes/${w.id}`)).status).toBe(200);
    }
    await expect.poll(() => page.evaluate(() => window.__bfScene!.state().wireframes.layers), { timeout: 60_000 }).toBe(1);

    // the files as the wizard «Импорт файлов» imports them: one job
    const loadStart = Date.now();
    const started = await api("POST", "/import/objects", { files: paths });
    expect(started.status, JSON.stringify(started.body)).toBe(200);
    let sawId = 0;
    let pointsId = 0;
    await expect
      .poll(
        async () => {
          const all = await layers();
          sawId = all.find((l) => l.line_count === SAW.levels * SAW.rows)?.id ?? 0;
          pointsId = all.find((l) => l.point_count === GRID.n * GRID.n)?.id ?? 0;
          const rasters = (await api("GET", "/rasters")).body.rasters as unknown[];
          return sawId > 0 && pointsId > 0 && rasters.length === 2;
        },
        { timeout: 300_000, intervals: [1000] },
      )
      .toBe(true);
    const importS = (Date.now() - loadStart) / 1000;

    // «Построить ЦТМ» from the points: the second surface of the project
    const built = await api("POST", "/wireframes/build-dtm", { name: "Подошва", layer_ids: [pointsId] });
    expect(built.status, JSON.stringify(built.body)).toBe(200);
    let wf: Wf | undefined;
    await expect
      .poll(async () => (wf = ((await api("GET", "/wireframes")).body.wireframes as Wf[]).find((w) => w.name === "Подошва")) !== undefined, { timeout: 300_000, intervals: [1000] })
      .toBe(true);
    expect(wf!.triangle_count).toBe(2 * (GRID.n - 1) ** 2);
    const dtmS = (Date.now() - loadStart) / 1000 - importS;
    await expect.poll(() => page.evaluate(() => window.__bfScene!.state().wireframes.layers), { timeout: 120_000 }).toBe(2);

    // the points the surface was built from are not a part of the scene measured
    const run = (id: string, args: Record<string, unknown>) => page.evaluate(([i, a]) => window.__bfScene!.run(i as string, a as Record<string, unknown>), [id, args] as const);
    expect((await run("layer.show", { layer: "lines", id: pointsId, visible: false })).ok).toBe(true);
    await page.keyboard.press("f");
    await waitFrame(page, 600_000);
    const state = await page.evaluate(() => window.__bfScene!.state());
    // the scene is what it is called: every segment of the layer and the whole surface are in the frame
    expect(state.layers).toEqual(expect.arrayContaining([`ln:${sawId}`, `wf:${wf!.id}`]));
    expect(state.lines.segments).toBeGreaterThanOrEqual(SEGMENTS);
    // more than a million segments in the frame are lines one pixel wide, not six triangles each (ADR 0007 §7)
    expect(state.stats.lines).toBeGreaterThanOrEqual(SEGMENTS);
    expect(state.rasters.count).toBe(2);
    expect(state.wireframes.layers).toBe(2);
    expect(state.sets.length).toBe(1);

    const r = await page.evaluate(() => window.__bfScene!.bench(60));
    const after = await page.evaluate(() => window.__bfScene!.state());
    const out = {
      segments: after.lines.segments,
      line_parts: after.lines.parts,
      lines_simplified: after.lines.simplified,
      dtm_triangles: wf!.triangle_count,
      wireframe_triangles: after.wireframes.triangles,
      wireframe_parts: after.wireframes.parts,
      wireframes_simplified: after.wireframes.simplified,
      rasters: after.rasters.count,
      raster_px: IMAGE_PX,
      raster_levels: after.rasters.levels,
      gl_lines: after.stats.lines,
      over: after.lines.over || after.rasters.over || after.wireframes.over,
      frames: r.frames,
      ms_per_frame: Math.round(r.ms_per_frame * 10) / 10,
      ms_median: Math.round(r.ms_median * 10) / 10,
      calls: r.calls,
      triangles: r.triangles,
      gpu_bytes: r.gpu_bytes,
      gpu_mb: Math.round((r.gpu_bytes / 1024 / 1024) * 10) / 10,
      import_s: Math.round(importS * 10) / 10,
      dtm_s: Math.round(dtmS * 10) / 10,
      renderer: r.gpu.renderer,
      platform: process.platform,
    };
    fs.mkdirSync(path.join(ROOT, "bench", "out"), { recursive: true });
    fs.writeFileSync(path.join(ROOT, "bench", "out", "frame_sw_heavy.json"), JSON.stringify(out, null, 2));
    console.log(`frame_sw_heavy: ${JSON.stringify(out)}`);
    expect(out.over).toBe(false);
    const limits = JSON.parse(fs.readFileSync(path.join(ROOT, "bench", "budgets.json"), "utf8")).external.frame_sw_heavy.limits as Record<string, number>;
    for (const [key, limit] of Object.entries(limits)) expect(out[key as keyof typeof out] as number, key).toBeLessThanOrEqual(limit);
  } finally {
    await close(app);
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

// ---------------------------------------------------------------- the scene with a point cloud (7.1.17)

// A square of 1.1 km around the demo holes: a grid of 3163 × 3163 points at 0.35 m — 10 004 569 points, 9 per m².
const CLOUD = { x0: 4650, y0: 7575, side: 1100, n: 3163, scale: 0.001 };
const CLOUD_POINTS = CLOUD.n * CLOUD.n;

/** The relief of the cloud scene, about the level of the demo collars (420 m). */
const reliefZ = (x: number, y: number) => 405 + 12 * Math.sin(x / 90) * Math.cos(y / 120) + 4 * Math.sin(y / 31);

/**
 * A LAS 1.2 of point format 0 (ASPRS LAS 1.4 R15, tables 3 and 7), 200 MB, written row by row: the relief with
 * 2 cm of noise, every point moved up to 8 cm in plan; one point in nine is «vegetation» 1.5–5.5 m above the
 * ground. No classes: «Фильтр земли» gives them.
 */
function cloudFile(dir: string): string {
  const file = path.join(dir, "survey-10m.las");
  const step = CLOUD.side / (CLOUD.n - 1);
  const head = Buffer.alloc(227);
  head.write("LASF", 0, "ascii");
  head.writeUInt8(1, 24);
  head.writeUInt8(2, 25);
  head.write("BF2 perf3d", 58, "ascii");
  head.writeUInt16LE(1, 90);
  head.writeUInt16LE(2026, 92);
  head.writeUInt16LE(227, 94);
  head.writeUInt32LE(227, 96);
  head.writeUInt32LE(0, 100);
  head.writeUInt8(0, 104);
  head.writeUInt16LE(20, 105);
  head.writeUInt32LE(CLOUD_POINTS, 107);
  head.writeUInt32LE(CLOUD_POINTS, 111);
  const out = fs.openSync(file, "w");
  fs.writeSync(out, head);
  const box = [Infinity, -Infinity, Infinity, -Infinity, Infinity, -Infinity];
  const row = Buffer.alloc(CLOUD.n * 20);
  let seed = 20261119;
  const rnd = () => (seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0) / 4294967296; // the same file every run
  for (let j = 0; j < CLOUD.n; j++) {
    for (let i = 0; i < CLOUD.n; i++) {
      const x = i * step + (rnd() - 0.5) * 0.16;
      const y = j * step + (rnd() - 0.5) * 0.16;
      const up = rnd();
      const z = reliefZ(CLOUD.x0 + x, CLOUD.y0 + y) + (rnd() - 0.5) * 0.04 + (up < 1 / 9 ? 1.5 + up * 36 : 0);
      const at = i * 20;
      const ix = Math.round(x / CLOUD.scale);
      const iy = Math.round(y / CLOUD.scale);
      const iz = Math.round(z / CLOUD.scale);
      row.writeInt32LE(ix, at);
      row.writeInt32LE(iy, at + 4);
      row.writeInt32LE(iz, at + 8);
      row.writeUInt8(1 | (1 << 3), at + 14); // return 1 of 1
      if (ix < box[0]) box[0] = ix;
      if (ix > box[1]) box[1] = ix;
      if (iy < box[2]) box[2] = iy;
      if (iy > box[3]) box[3] = iy;
      if (iz < box[4]) box[4] = iz;
      if (iz > box[5]) box[5] = iz;
    }
    fs.writeSync(out, row);
  }
  const offset = [CLOUD.x0, CLOUD.y0, 0];
  const where = Buffer.alloc(96);
  [CLOUD.scale, CLOUD.scale, CLOUD.scale, ...offset].forEach((v, i) => where.writeDoubleLE(v, i * 8));
  // max x, min x, max y, min y, max z, min z
  [1, 0, 3, 2, 5, 4].forEach((b, i) => where.writeDoubleLE(box[b] * CLOUD.scale + offset[i >> 1], 48 + i * 8));
  fs.writeSync(out, where, 0, 96, 131);
  fs.closeSync(out);
  return file;
}

test("perf3d cloud: the demo holes, a cloud of 10 million points, the ground filter and a DTM from it → bench/out/frame_sw_cloud.json", async () => {
  test.setTimeout(1_500_000);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-perf3d-cloud-"));
  const writeStart = Date.now();
  const file = cloudFile(dir);
  const writeS = (Date.now() - writeStart) / 1000;
  const { app, page } = await launch({ BF_E2E: "1" });
  try {
    await setWindowSize(app, 1280, 800);
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    await waitFrame(page);
    await holesOnlyDemo(page); // the scene of the budget: the holes, the cloud and its DTM

    const api = (method: string, url: string, body?: unknown) =>
      page.evaluate(
        async ([m, u, b]) => {
          const c = window.bf!.core()!;
          const r = await fetch(`${c.baseUrl}/api/v1${u}`, { method: m as string, headers: { "X-BF-Token": c.token, "Content-Type": "application/json" }, body: b === null ? undefined : JSON.stringify(b) });
          return { status: r.status, body: (await r.json()) as Record<string, unknown> };
        },
        [method, url, body ?? null] as const,
      );
    type Job = { status: string; result: Record<string, unknown> | null; error?: unknown };
    /** Starts a job of the core and waits for it; seconds from the request to «done». */
    const job = async (url: string, body: unknown, timeout: number): Promise<{ seconds: number; result: Record<string, unknown> }> => {
      const t0 = Date.now();
      const started = await api("POST", url, body);
      expect(started.status, JSON.stringify(started.body)).toBe(200);
      let last: Job = { status: "", result: null };
      await expect
        .poll(async () => (last = (await api("GET", `/jobs/${started.body.source}/${started.body.id}`)).body as unknown as Job).status, { timeout, intervals: [500] })
        .toMatch(/done|failed|cancelled/);
      expect(last.status, JSON.stringify(last)).toBe("done");
      return { seconds: (Date.now() - t0) / 1000, result: last.result ?? {} };
    };
    type Layer = { id: number; point_count: number; cloud: { modes: string[]; classes: Record<string, number> } | null };
    const cloudLayer = async () => ((await api("GET", "/lines/files")).body.files as { layers: Layer[] }[]).flatMap((f) => f.layers).find((l) => l.cloud !== null);

    // the file as «Импорт файлов» takes it: a `.las` gives a cloud
    const imported = await job("/import/objects", { files: [file] }, 600_000);
    const layer = (await cloudLayer())!;
    expect(layer.point_count).toBe(CLOUD_POINTS);
    // «Фильтр земли», the profile «Карьер»; then «Построить ЦТМ из облака» — the cloud gets its `dz` with it
    const filtered = await job(`/lines/layers/${layer.id}/classify`, { profile: "quarry" }, 900_000);
    expect(filtered.result.ground as number).toBeGreaterThan(0.8 * CLOUD_POINTS);
    const built = await job("/wireframes/from-points", { cloud_id: layer.id, name: "Земля" }, 900_000);
    const wfId = built.result.wireframe_id as number;
    expect(built.result.points as number).toBeLessThanOrEqual(2_000_000);
    expect((await cloudLayer())!.cloud!.modes).toEqual(["class", "h", "dz"]);
    await expect.poll(() => page.evaluate(() => window.__bfScene!.state().wireframes.layers), { timeout: 300_000 }).toBe(1);

    await page.keyboard.press("f");
    await waitFrame(page, 900_000);
    const state = await page.evaluate(() => window.__bfScene!.state());
    expect(state.layers).toEqual(expect.arrayContaining([`ln:${layer.id}`, `wf:${wfId}`]));
    expect(state.sets.length).toBe(1);
    expect(state.lines.cloud.points).toBeGreaterThan(100_000); // the cloud is in the frame, not a handful of points
    expect(state.lines.cloud.over).toBe(false);

    // a click on a point of the cloud is answered on the CPU (ТЗ 5.3: ≤ 50 ms), from above
    await page.keyboard.press("t");
    await waitFrame(page, 300_000);
    const target = { x: CLOUD.x0 + 300, y: CLOUD.y0 + 800 };
    const at = (await page.evaluate((p) => window.__bfScene!.screen(p.x, p.y, 405), target))!;
    await page.mouse.click(at.x, at.y);
    await waitFrame(page, 300_000);
    const clicked = await page.evaluate(() => window.__bfScene!.state());
    expect(clicked.picked).toMatchObject({ kind: "lines", id: layer.id, on: "point" });
    expect(clicked.pickMs).toBeLessThan(50);
    await page.keyboard.press("Escape");
    await page.keyboard.press("f");
    await waitFrame(page, 300_000);

    const before = await page.evaluate(() => window.__bfScene!.state());
    const r = await page.evaluate(() => window.__bfScene!.bench(60));
    const after = await page.evaluate(() => window.__bfScene!.state());
    const out = {
      cloud_points: CLOUD_POINTS,
      cloud_parts: after.lines.parts,
      ground_points: filtered.result.ground as number,
      dtm_vertices: built.result.points as number,
      dtm_triangles: built.result.triangles as number,
      wireframe_triangles: after.wireframes.triangles,
      wireframes_simplified: after.wireframes.simplified,
      point_budget: before.lines.cloud.budget,
      points: before.lines.cloud.points, // of the cloud in the frame, by the levels and the budget of the frame
      points_simplified: before.lines.cloud.simplified,
      gl_points: r.points, // the largest number of points drawn in one of the 60 frames
      over: after.lines.over || after.lines.cloud.over || after.wireframes.over,
      frames: r.frames,
      ms_per_frame: Math.round(r.ms_per_frame * 10) / 10,
      ms_median: Math.round(r.ms_median * 10) / 10,
      calls: r.calls,
      triangles: r.triangles,
      gpu_bytes: r.gpu_bytes,
      gpu_mb: Math.round((r.gpu_bytes / 1024 / 1024) * 10) / 10,
      pick_point_ms: Math.round(clicked.pickMs * 10) / 10,
      las_write_s: Math.round(writeS * 10) / 10,
      import_s: Math.round(imported.seconds * 10) / 10,
      filter_s: Math.round(filtered.seconds * 10) / 10,
      dtm_s: Math.round(built.seconds * 10) / 10,
      renderer: r.gpu.renderer,
      platform: process.platform,
    };
    fs.mkdirSync(path.join(ROOT, "bench", "out"), { recursive: true });
    fs.writeFileSync(path.join(ROOT, "bench", "out", "frame_sw_cloud.json"), JSON.stringify(out, null, 2));
    console.log(`frame_sw_cloud: ${JSON.stringify(out)}`);
    expect(out.over).toBe(false);
    const limits = JSON.parse(fs.readFileSync(path.join(ROOT, "bench", "budgets.json"), "utf8")).external.frame_sw_cloud.limits as Record<string, number>;
    for (const [key, limit] of Object.entries(limits)) expect(out[key as keyof typeof out] as number, key).toBeLessThanOrEqual(limit);
  } finally {
    await close(app);
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
