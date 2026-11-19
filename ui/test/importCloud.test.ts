/** «Облако точек» in «Импорт файлов» (ADR 0014 §6, подшаг 7.1.14): the card of a LAS, the columns of a table XYZ,
 * the text a `.laz` is refused with, «каждая k-я запись» as typed and the options a cloud file is sent with. */
import { describe, expect, it } from "vitest";
import {
  cloudCard, everyProposal, isLaz, lasFacts, mayBeCloud, parseEvery, pointsText, refusalText, sampleClasses, sampleNote, xyzFacts,
  type LasCard, type XyzCard,
} from "../src/modules/importing/cloud";
import { canStartObjects, everyErrors, initialObjects, startBody, takesEncoding, type Composition, type FileFailed, type FileOk, type ObjectsState } from "../src/modules/importing/objects";
import { routeImport } from "../src/modules/importing/route";


/** The numbers of the interface keep their thousands by a no-break space: compared here as plain spaces. */
const plain = <T,>(v: T): T => JSON.parse(JSON.stringify(v).replace(/[\u00a0\u202f]/g, " ")) as T;

const las = (p: Partial<LasCard> = {}): LasCard => ({
  format: "las", version: "1.2", point_format: 0, record_length: 20, point_count: 500, declared_count: 500,
  scale: [0.001, 0.001, 0.001], offset: [5000, 8000, 300], bbox: [5000.167, 8000.176, 300.032, 5099.877, 8099.602, 304.972], bbox_exact: true,
  crs: { wkt: "PROJCS[…]", epsg: null, geographic: false, name: "WGS 84 / UTM zone 37N" }, classes: { "1": 84, "2": 199, "7": 105, "5": 112 }, sampled: 500,
  not_loaded: ["интенсивность"], every: 1, software: "", system_id: "", summary: "LAS 1.2, формат точек 0: точек 500", warnings: [], points: 500, every_chosen: 1, ...p,
});

const xyz = (p: Partial<XyzCard> = {}): XyzCard => ({
  format: "xyz", encoding: "utf-8", delimiter: "whitespace", decimal: ".", header: true, columns: ["X", "Y", "Z", "CLASS", "I"],
  fields: { x: "X", y: "Y", z: "Z", class: "CLASS" }, preview: [["5000.0", "8000", "300", "1", "5"]], estimate: 100, not_loaded: ["колонка «I»"], warnings: [],
  summary: "Точки XYZ", points: 100, every_chosen: 1, ...p,
});

const file = (path: string, p: Partial<FileOk> = {}): FileOk => ({
  path, file: path.split("/").pop()!, ok: true, format: "las", label: "Облако точек LAS", kind: "cloud", encoding: null, crs: null, sidecars: [], layers: [], rasters: [], wireframes: [],
  totals: { layers: 1, lines: 0, points: 500, vertices: 0, rasters: 0, wireframes: 0, triangles: 0 }, unread: 0, preliminary: false, choices: {}, warnings: [], left_out: [], size: 10_000, cloud: las(), ...p,
});

const laz: FileFailed = {
  path: "/d/b.laz", file: "b.laz", ok: false, size: 10_000,
  error: { code: "laz_not_supported", message: "Сжатые облака LAZ не читаются.", hint: "Сохраните облако как LAS без сжатия (CloudCompare: «Сохранить как» → LAS; LAStools: laszip -i облако.laz -o облако.las) и загрузите снова." },
};

const composition = (files: Composition["files"]): Composition => ({
  files, rest: [], section: null, raster_z: { applies: false, elevation: null, shift: 0 },
  totals: { files: files.filter((f) => f.ok).length, failed: files.filter((f) => !f.ok).length, layers: 1, lines: 0, points: 500, vertices: 0, rasters: 0, wireframes: 0, triangles: 0 },
});

const state = (p: Partial<ObjectsState>): ObjectsState => ({ ...initialObjects(), ...p });

describe("the card of a LAS", () => {
  it("tells the version, the format of points, the records, scale, offset, extent and the CRS", () => {
    const facts = Object.fromEntries(plain(lasFacts(las())).map((f) => [f.label, f.value]));
    expect(facts["Версия"]).toBe("LAS 1.2, формат точек 0 (запись 20 байт)");
    expect(facts["Записей"]).toBe("500");
    expect(facts["Масштаб"]).toBe("0,001; 0,001; 0,001");
    expect(facts["Смещение"]).toBe("5000; 8000; 300");
    expect(facts["Охват"]).toBe("X 5 000,167 … 5 099,877; Y 8 000,176 … 8 099,602; Z 300,032 … 304,972");
    expect(facts["Система координат"]).toBe("WGS 84 / UTM zone 37N");
    expect(facts["Создан"]).toBeUndefined();
  });

  it("a header that lies about the records, an extent of a sample, degrees, no CRS", () => {
    const facts = Object.fromEntries(plain(lasFacts(las({ declared_count: 600, bbox_exact: false, crs: { wkt: null, epsg: 4326, geographic: true, name: "WGS 84" }, software: "LAStools" }))).map((f) => [f.label, f.value]));
    expect(facts["Записей"]).toBe("500 (в заголовке 600)");
    expect(facts["Охват"]).toMatch(/\(по выборке\)$/);
    expect(facts["Система координат"]).toBe("WGS 84 (EPSG:4326) — географическая, градусы");
    expect(facts["Создан"]).toBe("LAStools");
    expect(Object.fromEntries(plain(lasFacts(las({ crs: null }))).map((f) => [f.label, f.value]))["Система координат"]).toBe("не задана");
  });

  it("the classes of the sample in the order of the codes; the note says whether it is a sample", () => {
    expect(sampleClasses(las())).toEqual([{ code: 1, points: 84 }, { code: 2, points: 199 }, { code: 5, points: 112 }, { code: 7, points: 105 }]);
    expect(plain(sampleNote(las()))).toBe("по всем 500 записям");
    expect(plain(sampleNote(las({ point_count: 60_000_000, sampled: 1_000_000 })))).toBe("по выборке 1 000 000 записей из 60 000 000");
    expect(sampleClasses(xyz())).toEqual([]);
  });

  it("the points taken and the proposal to thin a big cloud out", () => {
    expect(plain(pointsText(las()))).toBe("точек: 500");
    const big = las({ point_count: 120_000_000, declared_count: 120_000_000, every: 3, points: 120_000_000 });
    expect(plain(everyProposal(big))).toEqual({ every: 3, text: "В файле 120 000 000 записей. Предлагается каждая 3-я запись: точек около 40 000 000." });
    expect(plain(everyProposal({ ...big, every_chosen: 3 }))).toBeNull(); // taken
    expect(plain(everyProposal(las()))).toBeNull();
    expect(plain(pointsText({ ...big, every_chosen: 3, points: 40_000_000 }))).toBe("точек: 40 000 000 из 120 000 000 записей (каждая 3-я)");
  });

  it("a LAS has no encoding; a table of points may be loaded as a cloud", () => {
    expect(takesEncoding(file("/d/a.las"))).toBe(false);
    expect(takesEncoding(file("/d/c.xyz", { format: "points_csv", cloud: xyz() }))).toBe(true);
    expect(mayBeCloud(file("/d/c.xyz", { format: "points", kind: "points", cloud: undefined }))).toBe(true);
    expect(mayBeCloud(file("/d/c.xyz", { format: "points_csv", cloud: xyz() }))).toBe(true);
    expect(mayBeCloud(file("/d/a.las"))).toBe(false);
    expect(cloudCard(file("/d/a.las"))?.format).toBe("las");
    expect(cloudCard(file("/d/r.str", { kind: "lines", format: "micromine_str", cloud: undefined }))).toBeNull();
  });
});

describe("a table XYZ", () => {
  it("its layout in Russian", () => {
    expect(plain(xyzFacts(xyz()))).toEqual([
      { label: "Кодировка", value: "utf-8" },
      { label: "Разделитель", value: "пробелы" },
      { label: "Десятичный знак", value: "точка" },
      { label: "Заголовок", value: "первая строка — названия колонок" },
      { label: "Строк", value: "около 100" },
    ]);
    expect(plain(xyzFacts(xyz({ delimiter: ";", decimal: ",", header: false })))[1].value).toBe("точка с запятой");
    expect(plain(pointsText(xyz({ estimate: 1000, every_chosen: 10, points: 100 })))).toBe("точек: около 100 из около 1 000 строк (каждая 10-я)");
  });

  it("the columns are sent as chosen; the class column «нет» goes as an empty name, untouched — not at all", () => {
    const s = state({ paths: ["/d/c.xyz"], cloud: "xyz", options: { "/d/c.xyz": { x: "E", y: "N", z: "H" } } });
    expect(startBody(s).options).toEqual({ "/d/c.xyz": { x: "E", y: "N", z: "H", cloud: true } });
    const none = state({ paths: ["/d/c.xyz"], cloud: "xyz", options: { "/d/c.xyz": { class_column: "" } } });
    expect(startBody(none).options).toEqual({ "/d/c.xyz": { class_column: "", cloud: true } });
    const chosen = state({ paths: ["/d/c.xyz"], options: { "/d/c.xyz": { cloud: true, class_column: "CLS" } } });
    expect(startBody(chosen).options).toEqual({ "/d/c.xyz": { class_column: "CLS", cloud: true } });
  });

  it("in «Импорт файлов» a table is a cloud only by its check box", () => {
    expect(startBody(state({ paths: ["/d/c.xyz"] })).options).toEqual({});
    expect(startBody(state({ paths: ["/d/c.xyz"], options: { "/d/c.xyz": { cloud: false } } })).options).toEqual({});
    expect(startBody(state({ paths: ["/d/c.xyz"], options: { "/d/c.xyz": { cloud: true } } })).options).toEqual({ "/d/c.xyz": { cloud: true } });
  });
});

describe("«каждая k-я запись»", () => {
  it("an integer from 1 to 1 000 000; empty is every record", () => {
    expect(plain(parseEvery(""))).toEqual({ every: 1, error: null });
    expect(plain(parseEvery(undefined))).toEqual({ every: 1, error: null });
    expect(plain(parseEvery(" 10 "))).toEqual({ every: 10, error: null });
    expect(plain(parseEvery("1 000"))).toEqual({ every: 1000, error: null });
    expect(plain(parseEvery("+3"))).toEqual({ every: 3, error: null });
    expect(plain(parseEvery("1000000")).error).toBeNull();
    for (const bad of ["0", "-2", "2,5", "2.5", "1e3", "abc", "1000001"]) expect(plain(parseEvery(bad)).error, bad).toMatch(/не целое число от 1 до 1 000 000/);
  });

  it("goes as a number; a wrong one is not sent and the import does not start", () => {
    const ok = file("/d/a.las");
    const s = state({ paths: ["/d/a.las"], cloud: "las", composition: composition([ok]), options: { "/d/a.las": { every: "4" } } });
    expect(startBody(s).options).toEqual({ "/d/a.las": { cloud: true, every: 4 } });
    expect(canStartObjects(s)).toBe(true);
    const one = state({ ...s, options: { "/d/a.las": { every: "1" } } });
    expect(startBody(one).options).toEqual({ "/d/a.las": { cloud: true } });
    const bad = state({ ...s, options: { "/d/a.las": { every: "0" } } });
    expect(startBody(bad).options).toEqual({ "/d/a.las": { cloud: true } });
    expect(plain(everyErrors(bad))).toEqual({ "/d/a.las": expect.stringMatching(/«0» — не целое число/) });
    expect(canStartObjects(bad)).toBe(false);
  });
});

describe("a .laz", () => {
  it("goes to the composition, where the core refuses it with the way to unpack it", () => {
    expect(routeImport(["/d/b.laz", "/d/a.las"]).objects).toEqual(["/d/b.laz", "/d/a.las"]);
    expect(isLaz(laz)).toBe(true);
    expect(isLaz(file("/d/a.las"))).toBe(false);
    expect(plain(refusalText(laz))).toBe(
      "Сжатые облака LAZ не читаются. Сохраните облако как LAS без сжатия (CloudCompare: «Сохранить как» → LAS; LAStools: laszip -i облако.laz -o облако.las) и загрузите снова.",
    );
    expect(plain(refusalText(file("/d/a.las")))).toBeNull();
  });

  it("alone it is nothing to import", () => {
    expect(canStartObjects(state({ paths: ["/d/b.laz"], cloud: "las", composition: { ...composition([laz]), totals: { ...composition([laz]).totals, files: 0, points: 0, layers: 0 } } }))).toBe(false);
  });
});
