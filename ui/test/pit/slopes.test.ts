// The slopes and the calendar of the pit (Э21, 21.17; ТЗ 8.11.2, 8.11.4, 8.11.5; ADR 0028 решения 4, 9–12, 19, 29; docs/progress/e21-api.md §2, §15 п. 11, §16): the parameters of the
// windows = the request the core takes (`slopes`, `k`, `calendar` of the canonical params of the cases of `core/tests/data/pit/params.json`), what is wrong is told in Russian with a
// hint in its place before anything is sent (a sector above 16, an angle outside 10…80°), the flat arguments of `pit.slopes` / `pit.calendar` and the forms are the same request.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { windowStore } from "../../src/shell/windows";
import { readParams } from "../../src/modules/pit/answers";
import { openCalendarWindow, openSlopesWindow, renderPitWindow, windowOfCalendar, windowOfSlopes } from "../../src/modules/pit/bind";
import { calendarIssueOf, slopesIssueOf } from "../../src/modules/pit/bindSetup";
import { CalendarWindow } from "../../src/modules/pit/CalendarWindow";
import { commands } from "../../src/modules/pit/commands";
import {
  calendarArgs,
  calendarFormOfArgs,
  calendarFormOfParams,
  calendarPatch,
  EMPTY_CALENDAR,
  hasCalendar,
  pctOf,
  shareOf,
  type CalendarForm,
} from "../../src/modules/pit/formsCalendar";
import {
  EMPTY_SLOPES,
  hasSlopes,
  parseSectors,
  sectorsText,
  slopesArgs,
  slopesFormOfArgs,
  slopesFormOfParams,
  slopesPatch,
  type SlopesForm,
} from "../../src/modules/pit/formsSlopes";
import { SlopesWindow } from "../../src/modules/pit/SlopesWindow";
import { PIT_CALENDAR_WINDOW, PIT_SLOPES_WINDOW, pitStore, selectScenario } from "../../src/modules/pit/state";
import { caseParams, ID, installCore, newApp, PARAMS, teardown, waitFor, type Core } from "./harness";

const CASES = Object.keys(PARAMS.cases as Record<string, unknown>);
const patchOf = (f: SlopesForm) => {
  const r = slopesPatch(f);
  if (!r.ok) throw new Error(JSON.stringify(r.issue));
  return r.patch;
};
const wrong = (patch: Partial<SlopesForm>) => {
  const r = slopesPatch({ ...EMPTY_SLOPES, ...patch });
  if (r.ok) throw new Error("the form must be wrong");
  return r.issue;
};
const sectors = (n: number) => Array.from({ length: n }, (_, i) => ({ az: String(i * 20), angle: "45" }));

describe("the window «Углы бортов»: the parameters of the window = the request", () => {
  it.each(CASES)("%s: the form of the canonical params gives the same `slopes` and `k` back", (name) => {
    const p = readParams(caseParams(name));
    expect(patchOf(slopesFormOfParams(p))).toEqual({ slopes: p.slopes, k: p.k });
  });
  it("the case with domains: the common table, the field, a table of its own for a label, K and the tolerance as the core keeps them", () => {
    const p = readParams(caseParams("sectors_domains"));
    const f = slopesFormOfParams(p);
    expect(f).toMatchObject({ domainOn: true, domainField: "dom", kAuto: false, k: "12", tolerance: "2" });
    expect(f.table.map((r) => [r.az, r.angle])).toEqual([["0", "40"], ["90", "45"], ["180", "50"]]);
    expect(f.domains.map((d) => [d.label, d.table.length])).toEqual([["A", 2], ["b", 1]]);
  });
  it("a number is typed with a decimal comma; K is auto or 1…20; a single sector may be without the azimuth (a constant angle)", () => {
    const r = patchOf({ ...EMPTY_SLOPES, table: [{ az: "", angle: "42,5" }], tolerance: "1,5", kAuto: false, k: "9" });
    expect(r).toEqual({ slopes: { table: [{ az: 0, angle: 42.5 }], domain_field: null, domains: [], tolerance: 1.5 }, k: 9 });
    expect(patchOf(EMPTY_SLOPES).k).toBeNull();
    expect(wrong({ table: [{ az: "", angle: "45" }, { az: "90", angle: "45" }] }).message).toBe("Углы бортов: азимут сектора 1 — не число.");
  });
});

describe("what is wrong is told in Russian with a hint and the place", () => {
  it("more than 16 sectors (the 16th is fine); no sector; a domain with more than 16", () => {
    expect(slopesPatch({ ...EMPTY_SLOPES, table: sectors(16) }).ok).toBe(true);
    const i = wrong({ table: sectors(17) });
    expect(i).toMatchObject({ field: "slopes", param: "slopes.table", message: "Углы бортов: секторов больше 16." });
    expect(i.hint).toBe("Объедините соседние секторы: их не больше 16.");
    expect(wrong({ table: [] }).message).toBe("Углы бортов: нет ни одного сектора.");
    expect(wrong({ domainOn: true, domainField: "DOM", domains: [{ label: "A", table: sectors(17) }] })).toMatchObject({ field: "domains", param: "slopes.domains.0.table", message: "Домен «A»: секторов больше 16." });
  });
  it("an angle outside 10…80° (the bounds are fine), not a number; an azimuth outside [0; 360), twice", () => {
    const at = (angle: string) => ({ table: [{ az: "0", angle }] });
    expect(slopesPatch({ ...EMPTY_SLOPES, ...at("10") }).ok).toBe(true);
    expect(slopesPatch({ ...EMPTY_SLOPES, ...at("80") }).ok).toBe(true);
    const low = wrong(at("9,9"));
    expect(low).toMatchObject({ field: "slopes", param: "slopes.table.0.angle", message: "Углы бортов: угол сектора 1 (9,9°) вне диапазона 10…80°." });
    expect(low.hint).toBe("Углы бортов карьера — от 10 до 80°.");
    expect(wrong(at("80,1")).param).toBe("slopes.table.0.angle");
    expect(wrong(at("крутой")).message).toBe("Углы бортов: угол сектора 1 — не число.");
    const rows = (az: string) => ({ table: [{ az: "0", angle: "45" }, { az, angle: "50" }] });
    expect(wrong(rows("360"))).toMatchObject({ param: "slopes.table.1.az", message: "Углы бортов: азимут сектора 2 (360°) вне диапазона [0; 360)." });
    expect(wrong(rows("-1")).param).toBe("slopes.table.1.az");
    expect(wrong(rows("0"))).toMatchObject({ param: "slopes.table.1.az", message: "Углы бортов: азимут 0° задан дважды." });
    expect(wrong({ domainOn: true, domainField: "DOM", domains: [{ label: "A", table: [{ az: "0", angle: "5" }] }] })).toMatchObject({ field: "domains", param: "slopes.domains.0.table.0.angle" });
  });
  it("domains: no field, a label twice or with no text, more than 16; K and the tolerance out of their ranges", () => {
    expect(wrong({ domainOn: true })).toMatchObject({ field: "domains", message: "Не выбрано поле доменов." });
    const d = (label: string) => ({ label, table: [{ az: "0", angle: "45" }] });
    expect(wrong({ domainOn: true, domainField: "DOM", domains: [d("A"), d("a")] }).message).toBe("Домен «a» повторяется.");
    expect(wrong({ domainOn: true, domainField: "DOM", domains: [d(" ")] }).message).toBe("У домена 1 нет подписи.");
    expect(wrong({ domainOn: true, domainField: "DOM", domains: Array.from({ length: 17 }, (_, i) => d(`Д${i}`)) }).message).toBe("Доменов больше 16.");
    expect(wrong({ kAuto: false, k: "0" })).toMatchObject({ field: "k", param: "k", message: "K = 0 вне диапазона 1…20." });
    expect(wrong({ kAuto: false, k: "21" }).field).toBe("k");
    expect(wrong({ kAuto: false, k: "8,5" }).message).toBe("Число уровней K — целое число.");
    expect(wrong({ kAuto: false, k: "" }).field).toBe("k");
    expect(slopesPatch({ ...EMPTY_SLOPES, kAuto: false, k: "20" }).ok).toBe(true);
    expect(wrong({ tolerance: "0,4" })).toMatchObject({ field: "tolerance", param: "slopes.tolerance", message: "Допуск угла 0,4° вне диапазона 0,5…10°." });
    expect(wrong({ tolerance: "10,1" }).field).toBe("tolerance");
    expect(wrong({ tolerance: "" }).message).toBe("Допуск угла — не число.");
    expect(slopesPatch({ ...EMPTY_SLOPES, tolerance: "0,5" }).ok && slopesPatch({ ...EMPTY_SLOPES, tolerance: "10" }).ok).toBe(true);
  });
});

describe("the flat arguments of `pit.slopes`", () => {
  const base = readParams(caseParams("sectors_domains"));
  it("the form → the arguments → the form → the same request, for every case", () => {
    for (const name of CASES) {
      const p = readParams(caseParams(name));
      const got = slopesArgs(slopesFormOfParams(p), ID.scenario);
      if (!got.ok) throw new Error(JSON.stringify(got.issue));
      const back = slopesFormOfArgs(got.args, readParams(caseParams("minimal")));
      if (!back.ok) throw new Error(JSON.stringify(back.issue));
      expect(patchOf(back.form), name).toEqual({ slopes: p.slopes, k: p.k });
    }
    const got = slopesArgs(slopesFormOfParams(base), null);
    if (!got.ok) throw new Error("wrong");
    expect(got.args).toEqual({ table: "0:40; 90:45; 180:50", domain_field: "dom", domains: JSON.stringify(base.slopes.domains), k: 12, tolerance: 2 });
  });
  it("the table is text «азимут:угол; …»; a single sector may be just the angle; a key that is not in the call leaves the scenario as it is; null takes things away", () => {
    expect(sectorsText([{ az: 0, angle: 40.5 }, { az: 90, angle: 45 }])).toBe("0:40,5; 90:45");
    expect(parseSectors("45")).toEqual([{ az: "", angle: "45" }]);
    expect(parseSectors("0:40; 90 : 45;\n180:50")).toHaveLength(3);
    expect(hasSlopes({})).toBe(false);
    expect(hasSlopes({ k: null })).toBe(true);
    const only = slopesFormOfArgs({ tolerance: 3 }, base);
    if (!only.ok) throw new Error("wrong");
    expect(patchOf(only.form)).toEqual({ slopes: { ...base.slopes, tolerance: 3 }, k: 12 });
    const cleared = slopesFormOfArgs({ k: null, domain_field: null }, base);
    if (!cleared.ok) throw new Error("wrong");
    expect(patchOf(cleared.form)).toEqual({ slopes: { ...base.slopes, domain_field: null, domains: [] }, k: null });
    const single = slopesFormOfArgs({ table: "45" }, base);
    if (!single.ok) throw new Error("wrong");
    expect(patchOf(single.form).slopes.table).toEqual([{ az: 0, angle: 45 }]);
  });
  it("domains that are not a list in JSON are told; a refusal of the core is put in its place of the form", () => {
    const r = slopesFormOfArgs({ domains: "{}" }, base);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.issue).toMatchObject({ field: "domains", message: "Домены — не список в JSON." });
    const f = (code: string, param: string | null) => slopesIssueOf({ code, message: "М", hint: "П", param }).field;
    expect(f("bad_param", "slopes.table.2.angle")).toBe("slopes");
    expect(f("bad_param", "slopes.domains.1.table.0.az")).toBe("domains");
    expect(f("block_field_not_found", "slopes.domain_field")).toBe("domains");
    expect(f("bad_param", "slopes.tolerance")).toBe("tolerance");
    expect(f("bad_param", "k")).toBe("k");
    expect(f("bad_param", "elements")).toBe("general");
  });
});

describe("the calendar: the parameters of the window = the request (the rate is a share in the API, percent in the window)", () => {
  const patchCal = (f: Partial<CalendarForm>) => {
    const r = calendarPatch({ ...EMPTY_CALENDAR, ...f });
    if (!r.ok) throw new Error(JSON.stringify(r.issue));
    return r.patch.calendar;
  };
  const wrongCal = (f: Partial<CalendarForm>) => {
    const r = calendarPatch({ ...EMPTY_CALENDAR, ...f });
    if (r.ok) throw new Error("the form must be wrong");
    return r.issue;
  };
  it.each(CASES)("%s: the form of the canonical params gives the same `calendar` back", (name) => {
    const p = readParams(caseParams(name));
    expect(patchCal(calendarFormOfParams(p))).toEqual(p.calendar);
  });
  it("8 % is 0,08 (the noise of the multiplication is removed); a number is typed with a comma; Q_гм that is empty is no limit, not 0", () => {
    expect(EMPTY_CALENDAR.ratePct).toBe("8");
    expect(patchCal({ qOre: "1 000 000", qMass: "2500000,5", ratePct: "7,5" })).toEqual({ q_ore: 1e6, q_mass: 2500000.5, rate: 0.075 });
    expect(patchCal({})).toEqual({ q_ore: null, q_mass: null, rate: 0.08 });
    expect(calendarFormOfParams({ ...readParams(caseParams("minimal")), calendar: { q_ore: null, q_mass: null, rate: 0.07 } }).ratePct).toBe("7");
    expect([pctOf(0.07), pctOf(0.29), shareOf(7), shareOf(29)]).toEqual([7, 29, 0.07, 0.29]);
    expect(patchCal({ ratePct: "" }).rate).toBe(0.08);
    expect(patchCal({ ratePct: "0" }).rate).toBe(0);
    expect(patchCal({ ratePct: "50" }).rate).toBe(0.5);
  });
  it("what is wrong is told with a hint in its place: Q not above 0 or not a number, a rate outside 0…50 %", () => {
    expect(wrongCal({ qOre: "0" })).toMatchObject({ field: "q_ore", param: "calendar.q_ore", message: "Производительность по руде Q_р должна быть больше нуля." });
    expect(wrongCal({ qOre: "-5" }).field).toBe("q_ore");
    expect(wrongCal({ qMass: "много" })).toMatchObject({ field: "q_mass", param: "calendar.q_mass", message: "Производительность по горной массе Q_гм — не число." });
    expect(wrongCal({ ratePct: "51" })).toMatchObject({ field: "rate", param: "calendar.rate", message: "Ставка r 51 % вне диапазона 0…50 %." });
    expect(wrongCal({ ratePct: "-1" }).field).toBe("rate");
    expect(wrongCal({ ratePct: "восемь" }).message).toBe("Ставка r — не число.");
  });
  it("the arguments: `rate_pct` is percent; the form → the arguments → the form → the same request; a key that is not in the call stays", () => {
    const p = readParams({ ...caseParams("minimal"), calendar: { q_ore: 1e6, q_mass: 2e6, rate: 0.075 } });
    const got = calendarArgs(calendarFormOfParams(p), ID.scenario);
    if (!got.ok) throw new Error(JSON.stringify(got.issue));
    expect(got.args).toEqual({ scenario_id: ID.scenario, q_ore: 1e6, q_mass: 2e6, rate_pct: 7.5 });
    expect(patchCal(calendarFormOfArgs(got.args, readParams(caseParams("minimal"))))).toEqual(p.calendar);
    expect(hasCalendar({})).toBe(false);
    expect(hasCalendar({ rate_pct: 8 })).toBe(true);
    expect(patchCal(calendarFormOfArgs({ q_mass: null }, p))).toEqual({ q_ore: 1e6, q_mass: null, rate: 0.075 });
    const f = (param: string) => calendarIssueOf({ code: "bad_param", message: "М", hint: "П", param }).field;
    expect([f("calendar.q_ore"), f("calendar.q_mass"), f("calendar.rate"), f("rf")]).toEqual(["q_ore", "q_mass", "rate", "general"]);
  });
});

describe("`pit.slopes`, `pit.calendar` and their windows", () => {
  let app: App;
  let core: Core;
  let unbind: () => void;
  beforeEach(async () => {
    core = installCore();
    ({ app, unbind } = newApp());
    await waitFor(() => pitStore.get().scenariosLoaded);
    core.scenario.params = caseParams("sectors_domains");
    await selectScenario(ID.scenario);
  });
  afterEach(() => teardown(unbind));
  const sentPuts = () => core.sent.filter((r) => r.method === "PUT" && r.path === `/pit/scenarios/${ID.scenario}`);
  const run = (id: "pit.slopes" | "pit.calendar", args: Record<string, unknown> = {}) => app.registry.execute(id, args, "user");
  const html = (c: typeof SlopesWindow | typeof CalendarWindow) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(c, { winId: "w" })));

  it("the commands are declared with flat parameters and bound; a project open for reading cannot change them", () => {
    const by = Object.fromEntries(commands.map((c) => [c.id, c]));
    expect(Object.keys(by["pit.slopes"].params ?? {})).toEqual(["scenario_id", "table", "domain_field", "domains", "k", "tolerance"]);
    expect(Object.keys(by["pit.calendar"].params ?? {})).toEqual(["scenario_id", "q_ore", "q_mass", "rate_pct"]);
    expect(by["pit.slopes"].stage ?? by["pit.calendar"].stage).toBeUndefined();
    expect([app.registry.reason("pit.slopes"), app.registry.reason("pit.calendar")]).toEqual([null, null]);
    app.store.set({ projectReadOnly: true });
    expect(app.registry.reason("pit.slopes")).toBe("Проект открыт только для чтения");
  });
  it("«Углы бортов» without arguments: the window with the table, the domains of the model, K and the tolerance in the form; one window", async () => {
    expect((await run("pit.slopes")).result).toBe("Открыто окно «Углы бортов карьера»");
    expect(windowStore.get().windows.filter((w) => w.kind === PIT_SLOPES_WINDOW)).toHaveLength(1);
    await waitFor(() => !!pitStore.get().models[ID.model]);
    expect(pitStore.get().slopesForm).toMatchObject({ domainOn: true, domainField: "dom", k: "12", tolerance: "2" });
    const text = html(SlopesWindow);
    for (const id of ["pit-slopes-window", "pit-sectors", "pit-sectors-az-0", "pit-sectors-angle-2", "pit-sectors-add", "pit-domains-on", "pit-domain-field", "pit-domains", "pit-domain-own-A", "pit-domain-own-B", "pit-domain-own-C", "pit-domain-table-A", "pit-k-auto", "pit-k-own", "pit-k", "pit-tolerance", "pit-slopes-apply", "pit-slopes-check", "pit-close"]) expect(text, id).toContain(`data-testid="${id}"`);
    expect(text).toContain("3 из 16");
    expect(text).toContain("своя таблица");
    expect(text).toContain("data-own=\"2\""); // «A» and «b» (the label «B» of the field, the case is not told apart) have their own tables
    expect(renderPitWindow(windowStore.get().windows[0])).not.toBeNull();
    await run("pit.slopes");
    expect(windowStore.get().windows.filter((w) => w.kind === PIT_SLOPES_WINDOW)).toHaveLength(1);
    expect(windowOfSlopes()).not.toBeNull();
  });
  it("with the slopes in the call the scenario is changed: the whole canonical params go with the version read, what the call does not name stays", async () => {
    const r = await run("pit.slopes", { table: "0:40; 120:46,5; 240:50", k: 10, tolerance: 1 });
    expect(r.ok).toBe(true);
    const put = sentPuts()[0].body;
    expect(put.expected_version).toBe(2);
    const base = caseParams("sectors_domains") as { slopes: Record<string, unknown> };
    expect(put.params).toEqual({ ...base, k: 10, slopes: { ...base.slopes, table: [{ az: 0, angle: 40 }, { az: 120, angle: 46.5 }, { az: 240, angle: 50 }], tolerance: 1 } });
    expect(r.result).toContain("Сценарий «Сценарий карьера» обновлён (версия 3).");
    expect(r.result).toContain("проверьте его командой «Проверить шаблон»");
    expect((await run("pit.slopes", { k: null })).ok).toBe(true);
    expect((sentPuts().at(-1)?.body.params as { k: number | null }).k).toBeNull();
  });
  it("«Применить» of the window is the same request as the command", async () => {
    await run("pit.slopes");
    const f = pitStore.get().slopesForm;
    const got = slopesArgs({ ...f, table: [...f.table, { az: "270", angle: "55" }] }, ID.scenario);
    if (!got.ok) throw new Error(JSON.stringify(got.issue));
    expect((await run("pit.slopes", got.args)).ok).toBe(true);
    const slopes = (sentPuts()[0].body.params as { slopes: { table: unknown[] } }).slopes;
    expect(slopes.table).toHaveLength(4);
  });
  it("17 sectors and an angle of 85° are told in Russian before anything is sent; a refusal of the core is told and put in its place", async () => {
    const many = await run("pit.slopes", { table: Array.from({ length: 17 }, (_, i) => `${i * 10}:45`).join("; ") });
    expect(many).toEqual({ ok: false, result: "Углы бортов: секторов больше 16. Объедините соседние секторы: их не больше 16." });
    expect(pitStore.get().slopesIssue).toMatchObject({ field: "slopes", param: "slopes.table" });
    const steep = await run("pit.slopes", { table: "0:85" });
    expect(steep.result).toMatch(/^Углы бортов: угол сектора 1 \(85°\) вне диапазона 10…80°\./);
    expect(pitStore.get().slopesIssue).toMatchObject({ param: "slopes.table.0.angle" });
    expect(sentPuts()).toHaveLength(0);
    core.force(`PUT /pit/scenarios/${ID.scenario}`, { status: 422, body: { code: "bad_param", message: "Домен «b» не найден.", hint: "Проверьте поле доменов.", param: "slopes.domains.1.label" } });
    expect((await run("pit.slopes", { tolerance: 3 })).result).toBe("Домен «b» не найден. Проверьте поле доменов.");
    expect(pitStore.get().slopesIssue).toMatchObject({ field: "domains", param: "slopes.domains.1.label" });
  });
  it("«Календарь» without arguments: the window with Q_р, Q_гм and the rate in percent", async () => {
    core.scenario.params = { ...caseParams("minimal"), calendar: { q_ore: 1e6, q_mass: null, rate: 0.1 } };
    expect((await run("pit.calendar")).result).toBe("Открыто окно «Календарь карьера»");
    expect(windowStore.get().windows.filter((w) => w.kind === PIT_CALENDAR_WINDOW)).toHaveLength(1);
    expect(pitStore.get().calendarForm).toEqual({ qOre: "1000000", qMass: "", ratePct: "10" });
    const text = html(CalendarWindow);
    for (const id of ["pit-calendar-window", "pit-q-ore", "pit-q-mass", "pit-rate", "pit-calendar-note", "pit-calendar-apply", "pit-close"]) expect(text, id).toContain(`data-testid="${id}"`);
    expect(text).toContain("без ограничения");
    expect(text).toContain("можно менять без нового запуска");
    expect(renderPitWindow(windowStore.get().windows[0])).not.toBeNull();
    expect(windowOfCalendar()).not.toBeNull();
  });
  it("the calendar changes the scenario with the rate as a share; the result stays valid (the core says `params_changed: false`)", async () => {
    core.scenario.has_result = true;
    core.force(`PUT /pit/scenarios/${ID.scenario}`, { status: 200, body: { id: ID.scenario, version: 3, name: "Сценарий карьера", params_changed: false } });
    const r = await run("pit.calendar", { q_ore: 1200000, q_mass: 2400000, rate_pct: 7.5 });
    expect(r).toMatchObject({ ok: true });
    expect(sentPuts()[0].body.params).toEqual({ ...caseParams("sectors_domains"), calendar: { q_ore: 1200000, q_mass: 2400000, rate: 0.075 } });
    expect(r.result).not.toContain("устарел");
    // the window of the form speaks percent: the arguments of its form are those the command takes
    await openCalendarWindow(ID.scenario);
    const got = calendarArgs(pitStore.get().calendarForm, ID.scenario);
    if (!got.ok) throw new Error("wrong");
    expect(got.args).toEqual({ scenario_id: ID.scenario, q_ore: null, q_mass: null, rate_pct: 8 });
    await openSlopesWindow(ID.scenario);
  });
  it("a wrong calendar is told with its hint in its place and nothing is sent; q_mass: null takes the limit away", async () => {
    expect((await run("pit.calendar", { rate_pct: 80 })).result).toMatch(/^Ставка r 80 % вне диапазона 0…50 %\. Ставка дисконтирования — от 0 до 50 % в год/);
    expect(pitStore.get().calendarIssue).toMatchObject({ field: "rate", param: "calendar.rate" });
    expect((await run("pit.calendar", { q_ore: 0 })).result).toMatch(/Q_р должна быть больше нуля/);
    expect(sentPuts()).toHaveLength(0);
    expect((await run("pit.calendar", { q_mass: null })).ok).toBe(true);
    expect((sentPuts()[0].body.params as { calendar: { q_mass: number | null } }).calendar.q_mass).toBeNull();
  });
});
