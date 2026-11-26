// The form of the window «Отчёт по ресурсам» (Э20, 20.12; ADR 0027 решения 3–11, 29; docs/progress/e20-api.md §2, §13): the parameters of the window =
// the request the core takes (the cases of `core/tests/data/resources/params.json`, the defaults and the limits of the real `defaults`), what the
// core refuses is told in Russian with a hint in its place of the form, the flat arguments of the command and the form are the same request.
import { describe, expect, it } from "vitest";
import { validateArgs } from "../../src/shell/commands";
import { parseDecimal } from "../../src/modules/blocks/report/state";
import { DEFAULT_LIMITS, readDefaults, type ResourceParams } from "../../src/modules/resources/api";
import { commands } from "../../src/modules/resources/commands";
import {
  argsOfForm,
  EMPTY_FORM,
  elementsText,
  formOfArgs,
  formOfParams,
  issueOfRefusal,
  isIssue,
  keysText,
  paramsOfForm,
  parseCutoffList,
  parseElements,
  parseKeys,
  restrictOfForm,
  type KeyChoice,
  type ResourceForm,
} from "../../src/modules/resources/form";
import { addKey, availableKeys, decodeDrag, dropOnAvailable, dropOnChosen, encodeDrag, moveKey, removeKey } from "../../src/modules/resources/groupOps";
import { answer, FILE, PARAMS } from "./harness";

const DEFAULTS = readDefaults(answer("defaults").body);
const CASES = PARAMS.cases as Record<string, { body: Record<string, unknown>; canonical: Record<string, unknown>; resolved: Record<string, unknown> }>;

const num = (v: unknown): number | null => (typeof v === "number" ? v : typeof v === "string" ? parseDecimal(v) : null);
const unit = (u: unknown): string | undefined => (typeof u === "string" && u ? (u === "г/т" ? "g/t" : u) : undefined);

/** A request in the form the core canonicalises to: the defaults of the core (`defaults.params`) filled in, the text numbers read, the shapes of a key made one. */
function norm(params: object): Record<string, unknown> {
  const p = params as Record<string, unknown>;
  const out: Record<string, unknown> = { model_id: p.model_id };
  if (Array.isArray(p.elements) && p.elements.length) out.elements = p.elements.map((e) => (typeof e === "string" ? { field: e } : { field: (e as { field: string }).field, ...(unit((e as { unit?: unknown }).unit) ? { unit: unit((e as { unit?: unknown }).unit) } : {}) }));
  if (typeof p.cutoff_field === "string") out.cutoff_field = p.cutoff_field;
  const c = p.cutoffs ?? DEFAULTS.params.cutoffs;
  out.cutoffs = Array.isArray(c) ? c.map(num) : { from: num((c as { from: unknown }).from), to: num((c as { to: unknown }).to), step: num((c as { step: unknown }).step) };
  if (p.balance) out.balance = { cutoff: num((p.balance as { cutoff: unknown }).cutoff), ...((p.balance as { off_cutoff?: unknown }).off_cutoff !== undefined && (p.balance as { off_cutoff?: unknown }).off_cutoff !== null ? { off_cutoff: num((p.balance as { off_cutoff: unknown }).off_cutoff) } : {}) };
  if (p.density !== undefined && p.density !== null) out.density = typeof p.density === "string" && num(p.density) === null ? p.density : num(p.density);
  if (typeof p.filter_id === "number") out.filter_id = p.filter_id;
  if (p.restrict) out.restrict = p.restrict;
  if (Array.isArray(p.groups) && p.groups.length) out.groups = p.groups.map((g) => (typeof g === "string" ? g : (g as { kind: string; field?: string }).kind === "bench" ? { kind: "bench" } : (g as { field: string }).field));
  if (p.bench && (p.groups as unknown[] | undefined)?.some((g) => typeof g === "object" && (g as { kind?: string }).kind === "bench")) {
    const b = p.bench as Record<string, unknown>;
    out.bench = { h: num(b.h) ?? 10, ...(num(b.z0) !== null ? { z0: num(b.z0) } : {}), ...(num(b.z_min) !== null ? { z_min: num(b.z_min) } : {}), ...(num(b.z_max) !== null ? { z_max: num(b.z_max) } : {}) };
  } else if (Array.isArray(p.groups) && p.groups.some((g) => typeof g === "object" && g !== null && (g as { kind?: string }).kind === "bench")) out.bench = { h: 10 };
  out.curve = typeof p.curve === "number" ? p.curve : DEFAULTS.params.curve;
  if (typeof p.template_id === "string") out.template_id = p.template_id;
  return JSON.parse(JSON.stringify(out)) as Record<string, unknown>;
}

const built = (f: ResourceForm): ResourceParams => {
  const r = paramsOfForm(f, DEFAULTS.limits);
  if (!r.ok) throw new Error(JSON.stringify(r.issue));
  return r.built.params;
};
const issueOf = (f: Partial<ResourceForm>): { field: string; message: string; hint: string } => {
  const r = paramsOfForm({ ...EMPTY_FORM, modelId: 12, ...f }, DEFAULTS.limits);
  if (r.ok) throw new Error("accepted");
  return r.issue;
};

describe("the parameters of the window are the request the core takes", () => {
  for (const [name, c] of Object.entries(CASES)) {
    it(`${name}: the body → the form → the same request`, () => {
      expect(norm(built(formOfParams(c.body)))).toEqual(norm(c.body));
    });
    it(`${name}: the params the core resolved (a saved report) fill the form, and it asks for the same`, () => {
      // the canonical `unit` of the core is `g/t`: the form shows «г/т»; the request is the same after the core reads it
      expect(norm(built(formOfParams(c.resolved)))).toEqual(norm(c.resolved));
    });
    it(`${name}: the arguments of the command are the same request`, () => {
      const f = formOfParams(c.body);
      const a = argsOfForm(f, false);
      expect(a.ok).toBe(true);
      if (!a.ok) return;
      expect(validateArgs(commands[0], a.args)).toBeNull(); // the arguments are flat words: the registry takes them
      const back = formOfArgs(a.args);
      expect(back.ok).toBe(true);
      if (back.ok) expect(norm(built(back.form))).toEqual(norm(built(f)));
    });
  }

  it("the empty form of a model asks for exactly the defaults of the core", () => {
    const p = built({ ...EMPTY_FORM, modelId: 12 });
    expect(p).toEqual({ model_id: 12, cutoffs: DEFAULTS.params.cutoffs, curve: DEFAULTS.params.curve });
    // «без борта» is the row of all the blocks: `null`, not 0 (8.0.1)
    expect(p.cutoffs).toEqual([null]);
    expect(EMPTY_FORM.benchH).toBe(String((DEFAULTS.params.bench as { h: number }).h));
    expect(DEFAULT_LIMITS).toEqual({ elements: 20, cutoffs: 200, group_keys: 3, benches: 1000, curve: 500 });
    expect(DEFAULTS.limits).toMatchObject({ elements: 20, cutoffs: 200, group_keys: 3, curve: 500 });
    expect(DEFAULTS.ranges.curve).toMatchObject({ lo: 2, hi: 500, integer: true });
    expect(DEFAULTS.ranges["bench.h"]).toMatchObject({ lo: 1e-6, hi: 1000, lo_open: false });
  });

  it("the request leaves out what is the default of the core: no elements, no density, no filter, no groups", () => {
    const p = built({ ...EMPTY_FORM, modelId: 12, cutoffField: "AU" });
    expect(Object.keys(p).sort()).toEqual(["curve", "cutoff_field", "cutoffs", "model_id"]);
  });

  it("a range of the cut-offs goes as a range (the core expands it), a list keeps «без борта» as null in its place", () => {
    expect(built({ ...EMPTY_FORM, modelId: 12, cutoffMode: "range", rangeFrom: "0,1", rangeTo: "0,5", rangeStep: "0,1" }).cutoffs).toEqual({ from: 0.1, to: 0.5, step: 0.1 });
    expect(built({ ...EMPTY_FORM, modelId: 12, cutoffs: "без борта; 0,5; 1; 0,5" }).cutoffs).toEqual([null, 0.5, 1]);
    expect(parseCutoffList("Без борта").ok).toBe(true);
    expect(parseCutoffList("0 ; ; 2")).toEqual({ ok: true, values: [0, 2] });
  });

  it("the unit of an element is typed or the unit of the field; the elements of a text are read in order, a repeat goes", () => {
    expect(parseElements("AU:г/т; CU:%, AU")).toEqual([{ field: "AU", unit: "г/т" }, { field: "CU", unit: "%" }]);
    expect(elementsText(parseElements("AU:г/т; CU"))).toBe("AU:г/т; CU");
    expect(built({ ...EMPTY_FORM, modelId: 12, elements: [{ field: "AU", unit: "г/т" }, { field: "CU", unit: "" }] }).elements).toEqual([{ field: "AU", unit: "г/т" }, "CU"]);
  });
});

describe("the restrictor: «Карьер», «Ниже рельефа» and the tree of rules are one tree (ADR 0027 решение 8)", () => {
  const f = (p: Partial<ResourceForm>): ResourceForm => ({ ...EMPTY_FORM, modelId: 12, ...p });
  it("nothing — no restrictor; one leaf — the leaf itself; several — «И»", () => {
    expect(restrictOfForm(f({}))).toEqual({ ok: true, rule: null });
    expect(restrictOfForm(f({ pit: { ...EMPTY_FORM.pit, on: true, n: "3" } }))).toEqual({ ok: true, rule: { expr: "PIT <= 3" } });
    expect(restrictOfForm(f({ pit: { on: true, mode: "wireframe", n: "1", wireframeId: 50, by: "frac" } }))).toEqual({ ok: true, rule: { wireframe: { id: 50, side: "inside", by: "frac", m: 4 } } });
    expect(restrictOfForm(f({ below: { on: true, surfaceId: 41, by: "centre" } }))).toEqual({ ok: true, rule: { surface: { id: 41, side: "below", by: "centre" } } });
    const both = restrictOfForm(f({ pit: { ...EMPTY_FORM.pit, on: true, n: "2,5" }, below: { on: true, surfaceId: 41, by: "frac" } }));
    expect(both).toEqual({ ok: true, rule: { op: "and", items: [{ expr: "PIT <= 2.5" }, { surface: { id: 41, side: "below", by: "frac" } }] } });
  });
  it("the tree of the window stands first, the shortcuts after it; the filter of the library goes beside, as its own key", () => {
    const tree = { op: "and" as const, items: [{ cond: { field: "DOM", op: "=" as const, value: "A", list: "" } }] };
    const p = built(f({ filterId: 5, restrictOn: true, restrict: tree, pit: { ...EMPTY_FORM.pit, on: true, n: "1" } }));
    expect(p.filter_id).toBe(5);
    expect(p.restrict).toEqual({ op: "and", items: [{ op: "and", items: [{ cond: { field: "DOM", op: "=", value: "A" } }] }, { expr: "PIT <= 1" }] });
  });
  it("a shortcut without its object is told in its place", () => {
    expect(issueOf({ pit: { on: true, mode: "wireframe", n: "1", wireframeId: null, by: "frac" } }).field).toBe("pit");
    expect(issueOf({ pit: { ...EMPTY_FORM.pit, on: true, n: "0" } }).field).toBe("pit");
    expect(issueOf({ pit: { ...EMPTY_FORM.pit, on: true, n: "много" } }).field).toBe("pit");
    expect(issueOf({ below: { on: true, surfaceId: null, by: "centre" } }).field).toBe("below");
    expect(issueOf({ restrictOn: true }).field).toBe("restrict");
  });
});

describe("a form that is not right is told in Russian, with a hint and its place, before the core is asked", () => {
  const cases: [Partial<ResourceForm>, string][] = [
    [{ modelId: null }, "model"],
    [{ elements: Array.from({ length: 21 }, (_, i) => ({ field: `E${i}`, unit: "" })) }, "elements"],
    [{ elements: [{ field: "AU", unit: "очень длинная единица измерения" }] }, "elements"],
    [{ cutoffs: "" }, "cutoffs"],
    [{ cutoffs: "0; много" }, "cutoffs"],
    [{ cutoffs: "1e13" }, "cutoffs"],
    [{ cutoffs: Array.from({ length: 201 }, (_, i) => String(i)).join("; ") }, "cutoffs"],
    [{ cutoffMode: "range", rangeFrom: "0", rangeTo: "1", rangeStep: "" }, "cutoffs"],
    [{ cutoffMode: "range", rangeFrom: "0", rangeTo: "1", rangeStep: "0" }, "cutoffs"],
    [{ cutoffMode: "range", rangeFrom: "2", rangeTo: "1", rangeStep: "0,1" }, "cutoffs"],
    [{ cutoffMode: "range", rangeFrom: "0", rangeTo: "1000", rangeStep: "1" }, "cutoffs"],
    [{ balanceOn: true, balanceCutoff: "" }, "balance"],
    [{ balanceOn: true, balanceCutoff: "1", balanceOff: "x" }, "balance"],
    [{ balanceOn: true, balanceCutoff: "1", balanceOff: "1" }, "balance"],
    [{ densityMode: "field", densityField: "" }, "density"],
    [{ densityMode: "value", densityValue: "0" }, "density"],
    [{ densityMode: "value", densityValue: "-2" }, "density"],
    [{ groups: [{ kind: "field", field: "A" }, { kind: "field", field: "B" }, { kind: "field", field: "C" }, { kind: "bench" }] }, "groups"],
    [{ groups: [{ kind: "field", field: "A" }, { kind: "field", field: "A" }] }, "groups"],
    [{ groups: [{ kind: "bench" }], benchH: "0" }, "bench"],
    [{ groups: [{ kind: "bench" }], benchH: "1001" }, "bench"],
    [{ groups: [{ kind: "bench" }], benchZ0: "высоко" }, "bench"],
    [{ groups: [{ kind: "bench" }], benchZMin: "10", benchZMax: "10" }, "bench"],
    [{ curvePoints: "1" }, "curve"],
    [{ curvePoints: "501" }, "curve"],
    [{ curvePoints: "2,5" }, "curve"],
    [{ name: "я".repeat(201) }, "name"],
  ];
  for (const [patch, field] of cases) {
    it(`${field}: ${JSON.stringify(patch).slice(0, 70)}`, () => {
      const i = issueOf(patch);
      expect(i.field).toBe(field);
      expect(i.message).toMatch(/[А-Яа-яЁё]/);
      expect(i.hint).toMatch(/[А-Яа-яЁё]/);
    });
  }
  it("a curve of 0 points is «без кривой»; 2 and 500 are the edges", () => {
    expect(built({ ...EMPTY_FORM, modelId: 12, curveOn: false }).curve).toBe(0);
    expect(built({ ...EMPTY_FORM, modelId: 12, curvePoints: "2" }).curve).toBe(2);
    expect(built({ ...EMPTY_FORM, modelId: 12, curvePoints: "500" }).curve).toBe(500);
  });
});

describe("the refusals of the core stand in their place of the form", () => {
  it("422 `bad_param` names the param of the body; `group_numeric` and `report_too_big` — the groups", () => {
    const bp = answer("bad_param").body as { code: string; message: string; hint: string; param: string };
    const i = issueOfRefusal({ code: bp.code, message: bp.message, hint: bp.hint, param: bp.param }, EMPTY_FORM);
    expect(i).toEqual({ field: "cutoffs", message: bp.message, hint: bp.hint });
    const gn = answer("group_numeric").body as { code: string; message: string; hint: string; param: string };
    expect(issueOfRefusal({ code: gn.code, message: gn.message, hint: gn.hint, param: gn.param }, EMPTY_FORM).field).toBe("groups");
    const refusals = FILE.refusals as Record<string, { body: { code: string; message: string; hint: string } }>;
    const r = (k: string, param: string | null = null) => issueOfRefusal({ ...refusals[k].body, param }, EMPTY_FORM);
    expect(r("report_too_big").field).toBe("groups");
    expect(r("group_numeric").field).toBe("groups");
    expect(r("filter_not_found").field).toBe("filter");
    expect(r("template_not_found").field).toBe("template");
    expect(r("block_field_not_found", "elements.0").field).toBe("elements");
    expect(r("block_field_not_found", "groups.1").field).toBe("groups");
    expect(r("resource_report_not_found").field).toBe("general");
    for (const k of Object.keys(refusals)) expect(r(k).hint, k).toMatch(/[А-Яа-яЁё]/);
  });
  it("`filter_field_missing` (the field PIT comes with Э21): in «Карьер» when it reads the field, else in the restrictor or the filter", () => {
    const body = answer("filter_field_missing").body as { code: string; message: string; hint: string };
    const refusal = { code: body.code, message: body.message, hint: body.hint, param: null };
    expect(issueOfRefusal(refusal, { ...EMPTY_FORM, pit: { ...EMPTY_FORM.pit, on: true } }).field).toBe("pit");
    expect(issueOfRefusal(refusal, { ...EMPTY_FORM, restrictOn: true }).field).toBe("restrict");
    expect(issueOfRefusal(refusal, { ...EMPTY_FORM, filterId: 5 }).field).toBe("filter");
  });
});

describe("the flat arguments of the command", () => {
  it("a call with arguments shows in the form: the elements, the cut-offs, the range, the density, the keys, the bench", () => {
    const r = formOfArgs({ model_id: 12, elements: "AU:г/т; CU:%", cutoffs: "без борта; 0,5", cutoff_field: "AU", density: "2,7", groups: "CLASS; уступ", bench_h: 12.5, bench_z0: -25, curve: 0, template_id: "jorc", name: "Мой" });
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.form).toMatchObject({ modelId: 12, cutoffField: "AU", densityMode: "value", densityValue: "2,7", benchH: "12,5", benchZ0: "-25", curveOn: false, templateId: "jorc", name: "Мой" });
    expect(built(r.form)).toEqual({ model_id: 12, elements: [{ field: "AU", unit: "г/т" }, { field: "CU", unit: "%" }], cutoff_field: "AU", cutoffs: [null, 0.5], density: 2.7, groups: ["CLASS", { kind: "bench" }], bench: { h: 12.5, z0: -25 }, curve: 0, template_id: "jorc" });
    const range = formOfArgs({ model_id: 12, cutoff_from: 0.5, cutoff_to: 3, cutoff_step: 0.5, balance_cutoff: 1, balance_off: 0.5, density: "SG" });
    expect(range.ok && built(range.form)).toMatchObject({ cutoffs: { from: 0.5, to: 3, step: 0.5 }, balance: { cutoff: 1, off_cutoff: 0.5 }, density: "SG" });
  });
  it("a wrong argument is told in Russian in its place; no model is told too", () => {
    const bad = (a: Record<string, unknown>) => {
      const r = formOfArgs(a);
      expect(r.ok).toBe(false);
      if (r.ok) throw new Error("accepted");
      expect(isIssue(r.issue)).toBe(true);
      expect(r.issue.message).toMatch(/[А-Яа-яЁё]/);
      expect(r.issue.hint).not.toBe("");
      return r.issue.field;
    };
    expect(bad({})).toBe("model");
    expect(bad({ model_id: 1.5 })).toBe("model");
    expect(bad({ model_id: 1, filter_id: 1.5 })).toBe("filter");
    expect(bad({ model_id: 1, restrict: "{не json" })).toBe("restrict");
    expect(bad({ model_id: 1, restrict: "[1]" })).toBe("restrict");
    expect(bad({ model_id: 1, curve: 1 })).toBe("curve");
    expect(bad({ model_id: 1, curve: 501 })).toBe("curve");
  });
  it("the keys of a text: the bench is «уступ» or «bench»", () => {
    expect(parseKeys("CLASS; Уступ, bench")).toEqual([{ kind: "field", field: "CLASS" }, { kind: "bench" }, { kind: "bench" }]);
    expect(keysText([{ kind: "field", field: "CLASS" }, { kind: "bench" }])).toBe("CLASS; уступ");
  });
});

describe("the keys of the group by dragging (≤ 3)", () => {
  const A: KeyChoice = { kind: "field", field: "A" };
  const B: KeyChoice = { kind: "field", field: "B" };
  const C: KeyChoice = { kind: "field", field: "C" };
  const BENCH: KeyChoice = { kind: "bench" };
  it("the available keys are the categories and the bench, without the chosen ones", () => {
    expect(availableKeys(["A", "B"], [])).toEqual([A, B, BENCH]);
    expect(availableKeys(["A", "B"], [B, BENCH])).toEqual([A]);
  });
  it("a key is added at its place; a repeat and a fourth key are refused", () => {
    expect(addKey([A], B, 3)).toEqual([A, B]);
    expect(addKey([A, B], BENCH, 3, 0)).toEqual([BENCH, A, B]);
    expect(addKey([A], A, 3)).toEqual([A]);
    expect(addKey([A, B, C], BENCH, 3)).toEqual([A, B, C]);
  });
  it("a key moves, leaves", () => {
    expect(moveKey([A, B, C], 0, 2)).toEqual([B, C, A]);
    expect(moveKey([A, B, C], 2, 0)).toEqual([C, A, B]);
    expect(moveKey([A, B], 5, 0)).toEqual([A, B]);
    expect(removeKey([A, B, C], 1)).toEqual([A, C]);
  });
  it("a drag is text: it comes back as it went, a broken one is nothing", () => {
    const d = { from: "chosen" as const, key: BENCH, index: 1 };
    expect(decodeDrag(encodeDrag(d))).toEqual(d);
    expect(decodeDrag("не json")).toBeNull();
    expect(decodeDrag(JSON.stringify({ from: "x", key: A, index: 0 }))).toBeNull();
    expect(decodeDrag(JSON.stringify({ from: "chosen", key: { kind: "field" }, index: 0 }))).toBeNull();
  });
  it("a drop on the chosen ones adds or moves, a drop on the available ones removes", () => {
    expect(dropOnChosen([A, B], { from: "available", key: BENCH, index: 0 }, 1, 3)).toEqual([A, BENCH, B]);
    expect(dropOnChosen([A, B, C], { from: "chosen", key: A, index: 0 }, 3, 3)).toEqual([B, C, A]);
    expect(dropOnChosen([A, B, C], { from: "available", key: BENCH, index: 0 }, 0, 3)).toEqual([A, B, C]);
    expect(dropOnAvailable([A, B], { from: "chosen", key: A, index: 0 })).toEqual([B]);
    expect(dropOnAvailable([A, B], { from: "available", key: C, index: 0 })).toEqual([A, B]);
  });
  it("the form asks for the keys in the order of the columns; the bench gives the `bench` of the request", () => {
    const p = built({ ...EMPTY_FORM, modelId: 12, groups: [BENCH, A], benchH: "5", benchZMin: "-10", benchZMax: "40" });
    expect(p.groups).toEqual([{ kind: "bench" }, "A"]);
    expect(p.bench).toEqual({ h: 5, z_min: -10, z_max: 40 });
    expect(built({ ...EMPTY_FORM, modelId: 12, groups: [A] }).bench).toBeUndefined();
  });
});
