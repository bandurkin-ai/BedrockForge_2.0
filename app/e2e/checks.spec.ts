// Э8, подшаги 8.16, 8.22 (ADR 0013 §3, §5–§6; ТЗ 7.21 «Проверки», «Свойства») on the demo project. «Свойства» of
// the DTM «Рельеф» tell what was measured when it was stored — an open surface with the number of its open
// edges, no volume — and that the normals and the self-intersections were not checked. «Проверить» starts the
// job «Проверка и копии для показа»: the button waits while it runs, then «Свойства» show what the core found
// — the same numbers as `stats.checks` of the core — and when. The layer of crossing triangles of the core is
// empty for a relief, so no red layer joins the scene. `wireframe.check` and `model.self_intersections` of
// the registry answer in Russian. (The red layer of a wireframe that crosses itself: ui/test/viz-mesh-checks,
// core tests of `wireframe.check`; the demo has no such wireframe.)
import { expect, test, type Page } from "@playwright/test";
import { close, launch, shot, waitFrame } from "./helpers";

const state = (page: Page) => page.evaluate(() => window.__bfScene!.state());
const flat = (text: string | null) => (text ?? "").replace(/\s/g, " ");

test("wireframe checks: «Проверить» in «Свойства», the numbers of the core, the layer of crossing triangles", async () => {
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
    /** The header of the BFA1 `checks` of a wireframe and the ETag it came with. */
    const layer = (id: number, tag?: string) =>
      page.evaluate(
        async ([wf, etag]) => {
          const c = window.bf!.core()!;
          const r = await fetch(`${c.baseUrl}/api/v1/wireframes/${wf}/checks`, { headers: { "X-BF-Token": c.token, ...(etag ? { "If-None-Match": etag as string } : {}) } });
          if (r.status !== 200) return { status: r.status, etag: r.headers.get("ETag"), header: null };
          const buf = await r.arrayBuffer();
          const length = new DataView(buf).getUint32(4, true);
          return { status: r.status, etag: r.headers.get("ETag"), header: JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 8, length))) as Record<string, unknown> };
        },
        [id, tag ?? null] as const,
      );
    const run = (id: string, args: Record<string, unknown>) => page.evaluate(([i, a]) => window.__bfScene!.run(i as string, a as Record<string, unknown>), [id, args] as const);
    const ru = (n: number) => n.toLocaleString("ru-RU").replace(/\s/g, " ");

    type Checks = { state: string; normal_conflicts: number; pairs: number; triangles: number; listed: number; seconds: number; checked_at: string };
    type Wireframe = { id: number; name: string; version: number; dtm: unknown; triangle_count: number; stats: { closed: boolean; open_edges: number; volume: number | null; area: number; checks?: Checks } };
    const reliefNow = async () => ((await api("GET", "/wireframes")).body.wireframes as Wireframe[]).find((w) => w.name === "Рельеф")!;
    const relief = await reliefNow();
    expect(relief.dtm).not.toBeNull();
    expect(relief.stats.checks).toBeUndefined(); // stored with its measures; the checks are a job
    expect(relief.stats.closed).toBe(false);
    expect(relief.stats.open_edges).toBeGreaterThan(0);
    expect((await layer(relief.id)).header).toMatchObject({ kind: "checks", state: "none", triangles: 0 });

    // ---- «Свойства» before the check: the measures of the stored mesh, nothing invented -----------------------
    const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
    await tree.getByTestId(`tree-dtm-${relief.id}`).getByText("Рельеф", { exact: true }).click();
    const checks = page.getByLabel("Правая панель").getByTestId("props-dtm").getByTestId("props-checks");
    await expect(checks).toHaveAttribute("data-state", "none");
    let text = flat(await checks.textContent());
    expect(text).toContain("Замкнутостьне замкнут");
    expect(text).toContain(`Открытых рёбер${ru(relief.stats.open_edges)}`); // an open wireframe shows its open edges (7.21)
    expect(text).toContain("Объёмнет: каркас не замкнут"); // never 0
    expect(text).toContain("Нормалине проверены");
    expect(text).toContain("Самопересеченияне проверены");
    expect(text).toContain("Провереноне проверялся");
    const button = checks.getByRole("button", { name: "Проверить" });
    await expect(button).toBeEnabled();

    // ---- «Проверить»: the job, then the numbers of the core ---------------------------------------------------
    await button.click();
    await expect(checks).toHaveAttribute("data-state", "done", { timeout: 240_000 });
    await expect(button).toBeEnabled();
    const checked = await reliefNow();
    const found = checked.stats.checks!;
    expect(found).toMatchObject({ state: "done", normal_conflicts: 0, pairs: 0, triangles: 0, listed: 0 }); // a relief is a height field
    expect(checked.version).toBe(relief.version); // derived data: an edit in «Свойства» is not refused for it
    text = flat(await checks.textContent());
    expect(text).toContain("Нормалисогласованы");
    expect(text).toContain("Самопересечениянет");
    expect(text).toMatch(/Проверено\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}, за /);
    expect(text).not.toContain("не проверены");
    await expect(checks).toHaveAttribute("data-pairs", "0");
    const jobs = (await api("GET", "/jobs")).body.jobs as { kind: string; status: string; title: string; params: { wireframe_ids: number[] } }[];
    const job = jobs.find((j) => j.kind === "wireframe.check")!;
    expect(job).toMatchObject({ status: "done", title: "Проверка каркаса «Рельеф»", params: { wireframe_ids: [relief.id] } });

    // ---- the layer of crossing triangles: none for a relief, answered with an ETag ----------------------------
    const empty = await layer(relief.id);
    expect(empty.header).toMatchObject({ kind: "checks", state: "done", triangles: 0, crossing: 0, groups: [] });
    expect((await layer(relief.id, empty.etag!)).status).toBe(304);
    await waitFrame(page);
    const scene = await state(page);
    expect(scene.checks).toEqual([]);
    expect(scene.layers.filter((k) => k.startsWith("chk:"))).toEqual([]);
    expect(scene.wireframes.layers).toBeGreaterThanOrEqual(1); // the surface itself is where it was
    await page.screenshot({ path: shot("checks-props.png") });

    // ---- the commands of the registry -----------------------------------------------------------------------
    const told = await run("model.self_intersections", { wireframe_id: relief.id });
    expect(told).toEqual({ ok: true, result: "«Рельеф»: нормали согласованы, самопересечений нет" });
    const again = await run("wireframe.check", { wireframe_id: relief.id });
    expect(again.ok).toBe(true);
    expect(again.result).toContain("Проверка «Рельеф» запущена: задание №");
    await expect
      .poll(async () => ((await api("GET", "/jobs")).body.jobs as { kind: string; status: string }[]).filter((j) => j.kind === "wireframe.check").map((j) => j.status), { timeout: 240_000 })
      .toEqual(["done", "done"]);
    const second = (await reliefNow()).stats.checks!;
    expect(second.checked_at > found.checked_at).toBe(true);
    expect((await layer(relief.id, empty.etag!)).status).toBe(304); // the same result: the window keeps what it has
    expect((await run("wireframe.check", { wireframe_id: 999_999 })).result).toBe("Каркас №999999 не найден: обновите дерево проекта");
  } finally {
    await close(app);
  }
});
