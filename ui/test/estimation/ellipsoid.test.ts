// The command `estimate.ellipsoid {id?, show?, pass?, block?}` (Э18, 18.21; docs/progress/e18-api.md §12) and the select of step 4 on the REAL answers of the
// core (`contract_answers.json`): the ellipsoid of the wizard at the block shown, a stored row at a block named, picked or typed, the refusals with the words,
// what is taken away. The 3D is a fake scene that the lazy chunk of the layers is attached to.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { App } from "../../src/shell/app";
import { openWindow, windowStore } from "../../src/shell/windows";
import { projectStore } from "../../src/modules/projects/state";
import { pickedBlockStore } from "../../src/modules/blocks/display";
import { setCurrentScene } from "../../src/modules/scene/sceneHost";
import { readDefaults } from "../../src/modules/estimation/api";
import { EMPTY_FORM } from "../../src/modules/estimation/form";
import { previewSceneStore, resetPreviewScene } from "../../src/modules/estimation/preview";
import { readPreview } from "../../src/modules/estimation/previewApi";
import { BLOCK_KEY, ELLIPSOID_KEY, PROBES_KEY } from "../../src/modules/estimation/scene";
import { loadRows } from "../../src/modules/estimation/state";
import { StepSearch } from "../../src/modules/estimation/StepSearch";
import { WIZARD_WINDOW, wizardStore } from "../../src/modules/estimation/wizardState";
import { A, ID, installCore, newApp, teardown, type Core } from "./harness";
import { fakeScene, type Log } from "./previewKit";

const ANSWER = readPreview(A.preview.body);
const DEFAULTS = readDefaults(A.defaults.body);
const ROUTE = "POST /geostat/estimate/preview";

let app: App;
let core: Core;
let log: Log;
beforeEach(async () => {
  core = installCore();
  app = newApp();
  resetPreviewScene();
  pickedBlockStore.set({ picked: null });
  await loadRows();
});
afterEach(() => {
  setCurrentScene(null);
  resetPreviewScene();
  pickedBlockStore.set({ picked: null });
  teardown();
});

const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));
async function waitFor(cond: () => boolean, ms = 2000): Promise<void> {
  const t0 = Date.now();
  while (!cond()) {
    if (Date.now() - t0 > ms) throw new Error("the condition did not come true");
    await tick(5);
  }
}
const keys = () => [...log.layers.keys()].sort();
const exec = (args: Record<string, unknown>) => app.registry.execute("estimate.ellipsoid", args, "user");
const sent = () => core.sent.filter((r) => `${r.method} ${r.path}` === ROUTE);

/** The 3D window is up (a fake scene; the layers attach to it from their lazy chunk). */
async function scene3d(): Promise<void> {
  const f = fakeScene();
  log = f.log;
  setCurrentScene(f.scene);
  await import("../../src/modules/estimation/scene");
  await tick(20); // the chunk of the layers is attached to the scene
}
/** The wizard open, with the defaults of the core and a block shown. */
function wizardShown(): void {
  openWindow(WIZARD_WINDOW, "Оценка содержаний", "normal", {});
  wizardStore.set({
    form: { ...EMPTY_FORM, variogramId: ID.vario },
    defaults: { phase: "ok", key: "", seq: 1, data: DEFAULTS, error: null },
    preview: { phase: "ok", at: ["4", "4", "7"], seq: 1, data: ANSWER, error: null, stale: false },
  });
}

describe("the command is in the registry", () => {
  it("it is available (no more «появится на этапе»); the arguments are declared", () => {
    const ctx = { hasProject: true, hasSet: true, hasHole: false, hasObject: false, hasSection: false, editing: false, coreOnline: true };
    expect(app.registry.reason("estimate.ellipsoid", ctx)).toBeNull();
    expect(app.registry.reason("estimate.ellipsoid", { ...ctx, hasProject: false })).toBe("Откройте или создайте проект");
    expect(Object.keys(app.registry.get("estimate.ellipsoid")!.params ?? {})).toEqual(["id", "show", "pass", "block"]);
  });
});

describe("the ellipsoid of the wizard", () => {
  it("without the 3D window, without the wizard, without a block shown: the refusal says what to do", async () => {
    expect(await exec({})).toMatchObject({ ok: false, result: expect.stringContaining("3D-окно ещё не готово") });
    await scene3d();
    expect(await exec({})).toMatchObject({ ok: false, result: expect.stringContaining("Мастер «Оценка содержаний» не открыт") });
    openWindow(WIZARD_WINDOW, "Оценка содержаний", "normal", {});
    const r = await exec({});
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Блок ещё не показан");
    expect(r.result).toContain("нажмите «Показать»");
  });
  it("shows the layers of the block shown, answers with the pass, the radii and the block; a second call is the same", async () => {
    await scene3d();
    wizardShown();
    const r = await exec({});
    expect(r).toEqual({ ok: true, result: "Эллипсоид поиска прохода 1: радиусы 60 × 60 × 30 м; блок 4; 4; 7, домен «окисл»; проб в оценке 24 из 147" });
    await waitFor(() => keys().length === 3);
    expect(keys()).toEqual([BLOCK_KEY, ELLIPSOID_KEY, PROBES_KEY].sort());
    expect(log.revealed).toHaveLength(1);
    expect((await exec({})).ok).toBe(true);
  });
  it("«pass» — another pass: the radii × its factor; a pass out of 1…5 is refused", async () => {
    await scene3d();
    wizardShown();
    expect((await exec({ pass: 2 })).result).toContain("прохода 2: радиусы 120 × 120 × 60 м");
    expect(previewSceneStore.get().pass).toBe(2);
    expect(await exec({ pass: 9 })).toMatchObject({ ok: false, result: expect.stringContaining("от 1 до 5") });
    expect(await exec({ pass: 1.5 })).toMatchObject({ ok: false });
  });
  it("show: false takes the layers away and keeps them away (even for the next block) until it is asked for again", async () => {
    await scene3d();
    wizardShown();
    await exec({});
    await waitFor(() => keys().length === 3);
    expect(await exec({ show: false })).toEqual({ ok: true, result: "Эллипсоид поиска убран из 3D" });
    expect(keys()).toEqual([]);
    wizardStore.set((s) => ({ preview: { ...s.preview, data: { ...ANSWER } } }));
    expect(keys()).toEqual([]);
    expect(await exec({ show: false })).toEqual({ ok: true, result: "Эллипсоид поиска в 3D не показывался" });
    await exec({});
    expect(keys()).toHaveLength(3);
  });
  it("the wizard closed takes the layers away with it", async () => {
    await scene3d();
    wizardShown();
    await exec({});
    await waitFor(() => keys().length === 3);
    windowStore.set({ windows: [] });
    expect(keys()).toEqual([]);
  });
});

describe("a stored row", () => {
  it("at a block named: the core estimates the row as it is (`estimation_id`), the answer stays in 3D without the wizard", async () => {
    await scene3d();
    const r = await exec({ id: ID.row, block: "4; 4; 7" });
    expect(r.ok).toBe(true);
    expect(r.result).toMatch(/^Строка «.+»: Эллипсоид поиска прохода 1: радиусы 60 × 60 × 30 м; блок 4; 4; 7/);
    expect(sent()).toHaveLength(1);
    expect(sent()[0].body).toEqual({ estimation_id: ID.row, at: { ijk: [4, 4, 7] } });
    await waitFor(() => keys().length === 3);
    expect(previewSceneStore.get().row).toMatchObject({ id: ID.row });
  });
  it("without a block: the one picked in 3D (of the model of the row), else the block of the wizard that holds this row, else the words", async () => {
    await scene3d();
    const none = await exec({ id: ID.row });
    expect(none.ok).toBe(false);
    expect(none.result).toContain("Не выбран блок");
    expect(sent()).toHaveLength(0);
    const model = A.estimations.body.estimations[0].model_id;
    pickedBlockStore.set({ picked: { model, point: [0, 0, 0], status: "found", answer: { found: true, row: 744, ijk: [4, 4, 7] } as never, error: null } });
    expect((await exec({ id: ID.row })).ok).toBe(true);
    expect(sent()[0].body).toEqual({ estimation_id: ID.row, at: { row: 744 } });
    // a block picked in another model is not the block of this row
    pickedBlockStore.set({ picked: { model: model + 50, point: [0, 0, 0], status: "found", answer: { found: true, row: 5, ijk: [1, 1, 1] } as never, error: null } });
    expect((await exec({ id: ID.row })).ok).toBe(false);
    wizardStore.set({ editing: { id: ID.row, version: 1 }, preview: { ...wizardStore.get().preview, at: ["1", "2", "3"] } });
    expect((await exec({ id: ID.row })).ok).toBe(true);
    expect(sent()[1].body).toEqual({ estimation_id: ID.row, at: { ijk: [1, 2, 3] } });
  });
  it("a wrong block, a row that is not there, a refusal of the core: the words, nothing drawn", async () => {
    await scene3d();
    expect(await exec({ id: ID.row, block: "4; x; 7" })).toMatchObject({ ok: false, result: expect.stringContaining("три целых неотрицательных числа") });
    expect(await exec({ id: ID.row, block: "4; -1; 7" })).toMatchObject({ ok: false });
    expect(await exec({ id: ID.none, block: "4; 4; 7" })).toMatchObject({ ok: false, result: expect.stringContaining(`№${ID.none} не найдена`) });
    core.force(ROUTE, { status: 422, body: { code: "bad_param", message: "Блок вне модели.", hint: "Проверьте индексы.", param: "at.ijk" } });
    const r = await exec({ id: ID.row, block: "400; 4; 7" });
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Блок вне модели.");
    expect(keys()).toEqual([]);
  });
  it("show: false with the id takes the row away and leaves the wizard's layer; without the id — both", async () => {
    await scene3d();
    wizardShown();
    await exec({ id: ID.row, block: "4; 4; 7" });
    await waitFor(() => keys().length === 3);
    expect(previewSceneStore.get().row).not.toBeNull();
    await exec({ id: ID.row, show: false });
    expect(previewSceneStore.get().row).toBeNull();
    await waitFor(() => keys().length === 3); // the wizard shows its block again
    expect(previewSceneStore.get().wizard).toBe(true);
    await exec({ id: ID.row, block: "4; 4; 7" });
    await exec({ show: false });
    expect(previewSceneStore.get()).toMatchObject({ row: null, wizard: false });
    expect(keys()).toEqual([]);
  });
  it("another project: what was asked of the first does not stay", async () => {
    await scene3d();
    await exec({ id: ID.row, block: "4; 4; 7" });
    await waitFor(() => keys().length === 3);
    projectStore.set({ current: { uid: "p2", name: "Другой" } as never, version: 2 });
    expect(previewSceneStore.get().row).toBeNull();
    expect(keys()).toEqual([]);
  });
});

describe("the select of step 4", () => {
  const render = () => renderToStaticMarkup(createElement(StepSearch)).replace(/[  ]/g, " ");
  it("before a block is shown it says where to look; after — what is drawn", () => {
    wizardStore.set({ form: { ...EMPTY_FORM, variogramId: ID.vario }, defaults: { phase: "ok", key: "", seq: 1, data: DEFAULTS, error: null } });
    const html = render();
    expect(html).toContain('data-testid="est-3d-pass"');
    expect(html).toContain("Покажите блок на шаге «Предпросмотр и запуск»");
    expect(html).toContain("эллипсоид прохода 3");
    expect(html).toContain("не показывать");
    wizardStore.set((s) => ({ preview: { ...s.preview, data: ANSWER } }));
    expect(render()).toContain("Эллипсоид прохода 1 стоит в центре блока предпросмотра");
    previewSceneStore.set({ pass: 2 });
    expect(render()).toContain("Эллипсоид прохода 2 стоит в центре блока");
    previewSceneStore.set({ wizard: false });
    expect(render()).toMatch(/<option value="off" selected/);
  });
});
