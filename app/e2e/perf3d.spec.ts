// The frame of the 3D window under software rendering (ADR 0007 §9, «Замеры»): 2000 synthetic holes and one
// display along them, 60 frames turning a full circle → bench/out/frame_sw.json. Budgets only on the
// deterministic counters (draw calls, triangles, video memory: bench/budgets.json → external.frame_sw); the ms
// per frame of SwiftShader go to the report (`python -m bench.run --check` reads the file when it is there).
// Э7 (ADR 0011 «Замеры», ТЗ 5.3): the lines, the plan and the DTM of the demo stay in the frame; before the
// benchmark — the first frame of the demo after a lost context (≤ 3 s) and a click on a line and on the
// surface (≤ 50 ms on the CPU).
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { close, launch, setWindowSize, waitFrame } from "./helpers";

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
