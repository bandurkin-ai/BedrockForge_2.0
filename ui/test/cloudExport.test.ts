// The window «Экспорт линий» with a point cloud (ADR 0014 §6, `lines/cloud_export.py`): LAS «по исходнику» or
// new, the CSV of a cloud, the cloud alone — told before the request.
import { describe, expect, it } from "vitest";
import type { CloudSummary } from "../src/modules/lines/classes";
import { cloudReason, exportBody, exportName, exportReason, exportTotals, fitFormat, formatChoices, formatInfo, formatNote, lasMode, LINES_FORMAT_IDS } from "../src/modules/lines/export";
import type { LineFileInfo, LineLayerInfo } from "../src/modules/scene/objects";

const summary = (kind: string): CloudSummary => ({ parts: 1, levels: [], bbox: null, classes: { "2": 10 }, modes: ["class"], source: { kind }, ground: null, dz: null });
const layer = (id: number, p: Partial<LineLayerInfo> = {}): LineLayerInfo => ({
  id, file_id: 1, name: `Слой ${id}`, version: 1, color: null, points_only: false, line_count: 12, point_count: 340, length: 1520.46,
  closed_count: 0, closed: "none", flat: false, extent: null, attr_schema: [], ...p,
});
const cloud = (id: number, kind: string) => layer(id, { name: "survey", points_only: true, line_count: 0, point_count: 3000, cloud: summary(kind) });
const file = (id: number, layers: LineLayerInfo[]): LineFileInfo => ({ id, name: `file${id}.las`, folder: null, version: 1, format: null, label: null, filename: null, crs_name: null, warnings: [], layers });

const files = [file(1, [cloud(11, "las")]), file(2, [cloud(21, "xyz")]), file(3, [layer(31, { name: "Контур" })])];

describe("the export of a point cloud", () => {
  it("LAS and the CSV of a cloud are offered for a cloud; LAS is off for other layers", () => {
    expect(LINES_FORMAT_IDS).toContain("las");
    expect(formatChoices(files, [11]).map((f) => [f.id, f.title, f.why])).toEqual([["las", "LAS", null], ["csv", "CSV (X;Y;Z;класс;h)", null]]);
    const lines = formatChoices(files, [31]);
    expect(lines.map((f) => f.id)).toEqual(["str", "surpac", "dxf", "csv", "dat", "las"]);
    expect(lines.find((f) => f.id === "las")?.why).toBe("только облако точек");
    expect(formatNote(files, [11], "csv")).toContain("класс");
    expect(formatNote(files, [31], "csv")).toBe(formatInfo("csv").note);
  });

  it("the format follows the checked layers", () => {
    expect(fitFormat(files, [11], "str")).toBe("las"); // «Экспорт…» of a cloud, the ribbon «В STR»
    expect(fitFormat(files, [11], "csv")).toBe("csv");
    expect(fitFormat(files, [31], "las")).toBe("str");
    expect(fitFormat(files, [31], "dxf")).toBe("dxf");
  });

  it("«по исходному файлу» only for a cloud of a LAS, and by default for it", () => {
    expect(lasMode(cloud(1, "las"), null)).toBe("source");
    expect(lasMode(cloud(1, "las"), "new")).toBe("new");
    expect(lasMode(cloud(1, "xyz"), null)).toBe("new");
    expect(lasMode(cloud(1, "xyz"), "source")).toBe("new");
  });

  it("tells before the request what the core would refuse", () => {
    expect(cloudReason(files, [11, 31], "las")).toBe("Облако точек экспортируется отдельным файлом, без других слоёв: оставьте отмеченным одно облако.");
    expect(exportReason(files, [11, 21], "csv", "C:\\a.csv")).toContain("отдельным файлом");
    expect(cloudReason(files, [11], "dxf")).toBe("Облако точек не записывается в DXF: выберите LAS или CSV.");
    expect(cloudReason(files, [31], "las")).toContain("только облака точек");
    expect(exportReason(files, [11], "las", null)).toBe("Выберите файл: «Сохранить как…».");
    expect(exportReason(files, [11], "las", "C:\\a.las")).toBeNull();
    expect(exportReason(files, [21], "csv", "C:\\a.csv")).toBeNull();
  });

  it("the name, the totals and the body of the request", () => {
    expect(exportName(files, [11], "las")).toBe("survey.las");
    expect(exportTotals(files, [11])).toBe("Облако точек: 3\u00a0000 точек");
    expect(exportBody(files, [11], "las", "C:\\a.las")).toEqual({ path: "C:\\a.las", format: "las", layer_ids: [11], las: "source" });
    expect(exportBody(files, [11], "las", "C:\\a.las", undefined, "new").las).toBe("new");
    expect(exportBody(files, [21], "las", "C:\\a.las", undefined, "source").las).toBe("new");
    expect(exportBody(files, [11], "csv", "C:\\a.csv")).toEqual({ path: "C:\\a.csv", format: "csv", layer_ids: [11], delimiter: ";", decimal: "," });
  });
});
