// The forms and the words of the results of the pit optimisation (Э21, 21.19; ТЗ 8.11.4–8.11.5; ADR 0028 решения 25–27; docs/progress/e21-api.md §9–§11) on the answers of
// `core/tests/data/pit/contract_answers.json` (`surfaces_result`, `solid_result`, `compare_result`): the readers, the numbers of the command, the rows and the curves of the comparison.
// A shell that only one scenario has is a gap («—»), never 0; a value that is not known is null.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { readScenario } from "../../src/modules/pit/answers";
import { CompareChart } from "../../src/modules/pit/CompareChart";
import { ParamsDiff, ShellsTable } from "../../src/modules/pit/CompareParts";
import { readCompare, readSolid, readSurfaces } from "../../src/modules/pit/resultsAnswers";
import { compareRows, compareText, curveOf, DASH, defaultShell, parseShells, paramLabel, paramValue, pickedOf, shellsOf, solidText, surfacesText, val } from "../../src/modules/pit/resultsModel";
import { answer } from "./harness";

const compare = () => {
  const r = readCompare(answer("compare_result").body);
  if (!r) throw new Error("the sample of the comparison is not read");
  return r;
};
const raw = () => structuredClone(answer("compare_result").body) as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

describe("the surfaces (§9): the answer of the job", () => {
  const sample = answer("surfaces_result").body;
  it("the sample: the DTMs of three shells with their RF, the nodes and the triangles", () => {
    const r = readSurfaces(sample);
    expect(r?.scenario_id).toBe(17);
    expect(r?.wireframes.map((w) => [w.shell, w.rf, w.nodes, w.triangles])).toEqual([
      [1, 0.8, 168, 286],
      [2, 1, 168, 286],
      [3, 1.2, 168, 286],
    ]);
    expect(r?.wireframes[0].name).toBe("Сценарий карьера — RF 0,8");
    expect(r?.folder).toBe("Карьер/Сценарий карьера"); // the real answer: the folder is a path of the project
  });
  it("the form the job writes (`dtm_job.py`): the folder is a path, `skipped`, `warnings` of the core, `replaced` of a DTM", () => {
    const r = readSurfaces({ ...sample, folder: "Карьер/Сценарий карьера", folder_id: undefined, skipped: [3], warnings: ["Оболочка 3: рельеф не покрывает план модели, ЦТМ не построена."], wireframes: (sample.wireframes as object[]).map((w) => ({ ...w, replaced: true })) });
    expect(r?.folder).toBe("Карьер/Сценарий карьера");
    expect(r?.skipped).toEqual([3]);
    expect(r?.warnings[0].message).toMatch(/рельеф не покрывает/);
    expect(r?.wireframes.every((w) => w.replaced)).toBe(true);
    expect(surfacesText(r!, "Сценарий карьера")).toContain("папка «Карьер/Сценарий карьера»");
  });
  it("an answer with no scenario is not read", () => expect(readSurfaces({ wireframes: [] })).toBeNull());
  it("the text for the log: the count, the folder, the shells; a gap in the nodes is «—»", () => {
    const r = readSurfaces({ ...sample, wireframes: [{ shell: 1, rf: null, wireframe_id: 1, name: "Х", nodes: null, triangles: null }] });
    const t = surfacesText(r!, "С");
    expect(t).toContain("ЦТМ 1");
    expect(t).toContain(`узлов ${DASH}`);
    expect(t).not.toMatch(/узлов 0\b/);
  });
});

describe("the solid (§10): the answer of the job", () => {
  const sample = answer("solid_result").body;
  it("the volume, the volume of the blocks, the difference in per cent, the parts, the method", () => {
    const r = readSolid(sample);
    expect(r).toMatchObject({ scenario_id: 17, shell: 2, rf: 1, wireframe_id: 22, volume: 830500, volume_blocks: 918000, volume_diff_pct: 9.53, parts: 1, closed: true, method: "between" });
    expect(r?.name).toBe("Сценарий карьера — карьер RF 1,00");
  });
  it("the form the job writes: `folder`, `replaced`, `warnings`", () => {
    const r = readSolid({ ...sample, folder: "Карьер/С", replaced: true, warnings: ["Часть плана модели вне рельефа: там карьер не построен."] });
    expect(r).toMatchObject({ folder: "Карьер/С", replaced: true });
    expect(solidText(r!)).toContain("Часть плана модели вне рельефа");
  });
  it("a number that is not known is «—», not 0; the text names the method in words", () => {
    const r = readSolid({ ...sample, volume_blocks: null, volume_diff_pct: null, parts: 3, method: "difference" });
    expect(r?.volume_blocks).toBeNull();
    const t = solidText(r!);
    expect(t).toContain(`объём блоков оболочки ${DASH} м³, расхождение ${DASH} %`);
    expect(t).toContain("частей 3");
    expect(t).toContain("запасной путь");
  });
});

describe("the comparison (§11)", () => {
  it("the heads, the shells by RF, the params that differ, the totals, the blocks", () => {
    const c = compare();
    expect([c.a.id, c.b.id, c.a.name, c.b.name]).toEqual([17, 18, "Сценарий карьера", "Вариант 2"]);
    expect(c.a.model.cells_sha).toBe(c.b.model.cells_sha);
    expect(c.same_grid).toBe(true);
    expect(c.shells.map((s) => [s.rf, s.status])).toEqual([
      [0.8, "both"],
      [1, "both"],
      [1.2, "both"],
      [1.3, "only_b"],
    ]);
    expect(c.params_diff.map((d) => d.param)).toEqual(["elements.0.price", "prefix", "rf.3"]);
    expect(c.totals?.diff?.npv_best).toBeCloseTo(c.totals!.a!.npv_best! - c.totals!.b!.npv_best!, 3);
    expect(c.blocks).toEqual({ only_a: { blocks: 0, tonnes: 0, no_tonnes: 0 }, only_b: { blocks: 0, tonnes: 0, no_tonnes: 0 } });
    expect(c.warnings).toEqual([]);
  });
  it("a shell only B has: A and the difference are null (a gap, never 0)", () => {
    const only = compare().shells[3];
    expect(only.a).toBeNull();
    expect(only.diff).toBeNull();
    expect(only.k_a).toBeNull();
    expect(only.b?.npv_best).toBeGreaterThan(0);
  });
  it("a side the answer fills in against its status is not believed: only_a has no B, only_b no A", () => {
    const b = raw();
    b.shells[3] = { ...b.shells[3], a: b.shells[2].a, diff: b.shells[2].diff };
    const s = readCompare(b)!.shells[3];
    expect(s.a).toBeNull();
    expect(s.diff).toBeNull();
  });
  it("A − B of the core is read as it is: «both» has a difference that is the A minus B of the numbers", () => {
    for (const s of compare().shells.filter((x) => x.status === "both")) expect(s.diff?.cf).toBeCloseTo(s.a!.cf! - s.b!.cf!, 3);
  });
  it("the form of the answer against the contract §11: the keys of the contract are all there; `algo`, `totals`, `blocks.*.no_tonnes` are the core's own", () => {
    const b = raw();
    expect(Object.keys(b)).toEqual(expect.arrayContaining(["a", "b", "same_grid", "params_diff", "shells", "blocks", "warnings"]));
    expect(Object.keys(b).filter((k) => !["a", "b", "same_grid", "params_diff", "shells", "blocks", "warnings"].includes(k)).sort()).toEqual(["algo", "totals"]);
    expect(Object.keys(b.blocks.only_a).sort()).toEqual(["blocks", "no_tonnes", "tonnes"]);
  });
  it("blocks is null when the grids differ; the warnings carry the side", () => {
    const b = raw();
    b.same_grid = false;
    b.blocks = null;
    b.warnings = [{ code: "grids_differ", message: "Сетки различаются.", hint: "Блоки не сравниваются.", side: "b" }];
    const c = readCompare(b)!;
    expect(c.blocks).toBeNull();
    expect(c.warnings[0]).toMatchObject({ code: "grids_differ", side: "b" });
    expect(compareText(c)).toContain("Сетки различаются: блоки по сценариям не сравниваются");
  });
  it("an answer with no heads is not read", () => expect(readCompare({ shells: [] })).toBeNull());
  it("the text for the assistant: the counts, the NPV of the best and the worst case, the blocks", () => {
    const t = compareText(compare());
    expect(t).toContain("различий параметров 3");
    expect(t).toContain("только в B 1");
    expect(t).toContain("у них нет разности");
    expect(t).toContain("NPV лучшего случая A");
    expect(t).toContain("худшего");
    expect(t).toContain("Блоки конечного карьера: только в A 0");
  });
});

describe("the rows and the curves of the comparison", () => {
  it("the rows by RF for a quantity: a shell only B has — «—» for A and for the difference, not 0", () => {
    const rows = compareRows(compare(), "npv_best");
    expect(rows).toHaveLength(4);
    expect(rows[3]).toMatchObject({ rf: 1.3, status: "only_b", a: null, diff: null });
    expect(rows[3].b).toBeGreaterThan(0);
    expect(val(rows[3].a)).toBe(DASH);
    expect(val(rows[3].diff)).toBe(DASH);
    expect(rows[1].diff).toBeCloseTo(rows[1].a! - rows[1].b!, 3);
  });
  it("A = A: every difference is 0, and it is shown as 0 (a real 0 is not a gap)", () => {
    const b = raw();
    b.shells = b.shells.slice(0, 3);
    for (const s of b.shells) {
      s.b = s.a;
      s.k_b = s.k_a;
      s.diff = Object.fromEntries(Object.keys(s.a).map((k) => [k, 0]));
    }
    const rows = compareRows(readCompare(b)!, "cf");
    expect(rows).toHaveLength(3);
    for (const r of rows) expect([r.diff, val(r.diff)]).toEqual([0, "0"]);
  });
  it("the curves: B has a point at RF 1,3, A has none there; the order grows with RF", () => {
    const c = compare();
    const a = curveOf(c, "a", "npv_best");
    const b = curveOf(c, "b", "npv_best");
    expect(a.xs).toEqual([0.8, 1, 1.2]);
    expect(b.xs).toEqual([0.8, 1, 1.2, 1.3]);
    expect(a.label).toBe("A — Сценарий карьера");
    expect(b.ys.every((y) => y !== null && y > 0)).toBe(true);
  });
  it("a value that is not known stays null in the rows and in the curve (it breaks the line, it is not 0)", () => {
    const b = raw();
    b.shells[1].a.npv_best = null;
    b.shells[1].diff.npv_best = null;
    const c = readCompare(b)!;
    expect(compareRows(c, "npv_best")[1]).toMatchObject({ a: null, diff: null });
    expect(curveOf(c, "a", "npv_best").ys).toEqual([expect.any(Number), null, expect.any(Number)]);
    const html = renderToStaticMarkup(createElement(ShellsTable, { answer: c, metric: "npv_best" }));
    expect(html).toMatch(/data-testid="pit-cmp-row-1"[^>]*>.*?<td class="[^"]*num[^"]*">—<\/td>/);
  });
  it("the table marks the shell only B has («только в B») and shows «—»; the chart draws two curves, the dashed one is B", () => {
    const c = compare();
    const t = renderToStaticMarkup(createElement(ShellsTable, { answer: c, metric: "npv_best" }));
    expect(t).toContain("только в B");
    expect(t).toMatch(/data-status="only_b"/);
    const g = renderToStaticMarkup(createElement(CompareChart, { answer: c, metric: "npv_best" }));
    expect(g).toContain('data-series="2"');
    expect(g).toContain("stroke-dasharray");
    expect(g).toContain("A — Сценарий карьера");
  });
  it("a chart with no value anywhere says so in words (no empty axes)", () => {
    const b = raw();
    for (const s of b.shells) {
      for (const k of ["a", "b"]) if (s[k]) s[k].npv_best = null;
      s.diff = null;
    }
    const g = renderToStaticMarkup(createElement(CompareChart, { answer: readCompare(b)!, metric: "npv_best" }));
    expect(g).toContain("pit-cmp-chart-empty");
    expect(g).toContain("нет значения");
  });
  it("the params that differ are a list in words: «Элемент 1: цена», «Коэффициент цены RF №4» with «—» where a side has none", () => {
    const html = renderToStaticMarkup(createElement(ParamsDiff, { answer: compare() }));
    expect(html).toContain("Элемент 1: цена");
    expect(html).toContain("Коэффициент цены RF №4");
    expect(html).toMatch(/data-param="rf\.3"[^>]*><td[^>]*>[^<]*<\/td><td>—<\/td><td>1,3<\/td>/);
    const same = compare();
    same.params_diff = [];
    expect(renderToStaticMarkup(createElement(ParamsDiff, { answer: same }))).toContain("не различаются");
  });
  it("the words of the parameters", () => {
    expect(paramLabel("mining.c_m0")).toBe("Добыча: затраты на добычу c_m0");
    expect(paramLabel("prefix")).toBe("Префикс полей PIT и ORE");
    expect(paramLabel("something.else")).toBe("something.else");
    expect([paramValue(null), paramValue(""), paramValue(true), paramValue("B_")]).toEqual([DASH, "(пусто)", "да", "B_"]);
    expect(paramValue(2200)).toMatch(/^2\s200$/);
  });
});

describe("the shells of a scenario and the numbers of the command", () => {
  const detail = () => readScenario(answer("scenario_get").body);
  it("the shells of the stored result: k, RF, the blocks; none without a result", () => {
    const s = shellsOf(detail());
    expect(s.map((x) => [x.k, x.rf])).toEqual([
      [1, 0.8],
      [2, 1],
      [3, 1.2],
    ]);
    expect(s[0].blocks).toBe(909);
    expect(shellsOf({ ...detail()!, has_result: false })).toEqual([]);
    expect(shellsOf(null)).toEqual([]);
  });
  it("the choice of the window: none picked — all; a pick keeps the order of the shells", () => {
    const s = shellsOf(detail());
    expect(pickedOf(s, null)).toEqual([1, 2, 3]);
    expect(pickedOf(s, [3, 1])).toEqual([1, 3]);
    expect(pickedOf(s, [])).toEqual([]);
  });
  it("the shell of the solid: the one at RF = 1, else the last", () => {
    const s = shellsOf(detail());
    expect(defaultShell(s)).toBe(2);
    expect(defaultShell(s.map((x) => ({ ...x, rf: x.k * 0.3 })))).toBe(3);
    expect(defaultShell([])).toBeNull();
  });
  it("`shells`: numbers through «;», «,» or a space, ranges, each once, ascending", () => {
    expect(parseShells("1; 3; 5-7", 10)).toEqual({ ok: true, shells: [1, 3, 5, 6, 7] });
    expect(parseShells("3, 1 1", 3)).toEqual({ ok: true, shells: [1, 3] });
    expect(parseShells(2, 3)).toEqual({ ok: true, shells: [2] });
    expect(parseShells("2", null)).toEqual({ ok: true, shells: [2] });
  });
  it.each([["", "Не заданы номера оболочек."], ["0", "Оболочки «0» нет"], ["4", "Оболочки «4» нет: в результате их 3"], ["2-5", "Оболочки «2-5» нет"], ["a", "«a» — не номер оболочки."], ["1.5", "«1.5» — не номер оболочки."], ["5-2", "Оболочки «5-2» нет"]])("`shells` = «%s» is wrong: in Russian, with a hint", (text, message) => {
    const r = parseShells(text, 3);
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.message).toContain(message);
      expect(r.hint).toMatch(/Номера оболочек/);
    }
  });
});
