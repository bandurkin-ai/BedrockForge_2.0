// The readers of the answers of the resource report (Э20, 20.12; docs/progress/e20-api.md §3–§9): the forms are those of the contract
// (`core/tests/data/resources/contract_answers.json`); a gap is `null` — never 0 (8.0.1); an answer that is not a report is not read.
import { describe, expect, it } from "vitest";
import { readCompare, readControl, readCurve, readDrill, readForm, readListRow, readReportResult, readRow, readWarnings } from "../../src/modules/resources/answers";
import { compareText, curveText, drillText, fmt, groupText, rowTitle, summaryText } from "../../src/modules/resources/format";
import { compareBody, selfCompare } from "./compareSample";
import { answer } from "./harness";

const result = () => readReportResult(answer("report_result").body)!;

describe("the result of the job", () => {
  it("the contract answer is a report: the model, the elements with their units, the rows, the total, the curve, the control", () => {
    const r = result();
    expect(r).not.toBeNull();
    expect(r).toMatchObject({ report_id: 2, saved: true, algo: "resources-1", rows_total: 8, truncated: false, cutoff_field: "AU" });
    expect(r.model).toMatchObject({ id: 1, name: "Модель" });
    expect(r.elements[0]).toMatchObject({ field: "AU", unit: "g/t", unit_label: "г/т", metal_family: "g", metal_base: "г" });
    expect(r.rows).toHaveLength(8);
    expect(r.keys[0]).toMatchObject({ kind: "field", field: "CLASS" });
    expect(r.totals).toMatchObject({ blocks: 5, tonnes: 12500 });
    expect(r.totals?.elements.AU).toMatchObject({ grade: 2.5, metal: 31250 });
    expect(r.curve?.cutoffs).toHaveLength(5);
    expect(r.control?.sum_groups?.ok).toBe(true);
    expect(r.excluded).toEqual({ filtered: 0, no_volume: 0, no_density: 0 });
    expect(r.params).toMatchObject({ model_id: 1, cutoffs: [1, 2] });
  });
  it("the preview has no number; a gap of an element is null, never 0", () => {
    const base = answer("report_result").body as { rows: Record<string, unknown>[] };
    const gap = { ...base.rows[1], elements: { AU: { wt: 0, sum: null, grade: null, metal: null, missing_t: 7500, missing_n: 3 } } };
    const r = readReportResult({ ...base, saved: false, report_id: null, rows: [gap] })!;
    expect(r.report_id).toBeNull();
    expect(r.saved).toBe(false);
    expect(r.rows[0].elements.AU).toMatchObject({ grade: null, metal: null, sum: null, missing_t: 7500, missing_n: 3 });
    expect(fmt(r.rows[0].elements.AU.grade)).toBe("нет значения");
  });
  it("an answer that is not a report is not read (no rows, a row without its kind, no model)", () => {
    expect(readReportResult(null)).toBeNull();
    expect(readReportResult({})).toBeNull();
    expect(readReportResult({ model: { id: 1 }, rows: [{ blocks: 1 }] })).toBeNull();
    expect(readReportResult({ model: {}, rows: [] })).toBeNull();
    expect(readReportResult({ model: { id: 1 }, rows: [] })?.rows).toEqual([]);
    expect(readRow({ kind: "cutoff", blocks: 2, cutoff: 1, volume: NaN, tonnes: null, elements: {} })).toMatchObject({ volume: null, tonnes: null });
  });
  it("the curve needs two points of numbers; the control and the form are read as they come", () => {
    expect(readCurve({ cutoffs: [1] })).toBeNull();
    expect(readCurve({ cutoffs: [1, null] })).toBeNull();
    expect(readCurve({ field: "AU", cutoffs: [1, 2], tonnes: [5, null], elements: { AU: { grade: [1, null], metal: [null, null] } } })?.tonnes).toEqual([5, null]);
    expect(readControl({})).toBeNull();
    expect(readControl({ status: { AU: { prefix: "AU_OK_", items: [{ code: 1, label: "вне домена", blocks: 2, volume: 20, share: 0.1 }] } }, wireframes: [{ wireframe_id: 7, name: "К", diff_pct: 1.5, warning: true }], no_density: { blocks: 3, volume: 30 } })).toMatchObject({ status: { AU: { items: [{ code: 1, share: 0.1 }] } }, wireframes: [{ warning: true, diff_pct: 1.5 }], no_density: { blocks: 3 } });
    expect(readForm(null)).toBeNull();
    const form = readForm({ template_id: "jorc", cutoff: 1, columns: [{ key: "tonnes", label: "Руда, тыс. т", group: null }], sections: [{ label: null, rows: [{ kind: "subtotal", label: "Изм. + Выяв.", cells: { tonnes: { value: 12.5, text: "12,5" }, x: { value: null, text: "—" } } }] }] })!;
    expect(form.sections[0].rows[0]).toMatchObject({ kind: "subtotal", cells: { tonnes: { value: 12.5, text: "12,5" }, x: { value: null, text: "—" } } });
  });
  it("the warnings: a code, the words, the hint; a bare text stays a text", () => {
    expect(readWarnings([{ code: "unit_unknown", message: "м", hint: "п", param: "elements.1.unit" }, "просто"])).toEqual([{ code: "unit_unknown", message: "м", hint: "п", param: "elements.1.unit" }, { code: "warning", message: "просто", hint: "", param: null }]);
  });
});

describe("the list, the drill and the comparison", () => {
  it("the list of the reports: the state in the graph, the totals for the tree", () => {
    const rows = (answer("reports_list").body as { reports: unknown[] }).reports.map(readListRow);
    expect(rows[0]).toMatchObject({ id: 2, state: "ready", model: { id: 1 }, graph: { stale: false, pinned: false } });
    expect(rows[0]?.totals?.elements.AU.metal).toBe(31250);
    expect(readListRow({ name: "без номера" })).toBeNull();
    expect(readListRow({ id: 1, state: "bogus" })?.state).toBe("ready");
    expect(readListRow({ id: 1, graph: { stale: true, stale_reason: "изменена таблица" } })?.graph).toMatchObject({ stale: true, stale_reason: "изменена таблица" });
  });
  it("the drill: the rules and the numbers of the cell", () => {
    const d = readDrill(answer("drill").body)!;
    expect(d.cell).toEqual({ blocks: 3, volume: 3000, tonnes: 7500 });
    expect(d.model_id).toBe(12);
    expect(readDrill({})).toBeNull();
    expect(drillText({ ...d, stale: true, stale_reason: "изменена таблица" }, "Р")).toContain("набор блоков может отличаться");
  });
  it("the comparison of the real answer: Δ = A − B on the rows both have; a row only in one has no difference (null, not 0); the head of the versions", () => {
    const c = readCompare(answer("compare_reports").body)!;
    expect(c.rows).toHaveLength(12);
    const both = c.rows.filter((r) => r.only_in === null);
    expect(both).toHaveLength(4);
    for (const r of both) expect(r.delta?.tonnes).toBe((r.a?.tonnes ?? NaN) - (r.b?.tonnes ?? NaN)); // the difference is A − B
    expect(both[0].a).toMatchObject({ blocks: 5, tonnes: 12500 });
    expect(both[0].b).toMatchObject({ blocks: 5, tonnes: 13500 });
    for (const r of c.rows.filter((x) => x.only_in !== null)) {
      expect(r.delta, "a row only in one report has no difference").toBeNull();
      expect(r.only_in === "a" ? r.b : r.a, "the other side is not there").toBeNull();
    }
    expect(c.a).toMatchObject({ id: 5, name: "Сравнение A", version: 1, stale: false, model: { id: 1, name: "Модель" } });
    expect(c.a.model.cells_sha).toMatch(/^[0-9a-f]{64}$/);
    expect(c.a.inputs.map((i) => i.kind)).toEqual(["object:block_model", "bm_field", "bm_field"]);
    expect(c.keys).toEqual(["CLASS"]);
    expect(c.warnings[0]).toMatchObject({ code: "density_differs" });
    expect(compareText(c)).not.toContain("Различий нет"); // the tonnes differ and there are rows only in one report
    expect(compareText(c)).toContain("Строки только в одном отчёте: 8");
  });
  it("the report against itself (A = B, made of the real answer): all the differences are 0", () => {
    const c = readCompare(selfCompare())!;
    expect(c.rows.length).toBeGreaterThan(1);
    for (const r of c.rows) expect(r.delta?.tonnes).toBe(0);
    expect(compareText(c)).toContain("Различий нет");
    const only = readCompare({ ...compareBody(), rows: [{ g: ["Х"], kind: "cutoff", cutoff: 1, only_in: "b", a: null, b: { blocks: 1, elements: {} }, delta: null }] })!;
    expect(only.rows[0]).toMatchObject({ only_in: "b", a: null, delta: null });
    expect(only.rows[0].b).toMatchObject({ blocks: 1 });
    expect(readCompare({ rows: [] })).toBeNull();
  });
});

describe("the words", () => {
  it("the title of a row and the labels of its keys", () => {
    expect(rowTitle({ kind: "all", cutoff: null })).toBe("без борта");
    expect(rowTitle({ kind: "cutoff", cutoff: 0.5 })).toBe("≥ 0,5");
    expect(rowTitle({ kind: "balance", cutoff: 1 })).toBe("балансовые ≥ 1");
    expect(rowTitle({ kind: "offbalance", cutoff: 0.5, cutoff_to: 1 })).toBe("забалансовые 0,5…1");
    expect(groupText({ g: ["Измеренные", null], total: false })).toBe("Измеренные / (пусто)");
    expect(groupText({ g: [null], total: true })).toBe("Итого");
  });
  it("the text of a run and of the curve for the log and the assistant", () => {
    const r = result();
    const text = summaryText(r);
    expect(text).toContain("записан в проект");
    expect(text).toContain("AU 2,5 г/т");
    expect(text).toMatch(/металл 31\s250 г/);
    expect(summaryText({ ...r, saved: false, report_id: null, truncated: true })).toContain("Предпросмотр");
    expect(summaryText({ ...r, warnings: [{ code: "unit_unknown", message: "Единица не распознана.", hint: "Металл не считается.", param: null }] })).toContain("Предупреждение: Единица не распознана. Металл не считается.");
    const c = curveText(r, r.curve!, 3);
    expect(c).toContain("5 бортов по полю AU");
    expect(c.split("\n")).toHaveLength(4);
  });
});
