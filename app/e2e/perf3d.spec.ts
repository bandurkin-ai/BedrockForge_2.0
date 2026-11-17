// The frame of the 3D window under software rendering (ADR 0007 §9, «Замеры»): 2000 synthetic holes and one
// display along them, 60 frames turning a full circle → bench/out/frame_sw.json. Budgets only on the
// deterministic counters (draw calls, triangles, video memory: bench/budgets.json → external.frame_sw); the ms
// per frame of SwiftShader go to the report (`python -m bench.run --check` reads the file when it is there).
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
