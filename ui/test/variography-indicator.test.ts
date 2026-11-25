// The indicator transform of the window «Вариография» (подшаг 18.10; ТЗ 8.8.1, ADR 0024 № 85): the choice «Индикатор
// (порог t)» in the data form, `transform: indicator:<t>` in the bodies, the commands, the stored model and the
// answers of the core; the threshold typed as text until it is a number.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { asTransform, defaultsQuery, readCalc, readDefaults, type DataBody } from "../src/modules/variography/api";
import { chartType } from "../src/modules/variography/calc";
import { countsText, DataForm, summaryText } from "../src/modules/variography/DataForm";
import { modelTitleText, propertiesText } from "../src/modules/variography/ModelsTree";
import { dataBody, dataFromArgs, DEFAULT_DATA, experimentalBody, storedData, type DataState } from "../src/modules/variography/state";

const DATA: DataState = { ...DEFAULT_DATA, setId: 3, table: "12", field: "AU" };
const ROW = { id: 1, name: "М", set_id: 3, table: "12", field: "AU", transform: "indicator:0.5", type: "semivariogram", color: null, sill: 0.25, structures: 1, stale: false, stale_reason: null, source_lost: false, created_at: null, updated_at: null };

describe("the transform in the data", () => {
  it("asTransform: none, nscore, indicator:<t>, indicator + threshold; anything else is none", () => {
    expect(asTransform("nscore")).toBe("nscore");
    expect(asTransform("indicator:0.5")).toBe("indicator:0.5");
    expect(asTransform("indicator", 1.5)).toBe("indicator:1.5");
    expect(asTransform("indicator", " 2 ")).toBe("indicator:2");
    expect(asTransform("indicator")).toBe("indicator:");
    for (const x of [undefined, null, "", "none", "log", 5]) expect(asTransform(x)).toBe("none");
  });

  it("dataBody: the threshold as a number (a decimal comma too); a text that is not a number is refused", () => {
    expect(dataBody({ ...DATA, transform: "indicator:1,50" }).body?.transform).toBe("indicator:1.5");
    expect(dataBody({ ...DATA, transform: "indicator: -2 " }).body?.transform).toBe("indicator:-2");
    expect(dataBody({ ...DATA, transform: "indicator:0" }).body?.transform).toBe("indicator:0");
    for (const t of ["indicator:", "indicator:  ", "indicator:abc", "indicator:1e999", "indicator:NaN"]) {
      const r = dataBody({ ...DATA, transform: t as DataState["transform"] });
      expect(r.body).toBeNull();
      expect(r.reason).toBe("Индикатор: введите порог — число в единицах поля");
    }
    expect(dataBody({ ...DATA, transform: "none" }).body?.transform).toBe("none");
    expect(dataBody({ ...DATA, transform: "nscore" }).body?.transform).toBe("nscore");
  });

  it("the experimental body and the query of /defaults carry the threshold", () => {
    const body = dataBody({ ...DATA, transform: "indicator:0.5" }).body as DataBody;
    expect(defaultsQuery(body)).toBe("set_id=3&table=12&field=AU&transform=indicator%3A0.5");
    expect(readDefaults({ set_id: 3, indicator: { threshold: 0.5, p: 0.4 } }).indicator).toEqual({ threshold: 0.5, p: 0.4 });
    expect(readDefaults({ set_id: 3 }).indicator).toBeNull();
    expect(readDefaults({ set_id: 3, indicator: { threshold: "x" } }).indicator).toBeNull();
    expect(experimentalBody).toBeTypeOf("function");
  });

  it("the commands: transform + threshold, or the string of the core", () => {
    expect(dataFromArgs({ set_id: 3, table: "12", field: "AU", transform: "indicator", threshold: 0.5 }).transform).toBe("indicator:0.5");
    expect(dataFromArgs({ set_id: 3, transform: "indicator:2" }).transform).toBe("indicator:2");
    expect(dataFromArgs({ set_id: 3, transform: "nscore" }).transform).toBe("nscore");
    expect(dataFromArgs({ set_id: 3, transform: "indicator" }).transform).toBe("indicator:");
    expect(dataFromArgs({ set_id: 3 }).transform).toBe("none");
  });

  it("a stored model and an answer of the core keep the indicator", () => {
    const m = { source: { set_id: 3, table: "12", field: "AU", domain: null, weights: null }, meta: { transform: "indicator:0.5" } };
    expect(storedData(m as never).transform).toBe("indicator:0.5");
    expect(readCalc({ transform: "indicator:0.5", sigma2: 0.24 }).transform).toBe("indicator:0.5");
    expect(readCalc({ transform: "log" }).transform).toBe("none");
    expect(chartType({ type: "semivariogram" }, "indicator:0.5")).toBe("semivariogram");
    expect(chartType({ type: "nscore" }, "indicator:0.5")).toBe("semivariogram");
  });
});

describe("the texts", () => {
  const answer = readDefaults({ set_id: 3, table: "12", table_name: "Пробы", field: "AU", unit: "г/т", counts: { rows: 10, used: 8 }, sigma2: 0.234375, sigma2_z: 2.1, median_length: 2, weights: null, indicator: { threshold: 2, p: 0.375 } });

  it("the line under the form has the threshold and the share p", () => {
    expect(summaryText(answer)).toContain("индикатор: порог 2, доля точек z ≤ t — 0,375");
    expect(summaryText(answer)).toContain("σ² = 0,2344");
    expect(summaryText({ ...answer, indicator: null })).not.toContain("индикатор");
    expect(countsText(answer)).toBe("Точек: 8 из 10");
  });

  it("the tree of models names the indicator and its threshold", () => {
    expect(modelTitleText(ROW)).toContain("(индикатор, порог 0,5)");
    expect(propertiesText(ROW)).toContain("Тип: полувариограмма, индикатор, порог 0,5");
    expect(modelTitleText({ ...ROW, transform: "none" })).not.toContain("индикатор");
  });
});

describe("the form", () => {
  const form = (data: DataState) => renderToStaticMarkup(createElement(DataForm, { value: data, onChange: () => undefined }));

  it("«Индикатор (порог t)» is a choice; the threshold field only with it", () => {
    const html = form({ ...DATA, transform: "indicator:0.5" });
    expect(html).toContain("Индикатор (порог t)");
    expect(html).toContain('aria-label="Порог индикатора"');
    expect(html).toContain('value="0.5"');
    expect(html).toMatch(/<option value="indicator" selected="">Индикатор \(порог t\)<\/option>/);
    expect(form({ ...DATA, transform: "none" })).not.toContain("Порог индикатора");
    expect(form({ ...DATA, transform: "nscore" })).not.toContain("Порог индикатора");
  });
});
