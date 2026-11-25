// The form of the wizard «Моделирование» (Э18б, 18.35) on the real answers of the core (`core/tests/data/simulate/answers.json`): the stored
// rows → the form → the body of the core is the same body (SGS, SIS by thresholds, categorical SIS, co-SGS), the words under a wrong field with its
// path of the body (the refusals of the core are the same words: `bad_param`, `variogram_scale`), the models of variogram of a kind (Д-38), the
// categories and their labels, the model of an indicator of a threshold, the flat arguments of the commands.
import { describe, expect, it } from "vitest";
import { bodyOf, categoriesOf, EMPTY_SIM_FORM, formOfArgs, formOfMeta, hasModel, indicatorOf, isCategorical, isFieldModel, isScoreModel, labelsOf, matchIndicators, simDefaultName, simStepOfParam, type SimForm, type VarioInfo } from "../../src/modules/simulation/form";
import { ID, INFOS, MODEL_VERSION, S, SG, SID, THRESHOLDS, VARIO_ROWS, VID, CAT_ROW } from "./harness";

const vario: VarioInfo = { rows: VARIO_ROWS, backs: { [VID.back]: true } };
const meta = (id: number) => INFOS[id].meta as Record<string, any>;
const built = (f: SimForm, v: VarioInfo = vario) => {
  const b = bodyOf(f, v);
  if (!b.ok) throw new Error(`${b.issue.param}: ${b.issue.message}`);
  return b.body as Record<string, any>;
};
const issue = (f: SimForm, v: VarioInfo = vario) => {
  const b = bodyOf(f, v);
  if (b.ok) throw new Error("the form was taken");
  return b.issue;
};

describe("the stored row → the form → the body", () => {
  it("SGS: the body has what the core stored (the target, the samples, the model, the search, the numbers, the tails)", () => {
    const m = meta(SID.sgs);
    const f = formOfMeta(m);
    expect(f).toMatchObject({ kind: "sgs", modelId: m.model_id, domainField: "ZONE", label: m.target.label, setId: m.samples.set_id, table: m.samples.table, field: m.samples.field, variogramId: m.variogram.model_id, nReal: String(m.n_real), seed: String(m.seed), nodes: String(m.nodes) });
    const b = built(f);
    expect(b).toMatchObject({ kind: "sgs", model_id: m.model_id, variogram: { model_id: m.variogram.model_id }, n_real: m.n_real, seed: m.seed, nodes: m.nodes, nscore: { ltail: "linear", utail: "linear", ltpar: null, utpar: null } });
    expect(b.target).toEqual({ domain_field: "ZONE", label: m.target.label, filter_id: null });
    expect(b.samples).toEqual({ set_id: m.samples.set_id, table: m.samples.table, field: m.samples.field, domain: { table: m.samples.domain.table, field: "ZONE", values: m.samples.domain.values } });
    // the search of the model: the ellipsoid is the core's, the one pass is typed
    expect(b.search).toEqual({ from: "model", passes: [{ factor: 1, n_max: 24 }] });
    expect(b.secondary).toBeUndefined();
    expect(b.method).toBeUndefined();
    expect(b.sub).toBeUndefined();
    expect(b.disc).toBeUndefined();
  });
  it("SIS by thresholds: the thresholds, the model of an indicator of each in the order of the list", () => {
    const m = meta(SID.sis);
    const f = formOfMeta(m);
    expect(f).toMatchObject({ kind: "sis", sisBy: "thresholds", ikModels: "per", variogramId: null });
    expect(f.variogramIds).toEqual(VID.ind);
    expect(f.thresholds).toBe(THRESHOLDS.map((t) => String(t).replace(".", ",")).join("; "));
    const b = built(f);
    expect(b.method).toEqual({ ik: { models: "per", thresholds: THRESHOLDS } });
    expect(b.variograms).toEqual(VID.ind.map((id, i) => ({ model_id: id, t: THRESHOLDS[i] })));
    expect(b.variogram).toBeUndefined();
    expect(b.nscore).toBeDefined();
  });
  it("the categorical SIS: the codes, the names, the median model; no tails, no top cut", () => {
    const m = meta(SID.cat);
    const f = formOfMeta(m);
    expect(f).toMatchObject({ kind: "sis", sisBy: "categories", ikModels: "median", categories: "1; 2; 3", labels: "Окисленные; Переходные; Первичные", field: "LITH" });
    expect(isCategorical(f)).toBe(true);
    const b = built(f);
    expect(b.method).toEqual({ ik: { models: "median", categories: [1, 2, 3], labels: ["Окисленные", "Переходные", "Первичные"] } });
    expect(b.nscore).toBeUndefined();
    expect(b.variogram).toEqual({ model_id: m.variogram.model_id });
    // a model in the units of the field is not refused here for SIS; the top cut is (the core says the same)
    expect(issue({ ...f, topcut: { mode: "value", field: "", value: "5" } })).toMatchObject({ param: "samples.topcut", code: "bad_param" });
  });
  it("co-SGS: the secondary field and ρ «по пробам» (null) as the core stored them; a typed ρ goes as a number", () => {
    const m = meta(SID.co);
    const f = formOfMeta(m);
    expect(f).toMatchObject({ kind: "cosgs", secField: SG, rhoMode: "samples" });
    expect(built(f).secondary).toEqual({ field: SG, rho: null });
    expect(built({ ...f, rhoMode: "value", rho: "-0,25" }).secondary).toEqual({ field: SG, rho: -0.25 });
    expect(formOfMeta({ ...m, secondary: { field: SG, rho: 0.4 } })).toMatchObject({ rhoMode: "value", rho: "0,4" });
  });
  it("the name of a row is that of the core when none is typed", () => {
    const f = formOfMeta(meta(SID.sgs));
    expect(simDefaultName({ ...f, name: "" })).toBe(`${f.field} · SGS · ${f.label}`);
    expect(simDefaultName({ ...f, kind: "cosgs" })).toBe(`${f.field} · Ко-SGS · ${f.label}`);
    expect(S.simulation.body.name).toBe(simDefaultName(f));
  });
});

describe("the words under a wrong field (the path of the body is the core's)", () => {
  const base = formOfMeta(meta(SID.sgs));
  it("the numbers: the ranges of Д-23, the same words as the core says", () => {
    expect(issue({ ...base, nReal: "101" })).toMatchObject({ param: "n_real", code: "bad_param" });
    expect(issue({ ...base, nReal: "101" }).message).toBe(S.bad_param.body.message.replace("Число реализаций: значение", "Число реализаций: значение"));
    expect(issue({ ...base, nReal: "0" }).hint).toMatch(/1.*100/);
    expect(issue({ ...base, nReal: "2,5" }).message).toMatch(/целое/);
    expect(issue({ ...base, seed: "-1" }).param).toBe("seed");
    expect(issue({ ...base, nodes: "65" }).param).toBe("nodes");
    expect(issue({ ...base, nMax: "129" })).toMatchObject({ param: S.bad_param_search.body.param, code: "bad_param" });
    expect(issue({ ...base, factor: "0" }).param).toBe("search.passes.0.factor");
    expect(issue({ ...base, nReal: "" }).message).toMatch(/не число/);
  });
  it("what is not chosen: the model, the model of variogram, the samples, the domain of the blocks", () => {
    expect(issue({ ...base, modelId: null }).param).toBe("model_id");
    expect(issue({ ...base, variogramId: null })).toMatchObject({ param: "variogram.model_id" });
    expect(issue({ ...base, setId: null }).param).toBe("samples.set_id");
    expect(issue({ ...base, field: "" }).param).toBe("samples.field");
    expect(issue({ ...base, label: "" }).param).toBe("target.label");
    expect(issue({ ...base, name: "я".repeat(201) }).param).toBe("name");
  });
  it("the model in the units of the field is not taken for SGS (the words of the core, Д-38); a model in Y without its back transform is not taken for SIS", () => {
    expect(issue({ ...base, variogramId: VID.vz })).toMatchObject({ param: "variogram.model_id", code: "variogram_scale", message: S.variogram_scale.body.message, hint: S.variogram_scale.body.hint });
    const sis = formOfMeta(meta(SID.sis));
    expect(issue({ ...sis, variogramIds: [VID.vy, ...VID.ind.slice(1)] })).toMatchObject({ param: "variograms.0.model_id", code: "variogram_scale" });
    expect(issue({ ...sis, variogramIds: [VID.back + 1, ...VID.ind.slice(1)] }).code).toBe("variogram_scale");
    // with the back transform the model of Y is a model of the field
    expect(built({ ...sis, variogramIds: [VID.back, ...VID.ind.slice(1)] }).variograms[0]).toEqual({ model_id: VID.back, t: THRESHOLDS[0] });
    // a model the list does not know is not judged here: the core checks it
    expect(built({ ...base, variogramId: 999 }).variogram).toEqual({ model_id: 999 });
    expect(isScoreModel("nscore")).toBe(true);
    expect(isScoreModel("indicator:1")).toBe(false);
    expect(isFieldModel("nscore", false)).toBe(false);
    expect(isFieldModel("indicator:1", false)).toBe(true);
  });
  it("SIS: no thresholds is the core's refusal («Для SIS не заданы пороги»); the thresholds go up; a model for every threshold", () => {
    const sis = formOfMeta(meta(SID.sis));
    expect(issue({ ...sis, thresholds: "" })).toMatchObject({ param: "method.ik.thresholds" });
    expect(issue({ ...sis, thresholds: "1; 0,5" }).param).toBe("method.ik.thresholds.1");
    expect(issue({ ...sis, variogramIds: [VID.ind[0], null, VID.ind[2]] })).toMatchObject({ param: "variograms.1.model_id" });
    expect(issue({ ...sis, ikModels: "median", variogramId: null }).param).toBe("variogram.model_id");
    expect(S.sis_no_method.body.param).toBe("method");
  });
  it("the categories: 2–20 codes 0–255, distinct, a name for every code or none", () => {
    expect(categoriesOf("1; 2; 3")).toEqual([1, 2, 3]);
    expect(categoriesOf("1")).toMatchObject({ param: "method.ik.categories" });
    expect(categoriesOf("1; 1")).toMatchObject({ param: "method.ik.categories.1" });
    expect(categoriesOf("1; 256")).toMatchObject({ param: "method.ik.categories.1" });
    expect(categoriesOf("1; 2,5")).toMatchObject({ code: "bad_param" });
    expect(categoriesOf(Array.from({ length: 21 }, (_, i) => i).join("; "))).toMatchObject({ param: "method.ik.categories" });
    expect(labelsOf("", [1, 2])).toBeNull();
    expect(labelsOf("А; Б", [1, 2])).toEqual(["А", "Б"]);
    expect(labelsOf("А", [1, 2])).toMatchObject({ param: "method.ik.labels" });
    expect(labelsOf(`А; ${"я".repeat(81)}`, [1, 2])).toMatchObject({ param: "method.ik.labels.1" });
  });
  it("co-SGS: the field is needed, ρ is between −1 and 1 (the core's words)", () => {
    const co = formOfMeta(meta(SID.co));
    expect(issue({ ...co, secField: "" })).toMatchObject({ param: "secondary.field" });
    const rho = issue({ ...co, rhoMode: "value", rho: "1,5" });
    expect(rho).toMatchObject({ param: "secondary.rho", code: "bad_param" });
    expect(rho.message).toBe(S.cosgs_bad_rho.body.message);
    expect(rho.hint).toMatch(/-1 до 1/);
    expect(S.cosgs_not_a_number.body.code).toBe("field_not_number");
  });
  it("the tails of the back transform: a power tail needs its exponent (> 0), a hyperbolic one — not less than 1", () => {
    expect(issue({ ...base, nscore: { ...base.nscore, ltail: "power" } }).param).toBe("nscore.ltpar");
    expect(issue({ ...base, nscore: { ...base.nscore, utail: "hyperbolic", utpar: "0,5" } })).toMatchObject({ param: "nscore.utpar" });
    expect(built({ ...base, nscore: { ltail: "power", utail: "hyperbolic", ltpar: "2", utpar: "1,5" } }).nscore).toEqual({ ltail: "power", utail: "hyperbolic", ltpar: 2, utpar: 1.5 });
    expect(built({ ...base, nscore: { ltail: "none", utail: "none", ltpar: "", utpar: "" } }).nscore).toMatchObject({ ltail: "none", utail: "none" });
  });
  it("the manual search: r1 is needed; the model search without a model is told", () => {
    expect(issue({ ...base, variogramId: null, search: { ...base.search, from: "model" } }).param).toBe("variogram.model_id");
    const manual = { ...base, search: { ...base.search, from: "manual" as const, ang: ["10", "", ""] as [string, string, string], r: ["50", "", "25"] as [string, string, string] } };
    expect(built(manual).search).toEqual({ from: "manual", ang1: 10, r1: 50, r3: 25, passes: [{ factor: 1, n_max: 24 }] });
    expect(issue({ ...manual, search: { ...manual.search, r: ["", "", ""] } }).param).toBe("search.r1");
    expect(hasModel(base)).toBe(true);
    expect(hasModel({ ...base, variogramId: null })).toBe(false);
  });
  it("the step of a path: the kind and the target — 1, the samples — 2, the models and the secondary — 3, the search — 4, the numbers — 5", () => {
    for (const [p, n] of [["model_id", 1], ["target.label", 1], ["kind", 1], ["samples.field", 2], ["variogram.model_id", 3], ["variograms.1.model_id", 3], ["method.ik.thresholds", 3], ["secondary.rho", 3], ["search.passes.0.n_max", 4], ["n_real", 5], ["nscore.ltpar", 5], ["seed", 5], [null, 6]] as const) expect(simStepOfParam(p)).toBe(n);
  });
});

describe("the models of the indicators and the flat arguments of the commands", () => {
  it("the model of an indicator of a threshold is the one with `indicator:<t>` of the same t", () => {
    expect(indicatorOf("indicator:0.7")).toBe(0.7);
    expect(indicatorOf("nscore")).toBeNull();
    expect(matchIndicators(VARIO_ROWS, THRESHOLDS)).toEqual(VID.ind);
    expect(matchIndicators(VARIO_ROWS, [0.7, 99])).toEqual([VID.ind[0], null]);
  });
  it("simulate.sis with the flat arguments makes the form of a SIS by thresholds; categories — a categorical one; co-SGS — the secondary field and ρ", () => {
    const sis = formOfArgs("sis", { model_id: ID.model, set_id: ID.set, table: ID.table, field: "AU", thresholds: "0,7; 1; 1,5", models: "per", variogram_ids: VID.ind.join(";"), n_real: 4, seed: 99 }, EMPTY_SIM_FORM);
    expect(sis).toMatchObject({ kind: "sis", sisBy: "thresholds", thresholds: "0,7; 1; 1,5", ikModels: "per", variogramIds: VID.ind, nReal: "4", seed: "99", setId: ID.set, table: ID.table, field: "AU", modelId: ID.model });
    const cat = formOfArgs("sis", { categories: "1; 2; 3", labels: "А; Б; В", models: "median", variogram_id: VID.vz }, sis);
    expect(cat).toMatchObject({ sisBy: "categories", categories: "1; 2; 3", labels: "А; Б; В", ikModels: "median", variogramId: VID.vz });
    const co = formOfArgs("cosgs", { secondary_field: SG, rho: 0.3, search_from: "manual", r1: 50 }, EMPTY_SIM_FORM);
    expect(co).toMatchObject({ kind: "cosgs", secField: SG, rhoMode: "value", rho: "0.3" });
    expect(co.search).toMatchObject({ from: "manual", r: ["50", "", ""] });
    const dom = formOfArgs("sgs", { domain_field: "ZONE", label: "окисл", sample_domain_field: "ZONE", domain_values: "окисл", domain_table: ID.domTable, ltail: "none" }, EMPTY_SIM_FORM);
    expect(dom).toMatchObject({ domainField: "ZONE", label: "окисл", domain: { table: ID.domTable, field: "ZONE", values: ["окисл"] }, nscore: { ltail: "none" } });
  });
  it("a row of the categories shows its codes and names to the windows; the version of the model is the one the post-processing asks", () => {
    expect(CAT_ROW.meta.method.ik.categories).toEqual([1, 2, 3]);
    expect(MODEL_VERSION).toBe(S.post_result.body.version - 1);
  });
});
