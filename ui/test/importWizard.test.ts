import { describe, expect, it } from "vitest";
import { blockers, canStart, clashOf, filesOf, initialState, replaces, stepOf, tableNameOf, type FileEntry } from "../src/modules/importing/wizard";

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

  it("two files into one table are refused; several collar files are joined", () => {
    const a = file({ id: 1, name: "assay_2023.csv", kind: "assay", tableName: "assay_2023", check: check([]) });
    const b = file({ id: 2, name: "assay_2024.csv", kind: "assay", tableName: "assay_2024", check: check([]) });
    expect(clashOf({ ...initialState(), files: [a, b] })).toContain("одна таблица проб");
    expect(canStart({ ...initialState(), files: [a, b] })).toBe(false);
    const l1 = file({ id: 3, name: "l1.csv", kind: "interval", tableName: "Литология", check: check([]) });
    const l2 = file({ id: 4, name: "l2.csv", kind: "interval", tableName: " литология ", check: check([]) });
    expect(clashOf({ ...initialState(), files: [l1, l2] })).toContain("«литология»");
    const c1 = file({ id: 5, kind: "collar", check: check([]) });
    const c2 = file({ id: 6, kind: "collar", check: check([]) });
    expect(clashOf({ ...initialState(), files: [c1, c2, a, l1] })).toBeNull();
    expect(tableNameOf(file({ kind: "assay", tableName: "" }))).toBe("Пробы (Assay)");
  });

  it("shows what the import replaces in an existing set («Заменить»: assay by type, others by name)", () => {
    const sets = [{ id: 7, name: "Н", holes: 3, tables: [{ name: "assay_2023", kind: "assay", rows: 1200 }, { name: "Литология", kind: "interval", rows: 5 }] }];
    const s = { ...initialState(), setMode: "existing" as const, setId: 7, sets };
    expect(replaces(s, file({ kind: "assay", tableName: "assay_2024" }))).toBe("таблицу «assay_2023» (1\u00a0200 строк)");
    expect(replaces(s, file({ kind: "interval", tableName: "литология" }))).toContain("«Литология»");
    expect(replaces(s, file({ kind: "interval", tableName: "RQD" }))).toBeNull();
    expect(replaces(s, file({ kind: "collar" }))).toBe("устья набора");
    expect(replaces({ ...s, setMode: "new" }, file({ kind: "assay" }))).toBeNull();
  });
});
