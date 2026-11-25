// The states of the tree (Э18, 18.Г3; ADR 0025 §10): the asks of one moment are one request, a state is asked once, the watched
// states are read again after the events of the core and a finished job, another project starts with an empty cache. On the
// answers of the real core (the chain of stubs).
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { projectStore } from "../../src/modules/projects/state";
import { jobsStore, refresh as refreshJobs } from "../../src/modules/jobs/state";
import { nodeKey, type LNode } from "../../src/modules/lineage/api";
import { BATCH_MS, followEvents, handleCoreEvent, lineageStore, nodeOfSelected, put, REFRESH_EVENTS, REFRESH_MS, refreshAll, statusOf, summary, watch, watchedNodes } from "../../src/modules/lineage/state";
import { installCore, newApp, S, tick, teardown, waitFor, type Core } from "./harness";

let core: Core;
beforeEach(() => {
  core = installCore();
  newApp();
});
afterEach(teardown);

const node = (id: number): LNode => ({ kind: "object:stub", id });
const asked = () => core.sent.filter((r) => r.path === "/lineage/status");

describe("the asks of one moment", () => {
  it("three rows that appear together ask once, with the three nodes", async () => {
    const stops = [watch([node(1)]), watch([node(2)]), watch([node(3), node(4)])];
    expect(asked()).toHaveLength(0);
    await waitFor(() => asked().length > 0);
    await tick(BATCH_MS * 3);
    expect(asked()).toHaveLength(1);
    expect(asked()[0].body.nodes).toEqual([node(1), node(2), node(3), node(4)]);
    expect(statusOf(node(3))).toMatchObject({ name: "Узел 2", state: "current" });
    stops.forEach((s) => s());
  });
  it("a state the cache has is not asked again (a section that closes and opens)", async () => {
    const stop = watch([node(1), node(2)]);
    await waitFor(() => statusOf(node(2)) !== undefined);
    stop();
    const again = watch([node(2)]);
    await tick(BATCH_MS * 3);
    expect(asked()).toHaveLength(1);
    again();
  });
  it("a node that is watched twice is read once per refresh; it stays watched until both stop", async () => {
    const a = watch([node(2)]);
    const b = watch([node(2)]);
    expect(watchedNodes()).toEqual([node(2)]);
    a();
    expect(watchedNodes()).toEqual([node(2)]);
    b();
    expect(watchedNodes()).toEqual([]);
  });
});

describe("the states follow the project", () => {
  it("an edit of a source and a refresh: «актуален» becomes «устарел» with the chain of causes", async () => {
    const stop = watch([node(1), node(2), node(6)]);
    await waitFor(() => statusOf(node(6)) !== undefined);
    expect(["current", "current", "current"]).toEqual([1, 2, 6].map((i) => statusOf(node(i))!.state));
    core.world.status = "status_stale";
    await refreshAll();
    expect(statusOf(node(1))!.state).toBe("current");
    expect(statusOf(node(2))!.state).toBe("stale");
    expect(statusOf(node(6))).toMatchObject({ state: "stale", reason: expect.stringContaining("← изменено: stub «Источник»") });
    stop();
  });
  it("the events that can change a state read the watched nodes again, once for the events of one moment", async () => {
    const stop = watch([node(2)]);
    await waitFor(() => statusOf(node(2)) !== undefined);
    expect(REFRESH_EVENTS).toEqual(expect.arrayContaining(["lineage.changed", "objects.changed", "drillholes.changed"]));
    core.world.status = "status_stale";
    handleCoreEvent("objects.changed");
    handleCoreEvent("lineage.changed");
    handleCoreEvent("drillholes.changed");
    handleCoreEvent("something.else");
    await waitFor(() => statusOf(node(2))!.state === "stale");
    await tick(REFRESH_MS * 2);
    expect(asked()).toHaveLength(2);
    expect(asked()[1].body.nodes).toEqual([node(2)]);
    stop();
  });
  it("`version` (the panels read impact and origin again by it) moves only when a state really changed", async () => {
    const stop = watch([node(2)]);
    await waitFor(() => statusOf(node(2)) !== undefined);
    const v0 = lineageStore.get().version;
    await refreshAll(); // the core tells the same
    await refreshAll();
    expect(lineageStore.get().version).toBe(v0);
    put({ ...statusOf(node(2))! }); // the same state told again
    expect(lineageStore.get().version).toBe(v0);
    core.world.status = "status_stale";
    await refreshAll();
    expect(statusOf(node(2))!.state).toBe("stale");
    expect(lineageStore.get().version).toBe(v0 + 1);
    stop();
  });
  it("nothing watched — nothing asked after an event", async () => {
    handleCoreEvent("objects.changed");
    await tick(REFRESH_MS * 2);
    expect(asked()).toHaveLength(0);
  });
  it("a state the core just told (the pin) is in the cache at once; the others are read soon", async () => {
    const stop = watch([node(2), node(3)]);
    await waitFor(() => statusOf(node(3)) !== undefined);
    core.world.status = "status_pinned";
    put(S.pin_n1.body);
    expect(statusOf(node(2))!.state).toBe("pinned");
    await waitFor(() => asked().length === 2);
    await waitFor(() => statusOf(node(3))!.state === "current");
    stop();
  });
  it("another project starts with an empty cache and nothing watched", async () => {
    const stop = watch([node(2)]);
    await waitFor(() => statusOf(node(2)) !== undefined);
    const off = followEvents();
    projectStore.set({ current: { uid: "p2", name: "Другой" } as never, version: 2 });
    expect(lineageStore.get().statuses).toEqual({});
    expect(watchedNodes()).toEqual([]);
    off();
    stop();
  });
  it("a finished job reads the watched states again", async () => {
    const off = followEvents();
    const stop = watch([node(2)]);
    await waitFor(() => statusOf(node(2)) !== undefined);
    expect(asked()).toHaveLength(1);
    // the tab «Задания» saw the job running; the core now lists it as done (`onJobFinished` of the store of jobs)
    const running = { id: 5, source: "proj", kind: "lineage.rebuild", title: "", queue: "heavy", status: "running" as const, progress: 0, message: null, error: null, result: null, attempts: 1, max_attempts: 1, cancel_requested: false, created_at: "", started_at: null, finished_at: null, why_waiting: null };
    jobsStore.set({ jobs: [running] });
    await refreshJobs();
    await waitFor(() => asked().length >= 2);
    off();
    stop();
  });
});

describe("the summary and the object chosen in the tree", () => {
  it("counts what the tree shows: all, stale, lost, pinned", async () => {
    core.world.status = "status_stale";
    const nodes = [1, 2, 3, 4, 5, 6].map(node);
    const stop = watch(nodes);
    await waitFor(() => statusOf(node(6)) !== undefined);
    expect(summary()).toMatchObject({ total: 6, stale: 5, lost: 0, pinned: 0 });
    expect(summary().stale_names).toEqual(["Узел 1", "Узел 2", "Ветка M", "Узел 3", "Узел 4"]);
    expect(summary([node(1)])).toMatchObject({ total: 1, stale: 0 });
    stop();
  });
  it("a wireframe and a block model are nodes; a layer of lines and a raster are not computed", () => {
    expect(nodeOfSelected({ kind: "wireframe", id: 4 })).toEqual({ kind: "wireframe", id: 4 });
    expect(nodeOfSelected({ kind: "blocks", id: 7 })).toEqual({ kind: "object:block_model", id: 7 });
    expect(nodeOfSelected({ kind: "lines", id: 1 })).toBeNull();
    expect(nodeOfSelected({ kind: "raster", id: 1 })).toBeNull();
    expect(nodeOfSelected(null)).toBeNull();
    expect(nodeKey(node(2))).toBe("object:stub:2:");
  });
});
