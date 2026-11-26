// The helpers of the E2E of the pit optimisation (Э21, 21.20): the constants and the shapes of the answers of the core, the checks (the numbers of the screen are
// compared with the answers of the core, NPV with the formula of ТЗ 8.11.4), the windows and Ctrl+K, the start of an app with the synthetic world, a scenario made
// through the commands of the registry (as the assistant makes it). Shared by `pit.spec.ts` and `pitCompare.spec.ts` (one spec = one app, one project).
import { expect, type ElectronApplication, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { coreApi, launch, runCommand, setWindowSize, waitFrame } from "./helpers";
import { lastJob, lastJobId } from "./estimationWorld";
import { build } from "./pitWorld";

export const EXE = !!process.env.BF_APP_EXE;
export const LIMIT = (ms: number) => (EXE ? 1.5 * ms : ms);
export const NBSP = " ";

export const NAME_A = "Карьер A";
export const NAME_B = "Карьер B";
export const RF_A = [0.5, 0.6, 0.7, 0.8, 0.9, 1, 1.1, 1.2];
export const RF_B = [0.7, 0.8, 0.9, 1, 1.1, 1.2, 1.3];
export const ROCKS = JSON.stringify([{ name: "Все породы", c_p: 20, rec: { AU: 0.9 } }]);
export const CALENDAR = { q_ore: 100_000, q_mass: 250_000, rate: 0.08 };

export type Ids = { model: number; terrain: number };
export type Cum = { blocks: number; ore_t: number; waste_t: number; strip: number | null; cf: number; revenue: number };
export type Shell = { k: number; rf: number; blocks: number; ore_t: number; waste_t: number; cf: number; cum: Cum; best: { npv: number | null }; worst: { npv: number | null } };
export type Report = {
  scenario_id: number;
  npv_ready: boolean;
  calendar: { q_ore: number | null; q_mass: number | null; rate: number | null };
  shells: Shell[];
  totals: Cum;
  series: { k: number[]; rf: number[]; ore_t: number[]; waste_t: number[]; cf: number[]; npv_best: (number | null)[]; npv_worst: (number | null)[] };
  table: { columns: { key: string; decimals: number }[]; rows: { k: number; cells: Record<string, { value: number | null; text: string | null }> }[] };
};
export type Scenario = {
  id: number;
  name: string;
  version: number;
  state: string;
  has_result: boolean;
  params_changed: boolean;
  params: { prefix: string; rf: number[]; calendar: { q_ore: number | null; q_mass: number | null; rate: number } };
  meta: { shells: { k: number; rf: number; blocks: number; ore_t: number; waste_t: number; value: number }[]; outputs: { fields: string[] }; control: { closed: boolean; nested: boolean }; template: { k: number } };
};
export type Side = { blocks: number | null; ore_t: number | null; waste_t: number | null; cf: number | null; npv_best: number | null; npv_worst: number | null };
export type Compared = {
  same_grid: boolean;
  params_diff: { param: string; a: unknown; b: unknown }[];
  shells: { rf: number; status: string; k_a: number | null; k_b: number | null; a: Side | null; b: Side | null; diff: Side | null }[];
  blocks: { only_a: { blocks: number; tonnes: number }; only_b: { blocks: number; tonnes: number } } | null;
};

export const scenario = async (page: Page, id: number) => (await coreApi<Scenario>(page, "GET", `/pit/scenarios/${id}`)).body;
export const report = async (page: Page, id: number, calendar?: unknown) => (await coreApi<Report>(page, "GET", `/pit/scenarios/${id}/report${calendar ? `?calendar=${encodeURIComponent(JSON.stringify(calendar))}` : ""}`)).body;
export const scenarios = async (page: Page) => (await coreApi<{ scenarios: { id: number; name: string }[] }>(page, "GET", "/pit/scenarios")).body.scenarios;
export const near = (a: number, b: number, tol = 1e-9) => expect(Math.abs(a - b), `${a} ≈ ${b}`).toBeLessThanOrEqual(tol * Math.max(1, Math.abs(a), Math.abs(b)));
/** The number as the interface prints it (ru-RU: the decimal comma, no-break spaces between the thousands, as `formatNumber` does). */
export const ru = (v: number, decimals: number) => v.toFixed(decimals).replace(/\B(?=(\d{3})+(?!\d))/g, NBSP).replace(".", ",");

/** NPV of ТЗ 8.11.4 by its formula (the best case: the shells ΔC_1…ΔC_n in the order of the pit): τ_i, t_i, the discounted CF. */
export function npv(parts: { ore: number; waste: number; cf: number }[], cal: { q_ore: number; q_mass: number | null; rate: number }): number {
  let t = 0;
  let sum = 0;
  for (const p of parts) {
    const tau = Math.max(p.ore / cal.q_ore, cal.q_mass === null ? 0 : (p.ore + p.waste) / cal.q_mass);
    if (cal.rate === 0) sum += p.cf;
    else if (tau === 0) sum += p.cf * (1 + cal.rate) ** -t;
    else sum += (p.cf * ((1 + cal.rate) ** -t - (1 + cal.rate) ** -(t + tau))) / (tau * Math.log(1 + cal.rate));
    t += tau;
  }
  return sum;
}

/** The bottom dock higher by `by` px (the splitter «Высота нижней панели» dragged up, as a hand does it). */
export async function raiseDock(page: Page, by: number): Promise<void> {
  const b = (await page.getByRole("separator", { name: "Высота нижней панели" }).boundingBox())!;
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2 - by, { steps: 6 });
  await page.mouse.up();
}

/** Every floating window closed by the button «Закрыть» of its bar. */
export async function closeWindows(page: Page): Promise<void> {
  const dialogs = page.getByRole("dialog");
  for (let n = 0; n < 12 && (await dialogs.count()) > 0; n++) {
    await dialogs.last().getByRole("button", { name: "Закрыть", exact: true }).first().click();
    await page.waitForTimeout(50);
  }
  await expect(dialogs).toHaveCount(0);
}

/** Ctrl+K for a query → the option with that title: whether it is unavailable and its hint (the reason of the registry); the palette is closed. */
export async function option(page: Page, query: string, title: RegExp): Promise<{ disabled: boolean; hint: string }> {
  await page.keyboard.press("Control+KeyK");
  await page.getByLabel("Поиск команды").fill(query);
  const opt = page.getByLabel("Палитра команд").getByRole("option").filter({ hasText: title }).first();
  await expect(opt, `Ctrl+K «${query}»`).toBeVisible({ timeout: 10_000 });
  const out = { disabled: (await opt.getAttribute("aria-disabled")) === "true", hint: (await opt.getAttribute("title")) ?? "" };
  await page.keyboard.press("Escape");
  await expect(page.getByLabel("Палитра команд")).toHaveCount(0);
  return out;
}

/** Ctrl+K → the option with that title is clicked (the first option of a query is not always the command asked for: «Сравнить» is a command of Э20 too). */
export async function choose(page: Page, query: string, title: RegExp): Promise<void> {
  await page.keyboard.press("Control+KeyK");
  await page.getByLabel("Поиск команды").fill(query);
  const opt = page.getByLabel("Палитра команд").getByRole("option").filter({ hasText: title }).first();
  await expect(opt, `Ctrl+K «${query}»`).toBeVisible({ timeout: 10_000 });
  await expect(opt).not.toHaveAttribute("aria-disabled", "true");
  await opt.click();
  await expect(page.getByLabel("Палитра команд")).toHaveCount(0);
}

export type Check = { k: number; k_auto: boolean; tolerance: number; domains: { n_offsets: number; error: { max: number; mean: number }; warning: boolean }[]; warnings: { code: string }[] };

/** The job `pit.check` of the core for the saved parameters of a scenario → its result (to compare with the window). */
export async function coreCheck(page: Page, id: number): Promise<Check> {
  const params = (await scenario(page, id)).params;
  const before = await lastJobId(page, "pit.check");
  const started = await coreApi<{ source: string; id: number }>(page, "POST", "/pit/check", { params });
  expect(started.status, JSON.stringify(started.body)).toBe(200);
  return (await lastJob(page, "pit.check", LIMIT(60_000), before)).result as never;
}

/**
 * A command of the registry as the assistant runs it. `pit.run` is unavailable while the scenario is read again after a change («Сценарий загружается»): the
 * command is repeated while it says so (the button of the ribbon is disabled the same way), any other word of the registry is the answer.
 */
export async function say(page: Page, id: string, args: Record<string, unknown>): Promise<{ ok: boolean; result: string }> {
  let last = { ok: false, result: "" };
  await expect
    .poll(
      async () => {
        last = await runCommand(page, id, args);
        return last.ok || !/загружается/.test(last.result);
      },
      { timeout: LIMIT(60_000), intervals: [100, 200, 400] },
    )
    .toBe(true);
  return last;
}

/** A scenario made through the commands: new → economics → rocks → angles → calendar (→ run). → its number. */
export async function makeScenario(page: Page, ids: Ids, o: { name: string; price: string; rf: string; prefix?: string; run?: boolean }): Promise<number> {
  const ok = async (id: string, args: Record<string, unknown>) => {
    const r = await say(page, id, args);
    expect(r.ok, `${id}: ${r.result}`).toBe(true);
  };
  await ok("pit.new", { name: o.name, model_id: ids.model, terrain_id: ids.terrain });
  const id = (await scenarios(page)).find((s) => s.name === o.name)!.id;
  await ok("pit.economics", { scenario_id: id, elements: `AU:g/t:${o.price}:5:0,02`, density: "DENS", c_m0: 2.5, c_gl: 0.05, rf: o.rf, ...(o.prefix ? { prefix: o.prefix } : {}) });
  await ok("pit.rocks", { scenario_id: id, rock_types: ROCKS });
  await ok("pit.slopes", { scenario_id: id, table: "45" });
  await ok("pit.calendar", { scenario_id: id, q_ore: CALENDAR.q_ore, q_mass: CALENDAR.q_mass, rate_pct: 8 });
  if (o.run) await ok("pit.run", { scenario_id: id });
  return id;
}

export interface Boot {
  app: ElectronApplication;
  page: Page;
  dir: string;
  profile: string;
  ids: Ids;
}

/** An app with the window of 1024 × 640 (the ribbon folded), the project «Карьер Э21» and the synthetic world (the model, the relief) built through the core. */
export async function boot(): Promise<Boot> {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-pit-"));
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-pit-profile-"));
  const { app, page } = await launch({ BF_E2E: "1" }, profile);
  await setWindowSize(app, 1024, 640); // as small as on the Windows runner
  page.on("pageerror", (e) => console.log(`PAGEERROR ${e.message}\n${e.stack}`));
  await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
  await page.getByLabel("Лента").getByRole("button", { name: "Новый проект" }).click();
  await page.getByRole("dialog").last().getByRole("textbox").fill("Карьер Э21");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("project-selector")).toContainText("Карьер Э21", { timeout: 60_000 });
  await waitFrame(page);
  expect((await runCommand(page, "ribbon.toggle")).ok).toBe(true);
  const t = Date.now();
  const ids = await build(page, dir);
  const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
  await expect(tree.getByTestId(`tree-block-${ids.model}`)).toBeAttached({ timeout: 30_000 });
  await expect(tree.getByTestId(`tree-dtm-${ids.terrain}`)).toBeAttached({ timeout: 30_000 });
  console.log(`pit: the model and the relief are built in ${((Date.now() - t) / 1000).toFixed(1)} s`);
  return { app, page, dir, profile, ids };
}
