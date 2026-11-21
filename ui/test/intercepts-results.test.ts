/**
 * Э15 15.17: the results `tt` and `intercepts` in «Обработка проб» (menu, «Параметры…» → the windows of
 * «Пересечения», «Свойства», «Перестроить» to `/intercepts`, «Удалить»), TT first in the thickness of Д-26, «Взять с
 * каркаса». The rows are in the form of `GET /composites/results` as core/tests/test_intercepts_results.py reads it.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ResultRow } from "../src/modules/composites/api";
import { bindComposites } from "../src/modules/composites/bind";
import { ResultFacts } from "../src/modules/composites/ResultProps";
import { loadSetResults, rebuildQuestion, resetResults, resultLabel, resultMenu } from "../src/modules/composites/results";
import { resultTitle, runResultAction } from "../src/modules/composites/ResultsTree";
import { meanPlane } from "../src/modules/intercepts/api";
import { RUN_WINDOW, TT_WINDOW } from "../src/modules/intercepts/bind";
import { runInitial } from "../src/modules/intercepts/InterceptsWindow";
import { OrientationForm } from "../src/modules/intercepts/OrientationForm";
import { DEFAULT_ORIENTATION } from "../src/modules/intercepts/params";
import { planeText } from "../src/modules/intercepts/PlanePicker";
import { interceptArgs, interceptMenu, interceptParamFacts, interceptReportFacts, orientationFacts, sourceText } from "../src/modules/intercepts/results";
import { interceptToolOfOp, isInterceptOp, ttFirst, ttResultOf, ttThicknessHint } from "../src/modules/intercepts/tools";
import { ttInitial } from "../src/modules/intercepts/TtWindow";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { AppContext } from "../src/shell/AppContext";
import { closeDialog, dialogStore } from "../src/shell/dialogs";
import { windowStore } from "../src/shell/windows";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

const GLOBAL = [{ source: "global", alpha: 30.0, dipdir: 120.0 }];
const INPUTS = { tables: [{ key: "пробы", name: "Пробы", token: "a", rev: 3, fields: [] }], set: { version: 7 }, meshes: [] };

/** `tt` as the core lists it: `written` — `[{table, columns}]`, `table` — the source with the fields. */
const TT: ResultRow = {
  id: 30,
  set_id: 3,
  table_id: 12,
  table: "Пробы",
  op: "tt",
  op_title: "Истинная мощность",
  makes_table: false,
  name: "Истинная мощность · Пробы",
  params: { set_id: 3, table: "12", orientation: GLOBAL, eps: 0.1, step: 1.0, angles: false },
  inputs: INPUTS,
  sha256: "1".repeat(64),
  algo: "tt-1",
  report: { written: [{ table: "пробы", columns: ["TT", "TT_FLAG", "TT_SRC"] }], counts: { src: { глобальная: 10, нет: 2 }, flags: { "1": 1, "2": 2, "4": 0, "8": 0 } }, rows: 12, sum_tt: null, seconds: 0.4 },
  written: [{ table: "пробы", columns: ["TT", "TT_FLAG", "TT_SRC"] }] as unknown as string[],
  job_id: 41,
  created_at: "2026-11-21T10:00:00",
  stale: false,
  reason: null,
};

/** `intercepts`: a new table; `params` — the canonical body of `/run` (`fields` [] — only the grade field). */
const RUN: ResultRow = {
  ...TT,
  id: 31,
  table_id: 14,
  table: "Рудные пересечения",
  op: "intercepts",
  op_title: "Рудные пересечения",
  makes_table: true,
  name: "Рудные пересечения",
  params: { set_id: 3, table: "12", field: "AU", cutoff: 1, include_cutoff: null, l_min: 2, min_by: "tt", waste_max: 2, missing: "waste", density: null, fields: [], orientation: GLOBAL, eps: 0.1, step: 1, name: "Рудные пересечения" },
  algo: "icpt-1",
  report: { table_columns: ["KIND", "LABEL"], intercepts: 4, len_g: 5.8 },
  written: [],
};

let app: App;
let list: ResultRow[] = [];
const sent: { method: string; path: string; body: unknown }[] = [];

beforeEach(() => {
  sent.length = 0;
  list = [TT, RUN];
  resetResults();
  windowStore.set({ windows: [] });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = new URL(url);
      const path = u.pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      sent.push({ method, path: `${path}${u.search}`, body: init?.body ? JSON.parse(String(init.body)) : null });
      if (path === "/composites/results") return json({ results: list });
      if (path.startsWith("/composites/results/") && method === "DELETE") return json({ deleted: 31, set_id: 3, deleted_tables: [14], changed: [], deleted_charts: [7] });
      if (path.endsWith("/rebuild")) return json({ source: "core", id: 50 });
      if (path === "/intercepts/orientation/plane") return json({ alpha: 32.5, dipdir: 118, spread: 6.2, triangles: 1204, area: 5000 });
      if (path === "/jobs") return json({ jobs: [] });
      return json({ code: "not_found", message: "Нет", hint: "" }, 404);
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  app = createApp({ persist: false });
  bindComposites(app);
  app.store.set({ projectName: "Проект", projectReadOnly: false, setName: "Набор" });
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
  for (const d of dialogStore.get().queue) closeDialog(d.id);
});

const answer = async (ok: boolean) => {
  await vi.waitFor(() => expect(dialogStore.get().queue).toHaveLength(1));
  const d = dialogStore.get().queue[0];
  if (d.kind !== "confirm") throw new Error("not a confirm");
  d.resolve(ok);
  closeDialog(d.id);
  return d.options;
};

describe("the rows of «Обработка проб»", () => {
  it("names, titles and the tool of the operation", () => {
    expect(resultLabel(TT)).toBe("Истинная мощность · Пробы");
    expect(resultTitle(TT)).toContain("Истинная мощность, поля в таблице «Пробы»");
    expect(resultTitle(RUN)).toContain("Рудные пересечения, таблица «Рудные пересечения»");
    expect(isInterceptOp("tt") && isInterceptOp("intercepts")).toBe(true);
    expect(isInterceptOp("ore") || isInterceptOp("constructor") || isInterceptOp(null)).toBe(false);
    expect(interceptToolOfOp("tt")?.command).toBe("intercept.tt");
    expect(interceptToolOfOp("intercepts")?.command).toBe("intercept.run");
  });

  it("the menu: «Параметры…» available (Э12 has no window for them), read-only — refused", () => {
    expect(resultMenu(TT, false).find((x) => x.action === "params")?.reason).toBe("Окна у этой операции нет");
    const menu = interceptMenu(TT, false);
    expect(menu.map((x) => [x.action, x.reason])).toEqual([
      ["open", null],
      ["params", null],
      ["rebuild", null],
      ["props", null],
      ["delete", null],
    ]);
    expect(menu[0].label).toBe("Открыть таблицу с полями");
    expect(interceptMenu(RUN, false)[0].label).toBe("Открыть таблицу");
    expect(interceptMenu(RUN, true).filter((x) => x.reason).map((x) => x.action)).toEqual(["params", "rebuild", "delete"]);
  });

  it("«Параметры…» opens «Истинная мощность» / «Рудные пересечения» with the values the core ran with", async () => {
    const tt = interceptArgs(TT);
    expect(tt).toEqual({ set_id: 3, table: "12", eps: 0.1, step: 1, angles: false, source: "global", alpha: 30, dipdir: 120 });
    const init = ttInitial(tt);
    expect(init.orient).toMatchObject({ source: "global", alpha: "30", dipdir: "120", enabled: true });
    expect(init.values).toMatchObject({ eps: "0,1", angles: false });
    const run = runInitial(interceptArgs(RUN));
    expect(run.grades).toEqual([]); // `fields: []` — only the grade field, not «all numeric»
    expect(run.values).toMatchObject({ cutoff: "1", min_by: "tt", missing: "waste", include_cutoff: "" });
    expect(run.src).toMatchObject({ setId: 3, table: "12", fields: ["AU"] });

    await runResultAction(app, TT, "params");
    await runResultAction(app, RUN, "params");
    const kinds = windowStore.get().windows.map((w) => w.kind);
    expect(kinds).toEqual([TT_WINDOW, RUN_WINDOW]);
    expect((windowStore.get().windows[1].props?.args as Record<string, unknown>).name).toBe("Рудные пересечения");
  });

  it("«Перестроить» goes to /intercepts (always asked); «Удалить» — the common route of Э12", async () => {
    await loadSetResults(3);
    expect(rebuildQuestion(TT)).toBe("Перестроить «Истинная мощность · Пробы»? Записанные поля будут пересчитаны по текущим данным.");
    const r = runResultAction(app, TT, "rebuild");
    expect((await answer(true)).okLabel).toBe("Перестроить");
    await r;
    const rb = runResultAction(app, RUN, "rebuild");
    await answer(true);
    await rb;
    expect(sent.filter((x) => x.method === "POST").map((x) => x.path)).toEqual(["/intercepts/results/30/rebuild", "/intercepts/results/31/rebuild"]);

    const del = runResultAction(app, RUN, "delete");
    expect((await answer(true)).danger).toBe(true);
    await del;
    expect(sent.filter((x) => x.method === "DELETE").map((x) => x.path)).toEqual(["/composites/results/31"]);
  });
});

describe("«Свойства»", () => {
  it("the parameters as the windows name them; the orientation in Russian; empty is «—», never 0", () => {
    const facts = Object.fromEntries(interceptParamFacts(RUN).map((f) => [f.label, f.value]));
    expect(facts).toMatchObject({
      Таблица: "Пробы",
      "Поле содержания": "AU",
      "Бортовое содержание c": "1",
      "Борт «в том числе» c2": "нет «в том числе»",
      "Мин. истинная мощность m_min, м": "2",
      "Критерий приёмки": "по истинной мощности (m_min)",
      Пропуски: "пустая порода (g = 0)",
      "Поле плотности": "без плотности",
      "Прочие содержания": "только поле содержания",
      Ориентировка: "Глобальная: α 30°, Aп 120°",
      "Порог вырождения ε_в": "0,1",
    });
    const labels = interceptParamFacts(TT).map((f) => f.label);
    expect(labels).toEqual(["Таблица", "Ориентировка", "Порог вырождения ε_в", "Часть ствола, м", "Записать углы ALPHA, DIPDIR, HOLE_THETA, HOLE_AZ"]);
    expect(orientationFacts([])).toEqual([{ label: "Ориентировка", value: "нет (TT пусто, TT_SRC «нет»)" }]);
    expect(orientationFacts([{ source: "domain", table: "12", field: "LITH", attitudes: [{ code: "QZ", alpha: 60, dipdir: 120 }] }, ...GLOBAL])).toEqual([
      { label: "Ориентировка", value: "По домену: поле «LITH»; кодов 1" },
      { label: "Коды домена, α/Aп", value: "QZ 60/120" },
      { label: "Если не найдена", value: "Глобальная: α 30°, Aп 120°" },
    ]);
    expect(sourceText({ source: "mesh", wireframe_ids: [5, 6], d_max: 50, r_n: 0 })).toBe("По каркасу: каркасы 5, 6; d_max 50 м, r_n 0 м");
    expect(sourceText({ source: "core", table: "14", alpha: "ALPHA_K", beta: "BETA_K", line: "toh", d_core: 10 })).toBe("Ориентированный керн: α_к «ALPHA_K», β_к «BETA_K», линия TOH, d_к 10 м");
    expect(sourceText({ source: "global", alpha: null, dipdir: 120 })).toBe("Глобальная: α —, Aп 120°");
  });

  it("the summary: rows by TT_SRC and by the bits; a null stays «—»", () => {
    expect(interceptReportFacts(TT)).toEqual([
      { label: "TT_SRC «глобальная»", value: "10" },
      { label: "TT_SRC «нет»", value: "2" },
      { label: "TT_FLAG 1 — вдоль тела", value: "1" },
      { label: "TT_FLAG 2 — нет ориентировки", value: "2" },
      { label: "TT_FLAG 4 — керн не пересчитан", value: "0" },
      { label: "TT_FLAG 8 — нет траектории", value: "0" },
      { label: "Строк", value: "12" },
      { label: "Σ TT, м", value: "—" },
    ]);
    expect(interceptReportFacts(RUN)).toEqual([
      { label: "Пересечений", value: "4" },
      { label: "Σ LEN_G", value: "5,8" },
    ]);
    const html = renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(ResultFacts, { r: { ...TT, stale: true, reason: "изменены устья или инклинометрия набора" } })));
    expect(html).toContain("Глобальная: α 30°, Aп 120°");
    expect(html).toContain("TT, TT_FLAG, TT_SRC");
    expect(html).toContain("устарел: изменены устья или инклинометрия набора");
    expect(html).not.toContain("source global");
  });
});

describe("Д-26: TT first in «Мощность пробы»", () => {
  const fields = [
    { key: "c1", name: "AU", kind: "number" as const, unit: "г/т" },
    { key: "c2", name: "LITH", kind: "text" as const, unit: null },
    { key: "c3", name: "TT", kind: "number" as const, unit: "м" },
  ];

  it("TT goes first, the rest keep their order; no TT — the same list", () => {
    expect(ttFirst(fields).map((f) => f.name)).toEqual(["TT", "AU", "LITH"]);
    expect(ttFirst(fields.slice(0, 2)).map((f) => f.name)).toEqual(["AU", "LITH"]);
    expect(ttFirst([{ name: "TT", kind: "text" }, ...fields.slice(0, 1)]).map((f) => f.name)).toEqual(["TT", "AU"]); // a text TT is not the thickness
  });

  it("the hint offers TT, says it is chosen, and says why it is out of date", () => {
    expect(ttThicknessHint(fields.slice(0, 2), "", null)).toBeNull();
    const offer = ttThicknessHint(fields, "", null);
    expect(offer).toMatchObject({ field: "TT", pick: true, stale: false });
    expect(offer?.text).toBe("В таблице есть поле TT — истинная мощность (ТЗ 8.5); можно взять его мощностью пробы.");
    const tt = ttResultOf([RUN, { ...TT, stale: true, reason: "изменена таблица «Пробы»" }], "12");
    expect(tt?.id).toBe(30);
    expect(ttResultOf([TT], "13")).toBeNull();
    const chosen = ttThicknessHint(fields, "TT", tt);
    expect(chosen).toMatchObject({ pick: false, stale: true });
    expect(chosen?.text).toBe("Мощность пробы — поле TT, истинная мощность (ТЗ 8.5). Результат «Истинная мощность · Пробы» устарел: изменена таблица «Пробы» — перестройте его в дереве («Обработка проб»).");
  });
});

describe("«Взять с каркаса»", () => {
  it("the mean plane of the wireframes; the block is the last of the global source", async () => {
    const p = await meanPlane([5, 6]);
    expect(sent.at(-1)).toMatchObject({ method: "POST", path: "/intercepts/orientation/plane", body: { wireframe_ids: [5, 6] } });
    expect(planeText(p)).toBe(`α 32,5°, Aп 118,0°; разброс нормалей 6,2°; треугольников ${(1204).toLocaleString("ru-RU")}; площадь ${(5000).toLocaleString("ru-RU")} м²`);
    expect(planeText({ ...p, spread: null, triangles: null, area: null })).toBe("α 32,5°, Aп 118,0°");
    const html = (source: "global" | "mesh") => renderToStaticMarkup(createElement(OrientationForm, { setId: null, table: null, fields: [], value: { ...DEFAULT_ORIENTATION, source }, errors: {}, onChange: () => undefined }));
    const g = html("global");
    expect(g).toContain("Взять с каркаса");
    expect(g.indexOf("Взять с каркаса")).toBeGreaterThan(g.indexOf("Азимут падения Aп"));
    expect(html("mesh")).not.toContain("Взять с каркаса");
  });
});
