/** «Свойства» of a point cloud (ADR 0014 §2, §6, подшаг 7.1.15): the ASPRS classes in Russian, the table of
 * classes with their visibility, the colour modes the cloud has, the style kept in `cloud_styles`, the facts of
 * the cloud and of its picked point. */
import { describe, expect, it } from "vitest";
import {
  ASPRS_NAMES, classLabel, className, classRows, cloudFacts, modeOf, modeOptions, pointFacts, shownText, sourceText, withAllClasses, withClass,
  type CloudPage, type CloudSummary,
} from "../src/modules/lines/classes";
import { CLASS_COLORS, cloudStyleOf } from "../src/viz/lines/cloud";
import type { LineLayerInfo } from "../src/modules/scene/objects";
import { patchEntry } from "../src/modules/scene/viewSync";


/** The numbers of the interface keep their thousands by a no-break space: compared here as plain spaces. */
const plain = <T,>(v: T): T => JSON.parse(JSON.stringify(v).replace(/[\u00a0\u202f]/g, " ")) as T;

const summary = (p: Partial<CloudSummary> = {}): CloudSummary => ({
  parts: 3, levels: [0, 0.5, 2, 8, 32], bbox: [5000, 8000, 300, 5100, 8100, 305], classes: { "2": 900, "1": 80, "7": 20 }, modes: ["class"],
  source: { kind: "las", every: 1, filename: "survey.las", las: { version: "1.4", point_format: 6, point_count: 1000, crs: true } }, ground: null, dz: null, ...p,
});

const layer = (cloud: CloudSummary | null = summary()): LineLayerInfo => ({
  id: 7, file_id: 3, name: "survey", version: 2, color: "#f46a1a", points_only: true, line_count: 0, point_count: 1000, length: 0, closed_count: 0, closed: "none",
  flat: false, extent: [5000, 8000, 300, 5100, 8100, 305], attr_schema: [], cloud,
});

describe("the ASPRS classes in Russian (LAS 1.4 R15, table 17)", () => {
  it("every standard code 0…18 has a name and a colour of the palette", () => {
    for (let c = 0; c <= 18; c++) {
      expect(ASPRS_NAMES[c], String(c)).toMatch(/^[а-яё(]/);
      expect(CLASS_COLORS[c], String(c)).toMatch(/^#[0-9a-f]{6}$/);
    }
    expect(classLabel(2)).toBe("2 — земля");
    expect(classLabel(7)).toBe("7 — низкая точка (шум)");
    expect(classLabel(9)).toBe("9 — вода");
    expect(className(40)).toBe("резерв ASPRS");
    expect(className(64)).toBe("пользовательский");
    expect(className(255)).toBe("пользовательский");
  });
});

describe("the table of classes", () => {
  it("rows by code with the points, the colour and the visibility of the style", () => {
    const rows = classRows(summary().classes, { hidden: [7] });
    expect(rows.map((r) => [r.code, r.name, r.points, r.shown])).toEqual([
      [1, "не классифицирована", 80, true],
      [2, "земля", 900, true],
      [7, "низкая точка (шум)", 20, false],
    ]);
    expect(rows[1].color).toBe(CLASS_COLORS[2]);
    expect(plain(shownText(rows))).toBe("Показано точек: 980 из 1 000");
    expect(plain(shownText(classRows(summary().classes, null)))).toBe("Показаны все классы: 1 000 точек");
    expect(classRows({ "x": 3, "300": 1, "4": 0 }, null)).toEqual([]); // junk of an old project is dropped
  });

  it("a class hidden and shown again; all at once; the mode stays", () => {
    const hidden = withClass({ color: "h", hidden: [9] }, 2, false);
    expect(hidden).toEqual({ color: "h", hidden: [2, 9] });
    expect(withClass(hidden, 2, true)).toEqual({ color: "h", hidden: [9] });
    expect(withClass(null, 7, false)).toEqual({ hidden: [7] });
    const rows = classRows(summary().classes, null);
    expect(withAllClasses({ color: "class" }, rows, false)).toEqual({ color: "class", hidden: [1, 2, 7] });
    expect(withAllClasses({ color: "class", hidden: [1, 2] }, rows, true)).toEqual({ color: "class", hidden: [] });
  });

  it("the style is one entry of the view key `cloud_styles`: the 3D window reads it back", () => {
    const key = patchEntry("7", withClass(null, 7, false))({ "3": { color: "layer" } });
    expect(key).toEqual({ "3": { color: "layer" }, "7": { hidden: [7] } });
    expect(cloudStyleOf(key, 7)).toEqual({ hidden: [7] });
    expect(cloudStyleOf(patchEntry("7", { color: "layer", hidden: [7] })(key), 7)).toEqual({ color: "layer", hidden: [7] });
  });
});

describe("colour modes", () => {
  it("«Класс» and «Цвет слоя» always; h and dz when the cloud has them", () => {
    expect(modeOptions(summary()).map((o) => [o.mode, o.title, o.why])).toEqual([
      ["class", "Класс", null],
      ["layer", "Цвет слоя", null],
      ["h", "Высота над землёй (h)", "появится после «Фильтра земли»"],
      ["dz", "Отклонение от ЦТМ (dz)", "появится после расчёта отклонения от ЦТМ"],
    ]);
    expect(modeOptions(summary({ modes: ["class", "h", "dz"] })).every((o) => o.why === null)).toBe(true);
  });

  it("the mode in force follows the 3D window: a mode the cloud has not falls back", () => {
    expect(modeOf(summary(), null)).toBe("class");
    expect(modeOf(summary(), { color: "layer" })).toBe("layer");
    expect(modeOf(summary(), { color: "h" })).toBe("class");
    expect(modeOf(summary({ modes: ["class", "h"] }), { color: "h" })).toBe("h");
    expect(modeOf(summary({ classes: {} }), null)).toBe("layer");
  });
});

describe("facts", () => {
  it("of the cloud: points, source, thinning, extent, CRS", () => {
    const facts = Object.fromEntries(plain(cloudFacts(layer(), { name: "survey.las", crs_name: null })).map((f) => [f.label, f.value]));
    expect(facts["Точек"]).toBe("1 000");
    expect(facts["Источник"]).toBe("LAS 1.4, формат точек 6");
    expect(facts["Частей"]).toBe("3");
    expect(facts["X"]).toBe("5 000 … 5 100");
    expect(facts["Система координат"]).toBe("задана в LAS");
    expect(facts["Прореживание"]).toBeUndefined();
    const thin = Object.fromEntries(plain(cloudFacts(layer(summary({ source: { kind: "xyz", every: 5 } })), { name: "pts.xyz", crs_name: null })).map((f) => [f.label, f.value]));
    expect(thin["Источник"]).toBe("Точки XYZ");
    expect(thin["Прореживание"]).toBe("каждая 5-я запись");
    expect(thin["Система координат"]).toBe("не задана");
    expect(sourceText({ source: null })).toBe("—");
  });

  it("of the picked point: X, Y, Z as the core gives them, «Класс: 2 — земля», the return, h, the record of the source", () => {
    const page: CloudPage = {
      layer_id: 7, version: 2, total: 1000, offset: 41, limit: 1,
      fields: [{ name: "Класс" }, { name: "Возврат" }, { name: "Число возвратов" }, { name: "Высота над землёй, м" }],
      rows: [[2, 1, 2, null]], xyz: [[512345.678901234, 7012345.5, 301.25]], seq: [82],
    };
    expect(plain(pointFacts(page))).toEqual([
      { label: "X", value: "512 345,679" },
      { label: "Y", value: "7 012 345,5" },
      { label: "Z", value: "301,25" },
      { label: "Класс", value: "2 — земля" },
      { label: "Возврат", value: "1 из 2" },
      { label: "Высота над землёй", value: "—" }, // empty is not 0
      { label: "Запись источника", value: "№ 83" },
    ]);
    expect(plain(pointFacts({ ...page, rows: [] }))).toEqual([]);
  });
});
