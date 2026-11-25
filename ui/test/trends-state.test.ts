// Trends (ТЗ 7.35 Д-15; ADR 0022 §10; Э16 16.20): the answers of the core (`GET /geostat/trends`, `GET /trends/{id}`,
// `/usage`, 409 `in_use`, 422) are read as they are — the JSON below is the real answer of the core of 16.16 (a run
// of `routes_trend.py` through TestClient with the set of `test_vario_store.py`, 2026-11-22), not a guess; the
// matrix A of the core = diag(1, 1/r2, 1/r3)·R of `viz/ellipsoid/frame.ts` (1e−12, the mirror of ТД15-1); the form
// is checked before a request; the handles' model ↔ the form; the view key `ellipsoids`; «Сохранить как тренд».
import { describe, expect, it } from "vitest";
import { readTrend, readUsage, usageOfError, usageText } from "../src/modules/trends/api";
import {
  bodyOfArgs,
  centreArg,
  checkValues,
  dropObject,
  ellipsoidEntries,
  EMPTY_VALUES,
  modelOf,
  newEdit,
  numText,
  originText,
  parseNum,
  ratiosArg,
  saveArgs,
  showObject,
  shownObjects,
  trendArgsOfWork,
  valuesOf,
  withModel,
} from "../src/modules/trends/state";
import { newWork } from "../src/modules/variography/state";
import type { DefaultsAnswer } from "../src/modules/variography/api";
import { CoreError } from "../src/shell/api";
import { rotationRows } from "../src/viz/ellipsoid/frame";

/** `GET /api/v1/geostat/trends` of the core (16.16), as it answered. */
export const CORE_LIST = {
  trends: [
    { id: 5, name: "Вручную", kind: "global", ang1: 17.0, ang2: -3.0, ang3: 0.0, ratios: [1.0, 0.3, 0.07], size: 100.0, centre: [5000123.25, 7200456.5, 312.125], color: "#f46a1a", origin: { type: "manual" }, used: false, matrix: [[0.29197101946266824, 0.9549941735309524, -0.05233595624294437], [-3.187682519876785, 0.9745723490757893, 0.0], [0.2185936106434886, 0.7149874837571574, 14.266136210779624]], version: 1, created_at: "2026-11-22T00:26:28.180727Z", updated_at: "2026-11-22T00:26:28.180727Z" },
    { id: 6, name: "Тренд", kind: "global", ang1: 12.5, ang2: 0.0, ang3: 0.0, ratios: [1.0, 1.0, 1.0], size: 100.0, centre: null, color: "#f46a1a", origin: { type: "ellipsoid" }, used: false, matrix: [[0.21643961393810288, 0.9762960071199334, 0.0], [-0.9762960071199334, 0.21643961393810288, 0.0], [0.0, 0.0, 1.0]], version: 1, created_at: "2026-11-22T00:26:28.186942Z", updated_at: "2026-11-22T00:26:28.186942Z" },
    { id: 4, name: "Тренд · Золото", kind: "global", ang1: 112.5, ang2: -35.0, ang3: -20.0, ratios: [1.0, 0.36, 0.05], size: 250.0, centre: null, color: "#f46a1a", origin: { type: "model", model_id: 3, structure: 2 }, used: false, matrix: [[0.7567978077333783, -0.31347591593739127, -0.5735764363510465], [0.4954528111443995, 2.620098846694185, -0.7782402766478759], [12.576863108994473, 2.1944914377164477, 15.39502262640114]], version: 1, created_at: "2026-11-22T00:26:28.175081Z", updated_at: "2026-11-22T00:26:28.175081Z" },
  ],
}; // prettier-ignore
/** `GET /trends/4` after `PUT {color}`, a consumer and `DELETE /variography/models/3`. */
export const CORE_MODEL_GONE = { id: 4, name: "Тренд · Золото", kind: "global", ang1: 112.5, ang2: -35.0, ang3: -20.0, ratios: [1.0, 0.36, 0.05], size: 250.0, centre: null, color: "#1f6bff", origin: { type: "model", model_id: null, structure: 2 }, used: true, matrix: CORE_LIST.trends[2].matrix, version: 3, created_at: "2026-11-22T00:26:28.175081Z", updated_at: "2026-11-22T00:26:28.213731Z" }; // prettier-ignore
export const CORE_USAGE = { usage: [{ id: 7, kind: "estimation", name: "Оценка AU", role: "trend" }] };
export const CORE_IN_USE = { usage: CORE_USAGE.usage, code: "in_use", message: "Тренд используется: «Оценка AU».", hint: "Выберите другой тренд в этих объектах или удалите их, затем удалите тренд." };

describe("the answers of the core", () => {
  it("a trend is read as the core gives it; the model gone — `model_id: null`, not dropped", () => {
    const [manual, ell, gold] = CORE_LIST.trends.map(readTrend);
    expect(manual).toMatchObject({ id: 5, name: "Вручную", kind: "global", ang1: 17, ang2: -3, ang3: 0, ratios: [1, 0.3, 0.07], size: 100, centre: [5000123.25, 7200456.5, 312.125], origin: { type: "manual" }, used: false, version: 1 });
    expect(ell.centre).toBeNull();
    expect(ell.origin).toEqual({ type: "ellipsoid" });
    expect(gold.origin).toEqual({ type: "model", model_id: 3, structure: 2 });
    const gone = readTrend(CORE_MODEL_GONE);
    expect(gone.origin).toEqual({ type: "model", model_id: null, structure: 2 });
    expect(gone).toMatchObject({ used: true, color: "#1f6bff", version: 3 });
    expect(originText(gone.origin)).toBe("Из модели вариограммы (модель удалена), структура 2");
    expect(originText(gold.origin, (id) => (id === 3 ? "Золото" : null))).toBe("Из модели «Золото», структура 2");
    expect(originText(ell.origin)).toBe("Из эллипсоида модели вариограммы");
  });

  it("the matrix A of the core = diag(1, 1/r2, 1/r3)·R of the interface (1e−12)", () => {
    for (const t of CORE_LIST.trends.map(readTrend)) {
      const r = rotationRows(t);
      const scale = [1, 1 / t.ratios[1], 1 / t.ratios[2]];
      for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) expect(Math.abs(t.matrix[i][j] - r[i][j] * scale[i]), `${t.name} ${i}${j}`).toBeLessThanOrEqual(1e-12 * Math.max(1, Math.abs(t.matrix[i][j])));
    }
  });

  it("«Где используется» and 409 `in_use` carry the consumers", () => {
    expect(readUsage(CORE_USAGE)).toEqual([{ id: 7, kind: "estimation", name: "Оценка AU", role: "trend" }]);
    expect(readUsage({ usage: [] })).toEqual([]);
    const e = new CoreError(409, "in_use", CORE_IN_USE.message, CORE_IN_USE.hint);
    e.body = CORE_IN_USE;
    expect(usageOfError(e)).toEqual(readUsage(CORE_USAGE));
    expect(usageOfError(new Error("x"))).toEqual([]);
    const many = Array.from({ length: 7 }, (_, i) => ({ id: i, kind: "estimation", name: `О${i}`, role: "trend" }));
    expect(usageText(many)).toBe("«О0», «О1», «О2», «О3», «О4» и ещё 2");
  });
});

describe("the form of a trend", () => {
  it("numbers with «,», the values of a trend and back without a loss", () => {
    expect(parseNum("12,5")).toBe(12.5);
    expect(parseNum(" −3 ")).toBe(-3);
    expect(parseNum("")).toBeNull();
    expect(parseNum("1,2,3")).toBeNull();
    expect(numText(0.36)).toBe("0,36");
    expect(numText(-0)).toBe("0");
    expect(numText(1 / 3)).toBe("0,333333333333");
    const t = readTrend(CORE_LIST.trends[0]);
    const v = valuesOf(t);
    expect(v).toMatchObject({ ang1: "17", ang2: "-3", r2: "0,3", r3: "0,07", cx: "5000123,25", cy: "7200456,5", cz: "312,125" });
    const c = checkValues(v);
    expect(c.ok && c.shape).toEqual({ name: "Вручную", ang1: 17, ang2: -3, ang3: 0, ratios: [1, 0.3, 0.07], size: 100, centre: [5000123.25, 7200456.5, 312.125], color: "#f46a1a" });
  });

  it("out of the ranges of the core is refused before a request, with the field", () => {
    const bad = (patch: Partial<typeof EMPTY_VALUES>) => {
      const c = checkValues({ ...EMPTY_VALUES, ...patch });
      return c.ok ? null : c.param;
    };
    expect(bad({})).toBeNull();
    expect(bad({ ang1: "361" })).toBe("ang1");
    expect(bad({ ang2: "-91" })).toBe("ang2");
    expect(bad({ ang3: "181" })).toBe("ang3");
    expect(bad({ r2: "0" })).toBe("r2");
    expect(bad({ r3: "1000001" })).toBe("r3");
    expect(bad({ size: "0" })).toBe("size");
    expect(bad({ size: "абв" })).toBe("size");
    expect(bad({ cx: "1" })).toBe("centre");
    expect(bad({ color: "red" })).toBe("color");
    const c = checkValues({ ...EMPTY_VALUES, ang1: "360", color: "#1F6BFF" });
    expect(c.ok && [c.shape.ang1, c.shape.color, c.shape.centre]).toEqual([0, "#1f6bff", null]);
  });

  it("the ellipsoid of the handles: a1 = size, a2 = size·r2, a3 = size·r3; a drag — the angles, size = a1, the ratios", () => {
    const t = readTrend(CORE_LIST.trends[2]);
    const m = modelOf(t);
    expect(m.angles).toEqual({ ang1: 112.5, ang2: -35, ang3: -20 });
    expect(m.ranges.a1).toBe(250);
    expect(m.ranges.a2).toBeCloseTo(90, 12);
    expect(m.ranges.a3).toBeCloseTo(12.5, 12);
    const v = withModel(valuesOf(t), { angles: { ang1: 120, ang2: -35, ang3: -20 }, ranges: { a1: 300, a2: 90, a3: 12.5 } });
    expect(v).toMatchObject({ ang1: "120", size: "300", r2: "0,3", r3: "0,0416666666667" });
    expect(v.name).toBe("Тренд · Золото");
  });

  it("the arguments of `trend.save` of a window: new / stored / a copy of a model; the centre emptied", () => {
    const fresh = saveArgs(newEdit({ values: { ...EMPTY_VALUES, name: "СВ", ang1: "45", r2: "0,5", r3: "0,2" } }));
    expect(fresh).toEqual({ args: { kind: "global", name: "СВ", size: 100, color: "#f46a1a", ang1: 45, ang2: 0, ang3: 0, ratios: "0,5; 0,2" } });
    const t = readTrend(CORE_LIST.trends[0]);
    const stored = saveArgs(newEdit({ trendId: 5, values: { ...valuesOf(t), cx: "", cy: "", cz: "" }, origin: t.origin }));
    expect(stored).toMatchObject({ args: { trend_id: 5, centre: "" } });
    const copy = saveArgs(newEdit({ fromModel: { model_id: 3, structure: 2 }, values: { ...EMPTY_VALUES, ang1: "112,5", size: "250" } }));
    expect(copy).toEqual({ args: { kind: "global", size: 250, color: "#f46a1a", model_id: 3, structure: 2 } });
    expect(saveArgs(newEdit({ values: { ...EMPTY_VALUES, r2: "-1" } }))).toEqual({ param: "r2", error: expect.stringMatching(/a2 \/ a1/) });
    const ell = saveArgs(newEdit({ origin: { type: "ellipsoid" } }));
    expect("args" in ell && ell.args.origin).toBe("ellipsoid");
  });

  it("the body of `trend.save` of its arguments is checked; the forms of ratios and centre", () => {
    expect(ratiosArg("0,36; 0,05")).toEqual([1, 0.36, 0.05]);
    expect(ratiosArg("1; 0,36; 0,05")).toEqual([1, 0.36, 0.05]);
    expect(ratiosArg([0.5, 0.2])).toEqual([1, 0.5, 0.2]);
    expect(typeof ratiosArg("2; 0,5; 0,2")).toBe("string");
    expect(typeof ratiosArg("0; 1")).toBe("string");
    expect(ratiosArg(undefined)).toBeNull();
    expect(centreArg("5000123,25; 7200456,5; 312,125")).toEqual([5000123.25, 7200456.5, 312.125]);
    expect(centreArg("")).toBeNull();
    expect(centreArg(undefined)).toBeUndefined();
    expect(typeof centreArg("1; 2")).toBe("string");
    expect(bodyOfArgs({ ang1: 360, ang2: "-3", ratios: "0,3; 0,07", color: "#F46A1A", origin: "ellipsoid" })).toEqual({ body: { ang1: 0, ang2: -3, ratios: [1, 0.3, 0.07], color: "#f46a1a", origin: { type: "ellipsoid" } } });
    expect(bodyOfArgs({ model_id: 3, structure: 2, size: 75 })).toEqual({ body: { model_id: 3, structure: 2, size: 75 } });
    expect(bodyOfArgs({ structure: 2 })).toMatchObject({ param: "structure" });
    expect(bodyOfArgs({ kind: "structural", ang1: 0 })).toMatchObject({ param: "kind", error: expect.stringMatching(/источником/) });
    expect(bodyOfArgs({ ang2: 91 })).toMatchObject({ param: "ang2" });
    expect(bodyOfArgs({ size: 1e8 })).toMatchObject({ param: "size" });
  });
});

describe("the view key `ellipsoids` (as `viewstate/keys.py` reads it)", () => {
  it("junk dropped, a repeat — the first, visible by default; show / hide / drop", () => {
    expect(ellipsoidEntries([{ object: 4 }, { object: 4, visible: false }, { object: -1 }, "x", { object: 5, visible: false }])).toEqual([{ object: 4, visible: true }, { object: 5, visible: false }]);
    let v: unknown = undefined;
    v = showObject(4, true)(v);
    v = showObject(5, true)(v);
    v = showObject(4, false)(v);
    expect(v).toEqual([{ object: 4, visible: false }, { object: 5, visible: true }]);
    expect(shownObjects(v)).toEqual([5]);
    expect(dropObject(5)(v)).toEqual([{ object: 4, visible: false }]);
    let many: unknown = Array.from({ length: 50 }, (_, i) => ({ object: i + 1, visible: i !== 10 }));
    many = showObject(99, true)(many);
    expect(ellipsoidEntries(many)).toHaveLength(50);
    expect(shownObjects(many)).toContain(99);
    expect(ellipsoidEntries(many).some((e) => e.object === 11)).toBe(false); // the oldest hidden went first
  });
});

describe("«Сохранить как тренд» of a variogram window", () => {
  const MODEL = { type: "semivariogram" as const, c0: 0.1, common_axes: false, structures: [
    { type: "spherical" as const, c: 0.5, ang1: 30, ang2: -10, ang3: 5, a1: 40, a2: 30, a3: 8 },
    { type: "exponential" as const, c: 0.6, ang1: 112.5, ang2: -35, ang3: -20, a1: 250, a2: 90, a3: 12.5 },
  ] }; // prettier-ignore
  const DEFAULTS = { extent: { min: [0, 0, -100], max: [500, 400, 0] } } as unknown as DefaultsAnswer;

  it("a saved model unchanged — a copy of its structure (the largest a1 by default); else the axes, «ellipsoid»", () => {
    expect(trendArgsOfWork(newWork({ model: MODEL, modelId: 3, defaults: DEFAULTS }))).toEqual({ kind: "global", model_id: 3, structure: 2, centre: "250; 200; -50" });
    expect(trendArgsOfWork(newWork({ model: MODEL, modelId: 3, dirty: true, ellipsoid: { visible: true, structure: 0, centre: [1, 2, 3] } }))).toEqual({ kind: "global", ang1: 30, ang2: -10, ang3: 5, ratios: "0,75; 0,2", size: 40, origin: "ellipsoid", centre: "1; 2; 3" });
    expect(trendArgsOfWork(newWork({ model: { ...MODEL, common_axes: true }, ellipsoid: { visible: true, structure: 1, centre: null } }))).toMatchObject({ ang1: 30, ang2: -10, ang3: 5, size: 250 });
    expect(trendArgsOfWork(newWork())).toBeNull();
    // the arguments pass the checks of `trend.save` as they are
    const a = trendArgsOfWork(newWork({ model: MODEL, dirty: true }))!;
    expect(bodyOfArgs(a)).toEqual({ body: { ang1: 112.5, ang2: -35, ang3: -20, size: 250, ratios: [1, 0.36, 0.05], origin: { type: "ellipsoid" } } });
  });
});
