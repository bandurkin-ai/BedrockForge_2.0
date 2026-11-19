/** «Свойства» of a layer, a raster, a wireframe and a DTM, and the windows of export and «Построение ЦТМ» as
 * data (ТЗ 7.18–7.20, 7.35 Д-07; ADR 0011 §5): facts, fields with a comma or a point, reasons, request bodies. */
import { describe, expect, it } from "vitest";
import { boundaryLayers, buildBody, EMPTY_FORM, initialForm, sourcesText } from "../src/modules/dtm/build";
import { backMessage, drapeOptions, dtmChanges, dtmOpacity, dtmSummary, hasSettings, verticalCount } from "../src/modules/dtm/props";
import { csvError, defaultLayers, exportBody, exportLayers, exportName, exportReason, exportTotals, LINES_FORMATS, MAX_EXPORT_LAYERS, skippedNote } from "../src/modules/lines/export";
import { ATTR_PAGE, attrsPath, attrText, closedText, featureFields, findLayer, layerFacts, pageStart, pager, pickedLine, pickedTitle, rowFacts } from "../src/modules/lines/props";
import { exportFilter, exportName as rasterExportName, exportNotes, exportReason as rasterExportReason } from "../src/modules/rasters/export";
import { checkLines, edgeTexts, opacityOf, parseEdges, parseField, placeMessage, rasterEdges, rasterFacts, rasterSummary, shiftReason } from "../src/modules/rasters/props";
import type { LineFileInfo, LineLayerInfo, RasterInfo } from "../src/modules/scene/objects";
import { attrNameError, attrPatch, attrResult, attrValueText, findAttr, parseAttrValue, wireframeFacts } from "../src/modules/wireframes/attrs";
import { extentFacts, fieldNumber, normalizeColor, range, toggleId } from "../src/shell/props";

const NB = "\u00a0"; // the thousands separator of ru-RU

const layer = (id: number, p: Partial<LineLayerInfo> = {}): LineLayerInfo => ({
  id, file_id: 1, name: `Слой ${id}`, version: 1, color: null, points_only: false, line_count: 12, point_count: 340, length: 1520.46,
  closed_count: 0, closed: "none", flat: false, extent: [5000, 8000, 300.5, 5300, 8300, 321], attr_schema: [], ...p,
});
const points = (id: number, p: Partial<LineLayerInfo> = {}) => layer(id, { points_only: true, line_count: 0, point_count: 21, length: 0, name: `Точки ${id}`, ...p });
const lineFile = (id: number, layers: LineLayerInfo[], p: Partial<LineFileInfo> = {}): LineFileInfo => ({
  id, name: `file${id}.str`, folder: null, version: 1, format: "micromine_str", label: null, filename: null, crs_name: null, warnings: [], layers, ...p,
});
const raster = (id: number, p: Partial<RasterInfo> = {}): RasterInfo => ({
  id, name: `Растр ${id}`, version: 1, width: 4000, height: 3000, georef: null, elevation: null, vertical: false, opacity: 1, has_elevation: false,
  displays: [], images: {}, warnings: [], ...p,
});
const facts = (list: { label: string; value: string }[]) => Object.fromEntries(list.map((f) => [f.label, f.value]));

describe("numbers of «Свойства»", () => {
  it("are written the Russian way; a missing number stays missing, never 0", () => {
    expect(fieldNumber(5000.5)).toBe("5000,5");
    expect(fieldNumber(-12.25)).toBe("−12,25");
    expect(fieldNumber(0)).toBe("0");
    expect([fieldNumber(null), fieldNumber(undefined), fieldNumber(Number.NaN)]).toEqual(["", "", ""]);
    expect(range(5000, 5300.456)).toBe(`5${NB}000 … 5${NB}300,46`);
    expect(range(null, 12)).toBe("— … 12");
    expect(extentFacts([5000, 8000, null, 5300, 8300, null])).toEqual([{ label: "X", value: `5${NB}000 … 5${NB}300` }, { label: "Y", value: `8${NB}000 … 8${NB}300` }]);
    expect(extentFacts(null)).toEqual([]);
    expect([normalizeColor("#FF8800"), normalizeColor(" #1f6bff "), normalizeColor("red"), normalizeColor("#fff"), normalizeColor(5)]).toEqual(["#ff8800", "#1f6bff", null, null, null]);
    expect([toggleId([1, 2], 3, true), toggleId([1, 2], 2, true), toggleId([1, 2], 1, false)]).toEqual([[1, 2, 3], [1, 2], [2]]);
  });
});

describe("«Свойства» of a layer of lines (ТЗ 7.18)", () => {
  const file = lineFile(1, [layer(11, { name: "Дороги", closed: "some", closed_count: 3 }), points(12)], { name: "roads.dxf", label: "AutoCAD DXF", crs_name: "МСК-23" });

  it("counts, length, closed lines, heights and the extent", () => {
    const f = facts(layerFacts(file.layers[0], file));
    expect(f).toMatchObject({ Файл: "roads.dxf (AutoCAD DXF)", Линий: "12", Вершин: "340", Длина: `1${NB}520,5 м`, Замкнутость: "замкнутых 3 из 12", Высоты: "есть", Z: "300,5 … 321", "Система координат": "МСК-23" });
    expect(closedText(layer(1, { closed: "all", closed_count: 12 }))).toBe("все линии замкнуты");
    expect(closedText(layer(1))).toBe("замкнутых нет");
    const p = facts(layerFacts(points(2, { flat: true })));
    expect(p).toMatchObject({ Точек: "21", Высоты: "нет: слой плоский" });
    expect(p.Длина).toBeUndefined(); // a layer of points has no length
    expect(findLayer([file], 12)?.file.name).toBe("roads.dxf");
    expect(findLayer([file], 99)).toBeNull();
  });

  it("the table of attributes goes by pages of 50 rows", () => {
    expect(attrsPath(7, 100)).toBe("/lines/layers/7/attrs?offset=100&limit=50");
    expect(attrsPath(7, 12, 1)).toBe("/lines/layers/7/attrs?offset=12&limit=1");
    expect(pager(1234, 0)).toEqual({ text: `Строки 1–50 из 1${NB}234`, prev: null, next: 50 });
    expect(pager(1234, 50)).toEqual({ text: `Строки 51–100 из 1${NB}234`, prev: 0, next: 100 });
    expect(pager(1234, 1200)).toEqual({ text: `Строки 1${NB}201–1${NB}234 из 1${NB}234`, prev: 1150, next: null });
    expect(pager(7, 0)).toEqual({ text: "Строки 1–7 из 7", prev: null, next: null });
    expect(pager(0, 0)).toEqual({ text: "Строк нет", prev: null, next: null });
    expect([pageStart(0), pageStart(49), pageStart(50), pageStart(1233)]).toEqual([0, 0, 50, 1200]);
    expect(ATTR_PAGE).toBe(50);
  });

  it("an empty attribute is «—», never 0; a zero is a zero", () => {
    expect([attrText(null), attrText(undefined), attrText(""), attrText(Number.NaN)]).toEqual(["—", "—", "—", "—"]);
    expect([attrText(0), attrText(12.5), attrText(2021), attrText(-0.25), attrText(true), attrText(false), attrText("TS-1")]).toEqual(["0", "12,5", "2021", "-0,25", "да", "нет", "TS-1"]);
  });

  it("the line picked in the 3D window gives the row of its attributes", () => {
    expect(pickedLine({ kind: "lines", id: 11, line: 4, on: "line", xyz: [1, 2, 3] }, 11)).toBe(4);
    expect(pickedLine({ kind: "lines", id: 12, line: 4, on: "line", xyz: [1, 2, 3] }, 11)).toBeNull(); // another layer
    expect(pickedLine({ kind: "raster", id: 11, xyz: [1, 2, 3] }, 11)).toBeNull();
    expect(pickedLine(null, 11)).toBeNull();
    expect([pickedTitle(layer(1), 4), pickedTitle(points(2), 0)]).toEqual(["Линия № 5", "Точка № 1"]);
    const schema = [{ name: "CODE", type: "text" }, { name: "AU", type: "number", unit: "г/т" }, { name: "Z", scope: "vertex" }];
    expect(featureFields(layer(1, { attr_schema: schema })).map((f) => f.name)).toEqual(["CODE", "AU"]);
    const page = { fields: schema.slice(0, 2), rows: [["TS-1", null]] };
    expect(rowFacts(page)).toEqual([{ label: "CODE", value: "TS-1" }, { label: "AU, г/т", value: "—" }]);
    expect(rowFacts(page, 3)).toEqual([]);
  });
});

describe("«Свойства» of a raster (ТЗ 7.19)", () => {
  const plan = raster(1, {
    name: "Ортофото", elevation: 250.5, has_elevation: true, opacity: 0.8,
    georef: { source: "world", pixel: 2, corners3d: [[4950, 8350, 250.5], [5462, 8350, 250.5], [5462, 7966, 250.5], [4950, 7966, 250.5]], checks: [{ source: "tab", max: 0.3512, points: [{ d: 0.1 }, { d: 0.3512 }, { d: 0.2 }, { d: 0.05 }] }] },
  });
  const scan = raster(2, { name: "Скан", vertical: true, has_elevation: true, georef: { source: "grf", vertical: true, corners3d: [[5000.5, 8000, 320], [5400, 8100.25, 320], [5400, 8100.25, 120], [5000.5, 8000, 120]] } });

  it("the georeference, the place and the deviations at the control points", () => {
    expect(facts(rasterFacts({ ...plan, filename: "ortho.jpg" }))).toMatchObject({ Размер: `4${NB}000 × 3${NB}000 px`, Привязка: "World-файл", Пиксель: "2 м", Положение: "в плане, высота 250,5 м", Файл: "ortho.jpg" });
    expect(facts(rasterFacts(raster(3)))).toMatchObject({ Привязка: "нет", Положение: "в плане, под самым низким объектом" });
    expect(facts(rasterFacts(scan))).toMatchObject({ Привязка: "Micromine GRF", Положение: "вертикальный" });
    expect(checkLines(plan)).toEqual(["MapInfo TAB: наибольшее отклонение 0,351 м (4 точки)"]);
    expect(checkLines(raster(3))).toEqual([]);
    expect(rasterSummary(plan)).toBe("Растр «Ортофото»: в плане, высота 250,5 м, непрозрачность 0,8");
  });

  it("«Сдвинуть» is not available without a height — with the reason", () => {
    expect(shiftReason(raster(3))).toBe("У растра не задана высота: сдвигать нечего. Введите «Высоту» и нажмите «Применить».");
    expect(shiftReason(plan)).toBeNull();
    expect(shiftReason(scan)).toBeNull(); // every corner of a vertical raster moves
  });

  it("fields take a comma or a point; an empty height is «not set», not 0", () => {
    expect(parseField("250,5", "Высота")).toEqual({ value: 250.5, error: null });
    expect(parseField(" −5.25 ", "Сдвиг по Z", true)).toEqual({ value: -5.25, error: null });
    expect(parseField("", "Высота")).toEqual({ value: null, error: null });
    expect(parseField("0", "Высота")).toEqual({ value: 0, error: null });
    expect(parseField("", "Сдвиг по Z", true).error).toBe("«Сдвиг по Z»: введите число.");
    expect(parseField("5 м", "Сдвиг по Z", true).error).toBe("«Сдвиг по Z»: «5 м» — не число. Введите число, например 12,5.");
    expect([opacityOf(0.05), opacityOf(1), opacityOf(0.04), opacityOf(1.01), opacityOf("0.5"), opacityOf(Number.NaN)]).toEqual([0.05, 1, null, null, null, null]);
  });

  it("the edges of a vertical raster come from its corners and go back as six numbers", () => {
    const e = rasterEdges(scan)!;
    expect(e).toEqual({ x1: 5000.5, y1: 8000, x2: 5400, y2: 8100.25, z_top: 320, z_bottom: 120 });
    expect(edgeTexts(e)).toEqual({ x1: "5000,5", y1: "8000", x2: "5400", y2: "8100,25", z_top: "320", z_bottom: "120" });
    expect(rasterEdges(raster(3))).toBeNull();
    expect(rasterEdges(raster(4, { georef: { corners3d: [[1, 2, null], [3, 4, null], [3, 0, null], [1, 0, null]] } }))).toBeNull(); // no heights
    expect(parseEdges(edgeTexts(e))).toEqual({ edges: e, error: null });
    expect(parseEdges({ ...edgeTexts(e), z_top: "315,5" }).edges?.z_top).toBe(315.5);
    expect(parseEdges({ ...edgeTexts(e), y2: "" }).error).toBe("«Правый край Y»: введите число.");
    expect(parseEdges({ ...edgeTexts(e), x2: "5000,5", y2: "8000" }).error).toContain("совпадают в плане");
    expect(parseEdges({ ...edgeTexts(e), z_bottom: "320" }).error).toContain("на одной высоте");
  });

  it("«На текущий разрез» asks in its own window what will change", () => {
    expect(placeMessage(plan, null)).toBe("Растр «Ортофото» встанет в плоскость линии разреза: края — по линии, верх и низ — по высотам данных проекта. Растр перестанет лежать в плане; прежняя привязка будет заменена.");
    expect(placeMessage(scan, 320.5)).toBe("Растр «Скан» встанет в плоскость линии разреза: края — по линии, верх — на отметке 320,5 м, низ — по пропорциям изображения. Прежнее положение растра будет заменено.");
  });

  it("export: the source file with its georeference or a GeoTIFF", () => {
    const jpg = { ...plan, format: "JPEG" };
    expect([rasterExportName(jpg, "source"), rasterExportName(jpg, "geotiff"), exportFilter(jpg, "source"), exportFilter(jpg, "geotiff")]).toEqual(["Ортофото.jpg", "Ортофото.tif", "jpg", "tif"]);
    expect([rasterExportName({ name: "a/b:c", format: null }, "source"), exportFilter({ format: null }, "source")]).toEqual(["a_b_c", "any"]);
    expect(rasterExportName({ name: "map.TIF", format: "TIFF" }, "geotiff")).toBe("map.TIF");
    const located = { ...plan, format: "JPEG", georef: { ...plan.georef, affine: [2, 0, 4950, 0, -2, 8350] } as RasterInfo["georef"] };
    expect(exportNotes(located, "source")).toEqual(["Высота растра в world-файл не записывается: чтобы сохранить её, выберите GeoTIFF."]);
    expect(exportNotes(located, "geotiff")).toEqual([]);
    expect(rasterExportReason(located, "geotiff", "C:\\out\\a.tif")).toBeNull();
    expect(rasterExportReason(located, "source", null)).toBe("Выберите файл: «Сохранить как…».");
    expect(rasterExportReason(raster(3), "geotiff", "C:\\out\\a.tif")).toContain("У растра нет привязки");
    expect(exportNotes(raster(3), "source")).toEqual(["У растра нет привязки: будет записан только исходный файл."]);
    expect(rasterExportReason(undefined, "source", "x")).toBe("Выберите растр.");
  });
});

describe("the settings of a DTM (ТЗ 7.20, Д-07)", () => {
  const rasters = [raster(1, { name: "Ортофото" }), raster(2, { name: "Скан", vertical: true }), raster(3, { name: "Карта" })];

  it("a vertical raster is never offered for a relief", () => {
    expect(drapeOptions(rasters)).toEqual([{ id: 1, name: "Ортофото" }, { id: 3, name: "Карта" }]);
    expect(verticalCount(rasters)).toBe(1);
  });

  it("the arguments of `wireframe.dtm` become the body of the request; a wrong one is told", () => {
    expect(dtmChanges({ wireframe_id: 5, raster_id: 3, clip: true, tint: "#FFEEDD", opacity: 0.8, shade: false })).toEqual({ raster_id: 3, clip: true, tint: "#ffeedd", opacity: 0.8, shade: false });
    expect(dtmChanges({ raster_id: 0 })).toEqual({ raster_id: 0 }); // 0 takes the raster off
    expect(dtmChanges({ on: false, wireframe_id: 5 })).toEqual({ on: false });
    expect(() => dtmChanges({ tint: "white" })).toThrow("Цвет растра задаётся как #rrggbb");
    expect(() => dtmChanges({ opacity: 1.5 })).toThrow("Непрозрачность растра — число от 0 до 1");
    expect([dtmOpacity(0), dtmOpacity(1), dtmOpacity(-0.01), dtmOpacity("1")]).toEqual([0, 1, null, null]);
    expect([hasSettings({ on: true }), hasSettings({}), hasSettings({ on: true, clip: false })]).toEqual([false, false, true]);
  });

  it("tells the settings in words", () => {
    expect(dtmSummary({ raster_id: 3, clip: true, tint: "#ffffff", opacity: 0.8, shade: true }, "Карта")).toBe("растр «Карта», цвет растра #ffffff, непрозрачность 0,8, тени рельефа включены, данные за ЦТМ скрыты");
    expect(dtmSummary({ raster_id: null, clip: false, tint: "#ffffff", opacity: 1, shade: false }, null)).toBe("без растра, цвет растра #ffffff, непрозрачность 1, тени рельефа выключены, данные за ЦТМ видны");
    expect(backMessage("Рельеф")).toContain("Настройки рельефа сбросятся");
  });
});

describe("attributes of the user (ТЗ 7.20)", () => {
  const attrs = [{ name: "Блок", value: 12.5 }, { name: "Проверен", value: true }, { name: "Автор", value: null }];

  it("a value is a text, a number, yes/no or empty — never 0 for empty", () => {
    expect([parseAttrValue(""), parseAttrValue("  "), parseAttrValue("12,5"), parseAttrValue("−3"), parseAttrValue("0"), parseAttrValue("Да"), parseAttrValue("нет"), parseAttrValue("Участок 2")]).toEqual([null, null, 12.5, -3, 0, true, false, "Участок 2"]);
    expect(attrs.map((a) => attrValueText(a.value))).toEqual(["12,5", "да", ""]);
    expect([attrValueText(-3), attrValueText(false), attrValueText("x"), attrValueText(undefined)]).toEqual(["−3", "нет", "x", ""]);
  });

  it("names are compared without case; add, change, rename and delete are one change each", () => {
    expect(findAttr(attrs, " блок ")?.value).toBe(12.5);
    expect(attrNameError("  ", attrs)).toBe("У атрибута должно быть название: введите его.");
    expect(attrNameError("БЛОК", attrs)).toBe("Атрибут «Блок» уже есть: введите другое название.");
    expect(attrNameError("БЛОК", attrs, "Блок")).toBeNull(); // the same attribute in another case
    expect(attrNameError("Участок", attrs)).toBeNull();
    expect(attrPatch({ name: "Блок", value: 13 })).toEqual({ set: { Блок: 13 } });
    expect(attrPatch({ name: "Автор", value: null })).toEqual({ set: { Автор: null } });
    expect(attrPatch({ name: "Блок", rename: "Участок" })).toEqual({ rename: { Блок: "Участок" } });
    expect(attrPatch({ name: "Блок", rename: "Участок", value: 2 })).toEqual({ rename: { Блок: "Участок" }, set: { Участок: 2 } });
    expect(attrPatch({ name: "Блок", remove: true, value: 5 })).toEqual({ remove: ["Блок"] });
    expect(attrResult({ name: "Блок", value: 12.5 })).toBe("Атрибут «Блок» = 12,5");
    expect(attrResult({ name: "Автор", value: null })).toBe("Атрибут «Автор» = пусто");
    expect(attrResult({ name: "Блок", rename: "Участок" })).toBe("Атрибут «Блок» переименован в «Участок»");
    expect(attrResult({ name: "Блок", remove: true })).toBe("Атрибут «Блок» удалён");
    expect(facts(wireframeFacts({ triangle_count: 1234, point_count: 640, extent: null }))).toEqual({ Треугольников: `1${NB}234`, Вершин: "640" });
  });
});

describe("the window «Экспорт линий» (ТЗ 7.18)", () => {
  const files = [lineFile(1, [layer(11, { name: "Дороги" }), points(12, { name: "Реперы" })], { name: "roads.dxf" }), lineFile(2, [layer(21, { name: "Контур" })], { name: "pit.str" })];

  it("five formats: the STR ones take lines only", () => {
    expect(LINES_FORMATS.map((f) => [f.id, f.ext, f.points])).toEqual([["str", ".str", false], ["surpac", ".str", false], ["dxf", ".dxf", true], ["csv", ".csv", true], ["dat", ".dat", true]]);
    expect(exportLayers(files, [11, 12, 21], "str")).toEqual({ take: [11, 21], skipped: ["Реперы"] });
    expect(exportLayers(files, [11, 12, 21], "dxf")).toEqual({ take: [11, 12, 21], skipped: [] });
    expect(skippedNote(["Реперы"], "surpac")).toBe("Surpac STR пишет только линии. Слои точек не войдут в файл: «Реперы». Для точек выберите DXF, CSV или Micromine DAT.");
    expect(skippedNote([], "str")).toBeNull();
  });

  it("the checked layers: the selected one, the layers of a file, or the only file", () => {
    expect(defaultLayers(files, 21)).toEqual([21]);
    expect(defaultLayers(files, null)).toEqual([]); // two files: the user chooses
    expect(defaultLayers(files, 99)).toEqual([]); // a layer that is gone
    expect(defaultLayers(files, 21, 1)).toEqual([11, 12]); // «Экспорт…» of a file
    expect(defaultLayers([files[0]], null)).toEqual([11, 12]);
  });

  it("tells why the export cannot start", () => {
    expect(exportReason([], [], "str", null)).toBe("В проекте нет слоёв линий: загрузите линии или точки.");
    expect(exportReason(files, [], "str", "C:\\a.str")).toBe("Отметьте слои, которые нужно записать.");
    expect(exportReason(files, [12], "str", "C:\\a.str")).toBe("В отмеченных слоях нет линий, а Micromine STR пишет только линии: выберите DXF, CSV или Micromine DAT.");
    expect(exportReason(files, [12], "csv", null)).toBe("Выберите файл: «Сохранить как…».");
    expect(exportReason(files, [11, 12], "str", "C:\\a.str")).toBeNull();
    const many = [lineFile(3, Array.from({ length: MAX_EXPORT_LAYERS + 1 }, (_, i) => layer(1000 + i)))];
    expect(exportReason(many, many[0].layers.map((l) => l.id), "dxf", "C:\\a.dxf")).toBe("Отмечено больше 2000 слоёв: экспортируйте их несколькими файлами.");
  });

  it("the name of the file and the body of the request", () => {
    expect([exportName(files, [21], "str"), exportName(files, [11, 12], "dxf"), exportName(files, [11, 21], "csv"), exportName(files, [], "dat")]).toEqual(["Контур.str", "roads.dxf", "Линии.csv", "Линии.dat"]);
    expect(exportName([lineFile(4, [layer(41, { name: "A/B: C?" })])], [41], "dxf")).toBe("A_B_ C_.dxf");
    expect(exportTotals(files, [11, 12])).toBe("2 слоя: 12 линий, 361 вершина или точка");
    expect(exportTotals(files, [])).toBe("Слои не отмечены");
    expect(exportBody(files, [11, 12], "str", "C:\\out\\a.str")).toEqual({ path: "C:\\out\\a.str", format: "str", layer_ids: [11] });
    expect(exportBody(files, [12, 11], "csv", "C:\\out\\a.csv", { delimiter: "\t", decimal: "." })).toEqual({ path: "C:\\out\\a.csv", format: "csv", layer_ids: [11, 12], delimiter: "\t", decimal: "." });
    expect(csvError({ delimiter: ",", decimal: "," })).toContain("оба запятая");
    expect(csvError({ delimiter: ";", decimal: "," })).toBeNull();
  });
});

describe("the window «Построение ЦТМ» (ТЗ 7.20, Д-07)", () => {
  const outline = layer(21, { name: "Контур", closed: "all", closed_count: 1, line_count: 1 });
  const pits = layer(22, { name: "Бровки", closed: "some", closed_count: 3, line_count: 8 });
  const files = [lineFile(1, [points(11, { name: "Рельеф", point_count: 1200 })]), lineFile(2, [outline, pits])];

  it("«Сразу в раздел ЦТМ» is on; the selected layer is a source already", () => {
    expect(EMPTY_FORM.toDtm).toBe(true);
    expect(initialForm(files, 11).layerIds).toEqual([11]);
    expect(initialForm(files, 99).layerIds).toEqual([]);
    expect(boundaryLayers(files).map((l) => l.id)).toEqual([21, 22]); // layers with a closed line
  });

  it("the body of the request: sources, the boundary, the longest edge with a comma", () => {
    const form = { ...EMPTY_FORM, name: "  Рельеф   2026 ", layerIds: [11], setIds: [3], boundaryLayer: 21, maxEdge: "250,5" };
    expect(buildBody(form, files)).toEqual({
      body: { name: "Рельеф 2026", layer_ids: [11], set_ids: [3], breaklines: true, boundary: { layer_id: 21, line: null, use_z: false }, max_edge: 250.5, to_dtm: true },
      error: null,
    });
    expect(buildBody({ ...EMPTY_FORM, layerIds: [11] }, files).body).toMatchObject({ name: null, boundary: null, max_edge: null }); // empty is «not set», not 0
    expect(buildBody({ ...EMPTY_FORM, layerIds: [11], boundaryLayer: 22, boundaryLine: "3", boundaryZ: true }, files).body?.boundary).toEqual({ layer_id: 22, line: 2, use_z: true });
  });

  it("tells why the build cannot start", () => {
    expect(buildBody(EMPTY_FORM, files).error).toBe("Не из чего строить поверхность: отметьте слои точек или линий, облака точек либо наборы скважин (их устья).");
    expect(buildBody({ ...EMPTY_FORM, layerIds: [11], maxEdge: "0" }, files).error).toContain("положительное число");
    expect(buildBody({ ...EMPTY_FORM, layerIds: [11], maxEdge: "сто" }, files).error).toContain("положительное число");
    expect(buildBody({ ...EMPTY_FORM, layerIds: [11], boundaryLayer: 22 }, files).error).toBe("В слое «Бровки» замкнутых линий: 3. Укажите номер линии границы.");
    expect(buildBody({ ...EMPTY_FORM, layerIds: [11], boundaryLayer: 22, boundaryLine: "9" }, files).error).toContain("целое от 1 до 8");
    expect(buildBody({ ...EMPTY_FORM, layerIds: [11], boundaryLayer: 11 }, files).error).toContain("нет замкнутой линии");
    expect(sourcesText({ layerIds: [11], setIds: [3] }, files, [{ id: 3, holes: 48 }])).toBe(`1 слой (1${NB}200 точек и вершин), устья 1 набора (48 скв.)`);
    expect(sourcesText({ layerIds: [], setIds: [] }, files, [])).toBe("Источники не отмечены");
  });
});
