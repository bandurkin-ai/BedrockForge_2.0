// Э10, подшаг 10.12 (ADR 0015 «Замеры», ADR 0016 п. 10): the string editor on a layer of 1 million vertices in the
// real application under software WebGL → bench/out/editor_1m.json. A layer of 1000 lines × 1000 vertices (float64,
// X ≈ 5·10⁵, Y ≈ 7·10⁶) written through the core; «Редактировать» → the session ready (the panel, the geometry
// loaded, the editing layer drawn); the screen index of the snap rebuilt; snap queries at vertices on the screen
// (the path of a hover: `EditSession.resolve`, timed in the page, not by pixels); an operation over one line
// (`lines.modify reverse`); the save of the one changed line (`lines.save`: PUT and the layer read back).
// ADR 0015: open ≤ 1.5 s, operation ≤ 5 ms, snap ≤ 2 ms, index ≤ 150 ms; under SwiftShader the limits ×2 — the
// strict numbers go to the report (bench/out/editor_1m.json, budgets.json → external.editor_1m); the test itself
// holds wider limits: the same spec runs on the packaged .exe of the Windows runner.
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { close, launch, newLineLayer, saveGridLines, setWindowSize, waitFrame } from "./helpers";

const ROOT = path.resolve(__dirname, "..", "..");
const GRID = { lines: 1000, vertices: 1000, x0: 500_000, y0: 7_000_000, step: 1 };
/** ADR 0015, ms */
const ADR = { open_ms: 1500, op_ms: 5, snap_ms: 2, index_ms: 150 };
/** what the test holds (a slow runner, the packaged .exe on Windows): ×4 of the ADR, the save by the first measure */
const HOLD = { open_ms: 6000, op_ms: 20, snap_ms: 8, index_ms: 600, save_ms: 15_000 };
const OPS = 21; // odd: the line is reversed at the end, the save carries one line
const QUERIES = 400;

type Ed = { state(): { layerId: number; version: number; dirty: boolean; lines: number } | null; session(): unknown };
const run = (page: Page, id: string, args: Record<string, unknown> = {}) => page.evaluate(([i, a]) => window.__bfScene!.run(i as string, a as Record<string, unknown>), [id, args] as const);
const edState = (page: Page) => page.evaluate(() => (window as unknown as { __bfEditor: Ed }).__bfEditor.state());

const median = (xs: number[]) => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)];
const r2 = (v: number) => Math.round(v * 100) / 100;

test("perfEditor: a layer of 1 million vertices — open, snap index, snap query, an operation, a save → bench/out/editor_1m.json", async () => {
  test.setTimeout(600_000);
  const started = Date.now();
  const { app, page } = await launch({ BF_E2E: "1" });
  try {
    await setWindowSize(app, 1024, 640); // the window of editor.spec: the panel covers half of the 3D window
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByLabel("Лента").getByRole("button", { name: "Новый проект" }).click();
    await page.getByRole("dialog").last().getByRole("textbox").fill("Редактор 1 млн");
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("project-selector")).toContainText("Редактор 1 млн", { timeout: 60_000 });
    await waitFrame(page);

    // ---- the layer through the core; the scene shows it before the editor opens (as the user sees it) ------------
    const layer = await newLineLayer(page, "Миллион");
    const t0 = Date.now();
    const version = await saveGridLines(page, layer.id, layer.version, GRID, "1 млн вершин");
    const writeS = (Date.now() - t0) / 1000;
    const key = `ln:${layer.id}`;
    await expect.poll(() => page.evaluate((k) => window.__bfScene!.state().layers.includes(k), key), { timeout: 120_000, intervals: [200, 400] }).toBe(true);
    expect((await run(page, "scene.view", { view: "top" })).ok).toBe(true);
    expect((await run(page, "scene.fit")).ok).toBe(true);
    await waitFrame(page, 180_000);

    // ---- «Редактировать»: the command, the panel, every line in the session, the editing layer drawn ---------------
    const open = await page.evaluate(async (id) => {
      const sc = window.__bfScene!;
      const start = performance.now();
      const r = await sc.run("lines.edit", { layer_id: id });
      const cmd = performance.now() - start;
      const until = (ok: () => boolean) =>
        new Promise<number>((res) => {
          const f = () => (ok() ? res(performance.now() - start) : setTimeout(f, 4));
          f();
        });
      const panel = await until(() => !!document.querySelector('[data-testid="edit-panel"]'));
      const session = await until(() => (window as unknown as { __bfEditor?: Ed }).__bfEditor?.state()?.lines === 1000);
      const added = await until(() => sc.state().layers.includes("edit"));
      const timeline: unknown[] = [];
      const tick = setInterval(() => { const st = sc.state(); timeline.push([Math.round(performance.now() - start), st.idle, st.loading, st.frames, st.lines.layers, st.lines.segments, st.lines.points, st.ready]); }, 1000);
      const drawn = await until(() => sc.ready());
      clearInterval(tick);
      (window as unknown as { __tl: unknown }).__tl = timeline;
      return { ok: r.ok, result: r.result, cmd, panel, session, added, drawn, timeline: (window as unknown as { __tl: unknown }).__tl };
    }, layer.id);
    expect(open.ok, open.result).toBe(true);
    expect(await edState(page)).toMatchObject({ layerId: layer.id, version, dirty: false, lines: GRID.lines });
    await expect(page.getByTestId("edit-panel")).toBeVisible();
    await waitFrame(page, 180_000);

    // ---- the screen index of the snap and the queries at vertices on the screen ------------------------------------
    const snap = await page.evaluate(
      ([queries, g]) => {
        type Hit = { kind: string; point: number[] } | null;
        type Session = {
          viewChanged(): void;
          snapIndex: { rebuild(): void; size: number };
          snap: { radius: number; enabled: boolean; kinds: string[] };
          layer: { lines: { id: number; points: number[][] }[] };
          resolve(e: { x: number; y: number; clientX: number; clientY: number }): { point: number[] | null; snap: Hit };
        };
        const s = (window as unknown as { __bfEditor: Ed }).__bfEditor.session() as Session;
        const builds: number[] = [];
        for (let k = 0; k < 8; k++) {
          s.viewChanged();
          const t = performance.now();
          s.snapIndex.rebuild();
          builds.push(performance.now() - t);
        }
        const canvas = document.querySelector('[data-testid="scene-host"] canvas')!.getBoundingClientRect();
        const times: number[] = [];
        let vertex = 0;
        let exact = 0;
        let hits = 0;
        const samples: { aim: number[]; got: number[] }[] = [];
        for (let k = 0; times.length < queries && k < queries * 20; k++) {
          const line = s.layer.lines[(k * 37) % g.lines];
          const v = line.points[(k * 101 + 13) % g.vertices];
          const p = window.__bfScene!.screen(v[0], v[1], v[2]);
          if (!p || p.x < canvas.left + 4 || p.x > canvas.right - 4 || p.y < canvas.top + 4 || p.y > canvas.bottom - 4) continue;
          const e = { x: p.x - canvas.left + 1, y: p.y - canvas.top + 1, clientX: p.x + 1, clientY: p.y + 1 };
          const t = performance.now();
          const h = s.resolve(e);
          times.push(performance.now() - t);
          if (h.snap) hits++;
          if (h.snap?.kind === "vertex") {
            if (samples.length < 4) samples.push({ aim: v, got: h.snap.point });
            vertex++;
            if (h.snap.point[0] === v[0] && h.snap.point[1] === v[1] && h.snap.point[2] === v[2]) exact++;
          }
        }
        const v0 = s.layer.lines[3].points[100];
        const view = (s as unknown as { view: { transform(): { matrix: number[]; width: number; height: number } } }).view.transform();
        const m = view.matrix;
        const cx = m[0] * v0[0] + m[4] * v0[1] + m[8] * v0[2] + m[12];
        const cy = m[1] * v0[0] + m[5] * v0[1] + m[9] * v0[2] + m[13];
        const cw = m[3] * v0[0] + m[7] * v0[1] + m[11] * v0[2] + m[15];
        const local = [((cx / cw + 1) / 2) * view.width, ((1 - cy / cw) / 2) * view.height];
        const client = window.__bfScene!.screen(v0[0], v0[1], v0[2]);
        const rects = [...document.querySelectorAll('[data-testid="scene-host"] canvas')].map((c) => { const r = c.getBoundingClientRect(); return [r.left, r.top, r.width, r.height, (c as HTMLCanvasElement).width]; });
        (samples as unknown[]).push({ local, client, rects, size: [view.width, view.height], dpr: devicePixelRatio });
        return { samples, builds, times, hits, vertex, exact, size: s.snapIndex.size, radius: s.snap.radius, enabled: s.snap.enabled, kinds: s.snap.kinds };
      },
      [QUERIES, GRID] as const,
    );
    expect(snap.enabled).toBe(true);
    expect(snap.times.length).toBe(QUERIES);
    // 1 px off a vertex with the whole layer in the view: the vertices are about a pixel apart, so the target is a
    // vertex or the midpoint of a segment next to it — always something (the share of vertices goes to the report)
    expect(snap.hits, JSON.stringify(snap.kinds)).toBeGreaterThan(QUERIES * 0.95);
    expect(snap.vertex).toBeGreaterThan(QUERIES / 2);

    // ---- an operation over one line: «Развернуть» on the selected line, OPS times --------------------------------
    const op = await page.evaluate(async (n) => {
      type Session = { layer: { lines: { id: number }[] }; setSelection(sel: { lines: number[]; vertices: unknown[] }): void; undo(): void; redo(): void };
      const s = (window as unknown as { __bfEditor: Ed }).__bfEditor.session() as Session;
      const sc = window.__bfScene!;
      s.setSelection({ lines: [s.layer.lines[500].id], vertices: [] });
      const times: number[] = [];
      const failed: string[] = [];
      for (let k = 0; k < n; k++) {
        const t = performance.now();
        const r = await sc.run("lines.modify", { action: "reverse" });
        times.push(performance.now() - t);
        if (!r.ok) failed.push(r.result);
      }
      // the frame after the last one: the editing layer redrawn
      const t1 = performance.now();
      await new Promise<void>((res) => {
        const f = () => (sc.ready() ? res() : setTimeout(f, 4));
        setTimeout(f, 0);
      });
      const frame = performance.now() - t1;
      // undo and redo straight on the session (the history and the own snap source, no command around them)
      const undo: number[] = [];
      for (let k = 0; k < 5; k++) {
        let t = performance.now();
        s.undo();
        undo.push(performance.now() - t);
        t = performance.now();
        s.redo();
        undo.push(performance.now() - t);
      }
      return { times, failed, frame, undo };
    }, OPS);
    expect(op.failed).toEqual([]);
    expect(await edState(page)).toMatchObject({ dirty: true });

    // ---- the save of the one changed line: PUT …/geometry and the layer read back ----------------------------------
    const save = await page.evaluate(async () => {
      const t = performance.now();
      const r = await window.__bfScene!.run("lines.save");
      return { ok: r.ok, result: r.result, ms: performance.now() - t };
    });
    expect(save.ok, save.result).toBe(true);
    expect(await edState(page)).toMatchObject({ version: version + 1, dirty: false, lines: GRID.lines });
    const journal = await page.evaluate(async (id) => {
      const c = window.bf!.core()!;
      return (await (await fetch(`${c.baseUrl}/api/v1/lines/layers/${id}/versions`, { headers: { "X-BF-Token": c.token } })).json()) as { version: number };
    }, layer.id);
    expect(journal.version).toBe(version + 1);

    const st = await page.evaluate(() => window.__bfScene!.state());
    const builds = [...snap.builds.slice(1)].sort((a, b) => a - b);
    const times = [...snap.times].sort((a, b) => a - b);
    const out = {
      lines: GRID.lines,
      vertices: GRID.lines * GRID.vertices,
      snap_targets: snap.size,
      open_ms: Math.round(open.drawn),
      open_command_ms: Math.round(open.cmd),
      open_panel_ms: Math.round(open.panel),
      open_session_ms: Math.round(open.session),
      index_ms: r2(median(builds)),
      index_best_ms: r2(builds[0]),
      index_first_ms: r2(snap.builds[0]),
      snap_ms: r2(times[Math.floor(times.length * 0.95)]),
      snap_median_ms: r2(median(times)),
      snap_max_ms: r2(times[times.length - 1]),
      snap_queries: snap.times.length,
      snap_hits: snap.hits,
      snap_vertex: snap.vertex,
      snap_exact: snap.exact,
      op_ms: r2(median(op.times)),
      op_max_ms: r2(Math.max(...op.times)),
      op_frame_ms: Math.round(op.frame),
      undo_ms: r2(median(op.undo)),
      save_ms: Math.round(save.ms),
      write_s: Math.round(writeS * 10) / 10,
      renderer: st.gpu.renderer,
      platform: process.platform,
    };
    fs.mkdirSync(path.join(ROOT, "bench", "out"), { recursive: true });
    fs.writeFileSync(path.join(ROOT, "bench", "out", "editor_1m.json"), JSON.stringify(out, null, 2));
    console.log(`editor_1m: ${JSON.stringify(out)}`);
    console.log(`DEBUG open ${JSON.stringify(open)} samples ${JSON.stringify(snap.samples)}`);
    const software = /swiftshader|llvmpipe|software/i.test(st.gpu.renderer);
    const strict = Object.entries(ADR).map(([k, v]) => `${k} ${out[k as keyof typeof ADR]} / ${software ? v * 2 : v}`);
    console.log(`editor_1m against ADR 0015${software ? " ×2 (software rendering)" : ""}: ${strict.join(", ")}`);
    for (const [k, limit] of Object.entries(HOLD)) expect(out[k as keyof typeof HOLD], k).toBeLessThanOrEqual(limit);
    await page.getByTestId("edit-finish").click();
    await expect(page.getByTestId("edit-panel")).toHaveCount(0);
  } finally {
    await close(app);
  }
  console.log(`perfEditor: ${((Date.now() - started) / 1000).toFixed(1)} s`);
});
