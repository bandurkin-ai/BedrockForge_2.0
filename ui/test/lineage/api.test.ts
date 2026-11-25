// The client of the graph of dependencies (Э18, 18.Г3) on the answers of the real core (`core/tests/data/lineage/answers.json`):
// the states and their words, the chain of the causes, the impact, the origin, the plan and the start of «Перестроить с
// зависимыми», the refusals in Russian. The shapes are those the core answers, not copies of the docs.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { CoreError } from "../../src/shell/api";
import { chainOf, getImpact, getOrigin, kindTitle, lostOf, nodeKey, planRebuild, postPin, postStatus, readOrigin, readStatus, startRebuild, stateTitle, STATE_TITLES, STATES } from "../../src/modules/lineage/api";
import { A, installCore, newApp, R, S, teardown, type Core } from "./harness";

let core: Core;
beforeEach(() => {
  core = installCore();
  newApp();
});
afterEach(teardown);

const requests = (route: string) => core.sent.filter((r) => `${r.method} ${r.path}` === route);

describe("the states", () => {
  it("every state the core tells has a word in Russian", () => {
    const told = new Set<string>();
    for (const part of [S, R]) for (const [name, a] of Object.entries(part as Record<string, { status?: number; body?: { items?: { state: string }[] } }>)) if (name.startsWith("status_") && a.status === 200) for (const i of a.body!.items!) told.add(i.state);
    expect([...told].sort()).toEqual(["current", "deleted", "pinned", "source_lost", "stale"]);
    for (const s of told) expect(STATE_TITLES[s as keyof typeof STATE_TITLES]).toBeTruthy();
    expect(STATES).toHaveLength(6);
    expect(STATE_TITLES).toMatchObject({ current: "актуален", stale: "устарел", source_lost: "источник удалён", pinned: "закреплён" });
  });
  it("the reason is a chain from the nearest input to the source; the tooltip puts one cause on a line", () => {
    const n4 = readStatus(S.status_stale.body.items[5]);
    expect(n4).toMatchObject({ kind: "object:stub", name: "Узел 4", state: "stale", changed: false });
    expect(chainOf(n4.reason)).toEqual(["stub «Узел 3»", "stub «Узел 2»", "stub «Узел 1»", "изменено: stub «Источник»"]);
    expect(stateTitle(n4)).toBe("устарел\nstub «Узел 3»\n← stub «Узел 2»\n← stub «Узел 1»\n← изменено: stub «Источник»");
    expect(chainOf(null)).toEqual([]);
    expect(stateTitle(readStatus(S.status_current.body.items[0]))).toBe("актуален");
  });
  it("a pinned object whose inputs changed says so; the object built from it is current", () => {
    const [, n1, n2] = S.status_pinned.body.items.map(readStatus);
    expect(n1).toMatchObject({ state: "pinned", changed: true });
    expect(stateTitle(n1)).toBe("закреплён (входы изменились)\nизменено: stub «Источник»");
    expect(n2.state).toBe("current");
  });
  it("a lost source is named in every cause along the chain", () => {
    const lost = S.status_source_lost.body.items.map(readStatus);
    expect(lost[0].state).toBe("deleted");
    expect(lost[5]).toMatchObject({ name: "Узел 4", state: "source_lost" });
    expect(chainOf(lost[5].reason).at(-1)).toBe("источник удалён: stub «Источник»");
  });
  it("the kinds are called as the core calls them in the causes; an unknown object kind is its own name", () => {
    expect(kindTitle("dh_table")).toBe("таблица");
    expect(kindTitle("object:variogram_model")).toBe("модель вариограммы");
    expect(kindTitle("bm_field")).toBe("поле блочной модели");
    expect(kindTitle("object:stub")).toBe("stub");
    expect(nodeKey({ kind: "bm_field", id: 3, sub: "au" })).toBe("bm_field:3:au");
    expect(nodeKey({ kind: "dh_table", id: 3 })).toBe("dh_table:3:");
  });
});

describe("POST /lineage/status", () => {
  it("answers in the order asked; the nodes sent are `{kind, id}` (sub only for a field)", async () => {
    const nodes = R.nodes as { kind: string; id: number }[];
    core.world.status = "status_stale";
    const got = await postStatus([...nodes, { kind: "bm_field", id: 7, sub: "au" }]);
    expect(requests("POST /lineage/status")).toHaveLength(1);
    expect(requests("POST /lineage/status")[0].body.nodes).toEqual([...nodes, { kind: "bm_field", id: 7, sub: "au" }]);
    expect(got.map((s) => s.state)).toEqual(["stale", "stale", "stale", "current", "deleted"]);
    expect(got[2]).toMatchObject({ kind: "object:variogram_model", name: "Модель AU", reason: "изменены траектории" });
  });
  it("more than 5000 nodes go in several requests, in one order", async () => {
    const nodes = Array.from({ length: 5001 }, (_, i) => ({ kind: "dh_table", id: i + 1 }));
    const got = await postStatus(nodes);
    expect(requests("POST /lineage/status").map((r) => (r.body.nodes as unknown[]).length)).toEqual([5000, 1]);
    expect(got).toHaveLength(5001);
    expect(got[5000].id).toBe(5001);
  });
  it("a refusal of the core is an error with its code and hint", async () => {
    core.force("POST /lineage/status", { status: 422, body: S.status_bad_kind.body });
    const e = await postStatus([{ kind: "table", id: 1 }]).catch((x: unknown) => x);
    expect(e).toBeInstanceOf(CoreError);
    expect((e as CoreError).code).toBe("lineage_bad_kind");
    expect((e as CoreError).hint).toBeTruthy();
  });
});

describe("the impact and the origin", () => {
  it("the impact: the total, the counts by kind, the dependents nearest first with their depth", async () => {
    const i = await getImpact({ kind: "object:stub", id: 2 });
    expect(i).toMatchObject({ total: 5, truncated: false, byKind: { "object:stub": 4, "object:report_stub": 1 } });
    expect(i.nodes.map((n) => [n.name, n.depth])).toEqual([["Узел 2", 1], ["Ветка M", 1], ["Узел 3", 2], ["Отчёт", 3], ["Узел 4", 3]]);
    expect(requests("GET /lineage/impact")[0].query).toBe("?kind=object%3Astub&id=2");
  });
  it("nothing is built from a leaf: total 0", async () => {
    expect(await getImpact({ kind: "object:stub", id: 6 })).toEqual({ total: 0, byKind: {}, nodes: [], truncated: false });
  });
  it("a node that is not there is 404 with a hint", async () => {
    const e = (await getImpact({ kind: "object:stub", id: 424242 }).catch((x: unknown) => x)) as CoreError;
    expect(e).toMatchObject({ status: 404, code: "lineage_node_not_found" });
    expect(e.hint).toBeTruthy();
  });
  it("the origin: the inputs with the version they had and have, the state of each", async () => {
    const o = await getOrigin({ kind: "object:stub", id: 3 });
    expect(o.node).toMatchObject({ name: "Узел 2", state: "stale" });
    expect(o.inputs).toHaveLength(1);
    expect(o.inputs[0]).toMatchObject({ kind: "object:stub", id: 2, name: "Узел 1", fp: "1", fpNow: "1", state: "stale", reason: "изменено: stub «Источник»" });
  });
  it("the real chain: the model of variography was built from the table of composites and the set (versions not kept → null)", () => {
    const real = R.origin_model_stale.body;
    const o = readOrigin(real);
    expect(o.inputs.map((i) => [i.kind, i.name, i.fp])).toEqual([["dh_table", "Композиты 2 м", null], ["dh_set", "Скважины", null]]);
    expect(o.inputs[0].fpNow).toBe("<token>:1");
    expect(o.inputs[0].state).toBe("stale");
    expect(o.inputs[1].state).toBe("current");
  });
});

describe("«Закрепить»", () => {
  it("sends the node and the flag, answers the state of the node", async () => {
    const s = await postPin({ kind: "object:stub", id: 2 }, true);
    expect(requests("POST /lineage/pin")[0].body).toEqual({ node: { kind: "object:stub", id: 2 }, pinned: true });
    expect(s).toMatchObject({ state: "pinned", changed: true, name: "Узел 1" });
    expect((await postPin({ kind: "object:stub", id: 2 }, false)).state).toBe("stale");
  });
});

describe("«Перестроить с зависимыми»", () => {
  it("the dry run is the plan: the order, what a job makes with another, what is left out and why", async () => {
    const p = await planRebuild({ kind: "object:stub", id: 2 });
    expect(requests("POST /lineage/rebuild")[0].body).toEqual({ node: { kind: "object:stub", id: 2 }, with_dependents: true, drop_lost: false, dry_run: true });
    expect(p.plan.map((i) => i.name)).toEqual(["Узел 1", "Узел 2", "Ветка M", "Узел 3", "Узел 4"]);
    expect(p.skipped).toEqual([expect.objectContaining({ name: "Отчёт", state: "unsupported" })]);
    expect(p.skipped[0].reason).toContain("нельзя перестроить");
  });
  it("a table and its result are made by one job: the table is `covered` by the item of the result", async () => {
    core.world.plan = "real:dry_run";
    const p = await planRebuild({ kind: "dh_result", id: 1 });
    expect(p.plan.map((i) => [i.kind, i.action, i.coveredBy])).toEqual([["dh_result", "rebuild", null], ["dh_table", "covered", 0], ["object:variogram_model", "rebuild", null]]);
    expect(p.skipped).toEqual([]);
  });
  it("the start is 202 with the job; the flags are sent as they are", async () => {
    const r = await startRebuild({ kind: "object:stub", id: 2 }, { withDependents: false, dropLost: true });
    expect(requests("POST /lineage/rebuild")[0].body).toEqual({ node: { kind: "object:stub", id: 2 }, with_dependents: false, drop_lost: true, dry_run: false });
    expect(r.job).toEqual({ source: "proj", id: 5 });
    expect(r.plan).toHaveLength(5);
    expect(r.skipped).toHaveLength(1);
  });
  it("the refusals: a pinned node (409), a kind without a maker (422), a read-only project (423) — in Russian, with a hint", async () => {
    for (const [name, status, code] of [["rebuild_pinned", 409, "lineage_node_pinned"], ["rebuild_unsupported", 422, "lineage_rebuild_unsupported"], ["rebuild_read_only", 423, "project_read_only"]] as const) {
      const a = S[name];
      expect(a.status).toBe(status);
      core.force("POST /lineage/rebuild", a);
      const e = (await startRebuild({ kind: "object:stub", id: 2 }).catch((x: unknown) => x)) as CoreError;
      expect(e).toBeInstanceOf(CoreError);
      expect(e.code).toBe(code);
      expect(e.message).toMatch(/[а-я]/i);
      expect(e.hint).toMatch(/[а-я]/i);
    }
  });
  it("`dependents_lost` is read as the names of what would be lost (the shape of composites/jobs.py `refuse_lost`)", async () => {
    core.force("POST /lineage/rebuild", {
      status: 409,
      body: { code: "dependents_lost", message: "При перестроении пропадут результаты «Урезка».", hint: "Подтвердите перестроение.", lost: [{ id: 4, op: "topcut", title: "Урезка", name: "Урезка 5 г/т" }, { kind: "column", name: "AU_CUT" }] },
    });
    const e = await startRebuild({ kind: "dh_table", id: 2 }).catch((x: unknown) => x);
    expect(lostOf(e)).toEqual([{ name: "Урезка 5 г/т", title: "Урезка" }, { name: "AU_CUT", title: "колонка" }]);
    expect(lostOf(new Error("x"))).toBeNull();
  });
  it("the file of answers is made by the generator, not by hand", () => {
    expect(A.rules).toContain("test_lineage_window_answers.py");
  });
});
