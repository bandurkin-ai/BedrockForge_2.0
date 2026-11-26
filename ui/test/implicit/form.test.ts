// The form of the wizard «Новая модель» (Э19, 19.22): the ranges are those of the core, the body is §3 of the contract, the words under a
// field carry the path of the body, a stored model gives its form back, the grid of the cells is counted with the defaults of the core.
import { describe, expect, it } from "vitest";
import { readDefaults } from "../../src/modules/implicit/api";
import { AXIS_MAX, bodyOf, defaultStep, EMPTY_FORM, extentOf, FALLBACK_RANGES, formOfArgs, formOfMeta, freeName, gridOf, moveGroup, newDomain, parseBox, rangesOf, stepOfParam, type GroupForm, type ModelForm } from "../../src/modules/implicit/form";
import { A, CANONICAL, NAME } from "./harness";

const D = readDefaults(A.defaults.body);
const form = (patch: Partial<ModelForm> = {}): ModelForm => ({ ...EMPTY_FORM, boundary: "box", box: ["0", "0", "-300", "1000", "800", "100"], ...patch });
const domain = (id: number, name: string, codes: string[]): GroupForm => ({ id, name, type: "domain", codes, raw: null });
const issueOf = (f: ModelForm) => {
  const r = bodyOf(f, D);
  if (r.ok) throw new Error("the form is right");
  return r.issue;
};
const bodyFor = (f: ModelForm) => {
  const r = bodyOf(f, D);
  if (!r.ok) throw new Error(`${r.issue.param}: ${r.issue.message}`);
  return r.body;
};

describe("the ranges and the defaults of the core", () => {
  it("the mirror of the ranges equals what the core says (a test of the table 8.9.4)", () => {
    for (const [key, r] of Object.entries(FALLBACK_RANGES)) expect(A.defaults.body.ranges[key], key).toMatchObject({ lo: r.lo, hi: r.hi, lo_open: r.lo_open, integer: r.integer, unit: r.unit });
  });
  it("the defaults of the answer are the numbers of 8.9.4", () => {
    expect(D.params).toMatchObject({ h_divisor: 200, levels: 2, kernel: "linear", s_contact: 0, s_grade: 0.05, cond_target: 1e8, n_patch: 1000, n_min: 100, delta: 0.25, d_max: 30, step: 5, v_min: 0, clip: "boundary" });
    expect(D.params.limits).toEqual({ groups: 50, codes: 1000, manual: 100, veins: 20 });
    expect(D.ranges.h).toMatchObject({ lo: 0.1, hi: 100 });
  });
  it("the ranges of the answer win over the mirror", () => {
    expect(rangesOf(null).h.hi).toBe(100);
    expect(rangesOf({ ...D, ranges: { ...D.ranges, h: { ...D.ranges.h, hi: 50 } } }).h.hi).toBe(50);
  });
});

describe("the body (§3)", () => {
  it("a box, the defaults of the core left to it: only what is typed goes", () => {
    const b = bodyFor(form());
    expect(b.boundary).toEqual({ box: [0, 0, -300, 1000, 800, 100] });
    expect(b.h).toBeUndefined();
    expect(b.levels).toBeUndefined();
    expect(b.kernel).toBe("linear");
    expect(b.smoothing).toEqual({ mode: "manual" });
    expect(b.data).toBeNull();
    expect(b.groups).toEqual([]);
    expect(b.post).toEqual({ simplify: false, clip: "boundary" });
    expect(b.trend_id).toBeUndefined();
    expect(b.anisotropy).toBeUndefined();
  });
  it("a wireframe under a DTM; the numbers are numbers («6,5» → 6.5)", () => {
    const b = bodyFor(form({ boundary: "wireframe", wireframeId: 50, dtmId: 41, h: "6,5", levels: "3" }));
    expect(b.boundary).toEqual({ wireframe_id: 50, dtm_id: 41 });
    expect(b.h).toBe(6.5);
    expect(b.levels).toBe(3);
  });
  it("the groups: the ids, the order by age as typed, the codes as text; the manual layers refer to a group by its id", () => {
    const b = bodyFor(form({ setId: 3, table: "12", field: "LITH", dMax: "20", step: "2,5", groups: [domain(1, "Дайка", ["DY"]), domain(2, "Песчаник", ["SS", "SSc"])], manual: [{ layerId: 77, role: "inside", group: 2 }] }));
    expect(b.data).toEqual({ set_id: 3, table: "12", field: "LITH", d_max: 20, step: 2.5 });
    expect(b.groups).toEqual([
      { id: 1, name: "Дайка", type: "domain", codes: ["DY"] },
      { id: 2, name: "Песчаник", type: "domain", codes: ["SS", "SSc"] },
    ]);
    expect(b.manual).toEqual([{ layer_id: 77, role: "inside", group: 2 }]);
  });
  it("the kernel of the finite radius, the smoothing «Авто», the trend of the list, the anisotropy by hand", () => {
    expect(bodyFor(form({ kernel: "wendland", kernelRadius: "150" })).kernel_radius).toBe(150);
    expect(bodyFor(form({ kernel: "cubic", kernelRadius: "150" })).kernel_radius).toBeUndefined();
    expect(bodyFor(form({ smoothing: "auto", cond: "1e6" })).smoothing).toEqual({ mode: "auto", cond_target: 1e6 });
    expect(bodyFor(form({ smoothing: "manual", s: "0,01" })).smoothing).toEqual({ mode: "manual", s: 0.01 });
    expect(bodyFor(form({ trend: "list", trendId: 9 })).trend_id).toBe(9);
    expect(bodyFor(form({ trend: "manual", ang: ["45", "-30", "0"], ratios: ["0,5", "0,25"] })).anisotropy).toEqual({ ang1: 45, ang2: -30, ang3: 0, ratios: [1, 0.5, 0.25] });
  });
  it("the patches and the post-processing", () => {
    const b = bodyFor(form({ nPatch: "2000", nMin: "50", delta: "0,5", vMin: "100", simplify: true, clip: "wireframe", clipId: 55 }));
    expect(b.pou).toEqual({ n_patch: 2000, n_min: 50, delta: 0.5 });
    expect(b.post).toEqual({ v_min: 100, simplify: true, clip: "wireframe", clip_id: 55 });
  });
  it("the words under a field: the path of the body, the range, the hint", () => {
    expect(issueOf(form({ box: ["", "0", "-300", "1000", "800", "100"] }))).toMatchObject({ param: "boundary.box.0" });
    expect(issueOf(form({ box: ["0", "0", "-300", "0", "800", "100"] }))).toMatchObject({ param: "boundary.box.3", code: "boundary_empty" });
    expect(issueOf(form({ boundary: "wireframe" }))).toMatchObject({ param: "boundary.wireframe_id" });
    const h = issueOf(form({ h: "500" }));
    expect(h.param).toBe("h");
    expect(h.message).toContain("вне диапазона");
    expect(h.hint).toContain("100");
    expect(issueOf(form({ levels: "2,5" }))).toMatchObject({ param: "levels", message: expect.stringContaining("целое") });
    expect(issueOf(form({ nMin: "10" }))).toMatchObject({ param: "pou.n_min" });
    expect(issueOf(form({ nPatch: "300", nMin: "400" }))).toMatchObject({ param: "pou.n_min", message: "N_min больше N_уч." });
    expect(issueOf(form({ kernel: "wendland", kernelRadius: "0" }))).toMatchObject({ param: "kernel_radius" });
    expect(issueOf(form({ smoothing: "auto", cond: "10" }))).toMatchObject({ param: "smoothing.cond_target" });
    expect(issueOf(form({ trend: "list" }))).toMatchObject({ param: "trend_id" });
    expect(issueOf(form({ trend: "manual", ratios: ["0", "1"] }))).toMatchObject({ param: "anisotropy.ratios.1" });
    expect(issueOf(form({ clip: "dtm" }))).toMatchObject({ param: "post.clip_id" });
  });
  it("the path of the refusal of the core is the same as the path of the form (the step it is on)", () => {
    expect(String(A.bad_param.body.param)).toBe("pou.n_min");
    expect(issueOf(form({ nMin: "10" })).param).toBe(A.bad_param.body.param);
    expect(stepOfParam(A.bad_param.body.param as string)).toBe(3);
    expect([stepOfParam("h"), stepOfParam("boundary.box.4"), stepOfParam("groups.2.codes.0"), stepOfParam("kernel"), stepOfParam("post.v_min"), stepOfParam(null)]).toEqual([1, 1, 2, 3, 4, 0]);
  });
  it("the groups: a name, a repeat, a code in two groups, a group without data, a code without a table", () => {
    const data = { setId: 3, table: "12", field: "LITH" };
    expect(issueOf(form({ ...data, groups: [domain(1, " ", ["DY"])] }))).toMatchObject({ param: "groups.0.name" });
    expect(issueOf(form({ ...data, groups: [domain(1, "A", ["DY"]), domain(2, "A", ["SS"])] }))).toMatchObject({ param: "groups.1.name" });
    expect(issueOf(form({ ...data, groups: [domain(1, "A", ["DY"]), domain(2, "B", ["SS", "DY"])] }))).toMatchObject({ param: "groups.1.codes.1" });
    expect(issueOf(form({ ...data, groups: [domain(1, "A", [])] }))).toMatchObject({ param: "groups.0.codes" });
    expect(bodyOf(form({ ...data, groups: [domain(1, "A", [])], manual: [{ layerId: 77, role: "contact", group: 1 }] }), D).ok).toBe(true);
    expect(issueOf(form({ groups: [domain(1, "A", ["DY"])] }))).toMatchObject({ param: "data.set_id" });
    expect(issueOf(form({ setId: 3 }))).toMatchObject({ param: "data.table" });
    expect(issueOf(form({ ...data, manual: [{ layerId: null, role: "contact", group: null }] }))).toMatchObject({ param: "manual.0.layer_id" });
    expect(issueOf(form({ ...data, groups: [domain(1, "A", ["DY"])], manual: [{ layerId: 77, role: "contact", group: 9 }] }))).toMatchObject({ param: "manual.0.group" });
  });
  it("the limits of the core: 50 groups", () => {
    const many = Array.from({ length: 51 }, (_, i) => domain(i + 1, `G${i}`, [`C${i}`]));
    expect(issueOf(form({ setId: 3, table: "12", field: "LITH", groups: many }))).toMatchObject({ param: "groups" });
  });
});

describe("a stored model", () => {
  it("the canonical form of the core gives the form back, and the form gives the same body (nothing is lost, nothing is made up)", () => {
    const f = formOfMeta(NAME, CANONICAL);
    expect(f).toMatchObject({ name: NAME, boundary: "box", dtmId: 41, setId: 3, table: "12", field: "LITH", kernel: "linear", smoothing: "manual", s: "", trend: "none", h: "", levels: "2", nPatch: "1000", dMax: "30", step: "5", clip: "boundary", simplify: false });
    expect(f.groups.map((g) => [g.id, g.name, g.codes])).toEqual([
      [1, "Дайка", ["DY"]],
      [2, "Песчаник", ["SS", "SSc"]],
      [3, "Алевролит", ["12", "AL"]],
    ]);
    const b = bodyFor(f);
    const c = CANONICAL as Record<string, unknown>;
    expect(b.name).toBe(c.name);
    expect(b.boundary).toEqual({ box: (c.boundary as { box: number[] }).box, dtm_id: 41 });
    expect(b.groups).toEqual(c.groups);
    expect(b.manual).toEqual(c.manual);
    expect(b.data).toEqual(c.data);
    expect(b.pou).toEqual(c.pou);
    expect(b.kernel).toBe("linear");
  });
  it("a group that is not a domain goes back as the core gave it", () => {
    const shell = { id: 5, name: "Au", type: "shell", codes: [], relation: "conformable", faults: [], params: { source: "composites", result_id: 31, field: "AU", transform: "linear", mode: "indicator", thresholds: [0.5, 1], p: 0.5 } };
    const f = formOfMeta("M", { ...CANONICAL, groups: [shell, ...((CANONICAL.groups as unknown[]) ?? [])] });
    const b = bodyFor(f) as { groups: unknown[] };
    expect(b.groups[0]).toEqual(shell);
  });
  it("a wireframe boundary, the structural trend, the anisotropy", () => {
    const f = formOfMeta("M", { boundary: { box: null, wireframe_id: 50, dtm_id: null }, trend_id: 10, kernel: "wendland", kernel_radius: 150, smoothing: { mode: "auto", s: null, cond_target: 1e8 }, post: { v_min: 100, simplify: true, clip: "wireframe", clip_id: 55 }, groups: [], manual: [] });
    expect(f).toMatchObject({ boundary: "wireframe", wireframeId: 50, trend: "list", trendId: 10, kernel: "wendland", kernelRadius: "150", smoothing: "auto", cond: "100000000", vMin: "100", simplify: true, clip: "wireframe", clipId: 55 });
    const g = formOfMeta("M", { anisotropy: { ang1: 45, ang2: -30, ang3: 0, ratios: [1, 0.5, 0.25] }, boundary: { box: [0, 0, 0, 1, 1, 1] } });
    expect(g).toMatchObject({ trend: "manual", ang: ["45", "-30", "0"], ratios: ["0,5", "0,25"] });
  });
});

describe("the cells of the grid (8.9.3, 8.9.4)", () => {
  const f = form();
  const e = extentOf(f, () => null)!;
  it("the step by default is the diagonal of the extent / 200 in the range of h", () => {
    expect(e).toEqual([0, 0, -300, 1000, 800, 100]);
    expect(defaultStep(e, D.params, rangesOf(D))).toBeCloseTo(Math.hypot(1000, 800, 400) / 200, 12);
    expect(defaultStep([0, 0, 0, 1, 1, 1], D.params, rangesOf(D))).toBe(0.1);
    expect(defaultStep([0, 0, 0, 1e5, 1e5, 1e5], D.params, rangesOf(D))).toBe(100);
  });
  it("the cells along an axis are a multiple of 2^L that covers the extent", () => {
    const g = gridOf(f, e, D.params, rangesOf(D))!;
    expect(g.typed).toBe(false);
    expect(g.levels).toBe(2);
    expect(g.n).toEqual([152, 120, 60]);
    expect(g.cells).toBe(152 * 120 * 60);
    expect(g.tooMany).toBe(false);
    const t = gridOf(form({ h: "10", levels: "0" }), e, D.params, rangesOf(D))!;
    expect(t).toMatchObject({ typed: true, h: 10, n: [100, 80, 40], cells: 320000 });
  });
  it("a step that makes more than 2^20 cells along an axis is told (the core refuses it)", () => {
    const big = gridOf(form({ h: "0,1", box: ["0", "0", "0", "1000000", "10", "10"] }), extentOf(form({ box: ["0", "0", "0", "1000000", "10", "10"] }), () => null)!, D.params, rangesOf(D))!;
    expect(big.n[0]).toBeGreaterThan(AXIS_MAX);
    expect(big.tooMany).toBe(true);
  });
  it("the extent of a wireframe is the wireframe's; a gap in it — no extent, no count (never 0)", () => {
    const w = form({ boundary: "wireframe", wireframeId: 50 });
    expect(extentOf(w, () => [100, 200, -50, 500, 600, 50])).toEqual([100, 200, -50, 500, 600, 50]);
    expect(extentOf(w, () => [100, 200, null, 500, 600, 50])).toBeNull();
    expect(extentOf(w, () => null)).toBeNull();
    expect(gridOf(w, null, D.params, rangesOf(D))).toBeNull();
    expect(extentOf(form({ box: ["0", "0", "0", "0", "1", "1"] }), () => null)).toBeNull();
  });
});

describe("the groups of the lithology: the order, the names", () => {
  it("the first is the youngest; a group moves by one; a new one is the oldest, with the next id", () => {
    const gs = [domain(1, "A", ["a"]), domain(2, "B", ["b"]), domain(5, "C", ["c"])];
    expect(moveGroup(gs, 2, -1).map((g) => g.name)).toEqual(["A", "C", "B"]);
    expect(moveGroup(gs, 0, -1).map((g) => g.name)).toEqual(["A", "B", "C"]);
    expect(moveGroup(gs, 2, 1).map((g) => g.name)).toEqual(["A", "B", "C"]);
    expect(newDomain(gs, "D", ["d"])).toMatchObject({ id: 6, type: "domain", codes: ["d"] });
    expect(freeName(gs)).toBe("Домен 4");
    expect(freeName([domain(1, "Домен 2", [])])).toBe("Домен 3");
  });
});

describe("the arguments of implicit.new", () => {
  it("a box as text with the minus of the print", () => {
    expect(parseBox("0; 0; −300; 1000; 800; 100")).toEqual([0, 0, -300, 1000, 800, 100]);
    expect(parseBox("0; 0; 1")).toMatchObject({ param: "boundary.box", message: "Охват: нужно шесть чисел." });
    expect(parseBox("0; 0; 0; 0; 1; 1")).toMatchObject({ param: "boundary.box.3", code: "boundary_empty" });
  });
  it("the arguments over the form: the name, the box, the step, a wireframe", () => {
    const f = formOfArgs({ name: "М", box: "0; 0; 0; 10; 10; 10", h: 0.5 }, EMPTY_FORM) as ModelForm;
    expect(f).toMatchObject({ name: "М", boundary: "box", box: ["0", "0", "0", "10", "10", "10"], h: "0,5" });
    expect(formOfArgs({ wireframe_id: 50 }, f)).toMatchObject({ boundary: "wireframe", wireframeId: 50 });
  });
});
