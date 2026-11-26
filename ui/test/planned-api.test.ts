// The planned holes (Э19б, 19.39; ТЗ 7.35 Д-34 ч. 2) on the REAL answers of the core (`core/tests/data/implicit/contract_answers.json`, keys `planned_*`): the
// readers (a gap stays `null`, a path is float64, the order of the keys is not relied on), the form (a decimal comma, the three modes, the ranges of
// the core, the words under a field), the body of `POST` / `PUT`, the words about a hole, the points of the chart.
import { describe, expect, it } from "vitest";
import { readCompare, readEvaluate, readExport, readRow, readRows, readTrajectory } from "../src/modules/drillholes/plannedApi";
import { bodyOf, chartPoints, compareLine, EMPTY_FORM, evaluateLine, exportName, fileSafe, formOfArgs, formOfRow, freeName, rowLine, sameForm, type PlannedForm } from "../src/modules/drillholes/plannedModel";
import { isIssue } from "../src/modules/estimation/formBase";
import { AIM, FIRST, R } from "./plannedHarness";

const flat = (s: string): string => s.replace(/[  ]/g, " ");

describe("the readers on the real answers", () => {
  it("a hole: the parameters, the path every 1 m as float64, the target", () => {
    const r = readRow(FIRST);
    expect(r.id).toBe(FIRST.id);
    expect(r.name).toBe("ПС-1");
    expect(r.version).toBe(1);
    expect(r.meta.collar).toEqual([210, 210, 330]);
    expect(r.meta.change).toEqual({ azimuth: 1.5, dip: 0.5 });
    expect(r.meta.target).toBeNull();
    expect(r.meta.method).toBe("minimum_curvature");
    expect(r.meta.set_id).toBe(1);
    expect(r.target_depth).toBeNull();
    const t = r.trajectory!;
    expect(t.depth).toBeInstanceOf(Float64Array);
    expect(t.depth.length).toBe(FIRST.trajectory.depth.length);
    expect([t.depth[0], t.depth[1], t.depth[t.depth.length - 1]]).toEqual([0, 1, 100]);
    expect([t.x[0], t.y[0], t.z[0]]).toEqual([210, 210, 330]); // the path starts at the collar
    expect(t.z[t.z.length - 1]).toBeLessThan(t.z[0]); // and goes down
  });

  it("a hole through a target: the target on the path at its depth, the azimuth and the dip solved by the core", () => {
    const r = readRow(AIM);
    expect(r.meta.target).toEqual([110, 100, 270]);
    expect(r.meta.change).toBeNull();
    expect(r.target_depth).toBeCloseTo(Math.hypot(50, 40, 60), 9);
    expect(r.meta.length).toBe(r.target_depth); // the hole ends at the target
    const t = r.trajectory!;
    const n = t.depth.length - 1;
    expect(Math.hypot(t.x[n] - 110, t.y[n] - 100, t.z[n] - 270)).toBeLessThan(1e-6); // ТД34-3 at the end of the path
  });

  it("the list has no paths; with them — the same rows", () => {
    const rows = readRows(R.planned_list.body);
    expect(rows.map((r) => r.name)).toEqual(["ПС-1", "ПС-2 (через цель)"]);
    expect(rows.every((r) => r.trajectory === null)).toBe(true);
    expect(readRows({ planned_holes: [FIRST, AIM] }).every((r) => r.trajectory !== null)).toBe(true);
    expect(readRows(null)).toEqual([]);
    expect(readRows({ planned_holes: [{ name: "без номера" }] })).toEqual([]);
  });

  it("a path with a gap is not a path: a non-number in a column is never read as 0", () => {
    expect(readTrajectory({ depth: [0, 1], x: [1, null], y: [1, 2], z: [1, 2] })).toBeNull();
    expect(readTrajectory({ depth: [0, 1], x: [1], y: [1, 2], z: [1, 2] })).toBeNull();
    expect(readTrajectory({ depth: [], x: [], y: [], z: [] })).toBeNull();
  });

  it("the forecast: a gap between the bodies has the code null (not 0), the lengths of the bodies, the nearest holes", () => {
    const a = readEvaluate(R.planned_evaluate.body);
    expect(a.intervals!.map((i) => i.code)).toEqual([null, "SS", null]);
    expect(a.intervals![0].from).toBe(0);
    expect(a.intervals![a.intervals!.length - 1].to).toBe(a.length);
    expect(a.codes!.find((c) => c.code === "SS")!.length).toBeCloseTo(a.inside!, 9);
    expect(a.nearest!.length).toBeLessThanOrEqual(5);
    expect(a.nearest![0]).toMatchObject({ hole: "H057", hole_id: 57 });
    expect(a.nearest!.map((n) => n.distance)).toEqual([...a.nearest!.map((n) => n.distance)].sort((x, y) => x - y));
    expect(a.journal.length).toBeGreaterThan(0);
    expect(a.target.depth).toBe(a.length); // no target: the end of the hole
  });

  it("without bodies the forecast is empty (null, not an empty table); the nearest holes of the target", () => {
    const a = readEvaluate(R.planned_evaluate_set.body);
    expect(a.intervals).toBeNull();
    expect(a.codes).toBeNull();
    expect(a.inside).toBeNull();
    expect(a.nearest!.length).toBe(5);
    expect(a.target.point).toEqual([110, 100, 270]);
    expect(a.set_id).toBe(1);
  });

  it("«План / факт»: the rows every 1 m, the largest deviation with its depth, the lengths", () => {
    const a = readCompare(R.planned_compare.body);
    expect(a.hole).toBe("H001");
    expect(a.rows.length).toBe(121);
    expect(a.rows[1].depth - a.rows[0].depth).toBe(1);
    expect(a.max).toBe(Math.max(...a.rows.map((r) => r.d)));
    expect(a.rows.find((r) => r.depth === a.max_depth)!.d).toBe(a.max);
    expect(a.length).toEqual({ planned: 120, actual: 126.3, compared: 120 });
    expect(a.rows[0].d).toBeCloseTo(Math.hypot(a.rows[0].dx, a.rows[0].dy, a.rows[0].dz), 9);
    expect(chartPoints(a.rows)[0]).toEqual({ x: 0, y: a.rows[0].d });
    expect(readCompare({}).max).toBeNull(); // nothing compared is not 0
  });

  it("the files", () => {
    expect(readExport(R.planned_export_csv.body)).toMatchObject({ format: "csv", files: ["collar.csv", "survey.csv"] });
    expect(readExport(R.planned_export_dxf.body)).toMatchObject({ format: "dxf", files: ["ПС-1.dxf"] });
    expect(readExport(R.planned_export_dxf.body).points).toBeGreaterThan(100);
  });
});

describe("the form", () => {
  it("a stored hole gives its form: the three modes", () => {
    const turning = formOfRow(readRow(FIRST));
    expect(turning).toMatchObject({ name: "ПС-1", mode: "change", azimuth: "90", dip: "-70", length: "100", changeAzimuth: "1,5", changeDip: "0,5", setId: 1 });
    expect(turning.collar).toEqual(["210", "210", "330"]);
    const aim = formOfRow(readRow(AIM));
    expect(aim.mode).toBe("target");
    expect(aim.target).toEqual(["110", "100", "270"]);
    expect(aim.length).toBe(""); // a hole that ends at its target has no length of its own
    const past = readRow({ ...AIM, meta: { ...AIM.meta, length: 120 } });
    expect(formOfRow(past).length).toBe("120"); // one that goes on past the target keeps it
    expect(formOfRow(readRow({ ...FIRST, meta: { ...FIRST.meta, change: null } })).mode).toBe("straight");
  });

  it("the body of a hole that turns, of a straight one and of one through a target", () => {
    const f = formOfRow(readRow(FIRST));
    const b = bodyOf(f);
    expect(b).toEqual({ ok: true, body: { name: "ПС-1", method: "minimum_curvature", set_id: 1, collar: [210, 210, 330], azimuth: 90, dip: -70, length: 100, target: null, change: { azimuth: 1.5, dip: 0.5 } } });
    const straight = bodyOf({ ...f, mode: "straight" });
    expect(straight.ok && straight.body.change).toBeNull();
    const aim = bodyOf({ ...formOfRow(readRow(AIM)), length: "" });
    expect(aim.ok).toBe(true);
    if (aim.ok) {
      expect(aim.body).toMatchObject({ target: [110, 100, 270], change: null, length: null }); // to the target
      expect("azimuth" in aim.body).toBe(false); // the core solves them
    }
    const long = bodyOf({ ...formOfRow(readRow(AIM)), length: "200,5" });
    expect(long.ok && long.body.length).toBe(200.5);
  });

  it("a decimal comma and spaces are numbers; a name left empty is left out", () => {
    const f: PlannedForm = { ...EMPTY_FORM, collar: ["5 500,25", "1 000", "-12,5"], azimuth: "45,5", dip: "-60", length: "250" };
    const b = bodyOf(f);
    expect(b.ok && b.body.collar).toEqual([5500.25, 1000, -12.5]);
    expect(b.ok && "name" in b.body).toBe(false);
  });

  it("the ranges of the core and the words under the field (the path of the body)", () => {
    const f: PlannedForm = { ...EMPTY_FORM, collar: ["1", "2", "3"], azimuth: "10", dip: "-60", length: "100" };
    const issue = (over: Partial<PlannedForm>) => {
      const b = bodyOf({ ...f, ...over });
      expect(b.ok).toBe(false);
      return b.ok ? null : b.issue;
    };
    expect(issue({ collar: ["1", "", "3"] })).toMatchObject({ param: "collar", code: "bad_param" });
    expect(issue({ collar: ["1", "2", "три"] })!.message).toContain("Z");
    expect(issue({ azimuth: "360" })).toMatchObject({ param: "azimuth" });
    expect(issue({ azimuth: "-1" })).toMatchObject({ param: "azimuth" });
    expect(issue({ dip: "-91" })).toMatchObject({ param: "dip" });
    expect(issue({ dip: "91" })).toMatchObject({ param: "dip" });
    expect(issue({ length: "0" })).toMatchObject({ param: "length" });
    expect(issue({ length: "10001" })).toMatchObject({ param: "length" });
    expect(issue({ length: "" })).toMatchObject({ param: "length" });
    expect(issue({ mode: "change", changeAzimuth: "31" })).toMatchObject({ param: "change.azimuth" });
    expect(issue({ mode: "change", changeDip: "-30,5" })).toMatchObject({ param: "change.dip" });
    expect(issue({ mode: "target", target: ["1", "2", ""] })).toMatchObject({ param: "target" });
    expect(issue({ mode: "target", target: ["1", "2", "3"], length: "-5" })).toMatchObject({ param: "length" });
    expect(bodyOf({ ...f, azimuth: "359,999" }).ok).toBe(true);
    expect(bodyOf({ ...f, dip: "-90" }).ok).toBe(true);
    expect(bodyOf({ ...f, mode: "change", changeAzimuth: "-30", changeDip: "30" }).ok).toBe(true);
  });

  it("the arguments of a command over the form; two modes at once are a refusal", () => {
    const f = formOfArgs({ name: "Новая", x: 10, y: 20, z: 30, azimuth: 90, dip: -45, length: 120, change_azimuth: 2 }, EMPTY_FORM);
    expect(isIssue(f)).toBe(false);
    expect(f).toMatchObject({ name: "Новая", collar: ["10", "20", "30"], azimuth: "90", dip: "-45", length: "120", mode: "change", changeAzimuth: "2" });
    const t = formOfArgs({ x: 1, y: 2, z: 3, target_x: 100, target_y: 200, target_z: -50 }, EMPTY_FORM);
    expect(t).toMatchObject({ mode: "target", target: ["100", "200", "-50"] });
    expect(isIssue(formOfArgs({ target_x: 1, change_dip: 1 }, EMPTY_FORM))).toBe(true);
    expect(isIssue(formOfArgs({ method: "прыжками" }, EMPTY_FORM))).toBe(true);
  });

  it("the same numbers in another spelling are the same form", () => {
    const f = formOfRow(readRow(FIRST));
    expect(sameForm(f, { ...f, azimuth: "90,0", collar: ["210,0", "210", "330"], name: " ПС-1 " })).toBe(true);
    expect(sameForm(f, { ...f, length: "101" })).toBe(false);
    expect(sameForm(f, { ...f, mode: "straight" })).toBe(false);
    expect(sameForm(f, { ...f, setId: null })).toBe(false);
  });
});

describe("the words", () => {
  it("a hole in a line; the forecast and «План / факт» in a line; the names", () => {
    const line = flat(rowLine(readRow(FIRST)));
    expect(line).toContain("«ПС-1»");
    expect(line).toContain("устье 210; 210; 330");
    expect(line).toContain("изменение на 100 м");
    expect(flat(rowLine(readRow(AIM)))).toContain("через цель (110; 100; 270)");
    const ev = flat(evaluateLine(readEvaluate(R.planned_evaluate.body), "ПС-1"));
    expect(ev).toContain("SS — 42,93 м");
    expect(ev).toContain("H057");
    expect(flat(evaluateLine(readEvaluate(R.planned_evaluate_set.body), "ПС-2"))).not.toContain("прогноз по телам");
    const cmp = flat(compareLine(readCompare(R.planned_compare.body), "ПС-1"));
    expect(cmp).toContain("«H001»");
    expect(cmp).toContain("наибольшее отклонение 281,41 м на глубине 120 м");
    expect(compareLine(readCompare({ rows: [] }), "ПС-1")).toContain("нечего сравнивать");
    expect(exportName("ПС/1: тест", "dxf")).toBe("ПС_1_ тест.dxf");
    expect(fileSafe("   ")).toBe("Проектная скважина");
    expect(freeName(["ПС-1", "пс-2"])).toBe("ПС-3");
    expect(freeName([])).toBe("ПС-1");
  });
});
