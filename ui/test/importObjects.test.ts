/** «Импорт файлов» for lines, points and rasters (ТЗ 7.8, 7.18, 7.19; ADR 0011 §5): where chosen files go, the
 * numbers of «Уровень растров по Z» (a comma and a point), the level a raster gets, the body of the import. */
import { describe, expect, it } from "vitest";
import {
  canStartObjects, initialObjects, parseLevel, parseNumber, rasterLevel, startBody, totalsText, withoutFile,
  type Composition, type FileOk, type ObjectsState, type RasterEntry,
} from "../src/modules/importing/objects";
import { routeImport } from "../src/modules/importing/route";

const raster = (p: Partial<RasterEntry> = {}): RasterEntry => ({
  name: "ortho", width: 4000, height: 3000, image: "JPEG", bits: 8, georef: { source: "world", source_label: "World-файл" },
  elevation: null, vertical: false, has_elevation: false, sidecars: ["ortho.jgw"], ...p,
});

const file = (path: string, p: Partial<FileOk> = {}): FileOk => ({
  path, file: path.split("/").pop()!, ok: true, format: "micromine_str", label: "Стринги Micromine (.STR, .PIT)", kind: "lines", encoding: "cp1251",
  crs: null, sidecars: [], layers: [], rasters: [], totals: { layers: 2, lines: 10, points: 0, vertices: 340, rasters: 0 }, warnings: [], left_out: [], size: 1000, ...p,
});

const composition = (files: Composition["files"], rest: Composition["rest"] = []): Composition => {
  const ok = files.filter((f): f is FileOk => f.ok);
  const sum = (k: keyof FileOk["totals"]) => ok.reduce((n, f) => n + f.totals[k], 0);
  return {
    files, rest,
    totals: { files: ok.length, failed: files.length - ok.length, layers: sum("layers"), lines: sum("lines"), points: sum("points"), vertices: sum("vertices"), rasters: sum("rasters") },
    raster_z: { applies: sum("rasters") > 0, elevation: null, shift: 0 },
    section: null,
  };
};

const state = (p: Partial<ObjectsState>): ObjectsState => ({ ...initialObjects(), ...p });

describe("numbers of «Уровень растров по Z» (ТЗ 7.19: a comma and a point)", () => {
  it("reads a comma, a point, both minus signs and spaces between thousands; empty is not 0", () => {
    expect(parseNumber("12,5")).toBe(12.5);
    expect(parseNumber("12.5")).toBe(12.5);
    expect(parseNumber(" -5 ")).toBe(-5);
    expect(parseNumber("−5,25")).toBe(-5.25); // U+2212, as the core prints it
    expect(parseNumber("+3")).toBe(3);
    expect(parseNumber("1 250,5")).toBe(1250.5);
    expect(parseNumber("1\u00a0250,5")).toBe(1250.5);
    expect(parseNumber("0")).toBe(0);
    expect(parseNumber("")).toBeNull();
    expect(parseNumber("   ")).toBeNull();
  });

  it("the value is exact: what is typed is the double that is sent", () => {
    expect(parseNumber("512345,678901234")).toBe(512345.678901234);
    expect(parseNumber("1e3")).toBe(1000);
    expect(Object.is(parseNumber("-0"), -0)).toBe(true);
  });

  it("anything else is not a number (never NaN, never a part of the text)", () => {
    for (const bad of ["abc", "12,5,3", "12.5.3", "1,5м", "--5", "5-", "Infinity", "NaN", "1e999", "0x10", ","]) expect(parseNumber(bad), bad).toBeUndefined();
  });

  it("the level: empty fields are «not set», a wrong one is told by its field", () => {
    expect(parseLevel({ elevation: "", shift: "" })).toEqual({ elevation: null, shift: 0, errors: {} });
    expect(parseLevel({ elevation: "250,5", shift: "-5" })).toEqual({ elevation: 250.5, shift: -5, errors: {} });
    const bad = parseLevel({ elevation: "25o", shift: "1,2,3" });
    expect(bad.errors.elevation).toContain("«25o»");
    expect(bad.errors.shift).toContain("не число");
    expect([bad.elevation, bad.shift]).toEqual([null, 0]);
  });
});

describe("the level a raster gets (ТЗ 7.19; the core applies the same rule on import)", () => {
  const level = (elevation: string, shift: string) => parseLevel({ elevation, shift });

  it("a plan raster without a height lies under the lowest object; a shift has nothing to move", () => {
    expect(rasterLevel(raster(), level("", ""))).toEqual({ text: "в плане, под самым низким объектом", warn: false });
    const shifted = rasterLevel(raster(), level("", "-5"));
    expect(shifted.warn).toBe(true);
    expect(shifted.text).toContain("сдвиг не применён");
  });

  it("«высота» replaces the height of the georeference, «сдвиг» is added to it", () => {
    expect(rasterLevel(raster(), level("250,5", "")).text).toBe("в плане, высота 250,5 м");
    expect(rasterLevel(raster(), level("250,5", "-5")).text).toBe("в плане, высота 245,5 м");
    const tab = raster({ elevation: 100, has_elevation: true });
    expect(rasterLevel(tab, level("", "")).text).toBe("в плане, высота 100 м");
    expect(rasterLevel(tab, level("", "2,5")).text).toBe("в плане, высота 102,5 м");
    expect(rasterLevel(tab, level("250", "")).text).toBe("в плане, высота 250 м (вместо 100 м из привязки)");
    expect(rasterLevel(tab, level("100", "")).text).toBe("в плане, высота 100 м");
  });

  it("a vertical raster ignores «высота»; the shift moves all its corners", () => {
    const scan = raster({ vertical: true, has_elevation: true });
    expect(rasterLevel(scan, level("250", "")).text).toBe("вертикальный");
    expect(rasterLevel(scan, level("250", "-5")).text).toBe("вертикальный, все углы сдвигаются на −5 м");
    expect(rasterLevel(scan, level("", "5")).text).toBe("вертикальный, все углы сдвигаются на +5 м");
  });
});

describe("the composition and the start (ТЗ 7.8)", () => {
  const lines = file("/d/roads.str");
  const image = file("/d/ortho.jpg", { format: "raster", kind: "raster", rasters: [raster()], totals: { layers: 0, lines: 0, points: 0, vertices: 0, rasters: 1 } });
  const broken = { path: "/d/bad.dxf", file: "bad.dxf", ok: false as const, error: { code: "empty_file", message: "В файле нет ни линий, ни точек.", hint: "Проверьте файл." }, size: 10 };

  it("starts when something loads, nothing is being read and the level is a number", () => {
    const paths = ["/d/roads.str", "/d/ortho.jpg", "/d/ortho.jgw", "/d/bad.dxf"];
    const ready = state({ paths, composition: composition([lines, image, broken]) });
    expect(canStartObjects(ready)).toBe(true);
    expect(canStartObjects({ ...ready, busy: true })).toBe(false);
    expect(canStartObjects({ ...ready, starting: true })).toBe(false);
    expect(canStartObjects({ ...ready, level: { elevation: "12,5,", shift: "" } })).toBe(false);
    expect(canStartObjects(state({ paths: ["/d/bad.dxf"], composition: composition([broken]) }))).toBe(false);
    expect(canStartObjects(state({ paths }))).toBe(false); // not read yet
    // without rasters the level is not used, so a wrong text in it does not block
    expect(canStartObjects(state({ paths: ["/d/roads.str"], composition: composition([lines]), level: { elevation: "x", shift: "" } }))).toBe(true);
  });

  it("the body: the chosen files, options of the files that have them, the level as numbers", () => {
    const s = state({
      paths: ["/d/roads.str", "/d/ortho.jpg", "/d/ortho.jgw"],
      options: { "/d/roads.str": { encoding: "cp866" }, "/d/gone.csv": { x: "E" }, "/d/ortho.jpg": {} },
      level: { elevation: "250,5", shift: "−5" },
      composition: composition([lines, image]),
    });
    expect(startBody(s)).toEqual({
      files: ["/d/roads.str", "/d/ortho.jpg", "/d/ortho.jgw"],
      options: { "/d/roads.str": { encoding: "cp866" } },
      raster_z: { elevation: 250.5, shift: -5 },
      section: true,
    });
    expect(startBody(state({ paths: ["/d/roads.str"], composition: composition([lines]), level: { elevation: "x", shift: "" } })).raster_z).toEqual({ elevation: null, shift: null });
  });

  it("totals in one line; zero counts are not listed", () => {
    expect(totalsText(composition([lines, image, broken]).totals)).toBe("Файлов: 2 · слоёв: 2 · линий: 10 · вершин: 340 · растров: 1 · не прочитано: 1");
    expect(totalsText(composition([]).totals)).toBe("Нет файлов для загрузки");
  });

  it("«Убрать» drops the file with its companions and keeps the rest of the composition", () => {
    const paths = ["/d/roads.str", "/d/ortho.jpg", "/d/ortho.jgw", "/e/ortho.tab", "/d/notes.md"];
    const rest = [{ path: "/d/notes.md", file: "notes.md", code: "unsupported", message: "Файл не загружается.", hint: "" }];
    const out = withoutFile(state({ paths, options: { "/d/ortho.jpg": { encoding: "utf-8" } }, composition: composition([lines, image], rest) }), "/d/ortho.jpg");
    expect(out.paths).toEqual(["/d/roads.str", "/e/ortho.tab", "/d/notes.md"]); // the .jgw next to the image goes with it
    expect(out.options).toEqual({});
    expect(out.composition!.files.map((f) => f.file)).toEqual(["roads.str"]);
    expect(out.composition!.totals).toMatchObject({ files: 1, rasters: 0, lines: 10 });
    expect(out.composition!.raster_z.applies).toBe(false);
    expect(out.composition!.rest).toEqual(rest);
    expect(withoutFile(state({ paths, composition: composition([lines], rest) }), "/d/notes.md").composition!.rest).toEqual([]);
  });
});

describe("where the chosen files go (ТЗ 7.8)", () => {
  it("«Импорт файлов»: tables to the drillhole wizard, lines, points and rasters to the objects, .bfggis to the project", () => {
    const r = routeImport(["/d/collar.csv", "/d/Восточный.STR", "/d/pit.PIT", "/d/faults.dxf", "/d/a.shp", "/d/a.dbf", "/d/a.prj", "/d/b.mif", "/d/b.mid", "/d/p.xyz", "/d/map.tif", "/d/map.tfw", "/d/x.bfggis"]);
    expect(r.holes).toEqual(["/d/collar.csv"]);
    expect(r.project).toEqual(["/d/x.bfggis"]);
    expect(r.objects).toEqual(["/d/Восточный.STR", "/d/pit.PIT", "/d/faults.dxf", "/d/a.shp", "/d/a.dbf", "/d/a.prj", "/d/b.mif", "/d/b.mid", "/d/p.xyz", "/d/map.tif", "/d/map.tfw"]);
    expect(r.skipped).toEqual([]);
  });

  it("what comes with later stages is told when nothing else of the selection is an object", () => {
    const r = routeImport(["/d/ore.tridb", "/d/model.omf", "/d/readme.md"]);
    expect(r.objects).toEqual([]);
    expect(r.skipped).toEqual(["ore.tridb — каркасы: появится на этапе Э8", "model.omf — OMF: появится на этапе Э11", "readme.md — тип файла не распознан"]);
    // next to object files they go to the composition: the core says why each is not loaded
    const mixed = routeImport(["/d/roads.str", "/d/ore.tridb", "/d/readme.md"]);
    expect(mixed.objects).toEqual(["/d/roads.str", "/d/ore.tridb", "/d/readme.md"]);
    expect(mixed.skipped).toEqual([]);
  });

  it("«Линии» and «Растры»: every chosen file is an object — a CSV is a table of points", () => {
    const r = routeImport(["/d/points.csv", "/d/relief.dat", "/d/roads.str", "/d/x.bfggis"], "objects");
    expect(r.objects).toEqual(["/d/points.csv", "/d/relief.dat", "/d/roads.str"]);
    expect(r.holes).toEqual([]);
    expect(r.project).toEqual(["/d/x.bfggis"]);
  });

  it("Windows paths", () => {
    expect(routeImport(["C:\\Данные\\Карьер 2026\\дороги.STR"]).objects).toHaveLength(1);
  });
});
