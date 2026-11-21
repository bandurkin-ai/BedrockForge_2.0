// Э15 15.18: the interface against the answers of the real core (15.11–15.13) — `tt/preview`, `run/preview` and the
// `report` of the records `tt` and `intercepts` as the core gave them on the demo set with the samples of Т8.5-11
// (global α 30°, Aп 120°; c = 1, c2 = 1,5, L_min = 2, L_п = 2, `min_by = tt`), trimmed to the first rows; the
// labels of every key of `report`; the cutoff c > 0 (the core: 422 `bad_param` {param: "cutoff"}).
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ResultRow } from "../src/modules/composites/api";
import { toRunPreview, toTtPreview } from "../src/modules/intercepts/api";
import { InterceptsPreview, interceptRows } from "../src/modules/intercepts/InterceptsPreview";
import { coreErrors, DEFAULT_ORIENTATION } from "../src/modules/intercepts/params";
import { interceptReportFacts } from "../src/modules/intercepts/results";

const COUNTS = (n: number) => ({ src: { глобальная: n }, flags: { "1": 0, "2": 0, "4": 0, "8": 0 } });

const TT_ANSWER = {
  hole: "DH-001",
  rows: {
    from: [0, 1],
    to: [1, 2],
    length: [1, 1],
    TT: [0.5447998057000659, 0.5443360045561643],
    TT_REL: [0.5447998057000659, 0.5443360045561643],
    TT_FLAG: [0, 0],
    TT_SRC: ["глобальная", "глобальная"],
    ALPHA: [30, 30],
    DIPDIR: [120, 120],
    HOLE_THETA: [30.37379707090349, 30.403753528928558],
    HOLE_AZ: [83.16924322322916, 83.18974105754404],
  },
  counts: COUNTS(10),
  mesh_skipped: false,
  set_summary: COUNTS(30),
  notes: [],
};

const REPORT_RUN = { counts: COUNTS(1), rows: 1, intercepts: 1, included: 0, length: 5, tt: 2.71703868091082, len_g: 5.8, mean: 1.16, waste_len: 2, miss_len: 0, excluded: 0, skipped: 0 };

const RUN_ANSWER = {
  hole: "DH-001",
  unit: "г/т",
  samples: { from: [0, 1, 2], to: [1, 2, 3], values: { AU: [0.1, 1.5, 2.0], CU: [0, 0.1, 0.2] }, ore: [false, true, true] },
  candidates: [
    { from: 1, to: 6, length: 5, tt: 2.71703868091082, grade: 1.16, kind: "основное", accepted: true, reason: null },
    { from: 1, to: 3, length: 2, tt: 1.0882080413403428, grade: 1.75, kind: "в том числе", accepted: false, reason: "ИМ меньше m_min" },
    { from: 9, to: 10, length: 1, tt: 0.5406196087305426, grade: 3, kind: "основное", accepted: false, reason: "ИМ меньше m_min" },
  ],
  result: {
    from: [1],
    to: [6],
    values: {
      LENGTH: [5], TT: [2.71703868091082], TT_FLAG: [0], TT_SRC: ["глобальная"], ALPHA: [30], DIPDIR: [120], HOLE_THETA: [30.46], HOLE_AZ: [83.23],
      AU: [1.16], CU: [0.3], LEN_G: [5.8], TT_G: [3.151764869856551], WASTE_LEN: [2], N_INT: [5], X: [4999.99], Y: [7996.71], Z: [415.36],
      INCL: [0], LABEL: ["5,0 м @ 1,16 г/т (ИМ 2,7 м)"], ID: [1], KIND: ["основное"], MISS_LEN: [0],
    },
  },
  summary: REPORT_RUN,
  mesh_skipped: false,
  set_summary: { ...REPORT_RUN, counts: COUNTS(3), rows: 3, intercepts: 3, length: 15, tt: 7.7815829448396165, len_g: 17.4, waste_len: 6 },
  notes: [],
};

const row = (op: string, report: Record<string, unknown>) => ({ id: 1, set_id: 3, table_id: 12, table: "Пробы Т11", op, op_title: op, makes_table: op === "intercepts", name: op, params: {}, inputs: {}, report, stale: false, reason: null }) as unknown as ResultRow;

describe("the answers of the real core", () => {
  it("`tt/preview`: rows, counts of the hole and of the set", () => {
    const a = toTtPreview(TT_ANSWER);
    expect(a.hole).toBe("DH-001");
    expect(a.rows).toHaveLength(2);
    expect(a.rows[0]).toMatchObject({ from: 0, to: 1, length: 1, tt: 0.5447998057000659, ratio: 0.5447998057000659, alpha: 30, dipdir: 120, src: "глобальная", flag: 0 });
    expect(a.bySrc).toEqual({ глобальная: 10 });
    expect(a.setCounts).toEqual({ bySrc: { глобальная: 30 }, byFlag: { 1: 0, 2: 0, 4: 0, 8: 0 } });
    expect(a.meshSkipped).toBe(false);
  });

  it("`run/preview`: the summary in the form of `report` (Σ TT, mean, WASTE_LEN, MISS_LEN shown; the set too)", () => {
    const a = toRunPreview(RUN_ANSWER);
    expect(a.summary).toEqual({ intercepts: 1, included: 0, length: 5, len_g: 5.8, tt: 2.71703868091082, mean: 1.16, waste_len: 2, miss_len: 0, rows: 1, excluded: 0, skipped: 0 });
    expect(a.setSummary).toMatchObject({ intercepts: 3, length: 15, len_g: 17.4, waste_len: 6 });
    expect(a.candidates.filter((c) => !c.accepted).map((c) => c.reason)).toEqual(["ИМ меньше m_min", "ИМ меньше m_min"]);
    const rows = interceptRows(a, "AU");
    expect(rows[0]).toMatchObject({ from: 1, to: 6, length: 5, grade: 1.16, lenG: 5.8, flag: 0, src: "глобальная", label: "5,0 м @ 1,16 г/т (ИМ 2,7 м)" });
    const html = renderToStaticMarkup(createElement(InterceptsPreview, { answer: a, field: "AU" }));
    for (const t of ["Σ TT, м", "Среднее содержание, г/т", "Σ WASTE_LEN, м", "Σ MISS_LEN, м", "17,4"]) expect(html, t).toContain(t);
    expect(html).not.toContain("Сводка по набору — после расчёта");
  });

  it("`report` of `tt`: every key in Russian; `sources`, `absent` read; nothing under a raw key", () => {
    const rep = { counts: COUNTS(30), rows: 30, length: 30, tt: 15.54, with_tt: 30, excluded: 0, capped: 2, absent: ["mesh"], sources: [{ source: "global", rows: 30 }], skipped: 1, journal: [], versions: { numpy: "2.3.5", numba: "0.68.1" }, edits: [], seconds: 8, written: [{ table: "пробы т11", columns: ["TT"] }] };
    const facts = interceptReportFacts(row("tt", rep));
    const by = Object.fromEntries(facts.map((f) => [f.label, f.value]));
    expect(by).toMatchObject({
      "Строк": "30",
      "Σ LENGTH, м": "30",
      "Σ TT, м": "15,54",
      "Строк с TT": "30",
      "Вне расчёта (нет глубины, TO ≤ FROM)": "0",
      "Интервалов с увеличенной частью ствола": "2",
      "Без FROM или TO (не прочитаны, поля пусты)": "1",
      "Строк по источнику «Глобальная»": "30",
      "Источников не было": "По каркасу",
    });
    for (const k of ["with_tt", "capped", "skipped", "absent", "sources", "journal", "seconds"]) expect(facts.map((f) => f.label)).not.toContain(k);
  });

  it("`report` of `intercepts`: Σ of the main ones, the unit by the mean, the field; a null TT stays «—»", () => {
    const rep = { ...REPORT_RUN, rows: 6, intercepts: 3, included: 3, length: 15, tt: null, len_g: 17.4, unit: "г/т", field: "AU", journal: [], versions: {}, edits: [], seconds: 4.1, written: [], table_columns: ["LENGTH"] };
    const by = Object.fromEntries(interceptReportFacts(row("intercepts", rep)).map((f) => [f.label, f.value]));
    expect(by).toMatchObject({
      "Пересечений": "3",
      "«В том числе»": "3",
      "Σ LENGTH основных, м": "15",
      "Σ TT основных, м": "—",
      "Среднее содержание, г/т": "1,16",
      "Проб вне правила (нет глубины, TO ≤ FROM, перекрытия)": "0",
      "Проб без FROM или TO (не прочитаны)": "0",
      "Единица содержания": "г/т",
      "Поле содержания": "AU",
    });
    expect(Object.keys(by)).not.toContain("unit");
    expect(Object.keys(by)).not.toContain("field");
  });

  it("422 `bad_param` {param: cutoff} of the core marks the field «Бортовое содержание c»", () => {
    const e = Object.assign(new Error("Бортовое содержание c должно быть больше нуля."), { body: { code: "bad_param", param: "cutoff", message: "Бортовое содержание c должно быть больше нуля.", hint: "Задайте c больше нуля." } });
    expect(coreErrors(e, DEFAULT_ORIENTATION)).toEqual({ cutoff: "Бортовое содержание c должно быть больше нуля." });
  });
});
