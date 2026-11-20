// The warning of ADR 0016 decision 4 (Э11 11.17): before new geometry of a series is saved, the window asks in its
// own dialog how many contours leave the windows of the sections (`POST /section-series/{id}/impact`); a series
// without contours saves without a question. The core is a fetch stub.
import fs from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { confirmImpact, impactQuestion, type SeriesImpact } from "../src/modules/section/seriesApi";
import { setCoreTarget } from "../src/shell/api";

const impact = (p: Partial<SeriesImpact> = {}): SeriesImpact => ({ series_id: 3, version: 2, layer_id: 7, layer: "Контуры", total: 5, outside: 2, moved: 0, examples: ["Разрез 1: линия 1 слоя «Контуры»", "Разрез 2: линия 2 слоя «Контуры»"], ...p });

describe("the question about the contours", () => {
  it("no contours, or all of them stay — no question", () => {
    expect(impactQuestion(impact({ total: 0, outside: 0, layer: null, examples: [] }))).toBeNull();
    expect(impactQuestion(impact({ outside: 0, moved: 0, examples: [] }))).toBeNull();
  });

  it("«N из M контуров выпадут из окон разрезов», the first of them, «Копировать серию»", () => {
    const q = impactQuestion(impact())!;
    expect(q.title).toBe("Контуры вне окон");
    expect(q.message.split("\n")).toEqual([
      "2 из 5 контуров слоя «Контуры» выпадут из окон разрезов.",
      "• Разрез 1: линия 1 слоя «Контуры»",
      "• Разрез 2: линия 2 слоя «Контуры»",
      "Контуры не удаляются. Чтобы оставить серию как есть, отмените и сделайте «Копировать серию» с новой геометрией.",
      "Сохранить новые параметры?",
    ]);
    expect([q.okLabel, q.danger]).toEqual(["Сохранить", true]);
  });

  it("Russian numbers; contours in another window are told too", () => {
    expect(impactQuestion(impact({ total: 1, outside: 1, examples: [] }))!.message).toMatch(/^1 из 1 контура слоя «Контуры» выпадет из окон разрезов\./);
    expect(impactQuestion(impact({ total: 21, outside: 21, layer: null, examples: [] }))!.message).toMatch(/^21 из 21 контура выпадет/);
    const both = impactQuestion(impact({ outside: 2, moved: 3 }))!.message.split("\n");
    expect(both[1]).toBe("Ещё 3 окажутся в окне другого разреза — атрибут SECTION у них не пересчитывается.");
    const moved = impactQuestion(impact({ outside: 0, moved: 1, examples: [] }))!;
    expect(moved.message.split("\n")[0]).toBe("1 из 5 контуров слоя «Контуры» окажется в окне другого разреза — атрибут SECTION у них не пересчитывается.");
    expect(moved.danger).toBe(false);
    const many = impactQuestion(impact({ total: 2000, outside: 1500, examples: Array.from({ length: 10 }, (_, k) => `Разрез ${k + 1}: линия 1`) }))!.message;
    expect(many).toContain("1 500 из 2 000 контуров");
    expect(many).toContain("… и ещё 1 495");
  });
});

describe("before the save", () => {
  const sent: { path: string; body: unknown }[] = [];
  let answer: Response;

  beforeEach(() => {
    sent.length = 0;
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      sent.push({ path: new URL(url).pathname.replace(/^\/api\/v1/, ""), body: JSON.parse(String(init?.body ?? "null")) });
      return answer.clone();
    });
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setCoreTarget(null);
  });

  it("asks the core with the parameters of the form; the user decides", async () => {
    answer = new Response(JSON.stringify(impact()), { status: 200 });
    const ask = vi.fn(async () => false);
    expect(await confirmImpact(3, { s0: 50, s1: 150 }, ask)).toBe(false);
    expect(sent).toEqual([{ path: "/section-series/3/impact", body: { params: { s0: 50, s1: 150 } } }]);
    expect(ask).toHaveBeenCalledTimes(1);
    expect(await confirmImpact(3, { s0: 50 }, async () => true)).toBe(true);
  });

  it("a series without contours saves without a question", async () => {
    answer = new Response(JSON.stringify(impact({ layer_id: null, layer: null, total: 0, outside: 0, examples: [] })), { status: 200 });
    const ask = vi.fn(async () => false);
    expect(await confirmImpact(3, { step: 30 }, ask)).toBe(true);
    expect(ask).not.toHaveBeenCalled();
  });

  it("a failed check does not stop the save: the PATCH tells the error with its field", async () => {
    answer = new Response(JSON.stringify({ code: "bad_series", message: "Шаг серии: число больше 0.", hint: "Исправьте шаг.", field: "step" }), { status: 422 });
    const ask = vi.fn(async () => false);
    expect(await confirmImpact(3, { step: -1 }, ask)).toBe(true);
    expect(ask).not.toHaveBeenCalled();
  });

  it("the window asks only when it edits a series, in its own dialog (ТЗ 7.0.11)", () => {
    const text = fs.readFileSync(path.join(__dirname, "../src/modules/section/SeriesWindow.tsx"), "utf8");
    expect(text).toContain('mode === "edit" ? readForm(f) : null');
    expect(text).toContain("confirmImpact(seriesId!, read.params, askConfirm)");
    expect(text).not.toMatch(/window\.(confirm|prompt|alert|open)\s*\(/);
  });
});
