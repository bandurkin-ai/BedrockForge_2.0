// TEMPORARY diagnostics of the first frame of the editing layer (10.21) — not committed.
import { expect, test } from "@playwright/test";
import fs from "node:fs";
import { close, launch, newLineLayer, saveGridLines, setWindowSize, waitFrame } from "./helpers";

const GRID = { lines: 1000, vertices: 1000, x0: 500_000, y0: 7_000_000, step: 1 };
const OUT = process.env.DIAG_OUT ?? "/tmp/diag.json";

test("tmpdiag", async () => {
  test.setTimeout(600_000);
  const { app, page } = await launch({ BF_E2E: "1" });
  try {
    await setWindowSize(app, 1024, 640);
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByLabel("Лента").getByRole("button", { name: "Новый проект" }).click();
    await page.getByRole("dialog").last().getByRole("textbox").fill("Диаг");
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("project-selector")).toContainText("Диаг", { timeout: 60_000 });
    await waitFrame(page);
    const layer = await newLineLayer(page, "Миллион");
    await saveGridLines(page, layer.id, layer.version, GRID, "1 млн");
    const key = `ln:${layer.id}`;
    await expect.poll(() => page.evaluate((k) => window.__bfScene!.state().layers.includes(k), key), { timeout: 120_000 }).toBe(true);
    await page.evaluate(() => window.__bfScene!.run("scene.view", { view: "top" }));
    await page.evaluate(() => window.__bfScene!.run("scene.fit"));
    const t0 = Date.now();
    await waitFrame(page, 180_000);
    const baseFrame = Date.now() - t0;
    const cdp = await page.context().newCDPSession(page);
    await cdp.send("Profiler.enable");
    await cdp.send("Profiler.setSamplingInterval", { interval: 1000 });
    await cdp.send("Profiler.start");
    await page.evaluate(() => {
      const calls: unknown[] = [];
      (window as unknown as { __calls: unknown[] }).__calls = calls;
      const P = WebGL2RenderingContext.prototype as unknown as Record<string, (...a: unknown[]) => unknown>;
      for (const name of ["bufferData", "bufferSubData", "drawArrays", "drawElements", "readPixels", "finish", "getError", "texImage2D", "compileShader", "linkProgram", "getProgramParameter"]) {
        const orig = P[name];
        P[name] = function (this: unknown, ...args: unknown[]) {
          const t = performance.now();
          const r = orig.apply(this, args);
          const d = performance.now() - t;
          if (d > 20) {
            const a1 = args[1] as { byteLength?: number } | number;
            calls.push([name, Math.round(t), Math.round(d), typeof a1 === "number" ? a1 : a1?.byteLength, args[2], (new Error().stack ?? "").split("\n").slice(2, 7).map((x) => x.trim().slice(0, 90)).join(" | ")]);
          }
          return r;
        };
      }
    });
    const timeline = await page.evaluate(async (id) => {
      const sc = window.__bfScene! as unknown as { run: (i: string, a: unknown) => Promise<unknown>; ready: () => boolean; state: () => Record<string, unknown> };
      const start = performance.now();
      const log: unknown[] = [];
      let lastRaf = performance.now();
      const gaps: number[][] = [];
      const raf = () => { const n = performance.now(); if (n - lastRaf > 200) gaps.push([Math.round(lastRaf - start), Math.round(n - lastRaf)]); lastRaf = n; if (n - start < 120000) requestAnimationFrame(raf); };
      requestAnimationFrame(raf);
      void sc.run("lines.edit", { layer_id: id });
      await new Promise<void>((res) => {
        const f = () => {
          const st = sc.state();
          log.push([Math.round(performance.now() - start), st.idle, st.frames, st.loading, (st.layers as string[]).join(","), JSON.stringify(st.gpu)]);
          if (sc.ready() && (st.layers as string[]).includes("edit")) res(); else setTimeout(f, 250);
        };
        f();
      });
      return { total: performance.now() - start, log, gaps, start, calls: (window as unknown as { __calls: unknown[] }).__calls };
    }, layer.id);
    const prof = (await cdp.send("Profiler.stop")).profile;
    const self = new Map<string, number>();
    const dt = prof.timeDeltas ?? [];
    const byId = new Map(prof.nodes.map((n) => [n.id, n]));
    (prof.samples ?? []).forEach((sid, i) => {
      const n = byId.get(sid)!;
      const k = `${n.callFrame.functionName || "(anon)"} ${n.callFrame.url.split("/").pop()}:${n.callFrame.lineNumber}`;
      self.set(k, (self.get(k) ?? 0) + (dt[i] ?? 0) / 1000);
    });
    const top = [...self.entries()].sort((a, b) => b[1] - a[1]).slice(0, 30);
    fs.writeFileSync(OUT, JSON.stringify({ baseFrame, timeline, top }, null, 1));
  } finally {
    await close(app);
  }
});
