// The window «Истинная мощность» (ТЗ 8.5.4; ADR 0021 §11; Э15 15.15): the answer of `POST /intercepts/tt/preview`
// made safe (empty stays empty — never zero; TT / LENGTH computed when not given; counts by TT_SRC and by the bits of
// TT_FLAG), the flags are visible in the rows and the counts, the column marks an interval without TT as empty and
// «вдоль тела» by its edge, the preview of a big mesh is reported, the window opens from the command arguments and
// sends the body of `interceptBody`. The core is a fetch stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flagText, startTt, toTtPreview, ttPreview } from "../src/modules/intercepts/api";
import { TT_WINDOW } from "../src/modules/intercepts/bind";
import { countRows, fmt, ratioFill, TtPreview } from "../src/modules/intercepts/TtPreview";
import { renderInterceptsWindow, ttInitial } from "../src/modules/intercepts/TtWindow";
import { setCoreTarget } from "../src/shell/api";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

// Т8.5-1 (α = 30°, vertical hole: TT = 0,8660254·l), Т8.5-3 (TT = 0, bit 1), no orientation (bit 2, TT empty)
const RAW = {
  hole: "С-1",
  intervals: {
    from: [0, 1, 3, 4],
    to: [1, 3, 4, 6],
    values: {
      TT: [0.8660254, 1.7320508, 0, null],
      TT_FLAG: [0, 0, 1, 2],
      TT_SRC: ["глобальная", "домен", "глобальная", "нет"],
      ALPHA: [30, 30, 30, null],
      DIPDIR: [90, 90, 90, null],
      HOLE_THETA: [0, 0, 60, 0],
      HOLE_AZ: [0, 0, 90, 0],
    },
  },
};

const sent: { path: string; body: Record<string, unknown> }[] = [];

beforeEach(() => {
  sent.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url).pathname.replace("/api/v1", "");
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      sent.push({ path, body });
      if (path === "/intercepts/tt/preview") return json(RAW);
      if (path === "/intercepts/tt") return json({ code: "bad_param", message: "Значение вне диапазона.", hint: "ε_в — 0,01–0,5.", param: "eps" }, 422);
      return json({ code: "not_found", message: "Нет", hint: "" }, 404);
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
});

describe("the answer of the preview", () => {
  it("empty stays empty, TT / LENGTH is computed, the counts come from the rows", () => {
    const a = toTtPreview(RAW);
    expect(a.hole).toBe("С-1");
    expect(a.rows).toHaveLength(4);
    expect(a.rows[0].length).toBe(1);
    expect(a.rows[0].ratio).toBeCloseTo(0.8660254, 12);
    expect(a.rows[2]).toMatchObject({ tt: 0, ratio: 0, flag: 1, theta: 60 });
    expect(a.rows[3]).toMatchObject({ tt: null, ratio: null, alpha: null, dipdir: null, flag: 2, src: "нет" });
    expect(a.bySrc).toEqual({ глобальная: 2, домен: 1, нет: 1 });
    expect(a.byFlag).toEqual({ 1: 1, 2: 1 });
    expect(a.meshSkipped).toBe(false);
  });

  it("the counts of the core win; a part the core leaves out is empty", () => {
    const a = toTtPreview({ ...RAW, counts: { src: { глобальная: 120 }, flags: { "2": 7 } }, mesh_skipped: true, notes: ["пропущено 1"] });
    expect(a.bySrc).toEqual({ глобальная: 120 });
    expect(a.byFlag).toEqual({ 2: 7 });
    expect(a.meshSkipped).toBe(true);
    expect(a.notes).toEqual(["пропущено 1"]);
    const e = toTtPreview({});
    expect(e).toEqual({ hole: "", rows: [], bySrc: {}, byFlag: {}, meshSkipped: false, notes: [] });
    const bad = toTtPreview({ intervals: { from: [0, null], to: [1, 2], values: { TT: ["x"], LENGTH: [0] } } });
    expect(bad.rows).toHaveLength(1);
    expect(bad.rows[0]).toMatchObject({ tt: null, length: 0, ratio: null, flag: 0, src: "" });
  });

  it("the bits of TT_FLAG in words; 0 and empty — nothing", () => {
    expect(flagText(1)).toBe("вдоль тела");
    expect(flagText(2 + 8)).toBe("нет ориентировки, нет траектории");
    expect(flagText(4)).toBe("керн не пересчитан");
    expect(flagText(0)).toBe("");
    expect(flagText(null)).toBe("");
  });

  it("numbers in Russian; empty — «—», never «0»", () => {
    expect(fmt(0.8660254)).toBe("0,87");
    expect(fmt(0)).toBe("0,00");
    expect(fmt(null)).toBe("—");
    expect(fmt(Number.NaN)).toBe("—");
    expect(ratioFill(null)).toBeNull();
    expect(ratioFill(1)).toContain("100%");
    expect(ratioFill(0)).toContain("8%");
  });

  it("`POST /intercepts/tt/preview {params, hole_id}`", async () => {
    const a = await ttPreview({ set_id: 3, table: "5" }, 17);
    expect(sent).toEqual([{ path: "/intercepts/tt/preview", body: { params: { set_id: 3, table: "5" }, hole_id: 17 } }]);
    expect(a.rows).toHaveLength(4);
  });

  it("422 `bad_param` of the job is the core's Russian error", async () => {
    await expect(startTt({ set_id: 3 }, async () => "cancel")).rejects.toMatchObject({ status: 422, code: "bad_param" });
  });
});

describe("the preview", () => {
  const html = renderToStaticMarkup(createElement(TtPreview, { answer: toTtPreview(RAW) }));

  it("shows the flags in the rows and the counts", () => {
    expect(html).toContain("вдоль тела");
    expect(html).toContain("нет ориентировки");
    expect(html).toContain('data-flag="1"');
    expect(html).toContain('data-testid="intercepts-tt-counts"');
    expect(html).toMatch(/глобальная<b>2<\/b>/);
    expect(html).toMatch(/нет ориентировки<b>1<\/b>/);
  });

  it("an interval without TT is empty, not zero", () => {
    const row = /<tr><td>4,00<\/td><td>6,00<\/td>(.*?)<\/tr>/.exec(html)?.[1] ?? "";
    expect(row).toContain("<td>2,00</td><td>—</td><td>—</td><td>—</td><td>—</td>");
    expect(row).not.toContain("0,00");
    const rects = html.match(/<rect[^>]*>/g) ?? [];
    expect(rects).toHaveLength(4);
    expect(rects[3]).toContain("_empty");
    expect(rects[3]).not.toContain("color-mix");
    expect(rects[2]).toContain("_along");
    expect(rects[2]).toContain("color-mix");
  });

  it("a big mesh is reported; nothing to show — no counts", () => {
    const big = renderToStaticMarkup(createElement(TtPreview, { answer: toTtPreview({ hole: "С-2", mesh_skipped: true }) }));
    expect(big).toContain("больше 2 млн треугольников");
    expect(big).not.toContain("intercepts-tt-counts");
    expect(big).not.toContain("<svg");
  });

  it("the counts keep the order of the core's TT_SRC and add an unknown one last", () => {
    expect(countRows({ bySrc: { нет: 1, "каркас: ближайшая": 2, другое: 3, домен: 0 }, byFlag: { 8: 2, 1: 1 } })).toEqual({
      src: [
        ["каркас: ближайшая", 2],
        ["нет", 1],
        ["другое", 3],
      ],
      flags: [
        ["вдоль тела", 1, "Скважина почти параллельна телу: TT / LENGTH меньше ε_в"],
        ["нет траектории", 2, "У скважины нет траектории: TT пусто"],
      ],
    });
  });
});

describe("the window", () => {
  it("opens from the arguments of the command with the block and the parameters of 8.5.4", () => {
    const init = ttInitial({ set_id: 3, table: "Пробы", source: "mesh", wireframe_ids: "4", eps: 0.2, fallback_alpha: 30, fallback_dipdir: 90 });
    expect(init.src).toMatchObject({ setId: 3, table: "Пробы" });
    expect(init.values).toMatchObject({ eps: "0,2", step: "1", angles: true });
    expect(init.orient).toMatchObject({ source: "mesh", meshWireframes: [4], fallback: true, fallbackAlpha: "30" });
    expect(ttInitial({ source: "trend" }).note).toContain("Источник ориентировки");
    const out = renderInterceptsWindow({ id: "w1", kind: TT_WINDOW, title: "Истинная мощность", props: { args: { set_id: 3, source: "mesh", d_max: 2000 } } } as never);
    const page = renderToStaticMarkup(createElement("div", null, out));
    for (const t of ["Ориентировка", "По каркасу", "Порог вырождения ε_в", "Часть ствола", "Предпросмотр", "Применить", "Если не найдена — глобальная α, Aп"]) expect(page, t).toContain(t);
    expect(page).toContain("допустимо 0–1000 м"); // d_max out of range is marked
    expect(page).toMatch(/data-testid="composites-apply"[^>]*disabled=""|disabled=""[^>]*data-testid="composites-apply"/);
    expect(renderInterceptsWindow({ id: "w2", kind: "composites-ore", title: "" } as never)).toBeNull();
  });
});
