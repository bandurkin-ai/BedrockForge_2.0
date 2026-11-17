import { describe, expect, it } from "vitest";
import { blockers, canStart, filesOf, initialState, stepOf, type FileEntry } from "../src/modules/importing/wizard";

const file = (p: Partial<FileEntry>): FileEntry => ({
  id: 1, path: "a.csv", name: "a.csv", options: { header_row: 1 }, inspect: null, check: null, kind: null,
  mapping: {}, components: [], attributes: [], force: false, tableName: "a", busy: false, error: null, ...p,
});

const check = (issues: FileEntry["check"] extends infer C ? (C extends { issues: infer I } ? I : never) : never, canForce = false) => ({
  rows: 3, columns: [], kind: "assay" as const, mapping: {}, components: [], attributes: [], warnings: [], issues, preview: [], can_force: canForce, fields: [],
});

describe("import wizard state (ТЗ 7.7)", () => {
  it("files go to the step of their kind", () => {
    expect([stepOf("collar"), stepOf("survey"), stepOf("assay"), stepOf("event"), stepOf(null)]).toEqual(["collar", "survey", "tables", "tables", "tables"]);
    const s = { ...initialState(), files: [file({ id: 1, kind: "survey", check: check([]) }), file({ id: 2, kind: "assay", check: check([]) })] };
    expect(filesOf(s, "survey").map((f) => f.id)).toEqual([1]);
    expect(filesOf(s, "tables").map((f) => f.id)).toEqual([2]);
  });

  it("data errors block unless «Загрузить с ошибками»; mapping errors always block", () => {
    const data = [{ severity: "error" as const, code: "non_numeric", message: "«Au»: нечисловых значений" }];
    expect(blockers(file({ kind: "assay", check: check(data, true) }))).toHaveLength(1);
    expect(blockers(file({ kind: "assay", force: true, check: check(data, true) }))).toHaveLength(0);
    expect(blockers(file({ kind: "assay", force: true, check: check(data, false) }))).toHaveLength(1);
  });

  it("start needs ready files and a set", () => {
    const ok = file({ kind: "assay", check: check([]) });
    expect(canStart({ ...initialState(), files: [ok] })).toBe(true);
    expect(canStart({ ...initialState(), files: [ok], setName: "  " })).toBe(false);
    expect(canStart({ ...initialState(), files: [ok], setMode: "existing", setId: null })).toBe(false);
    expect(canStart({ ...initialState(), files: [file({ kind: null, check: check([]) })] })).toBe(false);
    expect(canStart({ ...initialState(), files: [] })).toBe(false);
  });
});
