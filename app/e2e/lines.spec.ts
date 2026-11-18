// Э7, подшаги 7.24–7.25 (ADR 0011 §4) under software WebGL2: lines, points, a text and a raster imported into
// the demo project appear in the 3D window as layers `ln:<id>` and `rs:<id>`; a raster without an elevation
// lies under the lowest hole; a hidden layer leaves the frame; far away a dense line is drawn at a coarser
// level («упрощено»); a click on a line picks it within 50 ms; after a lost context everything comes again;
// a raster made vertical is a backdrop under clipping and an ordinary object without it.
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import zlib from "node:zlib";
import { close, launch, shot, waitFrame } from "./helpers";

const state = (page: Page) => page.evaluate(() => window.__bfScene!.state());

function crc32(data: Buffer): number {
  let c = ~0;
  for (const b of data) {
    c ^= b;
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  return ~c >>> 0;
}

/** An 8-bit RGB PNG with a gradient (no image library in the tests). */
function png(w: number, h: number): Buffer {
  const row = w * 3 + 1;
  const raw = Buffer.alloc(row * h);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) raw.set([(x * 255) / w, (y * 255) / h, 160], y * row + 1 + x * 3);
  }
  const chunk = (type: string, data: Buffer) => {
    const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const out = Buffer.alloc(8 + body.length);
    out.writeUInt32BE(data.length, 0);
    body.copy(out, 4);
    out.writeUInt32BE(crc32(body), 4 + body.length);
    return out;
  };
  const head = Buffer.alloc(13);
  head.writeUInt32BE(w, 0);
  head.writeUInt32BE(h, 4);
  head.set([8, 2, 0, 0, 0], 8);
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk("IHDR", head), chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
}

const dxf = (entities: (string | number)[][]) => ["0", "SECTION", "2", "ENTITIES", ...entities.flat().map(String), "0", "ENDSEC", "0", "EOF", ""].join("\r\n");

const ZIGZAG = 600;
const SEGMENTS = 2 + ZIGZAG - 1;

/** South of the demo holes (x 5000…5400, y 8000…8300): a road, a point, a text; points with and without Z; an image. */
function makeFiles(dir: string): string[] {
  const files = { lines: path.join(dir, "roads.dxf"), points: path.join(dir, "relief.csv"), image: path.join(dir, "ortho.png"), world: path.join(dir, "ortho.pgw") };
  fs.writeFileSync(
    files.lines,
    dxf([
      [0, "LINE", 8, "ROADS", 10, 4960, 20, 7900, 30, 320, 11, 5440, 21, 7900, 31, 330],
      [0, "LINE", 8, "ROADS", 10, 5440, 20, 7900, 30, 330, 11, 5440, 21, 7860, 31, 331],
      [0, "POINT", 8, "MARKS", 10, 5000.5, 20, 7880, 30, 300],
      [0, "TEXT", 8, "MARKS", 10, 5100, 20, 7880, 30, 300, 40, 5, 1, "Дорога"],
      // a dense zigzag of 0.5 m: the coarser levels (4 and 32 m) straighten it
      [0, "LWPOLYLINE", 8, "ZIGZAG", 90, ZIGZAG, 70, 0, 38, 310, ...Array.from({ length: ZIGZAG }, (_, i) => [10, 4960 + (480 * i) / (ZIGZAG - 1), 20, 7820 + (i % 2) * 0.5]).flat()],
    ]),
  );
  fs.writeFileSync(files.points, "X;Y;Z;NAME\n5000,5;7850,25;301,5;Репер 1\n5100;7850;;Репер 2\n5200;7850;303;Репер 3\n", "utf-8");
  fs.writeFileSync(files.image, png(256, 192));
  // 2 m pixels: the image covers x 4950…5462, y 7966…8350 — under the holes
  fs.writeFileSync(files.world, ["2.0", "0.0", "0.0", "-2.0", "4951.0", "8349.0", ""].join("\n"));
  return Object.values(files);
}

test("3D: lines, points and a raster — layers, floor, hide, click on a line, vertical raster as a backdrop", async () => {
  test.setTimeout(300_000);
  const { app, page } = await launch({ BF_E2E: "1" });
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-objects-"));
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    await waitFrame(page);
    const before = await state(page);
    expect(before.lines.layers).toBe(0);
    expect(before.rasters.count).toBe(0);
    const pixelsBefore = (await page.evaluate(() => window.__bfScene!.pixels()))!.nonBackground;

    // import through the core, as the wizard does
    const api = (method: string, url: string, body?: unknown) =>
      page.evaluate(
        async ([m, u, b]) => {
          const c = window.bf!.core()!;
          const r = await fetch(`${c.baseUrl}/api/v1${u}`, { method: m as string, headers: { "X-BF-Token": c.token, "Content-Type": "application/json" }, body: b === null ? undefined : JSON.stringify(b) });
          return { status: r.status, body: (await r.json()) as Record<string, unknown> };
        },
        [method, url, body ?? null] as const,
      );
    const started = await api("POST", "/import/objects", { files: makeFiles(dir) });
    expect(started.status, JSON.stringify(started.body)).toBe(200);
    type Layer = { id: number; name: string; points_only: boolean };
    let layers: Layer[] = [];
    let rasterId = 0;
    await expect
      .poll(
        async () => {
          const files = (await api("GET", "/lines/files")).body.files as { layers: Layer[] }[];
          const rasters = (await api("GET", "/rasters")).body.rasters as { id: number }[];
          layers = files.flatMap((f) => f.layers);
          rasterId = rasters[0]?.id ?? 0;
          return layers.length === 4 && rasters.length === 1;
        },
        { timeout: 60_000 },
      )
      .toBe(true);

    // every layer and the raster are in the scene; the image came; what is shown is not simplified up close
    await expect.poll(async () => (await state(page)).lines.layers, { timeout: 30_000 }).toBe(layers.length);
    await page.keyboard.press("f");
    await waitFrame(page);
    const shown = await state(page);
    for (const l of layers) expect(shown.layers).toContain(`ln:${l.id}`);
    expect(shown.layers).toContain(`rs:${rasterId}`);
    expect(shown.lines.segments).toBe(SEGMENTS);
    expect(shown.lines.points).toBeGreaterThanOrEqual(5); // the DXF point and text, three points of the table
    expect(shown.lines.simplified).toBe(0);
    expect(shown.rasters.levels[String(rasterId)]).not.toBeNull();
    expect(shown.rasters.backdrops).toEqual([]);
    // the raster has no elevation: it lies a metre under the lowest hole, not at 0
    expect(shown.floor).not.toBe(0);
    const pixelsShown = (await page.evaluate(() => window.__bfScene!.pixels()))!.nonBackground;
    expect(pixelsShown).toBeGreaterThan(pixelsBefore + 5000); // the image fills a large part of the view
    await page.screenshot({ path: shot("lines-rasters.png") });

    // a hidden layer leaves the frame, its data stays
    const road = layers.find((l) => l.name === "ROADS")!;
    expect(road).toBeDefined();
    expect((await page.evaluate((id) => window.__bfScene!.run("layer.show", { layer: "lines", id, visible: false }), road.id)).ok).toBe(true);
    await waitFrame(page);
    expect((await state(page)).lines.segments).toBe(SEGMENTS - 2);
    await page.evaluate((id) => window.__bfScene!.run("layer.show", { layer: "lines", id, visible: true }), road.id);
    await waitFrame(page);
    expect((await state(page)).lines.segments).toBe(SEGMENTS);

    // far away the zigzag is drawn simplified (a coarser level is loaded), close again — in full
    const box = (await page.getByTestId("scene-host").boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    const zoomUntil = async (delta: number, done: (s: Awaited<ReturnType<typeof state>>) => boolean) => {
      for (let i = 0; i < 40; i++) {
        await page.mouse.wheel(0, delta);
        await waitFrame(page);
        if (done(await state(page))) return;
      }
      throw new Error(`the level of detail did not change: ${JSON.stringify((await state(page)).lines)}`);
    };
    await zoomUntil(400, (s) => s.lines.simplified > 0);
    const far = await state(page);
    expect(far.lines.segments).toBeLessThan(SEGMENTS);
    await expect(page.getByText(/Линии упрощены/)).toBeVisible();
    await zoomUntil(-400, (s) => s.lines.simplified === 0 && s.lines.segments === SEGMENTS);
    await page.keyboard.press("f");
    await waitFrame(page);

    // a click on the road from above: the line of the layer, on the CPU within 50 ms
    await page.keyboard.press("t");
    await waitFrame(page);
    const at = (await page.evaluate(() => window.__bfScene!.screen(5200, 7900, 325)))!;
    expect(at).not.toBeNull();
    await page.mouse.click(at.x, at.y + 2);
    await waitFrame(page);
    const picked = await state(page);
    expect(picked.picked).toMatchObject({ kind: "lines", id: road.id, on: "line" });
    expect(Math.abs((picked.picked as { xyz: number[] }).xyz[0] - 5200)).toBeLessThan(3);
    expect(picked.pickMs).toBeLessThan(50);
    await page.screenshot({ path: shot("lines-picked.png") });
    // a click on the image beside everything picks the raster
    const empty = (await page.evaluate(() => window.__bfScene!.screen(5450, 7980, 300)))!;
    await page.mouse.click(empty.x, empty.y);
    await waitFrame(page);
    expect((await state(page)).picked).toMatchObject({ kind: "raster", id: rasterId });

    // a lost context: the lines and the image come again from the core
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
    expect(again.lines).toMatchObject({ layers: layers.length, segments: SEGMENTS, points: shown.lines.points });
    expect(again.rasters.levels[String(rasterId)]).not.toBeNull();
    expect((await page.evaluate(() => window.__bfScene!.pixels()))!.nonBackground).toBeGreaterThan(pixelsBefore + 5000);

    // the raster made vertical along y = 8100: an ordinary object, and a backdrop once the section clips
    const info = (await api("GET", `/rasters/${rasterId}`)).body as { version: number };
    const moved = await api("PATCH", `/rasters/${rasterId}`, { expected_version: info.version, vertical: { x1: 5000, y1: 8100, x2: 5400, y2: 8100, z_top: 350, z_bottom: 50 } });
    expect(moved.status, JSON.stringify(moved.body)).toBe(200);
    await page.keyboard.press("x");
    await waitFrame(page);
    expect((await state(page)).rasters.backdrops).toEqual([]);
    expect((await page.evaluate(() => window.__bfScene!.run("section.set", { x0: 5000, y0: 8100, az: 90, towards: 25, away: 25 }))).ok).toBe(true);
    await page.evaluate(() => window.__bfScene!.run("section.clip", { on: true }));
    await waitFrame(page);
    const clipped = await state(page);
    expect(clipped.clipPlanes).toBeGreaterThan(0);
    expect(clipped.rasters.backdrops).toEqual([rasterId]);
    expect(clipped.stats.calls).toBeGreaterThan(1); // the holes in the window are drawn over the backdrop
    await page.screenshot({ path: shot("raster-backdrop.png") });
    await page.evaluate(() => window.__bfScene!.run("section.clip", { on: false }));
    await waitFrame(page);
    expect((await state(page)).rasters.backdrops).toEqual([]);
    fs.writeFileSync(shot("lines-rasters.json"), JSON.stringify({ pickMs: picked.pickMs, lines: shown.lines, rasters: clipped.rasters, stats: clipped.stats, floor: shown.floor }, null, 2));
  } finally {
    await close(app);
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
