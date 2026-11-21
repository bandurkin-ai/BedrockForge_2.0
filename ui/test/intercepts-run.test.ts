// The window «Рудные пересечения» (ТЗ 8.5.3–8.5.4; ADR 0021 §11; Э15 15.16): the answer of
// `POST /intercepts/run/preview` in the form of docs/progress/e15-api.md made safe (empty stays empty), Т8.5-11 and
// Т8.5-12 in the preview (one intercept 1–6 м, 5 м @ 1,16, метропроцент 5,8, 9–10 refused «короче L_min»; «в том
// числе» 1–3 м @ 1,75, 5–6 refused), 409 `table_exists` → «Заменить?» in our own window and again with
// `replace: true`, the window from the arguments of the command. The core is a fetch stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { askReplace } from "../src/modules/composites/ToolWindow";
import { runPreview, startRun, toRunPreview } from "../src/modules/intercepts/api";
import { RUN_WINDOW } from "../src/modules/intercepts/bind";
import { candidateText, InterceptsPreview, interceptRows, lenGTitle, samplesWithOre } from "../src/modules/intercepts/InterceptsPreview";
import { gradeChoices, gradesFromArgs, runInitial, runParams } from "../src/modules/intercepts/InterceptsWindow";
import { renderInterceptsWindow } from "../src/modules/intercepts/TtWindow";
import { CoreError, setCoreTarget } from "../src/shell/api";
import { closeDialog, dialogStore } from "../src/shell/dialogs";
import type { FloatWin } from "../src/shell/windows";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

const G = [0.1, 1.5, 2.0, 0.2, 0.3, 1.8, 0.1, 0.1, 0.1, 3.0];
const SAMPLES = { from: G.map((_, i) => i), to: G.map((_, i) => i + 1), values: { AU: G }, ore: G.map((g) => g >= 1) };

// Т8.5-11: c = 1, L_п = 2, L_min = 2, no orientation (TT empty, bit 2)
const T11 = {
  hole: "СКВ-1",
  unit: "г/т",
  samples: SAMPLES,
  candidates: [
    { from: 1, to: 6, length: 5, tt: null, grade: 1.16, kind: "основное", accepted: true, reason: null },
    { from: 9, to: 10, length: 1, tt: null, grade: 3, kind: "основное", accepted: false, reason: "короче L_min" },
  ],
  result: {
    from: [1],
    to: [6],
    values: { ID: [1], KIND: ["основное"], LENGTH: [5], TT: [null], AU: [1.16], LEN_G: [5.8], TT_G: [null], WASTE_LEN: [0.5], MISS_LEN: [0], N_INT: [5], INCL: [null], TT_FLAG: [2], TT_SRC: ["нет"], LABEL: ["5,0 м @ 1,16 г/т"] },
  },
  summary: { intercepts: 1, included: 0, length: 5, len_g: 5.8 },
  set_summary: null,
  notes: [],
};

// Т8.5-12: Т8.5-11 with c2 = 1,5
const T12 = {
  ...T11,
  candidates: [
    ...T11.candidates,
    { from: 1, to: 3, length: 2, tt: null, grade: 1.75, kind: "в том числе", accepted: true, reason: null },
    { from: 5, to: 6, length: 1, tt: null, grade: 1.8, kind: "в том числе", accepted: false, reason: "короче L_min" },
  ],
  result: {
    from: [1, 1],
    to: [6, 3],
    values: {
      ID: [1, 1],
      KIND: ["основное", "в том числе"],
      LENGTH: [5, 2],
      TT: [null, null],
      AU: [1.16, 1.75],
      LEN_G: [5.8, 3.5],
      TT_G: [null, null],
      WASTE_LEN: [0.5, 0],
      MISS_LEN: [0, 0],
      N_INT: [5, 2],
      INCL: [null, 1],
      TT_FLAG: [2, 2],
      TT_SRC: ["нет", "нет"],
      LABEL: ["5,0 м @ 1,16 г/т", "в т. ч. 2,0 м @ 1,75 г/т"],
    },
  },
  summary: { intercepts: 1, included: 1, length: 5, len_g: 5.8 },
  set_summary: { intercepts: 120, included: 14, length: 610.5, len_g: 801.2 },
};

const sent: { path: string; body: Record<string, unknown> }[] = [];
let exists = 0;

beforeEach(() => {
  sent.length = 0;
  exists = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url).pathname.replace("/api/v1", "");
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      sent.push({ path, body });
      if (path === "/intercepts/run/preview") return json(T11);
      if (path === "/intercepts/run" && exists-- > 0)
        return json({ code: "table_exists", message: "Таблица уже есть.", hint: "Замените или задайте другое имя.", name: "Рудные пересечения", suggested: "Рудные пересечения (2)", lost: [{ id: 4, op: "tt", title: "Истинная мощность", name: "TT" }] }, 409);
      if (path === "/intercepts/run") return json({ source: "core", id: 21 });
      return json({ code: "not_found", message: "Нет", hint: "" }, 404);
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
  for (const d of dialogStore.get().queue) closeDialog(d.id);
});

describe("the answer of the preview", () => {
  it("Т8.5-11 read as the contract gives it; empty stays empty", () => {
    const a = toRunPreview(T11);
    expect(a.unit).toBe("г/т");
    expect(a.ore).toEqual([false, true, true, false, false, true, false, false, false, true]);
    expect(a.candidates.map((c) => [c.from, c.to, c.accepted, c.reason])).toEqual([
      [1, 6, true, null],
      [9, 10, false, "короче L_min"],
    ]);
    const rows = interceptRows(a, "AU");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ from: 1, to: 6, length: 5, grade: 1.16, lenG: 5.8, tt: null, ttG: null, flag: 2, label: "5,0 м @ 1,16 г/т" });
    expect(a.summary).toMatchObject({ intercepts: 1, included: 0, length: 5, len_g: 5.8, tt: null, mean: null });
    expect(a.setSummary).toBeNull();
    expect(samplesWithOre(a).values.__ORE).toEqual([null, "руда", "руда", null, null, "руда", null, null, null, "руда"]);
  });

  it("a part the core leaves out is empty, not zero", () => {
    const e = toRunPreview({});
    expect(e).toEqual({ hole: "", unit: null, samples: { from: [], to: [], values: {} }, ore: [], candidates: [], result: { from: [], to: [], values: {} }, summary: null, setSummary: null, notes: [] });
    const bad = toRunPreview({ samples: { from: [0, 1], to: [1, 2], values: { AU: [Number.NaN, "x"] }, ore: [null, true] }, candidates: [{ from: 0 }, "x"], summary: { intercepts: 2 } });
    expect(bad.samples.values.AU).toEqual([null, "x"]);
    expect(bad.ore).toEqual([null, true]);
    expect(bad.candidates).toEqual([]);
    expect(bad.summary).toEqual({ intercepts: 2, included: null, length: null, len_g: null, tt: null, mean: null, waste_len: null, miss_len: null, rows: null, excluded: null, skipped: null });
    expect(renderToStaticMarkup(createElement(InterceptsPreview, { answer: e, field: "AU" }))).toContain("нет проб");
  });

  it("`POST /intercepts/run/preview {params, hole_id}`", async () => {
    const a = await runPreview({ set_id: 3, table: "12", field: "AU", cutoff: 1 }, 17);
    expect(sent).toEqual([{ path: "/intercepts/run/preview", body: { params: { set_id: 3, table: "12", field: "AU", cutoff: 1 }, hole_id: 17 } }]);
    expect(a.hole).toBe("СКВ-1");
  });
});

describe("Т8.5-11 and Т8.5-12 in the preview", () => {
  it("Т8.5-11: one intercept 1–6 м, 5 м @ 1,16, метропроцент 5,8; 9–10 refused «короче L_min»", () => {
    const html = renderToStaticMarkup(createElement(InterceptsPreview, { answer: toRunPreview(T11), field: "AU" }));
    expect(html).toContain("5,0 м @ 1,16 г/т");
    const row = /<tr data-kind="основное">(.*?)<\/tr>/.exec(html)?.[1] ?? "";
    expect(row).toContain("<td>1,00</td><td>6,00</td><td>5,00</td><td>—</td><td>1,16</td><td>5,80</td><td>—</td>");
    expect(row).not.toContain("<td>0,00</td><td>1,16");
    expect(html).toContain("Отброшенные кандидаты");
    expect(html).toContain("9–10 м: 1,0 м @ 3,00 г/т — короче L_min");
    expect((html.match(/data-refused="yes"/g) ?? []).length).toBe(1);
    // the samples with g ≥ c are marked; the summary of the set comes after the job
    expect((/data-testid="intercepts-run-samples"[\s\S]*?<\/svg>/.exec(html)?.[0].match(/data-flag="yes"/g) ?? []).length).toBe(4);
    expect(html).toContain("Сводка по набору — после расчёта");
    expect(html).toContain("метрограмм");
  });

  it("Т8.5-12: «в том числе 1–3 м: 2 м @ 1,75»; 5–6 refused", () => {
    const html = renderToStaticMarkup(createElement(InterceptsPreview, { answer: toRunPreview(T12), field: "AU" }));
    expect(html).toContain("в т. ч. 2,0 м @ 1,75 г/т");
    const incl = /<tr data-kind="в том числе">(.*?)<\/tr>/.exec(html)?.[1] ?? "";
    expect(incl).toContain("<td>1.1</td>");
    expect(incl).toContain("<td>1,00</td><td>3,00</td><td>2,00</td><td>—</td><td>1,75</td>");
    expect(html).toContain("в т. ч. 5–6 м: 1,0 м @ 1,80 г/т — короче L_min");
    expect((html.match(/data-refused="yes"/g) ?? []).length).toBe(2);
    expect(html).not.toContain("Сводка по набору — после расчёта");
    expect(html).toContain("<td>120</td>");
  });

  it("texts of a candidate and of LEN_G", () => {
    expect(candidateText({ from: 1, to: 6, length: 5, tt: 4.1, grade: 1.16, kind: "основное", accepted: true, reason: null }, "г/т")).toBe("1–6 м: 5,0 м @ 1,16 г/т (ИМ 4,1 м)");
    expect(candidateText({ from: 1, to: 2, length: null, tt: null, grade: null, kind: "основное", accepted: false, reason: null }, null)).toBe("1–2 м: 1,0 м @ —");
    expect(lenGTitle("%")).toBe("метропроцент");
    expect(lenGTitle("г/т")).toBe("метрограмм");
    expect(lenGTitle(null)).toBe("метропроцент");
  });
});

describe("«Заменить?»", () => {
  it("409 table_exists asks in our own window with what is lost; «Заменить» repeats with replace: true", async () => {
    exists = 1;
    const run = startRun({ set_id: 3, table: "12", field: "AU", cutoff: 1, name: "Рудные пересечения" }, askReplace);
    await vi.waitFor(() => expect(dialogStore.get().queue).toHaveLength(1));
    const d = dialogStore.get().queue[0];
    if (d.kind !== "confirm") throw new Error("not a confirm");
    expect(d.options.title).toBe("Заменить?");
    expect(d.options.message).toContain("«Рудные пересечения»");
    expect(d.options.message).toContain("Истинная мощность «TT»");
    d.resolve(true);
    closeDialog(d.id);
    expect(await run).toEqual({ source: "core", id: 21 });
    expect(sent.map((r) => r.body.replace)).toEqual([false, true]);
  });

  it("«Отмена» starts nothing; another refusal is the core's error", async () => {
    exists = 1;
    const run = startRun({ set_id: 3 }, askReplace);
    await vi.waitFor(() => expect(dialogStore.get().queue).toHaveLength(1));
    const d = dialogStore.get().queue[0];
    d.resolve(false as never);
    closeDialog(d.id);
    expect(await run).toBeNull();
    expect(sent).toHaveLength(1);
    vi.stubGlobal("fetch", vi.fn(async () => json({ code: "name_is_source", message: "Имя результата совпадает с таблицей-источником.", hint: "Задайте другое имя.", param: "name" }, 422)));
    await expect(startRun({ set_id: 3 }, askReplace)).rejects.toBeInstanceOf(CoreError);
  });
});

describe("the window", () => {
  it("opens from the arguments: `fields` are the other grades, not the source field", () => {
    const init = runInitial({ set_id: 3, table: "Пробы", field: "AU", fields: "CU, AG", cutoff: 1, min_by: "tt", source: "global", alpha: 30, dipdir: 120 });
    expect(init.src).toMatchObject({ setId: 3, table: "Пробы", fields: ["AU"] });
    expect(init.grades).toEqual(["CU", "AG"]);
    expect(init.values).toMatchObject({ cutoff: "1", min_by: "tt", l_min: "2", waste_max: "2", missing: "waste" });
    expect(init.values).not.toHaveProperty("angles");
    expect(init.orient).toMatchObject({ enabled: true, source: "global", alpha: "30", dipdir: "120" });
    // without a source the orientation is off (TT empty, TT_SRC «нет»)
    expect(runInitial({ set_id: 3 }).orient.enabled).toBe(false);
    expect(runInitial({ source: "plane" }).note).toContain("Источник ориентировки");
    expect(gradesFromArgs(["CU"])).toEqual(["CU"]);
    expect(gradesFromArgs("")).toBeNull();
  });

  it("L_min is m_min by the true thickness; the grades offered leave out the grade, the density and TT", () => {
    expect(runParams({ min_by: "tt" }).find((p) => p.key === "l_min")?.label).toBe("Мин. истинная мощность m_min");
    expect(runParams({ min_by: "length" }).find((p) => p.key === "l_min")?.label).toBe("Мин. длина пересечения L_min");
    const f = (name: string, kind: "number" | "text" = "number") => ({ key: name, name, kind, unit: null });
    expect(gradeChoices([f("AU"), f("CU"), f("DENS"), f("TT"), f("HOLE_AZ"), f("ROCK", "text"), f("AG")], "AU", "DENS")).toEqual(["CU", "AG"]);
  });

  it("is drawn by the renderer of the shell", () => {
    const w = { id: "w1", kind: RUN_WINDOW, title: "Рудные пересечения", props: { args: { set_id: 3, field: "AU" } } } as unknown as FloatWin;
    const html = renderToStaticMarkup(renderInterceptsWindow(w) as React.ReactElement);
    expect(html).toContain('data-testid="intercepts-run"');
    expect(html).toContain("Поля содержаний сверх основного");
    expect(html).toContain("Считать истинную мощность");
    expect(html).toContain("Бортовое содержание c");
    expect(html).not.toContain("Записать углы");
    expect(renderInterceptsWindow({ ...w, kind: "other" } as FloatWin)).toBeNull();
  });
});
