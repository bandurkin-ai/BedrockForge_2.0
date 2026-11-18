// Э8, подшаг 8.20 (ADR 0013 §5–§6; ТЗ 7.21, 9.4): «Главная › Загрузка › Каркасы» opens the composition of «Импорт
// файлов» — the real Micromine TRIDB «ORE SEP 2025» (66 wireframes) and a DXF that holds a line and a wireframe:
// the drawing shows both its layers and its wireframes. The import puts the wireframes into «Каркасы» of the data
// tree and into the 3D window, the line file into «Линии»; the view moves to the TRIDB, which lies far from the
// demo (466 000 / 4 584 000) and was not seen — «показать загруженное»; then every stored wireframe is checked by
// itself — «Свойства › Проверки» of a wireframe are filled without «Проверить».
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { close, holesOnlyDemo, launch, shot, waitFrame } from "./helpers";

const REAL = path.resolve(__dirname, "..", "..", "tests", "data", "real");
const TRIDB_WIREFRAMES = 66;
const TRIDB_TRIANGLES = 3590;

const state = (page: Page) => page.evaluate(() => window.__bfScene!.state());
const flat = (text: string | null) => (text ?? "").replace(/\s/g, " ");

const dxf = (entities: (string | number)[][]) => ["0", "SECTION", "2", "ENTITIES", ...entities.flat().map(String), "0", "ENDSEC", "0", "EOF", ""].join("\r\n");

/** A road on «ROADS» and a closed tetrahedron of four 3DFACE on «ORE» near the demo holes. */
function drawing(file: string): void {
  const p = [
    [5100, 8100, 200],
    [5200, 8100, 200],
    [5150, 8200, 200],
    [5150, 8133, 280],
  ];
  const face = (a: number, b: number, c: number) => [0, "3DFACE", 8, "ORE", ...[a, b, c, c].flatMap((k, j) => [10 + j, p[k][0], 20 + j, p[k][1], 30 + j, p[k][2]])];
  fs.writeFileSync(file, dxf([[0, "LINE", 8, "ROADS", 10, 4960, 20, 7900, 30, 320, 11, 5440, 21, 7900, 31, 330], face(0, 2, 1), face(0, 1, 3), face(1, 2, 3), face(2, 0, 3)]));
}

test("wireframes: «Каркасы» — a real TRIDB and a DXF with lines and a wireframe, tree, 3D, checks after the import", async () => {
  test.setTimeout(480_000);
  const { app, page } = await launch({ BF_E2E: "1" });
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-tridb-"));
  const pickNext = (files: string[]) =>
    app.evaluate(({ dialog }, f) => {
      dialog.showOpenDialog = (async () => ({ canceled: false, filePaths: f })) as typeof dialog.showOpenDialog;
    }, files);
  const api = (method: string, url: string, body?: unknown) =>
    page.evaluate(
      async ([m, u, b]) => {
        const c = window.bf!.core()!;
        const r = await fetch(`${c.baseUrl}/api/v1${u}`, { method: m as string, headers: { "X-BF-Token": c.token, "Content-Type": "application/json" }, body: b === null ? undefined : JSON.stringify(b) });
        return { status: r.status, body: (await r.json()) as Record<string, unknown> };
      },
      [method, url, body ?? null] as const,
    );
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    await waitFrame(page);
    await holesOnlyDemo(page);

    // ---- the composition: a TRIDB of wireframes; a drawing with a layer of lines and a wireframe ---------------
    const tridb = path.join(dir, "ORE SEP 2025.tridb");
    fs.copyFileSync(path.join(REAL, "ORE SEP 2025.tridb"), tridb);
    const mixed = path.join(dir, "mixed.dxf");
    drawing(mixed);
    await pickNext([tridb, mixed]);
    await page.getByRole("navigation", { name: "Лента" }).getByRole("button", { name: "Каркасы", exact: true }).click();
    const win = page.getByRole("dialog", { name: "Импорт файлов" });
    const totals = win.getByTestId("objects-totals");
    await expect(totals).toContainText("Файлов: 2", { timeout: 60_000 });
    const ru = (n: number) => n.toLocaleString("ru-RU").replace(/\s/g, " ");
    expect(flat(await totals.textContent())).toBe(`Файлов: 2 · слоёв: 1 · линий: 1 · вершин: 2 · каркасов: ${TRIDB_WIREFRAMES + 1} · треугольников: ${ru(TRIDB_TRIANGLES + 4)}`);
    const tridbCard = win.getByTestId("objects-file").filter({ hasText: "ORE SEP 2025.tridb" });
    await expect(tridbCard).toContainText("Каркасы Micromine (.tridb)");
    expect(flat(await tridbCard.textContent())).toContain(`каркасов: ${TRIDB_WIREFRAMES} · треугольников: ${ru(TRIDB_TRIANGLES)}`);
    await expect(tridbCard.getByTestId("objects-wireframes")).toContainText("Body_1");
    await expect(tridbCard.getByRole("combobox", { name: /Кодировка/ })).toHaveCount(0); // a binary file
    await tridbCard.getByRole("button", { name: `Все каркасы (${TRIDB_WIREFRAMES})` }).click();
    await expect(tridbCard.getByTestId("objects-wireframes").locator("tbody tr")).toHaveCount(TRIDB_WIREFRAMES);
    const dxfCard = win.getByTestId("objects-file").filter({ hasText: "mixed.dxf" });
    await expect(dxfCard.getByRole("table", { name: "Слои mixed.dxf" })).toContainText("ROADS");
    await expect(dxfCard.getByRole("table", { name: "Каркасы mixed.dxf" })).toContainText("ORE");
    expect(flat(await dxfCard.textContent())).toContain("слоёв: 1 · линий: 1 · вершин: 2 · каркасов: 1 · треугольников: 4");
    await page.screenshot({ path: shot("wireframes-wizard.png") });
    await win.getByRole("button", { name: "Импортировать" }).click();
    await expect(win).toHaveCount(0);

    // ---- «Каркасы» and «Линии» of the tree, the 3D window -----------------------------------------------------
    type Wireframe = { id: number; name: string; extent: number[]; stats: { checks?: { state: string; pairs: number } } };
    let wireframes: Wireframe[] = [];
    await expect
      .poll(
        async () => {
          wireframes = (await api("GET", "/wireframes")).body.wireframes as Wireframe[];
          return wireframes.length;
        },
        { timeout: 120_000 },
      )
      .toBe(TRIDB_WIREFRAMES + 1);
    const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
    const body = wireframes.find((w) => w.name === "Body_1")!;
    const ore = wireframes.find((w) => w.name === "ORE")!;
    expect(body).toBeDefined();
    expect(ore).toBeDefined();
    await expect(tree.getByTestId(`tree-wireframe-${body.id}`)).toBeVisible({ timeout: 60_000 });
    await expect(tree.getByTestId(`tree-wireframe-${ore.id}`)).toBeVisible();
    await expect(tree.getByRole("treeitem", { name: "mixed.dxf, 1 слой" })).toBeVisible();
    await expect.poll(async () => (await state(page)).wireframes.layers, { timeout: 60_000 }).toBe(TRIDB_WIREFRAMES + 1);

    // ---- «показать загруженное»: the view moved to the TRIDB, far from the demo; the drawing near the holes was seen
    const far = wireframes.filter((w) => w.id !== ore.id).map((w) => w.extent);
    const lo = [0, 1, 2].map((k) => Math.min(...far.map((e) => e[k])));
    const hi = [3, 4, 5].map((k) => Math.max(...far.map((e) => e[k])));
    expect(lo[0]).toBeGreaterThan(400_000); // 461 km east of the holes of the demo
    await waitFrame(page);
    const host = (await page.getByTestId("scene-host").boundingBox())!;
    const onScreen = (p: number[]) => page.evaluate(([x, y, z]) => window.__bfScene!.screen(x, y, z), p as [number, number, number]);
    const mid = (await onScreen([0, 1, 2].map((k) => (lo[k] + hi[k]) / 2)))!;
    expect(mid.x).toBeGreaterThan(host.x + 0.25 * host.width);
    expect(mid.x).toBeLessThan(host.x + 0.75 * host.width);
    expect(mid.y).toBeGreaterThan(host.y + 0.25 * host.height);
    expect(mid.y).toBeLessThan(host.y + 0.75 * host.height);
    const [a, b] = [(await onScreen(lo))!, (await onScreen(hi))!];
    expect(Math.hypot(b.x - a.x, b.y - a.y)).toBeGreaterThan(0.4 * Math.min(host.width, host.height)); // not a dot
    expect((await page.evaluate(() => window.__bfScene!.pixels()))!.nonBackground).toBeGreaterThan(5000); // not an empty window

    // ---- the checks of every imported wireframe, started by the interface once the import ended ---------------
    type CheckJob = { kind: string; status: string; params: { wireframe_ids: number[] } };
    await expect
      .poll(
        async () => {
          const jobs = ((await api("GET", "/jobs")).body.jobs as CheckJob[]).filter((j) => j.kind === "wireframe.check");
          const done = jobs.filter((j) => j.status === "done").flatMap((j) => j.params.wireframe_ids);
          return new Set(done).size;
        },
        { timeout: 300_000, intervals: [1000] },
      )
      .toBe(TRIDB_WIREFRAMES + 1);
    const checked = (await api("GET", "/wireframes")).body.wireframes as Wireframe[];
    expect(checked.filter((w) => w.stats.checks?.state === "done")).toHaveLength(TRIDB_WIREFRAMES + 1);

    await tree.getByTestId(`tree-wireframe-${body.id}`).getByText("Body_1", { exact: true }).dblclick(); // «Свойства» of the wireframe
    const checks = page.getByLabel("Правая панель").getByTestId("props-wireframe").getByTestId("props-checks");
    await expect(checks).toHaveAttribute("data-state", "done", { timeout: 60_000 });
    const text = flat(await checks.textContent());
    expect(text).not.toContain("не проверены");
    expect(text).toMatch(/Проверено\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}, за /);
    await waitFrame(page);
    await page.screenshot({ path: shot("wireframes-imported.png") });
  } finally {
    await close(app);
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
