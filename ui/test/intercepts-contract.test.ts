// The interface of Э15 against the contract docs/progress/e15-api.md (commit e2d7e1d): the bodies built from the
// forms are the exact examples of the contract (`POST /intercepts/tt`, `/run`, the five sources of `orientation`),
// the answer of `tt/preview` in the form of the contract is read (`rows` as columns, TT_REL, `counts`,
// `set_summary`), and `param` of a refusal («orientation.0.alpha», «orientation.0.attitudes.3.code») marks the
// field of the form. The core is a fetch stub.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defaultValues } from "../src/modules/composites/params";
import { startRun, toTtPreview } from "../src/modules/intercepts/api";
import { coreErrors, DEFAULT_ORIENTATION, formKey, interceptBody, RUN_TOOL, TT_TOOL, type OrientationState } from "../src/modules/intercepts/params";
import { CoreError, setCoreTarget } from "../src/shell/api";

const st = (patch: Partial<OrientationState>): OrientationState => ({ ...DEFAULT_ORIENTATION, ...patch });
const GLOBAL = st({ alpha: "30", dipdir: "120" });

// the examples of the contract, as written there
const ORIENT = {
  global: { source: "global", alpha: 30, dipdir: 120 },
  domainField: { source: "domain", field: "DOMAIN", table: "12", attitudes: [{ code: "QZ", alpha: 30, dipdir: 120 }] },
  domainWf: { source: "domain", wireframe_ids: [5, 6], attitudes: [{ code: "Тело 1", alpha: 30, dipdir: 120 }] },
  mesh: { source: "mesh", wireframe_ids: [5, 6], d_max: 50, r_n: 0 },
  core: { source: "core", table: "14", alpha: "ALPHA_K", beta: "BETA_K", line: "boh", d_core: 10 },
};
const TT_EXAMPLE = { set_id: 3, table: "12", orientation: [ORIENT.global], eps: 0.1, step: 1, angles: true };
const RUN_EXAMPLE = {
  set_id: 3,
  table: "12",
  field: "AU",
  cutoff: 1,
  include_cutoff: 1.5,
  l_min: 2,
  min_by: "length",
  waste_max: 2,
  missing: "waste",
  density: null,
  fields: ["CU"],
  orientation: [ORIENT.global],
  eps: 0.1,
  step: 1,
  name: "Рудные пересечения",
  replace: false,
};

describe("the bodies of the forms are the examples of the contract", () => {
  it("`POST /intercepts/tt`", () => {
    const r = interceptBody(TT_TOOL, { setId: 3, table: "12" }, defaultValues(TT_TOOL.params), GLOBAL);
    expect(r.reason).toBeNull();
    expect(r.body).toStrictEqual(TT_EXAMPLE);
  });

  it("the five sources of `orientation`", () => {
    const one = (s: OrientationState) => interceptBody(TT_TOOL, { setId: 3, table: "12" }, defaultValues(TT_TOOL.params), s).body?.orientation;
    expect(one(GLOBAL)).toStrictEqual([ORIENT.global]);
    // a blank last row of the grid is not a row; spaces round a code go
    expect(one(st({ source: "domain", domainField: "DOMAIN", domainTable: "12", attitudes: [{ code: " QZ ", alpha: "30", dipdir: "120" }, { code: "", alpha: "", dipdir: "" }] }))).toStrictEqual([ORIENT.domainField]);
    expect(one(st({ source: "domain", domainBy: "wireframes", domainWireframes: [5, 6], attitudes: [{ code: "Тело 1", alpha: "30", dipdir: "120" }] }))).toStrictEqual([ORIENT.domainWf]);
    expect(one(st({ source: "mesh", meshWireframes: [5, 6] }))).toStrictEqual([ORIENT.mesh]);
    expect(one(st({ source: "core", coreTable: "14", coreAlpha: "ALPHA_K", coreBeta: "BETA_K" }))).toStrictEqual([ORIENT.core]);
    // «если не найдена — глобальная» — the second source; Aп 360 → 0
    expect(one(st({ source: "mesh", meshWireframes: [5, 6], fallback: true, fallbackAlpha: "30", fallbackDipdir: "360" }))).toStrictEqual([ORIENT.mesh, { source: "global", alpha: 30, dipdir: 0 }]);
  });

  it("`POST /intercepts/run` — the body sent, exactly", async () => {
    const values = { ...defaultValues(RUN_TOOL.params), cutoff: "1", include_cutoff: "1,5" };
    const r = interceptBody(RUN_TOOL, { setId: 3, table: "12", field: "AU", grades: ["CU"] }, values, GLOBAL);
    expect(r.reason).toBeNull();
    const sent: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        sent.push(JSON.parse(String(init?.body)));
        return new Response(JSON.stringify({ source: "core", id: 9 }), { status: 200, headers: { "Content-Type": "application/json" } });
      }),
    );
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    expect(await startRun(r.body!, async () => "cancel")).toEqual({ source: "core", id: 9 });
    expect(sent).toStrictEqual([RUN_EXAMPLE]);
    vi.unstubAllGlobals();
    setCoreTarget(null);
  });

  it("`/run`: no `angles`; `fields` absent — every numeric field, [] — only `field`; c > 0; name ≤ 200", () => {
    for (const bad of ["0", "-0,5"]) {
      const r = interceptBody(RUN_TOOL, { setId: 3, table: "12", field: "AU" }, { ...defaultValues(RUN_TOOL.params), cutoff: bad }, st({ enabled: false }));
      expect(r.body, bad).toBeNull();
      expect(r.errors.cutoff).toBe("«Бортовое содержание c»: должно быть больше нуля");
      expect(r.reason).toBe(r.errors.cutoff);
    }
    const values = { ...defaultValues(RUN_TOOL.params), cutoff: "0,5" };
    const all = interceptBody(RUN_TOOL, { setId: 3, table: "12", field: "AU", grades: null }, values, st({ enabled: false })).body!;
    expect(all).not.toHaveProperty("angles");
    expect(all).not.toHaveProperty("fields");
    expect(all).not.toHaveProperty("orientation");
    expect(all).toMatchObject({ cutoff: 0.5, include_cutoff: null, density: null });
    expect(interceptBody(RUN_TOOL, { setId: 3, table: "12", field: "AU", grades: ["AU"] }, values, st({ enabled: false })).body?.fields).toEqual([]);
    const long = interceptBody(RUN_TOOL, { setId: 3, table: "12", field: "AU" }, { ...values, name: "Я".repeat(201) }, st({ enabled: false }));
    expect(long.body).toBeNull();
    expect(long.errors.name).toBe("«Имя таблицы»: не больше 200 знаков");
  });
});

describe("the answer of `tt/preview` in the form of the contract", () => {
  it("rows as columns, the counts and the counts of the table", () => {
    const a = toTtPreview({
      hole: "СКВ-1",
      rows: { from: [0, 2], to: [2, 3], length: [2, 1], TT: [1.5, null], TT_REL: [0.75, null], TT_FLAG: [0, 2], TT_SRC: ["глобальная", "нет"], ALPHA: [30, null], DIPDIR: [120, null], HOLE_THETA: [10, 10], HOLE_AZ: [45, 45] },
      counts: { src: { глобальная: 1, нет: 1 }, flags: { "1": 0, "2": 1, "4": 0, "8": 0 } },
      mesh_skipped: false,
      set_summary: { src: { глобальная: 10, нет: 2 }, flags: { "1": 0, "2": 2, "4": 0, "8": 0 } },
      notes: ["…"],
    });
    expect(a.rows).toEqual([
      { from: 0, to: 2, length: 2, tt: 1.5, ratio: 0.75, alpha: 30, dipdir: 120, theta: 10, azimuth: 45, src: "глобальная", flag: 0 },
      { from: 2, to: 3, length: 1, tt: null, ratio: null, alpha: null, dipdir: null, theta: 10, azimuth: 45, src: "нет", flag: 2 },
    ]);
    expect(a.bySrc).toEqual({ глобальная: 1, нет: 1 });
    expect(a.byFlag).toEqual({ 1: 0, 2: 1, 4: 0, 8: 0 });
    expect(a.setCounts).toEqual({ bySrc: { глобальная: 10, нет: 2 }, byFlag: { 1: 0, 2: 2, 4: 0, 8: 0 } });
  });
});

describe("`param` of a refusal marks the field of the form", () => {
  it("paths inside the orientation", () => {
    expect(formKey("orientation.0.alpha", GLOBAL)).toBe("alpha");
    expect(formKey("orientation", GLOBAL)).toBe("orientation");
    expect(formKey("eps", GLOBAL)).toBe("eps");
    expect(formKey("table", GLOBAL)).toBeNull();
    const dom = st({ source: "domain", domainField: "D", attitudes: [{ code: "A", alpha: "1", dipdir: "1" }, { code: "", alpha: "", dipdir: "" }, { code: "B", alpha: "1", dipdir: "1" }], fallback: true, fallbackAlpha: "1", fallbackDipdir: "1" });
    // the core counts the rows it got (without the blank one): its row 1 is the row 2 of the grid
    expect(formKey("orientation.0.attitudes.1.code", dom)).toBe("attitudes.2.code");
    expect(formKey("orientation.0.attitudes", dom)).toBe("attitudes");
    expect(formKey("orientation.0.field", dom)).toBe("domain");
    expect(formKey("orientation.1.dipdir", dom)).toBe("fallback_dipdir");
    expect(formKey("orientation.0.wireframe_ids", st({ source: "mesh" }))).toBe("wireframes");
    expect(formKey("orientation.0.d_max", st({ source: "mesh" }))).toBe("d_max");
    expect(formKey("orientation.0.beta", st({ source: "core" }))).toBe("core");
    expect(formKey("orientation.0.d_core", st({ source: "core" }))).toBe("d_core");
  });

  it("a CoreError with `param` → {key: message}; without — nothing", () => {
    const e = new CoreError(422, "bad_param", "Угол падения α: значение вне диапазона от 0 до 90°.", "Задайте от 0 до 90°.");
    e.body = { code: "bad_param", param: "orientation.0.alpha" };
    expect(coreErrors(e, GLOBAL)).toEqual({ alpha: "Угол падения α: значение вне диапазона от 0 до 90°." });
    expect(coreErrors(new CoreError(404, "set_not_found", "Нет набора", ""), GLOBAL)).toEqual({});
  });
});

beforeEach(() => vi.unstubAllGlobals());
afterEach(() => setCoreTarget(null));
