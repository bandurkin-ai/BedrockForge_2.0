// The numbers of the search ellipsoid, the block and the probes of the preview in 3D (Э18, 18.21; ТЗ 8.8.4) on the REAL answers of the core
// (`contract_answers.json`: `preview`, `defaults`): the ellipsoid of the pass from the form of step 4 (by hand, «по модели»), its radii × the factor
// of the pass, the probes by the weight λ (a negative one apart, the ones cut off in Slate), the box of the block, what the command shows.
import { describe, expect, it } from "vitest";
import { readDefaults } from "../../src/modules/estimation/api";
import { EMPTY_FORM } from "../../src/modules/estimation/form";
import { EMPTY_SEARCH, hasVariogram, newPass } from "../../src/modules/estimation/formSearch";
import { buildBox, buildProbes, chosenPass, EMPTY_SCENE, formPose, numberOf, probesOf, shownText, viewOf, wizardPose } from "../../src/modules/estimation/preview";
import { readPreview } from "../../src/modules/estimation/previewApi";
import { weightColor } from "../../src/modules/estimation/inspect/view";
import { A } from "./harness";

const ANSWER = readPreview(A.preview.body);
const DEFAULTS = readDefaults(A.defaults.body);
const form = (search: Partial<typeof EMPTY_SEARCH> = {}, variogramId: number | null = 3) => ({ ...EMPTY_FORM, variogramId, search: { ...EMPTY_SEARCH, ...search } });
const wizard = (f = form(), preview = ANSWER) => ({
  form: f,
  defaults: { phase: "ok" as const, key: "", seq: 1, data: DEFAULTS, error: null },
  preview: { phase: "ok" as const, at: ["4", "4", "7"] as [string, string, string], seq: 1, data: preview, error: null, stale: false },
});

describe("the numbers typed", () => {
  it("a comma is a point; empty or not a number is none", () => {
    expect(numberOf("0,5")).toBe(0.5);
    expect(numberOf(" 12 ")).toBe(12);
    expect(numberOf("")).toBeNull();
    expect(numberOf("1e")).toBeNull();
    expect(numberOf("12,")).toBe(12);
  });
});

describe("the ellipsoid from the form of step 4", () => {
  it("«по модели» is what the core took (`defaults.search`): the answer of the preview has the very same ellipsoid in the first pass", () => {
    const pose = formPose(form(), DEFAULTS);
    expect(pose).toEqual({ ang1: 0, ang2: 0, ang3: 0, r1: 60, r2: 60, r3: 30 });
    const e = ANSWER.ellipsoid!;
    expect(pose).toMatchObject({ ang1: e.ang1, ang2: e.ang2, ang3: e.ang3, r1: e.r1, r2: e.r2, r3: e.r3 });
  });
  it("by hand: the angles and the radii typed; r2 and r3 without a value are r1, an empty angle is 0", () => {
    const f = form({ from: "manual", ang: ["35", "-10", ""], r: ["40,5", "", "20"] });
    expect(formPose(f, null)).toEqual({ ang1: 35, ang2: -10, ang3: 0, r1: 40.5, r2: 40.5, r3: 20 });
  });
  it("by hand and not drawable now (r1 empty, a half-typed number, a radius not positive): none, the layer keeps what it has", () => {
    expect(formPose(form({ from: "manual", r: ["", "", ""] }), null)).toBeNull();
    expect(formPose(form({ from: "manual", r: ["1e", "", ""] }), null)).toBeNull();
    expect(formPose(form({ from: "manual", r: ["40", "x", ""] }), null)).toBeNull();
    expect(formPose(form({ from: "manual", r: ["40", "", ""], ang: ["abc", "", ""] }), null)).toBeNull();
    expect(formPose(form({ from: "manual", r: ["0", "", ""] }), null)).toBeNull();
  });
  it("a trend and a model without defaults: the form does not say, the ellipsoid of the last preview is drawn", () => {
    expect(formPose(form({ from: "trend", trendId: 1 }), DEFAULTS)).toBe("answer");
    expect(formPose(form(), null)).toBe("answer");
    expect(formPose(form(), { search: { from: "model", ang1: 0, ang2: 0, ang3: 0, r1: 60, r2: null, r3: 30 } })).toBe("answer");
  });
  it("the variogram of the form: one model, or per threshold for IK", () => {
    expect(hasVariogram(form())).toBe(true);
    expect(hasVariogram(form({}, null))).toBe(false);
    expect(hasVariogram({ ...form({}, null), method: "ik", ikModels: "per", variogramIds: [null, 4] })).toBe(true);
    expect(hasVariogram({ ...form({}, null), method: "ik", ikModels: "per", variogramIds: [null, null] })).toBe(false);
  });
});

describe("the pass", () => {
  it("the one asked, else the one that gave the estimate, else the first; within the passes there are", () => {
    expect(chosenPass(null, ANSWER, 3)).toBe(1);
    expect(chosenPass(2, ANSWER, 3)).toBe(2);
    expect(chosenPass(9, ANSWER, 3)).toBe(3);
    expect(chosenPass(null, { pass: 2, ellipsoid: null }, 3)).toBe(2);
    expect(chosenPass(null, { pass: null, ellipsoid: null }, 3)).toBe(1);
    expect(chosenPass(0, ANSWER, 3)).toBe(1);
  });
  it("the radii are × the factor of the pass: the passes of the core 1, 2, 3 give 60, 120, 180", () => {
    const w = wizard();
    expect(wizardPose(w, ANSWER, null)).toEqual({ pose: { ang1: 0, ang2: 0, ang3: 0, r1: 60, r2: 60, r3: 30 }, pass: 1 });
    expect(wizardPose(w, ANSWER, 2).pose).toMatchObject({ r1: 120, r2: 120, r3: 60 });
    expect(wizardPose(w, ANSWER, 3).pose).toMatchObject({ r1: 180, r3: 90 });
  });
  it("the factor typed in the form of the pass is followed; an empty factor — none", () => {
    const f = form({ passes: [newPass(0), { ...newPass(1), factor: "1,5" }] });
    expect(wizardPose(wizard(f), ANSWER, 2).pose).toMatchObject({ r1: 90, r3: 45 });
    const bad = form({ passes: [{ ...newPass(0), factor: "" }] });
    expect(wizardPose(wizard(bad), ANSWER, null).pose).toBeNull();
  });
  it("a trend: the ellipsoid of the answer as it is, with its pass", () => {
    const w = wizard(form({ from: "trend", trendId: 1 }));
    expect(wizardPose(w, ANSWER, 2)).toEqual({ pose: { ang1: 0, ang2: 0, ang3: 0, r1: 60, r2: 60, r3: 30 }, pass: 1 });
  });
});

describe("what is shown", () => {
  it("the wizard: only while a window of the form is open, a block was shown and the layer is wanted", () => {
    const w = wizard();
    expect(viewOf(EMPTY_SCENE, w, false)).toBeNull();
    expect(viewOf({ ...EMPTY_SCENE, wizard: false }, w, true)).toBeNull();
    expect(viewOf(EMPTY_SCENE, { ...w, preview: { ...w.preview, data: null } }, true)).toBeNull();
    const v = viewOf(EMPTY_SCENE, w, true)!;
    expect(v.source).toBe("wizard");
    expect(v.centre).toEqual([500040, 6700040, 297.5]);
    expect(v.size).toEqual([10, 10, 5]);
    expect(v.pass).toBe(1);
    expect(v.answer).toBe(ANSWER);
  });
  it("a row asked for by the command wins and needs no window; its ellipsoid is the one of the core", () => {
    const v = viewOf({ ...EMPTY_SCENE, row: { id: 4, name: "Строка", answer: ANSWER } }, wizard(), false)!;
    expect(v.source).toBe("row");
    expect(v.pose).toEqual({ ang1: 0, ang2: 0, ang3: 0, r1: 60, r2: 60, r3: 30 });
  });
  it("a block with no estimate (no ellipsoid in the answer) still has its box and probes; the wizard draws the pose of the form, a row has none", () => {
    const none = { ...ANSWER, ellipsoid: null, pass: null, status: 2 };
    const v = viewOf(EMPTY_SCENE, wizard(form(), none), true)!;
    expect(v.pose).toMatchObject({ r1: 60 });
    expect(v.centre).toEqual(ANSWER.centre);
    const row = viewOf({ ...EMPTY_SCENE, row: { id: 1, name: "x", answer: none } }, wizard(), false)!;
    expect(row.pose).toBeNull();
  });
  it("the words of the command: the pass, the radii, the block, the domain, the probes", () => {
    const text = shownText(viewOf(EMPTY_SCENE, wizard(), true)!);
    expect(text).toBe("Эллипсоид поиска прохода 1: радиусы 60 × 60 × 30 м; блок 4; 4; 7, домен «окисл»; проб в оценке 24 из 147");
  });
});

describe("the probes of the preview", () => {
  const taken = ANSWER.samples.filter((s) => s.lambda !== null);
  const negative = taken.filter((s) => s.lambda! < 0);
  const b = buildProbes(ANSWER);
  it("every probe with a weight is a segment from the centre of the block to it; the others are points in Slate", () => {
    expect(taken).toHaveLength(24);
    expect(b.taken.ids).toEqual(taken.map((s) => s.i));
    expect(b.points.ids).toHaveLength(147);
    expect(b.points.taken).toBe(24);
    expect(new Set(b.points.ids).size).toBe(147);
    expect(b.origin).toEqual({ x: 500040, y: 6700040, z: 298 });
    expect(b.offset).toEqual([0, 0, -0.5]);
    expect(b.taken.positions[3]).toBeCloseTo(taken[0].xyz![0] - 500040, 4);
    expect(b.taken.positions[5]).toBeCloseTo(taken[0].xyz![2] - 298, 4);
    expect([...b.points.colors.slice(24 * 3, 24 * 3 + 3)].map((x) => +x.toFixed(4))).toEqual(weightColor(null, 1).map((x) => +x.toFixed(4)));
  });
  it("a negative weight is Copper Orange (apart from the positive ones), the heaviest positive one is Cobalt", () => {
    expect(negative).toHaveLength(4);
    const k = taken.findIndex((s) => s.lambda! < 0);
    expect([...b.taken.colors.slice(6 * k, 6 * k + 3)]).toEqual(weightColor(taken[k].lambda, b.maxWeight).map((x) => Math.fround(x)));
    expect(b.taken.colors[6 * k]).toBeGreaterThan(b.taken.colors[6 * k + 2]);
    const heavy = taken.findIndex((s) => s.lambda === Math.max(...taken.map((x) => x.lambda!)));
    expect([...b.taken.colors.slice(6 * heavy, 6 * heavy + 3)].map((x) => Math.round(x * 255))).toEqual([0x1f, 0x6b, 0xff]);
  });
  it("a line to a probe cut off by a limit (n_max), none to those outside the ellipsoid; the reach does not count the outside ones", () => {
    const reasons = new Set(ANSWER.samples.map((s) => s.reason));
    expect([...reasons].sort()).toEqual(["outside", "over_n_max", "selected"]);
    const over = ANSWER.samples.filter((s) => s.reason === "over_n_max");
    expect([...b.cut.ids].sort()).toEqual(over.map((s) => s.i).sort());
    const inside = ANSWER.samples.filter((s) => s.reason !== "outside");
    expect(b.reach[0]).toBeCloseTo(Math.min(500040, ...inside.map((s) => s.xyz![0])), 9);
  });
  it("a sample cut to t has a weight: it is a segment, not a second point", () => {
    const hy = { ...ANSWER, samples: ANSWER.samples.map((s, k) => (k === 0 ? { ...s, reason: "hy_capped" } : s)) };
    const p = probesOf(hy);
    expect(p.segments).toHaveLength(24);
    expect(p.candidates.filter((c) => c.i === ANSWER.samples[0].i)).toHaveLength(1);
    expect(buildProbes(hy).points.ids).toHaveLength(147);
  });
  it("a sample without a place is not drawn; a block with no probes has no lines", () => {
    const p = probesOf({ ...ANSWER, samples: [{ ...ANSWER.samples[0], xyz: null }] });
    expect(p.candidates).toEqual([]);
    expect(buildProbes({ ...ANSWER, samples: [] }).taken.ids).toEqual([]);
  });
});

describe("the box of the block", () => {
  it("12 edges of 2 points about the centre, float32 from the integer origin of the centre", () => {
    const b = buildBox([500040, 6700040, 297.5], [10, 10, 5]);
    expect(b.origin).toEqual({ x: 500040, y: 6700040, z: 298 });
    expect(b.positions).toHaveLength(12 * 6);
    expect(b.reach).toEqual([500035, 6700035, 295, 500045, 6700045, 300]);
    const xs = [...b.positions].filter((_, i) => i % 3 === 0);
    const zs = [...b.positions].filter((_, i) => i % 3 === 2);
    expect(Math.min(...xs)).toBe(-5);
    expect(Math.max(...xs)).toBe(5);
    expect(Math.min(...zs)).toBe(-3);
    expect(Math.max(...zs)).toBe(2);
  });
});
