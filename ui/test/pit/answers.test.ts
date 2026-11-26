// The readers of the answers of the pit optimisation (Э21, 21.16): the forms are those of the contract (`core/tests/data/pit/contract_answers.json`;
// docs/progress/e21-api.md §2–§6, §13); a gap is `null` — never 0 (8.0.1); an answer that is not a scenario is not read.
import { describe, expect, it } from "vitest";
import { DEFAULT_LIMITS, DEFAULT_PARAMS, readCheckSummary, readDefaults, readParams, readRunSummary, readScenario, readScenarioRow, readValidate, rfValues } from "../../src/modules/pit/answers";
import { checkText, fmt, runText, warningText } from "../../src/modules/pit/format";
import { issueOfRefusal } from "../../src/modules/pit/forms";
import { answer, FILE, PARAMS } from "./harness";

describe("the defaults of the core", () => {
  const d = readDefaults(answer("defaults").body);
  it("`defaults.params` is the canonical params of a new scenario (the built-in defaults are the same)", () => {
    expect(JSON.parse(JSON.stringify(d.params))).toEqual(answer("defaults").body.params);
    expect(d.params).toEqual(DEFAULT_PARAMS);
    const rf = d.params.rf as number[];
    expect(rf).toHaveLength(25);
    expect(rf[0]).toBe(0.3);
    expect(rf[24]).toBe(1.5);
    expect(d.params.calendar.rate).toBe(0.08);
    expect(d.params.slopes.tolerance).toBe(2.5);
  });
  it("the limits, the labels of the units and of the prices, the ranges, the model", () => {
    expect(d.limits).toEqual(DEFAULT_LIMITS);
    expect(d.units).toEqual(["g/t", "ppm", "ppb", "%"]);
    expect(d.gradeLabels["g/t"]).toBe("г/т");
    expect(d.priceLabels).toMatchObject({ "g/t": "$/унц", "%": "$/т" });
    expect(d.ranges.rf).toMatchObject({ lo: 0.01, hi: 5, integer: false });
    expect(d.ranges["calendar.rate"]).toMatchObject({ lo: 0, hi: 0.5, unit: "доля в год" });
    expect(d.model?.elements).toEqual([{ field: "AU", unit: "g/t" }, { field: "CU", unit: "%" }]);
    expect(d.model?.density).toBe("SG");
    expect(d.model?.categories[0]).toMatchObject({ field: "ROCK", labels: ["Окисленная", "Первичная"] });
    expect(d.model?.terrains).toEqual([]); // the real answer lists the DTMs of the project (21.11)
  });
  it("an answer that is not a defaults answer is the built-in defaults", () => {
    const e = readDefaults(null);
    expect(e.params).toEqual(DEFAULT_PARAMS);
    expect(e.limits).toEqual(DEFAULT_LIMITS);
    expect(e.model).toBeNull();
  });
});

describe("the params", () => {
  it("every case of `params.json`: the canonical params are read as they are (nothing added, nothing lost)", () => {
    for (const [name, c] of Object.entries(PARAMS.cases as Record<string, { canonical: unknown; resolved: unknown }>)) {
      expect(JSON.parse(JSON.stringify(readParams(c.canonical))), name).toEqual(c.canonical);
      expect(JSON.parse(JSON.stringify(readParams(c.resolved))), `${name} (resolved)`).toEqual(c.resolved);
    }
  });
  it("a key that is missing is the default of the core; a gap is null, never 0", () => {
    const p = readParams({ model_id: 12, "…": "resolved canonical (§2)" });
    expect(p).toEqual({ ...DEFAULT_PARAMS, model_id: 12 });
    expect(readParams({ elements: [{ field: "AU", price: null }] }).elements[0]).toEqual({ field: "AU", unit: null, price: null, sales: 0, royalty: 0 });
    expect(readParams({ mining: { c_m0: null } }).mining.c_m0).toBeNull();
    expect(readParams({ solver: "xx", rf: ["a"] })).toMatchObject({ solver: "auto", rf: DEFAULT_PARAMS.rf });
  });
  it("the range of RF is its list without the noise of the sum (0,3 + 3·0,05 → 0,45)", () => {
    expect(rfValues({ from: 0.3, to: 0.5, step: 0.05 })).toEqual([0.3, 0.35, 0.4, 0.45, 0.5]);
    expect(rfValues({ from: 0.5, to: 1.5, step: 0.25 })).toEqual(PARAMS.cases.rf_range.canonical.rf);
    expect(rfValues({ from: 0.3, to: 1.5, step: 0.05 })).toEqual(PARAMS.cases.minimal.canonical.rf);
    expect(rfValues({ from: 1, to: 0.5, step: 0.1 })).toEqual([]);
    expect(rfValues({ from: 0.5, to: 1, step: 0 })).toEqual([]);
  });
});

describe("the readiness (`validate`)", () => {
  it("a new scenario is not ready: the first missing is the model, the places are the paths of the params", () => {
    const v = readValidate(answer("validate_new").body);
    expect(v.ready).toBe(false);
    expect(v.missing[0]).toEqual({ param: "model_id", message: "Не выбрана блочная модель." });
    expect(v.missing.map((m) => m.param)).toEqual(["model_id", "elements", "rocks.types.Все породы", "mining.c_m0"]);
    expect(v.warnings.map((w) => w.code)).toEqual(["no_terrain", "no_calendar"]);
    expect(warningText(v.warnings[0])).toContain("Рельеф не задан");
  });
  it("a ready scenario has nothing missing; `ready` is true only without a missing (the one source of «Запустить»)", () => {
    expect(readValidate(answer("validate_ready").body)).toMatchObject({ ready: true, missing: [] });
    expect(readValidate({ ready: true, missing: [{ param: "x", message: "Нет x." }] }).ready).toBe(false);
    expect(readValidate(null)).toEqual({ ready: false, missing: [], warnings: [] });
  });
});

describe("the scenarios", () => {
  it("the list: the state, the result, the model, the graph (for the tree of 21.18)", () => {
    const rows = (answer("scenarios_list").body.scenarios as unknown[]).map(readScenarioRow);
    expect(rows[0]).toMatchObject({ id: 17, name: "Сценарий карьера", version: 2, state: "ready", has_result: true, params_changed: false, model: { id: 12, name: "Рудное тело" }, shells: 3, job_id: 3 });
    expect(rows[0]?.graph).toEqual({ stale: false, stale_reason: null, source_lost: false, pinned: false });
    expect(readScenarioRow({ id: "x" })).toBeNull();
    expect(readScenarioRow({ id: 1, name: "Н", state: "running", value: NaN })).toMatchObject({ state: "running", value: null, has_result: false, shells: null });
  });
  it("the scenario: the canonical params, the hash, the meta; an answer without an id is not a scenario", () => {
    const s = readScenario(answer("scenario_get").body)!;
    expect(s).toMatchObject({ id: 17, version: 2, state: "ready", error: null, has_result: true, params_changed: false });
    expect(s.params.model_id).toBe(12);
    expect(s.params_hash).toMatch(/^[0-9a-f]{64}$/);
    expect((s.meta?.shells as unknown[]).length).toBe(3);
    expect(readScenario({ name: "Н" })).toBeNull();
    expect(readScenario({ id: 1, name: "Н", state: "error", error: { code: "pit_internal", message: "Сбой.", hint: "Повторите." } })?.error).toEqual({ code: "pit_internal", message: "Сбой.", hint: "Повторите." });
  });
  it("the answers of the writes: `{id, version, name}`, `{id, version, name, params_changed}`, `{id, deleted}`", () => {
    expect(Object.keys(answer("scenario_post").body)).toEqual(["id", "version", "name"]);
    expect(Object.keys(answer("scenario_put").body)).toEqual(["id", "version", "name", "params_changed"]);
    expect(answer("scenario_delete").body).toEqual({ id: 17, deleted: true });
    expect(Object.keys(answer("run_post").body)).toEqual(["source", "id"]);
  });
});

describe("the results of the jobs", () => {
  it("`pit.run`: the shells, the totals, the template, the warnings; the text for the log and the assistant", () => {
    const r = readRunSummary(answer("run_result").body)!;
    expect(r).toMatchObject({ scenario_id: 17, name: "Сценарий карьера", solver: "hpf", q: 0.01, objective_error: expect.closeTo(7.56, 6) });
    expect(r.shells.map((s) => s.k)).toEqual([1, 2, 3]);
    expect(r.shells[2]).toMatchObject({ rf: 1.2, blocks: 921 });
    expect(r.totals.strip).toBeCloseTo(3.4946, 3);
    expect(r.template).toEqual({ k: 8, error_max: 1.44 });
    const text = runText(r);
    expect(text).toContain("Сценарий «Сценарий карьера» рассчитан (решатель HPF): оболочек 3");
    expect(text).toContain("K = 8, ошибка угла до 1,44°");
    expect(text).toMatch(/вскрыша 3,/);
    expect(readRunSummary({})).toBeNull();
  });
  it("a strip ratio without ore is not told as a number (O = 0 → null, not 0)", () => {
    const body = { ...(answer("run_result").body as Record<string, unknown>), totals: { ore_t: 0, waste_t: 5, strip: null, cf: -10 } };
    expect(runText(readRunSummary(body)!)).toContain("вскрыша не определена (руды нет)");
  });
  it("`pit.check`: K, the offsets and the angle error of every domain", () => {
    const c = readCheckSummary(answer("check_result").body)!;
    expect(c).toMatchObject({ k: 8, k_auto: true, tolerance: 2.5 });
    expect(c.domains[0]).toMatchObject({ label: null, n_offsets: 17, error_max: 1.44, warning: false });
    expect(typeof c.domains[0].error_mean).toBe("number"); // the value is the sample's until 21.11
    expect(checkText(c)).toContain("смещений 17, ошибка угла до 1,44°");
    expect(readCheckSummary({})).toBeNull();
    expect(fmt(null)).toBe("нет значения");
  });
});

describe("the refusals of the core are told in their place of the form", () => {
  const refusal = (key: string) => {
    const b = answer(key).body as { code: string; message: string; hint: string; param?: string };
    return { code: b.code, message: b.message, hint: b.hint, param: b.param ?? null };
  };
  it("`bad_param` names the place; `pit_not_ready` — the first missing", () => {
    expect(issueOfRefusal(refusal("bad_param"))).toMatchObject({ field: "rf", param: "rf.step" });
    expect(issueOfRefusal(refusal("pit_not_ready"))).toMatchObject({ field: "elements", param: "elements.0.price" });
    expect(answer("bad_param").status).toBe(422);
    expect(answer("pit_not_ready").status).toBe(422);
  });
  it("every refusal of the file has Russian words and a hint; the codes of the objects go to their fields", () => {
    const refusals = FILE.refusals as Record<string, { status: number; body: { code: string; message: string; hint: string } }>;
    expect(Object.keys(refusals)).toEqual(expect.arrayContaining(["pit_not_ready", "variable_grid", "pit_too_large", "pit_density_missing", "pit_empty", "filter_field_missing", "pit_solid_failed"]));
    for (const [code, r] of Object.entries(refusals)) {
      expect(r.body.code, code).toBe(code);
      expect(r.body.message, code).toMatch(/[А-Яа-я]/);
      expect(r.body.hint, code).toMatch(/[А-Яа-я]/);
    }
    const f = (code: string, param: string | null = null) => issueOfRefusal({ code, message: "М", hint: "П", param }).field;
    expect(f("block_model_not_found")).toBe("model");
    expect(f("wireframe_not_found")).toBe("relief");
    expect(f("filter_not_found")).toBe("filter");
    expect(f("filter_field_missing")).toBe("restrict");
    expect(f("name_taken", "name")).toBe("name");
    expect(f("bad_param", "mining.c_m0")).toBe("mining");
    expect(f("bad_param", "default_density")).toBe("density");
    expect(f("pit_not_ready", "rocks.types.Окисленная.c_p")).toBe("general");
  });
});
