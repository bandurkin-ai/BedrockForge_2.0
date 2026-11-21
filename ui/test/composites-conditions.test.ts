// «Анализ кондиций» and the manual edit of «Рудные интервалы» (ТЗ 7.35 Д-26, Т8.2-20; ADR 0018 §12, решения 23,
// 24; Э12 12.31): the body of the job goes through `oreFinish` (thickness, contacts) and gets `exclude`; more
// than 200 variants never starts and the window says why; the table of the form И1, сл. 24 (Σ MC / Σ M,
// Σ M / число, прирезка между соседними бортами, пометка «не 10–15 %», пропуск — «—»), its CSV; the edit:
// intervals of a hole, boundaries by samples, the bodies of split / merge / move, violations. The core is a stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ResultRow } from "../src/modules/composites/api";
import { jobBody } from "../src/modules/composites/bind";
import { ConditionsWindow, lastAnalysis, VariantsTable } from "../src/modules/composites/ConditionsWindow";
import { editReason, OreEdit } from "../src/modules/composites/OreEdit";
import { defaultValues, NO_SOURCE, toolBody } from "../src/modules/composites/params";
import { renderCompositesWindow } from "../src/modules/composites/ToolWindow";
import { conditions, conditionsFinish, MAX_VARIANTS, toVariants, variantCount, variantCountText, variantRows, variantsCsv, variantsFileName, variantsUnit, type Variant } from "../src/modules/composites/tools/conditions";
import { toOreAnswer } from "../src/modules/composites/tools/ore";
import { editBody, editIntervals, editText, moveTargets, sampleBounds, sendEdit, splitPoints, toEditAnswer, violationText, withRows } from "../src/modules/composites/tools/oreEdit";
import { setCoreTarget } from "../src/shell/api";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const sent: { path: string; method: string; body: Record<string, unknown> }[] = [];

beforeEach(() => {
  sent.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      const u = new URL(url);
      const path = u.pathname.replace("/api/v1", "") + u.search;
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      sent.push({ path, method: init?.method ?? "GET", body });
      if (path === "/drillholes/sets/3/tables") return Promise.resolve(json({ tables: [{ table: "5", kind: "assay", name: "Пробы", rows: 8, rev: 1 }] }));
      if (path === "/composites/results/9/edit") return Promise.resolve(json({ rows: { from: [3, 7], to: [7, 11], values: { CLASS: ["ore", "subgrade"], NESTED: [0, 0], MANUAL: [1, 1] } }, violations: [{ code: "k", message: "Интервал не отвечает условию К.", hint: "", from: 7, to: 11 }], report: { edits: [{ action: "split" }] } }));
      return Promise.resolve(json({ code: "not_found", message: "Нет", hint: "" }, 404));
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
});

const SRC = { ...NO_SOURCE, setId: 3, table: "5", fields: ["AU"] };
const values = (o: Record<string, string>) => ({ ...defaultValues(conditions.params), ...o });

describe("«Анализ кондиций»: the body of the job", () => {
  it("goes through oreFinish: thickness, no thickness_field / contacts_values; lists without repeats, ascending", () => {
    const r = toolBody(conditions, SRC, values({ cutoffs: "1,5; 0,5; 1; 1", m_min: "3; 1", m_waste_max: "5; 3", thickness_field: "TT" }));
    expect(r.reason).toBeNull();
    expect(r.body).toMatchObject({ set_id: 3, table: "5", field: "AU", cutoffs: [0.5, 1, 1.5], m_min: [1, 3], m_waste_max: [3, 5], mode: "strict_excl", missing: "break", thickness: { source: "field", field: "TT" } });
    expect(r.body).not.toHaveProperty("thickness_field");
    expect(r.body).not.toHaveProperty("contacts_values");
    expect(toolBody(conditions, SRC, values({ cutoffs: "1", m_min: "1", m_waste_max: "3" })).body).toMatchObject({ thickness: { source: "length" } });
  });

  it("«по контактам» without a domain is refused as in «Рудные интервалы»; codes → contacts_values", () => {
    expect(toolBody(conditions, SRC, values({ cutoffs: "1", m_min: "1", m_waste_max: "3", mode: "contacts" })).reason).toMatch(/по контактам/);
    const src = { ...SRC, domain: { kind: "field" as const, table: "7", field: "LITO" } };
    expect(toolBody(conditions, src, values({ cutoffs: "1", m_min: "1", m_waste_max: "3", mode: "contacts", contacts_values: "2, QZ" })).body).toMatchObject({ domain: { table: "7", field: "LITO" }, contacts_values: ["2", "QZ"] });
  });

  it("exclude {field, values} (решение 23): values as typed; a field without values — why not", () => {
    const r = toolBody(conditions, SRC, values({ cutoffs: "1", m_min: "1", m_waste_max: "3", exclude_field: "CODE", exclude_values: "Карьер, Отвал; Карьер" }));
    expect(r.body).toMatchObject({ exclude: { field: "CODE", values: ["Карьер", "Отвал"] } });
    expect(r.body).not.toHaveProperty("exclude_field");
    expect(toolBody(conditions, SRC, values({ cutoffs: "1", m_min: "1", m_waste_max: "3", exclude_field: "CODE" })).reason).toMatch(/Значения для исключения/);
    expect(toolBody(conditions, SRC, values({ cutoffs: "1", m_min: "1", m_waste_max: "3" })).body).not.toHaveProperty("exclude");
  });

  it("more than 200 variants is not started: the reason; repeats do not count", () => {
    const big = values({ cutoffs: "1; 2; 3; 4; 5; 6", m_min: "1; 2; 3; 4; 5; 6", m_waste_max: "1; 2; 3; 4; 5; 6" });
    expect(toolBody(conditions, SRC, big).reason).toBe("Вариантов 216: не больше 200");
    expect(conditionsFinish({ cutoffs: [1, 1, 1], m_min: [1, 2], m_waste_max: [3] })).toMatchObject({ cutoffs: [1] });
    const c = variantCount(values({ cutoffs: "0,5; 1; 1,5; 1", m_min: "1; 3", m_waste_max: "3; 5" }));
    expect(c).toEqual({ sizes: [3, 2, 2], total: 12 });
    expect(variantCountText(c)).toBe("Вариантов: 3 × 2 × 2 = 12");
    expect(variantCountText(variantCount(values({ cutoffs: "1", m_min: "", m_waste_max: "x" })))).toBe("Вариантов: 1 × ? × ?");
    expect(MAX_VARIANTS).toBe(200);
  });

  it("the assistant: the same body through jobBody; too many — a refusal with the reason", async () => {
    const body = await jobBody(conditions, { set_id: 3, table: "Пробы", field: "AU", cutoffs: "0,5; 1; 1,5", m_min: "1; 3", m_waste_max: "3; 5" });
    expect(body).toMatchObject({ table: "5", cutoffs: [0.5, 1, 1.5], thickness: { source: "length" } });
    await expect(jobBody(conditions, { set_id: 3, table: "Пробы", field: "AU", cutoffs: "1;2;3;4;5;6", m_min: "1;2;3;4;5;6", m_waste_max: "1;2;3;4;5;6" })).rejects.toThrow(/Вариантов 216: не больше 200/);
  });

  it("the window: the count, and with more than 200 variants «Применить» is off with the reason", () => {
    const html = renderToStaticMarkup(createElement(ConditionsWindow, { winId: "w", args: { set_id: 3, table: "5", field: "AU", cutoffs: "1;2;3;4;5;6", m_min: "1;2;3;4;5;6", m_waste_max: "1;2;3;4;5;6" } }));
    expect(html).toContain('data-testid="composites-conditions"');
    expect(html).toMatch(/data-testid="conditions-count"[^>]*>Вариантов: 6 × 6 × 6 = 216 — больше 200: расчёт не запускается/);
    expect(html).toMatch(/data-testid="composites-reason">Вариантов 216: не больше 200</);
    expect(html).toMatch(/data-testid="composites-apply" disabled/);
    const ok = renderToStaticMarkup(createElement(ConditionsWindow, { winId: "w", args: { set_id: 3, table: "5", field: "AU", cutoffs: "0,5; 1", m_min: "1", m_waste_max: "3" } }));
    expect(ok).toContain("Вариантов: 2 × 1 × 1 = 2");
    expect(ok).not.toMatch(/data-testid="composites-apply" disabled/);
    const routed = renderCompositesWindow({ id: "w1", kind: "composites-conditions", title: "Анализ кондиций", props: {} } as never);
    expect(renderToStaticMarkup(routed as never)).toContain('data-testid="composites-conditions"');
  });
});

// Т8.2-20 (И1, сл. 24): 17424 / 3432 = 5,08; 3432 / 610 = 5,63
const V = (o: Partial<Variant> & Pick<Variant, "cutoff" | "m_min" | "m_waste_max">): Record<string, unknown> => ({ count: null, m_sum: null, mc_sum: null, ...o });
const REPORT = {
  unit: "г/т",
  variants: [
    V({ cutoff: 1, m_min: 1, m_waste_max: 3, count: 610, m_sum: 3432, mc_sum: 17424 }),
    V({ cutoff: 0.5, m_min: 1, m_waste_max: 3, count: 700, m_sum: 4000, mc_sum: 18800, m_mean: 5.714, c_mean: 4.7 }),
    V({ cutoff: 1.5, m_min: 1, m_waste_max: 3, count: 600, m_sum: 3300, mc_sum: 17000 }),
    V({ cutoff: 0.5, m_min: 3, m_waste_max: 3, count: 0, m_sum: 0, mc_sum: 0 }),
    { cutoff: "x" },
  ],
};

describe("the table of the variants (И1, сл. 24–26)", () => {
  it("variants of a report or of the answer of a job; a broken row goes; missing values stay null", () => {
    expect(toVariants(REPORT)).toHaveLength(4);
    expect(toVariants({ report: REPORT })).toHaveLength(4);
    expect(toVariants({ rows: REPORT.variants })).toHaveLength(4);
    expect(toVariants(null)).toEqual([]);
    expect(variantsUnit({ report: REPORT })).toBe("г/т");
    const v = toVariants(REPORT).find((x) => x.cutoff === 1)!;
    expect(v.c_mean).toBeCloseTo(17424 / 3432, 12);
    expect(v.m_mean).toBeCloseTo(3432 / 610, 12);
    const empty = toVariants(REPORT).find((x) => x.m_min === 3)!;
    expect(empty.c_mean).toBeNull();
    expect(empty.m_mean).toBeNull();
  });

  it("rows by (Мрт, Мпп), Сб ascending; прирезка (ΣMC₁ − ΣMC₂)/(ΣM₁ − ΣM₂); the mark «не 10–15 %»", () => {
    const rows = variantRows(toVariants(REPORT));
    expect(rows.map((r) => [r.m_min, r.cutoff, r.first])).toEqual([
      [1, 0.5, true],
      [1, 1, false],
      [1, 1.5, false],
      [3, 0.5, true],
    ]);
    expect(rows[0].step_pct).toBeNull();
    expect(rows[0].mark).toBeNull();
    expect(rows[1].step_pct).toBeCloseTo(14.2, 6); // (4000 − 3432) / 4000
    expect(rows[1].c_trim).toBeCloseTo((18800 - 17424) / (4000 - 3432), 12);
    expect(rows[1].mark).toBeNull();
    expect(rows[2].step_pct).toBeCloseTo(3.846, 3);
    expect(rows[2].mark).toMatch(/на 3,8 % — рекомендуется 10–15 %/);
    expect(rows[3].step_pct).toBeNull();
    const grow = variantRows([{ cutoff: 1, m_min: 1, m_waste_max: 3, count: 1, m_sum: 10, m_mean: 10, c_mean: 1, mc_sum: 10 }, { cutoff: 2, m_min: 1, m_waste_max: 3, count: 1, m_sum: 12, m_mean: 12, c_mean: 2, mc_sum: 24 }]);
    expect(grow[1].c_trim).toBeNull();
    expect(grow[1].mark).toMatch(/не должна расти/);
  });

  it("the table and its CSV: «5,077», «5,63», «—» for a missing value (never 0)", () => {
    const html = renderToStaticMarkup(createElement(VariantsTable, { variants: toVariants(REPORT), unit: "г/т", field: "AU" }));
    expect(html.match(/data-testid="conditions-row"/g)).toHaveLength(4);
    expect(html.match(/data-marked="1"/g)).toHaveLength(1);
    expect(html).toContain("5,077");
    expect(html).toContain("5,63");
    expect(html).toContain("Сб, г/т");
    const csv = variantsCsv(variantRows(toVariants(REPORT)), "г/т");
    const lines = csv.trim().split("\r\n");
    expect(lines[0]).toBe("Сб, г/т;Мрт, м;Мпп, м;Число интервалов;Ср. содержание (ΣMC/ΣM), г/т;Σ M, м;Ср. M, м;Σ MC;Изменение Σ M, %;Содержание в прирезке, г/т;Пометка");
    expect(lines[2]).toBe("1;1;3;610;5,077;3432;5,63;17424;14,2;2,423;");
    expect(lines[4]).toBe("0,5;3;3;0;;0;;0;;;");
    expect(variantsFileName("AU")).toBe("Анализ кондиций · AU.csv");
  });

  it("the last analysis of the table and field in the set", () => {
    const r = (id: number, table: string, field: string, op = "conditions") => ({ id, op, params: { table, field } }) as unknown as ResultRow;
    expect(lastAnalysis([r(1, "5", "AU"), r(4, "5", "AU"), r(6, "5", "AG"), r(7, "5", "AU", "ore")], "5", "AU")?.id).toBe(4);
    expect(lastAnalysis([r(1, "6", "AU")], "5", "AU")).toBeNull();
  });
});

// Т8.2-12-like hole: samples 1 м from 0 to 14; ore 3–11, subgrade 12–14, waste_in 6–7 inside
const PROTO = toOreAnswer({
  hole: "С-1",
  unit: "г/т",
  samples: { from: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], to: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14], values: { AU: [0, 0, 0, 2, 3, 2, 0.4, 3, 3, 2, 2, 0, 0.7, 0.8] } },
  result: {
    from: [3, 6, 12, 0],
    to: [11, 7, 14, 3],
    values: { CLASS: ["ore", "waste_in", "subgrade", "waste"], NESTED: [0, 1, 0, 0], M: [8, 1, 2, 3], C: [2.175, 0.4, 0.75, 0], MC: [17.4, 0.4, 1.5, 0], MANUAL: [0, 0, 1, 0], LABEL: ["8,0 м @ 2,18 г/т (МС 17,4)", "", "", ""] },
  },
});

describe("the manual edit (Д-26 «Место»)", () => {
  it("intervals of the hole: ore and subgrade of the top level, MANUAL read; the boundaries of the samples", () => {
    const list = editIntervals(PROTO.result);
    expect(list.map((x) => [x.from, x.to, x.cls, x.manual])).toEqual([
      [3, 11, "ore", false],
      [12, 14, "subgrade", true],
    ]);
    const b = sampleBounds(PROTO.samples);
    expect(b).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]);
    expect(splitPoints(list[0], b)).toEqual([4, 5, 6, 7, 8, 9, 10]);
    expect(moveTargets(list, 0, "top", b)).toEqual([0, 1, 2, 4, 5, 6, 7, 8, 9, 10]);
    expect(moveTargets(list, 0, "bottom", b)).toEqual([4, 5, 6, 7, 8, 9, 10, 12]);
    expect(moveTargets(list, 1, "top", b)).toEqual([11, 13]);
  });

  it("bodies of split, merge, move by sample boundaries; anything else — why not", () => {
    const list = editIntervals(PROTO.result);
    const b = sampleBounds(PROTO.samples);
    expect(editBody("С-1", "split", list, 0, { at: 7 }, b)).toEqual({ hole: "С-1", action: "split", from: 3, to: 7 });
    expect(editBody("С-1", "split", list, 0, { at: 7.5 }, b)).toMatch(/по границе пробы/);
    expect(editBody("С-1", "split", list, 0, { at: null }, b)).toMatch(/Выберите границу/);
    expect(editBody("С-1", "merge", list, 0, {}, b)).toEqual({ hole: "С-1", action: "merge", from: 3, to: 14 });
    expect(editBody("С-1", "merge", list, 1, {}, b)).toMatch(/Ниже нет интервала/);
    expect(editBody("С-1", "move", list, 0, { side: "top", target: 2 }, b)).toEqual({ hole: "С-1", action: "move", from: 3, to: 2 });
    expect(editBody("С-1", "move", list, 0, { side: "bottom", target: 12 }, b)).toEqual({ hole: "С-1", action: "move", from: 11, to: 12 });
    expect(editBody("С-1", "move", list, 0, { side: "bottom", target: 13 }, b)).toMatch(/между соседними/);
    expect(editBody(" ", "merge", list, 0, {}, b)).toBe("Выберите скважину");
    expect(editText({ hole: "С-1", action: "split", from: 3, to: 7 })).toBe("Разделить интервал от 3 м по 7 м");
    expect(editText({ hole: "С-1", action: "move", from: 11, to: 12 })).toBe("Сдвинуть границу 11 → 12 м");
  });

  it("POST …/edit: the body as is; violations, rows and report.edits of the answer", async () => {
    const a = await sendEdit(9, { hole: "С-1", action: "split", from: 3, to: 7 });
    expect(sent.at(-1)).toMatchObject({ path: "/composites/results/9/edit", method: "POST", body: { hole: "С-1", action: "split", from: 3, to: 7 } });
    expect(a.edits).toBe(1);
    expect(a.violations.map(violationText)).toEqual(["7–11 м: Интервал не отвечает условию К."]);
    expect(editIntervals(withRows(PROTO, a.rows).result).map((x) => [x.from, x.to, x.manual])).toEqual([
      [3, 7, true],
      [7, 11, true],
    ]);
    expect(toEditAnswer({ violations: ["Прослой больше Мпп", "", 5] })).toEqual({ rows: null, violations: [{ text: "Прослой больше Мпп", from: null, to: null }], edits: 0 });
    expect(withRows(PROTO, null)).toBe(PROTO);
  });

  const R = (o: Partial<ResultRow>): ResultRow =>
    ({ id: 9, set_id: 3, table_id: 12, table: "Рудные интервалы", op: "ore", op_title: "Рудные интервалы (ГКЗ)", makes_table: true, name: "Рудные интервалы", params: { field: "AU", cutoff: 1 }, inputs: {}, sha256: "", algo: "", report: {}, written: [], job_id: null, created_at: null, stale: false, reason: null, ...o }) as ResultRow;

  it("no edit in a read-only project, of a stale result or of another operation — the reason is shown", () => {
    expect(editReason(R({}), false)).toBeNull();
    expect(editReason(R({}), true)).toBe("Проект открыт только для чтения");
    expect(editReason(R({ stale: true, reason: "изменена таблица „Пробы“" }), false)).toBe("Результат устарел: изменена таблица „Пробы“: сначала «Перестроить»");
    expect(editReason(R({ op: "conditions", table_id: null }), false)).toMatch(/только у результата/);
    const ro = renderToStaticMarkup(createElement(OreEdit, { result: R({}), readOnly: true }));
    expect(ro).toMatch(/data-testid="ore-edit-reason">Проект открыт только для чтения/);
    const ok = renderToStaticMarkup(createElement(OreEdit, { result: R({ report: { edits: [{}, {}] } }), readOnly: false }));
    expect(ok).toContain("Ручных правок: 2 — «Перестроить» их потеряет.");
    expect(ok).toContain('aria-label="Скважина предпросмотра"');
    expect(ok).not.toContain("ore-edit-reason");
  });
});
