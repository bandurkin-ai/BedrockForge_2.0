// The check of the template of the pit (Э21, 21.17; ТЗ 8.11.2, 8.11.5; ADR 0028 решения 4, 9–12, 29; docs/progress/e21-api.md §4, §13): the window «Проверка шаблона» is drawn from the answer of
// `pit.check` (`check_result` of `core/tests/data/pit/contract_answers.json`: the tests read the form, not the numbers): K,
// the offsets, the angle error (max, mean), the warning «увеличьте K», the table of the sectors and the chart of φ_эфф(az) at 360 azimuths; a frame of the chart is drawn in 16 ms.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { windowStore } from "../../src/shell/windows";
import { jobsStore } from "../../src/modules/jobs/state";
import { seriesColor } from "../../src/charts/core/palette";
import { openCheckWindow, renderPitWindow, windowOfCheck } from "../../src/modules/pit/bind";
import { domainTitle, exceeds, nearestAz, phiAt, phiCsv, phiRange, readCheck, sectorRows, type CheckResult } from "../../src/modules/pit/checkModel";
import { CheckWindow, summaryLine } from "../../src/modules/pit/CheckWindow";
import { PhiChart } from "../../src/modules/pit/PhiChart";
import { IDLE_CHECK, PIT_CHECK_WINDOW, pitStore, selectScenario } from "../../src/modules/pit/state";
import { answer, caseParams, ID, installCore, newApp, teardown, waitFor, type Core } from "./harness";

const SAMPLE = answer("check_result").body as Record<string, unknown>;
const real = (): CheckResult => readCheck(SAMPLE)!;

/** The sample with a second domain and a warning of the angle error. */
const withWarning = (): Record<string, unknown> => {
  const d0 = (SAMPLE.domains as Record<string, unknown>[])[0];
  const d1 = { ...d0, label: "B", table: [{ az: 0, angle: 30 }, { az: 180, angle: 60 }], error: { max: 3.1, mean: 1.2 }, warning: true, phi_eff: { az: (d0.phi_eff as { az: number[] }).az, angle: (d0.phi_eff as { angle: number[] }).angle.map((v) => v + 5) } };
  return { ...SAMPLE, domains: [{ ...d0, label: "A" }, d1], warnings: [{ code: "angle_error", param: "slopes", message: "Ошибка угла 3,10° больше допуска 2,5°.", hint: "Увеличьте K." }] };
};

describe("the answer of the check: the forms of the contract", () => {
  it("K, the levels, the tolerance, the domain with its table, the offsets, the error and φ_эфф at 360 azimuths", () => {
    const r = real();
    expect(r).toMatchObject({ k: 8, kAuto: true, tolerance: 2.5, levels: 8, warnings: [], model: { id: ID.model, name: "Рудное тело" } });
    expect(r.domains).toHaveLength(1);
    const d = r.domains[0];
    expect(d).toMatchObject({ label: null, offsets: 17, errorMax: 1.44, errorMean: 0.58, warning: false });
    expect(d.table).toEqual([{ az: 0, angle: 45 }]);
    expect(d.az).toHaveLength(360);
    expect(d.phi).toHaveLength(360);
    expect(d.az[0]).toBe(0);
    expect(d.az[359]).toBe(359);
    expect(domainTitle(d)).toBe("Все блоки");
  });
  it("a gap is `null` (NaN of the core), never 0; the count of offsets is told by the list when `n_offsets` is missing; an answer that is not a check is nothing", () => {
    const d0 = (SAMPLE.domains as Record<string, unknown>[])[0];
    const r = readCheck({ ...SAMPLE, domains: [{ ...d0, n_offsets: undefined, phi_eff: { az: [0, 1, 2], angle: [44, null, 46] } }] })!;
    expect(r.domains[0].phi).toEqual([44, null, 46]);
    expect(r.domains[0].offsets).toBe((d0.offsets as unknown[]).length);
    expect(readCheck({})).toBeNull();
    expect(readCheck(null)).toBeNull();
    expect(readCheck({ domains: [{}] })!.domains[0]).toMatchObject({ label: null, table: [], offsets: null, errorMax: null, phi: [] });
  });
  it("the range of the axis holds the line and the table; the azimuth is nearest on the circle (359° and 0° are neighbours); φ_эфф at a sector", () => {
    const r = real();
    const range = phiRange(r.domains)!;
    const all = r.domains[0].phi.filter((v): v is number => v !== null);
    expect(range.min).toBeLessThanOrEqual(Math.min(...all, 45));
    expect(range.max).toBeGreaterThanOrEqual(Math.max(...all, 45));
    expect(phiRange([])).toBeNull();
    expect(nearestAz([0, 90, 180, 270], 359)).toBe(0);
    expect(nearestAz([0, 90, 180, 270], 200)).toBe(2);
    const row = sectorRows(r.domains[0])[0];
    expect(row).toMatchObject({ az: 0, angle: 45, phi: r.domains[0].phi[0] });
    expect(row.delta).toBeCloseTo((r.domains[0].phi[0] as number) - 45, 9);
    expect(phiAt({ ...r.domains[0], az: [], phi: [] }, 10)).toBeNull();
  });
  it("the CSV of the chart: an azimuth and φ_эфф of every domain, a gap is an empty cell", () => {
    const lines = phiCsv(readCheck(withWarning())!).split("\r\n");
    expect(lines[0]).toBe('"Азимут, °";"φ_эфф, °: A";"φ_эфф, °: B"');
    expect(lines).toHaveLength(361);
    expect(lines[1].split(";")).toHaveLength(3);
    const d0 = (SAMPLE.domains as Record<string, unknown>[])[0];
    const gap = phiCsv(readCheck({ ...SAMPLE, domains: [{ ...d0, phi_eff: { az: [0, 1], angle: [44, null] } }] })!).split("\r\n");
    expect(gap[2]).toBe("1;");
  });
});

describe("the chart φ_эфф(az)", () => {
  const draw = (r: CheckResult) => renderToStaticMarkup(createElement(PhiChart, { result: r }));
  it("360 points of the line of a domain, the marks of its table with the whisker of the tolerance, the axis of the compass", () => {
    const html = draw(real());
    expect(html).toContain('data-series="phi"');
    expect(html).toContain('data-points="360"');
    expect(html).toContain('data-series="table"');
    expect(html).toContain('data-sector="0:45"');
    expect(html).toContain("Азимут, ° (от севера по часовой стрелке)");
    for (const t of ["0°", "45°", "180°", "360°"]) expect(html, t).toContain(`>${t}</text>`);
    expect((html.match(/<path d="M/g) ?? []).length).toBe(1); // one unbroken line
  });
  it("a gap breaks the line (two pieces), it does not fall to 0; an answer with no angle says so in words", () => {
    const d0 = (SAMPLE.domains as Record<string, unknown>[])[0];
    const angle = (d0.phi_eff as { angle: number[] }).angle.map((v, i) => (i === 100 ? null : v));
    const html = draw(readCheck({ ...SAMPLE, domains: [{ ...d0, phi_eff: { az: (d0.phi_eff as { az: number[] }).az, angle } }] })!);
    expect(html).toContain('data-points="359"');
    expect((html.match(/<path d="M/g) ?? []).length).toBe(2);
    const empty = draw(readCheck({ ...SAMPLE, domains: [{ label: null, table: [], phi_eff: { az: [], angle: [] } }] })!);
    expect(empty).toContain("График не построен");
  });
  it("every domain has its line in its colour of the palette (Cobalt, Copper Orange, …)", () => {
    const html = draw(readCheck(withWarning())!);
    expect(html).toContain('data-domain="A"');
    expect(html).toContain('data-domain="B"');
    expect(html).toContain(`stroke="${seriesColor(0)}"`); // Cobalt of the brand
    expect(html).toContain(`stroke="${seriesColor(1)}"`); // Copper Orange
    expect((html.match(/<path d="M/g) ?? []).length).toBe(2);
  });
  it("a frame of 360 points is drawn in 16 ms (the best of 30: the machine of the test may stall)", () => {
    const r = real();
    for (let i = 0; i < 5; i++) renderToStaticMarkup(createElement(PhiChart, { result: r })); // warm up
    let best = Infinity;
    let html = "";
    for (let i = 0; i < 30; i++) {
      const t0 = performance.now();
      html = renderToStaticMarkup(createElement(PhiChart, { result: r }));
      best = Math.min(best, performance.now() - t0);
    }
    expect(best).toBeLessThanOrEqual(16);
    expect(html).toContain('data-points="360"');
  });
});

describe("`pit.check` and the window", () => {
  let app: App;
  let core: Core;
  let unbind: () => void;
  beforeEach(async () => {
    core = installCore();
    ({ app, unbind } = newApp());
    await waitFor(() => pitStore.get().scenariosLoaded);
    core.scenario.params = caseParams("t8117");
    await selectScenario(ID.scenario);
  });
  afterEach(() => teardown(unbind));
  const run = (args: Record<string, unknown> = {}) => app.registry.execute("pit.check", args, "user");
  const html = () => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(CheckWindow, { winId: "w" })));

  it("the command checks the scenario and opens the window with the result: the text of the answer is as before, the window shows the table and the chart", async () => {
    const r = await run();
    expect(r.ok).toBe(true);
    expect(r.result).toContain("Шаблон проверен: K = 8 (подобран автоматически), допуск 2,50°.");
    expect(core.sent.filter((s) => s.path === "/pit/check")[0].body).toEqual({ params: caseParams("t8117") });
    const wins = windowStore.get().windows.filter((w) => w.kind === PIT_CHECK_WINDOW);
    expect(wins).toHaveLength(1);
    expect(wins[0].title).toBe("Проверка шаблона карьера");
    expect(pitStore.get().check).toMatchObject({ phase: "done", scenarioId: ID.scenario, scenarioName: "Сценарий карьера" });
    expect(pitStore.get().check.result?.domains[0].phi).toHaveLength(360);
    const text = html();
    for (const id of ["pit-check-window", "pit-check-head", "pit-check-summary", "pit-check-domains", "pit-check-domain-0", "pit-check-chart", "pit-check-sectors", "pit-check-ok", "pit-check-again", "pit-check-slopes", "pit-close"]) expect(text, id).toContain(`data-testid="${id}"`);
    expect(text).toContain("K = 8 (подобран автоматически)");
    expect(text).toContain("Все блоки");
    expect(text).toContain("17"); // the offsets
    expect(text).toContain("1,44°");
    expect(text).toContain("в допуске");
    expect(text).toContain('data-points="360"');
    expect(text).toContain("CSV φ_эфф");
    expect(renderPitWindow(wins[0])).not.toBeNull();
    await run();
    expect(windowStore.get().windows.filter((w) => w.kind === PIT_CHECK_WINDOW)).toHaveLength(1); // one window
    expect(windowOfCheck()).not.toBeNull();
  });
  it("an angle error above the tolerance: the warning «увеличьте K» is shown with the domain and the sectors of the chosen domain", async () => {
    core.checkJob = { status: "done", result: withWarning(), error: null };
    expect((await run()).result).toContain("Предупреждение: Ошибка угла 3,10° больше допуска 2,5°. Увеличьте K.");
    const r = pitStore.get().check.result!;
    expect(exceeds(r.domains[1], r.tolerance)).toBe(true);
    expect(exceeds(r.domains[0], r.tolerance)).toBe(false);
    const text = html();
    expect(text).toContain('data-testid="pit-check-warning-angle_error"');
    expect(text).toContain("Ошибка угла 3,10° больше допуска 2,5°. Увеличьте K.");
    expect(text).toContain('data-over="true"');
    expect(text).toContain("больше допуска");
    expect(text).not.toContain('data-testid="pit-check-ok"');
    expect(text).toContain('data-testid="pit-check-pick"'); // two domains: a list of the sectors by domain
    expect(text).toContain('data-domains="2"');
  });
  it("a refusal of the core (`variable_grid`) is told in Russian with the hint in the window and in the answer; a new check clears the old result", async () => {
    core.checkJob = { status: "failed", result: null, error: { code: "variable_grid", message: "Оптимизация карьера требует постоянного шага сетки.", hint: "Регуляризуйте модель." } };
    const r = await run();
    expect(r).toEqual({ ok: false, result: "Оптимизация карьера требует постоянного шага сетки. Регуляризуйте модель." });
    expect(pitStore.get().check).toMatchObject({ phase: "failed", result: null, error: { code: "variable_grid" } });
    const text = html();
    expect(text).toContain('data-testid="pit-check-error"');
    expect(text).toContain("Оптимизация карьера требует постоянного шага сетки. Регуляризуйте модель.");
    expect(text).not.toContain('data-testid="pit-check-chart"');
    jobsStore.set({ jobs: [], workers: null, error: null }); // the stub answers every check with the same job: its end is read again
    core.checkJob = { status: "done", result: SAMPLE, error: null };
    expect((await run()).ok).toBe(true);
    expect(pitStore.get().check).toMatchObject({ phase: "done", error: null });
  });
  it("a job that was cancelled leaves the window idle; an answer in a form the interface does not know is told; the window before any check says how to start one", async () => {
    core.checkJob = { status: "cancelled", result: null, error: null };
    expect((await run()).result).toBe("Проверка шаблона отменена");
    expect(pitStore.get().check.phase).toBe("idle");
    expect(html()).toContain('data-testid="pit-check-idle"');
    jobsStore.set({ jobs: [], workers: null, error: null });
    core.checkJob = { status: "done", result: { oops: 1 }, error: null };
    expect((await run()).result).toBe("Ядро вернуло проверку шаблона в непонятной форме. Обновите программу.");
    expect(pitStore.get().check.phase).toBe("failed");
    pitStore.set({ check: { ...IDLE_CHECK } });
    openCheckWindow();
    expect(html()).toContain("Шаблон ещё не проверялся");
  });
  it("the summary line: the model, K (auto or set), the levels when they differ, the tolerance, the time", () => {
    expect(summaryLine(real())).toBe("модель «Рудное тело» · K = 8 (подобран автоматически) · допуск 2,50° · 0,004 с");
    expect(summaryLine({ ...real(), seconds: null })).toBe("модель «Рудное тело» · K = 8 (подобран автоматически) · допуск 2,50°");
    expect(summaryLine({ ...real(), kAuto: false, k: 10, levels: 12, seconds: 0.02 })).toBe("модель «Рудное тело» · K = 10 · уровней в шаблоне 12 · допуск 2,50° · 0,020 с");
  });
  it("the project open for reading cannot check (the core has no planner there); the check is the one request `POST /pit/check`", async () => {
    app.store.set({ projectReadOnly: true });
    expect(app.registry.reason("pit.check")).toBe("Проект открыт только для чтения");
    expect(core.sent.filter((s) => s.path === "/pit/check")).toHaveLength(0);
  });
});
