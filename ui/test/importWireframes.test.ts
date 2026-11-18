/** «Импорт файлов» for wireframes (ТЗ 7.21, 9.4, 9.5; ADR 0013 §5–§6): where the files go, what the composition
 * counts for a TRIDB and for a drawing with lines and wireframes, the choices of Datamine, a DWG without the
 * converter, and the checks started once an import has stored its wireframes. */
import { beforeEach, describe, expect, it } from "vitest";
import { jobsStore, type Job } from "../src/modules/jobs/state";
import { checkImported, forgetHandled, importedWireframes } from "../src/modules/importing/afterImport";
import { fileCounts, hasDrawing, initialObjects, needsConverter, startBody, takesEncoding, totalsText, type FileOk, type ObjectsState, type WireframeEntry } from "../src/modules/importing/objects";
import { routeImport } from "../src/modules/importing/route";
import { detectImportKind } from "../src/shell/tree";

const wf = (key: number, p: Partial<WireframeEntry> = {}): WireframeEntry => ({
  key, name: `Body_${key}`, points: 52, triangles: 100, color: "#ff7300", attrs: [], extent: null, warnings: [], error: null, ...p,
});

const file = (path: string, p: Partial<FileOk> = {}): FileOk => ({
  path, file: path.split("/").pop()!, ok: true, format: "tridb", label: "Каркасы Micromine (.tridb)", kind: "wireframes", encoding: null, crs: null, sidecars: [],
  layers: [], rasters: [], wireframes: [wf(1), wf(2)], totals: { layers: 0, lines: 0, points: 0, vertices: 0, rasters: 0, wireframes: 2, triangles: 200 },
  unread: 0, preliminary: false, choices: {}, warnings: [], left_out: [], size: 262144, ...p,
});

const oda = { path: "/d/plan.dwg", file: "plan.dwg", ok: false as const, error: { code: "oda_not_found", message: "Для чтения DWG нужна программа ODA File Converter.", hint: "Укажите её в «Настройка › Внешние программы»." }, size: 10 };

describe("where wireframes go (ТЗ 7.8: Э8)", () => {
  it("TRIDB, OBJ, STL, Surpac DTM, Datamine, DXF and DWG go to the composition — nothing is «позже»", () => {
    const paths = ["/d/ORE SEP 2025.tridb", "/d/a.obj", "/d/b.STL", "/d/pit.dtm", "/d/pit.str", "/d/oretr.dm", "/d/orept.dm", "/d/faults.dxf", "/d/plan.DWG"];
    const r = routeImport(paths);
    expect(r.objects).toEqual(paths);
    expect(r.skipped).toEqual([]);
    expect(r.holes).toEqual([]);
    expect(routeImport(["C:\\Данные\\Rock Model.tridb"]).objects).toHaveLength(1);
  });

  it("a dropped DWG or .dm is of the kind «каркасы»", () => {
    expect(detectImportKind(["plan.dwg"])).toBe("wireframes");
    expect(detectImportKind(["oretr.dm"])).toBe("wireframes");
  });
});

describe("the composition of wireframes", () => {
  it("a TRIDB: wireframes and triangles, no «слоёв: 0»; what will not load is counted", () => {
    expect(fileCounts(file("/d/ore.tridb"))).toBe("каркасов: 2 · треугольников: 200");
    const one = file("/d/ore.tridb", { wireframes: [wf(1), wf(2, { error: { code: "broken", message: "Каркас повреждён.", hint: "" }, triangles: null })], totals: { layers: 0, lines: 0, points: 0, vertices: 0, rasters: 0, wireframes: 1, triangles: 100 }, unread: 1 });
    expect(fileCounts(one)).toBe("каркасов: 1 · треугольников: 100 · не загрузятся: 1");
  });

  it("a drawing gives both: its layers and its wireframes", () => {
    const dxf = file("/d/faults.dxf", { format: "dxf", kind: "lines", totals: { layers: 3, lines: 12, points: 0, vertices: 400, rasters: 0, wireframes: 2, triangles: 200 } });
    expect(fileCounts(dxf)).toBe("слоёв: 3 · линий: 12 · вершин: 400 · каркасов: 2 · треугольников: 200");
    const lines = file("/d/roads.dxf", { format: "dxf", kind: "lines", wireframes: [], totals: { layers: 1, lines: 2, points: 0, vertices: 4, rasters: 0, wireframes: 0, triangles: 0 } });
    expect(fileCounts(lines)).toBe("слоёв: 1 · линий: 2 · вершин: 4");
    const totals = { files: 2, failed: 1, layers: 3, lines: 12, points: 0, vertices: 400, rasters: 0, wireframes: 68, triangles: 3790 };
    expect(totalsText(totals).replace(/\s/g, " ")).toBe("Файлов: 2 · слоёв: 3 · линий: 12 · вершин: 400 · каркасов: 68 · треугольников: 3 790 · не прочитано: 1");
  });

  it("the encoding is offered where it changes the text: not for TRIDB, OBJ, STL, DTM, DWG", () => {
    expect(takesEncoding(file("/d/ore.tridb"))).toBe(false);
    for (const format of ["obj", "stl", "surpac_dtm", "dwg"]) expect(takesEncoding(file("/d/x", { format })), format).toBe(false);
    for (const format of ["datamine", "datamine_str", "dxf", "micromine_str"]) expect(takesEncoding(file("/d/x", { format })), format).toBe(true);
    expect(takesEncoding(oda)).toBe(true);
  });

  it("a DWG without ODA File Converter asks for «Настройка»; another failure does not", () => {
    expect(needsConverter(oda)).toBe(true);
    expect(needsConverter({ ...oda, error: { ...oda.error, code: "oda_failed" } })).toBe(false);
    expect(needsConverter(file("/d/ore.tridb"))).toBe(false);
    expect(hasDrawing(["/d/a.tridb", "C:\\d\\План.DWG"])).toBe(true);
    expect(hasDrawing(["/d/a.dxf"])).toBe(false);
  });

  it("Datamine: the field that divides goes with the file; «не делить» (empty) is not sent", () => {
    const s: ObjectsState = { ...initialObjects(), paths: ["/d/oretr.dm", "/d/orept.dm", "/d/str.dm"], options: { "/d/oretr.dm": { split_by: "SURFACE" }, "/d/str.dm": { layer_field: "" } } };
    expect(startBody(s).options).toEqual({ "/d/oretr.dm": { split_by: "SURFACE" } });
  });
});

describe("the checks after an import (ADR 0013 §5: «затем wireframe.check»)", () => {
  const job = (p: Partial<Job> = {}): Job => ({
    id: 7, source: "project", kind: "import.objects", title: "Импорт файлов: ore.tridb", queue: "heavy", status: "done", progress: 1, message: null, error: null,
    params: null, result: { wireframe_ids: [11, 12, 13] }, attempts: 1, max_attempts: 1, cancel_requested: false, created_at: "2026-11-18T10:00:00", started_at: null,
    finished_at: "2026-11-18T10:00:05", why_waiting: null, ...p,
  });

  beforeEach(() => {
    forgetHandled();
    jobsStore.set({ jobs: [] });
  });

  it("the wireframes of a finished import; none for a failed one, another job or lines only", () => {
    expect(importedWireframes(job())).toEqual([11, 12, 13]);
    expect(importedWireframes(job({ status: "failed" }))).toEqual([]);
    expect(importedWireframes(job({ kind: "import.trenches" }))).toEqual([]);
    expect(importedWireframes(job({ result: { wireframe_ids: [] } }))).toEqual([]);
    expect(importedWireframes(job({ result: null }))).toEqual([]);
  });

  it("one job of checks per import, once per job; one being checked already is not queued again", async () => {
    const batches: number[][] = [];
    const many = async (ids: number[]) => void batches.push(ids);
    const one = async () => { throw new Error("not expected"); };
    jobsStore.set({ jobs: [job({ id: 3, kind: "wireframe.check", status: "running", params: { wireframe_ids: [12] }, result: null })] });
    expect(await checkImported(job(), many, one)).toBe(2);
    expect(batches).toEqual([[11, 13]]);
    expect(await checkImported(job(), many, one)).toBe(0); // the list of jobs and the window both saw it end
    expect(batches).toEqual([[11, 13]]);
  });

  it("a wireframe deleted meanwhile does not stop the others", async () => {
    const started: number[] = [];
    const many = async () => { throw new Error("Каркас не найден"); };
    const one = async (id: number) => {
      if (id === 11) throw new Error("Каркас не найден");
      started.push(id);
    };
    expect(await checkImported(job(), many, one)).toBe(2);
    expect(started).toEqual([12, 13]);
  });
});
