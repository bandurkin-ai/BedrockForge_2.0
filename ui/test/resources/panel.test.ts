// The result panel «Ресурсы» (Э20, 20.13; ТЗ 8.10 «Вывод», «Интерфейс»; ADR 0027 решения 11, 13, 19): the tables, the chart and the control are drawn from the REAL answers of the core
// (`core/tests/data/resources/contract_answers.json`: `report_get`, `preview_result`, `report_rebuilt`) — the table is the answer of the core (the number of the cells, the texts of
// the form as they are), a gap is «нет значения» and not 0, the chart has the points of the curve of the core, 100 points are drawn in a frame (16 ms).
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { GradeTonnageChart } from "../../src/charts/gradetonnage/GradeTonnageChart";
import { readControl, readForm, readReportResult, type Control, type ReportResult } from "../../src/modules/resources/answers";
import { cellOfForm, cellOfRow, emitCellClick, setCellClickHandler, type CellRef } from "../../src/modules/resources/cells";
import { ControlView, allWarnings, relText, shareText } from "../../src/modules/resources/ControlView";
import { CurveChart } from "../../src/modules/resources/CurveChart";
import { curveData, curveElement, curveMarks } from "../../src/modules/resources/curveModel";
import { columnGroups } from "../../src/modules/resources/FormTable";
import { INITIAL_PANEL, type PanelState } from "../../src/modules/resources/panelState";
import { cutoffOptions, ResultView, type PanelActions } from "../../src/modules/resources/ResultPanel";
import { answer } from "./harness";

const real = (key: string): ReportResult => readReportResult(answer(key).body)!;
const SAVED = real("report_get");
const PREVIEW = real("preview_result");

const noop = () => undefined;
const ACTIONS: PanelActions = { tab: noop, template: noop, cutoff: noop, page: noop, element: noop, right: noop, rowCutoff: noop, exportFile: noop };
const view = (result: ReportResult, panel: Partial<PanelState> = {}, over: { onCell?: (c: CellRef) => void; templates?: { id: string; name: string; builtin: boolean; version: number }[] } = {}) =>
  renderToStaticMarkup(createElement(ResultView, { result, panel: { ...INITIAL_PANEL, mode: "report", ...panel }, templates: over.templates ?? [], actions: ACTIONS, onCell: over.onCell }));
const count = (html: string, re: RegExp) => (html.match(re) ?? []).length;

describe("the real answers are what the panel reads", () => {
  it("a saved report has its form (the template of the core), the rows, the curve and the control", () => {
    expect(SAVED).toMatchObject({ report_id: 2, saved: true, rows_total: 8 });
    expect(SAVED.form).toMatchObject({ template_id: "jorc", template_name: "JORC", cutoff: 1, cutoffs: [1] });
    expect(SAVED.form?.columns.map((c) => c.label)).toEqual(["Руда, тыс. т", "AU, г/т", "Металл AU, тыс. унц"]);
    expect(SAVED.form?.sections[0].rows.map((r) => r.kind)).toEqual(["row", "row", "subtotal", "row", "total"]);
    expect(SAVED.form?.sections[0].rows[2].members).toEqual(["Измеренные", "Выявленные"]);
    expect(PREVIEW).toMatchObject({ saved: false, report_id: null });
    expect(PREVIEW.form?.template_id).toBe("jorc");
    expect(PREVIEW.curve).toBeNull(); // the preview of the contract has «curve: 0»
  });
});

describe("«Форма»: the cells are the text of the core", () => {
  const html = view(SAVED);
  it("as many cells as the core made: 5 rows × 3 columns; every text is `text` of its cell, not rounded again", () => {
    const form = SAVED.form!;
    expect(count(html, /data-testid="res-form-cell-/g)).toBe(form.sections.reduce((n, s) => n + s.rows.length, 0) * form.columns.length);
    for (const [ri, row] of form.sections[0].rows.entries())
      for (const col of form.columns) {
        const cell = row.cells[col.key];
        expect(html, `${row.label} ${col.key}`).toContain(`data-testid="res-form-cell-0-${ri}-${col.key}"`);
        expect(html).toMatch(new RegExp(`data-testid="res-form-cell-0-${ri}-${col.key.replace(":", ":")}"[^>]*>${cell.text}</td>`));
      }
    // 2.5 kt are shown «3» (the template has no digits after the comma), 1.0 g/t — «1,00»: the interface does not round again
    expect(html).toMatch(/res-form-cell-0-0-tonnes"[^>]*>3<\/td>/);
    expect(html).toMatch(/res-form-cell-0-0-grade:AU"[^>]*>1,00<\/td>/);
    expect(html).toMatch(/res-form-cell-0-4-grade:AU"[^>]*>2,50<\/td>/);
  });

  it("the labels, the subtotal and the total are marked; the titles of the columns are the core's", () => {
    for (const l of ["Измеренные", "Выявленные", "Изм. + Выяв.", "Предполагаемые", "Итого"]) expect(html).toContain(`>${l}</td>`);
    expect(html).toMatch(/<tr class="[^"]*" data-kind="subtotal" data-testid="res-form-row-0-2"/);
    expect(html).toMatch(/<tr class="[^"]*" data-kind="total" data-testid="res-form-row-0-4"/);
    expect(html).toContain("Руда, тыс. т");
    expect(html).toContain("Металл AU, тыс. унц");
    expect(html).toContain('data-template="jorc"');
  });

  it("a gap of the form is «—» and marked, never 0", () => {
    const form = readForm({ ...(SAVED.form as object), template: { id: "jorc", name: "JORC" }, columns: [{ key: "tonnes", title: "Руда, тыс. т" }, { key: "grade:AU", title: "AU, г/т" }], sections: [{ title: null, rows: [{ label: "Измеренные", type: "group", cells: { tonnes: { value: 0, text: "0" }, "grade:AU": { value: null, text: "—" } } }] }] })!;
    const out = view({ ...SAVED, form });
    expect(out).toMatch(/res-form-cell-0-0-tonnes"[^>]*>0<\/td>/);
    expect(out).toMatch(/res-form-cell-0-0-grade:AU"[^>]*data-missing="true"[^>]*>—<\/td>/);
  });

  it("the form with balance blocks has the two groups of columns over the titles; the form with sections has a line for each", () => {
    const cols = [
      { key: "balance:tonnes", title: "Руда, тыс. т", group: "Балансовые", part: "balance" },
      { key: "balance:grade:AU", title: "AU, г/т", group: "Балансовые", part: "balance" },
      { key: "offbalance:tonnes", title: "Руда, тыс. т", group: "Забалансовые", part: "offbalance" },
    ];
    expect(columnGroups(readForm({ columns: cols })!.columns)).toEqual([{ title: "Балансовые", span: 2 }, { title: "Забалансовые", span: 1 }]);
    const split = readForm({ template: { id: "gkz", name: "ГКЗ" }, columns: cols, sections: [{ title: "D1", rows: [{ label: "A", type: "group", cells: { "balance:tonnes": { value: 1, text: "1" } } }] }, { title: "D2", rows: [] }] })!;
    const out = view({ ...SAVED, form: split });
    expect(out).toContain(">Балансовые</th>");
    expect(out).toContain(">Забалансовые</th>");
    expect(out).toMatch(/data-testid="res-form-section-0"><td[^>]*>D1<\/td>/);
    expect(out).toContain('data-testid="res-form-section-1"');
  });

  it("a report with no form says what to do: a saved one — choose a template; a preview — in the window", () => {
    const saved = view({ ...SAVED, form: null });
    expect(saved).toContain('data-testid="res-form-none"');
    expect(saved).toContain("выберите шаблон");
    expect(view({ ...PREVIEW, form: null })).toContain("в окне «Отчёт по ресурсам»");
  });
});

describe("«Таблица»: all the rows of the core", () => {
  const html = view(SAVED, { tab: "table" });
  it("one line per row of the answer; the total row is «Итого»; the numbers are the core's", () => {
    expect(count(html, /data-testid="res-row-\d+"/g)).toBe(SAVED.rows.length);
    expect(SAVED.rows).toHaveLength(8);
    expect(html).toMatch(/res-row-0-group"[^>]*>Итого<\/td>/);
    expect(html).toMatch(/res-row-1-group"[^>]*>Измеренные<\/td>/);
    expect(html).toMatch(/res-row-0-tonnes"[^>]*>12\s?500<\/td>/);
    expect(html).toMatch(/res-row-0-grade-AU"[^>]*>2,5<\/td>/);
    expect(html).toContain("Содержание, г/т");
    expect(html).toContain("Металл, г");
    expect(html).toContain("Строк: 8");
  });

  it("a row with no block above the cut-off has no grade and no metal: «нет значения», never 0", () => {
    const i = SAVED.rows.findIndex((r) => r.cutoff === 2 && r.g[0] === "Измеренные");
    expect(SAVED.rows[i].elements.AU.grade).toBeNull();
    expect(html).toMatch(new RegExp(`res-row-${i}-grade-AU"[^>]*data-missing="true"[^>]*>нет значения</td>`));
    expect(html).toMatch(new RegExp(`res-row-${i}-metal-AU"[^>]*data-missing="true"[^>]*>нет значения</td>`));
    expect(html).toMatch(new RegExp(`res-row-${i}-tonnes"[^>]*>0</td>`)); // tonnes 0 is a number: it stays 0
  });

  it("a metal of an unknown unit is «—» with the reason, a grade stays", () => {
    const rows = SAVED.rows.map((r) => ({ ...r, elements: { AU: { ...r.elements.AU, metal: null } } }));
    const out = view({ ...SAVED, rows }, { tab: "table" });
    expect(out).toMatch(/title="Единица «г\/т» не распознана[^"]*"[^>]*data-testid="res-row-0-metal-AU"[^>]*>—<\/td>/);
    expect(out).toMatch(/res-row-0-grade-AU"[^>]*>2,5<\/td>/);
  });

  it("the cut-off of the table: the rows of one cut-off; every cut-off of the report is in the list", () => {
    expect(cutoffOptions(SAVED)).toEqual([1]);
    expect(cutoffOptions({ ...SAVED, form: null })).toEqual([1, 2]);
    const one = view({ ...SAVED, form: null }, { tab: "table", rowCutoff: 2 });
    expect(count(one, /data-testid="res-row-\d+"/g)).toBe(4);
    expect(one).toContain("Строк: 4 из 8");
  });

  it("«без борта» is in the list of the cut-offs (the core lists only the numeric ones): a form on it can be left back, and a list of [без борта, 1] is not disabled", () => {
    const form = SAVED.form!;
    const noCut = { ...SAVED, form: { ...form, cutoff: null, cutoffs: [1] } };
    expect(cutoffOptions(noCut)).toEqual([null, 1]);
    const out = view(noCut, { tab: "form" });
    const sel = out.match(/<select[^>]*data-testid="res-form-cutoff"[^>]*>(.*?)<\/select>/)!;
    expect(sel[0]).not.toContain("disabled");
    expect(sel[1]).toContain(">без борта<");
    expect(sel[1]).toContain("≥ 1");
    expect(sel[1]).toMatch(/<option value="0" selected="">без борта/); // the form is on «без борта»: the first one is the chosen one
    // the rows of kind `all` make the same list for a table without a form
    const rowAll = { ...SAVED.rows[0], kind: "all" as const, cutoff: null };
    expect(cutoffOptions({ ...SAVED, form: null, rows: [rowAll, ...SAVED.rows] })).toEqual([null, 1, 2]);
    expect(cutoffOptions(SAVED)).toEqual([1]); // a form on a number: no «без борта» in the list
  });

  it("a preview is cut into pages of 500 on the screen; a saved report's rows are the page of the core", () => {
    const many = Array.from({ length: 1200 }, () => SAVED.rows[1]);
    const first = view({ ...PREVIEW, rows: many, rows_total: 1200 }, { mode: "run", tab: "table" });
    expect(count(first, /data-testid="res-row-\d+"/g)).toBe(500);
    expect(first).toContain("Страница 1 из 3");
    const last = view({ ...PREVIEW, rows: many, rows_total: 1200 }, { mode: "run", tab: "table", page: 3 });
    expect(count(last, /data-testid="res-row-\d+"/g)).toBe(200);
    const page = view({ ...SAVED, rows: many.slice(0, 500), rows_total: 1200 }, { mode: "report", tab: "table", page: 2 });
    expect(count(page, /data-testid="res-row-\d+"/g)).toBe(500);
    expect(page).toContain("Страница 2 из 3");
  });
});

describe("the template, the cut-off and the files", () => {
  const T = [{ id: "jorc", name: "JORC", builtin: true, version: 1 }, { id: "gkz", name: "ГКЗ", builtin: true, version: 1 }];
  it("a saved report: the template is chosen from the list, the exports are on", () => {
    const html = view(SAVED, {}, { templates: T });
    expect(html).toMatch(/<select[^>]*aria-label="Шаблон формы"[^>]*data-testid="res-template"[^>]*>/);
    expect(html).not.toMatch(/<select[^>]*data-testid="res-template"[^>]*disabled/);
    expect(html).toContain('<option value="jorc" selected="">JORC</option>');
    expect(html).toContain(">ГКЗ</option>");
    expect(html).not.toMatch(/data-testid="res-export-xlsx"[^>]*disabled|disabled=""[^>]*data-testid="res-export-xlsx"/);
    expect(html).toContain("Сохранить XLSX…");
    expect(html).toContain("CSV таблицы…");
    expect(html).toContain("CSV формы…");
  });
  it("a preview is not in the project: the template and the cut-off cannot be asked, the files cannot be written, and the panel says why", () => {
    const html = view(PREVIEW, { mode: "run" }, { templates: T });
    expect(html).toMatch(/disabled=""[^>]*data-testid="res-template"|data-testid="res-template"[^>]*disabled=""/);
    expect(html).toMatch(/disabled=""[^>]*data-testid="res-export-xlsx"|data-testid="res-export-xlsx"[^>]*disabled=""/);
    expect(html).toContain("Предпросмотр не записан в проект");
    expect(html).toContain("Предпросмотр отчёта по ресурсам");
    expect(html).toContain("не записан в проект");
  });
  it("the title of a saved report: the name, the number, the version, the badge of the graph", () => {
    const html = view(SAVED);
    expect(html).toContain("Отчёт «Отчёт по ресурсам» №2");
    expect(html).toContain("версия 1");
    expect(html).toContain("модель «Модель»");
  });
});

describe("«Контроль»", () => {
  it("the real control: the sum of the groups is the total, nothing is out of the count", () => {
    const html = view(SAVED, { tab: "control" });
    expect(html).toContain("Сумма групп равна итогу (норма 1,00e-12)");
    expect(html).toMatch(/data-testid="res-control-sum"/);
    expect(html).toContain("Тоннаж, т");
    expect(html).toContain("Металл AU, г");
    expect(html).toMatch(/res-control-no-density"[^>]*>0 бл\.<\/dd>/);
    expect(html).toMatch(/res-control-no-grade-AU"[^>]*>0 бл\.<\/dd>/);
    expect(html).not.toContain("res-control-status");
    expect(html).not.toContain("res-control-wireframes");
  });

  const rich: Control = readControl({
    tolerance: 1e-12,
    sum_groups: { tonnes: { sum: 20000, total: 20001, rel: 5e-5 }, volume: { sum: 8000, total: 8000, rel: 0 }, metal: { AU: { sum: 35000, total: 35000, rel: 0 } }, ok: false },
    no_density: { blocks: 3, volume: 3000 },
    default_density: { blocks: 2, tonnes: 5000 },
    no_grade: { AU: { blocks: 4, tonnes: 10000 } },
    status: { AU: { prefix: "AU_OK_", items: [{ code: 1, label: "вне домена", blocks: 2, volume: 2000, share: 0.25 }, { code: 2, label: "мало проб", blocks: 1, volume: 1000, share: 0.125 }, { code: 3, label: "вырожденная система", blocks: 0, volume: 0, share: 0 }] } },
    wireframes: [{ wireframe_id: 7, name: "Карьер", volume_restrictor: 1.0e6, volume_wireframe: 1.05e6, diff_pct: 4.8, warning: true, outside_extent: false }, { wireframe_id: 8, name: "Далеко", volume_restrictor: 0, volume_wireframe: 500, diff_pct: null, warning: true, outside_extent: true }],
    warnings: [{ code: "wireframe_volume", message: "Объём каркаса «Карьер» расходится с объёмом блоков на 4,8 %.", hint: "Проверьте охват модели." }],
  })!;
  it("the statuses of the estimate, the wireframes, the blocks out of the count and the warnings are told with their numbers", () => {
    const html = renderToStaticMarkup(createElement(ControlView, { result: { ...SAVED, control: rich, warnings: [{ code: "no_density", message: "Есть блоки без плотности.", hint: "", param: null }] } }));
    expect(html).toContain("Сумма групп не равна итогу: отклонение больше нормы 1,00e-12");
    expect(html).toContain("5,00e-5");
    expect(html).toMatch(/res-control-no-density"[^>]*>3 бл\., объём 3\s?000 м³ \(тоннаж неизвестен\)<\/dd>/);
    expect(html).toMatch(/res-control-default-density"[^>]*>2 бл\., тоннаж 5\s?000 т<\/dd>/);
    expect(html).toMatch(/res-control-no-grade-AU"[^>]*>4 бл\., 10\s?000 т \(в среднее и металл не входят\)<\/dd>/);
    expect(html).toContain("AU · AU_OK_STATUS");
    expect(html).toContain("1 — вне домена");
    expect(html).toContain("25 %");
    expect(html).toContain("12,5 %");
    expect(html).toContain("Карьер");
    expect(html).toContain("4,8 %");
    expect(html).toContain("каркас за охватом модели");
    expect(html).toContain("Объём каркаса «Карьер» расходится");
    expect(html).toContain("Есть блоки без плотности.");
  });
  it("the helpers: the relative difference in a power, the share in per cent, a gap is a gap", () => {
    expect(relText(0)).toBe("0");
    expect(relText(null)).toBe("нет значения");
    expect(relText(3.2e-13)).toBe("3,20e-13");
    expect(shareText(0.125)).toBe("12,5 %");
    expect(shareText(null)).toBe("нет значения");
    expect(allWarnings({ ...SAVED, warnings: [{ code: "x", message: "m", hint: "", param: null }], control: { ...rich, warnings: [{ code: "x", message: "m", hint: "", param: null }] } })).toHaveLength(1);
  });
  it("a report with no control says so", () => {
    expect(renderToStaticMarkup(createElement(ControlView, { result: { ...SAVED, control: null } }))).toContain("нет контроля");
  });
});

describe("«Кривая»: the points of the curve of the core", () => {
  it("the data of the chart are the arrays of the curve; the unit of the axis is the unit of the cut-off field", () => {
    const curve = SAVED.curve!;
    expect(curve.cutoffs).toEqual([0.2, 1.4, 2.6, 3.8, 5]);
    const data = curveData(SAVED, curve, "AU");
    expect(data.cutoffs).toEqual(curve.cutoffs);
    expect(data.tonnes).toEqual(curve.tonnes);
    expect(data.grade).toEqual(curve.elements.AU.grade);
    expect(data.metal).toEqual(curve.elements.AU.metal);
    expect(data).toMatchObject({ field: "AU", unit: "г/т", metal_unit: "г" });
    expect(curveMarks(SAVED)).toEqual([1, 2]);
    expect(curveElement(SAVED, curve, null)).toBe("AU");
    expect(curveElement(SAVED, curve, "ZZ")).toBe("AU");
  });
  it("the chart draws the line of the tonnes and the grade through every point; the tab shows it with the switch and the data export", () => {
    const html = view(SAVED, { tab: "curve" });
    expect(html).toContain('data-testid="res-curve"');
    expect(html).toContain('data-points="5"');
    expect(html).toContain('data-series="tonnes"');
    expect(html).toContain('data-series="grade"');
    expect(count(html, /<circle /g)).toBe(5); // a point of the grade line for each cut-off
    expect(html).toContain("5 бортов по полю AU");
    expect(html).toContain("Содержание ḡ");
    expect(html).toContain("Металл M");
    expect(html).toContain("CSV кривой");
    expect(html).toContain('data-mark="1"');
    const metal = view(SAVED, { tab: "curve", right: "metal" });
    expect(metal).toContain('data-series="metal"');
    expect(count(metal, /<circle /g)).toBe(5);
  });
  it("a gap of the grade breaks the line (it never drops to 0); a metal of an unknown unit switches the metal off", () => {
    const curve = { ...SAVED.curve!, elements: { AU: { grade: [1, null, 3, null, 5], metal: [null, null, null, null, null] } } };
    const html = renderToStaticMarkup(createElement(CurveChart, { result: SAVED, curve, element: "AU", right: "grade" }));
    expect(html).toContain('data-pieces="3"'); // 1 | 3 | 5
    expect(count(html, /<circle /g)).toBe(3);
    expect(html).toMatch(/disabled=""[^>]*data-testid="res-curve-metal"|data-testid="res-curve-metal"[^>]*disabled=""/);
    const asked = renderToStaticMarkup(createElement(CurveChart, { result: SAVED, curve, element: "AU", right: "metal" }));
    expect(asked).toContain('data-series="grade"'); // «Металл» is not possible: the grade is shown
  });
  it("two elements: the element of the chart is chosen in a list; a report with no curve says why", () => {
    const two = { ...SAVED.curve!, elements: { AU: SAVED.curve!.elements.AU, CU: { grade: [0.1, 0.2, 0.3, 0.4, 0.5], metal: [1, 2, 3, 4, 5] } } };
    const res = { ...SAVED, elements: [...SAVED.elements, { field: "CU", unit: "%", unit_label: "%", metal_family: "t", metal_base: "т" }] };
    const html = renderToStaticMarkup(createElement(CurveChart, { result: res, curve: two, element: "CU", right: "grade" }));
    expect(html).toContain('data-testid="res-curve-element"');
    expect(html).toContain('data-element="CU"');
    expect(html).toContain('<option value="CU" selected="">CU</option>');
    expect(view({ ...SAVED, curve: null }, { tab: "curve" })).toContain("Кривая не построена");
  });
  it("a frame of 100 points is drawn in 16 ms (the best of 30: the machine of the test may stall)", () => {
    const n = 100;
    const cutoffs = Array.from({ length: n }, (_, i) => 0.05 + i * 0.05);
    const tonnes = cutoffs.map((_, i) => 2e7 * (1 - i / (n + 5)));
    const curve = { field: "AU", cutoffs, blocks: cutoffs.map(() => 1), volume: tonnes, tonnes, elements: { AU: { grade: cutoffs.map((c) => c * 1.6), metal: tonnes.map((t, i) => t * (0.05 + i * 0.08)) } } };
    const data = curveData(SAVED, curve, "AU");
    const draw = () => renderToStaticMarkup(createElement(GradeTonnageChart, { curve: data, right: "grade", marks: [0.5, 1, 2] }));
    for (let i = 0; i < 5; i++) draw(); // warm up
    let best = Infinity;
    let html = "";
    for (let i = 0; i < 30; i++) {
      const t0 = performance.now();
      html = draw();
      best = Math.min(best, performance.now() - t0);
    }
    expect(best).toBeLessThanOrEqual(16);
    expect(html).toContain('data-series="tonnes"');
    expect(html).toContain('data-pieces="1"'); // one unbroken line through the 100 points (no dots over 80 points)
    expect((html.match(/<path d="M[^"]*"[^>]*stroke-width="2"/g) ?? []).length).toBeGreaterThanOrEqual(2);
  });
});

describe("a click on a cell: what 20.15 gets", () => {
  it("a cell of the table of the rows is exact: the labels of all the keys, the cut-off, the kind, the column", () => {
    const total = cellOfRow(SAVED, SAVED.rows[0], "tonnes");
    expect(total).toMatchObject({ reportId: 2, source: "table", exact: true, group: null, label: "Итого", cutoff: 1, kind: "cutoff", column: "tonnes", metric: "tonnes", element: null });
    const group = cellOfRow(SAVED, SAVED.rows[2], "grade:AU");
    expect(group).toMatchObject({ group: ["Выявленные"], cutoff: 1, metric: "grade", element: "AU" });
    expect(cellOfRow(SAVED, { ...SAVED.rows[1], g: [null], total: false }, "blocks").group).toBeNull();
    expect(cellOfRow(SAVED, { ...SAVED.rows[1], g: ["Измеренные", null] }, "blocks").group).toEqual(["Измеренные", "(пусто)"]);
    expect(cellOfRow(SAVED, { ...SAVED.rows[0], kind: "all", cutoff: null }, "volume")).toMatchObject({ kind: "all", cutoff: null });
  });
  it("a cell of the form: one group of one key is exact; a subtotal and a section are sums (members, section, no group)", () => {
    const form = SAVED.form!;
    const [measured, , subtotal, , total] = form.sections[0].rows;
    expect(cellOfForm(SAVED, form, null, measured, form.columns[0])).toMatchObject({ source: "form", exact: true, group: ["Измеренные"], cutoff: 1, kind: "cutoff", column: "tonnes", metric: "tonnes" });
    expect(cellOfForm(SAVED, form, null, subtotal, form.columns[1])).toMatchObject({ exact: false, group: null, members: ["Измеренные", "Выявленные"], element: "AU" });
    expect(cellOfForm(SAVED, form, null, total, form.columns[0])).toMatchObject({ exact: true, group: null });
    expect(cellOfForm(SAVED, form, "D1", measured, form.columns[0])).toMatchObject({ exact: false, section: "D1", group: null });
  });
  it("the cells of a saved report are buttons and give their cell to the taker; a preview's are not (with the reason)", () => {
    const got: CellRef[] = [];
    const saved = view(SAVED, { tab: "table" }, { onCell: (c) => got.push(c) });
    expect(saved).toMatch(/res-row-0-tonnes"[^>]*role="button"|role="button"[^>]*data-testid="res-row-0-tonnes"/);
    expect(saved).toContain("Щелчок — блоки этой ячейки");
    const formHtml = view(SAVED, { tab: "form" }, { onCell: (c) => got.push(c) });
    expect(formHtml).toMatch(/role="button"[^>]*data-testid="res-form-cell-0-0-tonnes"|data-testid="res-form-cell-0-0-tonnes"[^>]*role="button"/);
    const preview = view(PREVIEW, { mode: "run", tab: "table" }, { onCell: () => undefined });
    expect(preview).not.toContain('role="button"');
    expect(preview).toContain("Предпросмотр не записан в проект: сохраните отчёт, чтобы перейти к блокам ячейки");
    expect(view(SAVED, { tab: "table" })).not.toContain('role="button"'); // nobody takes the clicks
  });
  it("one taker at a time: it is called, taken away, and a click with none is told as not taken", () => {
    const got: CellRef[] = [];
    const off = setCellClickHandler((c) => got.push(c));
    const cell = cellOfRow(SAVED, SAVED.rows[0], "tonnes");
    expect(emitCellClick(cell)).toBe(true);
    expect(got).toEqual([cell]);
    const other = setCellClickHandler(() => undefined);
    off(); // the first one is not the taker any more: it does not take the second's place away
    expect(emitCellClick(cell)).toBe(true);
    other();
    expect(emitCellClick(cell)).toBe(false);
  });
});
