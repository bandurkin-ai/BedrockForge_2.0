/** Э9, подшаг 9.31 (ADR 0015 §6 «Запись операции»; ТЗ 7.23): the reports of the operations — «было → стало» of
 * «Исправить», the change of the volume and the deviation of «Упростить» and «Сгладить», cut and fill of
 * «Объём» — and the block «Операция» of «Свойства»: the operation, the sources, the parameters, the tolerances. */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { OpSpec } from "../../src/modules/model/opForms";
import {
  asBuilt,
  builtFacts,
  dtmParamFacts,
  lineNumbers,
  paramFacts,
  percent,
  reportText,
  reportView,
  roleTitle,
  small,
  sourceText,
  toleranceFacts,
  type Names,
} from "../../src/modules/model/report";
import { reportProps } from "../../src/modules/model/run";

const LIST = (JSON.parse(readFileSync(resolve(__dirname, "operations.json"), "utf-8")) as { operations: OpSpec[] }).operations;
const spec = (op: string): OpSpec => LIST.find((s) => s.op === op)!;
/** ru-RU groups thousands with a no-break space */
const plain = <T,>(v: T): T => JSON.parse(JSON.stringify(v ?? null).replace(/[\u00a0\u202f]/g, " ")) as T;
const NAMES: Names = { wireframe: (id) => ({ 41: "Куб", 42: "Тело 2" })[id] ?? null, layer: (id) => ({ 7: "Разрезы" })[id] ?? null };

describe("reports of the operations (ТЗ 7.23)", () => {
  it("«Исправить»: было → стало of the checks, what every step changed, the tolerance", () => {
    const report = {
      before: { triangles: 1200, vertices: 640, closed: false, open_edges: 14, nonmanifold_edges: 2, degenerate: 3, duplicate: 5, orientation_conflicts: 0, self_intersections: 0, components: 1, area: 600.5, volume: null },
      after: { triangles: 1194, vertices: 600, closed: true, open_edges: 0, nonmanifold_edges: 0, degenerate: 0, duplicate: 0, orientation_conflicts: 0, self_intersections: 0, components: 1, area: 600.25, volume: 1000 },
      steps: [
        { step: "weld", title: "Сшивка совпадающих вершин", changed: 40 },
        { step: "holes", title: "Закрытие отверстий", changed: 2 },
        { step: "orient", title: "Согласование нормалей", changed: 0, note: "Поверхность неориентируема: не согласовано рёбер — 0" },
      ],
      tolerance: 1e-6,
    };
    const v = reportView("repair", report, ["Самопересечения остались (3)."]);
    expect(plain(v.compare!.map((r) => [r.label, r.before, r.after, r.changed]))).toEqual([
      ["Треугольников", "1 200", "1 194", true],
      ["Вершин", "640", "600", true],
      ["Замкнутость", "не замкнут", "замкнут", true],
      ["Открытых рёбер", "14", "0", true],
      ["Рёбер с тремя и более треугольниками", "2", "0", true],
      ["Вырожденных треугольников", "3", "0", true],
      ["Повторных треугольников", "5", "0", true],
      ["Несогласованных нормалей (рёбер)", "0", "0", false],
      ["Самопересечений", "0", "0", false],
      ["Связных частей", "1", "1", false],
      ["Площадь", "600,5 м²", "600,25 м²", true],
      ["Объём", "—", "1 000 м³", true], // not known before — «—», never 0
    ]);
    expect(plain(v.steps)).toEqual(["Сшивка совпадающих вершин: изменено 40", "Закрытие отверстий: изменено 2", "Согласование нормалей: без изменений — Поверхность неориентируема: не согласовано рёбер — 0"]);
    expect(plain(v.facts)).toEqual([{ label: "Допуск сшивки", value: "1·10⁻⁶ м" }]);
    expect(plain(v.warnings)).toEqual(["Самопересечения остались (3)."]);
    expect(plain(reportView("repair", { before: { triangles: 4 }, after: {} }).compare)).toEqual([{ label: "Треугольников", before: "4", after: "—", changed: true }]);
  });

  it("«Упростить» and «Сгладить»: the change of the volume and the greatest deviation", () => {
    const s = reportView("simplify", { triangles_before: 20000, triangles_after: 5000, volume_before: 1000, volume_after: 998.8, volume_change_percent: -0.12, max_deviation: 0.034, closed_before: true, closed_after: true, method: "quadric" });
    expect(plain(s.compare!.map((r) => [r.label, r.before, r.after]))).toEqual([
      ["Треугольников", "20 000", "5 000"],
      ["Замкнутость", "замкнут", "замкнут"],
      ["Объём", "1 000 м³", "998,8 м³"],
    ]);
    expect(plain(s.facts)).toEqual([
      { label: "Изменение объёма", value: "−0,12 %" },
      { label: "Наибольшее отклонение", value: "0,034 м" },
      { label: "Метод", value: "квадратичные метрики ошибки (pyfqmr)" },
    ]);
    const m = reportView("smooth", { volume_before: null, volume_after: null, volume_change_percent: null, max_deviation: 0.5, iterations: 10, scale: 1.0000012 });
    expect(plain(m.compare)).toEqual([{ label: "Объём", before: "нет: не замкнут", after: "нет: не замкнут", changed: false }]);
    expect(plain(m.facts)).toEqual([
      { label: "Изменение объёма", value: "нет: каркас не замкнут" },
      { label: "Наибольшее отклонение", value: "0,5 м" },
      { label: "Проходов", value: "10" },
      { label: "Поправка объёма (масштаб)", value: "1,000001" },
    ]);
    expect(plain(percent(0.5))).toBe("+0,5 %");
  });

  it("«Объём»: выемка, насыпь, разность, площадь", () => {
    const v = reportView("volume_between", { cut: 12500.456, fill: 13000, net: -499.544, area: 40000, tolerance: 2e-9, method: "призмы: каждый треугольник рассечён плоскостью уровня аналитически (точно для TIN)" });
    expect(plain(v.compare)).toBeNull();
    expect(plain(v.facts)).toEqual([
      { label: "Выемка", value: "12 500,46 м³" },
      { label: "Насыпь", value: "13 000 м³" },
      { label: "Разность (выемка − насыпь)", value: "−499,54 м³" },
      { label: "Площадь в плане", value: "40 000 м²" },
      { label: "Допуск", value: "2·10⁻⁹ м" },
      { label: "Метод", value: "призмы: каждый треугольник рассечён плоскостью уровня аналитически (точно для TIN)" },
    ]);
  });

  it("the other operations: their numbers in Russian, the parts of a clip, nothing unnamed", () => {
    expect(plain(reportView("boolean", { op: "difference", volumes: [1000, 125], volume: 875, area: 650, triangles: 36, closed: true, parts: 1, origin: [0, 0, 0], flipped: [] }).facts)).toEqual([
      { label: "Треугольников", value: "36" },
      { label: "Объём", value: "875 м³" },
      { label: "Площадь", value: "650 м²" },
      { label: "Частей", value: "1" },
      { label: "Замкнутость", value: "замкнут" },
      { label: "Объёмы исходных тел", value: "1 000 м³; 125 м³" },
    ]);
    expect(plain(reportView("intersection_lines", { lines: 2, closed: 1, length: 81.25 }).facts)).toEqual([
      { label: "Линий", value: "2" },
      { label: "Длина", value: "81,25 м" },
      { label: "Замкнутых линий", value: "1" },
    ]);
    expect(plain(reportView("clip_plane", { triangles_positive: 10, triangles_negative: 12, volume_positive: 400 }).facts)).toEqual([
      { label: "Треугольников по нормали", value: "10" },
      { label: "Объём по нормали", value: "400 м³" },
      { label: "Треугольников против нормали", value: "12" },
    ]);
    expect(plain(reportView("stitch", { closed: true, volume: 1000, warnings: ["Разрез 3: ветвь срезана хордой."] }, []).warnings)).toEqual(["Разрез 3: ветвь срезана хордой."]);
  });

  it("the report as text and the props of its window from the job", () => {
    const v = reportView("smooth", { volume_before: 10, volume_after: 10, volume_change_percent: 0, max_deviation: 0.01 });
    expect(plain(reportText("Сгладить: Куб (сглажен)", v, ["«Куб (сглажен)» (объём 10 м³)"]))).toBe(
      ["Сгладить: Куб (сглажен)", "Создано: «Куб (сглажен)» (объём 10 м³)", "Было → стало:", "  Объём: 10 м³ → 10 м³", "Изменение объёма: 0 %", "Наибольшее отклонение: 0,01 м"].join("\n"),
    );
    const job = { title: "Операция", result: { op: "smooth", title: "Сгладить", name: "Куб (сглажен)", wireframe_ids: [51], volumes: [10], report: { max_deviation: 0.01 }, warnings: [], rebuild_of: 41 } };
    expect(plain(reportProps(job))).toEqual({ op: "smooth", title: "Сгладить", name: "Куб (сглажен)", report: { max_deviation: 0.01 }, warnings: [], wireframeIds: [51], layerIds: [], volumes: [10], rebuildOf: 41 });
  });

  it("small numbers and line numbers", () => {
    expect(plain(small(0.25))).toBe("0,25");
    expect(plain(small(1.5e-9))).toBe("1,5·10⁻⁹");
    expect(plain(small(1234.567))).toBe("1 234,57");
    expect(plain(lineNumbers([0, 2, 3, 4, 6]))).toBe("1, 3–5, 7");
  });
});

describe("the block «Операция» of «Свойства» (ТЗ 7.23: операция, исходные, параметры, допуски)", () => {
  const built = asBuilt({
    kind: "operation", op: "boolean", title: "Булева операция", algo: 1,
    params: { op: "difference", a: 41, b: [42] },
    sources: [{ role: "a", kind: "wireframe", id: 41, name: "Куб", version: 2 }, { role: "b", kind: "wireframe", id: null, name: "Тело 2", version: 1 }],
    tolerances: { manifold: 1.2e-7 }, report: { volume: 875 }, at: "2026-11-19T10:05:00", rebuild_of: 40,
  })!;

  it("the operation, when, rebuilt from what", () => {
    expect(plain(asBuilt({ kind: "other" }))).toBeNull();
    expect(plain(builtFacts(built, "Куб − Тело 2"))).toEqual([
      { label: "Операция", value: "Булева операция" },
      { label: "Построено", value: "19.11.2026 10:05" },
      { label: "Перестроено из", value: "«Куб − Тело 2» (№40)" },
    ]);
  });

  it("the sources by the fields of the window; a deleted one keeps its name; lines from 1", () => {
    expect(plain(built.sources!.map((s) => `${roleTitle(s.role, spec("boolean"))}: ${sourceText(s)}`))).toEqual(["Тело A: «Куб» (№41, версия 2)", "Тело B (одно или несколько): «Тело 2» — удалён из проекта"]);
    expect(plain(roleTitle("wireframe", spec("smooth")))).toBe("Каркас");
    expect(plain(roleTitle("contours[1]", spec("stitch")))).toBe("Контуры и тай-линии");
    expect(plain(roleTitle("surface", spec("clip_surface")))).toBe("Поверхность (ЦТМ, разлом)");
    expect(plain(roleTitle("wireframes[0]", spec("merge")))).toBe("Каркасы");
    expect(plain(sourceText({ role: "contours[0]", kind: "line_layer", id: 7, name: "Разрезы", version: 3, lines: [0, 1, 2, 5] }))).toBe("«Разрезы» (№7, версия 3) — линии 1–3, 6");
    expect(plain(sourceText({ kind: "line_layer", id: 7, name: "Разрезы", points: 1200, segments: 1100 }))).toBe("«Разрезы» (№7) — точек: 1 200, отрезков: 1 100");
  });

  it("the parameters as the window names them; a field hidden by `when` is left out", () => {
    expect(plain(paramFacts(spec("boolean"), built.params, NAMES))).toEqual([
      { label: "Действие", value: "Вычитание (A − B)" },
      { label: "Тело A", value: "«Куб»" },
      { label: "Тело B (одно или несколько)", value: "«Тело 2»" },
    ]);
    expect(plain(paramFacts(spec("repair"), { wireframe_id: 41, weld_on: false, weld: 1e-6, drop_duplicate: true, drop_degenerate: true, fix_nonmanifold: false, fill_holes: true, orient: true }, NAMES).map((f) => `${f.label}: ${f.value}`))).toEqual([
      "Каркас: «Куб»",
      "Сшить совпадающие вершины: нет",
      "Удалить повторные треугольники: да",
      "Удалить вырожденные треугольники: да",
      "Исправить рёбра с тремя и более треугольниками: нет",
      "Закрыть отверстия: да",
      "Согласовать нормали: да",
    ]);
    expect(plain(paramFacts(spec("stitch"), { contours: [{ layer_id: 7, lines: [0, 1] }, { layer_id: 9, lines: null }], start_cap: "point", end_cap: "flat", cap_distance: 0.5 }, NAMES))).toEqual([
      { label: "Контуры и тай-линии", value: "«Разрезы»: линии 1, 2; «№9»: все линии" },
      { label: "Начальный торец", value: "В точку" },
      { label: "Конечный торец", value: "Плоская крышка" },
      { label: "Длина торца, доля шага разрезов", value: "0,5" },
    ]);
    expect(plain(paramFacts(spec("clip_polygon"), { wireframe_id: 42, polygon: { layer_id: 7, line: null }, keep: "inside" }, NAMES)[1])).toEqual({ label: "Полигон в плане", value: "«Разрезы», единственная замкнутая линия" });
    expect(plain(paramFacts(spec("contours"), { wireframe_id: 43, interval: 5, base: 0, major_every: 5 }, NAMES)[1])).toEqual({ label: "Шаг", value: "5 м" });
    expect(plain(paramFacts(undefined, built.params, NAMES))).toEqual([]);
  });

  it("the tolerances; the record of «Построить ЦТМ»", () => {
    expect(plain(toleranceFacts(built.tolerances))).toEqual([{ label: "Допуск булевых операций (manifold3d)", value: "1,2·10⁻⁷ м" }]);
    expect(plain(toleranceFacts({ tolerance: 0.05, max_deviation: 0.031, unknown: 1, weld: null }))).toEqual([
      { label: "Допуск отклонения", value: "0,05 м" },
      { label: "Наибольшее отклонение", value: "0,031 м" },
    ]);
    const dtm = asBuilt({ kind: "dtm", method: "PythonCDT 2.0.2: триангуляция Делоне с ограничениями в плане", params: { breaklines: true, boundary: { layer_id: 7, line: 0 }, max_edge: null }, sources: [] })!;
    expect(plain(builtFacts(dtm, null))).toEqual([
      { label: "Операция", value: "Построить ЦТМ" },
      { label: "Метод", value: "PythonCDT 2.0.2: триангуляция Делоне с ограничениями в плане" },
    ]);
    expect(plain(dtmParamFacts(dtm.params))).toEqual([
      { label: "Линии — структурные", value: "да" },
      { label: "Граница", value: "замкнутая линия слоя" },
      { label: "Наибольшая длина ребра", value: "без ограничения" },
    ]);
  });
});
