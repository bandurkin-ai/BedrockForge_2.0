// Э27, 27.11 (Д-44 А5): several rows chosen with Ctrl and Shift in EVERY section of the data tree, the fields they share, and the one
// stack of «Отменить» (the command `props.undo`, the button of the panel, Ctrl+Z) with the priority of the editors that have their own undo.
// No DOM: the rules are pure, the clicks run against a registry, the panel is rendered to a string.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext } from "../src/shell/AppContext";
import { createApp, type App } from "../src/shell/app";
import { MultiProps } from "../src/shell/MultiProps";
import { buildLookup, NO_LISTS, rowExists, type Lists } from "../src/shell/multiLookup";
import { applyAll, applyPlan, commonKind, fieldsOf, kindCount, parseNumber, readField, revertPlan, whyNoFields, FIELDS } from "../src/shell/multiPropsModel";
import { pickGroup, pickMulti, pickPlain, forgetRow, clearGroup } from "../src/shell/multiSelect";
import { attachUndoKey, clearUndo, ctrlZTarget, isTextField, isUndoKey, lastUndo, pushUndo, undoLast, undoStore, UNDO_LIMIT } from "../src/shell/propsUndo";
import { groupItems, groupMark, groupStore, multiStore, NO_GROUP, NO_MULTI, pairId, pickOfTestId, unpair, type Group, type PickKind, type RowRef } from "../src/shell/selection";
import { commandContext } from "../src/shell/store";
import { sceneObjects } from "../src/modules/scene/objects";
import { viewStore } from "../src/modules/scene/bind";
import { tablesStore } from "../src/modules/tables/state";
import { viewsStore } from "../src/modules/views/state";

// `useSyncExternalStore` of the shell hooks has no server snapshot: the string renderer reads the stores once instead
vi.mock("../src/shell/useStore", () => ({
  useStore: (store: { get: () => unknown }, select: (s: unknown) => unknown) => select(store.get()),
  useRegistryVersion: () => 0,
}));

const T = (id: number): RowRef => ({ kind: "trend", id });
const S = (id: number): RowRef => ({ kind: "set", id });
const NONE = { ctrl: false, shift: false };
const ids = (items: readonly RowRef[]) => items.map((x) => x.id);

/** Every section of the tree with a row to choose: the test id of the row, its kind and the fields the registry lets change. */
const SECTIONS: { section: string; testId: string; kind: PickKind; fields: string[] }[] = [
  { section: "Виды", testId: "view-4", kind: "view", fields: ["folder"] },
  { section: "Серии разрезов", testId: "series-tree-4", kind: "series", fields: [] },
  { section: "Скважины", testId: "tree-set-4", kind: "set", fields: ["visible", "traceColor", "traceWidth", "collarSize"] },
  { section: "Каркасы", testId: "tree-wireframe-4", kind: "wireframe", fields: ["visible", "color"] },
  { section: "ЦТМ", testId: "tree-dtm-4", kind: "wireframe", fields: ["visible", "color"] },
  { section: "Линии", testId: "tree-layer-4", kind: "lines", fields: ["visible", "color"] },
  { section: "Растры", testId: "tree-raster-4", kind: "raster", fields: ["visible", "opacity"] },
  { section: "Блочные модели", testId: "tree-block-4", kind: "blocks", fields: ["visible", "opacity"] },
  { section: "Фильтры", testId: "tree-filter-4", kind: "filter", fields: [] },
  { section: "Аудит: данные QAQC", testId: "tree-qaqc-data-4", kind: "qaqc_data", fields: [] },
  { section: "Аудит: swath plot", testId: "tree-swath-4", kind: "swath", fields: ["visible"] },
  { section: "Отчёты QAQC", testId: "tree-qaqc-report-4", kind: "qaqc_report", fields: [] },
  { section: "Отчёты по ресурсам", testId: "tree-resource-report-4", kind: "resource_report", fields: [] },
  { section: "Тренды", testId: "tree-trend-4", kind: "trend", fields: ["visible"] },
  { section: "Модели вариограмм", testId: "tree-vario-model-4", kind: "vario", fields: [] },
  { section: "Параметры оценки: строки", testId: "tree-est-row-4", kind: "est_row", fields: [] },
  { section: "Параметры оценки: планы", testId: "tree-est-plan-4", kind: "est_plan", fields: [] },
  { section: "Моделирование", testId: "tree-sim-row-4", kind: "sim_row", fields: [] },
  { section: "Неявные модели", testId: "tree-implicit-4", kind: "implicit", fields: [] },
  { section: "Проектные скважины", testId: "tree-planned-4", kind: "planned", fields: ["visible"] },
  { section: "Карьер: сценарии", testId: "tree-pit-scenario-4", kind: "pit_scenario", fields: [] },
  { section: "Отображения по стволу", testId: "tree-display-4", kind: "display", fields: [] },
  { section: "Графики", testId: "tree-chart-4", kind: "chart", fields: [] },
  { section: "Результаты обработки проб", testId: "tree-result-4", kind: "result", fields: [] },
  { section: "Линии: файл", testId: "tree-linefile-4", kind: "line_file", fields: [] },
  { section: "Карьер: каркас сценария", testId: "tree-pit-9-wireframe-4", kind: "wireframe", fields: ["visible", "color"] },
  { section: "Неявные модели: каркас-выход", testId: "tree-implicit-out-4", kind: "wireframe", fields: ["visible", "color"] },
  { section: "Неявные модели: слой линий", testId: "tree-implicit-line-4", kind: "lines", fields: ["visible", "color"] },
];

/** Rows named by two numbers: the id is made of both. */
const PAIRS: { section: string; testId: string; kind: PickKind; a: number; b: number; fields: string[] }[] = [
  { section: "Серии разрезов: разрез серии", testId: "series-tree-3-12", kind: "series_section", a: 3, b: 12, fields: [] },
  { section: "Неявные модели: группа данных", testId: "tree-implicit-5-group-7", kind: "implicit_group", a: 5, b: 7, fields: ["visible"] },
  { section: "Неявные модели: разлом", testId: "tree-implicit-5-fault-7", kind: "implicit_fault", a: 5, b: 7, fields: [] },
  { section: "Неявные модели: домен стека", testId: "tree-implicit-5-stack-7", kind: "implicit_stack", a: 5, b: 7, fields: [] },
];

describe("every section of the tree has rows that can be chosen", () => {
  it.each(SECTIONS)("$section: the row «$testId» is a $kind", ({ testId, kind }) => {
    expect(pickOfTestId(testId)).toEqual({ kind, id: 4 });
  });

  it.each(PAIRS)("$section: the row «$testId» is a $kind named by two numbers", ({ testId, kind, a, b, fields }) => {
    const ref = pickOfTestId(testId)!;
    expect(ref.kind).toBe(kind);
    expect(unpair(ref.id)).toEqual([a, b]);
    expect(ref.id).toBe(pairId(a, b));
    expect(fieldsOf(kind).map((f) => f.key)).toEqual(fields);
    if (!fields.length) expect(whyNoFields(kind)).toMatch(/[а-я]/);
  });

  it("the rows of the parts of a section that are no objects are not chosen", () => {
    for (const id of ["tree-section-holes", "series-tree-4-more", "tree-implicit-error-4", "tree-implicit-4-data", "tree-implicit-4-wings-rest", "tree-implicit-out-x", "tree-pit-4-model", "tree-pit-4-none", "tree-charts-4", "tree-results-4", "tree-set-x"]) {
      expect(pickOfTestId(id)).toBeUndefined();
    }
  });

  it.each(SECTIONS)("$section: the fields are those the registry can set", ({ kind, fields }) => {
    expect(fieldsOf(kind).map((f) => f.key)).toEqual(fields);
    // a section with no field says why, in Russian
    if (!fields.length) expect(whyNoFields(kind)).toMatch(/[а-я]/);
  });

  it("every command a field uses is in the registry, with the arguments it declares", () => {
    const { registry } = createApp({ persist: false });
    for (const f of FIELDS) {
      for (const kind of f.kinds) {
        const steps = f.steps(kind, 7, f.type === "check" ? true : f.type === "color" ? "#112233" : f.type === "text" ? "А/Б" : 0.5);
        expect(steps.length).toBeGreaterThan(0);
        for (const step of steps) {
          const c = registry.get(step.command);
          expect(c, `${f.key} of ${kind}: ${step.command}`).toBeDefined();
          // the registry checks the arguments of a command: the same check the panel meets
          expect(Object.keys(step.args).filter((k) => !(k in (c!.params ?? {}))), `${step.command} of ${kind}`).toEqual([]);
        }
        expect(registry.get(f.command(kind)), `${f.key} of ${kind}`).toBeDefined();
      }
    }
  });
});

describe("a group of rows (sets, trends, views…)", () => {
  const rows = [T(1), T(2), T(3), T(4), T(5)];
  const g = (items: RowRef[], anchor: RowRef | null = items.at(-1) ?? null): Group => ({ items, anchor, kind: items[0]?.kind ?? null });

  it("Ctrl adds a row and takes it away; Shift takes the range of the kind from the anchor", () => {
    const one = pickGroup(NO_GROUP, T(2), { ctrl: true, shift: false }, rows);
    expect([ids(one.items), one.kind]).toEqual([[2], "trend"]);
    const two = pickGroup(one, T(4), { ctrl: true, shift: false }, rows);
    expect(ids(two.items)).toEqual([2, 4]);
    expect(ids(pickGroup(two, T(2), { ctrl: true, shift: false }, rows).items)).toEqual([4]);
    expect(ids(pickGroup(g([T(2)], T(2)), T(5), { ctrl: false, shift: true }, rows).items)).toEqual([2, 3, 4, 5]);
    expect(ids(pickGroup(g([T(1), T(3)], T(3)), T(5), { ctrl: true, shift: true }, rows).items)).toEqual([1, 3, 4, 5]);
  });

  it("the rows of another kind in the section are not in the range", () => {
    const mixed = [T(1), { kind: "trend" as const, id: 2 }, { kind: "filter" as const, id: 3 }, T(4)];
    expect(ids(pickGroup(g([T(1)], T(1)), T(4), { ctrl: false, shift: true }, mixed).items)).toEqual([1, 2, 4]);
  });

  it("taking a row away leaves the group alone marked: what the section keeps itself is «активный», never a second «выбран»", () => {
    const two = pickGroup(pickGroup(NO_GROUP, S(1), NONE, [S(1), S(2)]), S(2), { ctrl: true, shift: false }, [S(1), S(2)]);
    const left = pickGroup(two, S(1), { ctrl: true, shift: false }, [S(1), S(2)]); // the set that was clicked first is taken away
    expect(ids(left.items)).toEqual([2]);
    expect([groupMark(left, S(1)), groupMark(left, S(2))]).toEqual([2, 1]);
    const none = pickGroup(left, S(2), { ctrl: true, shift: false }, [S(1), S(2)]);
    expect([none.items, none.kind]).toEqual([[], "set"]);
    expect([groupMark(none, S(1)), groupMark(none, S(2))]).toEqual([2, 2]); // nothing is chosen
  });

  it("a row of another kind begins a new group", () => {
    const next = pickGroup(g([T(1), T(2)]), S(7), { ctrl: true, shift: false }, [S(7)]);
    expect([next.kind, ids(next.items)]).toEqual(["set", [7]]);
  });

  it("marks the rows: the chosen ones; the others only while two or more are chosen", () => {
    expect(groupMark(g([T(1), T(2)]), T(1))).toBe(1);
    expect(groupMark(g([T(1), T(2)]), T(3))).toBe(2);
    // a group of one, and a group that its last row was taken from: the group marks, what the section keeps itself is «активный»
    expect(groupMark(g([T(1)]), T(3))).toBe(2);
    expect(groupMark({ items: [], anchor: T(1), kind: "trend" }, T(1))).toBe(2);
    expect(groupMark(g([T(1), T(2)]), S(1))).toBe(0); // another kind
    expect(groupMark(NO_GROUP, T(1))).toBe(0); // no group: the section marks its own
    expect(groupItems(g([T(1)]))).toEqual([]);
    expect(groupItems(g([T(1), T(2)]))).toHaveLength(2);
  });
});

/** An app whose `object.select` sets the main object, as the real command does; the group commands are the real ones. */
function fakeApp(): App {
  const real = createApp({ persist: false });
  return {
    ...real,
    registry: {
      get: (id: string) => real.registry.get(id),
      bind: real.registry.bind.bind(real.registry),
      execute: async (id: string, args: Record<string, unknown> = {}) => {
        if (id.startsWith("tree.") || id === "selection.clear") {
          try {
            return { ok: true, result: String(await real.registry.get(id)!.run!(args, "user")) };
          } catch (e) {
            return { ok: false, result: (e as Error).message };
          }
        }
        if (id === "object.select") real.store.set({ selected: { kind: args.kind as never, id: Number(args.id) } });
        return { ok: true, result: "" };
      },
    },
  } as unknown as App;
}

describe("clicks on the rows of the sections without `object.select`", () => {
  const rows = [S(1), S(2), S(3), S(4)];
  beforeEach(() => clearGroup());
  afterEach(() => {
    clearGroup();
    multiStore.set(NO_MULTI);
  });

  it("a plain click makes a group of one and runs what the row did before; Ctrl and Shift add rows", async () => {
    const app = fakeApp();
    const did: string[] = [];
    pickPlain(app, S(2), () => did.push("select"));
    expect(did).toEqual(["select"]);
    expect(groupStore.get()).toMatchObject({ kind: "set", items: [S(2)] });
    await pickMulti(app, S(4), { ctrl: true, shift: false }, rows);
    expect(ids(groupStore.get().items)).toEqual([2, 4]);
    await pickMulti(app, S(1), { ctrl: false, shift: true }, rows); // the anchor is the last click (4)
    expect(ids(groupStore.get().items)).toEqual([1, 2, 3, 4]);
    expect(app.store.get().groupSize).toBe(4); // the shell knows: «Снять выбор» is available
    expect(commandContext(app.store.get()).hasGroup).toBe(true); // the button and Esc: «Снять выбор» is available
  });

  it("the choice of the section before it is not a part of the group: «Свойства» are the group's", async () => {
    const app = fakeApp();
    app.store.set({ selected: { kind: "lines", id: 9 } });
    await pickMulti(app, S(1), { ctrl: true, shift: false }, rows);
    expect(app.store.get().selected).toBeNull();
    expect(ids(groupStore.get().items)).toEqual([1]);
  });

  it("another object chosen (a click in the 3D window) drops the group; a plain click on a layer drops it too", async () => {
    const app = fakeApp();
    pickPlain(app, S(1), undefined);
    await pickMulti(app, S(2), { ctrl: true, shift: false }, rows);
    expect(groupStore.get().items).toHaveLength(2);
    app.store.set({ selected: { kind: "wireframe", id: 3 } });
    expect(groupStore.get().items).toEqual([]);
    expect(app.store.get().groupSize).toBe(0);

    pickPlain(app, S(1), undefined);
    pickPlain(app, { kind: "lines", id: 5 }, undefined);
    expect(groupStore.get().kind).toBeNull();
  });

  it("a scenario or a report chooses itself: its plain click is a member of the group, so the group is not dropped", () => {
    const app = fakeApp();
    pickPlain(app, { kind: "pit_scenario", id: 3 }, () => app.store.set({ selected: { kind: "pit_scenario", id: 3 } }));
    expect(groupStore.get().items).toEqual([{ kind: "pit_scenario", id: 3 }]);
  });

  it("a row that left the page leaves the group; closing the project drops it", async () => {
    const app = fakeApp();
    pickPlain(app, S(1), undefined);
    await pickMulti(app, S(2), { ctrl: true, shift: false }, rows);
    forgetRow(S(1));
    expect(ids(groupStore.get().items)).toEqual([2]);
    app.store.set({ projectName: "A" });
    await pickMulti(app, S(3), { ctrl: true, shift: false }, rows);
    app.store.set({ projectName: null });
    expect(groupStore.get().items).toEqual([]);
  });

  it("the registry command «Снять выбор» drops the group even when no object is chosen", async () => {
    const real = createApp({ persist: false });
    groupStore.set({ items: [S(1), S(2)], anchor: S(2), kind: "set" });
    const r = await real.registry.get("selection.clear")!.run!({}, "user");
    expect(groupStore.get()).toMatchObject({ items: [], kind: null });
    expect(r).toBe("Выбор снят");
  });
});

describe("the fields of the rows of the sections", () => {
  const lists = (p: Partial<Lists> = {}): Lists => ({ ...NO_LISTS, ...p });
  const env = (l: Lists, view: Record<string, unknown> = {}, hidden: string[] = []) =>
    buildLookup({ hidden, objects: { files: [], rasters: [], wireframes: [], loaded: true, error: null }, view, planned: { hidden: [2], chosen: null }, lists: l });
  const trend = (id: number) => ({ id }) as never;
  const set = (id: number) => ({ id, name: `Набор ${id}`, holes: 1 });

  it("a set: shown in 3D by the hidden keys; the width of the traces and the size of the collars come from the style of the view", () => {
    const lk = env(lists({ sets: [set(1), set(2)] }), { trace_styles: { "2": { width: 3 } }, collar_styles: { "1": { size: 20 } } }, ["set:2"]);
    const [visible, , width, size] = fieldsOf("set");
    expect(readField(visible, "set", [1, 2, 3], lk)).toEqual([{ id: 1, value: true }, { id: 2, value: false }]); // set 3 is gone
    expect(readField(width, "set", [1, 2], lk).map((r) => r.value)).toEqual([1.5, 3]);
    expect(readField(size, "set", [1, 2], lk).map((r) => r.value)).toEqual([20, 10]);
    expect(applyPlan(width, "set", [1, 2], 2)).toEqual([
      { command: "holes.trace_style", args: { set_id: 1, width: 2 } },
      { command: "holes.trace_style", args: { set_id: 2, width: 2 } },
    ]);
    expect(applyPlan(visible, "set", [5], false)).toEqual([{ command: "layer.show", args: { layer: "set", id: 5, visible: false } }]);
  });

  it("a trend, a planned hole, a swath plot: shown by their own keys of the view", () => {
    const lk = env(lists({ trends: [trend(1), trend(2)], planned: [{ id: 1 }, { id: 2 }] as never, swaths: [{ id: 3 }] }), { ellipsoids: [{ object: 2, visible: true }], swaths: [{ swath: 3, visible: true }] });
    expect(readField(fieldsOf("trend")[0], "trend", [1, 2], lk).map((r) => r.value)).toEqual([false, true]);
    expect(readField(fieldsOf("planned")[0], "planned", [1, 2], lk).map((r) => r.value)).toEqual([true, false]); // 2 is switched off
    expect(readField(fieldsOf("swath")[0], "swath", [3], lk).map((r) => r.value)).toEqual([true]);
    expect(applyPlan(fieldsOf("trend")[0], "trend", [4], true)).toEqual([{ command: "trend.show", args: { trend_id: 4, visible: true } }]);
    expect(applyPlan(fieldsOf("planned")[0], "planned", [4], false)).toEqual([{ command: "planned.show", args: { id: 4, visible: false } }]);
    expect(applyPlan(fieldsOf("swath")[0], "swath", [4], true)).toEqual([{ command: "stats.swath_3d", args: { swath_id: 4, visible: true } }]);
  });

  it("a group of data of an implicit model: the points in 3D, named by the model and the group", () => {
    const lists2 = lists({ implicit: [{ id: 5, group_list: [{ id: 7 }, { id: 8 }] }] });
    const lk = buildLookup({ hidden: [], objects: { files: [], rasters: [], wireframes: [], loaded: true, error: null }, view: {}, planned: { hidden: [], chosen: null }, implicit: { points: ["5:7"], ellipsoids: [] } as never, lists: lists2 });
    const [visible] = fieldsOf("implicit_group");
    expect(readField(visible, "implicit_group", [pairId(5, 7), pairId(5, 8), pairId(5, 9), pairId(6, 7)], lk).map((r) => [r.id, r.value])).toEqual([[pairId(5, 7), true], [pairId(5, 8), false]]);
    expect(applyPlan(visible, "implicit_group", [pairId(5, 8)], true)).toEqual([{ command: "implicit.show", args: { model_id: 5, group_id: 8, what: "points", visible: true } }]);
  });

  it("a view: the folder is a text, the root is an empty one; a block model: the opacity", () => {
    const lk = env(lists({ views: [{ id: 1, folder: "Разрезы/Север" }, { id: 2, folder: null }] as never }));
    expect(readField(fieldsOf("view")[0], "view", [1, 2], lk).map((r) => r.value)).toEqual(["Разрезы/Север", ""]);
    expect(applyPlan(fieldsOf("view")[0], "view", [1], "")).toEqual([{ command: "views.folder", args: { view_id: 1, folder: "" } }]);
    const opacity = fieldsOf("blocks")[1];
    expect(opacity.key).toBe("opacity");
    expect(applyPlan(opacity, "blocks", [6], 0.4)).toEqual([{ command: "blocks.style", args: { model_id: 6, opacity: 0.4 } }]);
    expect(readField(opacity, "blocks", [6], env(lists(), { block_styles: { "6": { visible: true, opacity: 0.4 } } })).map((r) => r.value)).toEqual([expect.any(Number)]);
  });

  it("a row that is gone is not read and not changed; the rest is", async () => {
    const lk = env(lists({ trends: [trend(1), trend(3)] }));
    const f = fieldsOf("trend")[0];
    const sent: number[] = [];
    const r = await applyAll(async (_c, a) => (sent.push(Number(a.trend_id)), true), f, "trend", [1, 2, 3], lk, true);
    expect(sent).toEqual([1, 3]);
    expect([r.done, r.total, r.skipped]).toEqual([2, 2, 1]);
    expect(revertPlan(f, "trend", [{ id: 1, value: false }]).map((s) => s.args.visible)).toEqual([false]);
    expect(rowExists(lists({ trends: [trend(1)] }), "trend", 2)).toBe(false);
    expect(rowExists(lists(), "filter", 2)).toBe(true); // a kind without a list here: it is drawn, so it is there
  });

  it("the number of a field is checked against its range, with a comma or a point", () => {
    const width = { min: 0.5, max: 12 };
    expect(parseNumber("1,5", width, "Толщина стволов", "1,5")).toEqual({ value: 1.5, error: null });
    expect(parseNumber("13", width, "Толщина стволов", "1,5").error).toBe("Толщина стволов — число от 0,5 до 12, например 1,5.");
    expect(parseNumber("a", width, "Толщина стволов").error).toContain("не число");
    expect(parseNumber(" ", width, "x").value).toBeNull();
  });

  it("the kind of the rows is named in the plural of the language", () => {
    expect(kindCount("set", 2)).toBe("2 набора скважин");
    expect(kindCount("trend", 5)).toBe("5 трендов");
    expect(kindCount("view", 21)).toBe("21 вид");
    expect(commonKind([T(1), T(2)])).toBe("trend");
    expect(commonKind([T(1), S(2)])).toBeNull();
  });
});

describe("«Свойства» of several rows", () => {
  const render = (items: RowRef[]) => {
    const app = createApp({ persist: false });
    return renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(MultiProps, { items })));
  };
  afterEach(() => {
    tablesStore.set({ sets: [] });
    viewsStore.set({ views: [] });
    viewStore.set({});
    sceneObjects.set({ files: [], rasters: [], wireframes: [], loaded: false, error: null });
    clearUndo();
  });

  it("sets: the fields they share, «разные» where they differ", () => {
    tablesStore.set({ sets: [{ id: 1, name: "А", holes: 3 }, { id: 2, name: "Б", holes: 4 }] });
    viewStore.set({ trace_styles: { "1": { width: 3 } } });
    const html = render([S(1), S(2)]);
    expect(html).toContain("2 набора скважин");
    expect(html).toMatch(/data-testid="props-multi-visible"[^>]*data-mixed="false"/);
    expect(html).toMatch(/data-testid="props-multi-traceWidth"[^>]*data-mixed="true"/);
    expect(html).toMatch(/<input[^>]*placeholder="разные"/);
    expect(html).toContain("Цвет стволов");
    expect(html).not.toContain("Без цвета"); // a trace always has a colour
  });

  it("views: the folder is a text field; filters: no fields, and the panel says why", () => {
    viewsStore.set({ views: [{ id: 1, name: "а", folder: "Север", version: 1, created_at: "", updated_at: "" }, { id: 2, name: "б", folder: "Север", version: 1, created_at: "", updated_at: "" }] });
    const views = render([{ kind: "view", id: 1 }, { kind: "view", id: 2 }]);
    expect(views).toContain("2 вида");
    expect(views).toContain('value="Север"');
    const filters = render([{ kind: "filter", id: 1 }, { kind: "filter", id: 2 }]);
    expect(filters).toContain("2 фильтра");
    expect(filters).toContain("не имеет настроек показа");
    expect(filters).not.toContain("<input");
  });

  it("the button «Отменить» shows the last change of the stack", () => {
    tablesStore.set({ sets: [{ id: 1, name: "А", holes: 3 }, { id: 2, name: "Б", holes: 4 }] });
    expect(render([S(1), S(2)])).not.toContain("props-multi-undo");
    pushUndo({ text: "«Показан в 3D»: изменено 2 набора скважин", steps: [{ command: "layer.show", args: { layer: "set", id: 1, visible: true } }] });
    const html = render([S(1), S(2)]);
    expect(html).toContain('data-testid="props-multi-undo"');
    expect(html).toContain("изменено 2 набора скважин");
  });
});

describe("the stack of «Отменить» for properties", () => {
  beforeEach(() => clearUndo());
  afterEach(() => clearUndo());
  const step = (id: number) => ({ command: "layer.show", args: { layer: "lines", id, visible: true } });

  it("keeps the changes in order, drops the oldest past the limit, ignores a change without steps", () => {
    pushUndo({ text: "ничего", steps: [] });
    expect(undoStore.get().entries).toHaveLength(0);
    for (let i = 0; i < UNDO_LIMIT + 5; i++) pushUndo({ text: `№${i}`, steps: [step(i)] });
    expect(undoStore.get().entries).toHaveLength(UNDO_LIMIT);
    expect(lastUndo()?.text).toBe(`№${UNDO_LIMIT + 4}`);
    expect(undoStore.get().entries[0].text).toBe("№5");
  });

  it("takes back the last change as one entry; a second call takes the one before; an empty stack answers null", async () => {
    pushUndo({ text: "первое", steps: [step(1), step(2)] });
    pushUndo({ text: "второе", steps: [step(3), step(4), step(5)] });
    const sent: number[] = [];
    const run = async (_c: string, a: Record<string, unknown>) => (sent.push(Number(a.id)), true);
    expect(await undoLast(run)).toMatchObject({ done: 3, failed: 0, text: "Отменено: второе" });
    expect(sent).toEqual([3, 4, 5]);
    expect(undoStore.get().entries.map((e) => e.text)).toEqual(["первое"]);
    expect((await undoLast(run))?.text).toBe("Отменено: первое");
    expect(await undoLast(run)).toBeNull();
  });

  it("an object that is gone does not keep the others from getting their values back", async () => {
    pushUndo({ text: "цвет", steps: [step(1), step(2), step(3)] });
    const r = await undoLast(async (_c, a) => a.id !== 2);
    expect([r?.done, r?.failed]).toEqual([2, 1]);
    expect(r?.text).toContain("не возвращено: 1");
  });

  it("the command `props.undo` of the registry: unavailable while the stack is empty, then runs the steps and keeps the depth in the state", async () => {
    const app = createApp({ persist: false });
    expect(app.registry.reason("props.undo", { ...app.registry.hooks.context() })).toContain("Нечего отменять");
    app.store.set({ theme: undefined } as never); // (nothing: keeps the store alive for the subscription below)
    pushUndo({ text: "тема", steps: [{ command: "theme.set", args: { theme: "light" } }] });
    expect(app.store.get().undoDepth).toBe(1);
    expect(app.registry.reason("props.undo")).toBeNull();
    const r = await app.registry.execute("props.undo");
    expect(r).toEqual({ ok: true, result: "Отменено: тема" });
    expect(app.store.get().workspace.theme).toBe("light");
    expect(app.store.get().undoDepth).toBe(0);
    expect((await app.registry.execute("props.undo")).ok).toBe(false);
  });

  it("when no step can be run the command says so and reports the failure", async () => {
    const app = createApp({ persist: false });
    pushUndo({ text: "цвет", steps: [{ command: "no.such.command", args: {} }] });
    const r = await app.registry.execute("props.undo");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Не отменено");
  });

  it("the stack is cleared when another project is opened: its objects are not these", () => {
    const app = createApp({ persist: false });
    app.store.set({ projectName: "A" });
    pushUndo({ text: "x", steps: [step(1)] });
    app.store.set({ projectName: "B" });
    expect(undoStore.get().entries).toHaveLength(0);
  });
});

describe("Ctrl+Z: who answers it", () => {
  const free = { typing: false, modal: false, inWindow: false, inBottom: false, handled: false };

  it("the stack of properties answers when nobody else has the key", () => {
    expect(ctrlZTarget(free)).toBe("props");
  });

  it.each([
    ["a text field: the browser undoes the text", { typing: true }],
    ["a dialog or the palette", { modal: true }],
    ["a window of its own (a form with its history)", { inWindow: true }],
    ["the bottom panel: the table has the journal of edits", { inBottom: true }],
    ["the string editor or a form already handled the key", { handled: true }],
  ])("%s keeps the key", (_why, patch) => {
    expect(ctrlZTarget({ ...free, ...patch })).toBe("other");
  });

  it("Ctrl+Z and the Russian «я»; Shift and Alt make another key; Ctrl+Y is not an undo", () => {
    const k = (key: string, p: Record<string, boolean> = {}) => ({ key, ctrlKey: true, metaKey: false, shiftKey: false, ...p });
    expect(isUndoKey(k("z"))).toBe(true);
    expect(isUndoKey(k("Z"))).toBe(true);
    expect(isUndoKey(k("я"))).toBe(true);
    expect(isUndoKey({ ...k("z"), ctrlKey: false, metaKey: true })).toBe(true);
    expect(isUndoKey(k("z", { shiftKey: true }))).toBe(false);
    expect(isUndoKey(k("z", { altKey: true }))).toBe(false);
    expect(isUndoKey(k("y"))).toBe(false);
    expect(isUndoKey({ ...k("z"), ctrlKey: false })).toBe(false);
  });

  it("a text field is where text is typed; a check box, a colour and a range are not", () => {
    const el = (tagName: string, type = "") => ({ tagName, type, isContentEditable: false }) as unknown as EventTarget;
    expect(isTextField(el("INPUT", "text"))).toBe(true);
    expect(isTextField(el("INPUT", "number"))).toBe(true);
    expect(isTextField(el("INPUT"))).toBe(true);
    expect(isTextField(el("TEXTAREA"))).toBe(true);
    expect(isTextField({ tagName: "DIV", isContentEditable: true } as unknown as EventTarget)).toBe(true);
    expect(isTextField(el("INPUT", "checkbox"))).toBe(false);
    expect(isTextField(el("INPUT", "color"))).toBe(false);
    expect(isTextField(el("INPUT", "range"))).toBe(false);
    expect(isTextField(el("SELECT"))).toBe(false);
    expect(isTextField(el("DIV"))).toBe(false);
    expect(isTextField(null)).toBe(false);
  });
});

describe("the key Ctrl+Z of the window", () => {
  /** A key event as the window gets it: the target is a stand-in with the few things the handler asks. */
  const press = (win: EventTarget, target: Record<string, unknown>, patch: Record<string, unknown> = {}) => {
    const e = new Event("keydown", { cancelable: true });
    const props = { key: "z", ctrlKey: true, metaKey: false, shiftKey: false, altKey: false, repeat: false, target, ...patch };
    for (const [k, v] of Object.entries(props)) Object.defineProperty(e, k, { value: v, configurable: true });
    if (patch.defaultPrevented) Object.defineProperty(e, "defaultPrevented", { value: true });
    win.dispatchEvent(e);
    return e;
  };
  const el = (tagName: string, closest: (sel: string) => unknown = () => null, type = "") => ({ tagName, type, isContentEditable: false, closest });

  afterEach(() => {
    vi.unstubAllGlobals();
    clearUndo();
  });

  it("takes the last change back from the page, and leaves the key to the editors, to the text and to the windows", async () => {
    const win = new EventTarget();
    vi.stubGlobal("window", win);
    const app = createApp({ persist: false });
    const detach = attachUndoKey(app);
    const dark = () => app.registry.execute("theme.set", { theme: "dark" });
    const push = () => pushUndo({ text: "тема", steps: [{ command: "theme.set", args: { theme: "light" } }] });
    const theme = () => app.store.get().workspace.theme;
    const settle = () => new Promise((r) => setTimeout(r, 0));

    // nothing on the stack: the key is not taken (the browser keeps it)
    await dark();
    expect(press(win, el("DIV")).defaultPrevented).toBe(false);

    // the page, a tree row, a check box: the stack answers
    for (const target of [el("DIV"), el("INPUT", () => null, "checkbox"), el("INPUT", () => null, "color")]) {
      await dark();
      push();
      const e = press(win, target);
      expect(e.defaultPrevented).toBe(true);
      await settle();
      expect(theme()).toBe("light");
    }

    // a text field, a window of its own, the bottom panel, a key handled already, Ctrl+Shift+Z, a repeat: not ours
    const dialog = (sel: string) => (sel === '[role="dialog"]' ? {} : null);
    const bottom = (sel: string) => (sel === '[aria-label="Нижняя панель"]' ? {} : null);
    const cases: [string, Record<string, unknown>, Record<string, unknown>][] = [
      ["a text field", el("INPUT", () => null, "text"), {}],
      ["a window", el("DIV", dialog), {}],
      ["the bottom panel", el("DIV", bottom), {}],
      ["a handled key", el("DIV"), { defaultPrevented: true }],
      ["Ctrl+Shift+Z", el("DIV"), { shiftKey: true }],
      ["a repeat", el("DIV"), { repeat: true }],
    ];
    for (const [why, target, patch] of cases) {
      await dark();
      push();
      press(win, target, patch);
      await settle();
      expect(theme(), why).toBe("dark");
      expect(lastUndo(), why).not.toBeNull();
      clearUndo();
    }
    detach();
    push();
    expect(press(win, el("DIV")).defaultPrevented).toBe(false); // detached: the key is nobody's
  });
});

afterEach(() => {
  groupStore.set(NO_GROUP);
  multiStore.set(NO_MULTI);
});
