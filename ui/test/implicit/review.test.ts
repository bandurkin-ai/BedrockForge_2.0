// The review of the interface of Э19 (19.29в): `implicit.structural` (the trend of the model), the reads of the details that overtake each other,
// the reasons of the commands, the second start of a run. The answers are those of `harness.ts` (the core is a fetch stub).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { App } from "../../src/shell/app";
import { windowStore } from "../../src/shell/windows";
import { projectStore } from "../../src/modules/projects/state";
import { editOf, trendsStore } from "../../src/modules/trends/state";
import { commands } from "../../src/modules/implicit/commands";
import { runBuild } from "../../src/modules/implicit/run";
import { implicitStore, loadDetails, loadRows } from "../../src/modules/implicit/state";
import { EMPTY_FORM } from "../../src/modules/implicit/form";
import { putForm, saveWizard } from "../../src/modules/implicit/wizardState";
import { answerClaude } from "../claude-confirm";
import { ID, installCore, json, newApp, teardown, waitFor, type Core } from "./harness";

let app: App;
let core: Core;
beforeEach(() => {
  core = installCore();
  app = newApp();
  trendsStore.set({ trends: [], loaded: false, selected: null, edits: {}, dragging: null, discs: {} });
});
afterEach(teardown);

const requests = (route: string) => core.sent.filter((r) => `${r.method} ${r.path}` === route);
const ctx = { hasProject: true, hasSet: true, hasHole: false, hasObject: false, hasSection: false, editing: false, coreOnline: true };
const reason = (id: string) => app.registry.reason(id, ctx);

/** The core's answers of the trends added to the stub of the models (a structural trend is created, found by its id). */
function withTrends(): { posted: Record<string, unknown>[] } {
  const posted: Record<string, unknown>[] = [];
  const prev = globalThis.fetch;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url).pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      if (path === "/geostat/trends" && method === "POST") {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        posted.push(body);
        return json({ id: 21, name: (body.name as string) ?? "Структурный тренд", version: 1 });
      }
      return prev(url, init);
    }),
  );
  return { posted };
}

describe("implicit.structural", () => {
  it("is bound: a project is enough to ask, no word of a stage", () => {
    expect(reason("implicit.structural")).toBeNull();
    expect(commands.find((c) => c.id === "implicit.structural")?.stage).toBeUndefined();
  });
  it("with the source and the model: trend.save of a structural trend, then PUT /models/{id} {trend_id} over the stored version", async () => {
    await loadRows();
    const t = withTrends();
    const r = await app.registry.execute("implicit.structural", { model_id: ID.model, name: "Складка", set_id: ID.set, table: "Замеры", alpha: "ALPHA", dipdir: "DIPDIR" }, "user");
    expect(r.ok, r.result).toBe(true);
    expect(t.posted).toHaveLength(1);
    expect(t.posted[0]).toMatchObject({ name: "Складка", kind: "structural", source: { type: "measurements", set_id: ID.set, table: "Замеры", alpha: "ALPHA", dipdir: "DIPDIR" } });
    const put = requests(`PUT /implicit/models/${ID.model}`);
    expect(put).toHaveLength(1);
    expect(put[0].body).toMatchObject({ trend_id: 21, expected_version: 3 });
    expect(r.result).toContain("тренд №21 задан модели");
  });
  it("a wireframe is the source too; without a model the trend is saved and the answer says where to choose it", async () => {
    const t = withTrends();
    const r = await app.registry.execute("implicit.structural", { wireframe_id: ID.wire }, "user");
    expect(r.ok, r.result).toBe(true);
    expect(t.posted[0]).toMatchObject({ kind: "structural", source: { type: "mesh", wireframe_id: ID.wire } });
    expect(requests(`PUT /implicit/models/${ID.model}`)).toHaveLength(0);
    expect(r.result).toContain("Модель не выбрана");
  });
  it("a wrong source is the refusal of trend.save: nothing is written to the model", async () => {
    await loadRows();
    withTrends();
    const r = await app.registry.execute("implicit.structural", { model_id: ID.model, set_id: ID.set, table: "Замеры", alpha: "A", dipdir: "B", radius: 5 }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("R_тр");
    expect(requests(`PUT /implicit/models/${ID.model}`)).toHaveLength(0);
  });
  it("without a complete source: the window «Тренд» of a new structural trend, the set of the data of the model and the table put in", async () => {
    await loadRows();
    const r = await app.registry.execute("implicit.structural", { model_id: ID.model, table: "Замеры" }, "user");
    expect(r.ok, r.result).toBe(true);
    const w = windowStore.get().windows.find((x) => x.kind === "trend");
    expect(w).toBeDefined();
    expect(editOf(w!.id)).toMatchObject({ trendId: null, kind: "structural", structural: { type: "measurements", setId: ID.set, table: "Замеры" } });
    expect(r.result).toContain("станет трендом модели");
    expect(requests("POST /geostat/trends")).toHaveLength(0);
  });
  it("the trend saved in that window becomes the trend of the model", async () => {
    await loadRows();
    await app.registry.execute("implicit.structural", { model_id: ID.model }, "user");
    const w = windowStore.get().windows.find((x) => x.kind === "trend")!;
    trendsStore.set((s) => ({ edits: { ...s.edits, [w.id]: { ...s.edits[w.id], trendId: 21 } } }));
    await waitFor(() => requests(`PUT /implicit/models/${ID.model}`).length === 1);
    expect(requests(`PUT /implicit/models/${ID.model}`)[0].body).toMatchObject({ trend_id: 21 });
  });
});

describe("loadDetails", () => {
  it("a read that a newer read of the same model overtook answers with the newer one, not null", async () => {
    const prev = globalThis.fetch;
    let n = 0;
    let release: () => void = () => undefined;
    const gate = new Promise<void>((r) => (release = r));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (new URL(url).pathname.endsWith(`/implicit/models/${ID.model}`) && !init?.method && ++n === 1) await gate;
        return prev(url, init);
      }),
    );
    const first = loadDetails(ID.model);
    const second = loadDetails(ID.model);
    const b = await second;
    release();
    const a = await first;
    expect(b).not.toBeNull();
    expect(a).toBe(b);
    expect(implicitStore.get().details[ID.model]).toBe(b);
  });
  it("null — only when another project was opened meanwhile", async () => {
    const prev = globalThis.fetch;
    let release: () => void = () => undefined;
    const gate = new Promise<void>((r) => (release = r));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (new URL(url).pathname.endsWith(`/implicit/models/${ID.model}`)) await gate;
        return prev(url, init);
      }),
    );
    const read = loadDetails(ID.model);
    projectStore.set({ current: { uid: "p2", name: "Другой" } as never, version: 1 });
    release();
    expect(await read).toBeNull();
  });
});

describe("the reasons of the commands", () => {
  it("no models in the project: the commands of a model say so", async () => {
    core.models.length = 0;
    await loadRows();
    for (const id of ["implicit.update", "implicit.check", "implicit.support", "implicit.compare_holes", "implicit.to_wireframe", "implicit.delete"]) expect(reason(id), id).toContain("нет неявных моделей");
    expect(reason("implicit.new")).toBeNull();
  });
  it("the model is being built", async () => {
    core.models[0] = { ...core.models[0], state: "building" } as never;
    await loadRows();
    for (const id of ["implicit.update", "implicit.check", "implicit.support", "implicit.compare_holes", "implicit.to_wireframe", "implicit.delete"]) expect(reason(id), id).toContain("сейчас строится");
  });
  it("the model is not built: «Обновить» builds it, the commands over the results wait", async () => {
    core.models[0] = { ...core.models[0], stats: null, outputs: 0 } as never;
    await loadRows();
    for (const id of ["implicit.check", "implicit.support", "implicit.compare_holes", "implicit.to_wireframe"]) expect(reason(id), id).toContain("ещё не построена");
    expect(reason("implicit.update")).toBeNull();
    expect(reason("implicit.delete")).toBeNull();
  });
  it("a built model that is ready: no reason; a project for reading only keeps its own", async () => {
    await loadRows();
    for (const id of ["implicit.update", "implicit.check", "implicit.delete"]) expect(reason(id), id).toBeNull();
    app.store.set({ projectReadOnly: true });
    expect(reason("implicit.update")).toBe("Проект открыт только для чтения");
  });
});

describe("a second start", () => {
  it("«Обновить» of a model that is being run is refused with the words, the first run goes on", async () => {
    await loadRows();
    const first = app.registry.execute("implicit.update", { model_id: ID.model }, "claude");
    await answerClaude(true, "implicit.update"); // 23.15: a card of the chat for each start
    const secondRun = app.registry.execute("implicit.update", { model_id: ID.model }, "claude");
    await answerClaude(true, "implicit.update");
    const second = await secondRun;
    expect(second.ok).toBe(false);
    expect(second.result).toContain("уже строится");
    expect((await first).ok).toBe(true);
    expect(requests(`POST /implicit/models/${ID.model}/update`)).toHaveLength(1);
    await expect(runBuild(ID.model)).resolves.toContain("обновлена"); // the run is over: the next one is free
  });
  it("the save of the wizard that is being written is not started twice", async () => {
    putForm({ ...EMPTY_FORM, name: "Новая", box: ["0", "0", "0", "10", "10", "10"] });
    const first = saveWizard();
    await expect(saveWizard()).rejects.toMatchObject({ code: "busy" });
    await first;
    expect(requests("POST /implicit/models")).toHaveLength(1);
  });
});
