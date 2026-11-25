// The client of the estimation (Э18, 18.19): the readers on the real answers of the core
// (`core/tests/data/estimate/contract_answers.json`, written by `BF_REGEN_ESTIMATE=1` of 18.13) and the queries.
import { CoreError } from "../../src/shell/api";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { defaultsPath, loadEstimation, loadEstimations, loadPlans, readDefaults, readLastRun, readRow, readRun, refusalOf, METHODS, STATUS_LABELS } from "../../src/modules/estimation/api";
import { RANGES } from "../../src/modules/estimation/form";
import { isEstimationEvent } from "../../src/modules/estimation/state";
import { A, FIELD, ID, installCore, newApp, teardown, type Core } from "./harness";

let core: Core;
beforeEach(() => {
  core = installCore();
  newApp();
});
afterEach(teardown);

describe("GET /estimate/defaults", () => {
  const raw = A.defaults.body;
  const d = readDefaults(raw);
  it("the model, its domain fields with the labels, the sub-blocks", () => {
    expect(d.model).toMatchObject({ id: raw.model.id, name: raw.model.name, version: raw.model.version, rows: raw.model.rows, parents: raw.model.parents, sub: raw.model.sub, regular: true, size: raw.model.size });
    expect(d.model.domain_fields).toEqual(raw.model.domain_fields.map((f: { name: string; kind: string; labels: string[] }) => ({ ...f, values: [] })));
  });
  it("the counts, the means (a gap is null, never 0), the values of the domain", () => {
    expect(d.samples.counts).toEqual(raw.samples.counts);
    expect(d.samples.mean).toEqual(raw.samples.mean);
    expect(d.samples.domain?.values.map((v) => [v.value, v.n, v.declus])).toEqual(raw.samples.domain.values.map((v: { value: string; n: number; declus: number }) => [v.value, v.n, v.declus]));
    // a gap of the answer stays null, it is not 0
    const gap = readDefaults({ ...raw, samples: { ...raw.samples, mean: { naive: 1.5 }, domain: { ...raw.samples.domain, values: [{ value: "x", n: 3, naive: 1, declus: null }] } } });
    expect(gap.samples.mean).toEqual({ naive: 1.5, declus: null });
    expect(gap.samples.domain?.values[0].declus).toBeNull();
  });
  it("the mapping of the labels, the model of variogram, the search of the model, the ranges", () => {
    expect(d.mapping).toEqual(raw.mapping);
    expect(d.variogram).toMatchObject({ id: raw.variogram.id, scale: raw.variogram.scale, share: raw.variogram.share, warnings: raw.variogram.warnings, sha: raw.variogram.sha });
    expect(d.search).toEqual(raw.search);
    expect(d.ranges.power).toEqual(raw.ranges.power);
    expect(d.ranges.topcut?.lo ?? null).toBeNull();
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
    const p = defaultsPath({ model_id: ID.model, set_id: ID.set, table: ID.table, field: FIELD, block_domain: "ZONE", domain_table: ID.domTable, domain_field: "ZONE", domain_values: ["окисл", "перех"], weights: "", variogram_id: ID.vario, method: "ok" });
    const q = new URLSearchParams(p.split("?")[1]);
    expect(p.startsWith("/geostat/estimate/defaults?")).toBe(true);
    expect(q.get("domain_values")).toBe("окисл;перех");
    expect(q.get("weights")).toBe("");
    expect(q.get("variogram_id")).toBe(String(ID.vario));
    expect(defaultsPath({ model_id: ID.model, set_id: ID.set, table: ID.table, field: FIELD }).includes("weights")).toBe(false);
  });
});

describe("the rows and the plans", () => {
  it("GET /estimations", async () => {
    const rows = await loadEstimations();
    const raw = A.estimations.body.estimations;
    expect(rows).toHaveLength(raw.length);
    expect(rows[0]).toMatchObject({ id: raw[0].id, name: raw[0].name, model_id: raw[0].model_id, method: raw[0].method, prefix: raw[0].prefix, label: raw[0].label, version: raw[0].version, stale: false, plans: raw[0].plans });
    expect(rows[0].last_run?.by_status).toEqual(raw[0].last_run.by_status);
  });
  it("readRow: a method the core does not know becomes OK, a stale row keeps its reason", () => {
    expect(readRow({ id: 1, method: "xx", stale: true, stale_reason: "изменена таблица «Композиты»" })).toMatchObject({ method: "ok", stale: true, stale_reason: "изменена таблица «Композиты»", last_run: null });
  });
  it("GET /estimations/{id}: the parameters as stored and the fields the row writes", async () => {
    const raw = A.estimation.body;
    const e = await loadEstimation(ID.row);
    expect(e.version).toBe(raw.version);
    expect((e.meta.samples as { field: string }).field).toBe(FIELD);
    expect(e.columns.map((c) => c.name)).toEqual(raw.columns.map((c: { name: string }) => c.name));
    expect(e.columns.find((c) => c.key === "STATUS")?.description).toContain("0 — оценён");
    expect(e.fields_exist).toEqual(raw.fields_exist);
    expect(core.sent.some((r) => r.path === `/geostat/estimations/${ID.row}`)).toBe(true);
  });
  it("the stored last run: the job number, the counts, the hash of the parameters, the stages of the run", () => {
    const lr = A.estimation.body.meta.last_run;
    expect(typeof lr.job).toBe("number");
    expect(typeof lr.params).toBe("string");
    expect(readLastRun(lr)).toMatchObject({ job: lr.job, version: lr.version, params: lr.params, counts: { targets: lr.counts.targets } });
    expect(readLastRun(null)).toBeNull();
  });
  it("GET /estimation-plans", async () => {
    const raw = A.plan.body;
    expect((await loadPlans())[0]).toMatchObject({ id: raw.id, model_id: raw.model_id, items: raw.items.map((i: { id: number }) => i.id) });
  });
  it("the events: the estimations and the plans are read again, a simulation is not", () => {
    expect(isEstimationEvent(JSON.stringify({ kind: "estimation", id: ID.row }))).toBe(true);
    expect(isEstimationEvent(JSON.stringify({ kind: "estimation_plan", id: ID.plan }))).toBe(true);
    expect(isEstimationEvent(JSON.stringify({ kind: "simulation", id: 1 }))).toBe(false);
    expect(isEstimationEvent("")).toBe(true);
  });
});

describe("the run and the refusals", () => {
  it("the result of the job as the core wrote it", () => {
    const raw = A.run_result.body;
    const r = readRun(raw)!;
    expect(r).toMatchObject({ model_id: raw.model_id, version: raw.version, plan_id: raw.plan_id });
    expect(r.fields).toEqual(raw.fields);
    expect(r.rows[1].counts.by_status).toEqual(raw.rows[1].counts.by_status);
    expect(r.log).toEqual(raw.log);
    expect(r.log[0]).toContain("вырожденная система");
    expect(readRun({})).toBeNull();
  });
  it("the stages of the run (seconds) are kept, the events of the result are not needed", () => {
    const r = readRun(A.run_result.body)!;
    expect(r.stages).toEqual(A.run_result.body.stages);
    expect(readRun({ model_id: 1, fields: [] })!.stages).toEqual({});
  });
  it("a refusal keeps the path of the body", () => {
    const b = A.bad_param.body;
    const e = Object.assign(new CoreError(422, b.code, b.message, b.hint), { body: b });
    expect(refusalOf(e)).toEqual({ code: "bad_param", message: b.message, hint: b.hint, param: b.param });
    expect(refusalOf(new Error("сеть")).param).toBeNull();
  });
});
