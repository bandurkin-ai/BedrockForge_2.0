// The form of the wizard (Э18, 18.19): the stored parameters of the core (`core/tests/data/estimate/params.json`: `canonical` of
// five rows) → the form → the body, the first wrong thing of the form with the words and the path of the body, the scale of the
// models of variogram (Д-38), the query of the defaults. Nothing wrong reaches the core.
import { describe, expect, it } from "vitest";
import { applySoft, bodyOf, defaultName, defaultsQueryOf, EMPTY_FORM, formOfArgs, formOfMeta, issueAt, sameName, scaleOf, stepOfParam, thresholdsOf, type EstimationForm } from "../../src/modules/estimation/form";
import { EMPTY_SEARCH } from "../../src/modules/estimation/formSearch";
import { A, FIELD, ID, PARAMS } from "./harness";

const CASES = Object.entries(PARAMS.cases) as [string, { body: Record<string, unknown>; canonical: Record<string, unknown> }][];

/** A complete form of an OK row on the contract's data. */
const ok = (patch: Partial<EstimationForm> = {}): EstimationForm => ({ ...EMPTY_FORM, modelId: ID.model, domainField: "ZONE", label: "окисл", setId: ID.set, table: ID.table, field: FIELD, domain: { table: ID.domTable, field: "ZONE", values: ["окисл"] }, method: "ok", variogramId: ID.vario, ...patch });

const issueOf = (f: EstimationForm, scales: Record<number, "z" | "back" | "y"> = {}) => {
  const got = bodyOf(f, { scales });
  if (got.ok) throw new Error("the form was accepted");
  return got.issue;
};

describe("the stored row → the form → the body", () => {
  for (const [name, c] of CASES) {
    it(`${name}: the steps 1–5 of the body are the stored ones`, () => {
      const form = formOfMeta(c.canonical);
      const got = bodyOf(form);
      expect(got.ok).toBe(true);
      if (!got.ok) return;
      const b = got.body as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
      const s = c.canonical as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
      expect(b.model_id).toBe(s.model_id);
      expect(b.target).toEqual({ domain_field: s.target.domain_field, label: s.target.label, filter_id: s.target.filter_id, ...(s.target.rules ? { rules: s.target.rules } : {}) });
      expect(b.sub).toBe(s.sub);
      expect(b.disc).toEqual(s.disc);
      expect(b.samples.set_id).toBe(s.samples.set_id);
      expect(b.samples.table).toBe(s.samples.table);
      expect(b.samples.field).toBe(s.samples.field);
      expect(b.samples.domain).toEqual(s.samples.domain);
      expect(b.samples.topcut ?? null).toEqual(s.samples.topcut);
      expect(b.method.kind).toBe(s.method.kind);
      if (s.method.kind === "ik") expect(b.method.ik).toEqual(s.method.ik);
      if (s.method.kind === "idw") expect(b.method.power).toBe(s.method.power);
      if (s.method.kind === "sk") expect(b.method.mean).toEqual(s.method.mean);
      if (s.variogram) expect(b.variogram).toEqual({ model_id: s.variogram.model_id });
      if (s.variograms) expect(b.variograms).toEqual(s.variograms.map((v: { model_id: number; t: number }) => ({ model_id: v.model_id, t: v.t })));
      // what steps 4–5 edit goes back as it was stored; «по модели» sends no numbers (the core takes the angles and the radii itself)
      expect(b.search.from).toBe(s.search.from);
      for (const k of ["ang1", "ang2", "ang3", "r1", "r2", "r3"]) expect(b.search[k], k).toBe(s.search.from === "manual" ? s.search[k] : undefined);
      expect(b.search.passes).toEqual(s.search.passes);
      expect(b.outputs).toEqual(s.outputs);
    });
  }
  it("the row of the answer of the core (GET /estimations/{id}): the form is what the core stored", () => {
    const m = A.estimation.body.meta;
    const f = formOfMeta(m);
    expect(f).toMatchObject({ name: m.name, modelId: m.model_id, domainField: m.target.domain_field, label: m.target.label, method: m.method.kind, variogramId: m.variogram.model_id, setId: m.samples.set_id, table: m.samples.table, field: m.samples.field, disc: m.disc.map(String) });
    expect(f.domain).toEqual(m.samples.domain);
    // the body of the form is accepted by the core's own parser: what the form sends is what the core stored (the model and the searching come from the core)
    const got = bodyOf(f);
    expect(got.ok && (got.body.samples as { domain: unknown }).domain).toEqual(m.samples.domain);
  });
  it("soft boundary, top cut and weights survive the trip", () => {
    const meta = { ...PARAMS.cases.ok_block_passes.canonical, samples: { ...PARAMS.cases.ok_block_passes.canonical.samples, soft: { domains: ["перех"], dist: 5 }, topcut: { field: "AU_CUT" }, weights: "W" } };
    const f = formOfMeta(meta);
    expect(f.soft).toEqual({ on: true, domains: ["перех"], dist: "5" });
    expect(f.topcut).toEqual({ mode: "field", field: "AU_CUT", value: "" });
    expect(f.weights).toEqual({ kind: "field", field: "W" });
    const b = bodyOf(f);
    expect(b.ok && (b.body.samples as Record<string, unknown>).soft).toEqual({ domains: ["перех"], dist: 5 });
  });
});

describe("the body of a new row", () => {
  it("only what the person chose; the search, the outputs and the name are the core's defaults", () => {
    const got = bodyOf(ok());
    expect(got.ok && got.body).toEqual({
      model_id: ID.model,
      target: { domain_field: "ZONE", label: "окисл", filter_id: null },
      sub: "parent",
      disc: [4, 4, 2],
      samples: { set_id: ID.set, table: ID.table, field: FIELD, domain: { table: ID.domTable, field: "ZONE", values: ["окисл"] } },
      method: { kind: "ok" },
      variogram: { model_id: ID.vario },
    });
  });
  it("the domain of the samples in the table of the samples has no `table`; the weights: none → false, field → its name", () => {
    const b = bodyOf(ok({ domain: { table: "", field: "LITH", values: ["a"] }, weights: { kind: "none" } }));
    expect(b.ok && (b.body.samples as Record<string, unknown>)).toMatchObject({ domain: { field: "LITH", values: ["a"] }, weights: false });
    const w = bodyOf(ok({ weights: { kind: "field", field: "W" } }));
    expect(w.ok && (w.body.samples as Record<string, unknown>).weights).toBe("W");
  });
  it("«0,5» and «0.5» are the same number; an IK row has its thresholds and a model for each", () => {
    const b = bodyOf(ok({ method: "ik", thresholds: "0,5; 1; 2.5", variogramId: null, variogramIds: [31, 32, 33] }));
    expect(b.ok && b.body.method).toEqual({ kind: "ik", ik: { thresholds: [0.5, 1, 2.5], models: "per", class_means: "declus" } });
    expect(b.ok && b.body.variograms).toEqual([{ model_id: 31, t: 0.5 }, { model_id: 32, t: 1 }, { model_id: 33, t: 2.5 }]);
    expect(b.ok && "variogram" in b.body).toBe(false);
  });
  it("IK with one model: `models: median` and `variogram`", () => {
    const b = bodyOf(ok({ method: "ik", thresholds: "1; 2", ikModels: "median" }));
    expect(b.ok && b.body.variogram).toEqual({ model_id: ID.vario });
    expect(b.ok && (b.body.method as { ik: { models: string } }).ik.models).toBe("median");
  });
  it("NN and IDW: the model is optional (only for the search)", () => {
    // without a model the radii are typed (the search is by hand): the core would ask for r1 too
    const by = { ...EMPTY_SEARCH, from: "manual" as const, r: ["50", "", ""] as [string, string, string] };
    const nn = bodyOf(ok({ method: "nn", variogramId: null, search: by }));
    expect(nn.ok && nn.body.search).toEqual({ from: "manual", r1: 50 });
    expect(issueOf(ok({ method: "nn", variogramId: null }))).toMatchObject({ param: "search.r1", code: "bad_param" });
    const idw = bodyOf(ok({ method: "idw", power: "1,5", variogramId: ID.vario }));
    expect(idw.ok && idw.body.method).toEqual({ kind: "idw", power: 1.5 });
    expect(idw.ok && idw.body.variogram).toEqual({ model_id: ID.vario });
  });
});

describe("the first wrong thing: the words, the hint, the path of the body", () => {
  const cases: [string, Partial<EstimationForm>, string, RegExp][] = [
    ["no model", { modelId: null }, "model_id", /блочная модель/],
    ["a name too long", { name: "я".repeat(201) }, "name", /200/],
    ["a domain of the blocks without its value", { label: "" }, "target.label", /домен/],
    ["a value without its field", { domainField: "" }, "target.domain_field", /без поля/],
    ["a discretization out of the range", { disc: ["4", "11", "2"] }, "disc.1", /от 1 до 10/],
    ["a discretization that is not whole", { disc: ["4", "4", "2,5"] }, "disc.2", /целое/],
    ["a discretization that is text", { disc: ["x", "4", "2"] }, "disc.0", /не число/],
    ["no set", { setId: null }, "samples.set_id", /набор/],
    ["no table", { table: null }, "samples.table", /таблиц/],
    ["no field", { field: null }, "samples.field", /поле содержания/],
    ["a domain without its field", { domain: { table: "", field: "", values: ["a"] } }, "samples.domain.field", /поле/],
    ["a domain without values", { domain: { table: "", field: "ZONE", values: [] } }, "samples.domain.values", /значения/],
    ["a soft boundary without the domain", { domain: null, soft: { on: true, domains: ["b"], dist: "5" } }, "samples.soft", /без домена проб/],
    ["a soft distance out of the range", { soft: { on: true, domains: ["перех"], dist: "-1" } }, "samples.soft.dist", /от 0 до 100/],
    ["a soft boundary without neighbours", { soft: { on: true, domains: [], dist: "5" } }, "samples.soft.domains", /соседни/],
    ["a neighbour that is the domain", { soft: { on: true, domains: ["окисл"], dist: "5" } }, "samples.soft.domains", /совпадает/],
    ["a top cut field not chosen", { topcut: { mode: "field", field: "", value: "" } }, "samples.topcut.field", /урезк/i],
    ["a top cut threshold that is not a number", { topcut: { mode: "value", field: "", value: "много" } }, "samples.topcut.value", /не число/],
    ["weights: a field not chosen", { weights: { kind: "field", field: " " } }, "samples.weights", /веса/i],
    ["IDW power out of the range", { method: "idw", power: "6" }, "method.power", /от 0,5 до 5/],
    ["SK mean that is not a number", { method: "sk", meanMode: "value", meanValue: "" }, "method.mean.value", /не число/],
    ["IK without thresholds", { method: "ik", thresholds: "  " }, "method.ik.thresholds", /Не заданы пороги/],
    ["IK thresholds not ascending", { method: "ik", thresholds: "1; 2; 2" }, "method.ik.thresholds.2", /строго возрастать/],
    ["IK threshold that is text", { method: "ik", thresholds: "1; два" }, "method.ik.thresholds.1", /не число/],
    ["IK with more than 20 thresholds", { method: "ik", thresholds: Array.from({ length: 21 }, (_, i) => i + 1).join("; ") }, "method.ik.thresholds", /больше 20/],
    ["OK without the model", { variogramId: null }, "variogram.model_id", /модель вариограммы/],
    ["SK without the model", { method: "sk", variogramId: null }, "variogram.model_id", /модель вариограммы/],
    ["IK median without the model", { method: "ik", thresholds: "1", ikModels: "median", variogramId: null }, "variogram.model_id", /модель вариограммы/],
    ["IK per: a threshold without a model", { method: "ik", thresholds: "1; 2", variogramId: null, variogramIds: [31] }, "variograms.1.model_id", /порога 2/],
  ];
  for (const [name, patch, param, words] of cases) {
    it(`${name} → ${param}`, () => {
      const i = issueOf(ok(patch));
      expect(i.param).toBe(param);
      expect(i.message).toMatch(words);
      expect(i.hint).not.toBe("");
      expect(i.code).toBe("bad_param");
    });
  }
  it("the steps come in order: a model first, then the samples, then the method", () => {
    expect(issueOf(ok({ modelId: null, setId: null, variogramId: null })).param).toBe("model_id");
    expect(issueOf(ok({ setId: null, variogramId: null })).param).toBe("samples.set_id");
  });
});

describe("the scale of the model of variogram (Д-38)", () => {
  it("Z without the transform, back with it, Y without it", () => {
    expect(scaleOf("none", false)).toBe("z");
    expect(scaleOf("none", true)).toBe("z");
    expect(scaleOf("nscore", true)).toBe("back");
    expect(scaleOf("nscore", false)).toBe("y");
  });
  it("a model in Y is refused with the words of the core (`variogram_scale`); a model with back goes", () => {
    const i = issueOf(ok({ variogramId: ID.varioY }), { [ID.varioY]: "y" });
    const core = A.variogram_scale.body;
    expect([i.code, i.message, i.hint, i.param]).toEqual([core.code, core.message, core.hint, core.param]);
    expect(bodyOf(ok({ variogramId: ID.varioBack }), { scales: { [ID.varioBack]: "back" } }).ok).toBe(true);
  });
  it("IK per threshold: the path names the threshold", () => {
    const i = issueOf(ok({ method: "ik", thresholds: "1; 2", variogramId: null, variogramIds: [ID.vario, ID.varioY] }), { [ID.vario]: "z", [ID.varioY]: "y" });
    expect([i.code, i.param]).toEqual(["variogram_scale", "variograms.1.model_id"]);
  });
});

describe("the pieces", () => {
  it("the thresholds", () => {
    expect(thresholdsOf("0,5; 1; 2,5")).toEqual([0.5, 1, 2.5]);
    expect(Array.isArray(thresholdsOf("1;\n2"))).toBe(true);
    expect((thresholdsOf("") as { param: string }).param).toBe("method.ik.thresholds");
  });
  it("the step of a path of the body, the words under a field", () => {
    expect(["name", "model_id", "target.label", "disc.1", "samples.soft.dist", "method.ik.thresholds.1", "variograms.0.model_id", "search.passes.1.n_max", "outputs.fields.3", "expected_version", null].map((p) => stepOfParam(p))).toEqual([1, 1, 1, 1, 2, 3, 3, 4, 5, 6, 6]);
    const i = issueOf(ok({ disc: ["4", "11", "2"] }));
    expect(issueAt(i, "disc")).toBe(i);
    expect(issueAt(i, "disc.1")).toBe(i);
    expect(issueAt(i, "disc.0")).toBeNull();
    expect(issueAt(i, "dis")).toBeNull();
    expect(issueAt(null, "disc")).toBeNull();
  });
  it("the default name and the preselected domain values", () => {
    expect(defaultName(ok())).toBe("AU · OK · окисл");
    expect(defaultName({ ...EMPTY_FORM })).toBe("поле · OK");
    expect(sameName("Окисл", ["окисл", "перех"])).toEqual(["окисл"]);
    expect(sameName("", ["окисл"])).toEqual([]);
  });
  it("the query of the defaults: complete data only; the domain, the weights, the model", () => {
    expect(defaultsQueryOf(EMPTY_FORM)).toBeNull();
    expect(defaultsQueryOf(ok({ setId: null }))).toBeNull();
    expect(defaultsQueryOf(ok({ weights: { kind: "none" } }))).toEqual({ model_id: ID.model, set_id: ID.set, table: ID.table, field: FIELD, method: "ok", block_domain: "ZONE", domain_table: ID.domTable, domain_field: "ZONE", domain_values: ["окисл"], weights: "", variogram_id: ID.vario });
    expect(defaultsQueryOf(ok({ domain: null, domainField: "", method: "ik", ikModels: "median" }))).toEqual({ model_id: ID.model, set_id: ID.set, table: ID.table, field: FIELD, method: "ik", variogram_id: ID.vario });
  });
  it("«Задать мягкую границу…» of the contact analysis → the domain A of the samples and the neighbour B", () => {
    const f = applySoft(EMPTY_FORM, { a: "окисл", b: "перех", distance: 7.5 });
    expect(f.domain).toEqual({ table: "", field: "", values: ["окисл"] });
    expect(f.soft).toEqual({ on: true, domains: ["перех"], dist: "7,5" });
    expect(formOfArgs({ soft_a: "а", soft_b: "б", soft_distance: 3, method: "sk", model_id: ID.model }, EMPTY_FORM)).toMatchObject({ method: "sk", modelId: ID.model, soft: { on: true, domains: ["б"], dist: "3" } });
    expect(formOfArgs({ method: "xx" }, EMPTY_FORM).method).toBe("ok");
  });
});
