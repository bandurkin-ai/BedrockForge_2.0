// Э8, подшаги 8.17, 8.23–8.24 (ADR 0013 §5–§6, ADR 0007 §5; ТЗ 7.14, 7.15, 7.21) under software WebGL2, on the
// demo project. A click on the relief of the demo is answered by the core: the exact triangle under the click —
// its point lies on the float64 surface, «Свойства» show the point, the dip and the dip direction. A section
// through the DTM «Рельеф» with clipping on shows the wireframe by its section line: the line of the core, in the
// colour of the wireframe, seen on the screen; moving the line replaces it, clipping off removes it. The style
// of the traces «Пересечения» marks where the holes cross wireframes — not the DTMs: the relief of the demo is
// built through the collars, every hole would get «0,000 м · Рельеф». With «с ЦТМ» the holes are marked on the
// relief too: as many marks as the core finds. (The wireframes of «Каркасы» of the demo are hidden until the
// marks: the pick and the section here are of the relief; the whole story is `wireframes.spec.ts`.)
import { expect, test, type Page } from "@playwright/test";
import { close, launch, shot, waitFrame } from "./helpers";

const state = (page: Page) => page.evaluate(() => window.__bfScene!.state());
const LINE_COLOR = "#ff00ff";

test("3D: exact pick of the relief in «Свойства», the section line of a DTM under clipping, marks of crossings", async () => {
  test.setTimeout(420_000);
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
    /** The header of a BFA1 answer of the core and its float64 array `xyz` (the first `limit` numbers). */
    const bfa1 = (url: string, limit = 300) =>
      page.evaluate(
        async ([u, n]) => {
          const c = window.bf!.core()!;
          const r = await fetch(`${c.baseUrl}/api/v1${u}`, { headers: { "X-BF-Token": c.token } });
          const buf = await r.arrayBuffer();
          const length = new DataView(buf).getUint32(4, true);
          const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 8, length))) as { arrays: [string, string, number, number][] } & Record<string, unknown>;
          const xyz = header.arrays.find((a) => a[0] === "xyz")!;
          return { status: r.status, header, xyz: Array.from(new Float64Array(buf, xyz[3], Math.min(xyz[2], n as number))) };
        },
        [url, limit] as const,
      );
    const run = (id: string, args: Record<string, unknown>) => page.evaluate(([i, a]) => window.__bfScene!.run(i as string, a as Record<string, unknown>), [id, args] as const);

    type Wireframe = { id: number; name: string; version: number; dtm: unknown; extent: number[] };
    const relief = ((await api("GET", "/wireframes")).body.wireframes as Wireframe[]).find((w) => w.name === "Рельеф")!;
    expect(relief.dtm).not.toBeNull();
    // only the holes and the relief stay in the view: a click on the surface must not meet a survey point or a line
    const files = (await api("GET", "/lines/files")).body.files as { layers: { id: number }[] }[];
    for (const layer of files.flatMap((f) => f.layers)) expect((await run("layer.show", { layer: "lines", id: layer.id, visible: false })).ok).toBe(true);
    // … nor a wireframe of «Каркасы» (the ore bodies and the fault of the demo, Э8): they come back for the marks
    const others = ((await api("GET", "/wireframes")).body.wireframes as Wireframe[]).filter((w) => w.id !== relief.id);
    for (const w of others) expect((await run("layer.show", { layer: "wireframe", id: w.id, visible: false })).ok).toBe(true);
    await page.keyboard.press("t");
    await waitFrame(page);

    // ---- 8.23: the exact triangle under a click, in «Свойства» ---------------------------------------------
    const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
    await tree.getByTestId(`tree-dtm-${relief.id}`).getByText("Рельеф", { exact: true }).click();
    const props = page.getByLabel("Правая панель").getByTestId("props-dtm");
    await expect(props).toContainText("Щёлкните поверхность в 3D-окне");
    await waitFrame(page);
    const target = [5437.5, 8312.25]; // on the hills east of the hole grid, away from every collar
    const at = (await page.evaluate(([x, y]) => window.__bfScene!.screen(x, y, 420), target))!;
    await page.mouse.click(at.x, at.y);
    await expect.poll(async () => (await state(page)).picked?.kind, { timeout: 15_000 }).toBe("wireframe");
    await expect
      .poll(async () => {
        const p = (await state(page)).picked;
        return p?.kind === "wireframe" && p.exact ? p.exact.wireframeId : null;
      }, { timeout: 30_000 })
      .toBe(relief.id);
    const picked = (await state(page)).picked as Extract<BfSceneState["picked"], { kind: "wireframe" }>;
    const exact = picked.exact!;
    expect(picked.xyz).toEqual(exact.point); // the picked place is now the point on the exact triangle
    expect(Math.hypot(exact.point[0] - target[0], exact.point[1] - target[1])).toBeLessThan(3); // under the cursor
    expect(exact.dip).not.toBeNull();
    expect(exact.dip!).toBeGreaterThanOrEqual(0);
    expect(exact.dip!).toBeLessThan(45); // a gentle relief
    // the same point by the core, straight down: on the float64 surface to a micron
    const down = await api("POST", "/wireframes/pick", { origin: [exact.point[0], exact.point[1], 2000], direction: [0, 0, -1], wireframe_ids: [relief.id] });
    expect(down.status).toBe(200);
    const hit = down.body.hit as { triangle: number; point: number[]; dip: number; dip_direction: number | null };
    expect(hit.triangle).toBe(exact.triangle);
    expect(Math.abs(hit.point[2] - exact.point[2])).toBeLessThan(1e-6);
    expect(hit.dip).toBeCloseTo(exact.dip!, 9);
    // «Свойства»: the point to the millimetre, the dip and the dip direction
    const triangle = props.getByTestId("props-picked-triangle");
    await expect(triangle).toHaveAttribute("data-exact", "yes");
    const ru = (v: number, digits: number) => v.toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits });
    const text = ((await triangle.textContent()) ?? "").replace(/\s/g, " ");
    expect(text).toContain(ru(exact.point[0], 3).replace(/\s/g, " "));
    expect(text).toContain(ru(exact.point[2], 3).replace(/\s/g, " "));
    expect(text).toContain(`Угол падения${ru(exact.dip!, 1)}°`);
    expect(text).toContain(exact.dipDirection === null ? "треугольник горизонтален" : `Азимут падения${ru(exact.dipDirection, 1)}°`);
    expect(text).toContain("Треугольник №");
    await page.screenshot({ path: shot("exact-pick.png") });

    // ---- 8.24: a section through the DTM «Рельеф» — its section line -------------------------------------
    expect((await state(page)).exact.sections).toEqual([]); // no clipping: no section lines
    expect((await api("PATCH", `/wireframes/${relief.id}`, { expected_version: relief.version, color: LINE_COLOR })).status).toBe(200);
    const line = { x0: 4950, y0: 8120.5, az: 90 };
    expect((await run("section.set", line)).ok).toBe(true);
    expect((await run("section.clip", { on: true })).ok).toBe(true);
    await expect.poll(async () => (await state(page)).exact.sections.length, { timeout: 30_000 }).toBe(1);
    await waitFrame(page);
    const core = await bfa1(`/viz/wireframes/${relief.id}/section?x0=${line.x0}&y0=${line.y0}&az=${line.az}`, 30_000);
    expect(core.status).toBe(200);
    expect(core.header.kind).toBe("section");
    const shown = (await state(page)).exact.sections[0];
    expect(shown.wireframe).toBe(relief.id);
    expect(shown.lines).toBe(core.header.lines); // an open surface: one line from border to border
    expect(shown.lines).toBeGreaterThanOrEqual(1);
    expect(shown.closed).toBe(0);
    expect(shown.segments).toBe((core.header.points as number) - (core.header.lines as number));
    expect(shown.color).toBe(LINE_COLOR); // the colour of the wireframe
    expect(shown.bbox![1]).toBeCloseTo(line.y0, 6); // in the plane of the section line…
    expect(shown.bbox![4]).toBeCloseTo(line.y0, 6);
    expect(shown.bbox![3] - shown.bbox![0]).toBeGreaterThan(500); // … across the whole relief (600 m)
    // seen on the screen: looking at the plane, the points of the line are drawn in the colour of the wireframe
    expect((await run("scene.view", { view: "section" })).ok).toBe(true);
    await waitFrame(page);
    let seen = 0;
    const points = core.xyz.length / 3;
    for (let k = 0; k < 12; k++) {
      const i = Math.floor(((k + 0.5) / 12) * points) * 3;
      const px = await page.evaluate(([x, y, z]) => {
        const s = window.__bfScene!.screen(x, y, z);
        return s ? window.__bfScene!.pixel(s.x, s.y) : null;
      }, [core.xyz[i], core.xyz[i + 1], core.xyz[i + 2]]);
      if (px && px[0] > 200 && px[1] < 90 && px[2] > 200) seen++;
    }
    expect(seen).toBeGreaterThanOrEqual(9); // labels and traces may cover a point or two
    await page.screenshot({ path: shot("exact-section.png") });

    // the next profile: the line of the new plane replaces the old one
    await page.keyboard.press("]");
    await expect.poll(async () => (await state(page)).exact.sections[0]?.bbox?.[1] ?? null, { timeout: 30_000 }).toBeCloseTo(line.y0 + 50, 6);
    await waitFrame(page);
    // a hidden wireframe has no section line; shown again — it comes back; clipping off — no lines
    expect((await run("layer.show", { layer: "wireframe", id: relief.id, visible: false })).ok).toBe(true);
    await expect.poll(async () => (await state(page)).exact.sections.length).toBe(0);
    expect((await run("layer.show", { layer: "wireframe", id: relief.id, visible: true })).ok).toBe(true);
    await expect.poll(async () => (await state(page)).exact.sections.length, { timeout: 30_000 }).toBe(1);
    expect((await run("section.clip", { on: false })).ok).toBe(true);
    await expect.poll(async () => (await state(page)).exact.sections.length).toBe(0);
    await waitFrame(page);

    // ---- 8.24: marks where the holes cross the relief ----------------------------------------------------
    const sets = (await api("GET", "/drillholes/sets")).body.sets as { id: number }[];
    for (const w of others) expect((await run("layer.show", { layer: "wireframe", id: w.id, visible: true })).ok).toBe(true);
    await waitFrame(page);
    expect((await state(page)).exact.marks).toEqual([]);
    // what the holes may be marked on: the wireframes of «Каркасы» and — only when asked — the DTMs
    type Listed = { id: number; triangle_count: number; dtm: unknown };
    const listed = ((await api("GET", "/wireframes")).body.wireframes as Listed[]).filter((w) => w.triangle_count > 0);
    const solids = listed.filter((w) => w.dtm === null).map((w) => w.id).sort((a, b) => a - b);
    const everything = listed.map((w) => w.id).sort((a, b) => a - b);
    expect(everything).toContain(relief.id);
    expect(solids).not.toContain(relief.id);
    // by default — without the DTMs: no «0,000 м · Рельеф» at the collar of every hole
    expect((await run("holes.trace_style", { set_id: sets[0].id, intersections: true })).ok).toBe(true);
    const onSolids = solids.length ? ((await bfa1(`/viz/sets/${sets[0].id}/intersections?wireframes=${solids.join(",")}`, 0)).header.n as number) : 0;
    await expect.poll(async () => (await state(page)).exact.marks[0]?.marks ?? 0, { timeout: 30_000 }).toBe(onSolids);
    await waitFrame(page);
    expect((await state(page)).exact.marks.flatMap((m) => m.objects)).toEqual(onSolids ? solids : []); // not the relief
    expect((await state(page)).layers.includes(`xm:${sets[0].id}`)).toBe(onSolids > 0); // nothing to mark: no layer
    // «с ЦТМ»: the relief is crossed too
    expect((await run("holes.trace_style", { set_id: sets[0].id, withDtm: true })).ok).toBe(true);
    const found = await bfa1(`/viz/sets/${sets[0].id}/intersections?wireframes=${everything.join(",")}`, 0);
    expect(found.header.kind).toBe("intersections");
    expect(found.header.n as number).toBeGreaterThan(0);
    await expect.poll(async () => (await state(page)).exact.marks[0]?.objects ?? [], { timeout: 30_000 }).toEqual(everything);
    await expect.poll(async () => (await state(page)).exact.marks[0]?.marks ?? 0, { timeout: 30_000 }).toBe(found.header.n);
    await waitFrame(page);
    const marks = (await state(page)).exact.marks[0];
    expect(marks).toMatchObject({ set: sets[0].id, objects: everything });
    expect(marks.labels).toBe(Math.min(marks.marks, 3000));
    expect((await state(page)).layers).toContain(`xm:${sets[0].id}`);
    await page.screenshot({ path: shot("exact-marks.png") });
    // «только с видимыми объектами»: a hidden relief gives no marks; the style off — no marks
    expect((await run("layer.show", { layer: "wireframe", id: relief.id, visible: false })).ok).toBe(true);
    await expect.poll(async () => (await state(page)).exact.marks.flatMap((m) => m.objects), { timeout: 30_000 }).toEqual(onSolids ? solids : []);
    expect((await run("layer.show", { layer: "wireframe", id: relief.id, visible: true })).ok).toBe(true);
    await expect.poll(async () => (await state(page)).exact.marks[0]?.objects ?? [], { timeout: 30_000 }).toEqual(everything);
    expect((await run("holes.trace_style", { set_id: sets[0].id, intersections: false })).ok).toBe(true);
    await expect.poll(async () => (await state(page)).exact.marks.length).toBe(0);
    expect((await state(page)).layers).not.toContain(`xm:${sets[0].id}`);
  } finally {
    await close(app);
  }
});
