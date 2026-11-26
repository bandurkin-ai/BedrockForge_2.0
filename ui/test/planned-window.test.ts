// The window «Проектная скважина» and the section of the tree (Э19б, 19.39; ТЗ 7.35 Д-34 ч. 2) on the REAL answers of the core: the tabs «Параметры» (the
// three modes, the words under a field), «Прогноз» (the intervals, «вне тел», the nearest holes), «План / факт» (the facts, the chart d(depth), the
// table by pages), the footer (the reasons of the buttons), the conflict, the pick of a point on the scene, the section of the tree and its menu.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../src/shell/AppContext";
import type { App } from "../src/shell/app";
import { menuStore, openMenu } from "../src/shell/ContextMenu";
import { PlannedChart } from "../src/modules/drillholes/PlannedChart";
import { PlannedWindow } from "../src/modules/drillholes/PlannedWindow";
import { PlannedTree, plannedMenu } from "../src/modules/drillholes/PlannedTree";
import { readCompare } from "../src/modules/drillholes/plannedApi";
import { heightOf, onPlane, pickStore, startPick, stopPick } from "../src/modules/drillholes/plannedPick";
import { plannedStore, plannedViewStore, plannedWin, setForm, setTab, setXyz } from "../src/modules/drillholes/plannedState";
import { setCurrentScene } from "../src/modules/scene/sceneHost";
import type { Scene } from "../src/viz/Scene";
import { AIM, FIRST, installCore, MODEL, newApp, R, SET, teardown, waitFor, type Core } from "./plannedHarness";

let app: App;
let core: Core;
beforeEach(async () => {
  core = installCore();
  app = newApp();
  await waitFor(() => plannedStore.get().loaded);
});
afterEach(() => {
  stopPick();
  setCurrentScene(null);
  teardown();
});

const flat = (s: string): string => s.replace(/[\u00a0\u202f]/g, " ").replace(/\u2212/g, "-");
const run = (id: string, args: Record<string, unknown> = {}) => app.registry.execute(id, args, "user");
const render = (el: ReturnType<typeof createElement>) => flat(renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el)));
const windowHtml = () => render(createElement(PlannedWindow, { winId: "w1" }));
const treeHtml = () => render(createElement(PlannedTree));
/** The opening tag of the element with the test id (React writes `disabled=""` before `data-testid`: the order of the props). */
const tag = (html: string, testid: string): string => new RegExp(`<[a-z]+ [^>]*data-testid="${testid}"[^>]*>`).exec(html)?.[0] ?? "";
const isDisabled = (html: string, testid: string): boolean => / disabled=""/.test(tag(html, testid));
const value = (html: string, testid: string): string | null => new RegExp(`data-testid="${testid}"[^>]*?value="([^"]*)"|value="([^"]*)"[^>]*?data-testid="${testid}"`).exec(html)?.slice(1).find((v) => v !== undefined) ?? null;

describe("the tab «Параметры»", () => {
  it("a hole that turns: the collar, the direction, the change per 100 m", async () => {
    await run("planned.open", { id: FIRST.id });
    const html = windowHtml();
    expect(html).toContain("ПС-1");
    expect(html).toContain(`№${FIRST.id}, версия 1`);
    expect(value(html, "pl-collar-x")).toBe("210");
    expect(value(html, "pl-collar-z")).toBe("330");
    expect(value(html, "pl-azimuth")).toBe("90");
    expect(value(html, "pl-dip")).toBe("-70");
    expect(value(html, "pl-length")).toBe("100");
    expect(value(html, "pl-change-azimuth")).toBe("1,5");
    expect(html).toContain('data-mode="change"');
    expect(html).toContain("Вниз — отрицательный");
    expect(html).not.toContain("pl-target-x"); // no target in this mode
    for (const t of ["Прямая", "С изменением на 100 м", "Через цель"]) expect(html).toContain(t);
    for (const m of ["Минимальная кривизна", "Касательный", "Сбалансированный касательный", "Средний угол"]) expect(html).toContain(m);
  });

  it("a hole through a target: the target, «до цели» instead of a length, what the core solved", async () => {
    await run("planned.open", { id: AIM.id });
    const html = windowHtml();
    expect(html).toContain('data-mode="target"');
    expect(value(html, "pl-target-x")).toBe("110");
    expect(value(html, "pl-target-z")).toBe("270");
    expect(html).toContain('placeholder="до цели"');
    expect(html).not.toContain('data-testid="pl-azimuth"'); // the azimuth and the dip are the core's here
    expect(html).toContain("По устью и цели ядро нашло азимут 51,3° и угол -43,1°");
    setForm({ length: "200" });
    expect(windowHtml()).not.toContain("pl-solved"); // the solved angles are of the stored target: not shown for a form being changed
  });

  it("a new hole: the next free name, the dip −90 down, the buttons are written for a person", async () => {
    await run("planned.new");
    const html = windowHtml();
    expect(html).toContain("ПС-2");
    expect(html).toContain("не сохранена");
    expect(value(html, "pl-dip")).toBe("-90");
    expect(html).toContain("Указать на сцене");
    expect(html).toContain("Сохранить как новую");
    expect(isDisabled(html, "pl-save-new")).toBe(true);
  });

  it("the words of a refusal stand under their field; the red ring is on the field", async () => {
    await run("planned.new", { x: 1, y: 2, z: 3 });
    setForm({ azimuth: "360", length: "50" });
    await run("planned.save");
    const html = windowHtml();
    expect(html).toContain('data-testid="pl-issue-azimuth"');
    expect(html).toContain("Азимут: нужно число от 0 до 360");
    expect(html).toMatch(/aria-invalid="true"[^>]*data-testid="pl-azimuth"|data-testid="pl-azimuth"[^>]*aria-invalid="true"/);
    expect(html).not.toContain('data-testid="pl-issue-dip"');
    setForm({ azimuth: "10" });
    expect(windowHtml()).not.toContain("pl-issue-azimuth");
  });

  it("the refusals of the core: the target is beyond the length (with what is needed), a name taken", async () => {
    await run("planned.new", { x: 0, y: 0, z: 0 });
    setForm({ mode: "target", target: ["300", "0", "0"], length: "100" });
    core.force("POST /planned_holes", R.planned_target_far);
    await run("planned.save");
    const html = windowHtml();
    expect(html).toContain('data-testid="pl-issue-length"');
    expect(html).toContain("Увеличьте длину не менее чем до 300 м");
  });

  it("a conflict of versions has «Перечитать»; a hole deleted elsewhere says so", async () => {
    await run("planned.open", { id: FIRST.id });
    plannedWin.set({ version: 4 });
    setForm({ length: "110" });
    await run("planned.save");
    const html = windowHtml();
    expect(html).toContain('data-testid="pl-conflict"');
    expect(html).toContain("Перечитать");
    expect(html).toContain("изменена в другом окне");
    expect(html).toContain("есть несохранённые изменения");
  });

  it("a project for viewing only: the fields and «Сохранить» are locked with the reason", async () => {
    await run("planned.open", { id: FIRST.id });
    app.store.set({ projectReadOnly: true });
    const html = windowHtml();
    expect(html).toMatch(/<fieldset[^>]*disabled/);
    expect(isDisabled(html, "pl-save")).toBe(true);
    expect(html).toContain("Проект открыт только для чтения");
  });
});

describe("the tab «Прогноз»", () => {
  it("before the request: the sources, the models that are built, the closed wireframes only; the button's reason", async () => {
    await run("planned.evaluate", { id: FIRST.id });
    let html = windowHtml();
    expect(html).toContain('data-tab="forecast"');
    for (const t of ["Только ближайшие скважины", "Тела неявной модели", "Замкнутые каркасы"]) expect(html).toContain(t);
    expect(html).not.toContain("Выберите модель или каркасы"); // the hole has a set: «только ближайшие» is a request
    const { setCheckSet } = await import("../src/modules/drillholes/plannedState");
    setCheckSet(null);
    expect(windowHtml()).toContain("Выберите модель или каркасы"); // no set, no bodies: the reason stands by the button
    const { setSource } = await import("../src/modules/drillholes/plannedState");
    setSource({ kind: "model" });
    html = windowHtml();
    expect(html).toContain("Литология");
    setSource({ kind: "meshes" });
    html = windowHtml();
    expect(html).toContain("Рудное тело");
    expect(html).not.toContain("Дыра"); // an open wireframe has no inside
  });

  it("the answer: the intervals with «вне тел» (never a code of 0), the length of the bodies, the nearest holes", async () => {
    const r = await run("planned.evaluate", { id: FIRST.id, model_id: MODEL, set_id: SET });
    expect(r.ok).toBe(true);
    const html = windowHtml();
    const rows = html.match(/<tr data-code="[^"]*">/g) ?? [];
    expect(rows).toHaveLength(3);
    expect(html).toContain('data-code=""');
    expect(html).toContain("вне тел");
    expect(html).toContain('data-code="SS"');
    expect(html).toContain("60,91"); // the end of the first gap, two decimals
    expect(html).toContain("В телах: INT — 0 м; SS — 42,93 м");
    expect(html).toContain('data-testid="pl-nearest"');
    expect((html.match(/<tr><td>H\d+<\/td>/g) ?? []).length).toBeLessThanOrEqual(5);
    expect(html).toContain("H057");
    expect(html).toContain("Модель «Литология» устарела"); // the journal of the core
  });

  it("only the nearest holes: no table of intervals", async () => {
    await run("planned.evaluate", { id: AIM.id, set_id: SET });
    const html = windowHtml();
    expect(html).not.toContain("pl-intervals");
    expect(html).toContain('data-testid="pl-nearest"');
    expect(html).toContain(R.planned_evaluate_set.body.nearest[0].hole);
    expect(html).toContain("Длина скважины");
  });

  it("a form with changes is not asked: the button says why", async () => {
    await run("planned.evaluate", { id: FIRST.id });
    setForm({ dip: "-60" });
    expect(windowHtml()).toContain("сохраните скважину");
    expect(isDisabled(windowHtml(), "pl-evaluate")).toBe(true);
  });

  it("the refusal of the core stands under the button", async () => {
    core.force(`POST /planned_holes/${FIRST.id}/evaluate`, R.planned_evaluate_bad);
    await run("planned.evaluate", { id: FIRST.id, model_id: MODEL });
    expect(windowHtml()).toContain('data-testid="pl-evaluate-error"');
    expect(windowHtml()).toContain("Нечего проверять");
  });
});

describe("the tab «План / факт»", () => {
  it("the facts, the chart d(depth), the table by pages of 50", async () => {
    const r = await run("planned.compare", { id: FIRST.id, set_id: SET, hole: "H001" });
    expect(r.ok).toBe(true);
    const html = windowHtml();
    expect(html).toContain('data-tab="compare"');
    expect(html).toContain("281,41 м");
    expect(html).toContain("120 м");
    expect(html).toContain("126,3 м");
    expect(html).toContain('data-points="121"');
    expect(html).toContain('data-mark="max"');
    expect(html).toContain("макс. 281");
    expect(html).toContain('data-rows="121"');
    expect((html.match(/<tr><td class="[^"]*">\d/g) ?? []).length).toBe(50);
    expect(html).toContain("Строки 1–50 из 121");
    expect(html).toContain("Глубина, м");
    expect(html).toContain("ΔX, м");
  });

  it("the chart: the line of the deviation on its axes; equal paths are a line on 0; no rows — no chart", () => {
    const a = readCompare(R.planned_compare.body);
    const svg = render(createElement(PlannedChart, { answer: a }));
    expect(svg).toContain("Отклонение факта от плана по глубине");
    expect(svg).toContain("Глубина, м");
    expect(svg).toContain("Отклонение d, м");
    const zero = readCompare({ rows: [0, 1, 2].map((d) => ({ depth: d, dx: 0, dy: 0, dz: 0, d: 0 })), max: 0, max_depth: 0, hole: "H1" });
    const flatLine = render(createElement(PlannedChart, { answer: zero }));
    expect(flatLine).toContain('data-points="3"');
    expect(render(createElement(PlannedChart, { answer: readCompare({ rows: [] }) }))).toContain("Нечего рисовать");
  });

  it("the button says what to choose", async () => {
    await run("planned.compare", { id: FIRST.id });
    const html = windowHtml();
    expect(html).toContain("Выберите фактическую скважину");
    expect(isDisabled(html, "pl-compare-run")).toBe(true);
    setTab("params");
  });
});

describe("the footer", () => {
  it("the buttons of the files wait for a saved hole; a saved one has both", async () => {
    await run("planned.new");
    expect(isDisabled(windowHtml(), "pl-export-csv")).toBe(true);
    await run("planned.open", { id: FIRST.id });
    const html = windowHtml();
    expect(isDisabled(html, "pl-export-csv")).toBe(false);
    expect(html).toContain("Экспорт CSV");
    expect(html).toContain("Экспорт DXF");
    expect(html).toContain("collar.csv и survey.csv");
  });

  it("what an export wrote is told in the footer", async () => {
    await run("planned.open", { id: FIRST.id });
    await run("planned.export", { id: FIRST.id, format: "csv", path: "/tmp/out" });
    expect(windowHtml()).toContain("collar.csv; survey.csv");
  });
});

describe("the pick of a point on the scene", () => {
  it("the ray meets the plane at the height of the field; a horizontal ray misses it", () => {
    const p = onPlane({ origin: [0, 0, 100], dir: [1, 0, -1] }, 40);
    expect(p).toEqual([60, 0, 40]);
    expect(onPlane({ origin: [0, 0, 100], dir: [1, 0, 0] }, 40)).toBeNull();
    expect(onPlane({ origin: [0, 0, 100], dir: [1, 0, 1] }, 40)).toBeNull(); // behind the cursor
    expect(heightOf("12,5", 7)).toBe(12.5);
    expect(heightOf("", 7)).toBe(7);
    expect(heightOf("", null)).toBe(0);
  });

  it("a click puts the point into the form; a right click ends the pick; without a scene it is a refusal in Russian", async () => {
    expect(() => startPick("collar", () => "")).toThrow(/3D-окно ещё не готово/);
    await run("planned.new");
    let tool: { pointer(e: unknown): boolean } | null = null;
    const scene = {
      clipPlanes: [],
      resolution: {},
      addLayer: () => undefined,
      removeLayer: () => undefined,
      invalidate: () => undefined,
      onRestored: () => () => undefined,
      setHandles: () => undefined, // the handles of the other modules that join every scene
      setTool: (t: typeof tool) => void (tool = t),
      cursorRay: (x: number, y: number) => ({ origin: [x, y, 500], dir: [0, 0, -1] }),
      worldCamera: () => ({ target: [0, 0, 250] }),
    } as unknown as Scene;
    setCurrentScene(scene);
    setXyz("collar", 2, "300");
    startPick("collar", () => plannedWin.get().form.collar[2]);
    expect(pickStore.get().what).toBe("collar");
    expect(flat(pickStore.get().note ?? "")).toContain("Z = 300 м");
    expect(tool!.pointer({ type: "move", x: 1, y: 1, button: 0 })).toBe(false);
    expect(tool!.pointer({ type: "click", x: 120.456, y: 80, button: 0 })).toBe(true);
    expect(plannedWin.get().form.collar).toEqual(["120,46", "80", "300"]);
    expect(plannedWin.get().dirty).toBe(true);
    expect(pickStore.get().what).toBeNull();
    expect(tool).toBeNull(); // the tool went with the pick
    startPick("target", () => "");
    expect(flat(pickStore.get().note ?? "")).toContain("Z = 250 м"); // the height of the view target
    expect(tool!.pointer({ type: "click", x: 5, y: 6, button: 2 })).toBe(true);
    expect(pickStore.get().what).toBeNull();
    expect(plannedWin.get().form.target).toEqual(["", "", ""]);
  });

  it("the command `planned.pick` needs the window and is not for the assistant", async () => {
    expect((await app.registry.execute("planned.pick", { what: "collar" }, "user")).result).toContain("Нет открытого окна");
    expect((await app.registry.execute("planned.pick", { what: "collar" }, "claude")).result).toContain("только из интерфейса");
  });
});

describe("the section of the tree", () => {
  it("lists the holes by name with the way they are given and the check box «в 3D»; an empty project says what to do", () => {
    const html = treeHtml();
    expect(html).toContain('data-testid="tree-planned"');
    expect(html).toContain('data-holes="2"');
    expect(html).toContain("ПС-1");
    expect(html).toContain("ПС-2 (через цель)");
    expect(html).toContain(">дуга<");
    expect(html).toContain(">цель<");
    expect(html).toContain("показать в 3D");
    expect((html.match(/type="checkbox"[^>]*checked=""/g) ?? []).length).toBe(2);
    plannedViewStore.set({ hidden: [AIM.id], chosen: FIRST.id });
    const hidden = treeHtml();
    expect((hidden.match(/type="checkbox"[^>]*checked=""/g) ?? []).length).toBe(1);
    expect(hidden).toMatch(/aria-selected="true"[^>]*aria-label="ПС-1/);
    plannedStore.set({ rows: [], loaded: true });
    expect(treeHtml()).toContain("Нет проектных скважин");
    plannedStore.set({ rows: [], loaded: true, error: "Ядро недоступно." });
    expect(treeHtml()).toContain("Не прочитано: Ядро недоступно.");
  });

  it("the menu: the commands of the registry with the hole's number; unavailable ones with the reason", () => {
    const items = plannedMenu({ id: FIRST.id }, (id) => (id === "planned.delete" ? "Проект открыт только для чтения" : null), () => undefined, () => undefined);
    expect(items.filter((i) => !i.separator).map((i) => i.label)).toEqual(["Открыть…", "Прогноз…", "План / факт…", "Экспорт CSV…", "Экспорт DXF…", "Обновить список", "Удалить…"]);
    expect(items.find((i) => i.label === "Удалить…")!.reason).toBe("Проект открыт только для чтения");
    openMenu(1, 2, items);
    expect(menuStore.get().items).toHaveLength(items.length);
  });
});
