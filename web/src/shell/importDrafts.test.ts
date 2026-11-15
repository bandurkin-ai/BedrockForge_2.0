import { describe, expect, it } from "vitest";
import type { UploadResponse } from "../api";
import { buildRequest, distribute, makeDraft, missingFields, tableKind, uniqueName } from "./importDrafts";

const upload = (filename: string, kind: string | null, columns: string[], suggestions: UploadResponse["suggestions"] = {}): UploadResponse => ({
  file_id: filename, filename, format: "text", row_count: 10,
  columns: columns.map((name) => ({ name, dtype: "float64", numeric: name !== "BHID" && name !== "LITH", sample: [] })),
  detected_kind: kind, suggestions, preview: [], options: { encoding: "utf-8", delimiter: ",", header_row: 1, sheet: null }, sheets: [], notes: [],
});

describe("drillhole import wizard", () => {
  it("puts files on their steps by the detected kind", () => {
    const files = [upload("assay.csv", "assay", []), upload("collar.csv", "collar", []), upload("survey.csv", "survey", []), upload("lith.csv", null, []), upload("collar2.csv", "collar", [])];
    const d = distribute(files, { collar: false, survey: false });
    expect(d.collar?.filename).toBe("collar.csv");
    expect(d.survey?.filename).toBe("survey.csv");
    expect(d.tables.map((u) => u.filename)).toEqual(["assay.csv", "lith.csv", "collar2.csv"]);
    expect(distribute(files, { collar: false, survey: true }).tables).toHaveLength(4);
  });

  it("reads an unknown file as an interval table and keeps its columns", () => {
    const u = upload("lith.csv", null, ["BHID", "FROM", "TO", "LITH"], { interval: { mapping: { hole_id: "BHID", from: "FROM", to: "TO" }, components: [] } });
    expect(tableKind(u)).toBe("interval");
    expect(tableKind(upload("c.csv", "collar", []))).toBe("interval");
    expect(tableKind(upload("e.csv", "event", []))).toBe("event");
    const d = makeDraft(u, "interval");
    expect(d.tableName).toBe("lith");
    expect(d.attributes).toEqual(["LITH"]);
    expect(buildRequest(d)).toMatchObject({ kind: "interval", attributes: ["LITH"], components: [] });
  });

  it("sends the checked components of an assay file and names the missing fields", () => {
    const u = upload("a.csv", "assay", ["BHID", "FROM", "TO", "AU", "CU"], { assay: { mapping: { hole_id: "BHID", from: "FROM" }, components: [{ column: "AU", unit: "г/т" }] } });
    const d = makeDraft(u, "assay", "Пробы");
    expect(buildRequest(d).components).toEqual([{ column: "AU", unit: "г/т" }]);
    const fields = [{ key: "hole_id", label: "Скважина", required: true }, { key: "to", label: "До", required: true }, { key: "sample_id", label: "Проба", required: false }];
    expect(missingFields(d, fields)).toEqual(["До"]);
  });

  it("keeps table names apart", () => {
    expect(uniqueName("lith", ["assay"])).toBe("lith");
    expect(uniqueName("lith", ["Lith", "lith 2"])).toBe("lith 3");
  });
});
