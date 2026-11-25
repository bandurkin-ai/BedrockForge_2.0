// The source «тренд» of the orientation (ТЗ 8.5.2, вопрос № 57; ADR 0026 решение 27; Э19 19.26; core `intercepts/{spec_sources,orient}.py`): `{"source": "trend",
// "trend_id": N}` — a trend of the project of any kind (a global one gives the plane of its minor axis, a structural one the normal at the middle
// of the interval); TT_SRC «тренд» (`orient.SRC_LABELS`); the report of the source `{source: "trend", rows, trend, no_position, nearest}`.
// The block «Ориентировка» checks the choice before a request, the commands take `trend_id`, the form lists the trends of the project.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TT_SOURCES } from "../src/modules/intercepts/api";
import { commands } from "../src/modules/intercepts/commands";
import { OrientationForm } from "../src/modules/intercepts/OrientationForm";
import { bindIntercepts, interceptJobBody } from "../src/modules/intercepts/bind";
import { argsFromOrientation, checkOrientation, coreErrors, DEFAULT_ORIENTATION, formKey, ORIENT_SOURCES, orientationFromArgs, SOURCE_TITLES, TT_TOOL, type OrientationState } from "../src/modules/intercepts/params";
import { interceptReportFacts, sourceText, trendFacts } from "../src/modules/intercepts/results";
import { bindComposites } from "../src/modules/composites/bind";
import type { ResultRow } from "../src/modules/composites/api";
import { CoreError, setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { windowStore } from "../src/shell/windows";

const st = (patch: Partial<OrientationState>): OrientationState => ({ ...DEFAULT_ORIENTATION, ...patch });
const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

describe("TT_SRC", () => {
  it("«тренд» stands before «нет» (the eight labels of `orient.SRC_LABELS`)", () => {
    expect(TT_SOURCES).toEqual(["глобальная", "домен", "каркас: пересечение", "каркас: ближайшая", "керн: замер", "керн: ближайший", "тренд", "нет"]);
    expect(TT_SOURCES.indexOf("тренд")).toBe(TT_SOURCES.indexOf("нет") - 1);
    // the same texts as the core's `SRC_LABELS = ("нет", "глобальная", "домен", "каркас: пересечение", "каркас: ближайшая", "керн: замер", "керн: ближайший", "тренд")`
    expect([...TT_SOURCES].sort()).toEqual(["нет", "глобальная", "домен", "каркас: пересечение", "каркас: ближайшая", "керн: замер", "керн: ближайший", "тренд"].sort());
  });
});

describe("the block «Ориентировка» with a trend", () => {
  it("the fifth source: the title, the list of the sources", () => {
    expect(ORIENT_SOURCES).toEqual(["global", "domain", "mesh", "core", "trend"]);
    expect(SOURCE_TITLES.trend).toBe("По тренду");
  });

  it("a trend chosen → `{source: \"trend\", trend_id}`; none chosen — marked, nothing sent; «если не найдена» goes after", () => {
    expect(checkOrientation(st({ source: "trend", trendId: 7 })).orientation).toEqual([{ source: "trend", trend_id: 7 }]);
    const none = checkOrientation(st({ source: "trend" }));
    expect(none.orientation).toBeNull();
    expect(none.errors.trend).toBe("Выберите тренд из списка трендов проекта");
    expect(none.reason).toBe(none.errors.trend);
    expect(checkOrientation(st({ source: "trend", trendId: 7, fallback: true, fallbackAlpha: "30", fallbackDipdir: "120" })).orientation).toEqual([{ source: "trend", trend_id: 7 }, { source: "global", alpha: 30, dipdir: 120 }]);
    expect(checkOrientation(st({ enabled: false })).orientation).toEqual([]);
  });

  it("the arguments of the commands and back («Параметры…»): `source = trend`, `trend_id`", () => {
    const o = orientationFromArgs({ source: "trend", trend_id: 7 }) as OrientationState;
    expect(o).toMatchObject({ source: "trend", trendId: 7 });
    expect(orientationFromArgs({ source: "trend", trend_id: "7" })).toMatchObject({ trendId: 7 });
    expect(orientationFromArgs({ source: "trend" })).toMatchObject({ source: "trend", trendId: null }); // the window opens; the form says «выберите»
    for (const bad of ["x", "0", "-3", "2,5"]) expect(orientationFromArgs({ source: "trend", trend_id: bad }), bad).toContain("trend_id");
    expect(orientationFromArgs({ source: "plane" })).toContain("Источник ориентировки");
    expect(checkOrientation(o).orientation).toEqual([{ source: "trend", trend_id: 7 }]);
    expect(argsFromOrientation([{ source: "trend", trend_id: 7, trend: "Кровля" }])).toEqual({ source: "trend", trend_id: 7 });
    expect(argsFromOrientation([{ source: "trend", trend_id: 7 }, { source: "global", alpha: 10, dipdir: 20 }])).toEqual({ source: "trend", trend_id: 7, fallback_alpha: 10, fallback_dipdir: 20 });
    expect(orientationFromArgs(argsFromOrientation([{ source: "trend", trend_id: 7 }]))).toMatchObject({ source: "trend", trendId: 7 });
  });

  it("a refusal of the core marks the field of the form: `orientation.0.trend_id` → `trend` (404 `trend_not_found`, 422 `no_orientation`)", () => {
    expect(formKey("orientation.0.trend_id", st({ source: "trend", trendId: 9 }))).toBe("trend");
    const e = new CoreError(422, "trend_not_found", "Тренд №9 не найден в проекте.", "Обновите список трендов и выберите тренд снова.");
    e.body = { param: "orientation.0.trend_id", message: "Тренд №9 не найден в проекте." };
    expect(coreErrors(e, st({ source: "trend", trendId: 9 }))).toEqual({ trend: "Тренд №9 не найден в проекте." });
  });

  it("the form: the fifth radio «По тренду», the list of the trends (global and structural), the note of what each gives", () => {
    const html = renderToStaticMarkup(createElement(OrientationForm, { setId: null, table: null, fields: [], value: st({ source: "trend", trendId: 9 }), errors: { trend: "Выберите тренд из списка трендов проекта" }, onChange: () => undefined }));
    for (const text of ["Глобальная", "По домену", "По каркасу", "Ориентированный керн", "По тренду", "плоскость, перпендикулярную малой оси a3", "структурный — плоскость замера или каркаса", "Выберите тренд из списка трендов проекта"]) expect(html, text).toContain(text);
    expect(html).not.toContain("Взять с каркаса");
    const other = renderToStaticMarkup(createElement(OrientationForm, { setId: null, table: null, fields: [], value: st({ source: "mesh" }), errors: {}, onChange: () => undefined }));
    expect(other).not.toContain("плоскость, перпендикулярную малой оси a3");
  });
});

describe("the commands", () => {
  it("`intercept.tt`, `intercept.run` name the source «trend» and `trend_id`", () => {
    for (const c of commands) {
      expect(c.params!.source.enum, c.id).toEqual(["global", "domain", "mesh", "core", "trend"]);
      expect(c.params!.trend_id, c.id).toMatchObject({ type: "integer" });
      expect(c.params!.source.description).toMatch(/trend — тренд/);
    }
  });
});

let app: App;
const sent: { method: string; path: string; body: Record<string, unknown> }[] = [];

beforeEach(() => {
  sent.length = 0;
  windowStore.set({ windows: [] });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url).pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      sent.push({ method, path, body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {} });
      if (path === "/drillholes/sets/3/tables") return json({ tables: [{ table: "5", kind: "assay", name: "Пробы", rows: 10, rev: 4 }] });
      if (path === "/jobs") return json({ jobs: [] });
      if (path === "/composites/results") return json({ results: [] });
      if (path === "/geostat/trends") return json({ trends: [{ id: 7, name: "Кровля", kind: "structural" }, { id: 8, name: "Общий", kind: "global" }] });
      if (method === "POST") return json({ source: "core", id: 51 });
      return json({ code: "not_found", message: "Нет", hint: "" }, 404);
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  app = createApp({ persist: false });
  bindComposites(app);
  bindIntercepts(app);
  app.store.set({ projectName: "Проект", projectReadOnly: false, setName: "Набор" });
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
});

describe("the job of «Истинная мощность» by a trend", () => {
  it("the body of `POST /intercepts/tt`: `orientation` [{source: trend, trend_id}] (+ the global one after it)", async () => {
    const body = await interceptJobBody(TT_TOOL, { set_id: 3, table: "Пробы", source: "trend", trend_id: 7 });
    expect(body).toEqual({ set_id: 3, table: "5", eps: 0.1, step: 1, angles: true, orientation: [{ source: "trend", trend_id: 7 }] });
    const fb = await interceptJobBody(TT_TOOL, { set_id: 3, table: "Пробы", source: "trend", trend_id: 8, fallback_alpha: 10, fallback_dipdir: 20 });
    expect(fb.orientation).toEqual([{ source: "trend", trend_id: 8 }, { source: "global", alpha: 10, dipdir: 20 }]);
  });

  it("through the registry: the job starts; no trend chosen — refused before any request", async () => {
    const ok = await app.registry.execute("intercept.tt", { set_id: 3, table: "Пробы", source: "trend", trend_id: 7 }, "user");
    expect(ok.ok, ok.result).toBe(true);
    const post = sent.find((r) => r.method === "POST" && r.path === "/intercepts/tt")!;
    expect(post.body.orientation).toEqual([{ source: "trend", trend_id: 7 }]);
    sent.length = 0;
    const none = await app.registry.execute("intercept.tt", { set_id: 3, table: "Пробы", source: "trend" }, "user");
    expect(none.ok).toBe(false);
    expect(sent.filter((r) => r.method === "POST")).toEqual([]);
  });
});

describe("the result with the source «тренд»", () => {
  const TT: ResultRow = {
    id: 30,
    set_id: 3,
    table_id: 12,
    table: "Пробы",
    op: "tt",
    op_title: "Истинная мощность",
    makes_table: false,
    name: "Истинная мощность · Пробы",
    params: { set_id: 3, table: "12", orientation: [{ source: "trend", trend_id: 7, trend: "Кровля" }], eps: 0.1, step: 1.0, angles: false },
    inputs: { tables: [], set: { version: 7 }, meshes: [] },
    sha256: "1".repeat(64),
    algo: "tt-1",
    report: { counts: { src: { тренд: 120, нет: 8 }, flags: { "1": 0, "2": 8, "4": 0, "8": 3 } }, rows: 128, sources: [{ source: "trend", rows: 120, trend: "Кровля", no_position: 3, nearest: 5 }], seconds: 0.4 },
    written: [],
    job_id: 41,
    created_at: "2026-11-25T10:00:00",
    stale: false,
    reason: null,
  };

  it("the properties name the trend; the summary counts TT_SRC «тренд» and the report of the source: the rows, the trend, no position, the nearest measurement", () => {
    expect(sourceText({ source: "trend", trend_id: 7, trend: "Кровля" })).toBe("По тренду: тренд «Кровля» (№7)");
    expect(sourceText({ source: "trend", trend_id: 7 })).toBe("По тренду: тренд — (№7)");
    const facts = interceptReportFacts(TT);
    expect(facts).toContainEqual({ label: "TT_SRC «тренд»", value: "120" });
    expect(facts).toContainEqual({ label: "Строк по источнику «По тренду»", value: "120" });
    expect(facts).toContainEqual({ label: "Источник «тренд»: тренд", value: "Кровля" });
    expect(facts).toContainEqual({ label: "Источник «тренд»: строк без положения", value: "3" });
    expect(facts).toContainEqual({ label: "Источник «тренд»: взят ближайший замер (нет замеров в R_тр)", value: "5" });
    expect(trendFacts({ source: "trend", rows: 4 })).toEqual([]);
    expect(trendFacts({ source: "trend", no_position: 0, nearest: 0, trend: "" })).toEqual([
      { label: "Источник «тренд»: строк без положения", value: "0" },
      { label: "Источник «тренд»: взят ближайший замер (нет замеров в R_тр)", value: "0" },
    ]);
  });
});
