// The client of the estimation (Э18, 18.19): the readers on the samples of the contract
// (`core/tests/data/estimate/contract_answers.json`, until the routes of 18.13 give real answers) and the queries.
import { CoreError } from "../../src/shell/api";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { defaultsPath, loadEstimation, loadEstimations, loadPlans, readDefaults, readRow, readRun, refusalOf, METHODS, STATUS_LABELS } from "../../src/modules/estimation/api";
import { RANGES } from "../../src/modules/estimation/form";
import { isEstimationEvent } from "../../src/modules/estimation/state";
import { A, installCore, newApp, teardown, type Core } from "./harness";

let core: Core;
beforeEach(() => {
  core = installCore();
  newApp();
});
afterEach(teardown);

describe("GET /estimate/defaults", () => {
  const d = readDefaults(A.defaults.body);
  it("the model, its domain fields with the labels, the sub-blocks", () => {
    expect(d.model).toMatchObject({ id: 7, name: "Модель", version: 4, rows: 1200, parents: 1000, sub: true, regular: true, size: [10, 10, 5] });
    expect(d.model.domain_fields).toEqual([{ name: "ZONE", kind: "category", labels: ["окисл", "перех", "перв"], values: [] }]);
  });
  it("the counts, the means (a gap is null, never 0), the values of the domain", () => {
    expect(d.samples.counts).toEqual({ rows: 1196, missing: 4, no_position: 0, outside_domain: 0, used: 1192 });
    expect(d.samples.mean).toEqual({ naive: 1.84, declus: 1.62 });
    expect(d.samples.domain?.values.map((v) => [v.value, v.n, v.declus])).toEqual([["окисл", 880, 1.77], ["перех", 200, 1.21], ["перв", 112, null]]);
  });
  it("the mapping of the labels, the model of variogram, the search of the model, the ranges", () => {
    expect(d.mapping).toEqual({ окисл: ["окисл"], перех: ["перех"], перв: ["перв"] });
    expect(d.variogram).toMatchObject({ id: 21, scale: "z", share: null, warnings: [] });
    expect(d.search).toMatchObject({ from: "model", r1: 30, ang1: 0 });
    expect(d.ranges.power).toEqual({ lo: 0.5, hi: 5, lo_open: false, integer: false, unit: "" });
    expect(d.ranges.topcut.lo).toBeNull();
  });
  it("the defaults of the core are kept as given (the passes, the fields of each method, the status words)", () => {
    expect((d.params.outputs as { fields: Record<string, string[]> }).fields.ok).toContain("KE");
    expect(((d.params.outputs as { status: string[] }).status)).toEqual([...STATUS_LABELS]);
    expect(METHODS).toEqual(["nn", "idw", "sk", "ok", "ik"]);
  });
  it("garbage gives an empty answer, not an exception", () => {
    expect(readDefaults(null).samples.counts.used).toBe(0);
    expect(readDefaults("x").mapping).toBeNull();
    expect(readDefaults({ variogram: {} }).variogram).toBeNull();
  });
  it("the form mirrors the ranges of the core (spec_parts.RANGES)", () => {
    const r = d.ranges;
    for (const [mine, theirs] of [["power", "power"], ["disc", "disc"], ["soft_dist", "soft_dist"], ["topcut", "topcut"], ["mean", "mean"], ["threshold", "threshold"]] as const) {
      expect([mine, RANGES[mine].lo, RANGES[mine].hi, !!RANGES[mine].integer]).toEqual([mine, r[theirs].lo, r[theirs].hi, r[theirs].integer]);
    }
  });
  it("the query: the domain values joined by «;», «без весов» is an empty `weights`", () => {
    const p = defaultsPath({ model_id: 7, set_id: 3, table: "12", field: "AU", block_domain: "ZONE", domain_table: "14", domain_field: "ZONE", domain_values: ["окисл", "перех"], weights: "", variogram_id: 21, method: "ok" });
    const q = new URLSearchParams(p.split("?")[1]);
    expect(p.startsWith("/geostat/estimate/defaults?")).toBe(true);
    expect(q.get("domain_values")).toBe("окисл;перех");
    expect(q.get("weights")).toBe("");
    expect(q.get("variogram_id")).toBe("21");
    expect(defaultsPath({ model_id: 7, set_id: 3, table: "12", field: "AU" }).includes("weights")).toBe(false);
  });
});

describe("the rows and the plans", () => {
  it("GET /estimations", async () => {
    const rows = await loadEstimations();
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ id: 41, name: "AU · OK · окисл", model_id: 7, method: "ok", prefix: "AU_OK_", label: "окисл", version: 2, stale: false, plans: [51] });
    expect(rows[0].last_run?.by_status).toEqual([1100, 60, 35, 5]);
  });
  it("readRow: a method the core does not know becomes OK, a stale row keeps its reason", () => {
    expect(readRow({ id: 1, method: "xx", stale: true, stale_reason: "изменена таблица «Композиты»" })).toMatchObject({ method: "ok", stale: true, stale_reason: "изменена таблица «Композиты»", last_run: null });
  });
  it("GET /estimations/{id}: the parameters as stored and the fields the row writes", async () => {
    const e = await loadEstimation(41);
    expect(e.version).toBe(2);
    expect((e.meta.samples as { field: string }).field).toBe("AU");
    expect(e.columns.map((c) => c.name)).toContain("AU_OK_EST");
    expect(e.fields_exist).toEqual(["AU_OK_EST"]);
    expect(core.sent.some((r) => r.path === "/geostat/estimations/41")).toBe(true);
  });
  it("GET /estimation-plans", async () => {
    expect((await loadPlans())[0]).toMatchObject({ id: 51, model_id: 7, items: [41, 42] });
  });
  it("the events: the estimations and the plans are read again, a simulation is not", () => {
    expect(isEstimationEvent(JSON.stringify({ kind: "estimation", id: 41 }))).toBe(true);
    expect(isEstimationEvent(JSON.stringify({ kind: "estimation_plan", id: 51 }))).toBe(true);
    expect(isEstimationEvent(JSON.stringify({ kind: "simulation", id: 1 }))).toBe(false);
    expect(isEstimationEvent("")).toBe(true);
  });
});

describe("the run and the refusals", () => {
  it("the result of the job as the core wrote it", () => {
    const r = readRun(A.run_result.body)!;
    expect(r).toMatchObject({ model_id: 7, version: 5, plan_id: 51 });
    expect(r.fields).toHaveLength(9);
    expect(r.rows[1].counts.by_status).toEqual([180, 1020, 0, 0]);
    expect(r.log[0]).toContain("вырожденная система");
    expect(readRun({})).toBeNull();
  });
  it("a refusal keeps the path of the body", () => {
    const b = A.bad_param.body;
    const e = Object.assign(new CoreError(422, b.code, b.message, b.hint), { body: b });
    expect(refusalOf(e)).toEqual({ code: "bad_param", message: b.message, hint: b.hint, param: "search.passes.1.n_max" });
    expect(refusalOf(new Error("сеть")).param).toBeNull();
  });
});
