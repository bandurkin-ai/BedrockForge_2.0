// Э8, подшаг 8.26 (ADR 0013 «Демо», §5–§6; ТЗ 7.5, 7.14, 7.15, 7.21, 9.4): the wireframes of the demo as a user
// meets them. «Рудное тело 1» of «Каркасы» in the tree → «Свойства»: a closed solid with its volume, «Проверить»
// fills «Проверки». With the relief, the lines and the holes hidden, a click on the ore body is answered by the
// core with its exact triangle. A section line with clipping shows the section lines of the ore bodies (closed)
// and of the fault (open) — the lines of the core. The holes shown again, «Пересечения» of their style marks
// where they cross the ore bodies and the fault: as many marks as the core finds. The ore body goes to a binary
// STL through «Экспорт каркасов». (Each step in detail: exact.spec, checks.spec, wireframesExport.spec.)
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { close, launch, shot, waitFrame } from "./helpers";

const state = (page: Page) => page.evaluate(() => window.__bfScene!.state());
const flat = (text: string | null) => (text ?? "").replace(/\s/g, " ");
const ru = (v: number, digits: number) => v.toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits }).replace(/\s/g, " ");

test("wireframes of the demo: «Свойства» and «Проверить», the exact pick, section lines, marks of crossings, export", async () => {
  test.setTimeout(480_000);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-wireframes-"));
  const { app, page } = await launch({ BF_E2E: "1" });
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    await waitFrame(page);

    const api = (method: string, url: string, body?: unknown) =>
      page.evaluate(
        async ([m, u, b]) => {
          const c = window.bf!.core()!;
          const r = await fetch(`${c.baseUrl}/api/v1${u}`, { method: m as string, headers: { "X-BF-Token": c.token, "Content-Type": "application/json" }, body: b === null ? undefined : JSON.stringify(b) });
          return { status: r.status, body: (await r.json()) as Record<string, unknown> };
        },
        [method, url, body ?? null] as const,
      );
    /** The header of a BFA1 answer of the core. */
    const header = (url: string) =>
      page.evaluate(async (u) => {
        const c = window.bf!.core()!;
        const buf = await (await fetch(`${c.baseUrl}/api/v1${u}`, { headers: { "X-BF-Token": c.token } })).arrayBuffer();
        return JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 8, new DataView(buf).getUint32(4, true)))) as Record<string, unknown>;
      }, url);
    const run = (id: string, args: Record<string, unknown>) => page.evaluate(([i, a]) => window.__bfScene!.run(i as string, a as Record<string, unknown>), [id, args] as const);

    type Wireframe = { id: number; name: string; dtm: unknown; color: string; triangle_count: number; extent: number[]; stats: { volume: number | null; closed: boolean; checks?: { state: string } } };
    // the body stitched from contours of Э9 (core demo/contours.py) is not the subject here: without it the
    // section, the marks of crossings and the counts below are those of the wireframes of Э8
    const stitched = ((await api("GET", "/wireframes")).body.wireframes as Wireframe[]).find((w) => w.name === "Рудное тело 3")!;
    expect((await api("DELETE", `/wireframes/${stitched.id}`)).status).toBe(200);
    const all =(await api("GET", "/wireframes")).body.wireframes as Wireframe[];
    const byName = (name: string) => all.find((w) => w.name === name)!;
    const [body, lens, fault, relief] = ["Рудное тело 1", "Рудное тело 2", "Разлом", "Рельеф"].map(byName);
    const solids = [body, lens, fault].map((w) => w.id).sort((a, b) => a - b);
    expect(all.filter((w) => w.dtm === null).map((w) => w.id).sort((a, b) => a - b)).toEqual(solids);
    expect(body.stats.closed && lens.stats.closed && !fault.stats.closed).toBe(true);
    const sets = (await api("GET", "/drillholes/sets")).body.sets as { id: number }[];
    await expect.poll(async () => (await state(page)).wireframes.layers, { timeout: 60_000 }).toBe(4);

    // ---- «Каркасы» of the tree → «Свойства»: a closed solid with its volume; «Проверить» ------------------------
    const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
    await tree.getByTestId(`tree-wireframe-${body.id}`).getByText(body.name, { exact: true }).click();
    const props = page.getByLabel("Правая панель").getByTestId("props-wireframe");
    const checks = props.getByTestId("props-checks");
    await expect(checks).toHaveAttribute("data-state", "none");
    let text = flat(await checks.textContent());
    expect(text).toContain("Замкнутостьзамкнут");
    expect(text).toContain(`Объём${ru(body.stats.volume!, 2)} м³`);
    expect(text).toContain("Самопересеченияне проверены");
    await checks.getByRole("button", { name: "Проверить" }).click();
    await expect(checks).toHaveAttribute("data-state", "done", { timeout: 240_000 });
    text = flat(await checks.textContent());
    expect(text).toContain("Нормалисогласованы, смотрят наружу");
    expect(text).toContain("Самопересечениянет");
    expect(text).toMatch(/Проверено\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}, за /);
    await expect(checks).toHaveAttribute("data-pairs", "0");

    // ---- the exact pick: the relief, the lines and the holes out of the way, a click on the ore body ------------
    const files = (await api("GET", "/lines/files")).body.files as { layers: { id: number }[] }[];
    for (const layer of files.flatMap((f) => f.layers)) expect((await run("layer.show", { layer: "lines", id: layer.id, visible: false })).ok).toBe(true);
    expect((await run("layer.show", { layer: "wireframe", id: relief.id, visible: false })).ok).toBe(true);
    expect((await run("layer.show", { layer: "set", id: sets[0].id, visible: false })).ok).toBe(true);
    await page.keyboard.press("t");
    await waitFrame(page);
    const target = [5100, 8180]; // over the lens of «Рудное тело 1», west of «Рудное тело 2»
    const at = (await page.evaluate(([x, y]) => window.__bfScene!.screen(x, y, 340), target))!;
    await page.mouse.click(at.x, at.y);
    await expect
      .poll(async () => {
        const p = (await state(page)).picked;
        return p?.kind === "wireframe" && p.exact ? p.exact.wireframeId : null;
      }, { timeout: 30_000 })
      .toBe(body.id);
    const exact = ((await state(page)).picked as Extract<BfSceneState["picked"], { kind: "wireframe" }>).exact!;
    expect(Math.hypot(exact.point[0] - target[0], exact.point[1] - target[1])).toBeLessThan(3); // under the cursor
    expect(exact.point[2]).toBeGreaterThan(body.extent[2]);
    expect(exact.point[2]).toBeLessThan(body.extent[5]);
    await expect(page.getByLabel("Правая панель").getByTestId("props-wireframe").getByTestId("props-picked-triangle")).toHaveAttribute("data-exact", "yes");
    await page.screenshot({ path: shot("wireframes-pick.png") });

    // ---- a section with clipping: the lines of the core, closed for the ore bodies, open for the fault -----------
    const line = { x0: 4950, y0: 8120.5, az: 90 };
    expect((await run("section.set", line)).ok).toBe(true);
    expect((await run("section.clip", { on: true })).ok).toBe(true);
    await expect.poll(async () => (await state(page)).exact.sections.map((s) => s.wireframe).sort((a, b) => a - b), { timeout: 30_000 }).toEqual(solids); // the hidden relief has none
    expect((await run("scene.view", { view: "section" })).ok).toBe(true);
    await waitFrame(page);
    for (const w of [body, lens, fault]) {
      const core = await header(`/viz/wireframes/${w.id}/section?x0=${line.x0}&y0=${line.y0}&az=${line.az}`);
      const shown = (await state(page)).exact.sections.find((s) => s.wireframe === w.id)!;
      expect(shown.lines).toBeGreaterThanOrEqual(1);
      const closed = w === fault ? 0 : shown.lines; // a solid gives closed lines, the fault an open one
      // a closed line of k points has k segments, an open one k − 1
      expect(shown).toMatchObject({ lines: core.lines, closed, segments: (core.points as number) - (core.lines as number) + closed, color: w.color });
      expect(shown.bbox![1]).toBeCloseTo(line.y0, 6);
    }
    await page.screenshot({ path: shot("wireframes-section.png") });
    expect((await run("section.clip", { on: false })).ok).toBe(true);
    await expect.poll(async () => (await state(page)).exact.sections.length).toBe(0);

    // ---- the holes again: marks where they cross the ore bodies and the fault -----------------------------------
    expect((await run("layer.show", { layer: "set", id: sets[0].id, visible: true })).ok).toBe(true);
    expect((await run("holes.trace_style", { set_id: sets[0].id, intersections: true })).ok).toBe(true);
    const found = await header(`/viz/sets/${sets[0].id}/intersections?wireframes=${solids.join(",")}`);
    expect(found.n as number).toBeGreaterThan(100); // nearly every hole enters the lens and leaves it
    await expect.poll(async () => (await state(page)).exact.marks[0]?.marks ?? 0, { timeout: 30_000 }).toBe(found.n);
    expect((await state(page)).exact.marks[0].objects).toEqual(solids);
    expect((await run("scene.view", { view: "iso" })).ok).toBe(true);
    await waitFrame(page);
    await page.screenshot({ path: shot("wireframes-marks.png") });

    // ---- the ore body to a binary STL through «Экспорт каркасов» ---------------------------------------------
    const stl = path.join(dir, "рудное тело.stl");
    await app.evaluate(({ dialog }, f) => {
      dialog.showSaveDialog = (async () => ({ canceled: false, filePath: f })) as typeof dialog.showSaveDialog;
    }, stl);
    await run("wireframe.export", { format: "stl", wireframe_id: body.id });
    const win = page.getByRole("dialog", { name: "Экспорт каркасов" });
    await expect(win.getByLabel(`Каркас «${body.name}»`)).toBeChecked();
    await win.getByRole("button", { name: "Сохранить как…" }).click();
    await expect(win.getByLabel("Файл экспорта")).toHaveValue(stl);
    await win.getByRole("button", { name: "Экспортировать" }).click();
    await expect(win).toHaveCount(0);
    await expect.poll(() => (fs.existsSync(stl) ? fs.statSync(stl).size : 0), { timeout: 60_000 }).toBe(84 + 50 * body.triangle_count);
  } finally {
    await close(app);
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
