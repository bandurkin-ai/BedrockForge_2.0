/** «Канавы по полилиниям» (ТЗ 7.35 Д-16; ADR 0011 §5): the steps of the wizard in the mode of trenches, the
 * parameters of the file of polylines (name from the string or from a field, «Обратить»), samples, the start. */
import { describe, expect, it } from "vitest";
import {
  initialTrench, isVertexTable, linesSpec, reverseOf, toggleReverse, trenchProblem, trenchSummary, type TrenchPreview, type TrenchState,
} from "../src/modules/importing/trenches";
import { canStart, initialState, sampleFile, stepsOf, trenchBody, type FileEntry, type WizardState } from "../src/modules/importing/wizard";

const preview = (p: Partial<TrenchPreview> = {}): TrenchPreview => ({
  format: "micromine_str", layers: [{ name: "TR-1", lines: 1 }, { name: "TR-2", lines: 1 }], fields: ["JOIN"], trenches: 2, vertices: 7,
  length: 162.25, skipped: 0, warnings: [], preview: [{ name: "TR-1", vertices: 4, length: 100.25, x: 1, y: 2, z: 3 }, { name: "TR-2", vertices: 3, length: 62, x: 4, y: 5, z: 6 }], ...p,
});

const trench = (p: Partial<TrenchState> = {}): TrenchState => ({ ...initialTrench(), path: "/d/trenches.str", name: "trenches.str", preview: preview(), ...p });

const file = (p: Partial<FileEntry>): FileEntry => ({
  id: 1, path: "/d/samples.csv", name: "samples.csv", options: { header_row: 1 }, inspect: null, kind: "assay", mapping: { hole_id: "TRENCH", from: "FROM", to: "TO" },
  components: [{ column: "AU", unit: "g/t" }], attributes: [], force: false, tableName: "samples", busy: false, error: null,
  check: { rows: 4, columns: [], kind: "assay", mapping: {}, components: [], attributes: [], warnings: [], issues: [], preview: [], fields: [] }, ...p,
});

const wizard = (p: Partial<WizardState> = {}): WizardState => ({ ...initialState("trenches"), trench: trench(), ...p });

describe("the wizard in the mode of trenches", () => {
  it("step 1 is «Канавы по полилиниям», then samples and the import; holes keep their four steps", () => {
    expect(stepsOf("trenches").map((s) => s.title)).toEqual(["Канавы по полилиниям", "Пробы канав", "Импорт"]);
    expect(stepsOf("holes").map((s) => s.id)).toEqual(["collar", "survey", "tables", "import"]);
    expect(initialState("trenches")).toMatchObject({ mode: "trenches", step: "trenches", setName: "Канавы" });
    expect(initialState()).toMatchObject({ mode: "holes", step: "collar", setName: "Скважины" });
  });

  it("the file of polylines: name from the string by default, or from a field; layers; «Обратить»", () => {
    expect(linesSpec(trench())).toEqual({ path: "/d/trenches.str", name_from: "string", reverse: false });
    expect(linesSpec(trench({ nameFrom: "field", nameField: " JOIN " }))).toEqual({ path: "/d/trenches.str", name_from: "field", name_field: "JOIN", reverse: false });
    expect(linesSpec(trench({ layers: ["TR-2"], reverseAll: true, reversed: ["TR-1"], encoding: "cp866" }))).toEqual({
      path: "/d/trenches.str", name_from: "string", layers: ["TR-2"], encoding: "cp866", reverse: true,
    });
    expect(linesSpec(trench({ reversed: ["TR-2"] })).reverse).toEqual(["TR-2"]);
  });

  it("a table of vertices names by a column; X, Y, Z are given only when typed", () => {
    const t = trench({ path: "/d/v.csv", preview: preview({ format: "points", fields: ["TRENCH", "NOTE"], layers: [] }), nameField: "TRENCH", columns: { x: "EAST", y: "", z: " RL " } });
    expect(isVertexTable(t)).toBe(true);
    expect(linesSpec(t)).toEqual({ path: "/d/v.csv", name_from: "string", name_field: "TRENCH", x: "EAST", z: "RL", reverse: false });
    expect(isVertexTable(trench())).toBe(false);
    // before the first answer of the core the extension tells a table
    expect(isVertexTable(trench({ path: "/d/v.CSV", preview: null }))).toBe(true);
    expect(isVertexTable(trench({ path: "/d/v.dxf", preview: null }))).toBe(false);
  });

  it("«Обратить направление»: every trench, or the ticked ones", () => {
    expect(reverseOf(trench())).toBe(false);
    expect(reverseOf(trench({ reverseAll: true, reversed: ["TR-1"] }))).toBe(true);
    expect(toggleReverse(["TR-1"], "TR-2", true)).toEqual(["TR-1", "TR-2"]);
    expect(toggleReverse(["TR-1", "TR-2"], "TR-1", false)).toEqual(["TR-2"]);
    expect(toggleReverse(["TR-1"], "TR-1", true)).toEqual(["TR-1"]);
  });

  it("the summary: trenches, vertices, the total 3D length, what was left out", () => {
    expect(trenchSummary(preview())).toBe("Канав: 2 · вершин: 7 · общая длина по 3D: 162,3 м");
    expect(trenchSummary(preview({ trenches: 1250, vertices: 62500, length: 98765.44, skipped: 3 }))).toBe("Канав: 1\u00a0250 · вершин: 62\u00a0500 · общая длина по 3D: 98\u00a0765,4 м · линий без канавы: 3");
  });

  it("what stops the import is told in words", () => {
    expect(trenchProblem(initialTrench())).toBe("Выберите файл с линиями канав");
    expect(trenchProblem(trench({ busy: true }))).toBe("Файл читается…");
    expect(trenchProblem(trench({ error: "В таблице вершин не найдена колонка с именем канавы.", preview: null }))).toContain("не найдена колонка");
    expect(trenchProblem(trench({ nameFrom: "field", nameField: " " }))).toBe("Выберите поле с именем канавы");
    expect(trenchProblem(trench({ preview: preview({ trenches: 0, preview: [] }) }))).toBe("В файле нет ни одной канавы: проверьте слои и источник имени");
    expect(trenchProblem(trench())).toBeNull();
  });
});

describe("the start of `import.trenches`", () => {
  it("needs the trenches and a set; samples are optional", () => {
    expect(canStart(wizard())).toBe(true);
    expect(canStart(wizard({ trench: initialTrench() }))).toBe(false);
    expect(canStart(wizard({ setName: " " }))).toBe(false);
    expect(canStart(wizard({ setMode: "existing", setId: null }))).toBe(false);
    expect(canStart(wizard({ starting: true }))).toBe(false);
    expect(canStart(wizard({ files: [file({})] }))).toBe(true);
  });

  it("samples: one table of samples or intervals, mapped and without blocking errors", () => {
    expect(canStart(wizard({ files: [file({ kind: "collar" })] }))).toBe(false);
    expect(canStart(wizard({ files: [file({ id: 1 }), file({ id: 2, name: "more.csv" })] }))).toBe(false);
    expect(canStart(wizard({ files: [file({ busy: true })] }))).toBe(false);
    expect(sampleFile(wizard({ files: [file({})] }))?.name).toBe("samples.csv");
    expect(sampleFile(wizard())).toBeNull();
  });

  it("a set that has a table of samples already: replacing it has to be ticked", () => {
    const sets = [{ id: 7, name: "Канавы 2025", holes: 3, tables: [{ name: "Пробы", kind: "assay", rows: 1200 }] }];
    const s = wizard({ files: [file({})], setMode: "existing", setId: 7, sets });
    expect(canStart(s)).toBe(false);
    expect(canStart({ ...s, replaceSamples: true })).toBe(true);
    // an interval table of its own name replaces nothing; trenches without samples need no tick
    expect(canStart({ ...s, files: [file({ kind: "interval", tableName: "Борозды" })] })).toBe(true);
    expect(canStart({ ...s, files: [] })).toBe(true);
  });

  it("the body: the set, the file of polylines, the samples as a file of the drillhole wizard", () => {
    const s = wizard({ files: [file({})], trench: trench({ reversed: ["TR-2"] }), setName: " Канавы 2026 ", crs: "WKT" });
    expect(trenchBody(s)).toEqual({
      set_id: null, set_name: "Канавы 2026", crs: "WKT",
      lines: { path: "/d/trenches.str", name_from: "string", reverse: ["TR-2"] },
      samples: {
        path: "/d/samples.csv", options: { header_row: 1 }, kind: "assay", mapping: { hole_id: "TRENCH", from: "FROM", to: "TO" },
        components: [{ column: "AU", unit: "g/t" }], attributes: [], force: false, table_name: "samples", replace: false,
      },
    });
    const into = trenchBody(wizard({ setMode: "existing", setId: 7, replaceSamples: true }));
    expect(into).toMatchObject({ set_id: 7, set_name: null, samples: null });
  });
});
