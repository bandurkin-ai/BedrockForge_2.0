// The block «Ориентировка» (ADR 0021 §4, §11; Э15 15.14): the four sources into the list `orientation` by priority,
// «если не найдена — глобальная», the ranges of 8.5.4 (out of range — marked, never sent; empty — not zero), Aп 360
// → 0, the table of domains (the command text «код=α/Aп; …», a paste from Excel/CSV, «Заполнить кодами поля»), the
// arguments of a command and back («Параметры…»); the form renders the inputs of the chosen source.
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { describe, expect, it } from "vitest";
import { DomainGrid } from "../src/modules/intercepts/DomainGrid";
import { OrientationForm } from "../src/modules/intercepts/OrientationForm";
import {
  argsFromOrientation,
  attitudesText,
  checkOrientation,
  DEFAULT_ORIENTATION,
  fillCodes,
  interceptBody,
  orientationFromArgs,
  parseAttitudes,
  parseAttitudeTable,
  RUN_TOOL,
  TT_TOOL,
  type OrientationState,
} from "../src/modules/intercepts/params";
import { defaultValues } from "../src/modules/composites/params";

const st = (patch: Partial<OrientationState>): OrientationState => ({ ...DEFAULT_ORIENTATION, ...patch });

describe("checkOrientation", () => {
  it("global: α, Aп in range; Aп 360 → 0; «0,5» is a number", () => {
    expect(checkOrientation(st({ alpha: "30", dipdir: "360" })).orientation).toEqual([{ source: "global", alpha: 30, dipdir: 0 }]);
    expect(checkOrientation(st({ alpha: "0,5", dipdir: "120,25" })).orientation).toEqual([{ source: "global", alpha: 0.5, dipdir: 120.25 }]);
    const bad = checkOrientation(st({ alpha: "91", dipdir: "-1" }));
    expect(bad.orientation).toBeNull();
    expect(bad.errors.alpha).toBe("«Угол падения α»: допустимо 0–90 °");
    expect(bad.errors.dipdir).toBe("«Азимут падения Aп»: допустимо 0–360 °");
    expect(bad.reason).toBe(bad.errors.alpha);
  });

  it("empty is never zero: an empty α is an error, not 0", () => {
    const r = checkOrientation(st({ alpha: "", dipdir: "10" }));
    expect(r.orientation).toBeNull();
    expect(r.errors.alpha).toContain("введите значение");
    const row = checkOrientation(st({ source: "domain", domainField: "LITH", attitudes: [{ code: "QZ", alpha: "", dipdir: "10" }] }));
    expect(row.errors["attitudes.0.alpha"]).toBe("«Строка 1, α»: введите значение");
  });

  it("domain: a field (the source table or another), wireframes; repeated codes and an empty table are refused", () => {
    const rows = [
      { code: "QZ", alpha: "60", dipdir: "120" },
      { code: "", alpha: "", dipdir: "" },
      { code: "SH", alpha: "45", dipdir: "360" },
    ];
    expect(checkOrientation(st({ source: "domain", domainField: "LITH", attitudes: rows })).orientation).toEqual([
      { source: "domain", field: "LITH", attitudes: [{ code: "QZ", alpha: 60, dipdir: 120 }, { code: "SH", alpha: 45, dipdir: 0 }] },
    ]);
    expect(checkOrientation(st({ source: "domain", domainField: "LITH", domainTable: "6", attitudes: rows })).orientation?.[0]).toMatchObject({ field: "LITH", table: "6" });
    expect(checkOrientation(st({ source: "domain", domainBy: "wireframes", domainWireframes: [4], attitudes: rows })).orientation?.[0]).toEqual({ source: "domain", wireframe_ids: [4], attitudes: [{ code: "QZ", alpha: 60, dipdir: 120 }, { code: "SH", alpha: 45, dipdir: 0 }] });
    expect(checkOrientation(st({ source: "domain", domainField: "LITH" })).errors.attitudes).toContain("пуста");
    expect(checkOrientation(st({ source: "domain", attitudes: rows })).errors.domain).toBe("Выберите поле кода домена");
    expect(checkOrientation(st({ source: "domain", domainField: "LITH", attitudes: [rows[0], rows[0]] })).errors["attitudes.1.code"]).toContain("повторяется");
  });

  it("mesh: wireframes (up to 16), d_max 0–1000, r_n 0–200", () => {
    expect(checkOrientation(st({ source: "mesh", meshWireframes: [3, 1] })).orientation).toEqual([{ source: "mesh", wireframe_ids: [3, 1], d_max: 50, r_n: 0 }]);
    expect(checkOrientation(st({ source: "mesh" })).errors.wireframes).toContain("Отметьте");
    expect(checkOrientation(st({ source: "mesh", meshWireframes: Array.from({ length: 17 }, (_, i) => i + 1) })).errors.wireframes).toContain("16");
    expect(checkOrientation(st({ source: "mesh", meshWireframes: [1], rN: "250" })).errors.r_n).toBe("«Радиус осреднения нормали r_n»: допустимо 0–200 м");
  });

  it("core: table, two fields, the line, d_к; the fallback goes second", () => {
    const r = checkOrientation(st({ source: "core", coreTable: "7", coreAlpha: "A", coreBeta: "B", fallback: true, fallbackAlpha: "30", fallbackDipdir: "90" }));
    expect(r.orientation).toEqual([
      { source: "core", table: "7", alpha: "A", beta: "B", line: "boh", d_core: 10 },
      { source: "global", alpha: 30, dipdir: 90 },
    ]);
    expect(checkOrientation(st({ source: "core", coreTable: "7" })).errors.core).toBe("Выберите поля α_к и β_к");
    expect(checkOrientation(st({ source: "core", coreTable: "7", coreAlpha: "A", coreBeta: "B", fallback: true, fallbackAlpha: "" })).errors.fallback_alpha).toContain("Глобальная α");
  });

  it("off (only «Рудные пересечения»): an empty list, and the body has no `orientation`", () => {
    expect(checkOrientation(st({ enabled: false })).orientation).toEqual([]);
    const values = { ...defaultValues(RUN_TOOL.params), cutoff: "1" };
    const r = interceptBody(RUN_TOOL, { setId: 3, table: "5", field: "AU" }, values, st({ enabled: false }));
    expect(r.reason).toBeNull();
    expect(r.body).not.toHaveProperty("orientation");
    const tt = interceptBody(TT_TOOL, { setId: 3, table: "5" }, defaultValues(TT_TOOL.params), st({ enabled: false }));
    expect(tt.reason).toBe("Задайте ориентировку");
  });

  it("the body: source first, then parameters, then the block", () => {
    const values = defaultValues(TT_TOOL.params);
    expect(interceptBody(TT_TOOL, { setId: null, table: null }, values, st({ alpha: "1", dipdir: "1" })).reason).toBe("Выберите набор скважин");
    expect(interceptBody(TT_TOOL, { setId: 3, table: "5" }, { ...values, eps: "0,6" }, st({ alpha: "1", dipdir: "1" })).reason).toBe("«Порог вырождения ε_в»: допустимо 0,01–0,5");
    expect(interceptBody(TT_TOOL, { setId: 3, table: "5" }, values, st({ alpha: "1", dipdir: "1" })).body).toEqual({ set_id: 3, table: "5", eps: 0.1, step: 1, angles: true, orientation: [{ source: "global", alpha: 1, dipdir: 1 }] });
  });
});

describe("the table of domains", () => {
  it("«код=α/Aп; …» both ways", () => {
    const rows = parseAttitudes("QZ=60/120; SH = 45,5 / 90");
    expect(rows).toEqual([
      { code: "QZ", alpha: "60", dipdir: "120" },
      { code: "SH", alpha: "45,5", dipdir: "90" },
    ]);
    expect(attitudesText(rows!)).toBe("QZ=60/120; SH=45,5/90");
    expect(parseAttitudes("QZ 60 120")).toBeNull();
  });

  it("a paste from Excel (tabs, a header) and CSV («;»)", () => {
    expect(parseAttitudeTable("Код\tAlpha\tAz\nQZ\t60\t120\r\nSH\t45\t90\n")).toEqual([
      { code: "QZ", alpha: "60", dipdir: "120" },
      { code: "SH", alpha: "45", dipdir: "90" },
    ]);
    expect(parseAttitudeTable("QZ;60;120\nbad line")).toEqual([{ code: "QZ", alpha: "60", dipdir: "120" }]);
  });

  it("«Заполнить кодами поля» keeps typed rows and adds new codes once", () => {
    const rows = fillCodes([{ code: "QZ", alpha: "60", dipdir: "120" }, { code: "", alpha: "", dipdir: "" }], ["QZ", "SH", "SH", " ", "GR"]);
    expect(rows).toEqual([
      { code: "QZ", alpha: "60", dipdir: "120" },
      { code: "SH", alpha: "", dipdir: "" },
      { code: "GR", alpha: "", dipdir: "" },
    ]);
  });
});

describe("arguments of the commands", () => {
  it("flat arguments → the block, and the params of a result → the arguments again", () => {
    const s = orientationFromArgs({ source: "domain", domain_wireframes: "4, 9", attitudes: "A=1/2", fallback_alpha: 5, fallback_dipdir: 6 });
    expect(typeof s).toBe("object");
    const o = checkOrientation(s as OrientationState).orientation!;
    expect(o).toEqual([
      { source: "domain", wireframe_ids: [4, 9], attitudes: [{ code: "A", alpha: 1, dipdir: 2 }] },
      { source: "global", alpha: 5, dipdir: 6 },
    ]);
    expect(argsFromOrientation(o)).toEqual({ source: "domain", domain_wireframes: "4, 9", attitudes: "A=1/2", fallback_alpha: 5, fallback_dipdir: 6 });
    const mesh = [{ source: "mesh", wireframe_ids: [3], d_max: 20, r_n: 1 }];
    expect(checkOrientation(orientationFromArgs(argsFromOrientation(mesh)) as OrientationState).orientation).toEqual(mesh);
    expect(orientationFromArgs({ source: "trend" })).toContain("Источник ориентировки");
    expect(orientationFromArgs({ source: "domain", attitudes: "QZ 60" })).toContain("«attitudes»");
    expect((orientationFromArgs({}, true) as OrientationState).enabled).toBe(false);
  });
});

describe("the form", () => {
  it("shows the inputs of the chosen source and marks a value out of range", () => {
    const html = (v: OrientationState, errors: Record<string, string> = {}, optional = false) =>
      renderToStaticMarkup(createElement(OrientationForm, { setId: null, table: null, fields: [], value: v, errors, onChange: () => undefined, optional }));
    const g = html(st({ alpha: "95" }), { alpha: "«Угол падения α»: допустимо 0–90 °" });
    expect(g).toContain("Ориентировка");
    for (const t of ["Глобальная", "По домену", "По каркасу", "Ориентированный керн"]) expect(g).toContain(t);
    expect(g).toContain('aria-invalid="true"');
    expect(g).toContain("допустимо 0–90 °");
    expect(g).not.toContain("Если не найдена");
    const m = html(st({ source: "mesh" }));
    expect(m).toContain("d_max");
    expect(m).toContain("Если не найдена — глобальная α, Aп");
    const c = html(st({ source: "core" }));
    expect(c).toContain("Линия отсчёта");
    expect(c).toContain("BOH");
    expect(html(st({ source: "domain" }))).toContain("Заполнить кодами поля");
    expect(html(st({ enabled: false }), {}, true)).not.toContain("По каркасу");
  });

  it("the grid marks the cell with an error and offers its tools", () => {
    const out = renderToStaticMarkup(createElement(DomainGrid, { rows: [{ code: "QZ", alpha: "99", dipdir: "1" }], errors: { "attitudes.0.alpha": "«Строка 1, α»: допустимо 0–90 °" }, onChange: () => undefined, loadCodes: null, fillReason: "Выберите поле кода домена" }));
    expect(out).toContain('aria-invalid="true"');
    expect(out).toContain("Вставить из буфера");
    expect(out).toMatch(/disabled=""[^>]*title="Выберите поле кода домена"|title="Выберите поле кода домена"[^>]*disabled=""/);
  });
});
