/**
 * Э15 15.20, the review of the interface: «Заполнить кодами поля» from `GET /intercepts/codes` (every code, not the
 * 10 most frequent; «показаны 1 000 из N»), the warning of a skipped mesh said once (the core's own note), the error of
 * a list read cleared, «Перестроить» of a result whose operation is not known (another project, a set not read),
 * the text of the mean plane, the radio buttons of «Поля содержаний» in one group.
 */
import { createElement } from "react";
import type React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadResults, rebuildResult, type ResultRow } from "../src/modules/composites/api";
import { loadSetResults, resetResults } from "../src/modules/composites/results";
import { codesNote, loadCodes, toTtPreview } from "../src/modules/intercepts/api";
import { RUN_WINDOW } from "../src/modules/intercepts/bind";
import { setListError, startRead } from "../src/modules/intercepts/OrientationForm";
import { planeText } from "../src/modules/intercepts/PlanePicker";
import { TtPreview } from "../src/modules/intercepts/TtPreview";
import { renderInterceptsWindow } from "../src/modules/intercepts/TtWindow";
import { CoreError, setCoreTarget } from "../src/shell/api";
import type { FloatWin } from "../src/shell/windows";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const n = (x: number) => x.toLocaleString("ru-RU");

let sent: { method: string; path: string }[] = [];
let results: Partial<ResultRow>[] = [];
let codes: Record<string, unknown> = {};

beforeEach(() => {
  sent = [];
  results = [];
  resetResults();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = new URL(url);
      const path = u.pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      sent.push({ method, path: `${path}${u.search}` });
      if (path === "/intercepts/codes") return json(codes);
      if (path === "/composites/results") return json({ results });
      // a result of `tt` asked under /composites: the job kind is not of that module
      if (path.startsWith("/composites/results/") && path.endsWith("/rebuild")) return json({ code: "not_ready", message: "Расчёт ещё не подключён в этой сборке.", hint: "Обновите программу." }, 422);
      if (path.startsWith("/intercepts/results/") && path.endsWith("/rebuild")) return json({ source: "core", id: 50 });
      return json({ code: "not_found", message: "Нет", hint: "" }, 404);
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
});

describe("№ 1 «Заполнить кодами поля»", () => {
  it("reads every code from /intercepts/codes and says when they were cut", async () => {
    codes = { codes: ["Д01", "Д02", "", 3, "Д10"], total: 1500, truncated: true };
    const got = await loadCodes(3, "12", "c5");
    expect(sent.at(-1)).toEqual({ method: "GET", path: "/intercepts/codes?set_id=3&table=12&column=c5" });
    expect(got).toEqual({ codes: ["Д01", "Д02", "Д10"], total: 1500, truncated: true });
    expect(codesNote({ codes: Array.from({ length: 1000 }, (_, i) => String(i)), total: 1500, truncated: true })).toBe(`Кодов в поле: ${n(1500)}, показаны ${n(1000)} из ${n(1500)}; α и Aп новых строк — введите`);
    expect(codesNote({ codes: ["a", "b"], total: 2, truncated: false })).toBe("Кодов в поле: 2; α и Aп новых строк — введите");
    expect(codesNote({ codes: [], total: 0, truncated: false })).toBe("В поле нет кодов");
  });

  it("an answer without total or truncated — the codes given, not cut", async () => {
    codes = { codes: Array.from({ length: 12 }, (_, i) => `К${i}`) };
    expect(await loadCodes(3, "12", "c5")).toMatchObject({ total: 12, truncated: false });
  });
});

describe("№ 2 a skipped mesh", () => {
  const MESH_SKIPPED = "Ориентировка по каркасу в предпросмотре не считается: в каркасах больше 2 млн треугольников. В расчёте она будет учтена.";

  it("the core's note is said once, as the warning; other notes stay", () => {
    const raw = { hole: "С-2", mesh_skipped: true, rows: { from: [], to: [] }, notes: [MESH_SKIPPED, "пропущено 1"] };
    const html = renderToStaticMarkup(createElement(TtPreview, { answer: toTtPreview(raw) }));
    expect(html.split("2 млн треугольников")).toHaveLength(2);
    expect(html).toContain("В расчёте она будет учтена.");
    expect(html).toContain("пропущено 1");
    expect(html).toContain('data-testid="intercepts-tt-mesh-skipped"');
  });

  it("without the note the warning is the window's own, once", () => {
    const html = renderToStaticMarkup(createElement(TtPreview, { answer: toTtPreview({ hole: "С-2", mesh_skipped: true }) }));
    expect(html.split("2 млн треугольников")).toHaveLength(2);
  });
});

describe("№ 3 the error of a list read", () => {
  it("is cleared by a new read and by a read that succeeds; a stale answer is dropped", async () => {
    let errs: Record<string, string> = { tables: "Таблица не найдена." };
    const fail = (e: unknown) => (errs = setListError(errs, "tables", e));
    const values: unknown[] = [];
    await startRead(async () => ["Пробы"], () => true, (v) => values.push(v), fail);
    expect(errs).toEqual({});
    expect(values).toEqual([null, ["Пробы"]]);

    await startRead(async () => Promise.reject(new CoreError(404, "set_not_found", "Набор скважин не найден.", "Обновите дерево.")), () => true, () => undefined, fail);
    expect(errs.tables).toContain("Набор скважин не найден.");
    await startRead(null, () => true, () => undefined, fail); // the list is not needed any more
    expect(errs).toEqual({});

    await startRead(async () => Promise.reject(new Error("старое")), () => false, () => undefined, fail);
    expect(errs).toEqual({});
  });

  it("clears only its own list", () => {
    const m = { tables: "a", core: "b" };
    expect(setListError(m, "tables", null)).toEqual({ core: "b" });
    expect(setListError(m, "domain", null)).toBe(m);
  });
});

describe("№ 4 «Перестроить»", () => {
  const TT = { id: 30, set_id: 3, op: "tt", name: "Истинная мощность · Пробы" } as Partial<ResultRow>;

  it("a result the tree holds goes to the module of its operation", async () => {
    results = [TT];
    await loadSetResults(3);
    expect(await rebuildResult(30, async () => true)).toEqual({ source: "core", id: 50 });
    expect(sent.filter((x) => x.method === "POST").map((x) => x.path)).toEqual(["/intercepts/results/30/rebuild"]);
  });

  it("another project forgets the operations read; not known and `/composites` refuses `not_ready` — /intercepts", async () => {
    results = [{ ...TT, op: "ore" }];
    await loadResults(3); // the op of 30 in the old project — «ore»
    resetResults(); // another project: id 30 there is a result of `tt`, its set not read
    expect(await rebuildResult(30, async () => true)).toEqual({ source: "core", id: 50 });
    expect(sent.filter((x) => x.method === "POST").map((x) => x.path)).toEqual(["/composites/results/30/rebuild", "/intercepts/results/30/rebuild"]);
  });

  it("a known operation of /composites keeps its refusal", async () => {
    await expect(rebuildResult(30, async () => true, "ore")).rejects.toMatchObject({ status: 422, code: "not_ready" });
    expect(sent.filter((x) => x.method === "POST")).toHaveLength(1);
  });
});

describe("№ 5, № 6", () => {
  it("the text of the mean plane — one decimal, with the area", () => {
    expect(planeText({ alpha: 32.123456, dipdir: 118, spread: 6.24, triangles: 1204, area: 5000.25 })).toBe(`α 32,1°, Aп 118,0°; разброс нормалей 6,2°; треугольников ${n(1204)}; площадь ${(5000.25).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} м²`);
    expect(planeText({ alpha: 32.5, dipdir: 118, spread: null, triangles: null, area: null })).toBe("α 32,5°, Aп 118,0°");
  });

  it("the radio buttons of «Поля содержаний сверх основного» are one group", () => {
    const w = { id: "w1", kind: RUN_WINDOW, title: "Рудные пересечения", props: { args: { set_id: 3, field: "AU" } } } as unknown as FloatWin;
    const html = renderToStaticMarkup(renderInterceptsWindow(w) as React.ReactElement);
    const start = html.indexOf('data-testid="intercepts-run-grades"');
    const block = html.slice(start, html.indexOf("</fieldset>", start));
    const names = [...block.matchAll(/type="radio" name="([^"]+)"/g)].map((m) => m[1]);
    expect(names).toHaveLength(2);
    expect(names[0]).toBe(names[1]);
  });
});
