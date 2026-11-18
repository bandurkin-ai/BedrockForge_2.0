// Э7, подшаг 7.26 (ADR 0011 §4; ТЗ 7.20) under software WebGL2: a DTM built from points («Построить ЦТМ») is a
// surface `wf:<id>` of the 3D window and counts in the floor of what has no height; a raster laid on it repeats
// the relief at every level of detail — the same plan point has the same image coordinates and the same colour
// on the screen at 1/64 and in full; far away the surface is drawn at 1/64, close — in full; «Скрыть данные за
// ЦТМ» hides the flat raster, without it the raster is cut out under the surface; a click picks the surface
// within 50 ms; a hidden surface leaves the frame; «Вернуть в каркасы» takes the raster off; after a lost
// context everything comes again.
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { close, launch, shot, waitFrame } from "./helpers";
import { gradientPng } from "./images";

const state = (page: Page) => page.evaluate(() => window.__bfScene!.state());

// South of the demo holes (x 5000…5400, y 8000…8300): nothing else is drawn over the relief.
const GRID = { x0: 4960, y0: 7500, nx: 81, ny: 71, step: 6 }; // 480 × 420 m, 5751 points, about 11 200 triangles
const IMAGE = { w: 256, h: 192, pixel: 2, left: 4950, top: 7910 }; // x 4950…5462, y 7526…7910
const height = (x: number, y: number) => 300 + 25 * Math.sin(x / 55) * Math.cos(y / 70) + 8 * Math.sin(y / 23);

function makeFiles(dir: string): string[] {
  const files = { points: path.join(dir, "relief.csv"), image: path.join(dir, "ortho.png"), world: path.join(dir, "ortho.pgw") };
  const rows = ["X;Y;Z"];
  for (let j = 0; j < GRID.ny; j++) {
    for (let i = 0; i < GRID.nx; i++) {
      const x = GRID.x0 + i * GRID.step;
      const y = GRID.y0 + j * GRID.step;
      rows.push(`${x};${y};${height(x, y).toFixed(3).replace(".", ",")}`);
    }
  }
  fs.writeFileSync(files.points, `${rows.join("\n")}\n`, "utf-8");
  fs.writeFileSync(files.image, gradientPng(IMAGE.w, IMAGE.h));
  // the world file gives the centre of the top-left pixel
  fs.writeFileSync(files.world, [IMAGE.pixel, 0, 0, -IMAGE.pixel, IMAGE.left + IMAGE.pixel / 2, IMAGE.top - IMAGE.pixel / 2, ""].join("\n"));
  return Object.values(files);
}

/** Image coordinates of a plan point by the world file, float64: the reference. */
const imageUv = (x: number, y: number) => [(x - IMAGE.left) / (IMAGE.w * IMAGE.pixel), (IMAGE.top - y) / (IMAGE.h * IMAGE.pixel)];

// plan points inside the surface and the image, off the nodes of the grid
const POINTS = [[5075.5, 7655.25], [5200.25, 7750.5], [5333.75, 7590.75], [5010.5, 7860.25], [5401.25, 7700.5]];

test("3D: a DTM built from points, a raster on it at two levels of detail, clip, click, hide, lost context", async () => {
  test.setTimeout(420_000);
  const { app, page } = await launch({ BF_E2E: "1" });
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-dtm-"));
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    await waitFrame(page);
    expect((await state(page)).wireframes.layers).toBe(0);

    const api = (method: string, url: string, body?: unknown) =>
      page.evaluate(
        async ([m, u, b]) => {
          const c = window.bf!.core()!;
          const r = await fetch(`${c.baseUrl}/api/v1${u}`, { method: m as string, headers: { "X-BF-Token": c.token, "Content-Type": "application/json" }, body: b === null ? undefined : JSON.stringify(b) });
          return { status: r.status, body: (await r.json()) as Record<string, unknown> };
        },
        [method, url, body ?? null] as const,
      );
    const run = (id: string, args: Record<string, unknown>) => page.evaluate(([i, a]) => window.__bfScene!.run(i as string, a as Record<string, unknown>), [id, args] as const);

    // the points of the relief and the image, as the wizard imports them
    const started = await api("POST", "/import/objects", { files: makeFiles(dir) });
    expect(started.status, JSON.stringify(started.body)).toBe(200);
    let layerId = 0;
    let rasterId = 0;
    await expect
      .poll(
        async () => {
          const files = (await api("GET", "/lines/files")).body.files as { layers: { id: number; point_count: number }[] }[];
          const rasters = (await api("GET", "/rasters")).body.rasters as { id: number }[];
          layerId = files.flatMap((f) => f.layers).find((l) => l.point_count === GRID.nx * GRID.ny)?.id ?? 0;
          rasterId = rasters[0]?.id ?? 0;
          return layerId > 0 && rasterId > 0;
        },
        { timeout: 60_000 },
      )
      .toBe(true);

    // «Построить ЦТМ» from the points: the surface comes into the scene by itself when the job is done
    const built = await api("POST", "/wireframes/build-dtm", { name: "Рельеф", layer_ids: [layerId] });
    expect(built.status, JSON.stringify(built.body)).toBe(200);
    type Wf = { id: number; version: number; triangle_count: number; extent: number[]; dtm: { raster_id: number | null } | null };
    let wf: Wf | undefined;
    await expect
      .poll(async () => (wf = ((await api("GET", "/wireframes")).body.wireframes as Wf[])[0]) !== undefined, { timeout: 180_000 })
      .toBe(true);
    const id = wf!.id;
    expect(wf!.dtm).not.toBeNull();
    expect(wf!.triangle_count).toBeGreaterThan(10_000);
    await expect.poll(async () => (await state(page)).wireframes.layers, { timeout: 60_000 }).toBe(1);
    await run("scene.style", { grid: false, wireframe_opacity: 1 });
    // only the surface and the image: the points it was built from would cover the pixels read below
    await run("layer.show", { layer: "lines", id: layerId, visible: false });
    await page.keyboard.press("t");
    await page.keyboard.press("f");
    await waitFrame(page);
    const plain = await state(page);
    expect(plain.layers).toContain(`wf:${id}`);
    expect(plain.wireframes.parts).toBe(1);
    expect(plain.wireframes.triangles).toBeGreaterThan(0);
    expect(plain.wireframes.draped).toEqual([]);
    expect(plain.rasters.covered).toEqual([]);
    // the surface counts as an object others lie under: with the holes hidden the raster without an elevation
    // is a metre under the lowest point of the relief
    const setId = plain.sets[0];
    await run("layer.show", { layer: "set", id: setId, visible: false });
    await waitFrame(page);
    expect((await state(page)).floor).toBeCloseTo(wf!.extent[2] - 1, 6);
    await run("layer.show", { layer: "set", id: setId, visible: true });
    await waitFrame(page);

    // the raster on the relief, colours as they are; «Скрыть данные за ЦТМ» — the flat raster is not drawn
    const put = async (body: Record<string, unknown>) => {
      const cur = (await api("GET", `/wireframes/${id}`)).body as { version: number };
      const r = await api("PUT", `/wireframes/${id}/dtm`, { expected_version: cur.version, ...body });
      expect(r.status, JSON.stringify(r.body)).toBe(200);
    };
    await put({ raster_id: rasterId, clip: true, tint: "#ffffff", opacity: 1, shade: false });
    await expect.poll(async () => (await state(page)).wireframes.draped, { timeout: 30_000 }).toEqual([id]);
    await waitFrame(page);
    expect((await state(page)).rasters.covered).toEqual([rasterId]);

    // two levels of detail: the same image coordinates and the same colour at the same plan points
    const sample = async (level: number) => {
      await page.evaluate((l) => window.__bfScene!.lod(l), level);
      await expect.poll(async () => (await waitFrame(page), (await state(page)).wireframes.shown[level] ?? 0), { timeout: 60_000 }).toBe(1);
      return page.evaluate(
        ([wid, pts]) =>
          (pts as number[][]).map(([x, y]) => {
            const s = window.__bfScene!.screen(x, y, 300)!; // a top view: the height does not move the point
            return { probe: window.__bfScene!.probe(wid as number, x, y)!, pixel: window.__bfScene!.pixel(s.x, s.y)! };
          }),
        [id, POINTS] as const,
      );
    };
    const coarse = await sample(2);
    await page.screenshot({ path: shot("dtm-drape-lod2.png") });
    const full = await sample(0);
    await page.screenshot({ path: shot("dtm-drape-lod0.png") });
    expect(coarse[0].probe.triangles).toBeLessThan(full[0].probe.triangles / 8);
    expect(full[0].probe.triangles).toBe(wf!.triangle_count);
    let uvError = 0;
    let lodError = 0;
    let heightGap = 0;
    POINTS.forEach(([x, y], i) => {
      const [c, f] = [coarse[i], full[i]];
      expect(c.probe.lod).toBe(2);
      expect(f.probe.lod).toBe(0);
      const ref = imageUv(x, y);
      for (const k of [0, 1]) {
        lodError = Math.max(lodError, Math.abs(c.probe.uv![k] - f.probe.uv![k]));
        uvError = Math.max(uvError, Math.abs(f.probe.uv![k] - ref[k]), Math.abs(c.probe.uv![k] - ref[k]));
      }
      heightGap = Math.max(heightGap, Math.abs(c.probe.z - f.probe.z));
      // the full level passes through the points it was built from
      expect(Math.abs(f.probe.z - height(x, y))).toBeLessThan(1);
      // the colour of the image at that place (red — across, green — down), the same at both levels
      const want = [255 * ref[0], 255 * ref[1], 160];
      for (const k of [0, 1, 2]) {
        expect(Math.abs(f.pixel[k] - want[k]), `full, point ${i}, channel ${k}: ${f.pixel}`).toBeLessThanOrEqual(6);
        expect(Math.abs(c.pixel[k] - f.pixel[k]), `1/64 against full, point ${i}, channel ${k}: ${c.pixel} / ${f.pixel}`).toBeLessThanOrEqual(3);
      }
    });
    expect(lodError).toBeLessThan(1e-6); // of the image: 0.0003 px of 256
    expect(uvError * IMAGE.w).toBeLessThan(0.02); // px of the image
    expect(heightGap).toBeGreaterThan(0.05); // the levels are different surfaces

    // by the size on the screen: far away 1/64, close — every triangle
    await page.evaluate(() => window.__bfScene!.lod(null));
    const box = (await page.getByTestId("scene-host").boundingBox())!;
    const centre = (await page.evaluate(() => window.__bfScene!.screen(5200, 7710, 300)))!;
    await page.mouse.move(centre.x, centre.y);
    const zoomUntil = async (delta: number, done: (s: Awaited<ReturnType<typeof state>>) => boolean) => {
      for (let i = 0; i < 60; i++) {
        await page.mouse.wheel(0, delta);
        await waitFrame(page);
        if (done(await state(page))) return;
      }
      throw new Error(`the level of detail did not change: ${JSON.stringify((await state(page)).wireframes)}`);
    };
    await zoomUntil(400, (s) => s.wireframes.shown[2] === 1);
    expect((await state(page)).wireframes.simplified).toBe(1);
    await zoomUntil(-400, (s) => s.wireframes.shown[0] === 1);
    await page.keyboard.press("f");
    await waitFrame(page);
    expect(box.width).toBeGreaterThan(100);

    // a click on the surface picks it, on the CPU within 50 ms
    const at = (await page.evaluate(() => window.__bfScene!.screen(5200.25, 7750.5, 300)))!;
    await page.mouse.click(at.x, at.y);
    await waitFrame(page);
    const picked = await state(page);
    expect(picked.picked).toMatchObject({ kind: "wireframe", id });
    const xyz = (picked.picked as { xyz: number[] }).xyz;
    expect(Math.abs(xyz[0] - 5200.25)).toBeLessThan(2);
    expect(Math.abs(xyz[2] - height(xyz[0], xyz[1]))).toBeLessThan(1);
    expect(picked.pickMs).toBeLessThan(50);

    // without «Скрыть данные за ЦТМ» the flat raster stays around the surface, cut out under it
    await put({ clip: false });
    await expect.poll(async () => (await state(page)).rasters.cut, { timeout: 30_000 }).toEqual([rasterId]);
    await waitFrame(page);
    expect((await state(page)).rasters.covered).toEqual([]);
    await page.screenshot({ path: shot("dtm-drape-flat-around.png") });

    // a hidden surface leaves the frame and gives the raster back whole
    expect((await run("layer.show", { layer: "wireframe", id, visible: false })).ok).toBe(true);
    await waitFrame(page);
    const hidden = await state(page);
    expect(hidden.wireframes.triangles).toBe(0);
    expect(hidden.rasters.cut).toEqual([]);
    await run("layer.show", { layer: "wireframe", id, visible: true });
    await waitFrame(page);
    expect((await state(page)).wireframes.triangles).toBe(wf!.triangle_count);

    // a lost context: the surface and its image come again from the core
    await page.evaluate(() => {
      const canvas = document.querySelector<HTMLCanvasElement>('[data-testid="scene-host"] canvas')!;
      window.__bfLose = canvas.getContext("webgl2")!.getExtension("WEBGL_lose_context");
      window.__bfLose!.loseContext();
    });
    await expect.poll(() => page.evaluate(() => window.__bfScene!.state().lost)).toBe(true);
    await page.evaluate(() => window.__bfLose!.restoreContext());
    await expect(page.getByTestId("scene-lost")).toBeHidden({ timeout: 30_000 });
    await waitFrame(page);
    const again = await state(page);
    expect(again.wireframes).toMatchObject({ layers: 1, parts: 1, draped: [id] });
    expect(again.rasters.cut).toEqual([rasterId]);

    // «Вернуть в каркасы»: the settings and the raster are gone, the surface stays
    await put({ on: false });
    await expect.poll(async () => (await state(page)).wireframes.draped, { timeout: 30_000 }).toEqual([]);
    await waitFrame(page);
    const back = await state(page);
    expect(back.wireframes.layers).toBe(1);
    expect(back.rasters).toMatchObject({ covered: [], cut: [] });
    fs.writeFileSync(shot("dtm.json"), JSON.stringify({ pickMs: picked.pickMs, uvErrorPx: uvError * IMAGE.w, lodError, heightGap, triangles: [full[0].probe.triangles, coarse[0].probe.triangles], stats: again.stats }, null, 2));
  } finally {
    await close(app);
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
