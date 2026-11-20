// Э7, подшаг 7.29 (ADR 0011 §5; ТЗ 7.35 Д-16) under software WebGL2: the demo project has a trench — «К-01» of
// «Скважины 2025», a hole whose stations carry coordinates — and the 3D window draws it where its first vertex
// is. «Импорт канав…» makes a new set from the 3D polylines of a DXF (a file name with Cyrillic letters and a
// space, no samples): its trenches are in the tree, in the core with a station at every vertex, and in the 3D
// window; a click on one opens its card.
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { close, launch, shot, waitFrame } from "./helpers";

const state = (page: Page) => page.evaluate(() => window.__bfScene!.state());

interface Row {
  id: number;
  hole: string;
  x: number;
  y: number;
  z: number;
  td: number;
}
interface Card {
  type: { title: string };
  surveys: number;
  tables: Record<string, { name: string; rows: number }>;
  trajectory: { length: number | null; end: number[] | null };
}

/** North of the demo holes (x 5000…5350, y 8000…8250): two trenches, vertices with their own heights. */
const TRENCHES: Record<string, [number, number, number][]> = {
  "TR-101": [
    [5020.5, 8290.25, 421.5],
    [5052, 8293, 422.25],
    [5086, 8291.5, 423],
    [5120, 8296, 423.75],
  ],
  "TR-102": [
    [5200, 8292, 420],
    [5243.5, 8297, 421],
    [5287, 8294.5, 422.5],
    [5330, 8300, 424],
  ],
};

/** A DXF of 3D polylines (POLYLINE with VERTEX records), one layer a trench: the layer names the trench. */
function dxf(): string {
  const out: (string | number)[] = [0, "SECTION", 2, "ENTITIES"];
  for (const [name, line] of Object.entries(TRENCHES)) {
    out.push(0, "POLYLINE", 8, name, 66, 1, 70, 8);
    for (const [x, y, z] of line) out.push(0, "VERTEX", 8, name, 10, x, 20, y, 30, z, 70, 32);
    out.push(0, "SEQEND", 8, name);
  }
  out.push(0, "ENDSEC", 0, "EOF", "");
  return out.map(String).join("\r\n");
}

test("trenches: the demo trench in 3D; «Импорт канав…» from a DXF → a set, stations at the vertices, 3D, card", async () => {
  test.setTimeout(300_000);
  const { app, page } = await launch({ BF_E2E: "1" });
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-канавы-"));
  const pickNext = (files: string[]) =>
    app.evaluate(({ dialog }, f) => {
      dialog.showOpenDialog = (async () => ({ canceled: false, filePaths: f })) as typeof dialog.showOpenDialog;
    }, files);
  const api = (url: string) =>
    page.evaluate(async (u) => {
      const c = window.bf!.core()!;
      const r = await fetch(`${c.baseUrl}/api/v1${u}`, { headers: { "X-BF-Token": c.token } });
      return (await r.json()) as Record<string, unknown>;
    }, url);
  const collars = () => page.evaluate(() => window.__bfScene!.collars(2000));
  const screen = (p: [number, number, number]) => page.evaluate(([x, y, z]) => window.__bfScene!.screen(x, y, z), p);
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    await waitFrame(page);

    // ---- the trench of the demo ---------------------------------------------------------------------------
    const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
    await expect(tree.getByRole("treeitem", { name: "Скважины 2025, 49 скв." })).toBeVisible();
    const sets = (await api("/drillholes/sets")).sets as { id: number; name: string; holes: number }[];
    expect(sets.map((s) => [s.name, s.holes])).toEqual([["Скважины 2025", 49]]);
    // the first view holds the whole demo, whichever of its layers came first: every collar is on the screen
    const seen = await collars();
    expect(seen).toHaveLength(49);
    const demo = seen.find((c) => c.name === "К-01");
    expect(demo, "the demo trench is drawn").toBeTruthy();
    const own = (await api(`/drillholes/sets/${sets[0].id}/tables/collar?limit=100`)).rows as Row[];
    const head = own.find((r) => r.hole === "К-01")!;
    const at = await screen([head.x, head.y, head.z]);
    expect(Math.hypot(at!.x - demo!.x, at!.y - demo!.y)).toBeLessThan(1.5); // drawn at its collar — the first vertex
    const card = (await api(`/drillholes/sets/${sets[0].id}/holes/${head.id}`)) as unknown as Card;
    expect(card.type.title).toBe("Канава");
    expect(card.surveys).toBe(9); // a station at every vertex of the polyline
    expect(Math.abs(card.trajectory.length! - head.td)).toBeLessThan(1e-6); // «от/до» run along the polyline
    // 2 m samples (the tables of «Обработка проб» of the demo, Э12, hold the trench too)
    const processed = ["Композиты 2 м", "Рудные интервалы"];
    expect(Object.values(card.tables).filter((t) => !processed.includes(t.name)).map((t) => [t.name, t.rows > 100])).toEqual([["Пробы", true]]);
    const first = await state(page);
    expect(first.sets).toEqual([sets[0].id]);

    // ---- «Импорт канав…»: a DXF of 3D polylines → a new set ---------------------------------------------
    const file = path.join(dir, "Канавы 2026.dxf");
    fs.writeFileSync(file, dxf(), "ascii");
    await tree.getByTestId("tree-section-holes").getByRole("button", { name: "Новый набор, импорт скважин, импорт канав" }).click();
    await page.getByRole("menuitem", { name: "Импорт канав…" }).click();
    const wiz = page.getByRole("dialog", { name: "Импорт канав" });
    await expect(wiz.getByRole("button", { name: /^1\. Канавы по полилиниям/ })).toBeVisible();
    await pickNext([file]);
    await wiz.getByRole("button", { name: "Выбрать файл…" }).click();
    const preview = wiz.getByTestId("trench-preview");
    await expect(preview).toContainText("Канав: 2 · вершин: 8", { timeout: 60_000 });
    await expect(preview.getByRole("row").filter({ hasText: "TR-101" })).toBeVisible();
    await expect(preview.getByRole("row").filter({ hasText: "TR-102" })).toBeVisible();
    await wiz.getByRole("button", { name: "Далее" }).click(); // no table of samples
    await wiz.getByRole("button", { name: "Далее" }).click();
    await expect(wiz.getByTestId("import-summary")).toContainText("Канавы 2026.dxf: Канав: 2 · вершин: 8");
    await wiz.getByLabel("Название нового набора").fill("Канавы 2026");
    await page.screenshot({ path: shot("trenches-dxf-wizard.png") });
    await wiz.getByRole("button", { name: "Импортировать" }).click();
    await expect(wiz).toHaveCount(0);
    await expect(tree.getByRole("treeitem", { name: "Канавы 2026, 2 скв." })).toBeVisible({ timeout: 90_000 });

    // ---- in the core: collars at the first vertices, a station with coordinates at every vertex ----------
    const after = (await api("/drillholes/sets")).sets as { id: number; name: string; holes: number }[];
    const made = after.find((s) => s.name === "Канавы 2026")!;
    expect(made.holes).toBe(2);
    const rows = (await api(`/drillholes/sets/${made.id}/tables/collar?limit=100`)).rows as Row[];
    for (const [name, line] of Object.entries(TRENCHES)) {
      const row = rows.find((r) => r.hole === name)!;
      expect([row.x, row.y, row.z]).toEqual(line[0]); // float64 as written in the file
      let length = 0;
      for (let i = 1; i < line.length; i++) length += Math.hypot(line[i][0] - line[i - 1][0], line[i][1] - line[i - 1][1], line[i][2] - line[i - 1][2]);
      expect(Math.abs(row.td - length)).toBeLessThan(1e-9); // the depth of the trench is its 3D length
      const made_card = (await api(`/drillholes/sets/${made.id}/holes/${row.id}`)) as unknown as Card;
      expect(made_card.type.title).toBe("Канава");
      expect(made_card.surveys).toBe(line.length);
      const end = made_card.trajectory.end!;
      for (let k = 0; k < 3; k++) expect(Math.abs(end[k] - line[line.length - 1][k])).toBeLessThan(1e-6); // ends at the last vertex
    }

    // ---- in the 3D window --------------------------------------------------------------------------------
    await waitFrame(page);
    await expect.poll(async () => (await state(page)).sets.length, { timeout: 60_000 }).toBe(2);
    await page.keyboard.press("f"); // fit: the new set is in the view
    await waitFrame(page);
    const shown = await collars();
    for (const [name, line] of Object.entries(TRENCHES)) {
      const c = shown.find((x) => x.name === name);
      expect(c, `${name} is drawn`).toBeTruthy();
      expect(c!.setId).toBe(made.id);
      const p = await screen(line[0]);
      expect(Math.hypot(p!.x - c!.x, p!.y - c!.y)).toBeLessThan(1.5);
    }
    expect((await state(page)).segments).toBeGreaterThan(first.segments); // their traces are drawn
    await page.screenshot({ path: shot("trenches-3d.png") });

    // a click on the collar of a trench: its card
    await page.keyboard.press("t"); // from above nothing stands before the collars
    await waitFrame(page);
    const top = (await collars()).find((x) => x.name === "TR-102")!;
    await page.mouse.click(top.x, top.y);
    await expect(page.getByRole("contentinfo")).toContainText("Выбрана: TR-102");
    await expect(page.getByLabel("Скважина TR-102")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByLabel("Скважина TR-102")).toContainText("Канава");
    await page.screenshot({ path: shot("trenches-card.png") });
  } finally {
    await close(app);
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
