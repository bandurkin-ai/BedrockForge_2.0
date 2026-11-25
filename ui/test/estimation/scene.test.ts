// The 3D of the preview of the wizard (Э18, 18.21; ТЗ 8.8.4) on the REAL answers of the core (`contract_answers.json`: `preview`, `defaults`): the layers of
// the scene (the ellipsoid of the pass, the probes by the weight λ, the box of the block) while a window of the form is open and a block was shown;
// an edit of step 4 moves only the matrix of the ellipsoid (the same layer, no new geometry — no blink); a new block builds the probes again; the
// window closed, another project, the layer switched off — the layers go; a row asked for by the command; a lost context. A fake scene.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { closeWindow, openWindow, windowStore } from "../../src/shell/windows";
import { readDefaults } from "../../src/modules/estimation/api";
import { EMPTY_FORM } from "../../src/modules/estimation/form";
import { chooseView, previewSceneStore, resetPreviewScene } from "../../src/modules/estimation/preview";
import { readPreview } from "../../src/modules/estimation/previewApi";
import { BlockBoxLayer, BLOCK_KEY, ELLIPSOID_KEY, PreviewScene, PROBES_KEY } from "../../src/modules/estimation/scene";
import { PARAMS_WINDOW } from "../../src/modules/estimation/ParamsWindow";
import { resetWizard, setSearch, setStep, WIZARD_WINDOW, wizardStore } from "../../src/modules/estimation/wizardState";
import { buildBox } from "../../src/modules/estimation/preview";
import { A } from "./harness";
import { fakeScene, type Log } from "./previewKit";

const ANSWER = readPreview(A.preview.body);
const DEFAULTS = readDefaults(A.defaults.body);

let log: Log;
let sc: PreviewScene;
let win: string;

/** The wizard with the defaults of the core, the model of variogram and a block shown (the real answer). */
function shown(): void {
  wizardStore.set({
    form: { ...EMPTY_FORM, variogramId: 3 },
    defaults: { phase: "ok", key: "", seq: 1, data: DEFAULTS, error: null },
    preview: { phase: "ok", at: ["4", "4", "7"], seq: 1, data: ANSWER, error: null, stale: false },
  });
}

beforeEach(() => {
  windowStore.set({ windows: [] });
  resetWizard();
  resetPreviewScene();
  const f = fakeScene();
  log = f.log;
  sc = new PreviewScene(f.scene);
});
afterEach(() => {
  sc.dispose();
  windowStore.set({ windows: [] });
  resetWizard();
  resetPreviewScene();
});
const open = (kind = WIZARD_WINDOW) => openWindow(kind, "Окно", "normal", {});
const keys = () => [...log.layers.keys()].sort();

describe("the layers follow the window and the block", () => {
  it("nothing without a window of the form or without a block shown; the window with a block has the three layers", () => {
    shown();
    expect(keys()).toEqual([]);
    win = open();
    expect(keys()).toEqual([BLOCK_KEY, ELLIPSOID_KEY, PROBES_KEY].sort());
    const s = sc.stats();
    expect(s.source).toBe("wizard");
    expect(s.block).toBe(true);
    expect(s.probes).toMatchObject({ taken: 24, points: 147, excluded: true, segments: true });
    expect(s.ellipsoid!.bases).toHaveLength(1);
    expect(s.pose).toEqual({ ang1: 0, ang2: 0, ang3: 0, r1: 60, r2: 60, r3: 30, centre: [500040, 6700040, 297.5], pass: 1 });
  });
  it("another window moved or resized is not a reason to look again or to redraw: only the form window opening or closing is", () => {
    shown();
    win = open();
    const other = openWindow("other", "Другое", "normal", {});
    log.invalid = 0;
    log.changed = 0;
    for (let k = 0; k < 20; k++) windowStore.set((w) => ({ windows: w.windows.map((x) => (x.id === other ? { ...x, x: x.x + 1 } : x)) }));
    expect(log.invalid).toBe(0);
    expect(log.changed).toBe(0);
    closeWindow(win);
    expect(keys()).toEqual([]);
  });
  it("the window closed takes the layers away; the window «Параметры оценки» holds the same form and shows them too", () => {
    shown();
    win = open();
    expect(keys()).toHaveLength(3);
    closeWindow(win);
    expect(keys()).toEqual([]);
    expect(sc.stats()).toMatchObject({ source: null, ellipsoid: null, probes: null, block: false, pose: null });
    open(PARAMS_WINDOW);
    expect(keys()).toHaveLength(3);
  });
  it("the wizard started anew (another project) takes them away; the preview shown while the window is open brings them", () => {
    win = open();
    expect(keys()).toEqual([]);
    shown();
    expect(keys()).toHaveLength(3);
    resetWizard();
    expect(keys()).toEqual([]);
  });
  it("the select of step 4: another pass, the pass that gave the estimate, not in 3D", () => {
    shown();
    open();
    chooseView("3");
    expect(sc.stats().pose).toMatchObject({ r1: 180, r2: 180, r3: 90, pass: 3 });
    chooseView("auto");
    expect(sc.stats().pose).toMatchObject({ r1: 60, pass: 1 });
    chooseView("off");
    expect(keys()).toEqual([]);
    chooseView("auto");
    expect(keys()).toHaveLength(3);
  });
  it("a layer takes the section window and the size of the view of the scene; the box bounds are the block", () => {
    shown();
    open();
    const box = log.layers.get(BLOCK_KEY) as BlockBoxLayer;
    expect(box.bbox).toEqual([500035, 6700035, 295, 500045, 6700045, 300]);
    expect(box.origin).toEqual({ x: 500040, y: 6700040, z: 298 });
    expect(log.layers.get(PROBES_KEY)!.bbox).toHaveLength(6);
  });
});

describe("an edit of step 4 does not blink", () => {
  it("by hand: the radii and the angles typed change only the matrix of the same layer, the probes and the box stay", () => {
    shown();
    open();
    const ell = log.layers.get(ELLIPSOID_KEY);
    const probes = log.layers.get(PROBES_KEY);
    const adds = log.added.length;
    const before = sc.stats().ellipsoid!.bases[0];
    setSearch({ from: "manual", ang: ["35", "-10", "20"], r: ["43,2", "31,2", "16,8"] });
    expect(log.layers.get(ELLIPSOID_KEY)).toBe(ell);
    expect(log.layers.get(PROBES_KEY)).toBe(probes);
    expect(log.added.length).toBe(adds);
    expect(log.removed).toEqual([]);
    expect(sc.stats().pose).toMatchObject({ ang1: 35, ang2: -10, ang3: 20, r1: 43.2, r2: 31.2, r3: 16.8 });
    expect(sc.stats().ellipsoid!.bases[0]).not.toEqual(before);
    // the radius of the pass: another factor
    chooseView("2");
    expect(sc.stats().pose).toMatchObject({ r1: 86.4 });
    expect(log.removed).toEqual([]);
  });
  it("a number half typed or an empty radius keeps the pose it has (no blink to the old ellipsoid, no gap)", () => {
    shown();
    open();
    setSearch({ from: "manual", ang: ["", "", ""], r: ["50", "", ""] });
    const ell = log.layers.get(ELLIPSOID_KEY);
    expect(sc.stats().pose).toMatchObject({ r1: 50, r2: 50, r3: 50 });
    setSearch({ r: ["", "", ""] });
    expect(log.layers.get(ELLIPSOID_KEY)).toBe(ell);
    expect(sc.stats().pose).toMatchObject({ r1: 50 });
    setSearch({ r: ["6", "", ""] });
    expect(sc.stats().pose).toMatchObject({ r1: 6 });
    expect(log.removed).toEqual([]);
  });
  it("the form edited elsewhere (the step, a field outside the search) does not touch the layers", () => {
    shown();
    open();
    const adds = log.added.length;
    const ms = sc.stats().ms;
    setStep(5);
    wizardStore.set((s) => ({ form: { ...s.form, name: "Другое" } }));
    expect(log.added.length).toBe(adds);
    expect(sc.stats().ms).toBe(ms);
  });
  it("a trend: the ellipsoid of the answer stays as it was drawn", () => {
    shown();
    open();
    setSearch({ from: "trend", trendId: 1 });
    expect(sc.stats().pose).toMatchObject({ r1: 60, pass: 1 });
    expect(log.removed).toEqual([]);
  });
});

describe("a new block", () => {
  it("another answer builds the probes and the box again and puts the ellipsoid at the new centre; the preview marked «устарел» keeps the old ones", () => {
    shown();
    open();
    const probes = log.layers.get(PROBES_KEY);
    wizardStore.set((s) => ({ preview: { ...s.preview, stale: true } }));
    expect(log.layers.get(PROBES_KEY)).toBe(probes);
    const next = { ...ANSWER, centre: [500050, 6700050, 297.5] as [number, number, number], ellipsoid: { ...ANSWER.ellipsoid!, centre: [500050, 6700050, 297.5] as [number, number, number] } };
    wizardStore.set((s) => ({ preview: { ...s.preview, data: next, stale: false } }));
    expect(log.layers.get(PROBES_KEY)).not.toBe(probes);
    expect(sc.stats().pose!.centre).toEqual([500050, 6700050, 297.5]);
    expect(log.layers.get(BLOCK_KEY)!.origin).toEqual({ x: 500050, y: 6700050, z: 298 });
  });
  it("a block of the answer with no size has no box", () => {
    shown();
    open();
    wizardStore.set((s) => ({ preview: { ...s.preview, data: { ...ANSWER, size: null } } }));
    expect(keys()).toEqual([ELLIPSOID_KEY, PROBES_KEY]);
    expect(sc.stats().block).toBe(false);
  });
  it("a block with no estimate (status 2): the box, the probes and the ellipsoid of the form", () => {
    shown();
    open();
    wizardStore.set((s) => ({ preview: { ...s.preview, data: { ...ANSWER, status: 2, pass: null, ellipsoid: null } } }));
    expect(keys()).toHaveLength(3);
    expect(sc.stats().pose).toMatchObject({ r1: 60 });
  });
});

describe("a row asked for by the command", () => {
  it("the answer of the core is drawn without any window; taken away — the layers go", () => {
    previewSceneStore.set({ row: { id: 4, name: "Строка", answer: ANSWER } });
    expect(keys()).toHaveLength(3);
    expect(sc.stats().source).toBe("row");
    previewSceneStore.set({ row: null });
    expect(keys()).toEqual([]);
  });
  it("the camera is brought to the layers once per request", () => {
    shown();
    open();
    expect(log.revealed).toEqual([]);
    previewSceneStore.set((s) => ({ reveal: s.reveal + 1 }));
    expect(log.revealed).toHaveLength(1);
    expect(log.revealed[0].slice(0, 3).every((v, i) => v <= [500035, 6700035, 295][i])).toBe(true);
    chooseView("2");
    expect(log.revealed).toHaveLength(1);
  });
});

describe("the scene lost its context", () => {
  it("the layers come again from the stores", () => {
    shown();
    open();
    log.layers.clear();
    log.restore();
    expect(keys()).toHaveLength(3);
  });
  it("disposed — everything goes and nothing is drawn after", () => {
    shown();
    open();
    sc.dispose();
    expect(keys()).toEqual([]);
    previewSceneStore.set({ row: { id: 1, name: "x", answer: ANSWER } });
    expect(keys()).toEqual([]);
  });
});

describe("the cost", () => {
  it("ms of the build of the three layers for a block of 147 candidates, and of a new matrix of the ellipsoid (printed for the checkpoint)", () => {
    shown();
    open();
    const full: number[] = [];
    const edit: number[] = [];
    for (let k = 0; k < 20; k++) {
      wizardStore.set((s) => ({ preview: { ...s.preview, data: { ...ANSWER, centre: [500040 + k, 6700040, 297.5] as [number, number, number] } } }));
      full.push(sc.stats().ms);
      setSearch({ from: "manual", ang: ["", "", ""], r: [String(30 + k), "", ""] });
      edit.push(sc.stats().ms);
    }
    const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;
    console.log(`preview layers: build ${mean(full).toFixed(2)} ms, a new pose ${mean(edit).toFixed(2)} ms`);
    expect(mean(full)).toBeLessThan(50);
    expect(mean(edit)).toBeLessThan(20);
    expect(buildBox([0, 0, 0], [1, 1, 1]).positions).toHaveLength(72);
  });
});
