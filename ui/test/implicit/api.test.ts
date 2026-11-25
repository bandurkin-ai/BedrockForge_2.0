// The readers of the client of the implicit modelling (Э19, 19.22): the forms are those of the contract (e19-api.md §3–§8), a gap of an
// answer is `null` — never 0 (8.0.1) — and a missing key is nothing, not a made-up number; the paths and the bodies of the calls.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { addGroup, createModel, loadDefaults, loadModel, loadModels, loadTrendChoices, previewPoints, readBuildResult, readGroup, readInfo, readPreview, readRow, replaceGroup, replaceModel, startBuild, startUpdate, toWireframeApi } from "../../src/modules/implicit/api";
import { A, buildResult, ID, installCore, newApp, teardown, type Core } from "./harness";

let core: Core;
beforeEach(() => {
  core = installCore();
  newApp();
});
afterEach(teardown);

const requests = (route: string) => core.sent.filter((r) => `${r.method} ${r.path}` === route);

describe("the readers", () => {
  it("a row of the list: the counts as numbers or as lists, the stats of a model never built — null", () => {
    expect(readRow({ id: 7, name: "М", version: 3, state: "ready", groups: 3, outputs: [{}, {}], stats: { points: 10, patches: 2, seconds: null } })).toMatchObject({ groups: 3, outputs: 2, stats: { points: 10, patches: 2, seconds: null }, stale: false, state: "ready" });
    expect(readRow({ id: 7, stats: null }).stats).toBeNull();
    expect(readRow({ id: 7 }).stats).toBeNull();
    expect(readRow({ id: 7, state: "building" }).state).toBe("building");
    expect(readRow({ id: 7, state: "bogus" }).state).toBe("ready");
    expect(readRow({ id: 7, stale: true, stale_reason: "изменена таблица «Литология»", source_lost: true })).toMatchObject({ stale: true, stale_reason: "изменена таблица «Литология»", source_lost: true });
  });
  it("a model: the meta as the core stores it, the outputs with the gaps kept", () => {
    const i = readInfo({ id: 7, name: "М", version: 3, meta: { kernel: "cubic" }, state: "error", error: { code: "iso_check", message: "m", hint: "h", group: 3 }, outputs: [{ wireframe_id: 301, kind: "surface", group: 1, wing: null, threshold: null, name: "п", volume: null, area: 1.5, triangles: 12 }] });
    expect(i.meta).toEqual({ kernel: "cubic" });
    expect(i.error).toEqual({ code: "iso_check", message: "m", hint: "h", group: 3 });
    expect(i.outputs[0]).toMatchObject({ wireframe_id: 301, volume: null, wing: null, threshold: null, area: 1.5 });
    expect(readInfo({ id: 1 }).error).toBeNull();
  });
  it("a group: the codes as text (a number is written as the core writes it), the parameters stay as they are", () => {
    expect(readGroup({ id: 3, name: "А", type: "domain", codes: [12, "AL"], relation: "erode", faults: [1], params: { x: 1 } })).toEqual({ id: 3, name: "А", type: "domain", codes: ["12", "AL"], relation: "erode", faults: [1], params: { x: 1 } });
    expect(readGroup({}).id).toBeNull();
  });
  it("the points of a group: the columns in the order of the core, a gap — null", () => {
    const p = readPreview({ group: 2, n: 3, truncated: true, counts: { contact: 1, inside: 1, outside: 1 }, notes: ["a"], points: { x: [1, null, 3], y: [1, 2, 3], z: [0, 0, 0], f: [30, null, -20], kind: [2, 0, 1] } });
    expect(p.points.x).toEqual([1, null, 3]);
    expect(p.points.f).toEqual([30, null, -20]);
    expect(p).toMatchObject({ n: 3, truncated: true, notes: ["a"], counts: { contact: 1, manual: 0 } });
  });
  it("the result of the job: the stats, what was taken from the earlier run, the journal, the error of the checks", () => {
    const r = readBuildResult(buildResult())!;
    expect(r).toMatchObject({ model_id: 7, version: 4, state: "ready", error: null });
    expect(r.outputs.map((o) => [o.kind, o.group])).toEqual([["surface", 1], ["body", 1], ["body", null]]);
    expect(r.stats).toMatchObject({ points: 41230, patches: 57, max_residual: 3.2e-9, reused: { patches: 0, bricks: 0 } });
    expect(readBuildResult(buildResult({ state: "error", error: { code: "iso_check", message: "m", hint: "h", group: 3 } }))!.error).toMatchObject({ code: "iso_check", group: 3 });
    expect(readBuildResult({})).toBeNull();
    expect(readBuildResult(buildResult({ stats: { points: null } }))!.stats.points).toBeNull();
  });
});

describe("the calls", () => {
  it("the defaults and the ranges: the answer the core wrote", async () => {
    const d = await loadDefaults();
    expect(d.params.kernel).toBe(A.defaults.body.params.kernel);
    expect(d.ranges.n_min).toMatchObject({ lo: 20, hi: 500, integer: true });
    expect(d.ranges.v_min.hi).toBeNull();
  });
  it("the models: the list, one with its parameters, the trends of the list", async () => {
    expect((await loadModels()).map((m) => m.id)).toEqual([ID.model]);
    const m = await loadModel(ID.model);
    expect(m.meta.kernel).toBe("linear");
    expect((await loadTrendChoices()).map((t) => [t.id, t.kind])).toEqual([[9, "global"], [10, "structural"]]);
  });
  it("the writes carry the version: POST, PUT {expected_version}, the groups, the jobs", async () => {
    expect(await createModel({ name: "М" })).toMatchObject({ id: ID.model2, version: 1 });
    await replaceModel(ID.model, 3, { kernel: "cubic" });
    expect(requests(`PUT /implicit/models/${ID.model}`)[0].body).toEqual({ kernel: "cubic", expected_version: 3 });
    const g = await addGroup(ID.model, 4, { name: "Г", type: "domain", codes: ["GR"] });
    expect(g.group).toMatchObject({ id: 4, name: "Г", codes: ["GR"] });
    expect(requests(`POST /implicit/models/${ID.model}/groups`)[0].body).toEqual({ expected_version: 4, group: { name: "Г", type: "domain", codes: ["GR"] } });
    core.force(`PUT /implicit/models/${ID.model}/groups/4`, { status: 200, body: { id: 7, version: 5, group: { id: 4, name: "Г", type: "domain", codes: ["GR", "GRc"] } } });
    expect((await replaceGroup(ID.model, 4, 4, { name: "Г", type: "domain", codes: ["GR", "GRc"] })).group.codes).toEqual(["GR", "GRc"]);
    expect(await startBuild(ID.model, 3, 4)).toEqual(ID.job);
    expect(requests(`POST /implicit/models/${ID.model}/build`)[0].body).toEqual({ expected_version: 3, threads: 4 });
    await startUpdate(ID.model, 3);
    expect(requests(`POST /implicit/models/${ID.model}/update`)[0].body).toEqual({ expected_version: 3 });
  });
  it("the preview and «В каркас»", async () => {
    core.force(`POST /implicit/models/${ID.model}/points/preview`, { status: 200, body: { group: 2, n: 1, truncated: false, counts: {}, notes: [], points: { x: [1], y: [2], z: [3], f: [0], kind: [0] } } });
    expect((await previewPoints(ID.model, { group_id: 2, limit: 100 })).n).toBe(1);
    expect(requests(`POST /implicit/models/${ID.model}/points/preview`)[0].body).toEqual({ group_id: 2, limit: 100 });
    core.force(`POST /implicit/models/${ID.model}/to_wireframe`, { status: 200, body: { wireframe_id: 340, name: "копия" } });
    expect(await toWireframeApi(ID.model, 302, "копия")).toEqual({ wireframe_id: 340, name: "копия" });
    expect(requests(`POST /implicit/models/${ID.model}/to_wireframe`)[0].body).toEqual({ wireframe_id: 302, name: "копия" });
  });
  it("a refusal of the core keeps its words, its place and its hint (§7)", async () => {
    core.force(`POST /implicit/models/${ID.model}/build`, A.refusals.model_building);
    await expect(startBuild(ID.model, 3)).rejects.toMatchObject({ code: "model_building", status: 409, message: A.refusals.model_building.body.message, hint: A.refusals.model_building.body.hint });
    core.force("POST /implicit/models", A.bad_param);
    await expect(createModel({})).rejects.toMatchObject({ code: "bad_param", body: expect.objectContaining({ param: "pou.n_min" }) });
  });
});
