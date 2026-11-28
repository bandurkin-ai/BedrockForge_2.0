// Э27, 27.7 (Д-44 А5): several objects of the data tree — Ctrl adds or takes away, Shift takes the range of the section;
// «Свойства» of objects of one kind show the shared fields («разные» for the ones that differ) and a change goes to every
// chosen object as one step of undo; objects of different kinds have no properties. No DOM: the rules are pure, the click
// is run against a registry that records the commands, the panel is rendered to a string.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { sceneObjects, type LineLayerInfo, type RasterInfo } from "../src/modules/scene/objects";
import { AppContext } from "../src/shell/AppContext";
import { createApp, type App } from "../src/shell/app";
import { MultiProps } from "../src/shell/MultiProps";
import { applyAll, applyPlan, commonKind, commonValue, fieldsOf, parseOpacity, readField, revertPlan, type Lookup } from "../src/shell/multiProps";
import { bindTreeSelection, pickMulti, pickPlain } from "../src/shell/multiSelect";
import { isSelected, multiStore, NO_MULTI, pickOfTestId, pickSelection, selectedIds, selectedItems, type Mods, type Selected } from "../src/shell/selection";

// `useSyncExternalStore` of the shell hooks has no server snapshot: the string renderer reads the stores once instead
vi.mock("../src/shell/useStore", () => ({
  useStore: (store: { get: () => unknown }, select: (s: unknown) => unknown) => select(store.get()),
  useRegistryVersion: () => 0,
}));

const L = (id: number): Selected => ({ kind: "lines", id });
const R = (id: number): Selected => ({ kind: "raster", id });
const rows = [L(1), L(2), L(3), L(4), L(5)];
const NONE: Mods = { ctrl: false, shift: false };
const ids = (items: readonly Selected[]) => items.map((x) => x.id);

describe("what a click chooses (pickSelection)", () => {
  it("a plain click chooses one object and it becomes the anchor", () => {
    const r = pickSelection([L(1), L(2)], L(1), L(4), NONE, rows);
    expect(r).toEqual({ items: [L(4)], primary: L(4), anchor: L(4) });
  });

  it("Ctrl adds an object and takes it away again; the main object is the last one left", () => {
    const added = pickSelection([L(1)], L(1), L(3), { ctrl: true, shift: false }, rows);
    expect(ids(added.items)).toEqual([1, 3]);
    expect(added.primary).toEqual(L(3));
    const back = pickSelection(added.items, added.anchor, L(3), { ctrl: true, shift: false }, rows);
    expect(ids(back.items)).toEqual([1]);
    expect(back.primary).toEqual(L(1));
    const none = pickSelection([L(1)], L(1), L(1), { ctrl: true, shift: false }, rows);
    expect(none).toEqual({ items: [], primary: null, anchor: L(1) });
  });

  it("Shift takes the rows between the anchor and the clicked one, in either direction; the anchor stays", () => {
    const down = pickSelection([L(2)], L(2), L(4), { ctrl: false, shift: true }, rows);
    expect(ids(down.items)).toEqual([2, 3, 4]);
    expect(down.primary).toEqual(L(4));
    expect(down.anchor).toEqual(L(2));
    const up = pickSelection(down.items, down.anchor, L(1), { ctrl: false, shift: true }, rows);
    expect(ids(up.items)).toEqual([1, 2]);
  });

  it("Ctrl+Shift adds the range to what is chosen already", () => {
    const r = pickSelection([L(1)], L(3), L(5), { ctrl: true, shift: true }, rows);
    expect(ids(r.items).sort()).toEqual([1, 3, 4, 5]);
  });

  it("Shift without the anchor in the rows of the section (another section, a folder collapsed) is a plain click", () => {
    expect(pickSelection([], null, L(3), { ctrl: false, shift: true }, rows).items).toEqual([L(3)]);
    expect(pickSelection([R(9)], R(9), L(3), { ctrl: false, shift: true }, rows).items).toEqual([L(3)]);
  });
});

describe("the set of the chosen objects next to the main one", () => {
  const main = L(2);
  const set = { items: [L(1), L(2), L(3)], primary: main, anchor: L(1) };

  it("counts only while the main object is the very one it was made for", () => {
    expect(selectedItems(main, set)).toEqual(set.items);
    expect(selectedItems({ ...main }, set)).toEqual([main]); // another choice of the same object: the set is stale
    expect(selectedItems(null, set)).toEqual([]);
    expect(selectedItems(main, NO_MULTI)).toEqual([main]);
  });

  it("gives the ids of a kind for the commands that work on all the chosen objects", () => {
    const mixed = { items: [L(1), R(7), L(3)], primary: main, anchor: null };
    expect(selectedIds(main, "lines", mixed)).toEqual([1, 3]);
    expect(selectedIds(main, "raster", mixed)).toEqual([7]);
    expect(selectedIds(null, "lines", mixed)).toEqual([]);
  });

  it("marks every row of the set, and only while the set is valid", () => {
    expect([1, 2, 3, 4].map((id) => isSelected(main, "lines", id, set))).toEqual([true, true, true, false]);
    expect(isSelected({ ...main }, "lines", 1, set)).toBe(false);
    expect(isSelected(main, "raster", 1, set)).toBe(false);
  });

  it("the rows of the sections that name the object in the test id are recognised", () => {
    expect(pickOfTestId("tree-layer-12")).toEqual(L(12));
    expect(pickOfTestId("tree-dtm-5")).toEqual({ kind: "wireframe", id: 5 });
    expect(pickOfTestId("tree-wireframe-5")).toEqual({ kind: "wireframe", id: 5 });
    expect(pickOfTestId("tree-block-3")).toEqual({ kind: "blocks", id: 3 });
    expect(pickOfTestId("tree-raster-8")).toEqual(R(8));
    expect(pickOfTestId("tree-layer-edited-12")).toBeUndefined();
    expect(pickOfTestId("tree-section-lines")).toBeUndefined();
    expect(pickOfTestId(undefined)).toBeUndefined();
  });
});

/** An app whose registry records `object.select` and sets the main object as the real command does. */
function fakeApp(): { app: App; calls: string[] } {
  const real = createApp({ persist: false });
  const calls: string[] = [];
  const app = {
    ...real,
    registry: {
      get: (id: string) => real.registry.get(id),
      bind: (id: string, run: never, un?: never) => real.registry.bind(id, run, un),
      execute: async (id: string, args: Record<string, unknown> = {}) => {
        calls.push(`${id} ${JSON.stringify(args)}`);
        if (id.startsWith("tree.")) {
          try {
            return { ok: true, result: String(await real.registry.get(id)!.run!(args, "user")) };
          } catch (e) {
            return { ok: false, result: (e as Error).message };
          }
        }
        if (id === "object.select") real.store.set({ selected: { kind: args.kind as Selected["kind"], id: Number(args.id) } });
        if (id === "selection.clear") real.store.set({ selected: null });
        return { ok: true, result: "" };
      },
    },
  } as unknown as App;
  bindTreeSelection(app);
  return { app, calls };
}

afterEach(() => multiStore.set(NO_MULTI));

describe("a click with Ctrl and Shift on the rows (pickMulti, pickPlain)", () => {
  it("Ctrl chooses the object by `object.select` and adds it; Shift then takes the range from the anchor", async () => {
    const { app, calls } = fakeApp();
    pickPlain(app, L(2), () => app.store.set({ selected: L(2) }));
    await pickMulti(app, L(4), { ctrl: true, shift: false }, rows);
    expect(calls).toEqual(['tree.select_toggle {"kind":"lines","id":4}', 'object.select {"kind":"lines","id":4}']);
    const s = app.store.get().selected;
    expect(s).toEqual(L(4)); // the main object: what every reader of a single selection sees
    expect(ids(selectedItems(s))).toEqual([2, 4]);
    await pickMulti(app, L(5), { ctrl: false, shift: true }, rows); // the anchor is the last click (4)
    expect(ids(selectedItems(app.store.get().selected))).toEqual([4, 5]);
  });

  it("a plain click drops the set, even on the main object, and the rows are drawn again", async () => {
    const { app } = fakeApp();
    pickPlain(app, L(1), () => app.store.set({ selected: L(1) }));
    await pickMulti(app, L(3), { ctrl: true, shift: false }, rows);
    const before = app.store.get().selected;
    expect(selectedItems(before)).toHaveLength(2);
    pickPlain(app, L(3), () => undefined); // object.select would change nothing: the object is the main one already
    const after = app.store.get().selected;
    expect(after).toEqual(L(3));
    expect(after).not.toBe(before); // a new object: the rows read the choice again
    expect(selectedItems(after)).toEqual([L(3)]);
  });

  it("taking away the last object clears the choice; another `object.select` makes the set stale", async () => {
    const { app, calls } = fakeApp();
    pickPlain(app, L(1), () => app.store.set({ selected: L(1) }));
    await pickMulti(app, L(1), { ctrl: true, shift: false }, rows);
    expect(calls).toEqual(['tree.select_toggle {"kind":"lines","id":1}', "selection.clear {}"]);
    expect(app.store.get().selected).toBeNull();

    pickPlain(app, L(1), () => app.store.set({ selected: L(1) }));
    await pickMulti(app, L(2), { ctrl: true, shift: false }, rows);
    app.store.set({ selected: L(2) }); // a click in the 3D window: the same object, a new choice
    expect(selectedItems(app.store.get().selected)).toEqual([L(2)]);
  });
});

describe("the fields the chosen objects share", () => {
  const lookup = (p: Partial<Lookup> = {}): Lookup => ({
    exists: () => true,
    hidden: [],
    blockShown: () => false,
    layerColor: () => null,
    wireframeColor: () => null,
    rasterOpacity: () => 1,
    ...p,
  });

  it("which fields a kind has", () => {
    expect(fieldsOf("lines").map((f) => f.key)).toEqual(["visible", "color"]);
    expect(fieldsOf("raster").map((f) => f.key)).toEqual(["visible", "opacity"]);
    expect(fieldsOf("blocks").map((f) => f.key)).toEqual(["visible"]);
    expect(fieldsOf("implicit")).toEqual([]);
  });

  it("one value for all, or «разные»; a missing colour is a value, colours are compared as written", () => {
    expect(commonValue([true, true])).toEqual({ mixed: false, value: true });
    expect(commonValue([true, false]).mixed).toBe(true);
    expect(commonValue([null, null])).toEqual({ mixed: false, value: null });
    expect(commonValue([null, "#ff8800"]).mixed).toBe(true);
    expect(commonValue(["#FF8800", "#ff8800"]).mixed).toBe(false);
  });

  it("reads each object; an object that is not in the lists is left out", () => {
    const color = fieldsOf("lines")[1];
    const lk = lookup({ layerColor: (id) => (id === 1 ? "#ff8800" : id === 2 ? null : undefined) });
    expect(readField(color, "lines", [1, 2, 3], lk)).toEqual([
      { id: 1, value: "#ff8800" },
      { id: 2, value: null },
    ]);
    const visible = fieldsOf("lines")[0];
    expect(readField(visible, "lines", [1, 2], lookup({ hidden: ["ln:2"] })).map((r) => r.value)).toEqual([true, false]);
  });

  it("the commands that set a value on every object, and the same with the old values", () => {
    const [visible, color] = fieldsOf("lines");
    expect(applyPlan(color, "lines", [1, 2], "#1f6bff")).toEqual([
      { command: "object.color", args: { kind: "lines", id: 1, color: "#1f6bff" } },
      { command: "object.color", args: { kind: "lines", id: 2, color: "#1f6bff" } },
    ]);
    expect(applyPlan(visible, "lines", [4], false)).toEqual([{ command: "layer.show", args: { layer: "lines", id: 4, visible: false } }]);
    expect(applyPlan(fieldsOf("blocks")[0], "blocks", [7], true)).toEqual([{ command: "blocks.show", args: { model_id: 7, visible: true } }]);
    expect(applyPlan(fieldsOf("raster")[1], "raster", [3], 0.5)).toEqual([{ command: "raster.set", args: { raster_id: 3, opacity: 0.5 } }]);
    expect(revertPlan(color, "lines", [{ id: 1, value: "#ff8800" }, { id: 2, value: null }]).map((s) => s.args.color)).toEqual(["#ff8800", null]);
  });

  it("a change goes to all the chosen objects; one undo brings them all back; a failure stops it", async () => {
    const color = fieldsOf("lines")[1];
    const lk = lookup({ layerColor: (id) => ({ 1: "#ff8800", 2: null, 3: "#00ff00" })[id] });
    const sent: string[] = [];
    const ok = await applyAll(async (c, a) => (sent.push(`${c}:${a.id}:${a.color}`), true), color, "lines", [1, 2, 3], lk, "#1f6bff");
    expect(sent).toEqual(["object.color:1:#1f6bff", "object.color:2:#1f6bff", "object.color:3:#1f6bff"]);
    expect([ok.done, ok.total]).toEqual([3, 3]);
    expect(ok.undo.map((s) => s.args.color)).toEqual(["#ff8800", null, "#00ff00"]);

    let n = 0;
    const part = await applyAll(async () => ++n < 2, color, "lines", [1, 2, 3], lk, "#1f6bff");
    expect([part.done, part.total]).toEqual([1, 3]);
    expect(part.undo.map((s) => s.args.id)).toEqual([1]); // only what was changed is brought back
  });

  it("an object that was deleted is not read and not changed; «не найден» does not stop the others", async () => {
    const [visible] = fieldsOf("lines");
    const gone = new Set([2]);
    const lk = lookup({ exists: (_k, id) => !gone.has(id) });
    // the deleted layer has no value, though «Показан в 3D» is a flag that a missing object would answer too
    expect(readField(visible, "lines", [1, 2, 3], lk).map((r) => r.id)).toEqual([1, 3]);
    const sent: number[] = [];
    const r = await applyAll(async (_c, a) => (sent.push(Number(a.id)), true), visible, "lines", [1, 2, 3], lk, false);
    expect(sent).toEqual([1, 3]);
    expect([r.done, r.total, r.skipped]).toEqual([2, 2, 1]);
    expect(r.undo.map((s) => s.args.id)).toEqual([1, 3]);

    // deleted while the run is going: its step fails, the run goes on with the rest
    const live = new Set([1, 2, 3]);
    const during = await applyAll(
      async (_c, a) => {
        if (a.id === 2) {
          live.delete(2);
          return false;
        }
        return true;
      },
      visible,
      "lines",
      [1, 2, 3],
      lookup({ exists: (_k, id) => live.has(id) }),
      false,
    );
    expect([during.done, during.total, during.skipped]).toEqual([2, 2, 1]);
    // a real failure of an object that is still there stops the run
    const stop = await applyAll(async (_c, a) => a.id !== 2, visible, "lines", [1, 2, 3], lookup(), false);
    expect([stop.done, stop.total]).toEqual([1, 3]);
  });

  it("the opacity is a number from 0,05 to 1 with a comma or a point", () => {
    expect(parseOpacity("0,5")).toEqual({ value: 0.5, error: null });
    expect(parseOpacity(" 0.25 ").value).toBe(0.25);
    expect(parseOpacity("").value).toBeNull();
    expect(parseOpacity("abc").error).toContain("не число");
    expect(parseOpacity("2").error).toContain("от 0,05 до 1");
  });

  it("the kind the objects share, or none", () => {
    expect(commonKind([L(1), L(2)])).toBe("lines");
    expect(commonKind([L(1), R(2)])).toBeNull();
    expect(commonKind([])).toBeNull();
  });
});

describe("«Свойства» of several objects", () => {
  const layer = (id: number, color: string | null): LineLayerInfo => ({
    id, file_id: 1, name: `Слой ${id}`, version: 1, color, points_only: false, line_count: 1, point_count: 2, length: 3, closed_count: 0, closed: "none", flat: false, extent: null, attr_schema: [],
  });
  const raster = (id: number, opacity: number): RasterInfo => ({
    id, name: `Растр ${id}`, version: 1, width: 1, height: 1, georef: null, elevation: null, vertical: false, opacity, has_elevation: false, displays: [], images: {}, warnings: [],
  });
  const file = { id: 1, name: "f.dxf", folder: null, version: 1, format: "dxf", label: null, filename: null, crs_name: null, warnings: [], layers: [] as LineLayerInfo[] };
  const render = (items: Selected[]) => {
    const app = createApp({ persist: false });
    return renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(MultiProps, { items })));
  };
  afterEach(() => sceneObjects.set({ files: [], rasters: [], wireframes: [], loaded: false, error: null }));

  it("the values that differ show «разные»; the values that agree are shown", () => {
    sceneObjects.set({ files: [{ ...file, layers: [layer(1, "#ff8800"), layer(2, "#1f6bff"), layer(3, "#ff8800")] }], rasters: [], wireframes: [], loaded: true, error: null });
    const differ = render([L(1), L(2)]);
    expect(differ).toContain("Выбрано");
    expect(differ).toContain("2 слоя линий");
    expect(differ).toMatch(/data-testid="props-multi-color"[^>]*data-mixed="true"/);
    expect(differ).toContain("разные");
    const same = render([L(1), L(3)]);
    expect(same).toMatch(/data-testid="props-multi-color"[^>]*data-mixed="false"/);
    expect(same).toContain('value="#ff8800"');
    expect(same).not.toMatch(/props-multi-color[^]*разные[^]*props-multi-undo/);
  });

  it("a number field that differs is empty with the hint «разные»", () => {
    sceneObjects.set({ files: [], rasters: [raster(1, 0.5), raster(2, 1)], wireframes: [], loaded: true, error: null });
    expect(render([R(1), R(2)])).toMatch(/<input[^>]*placeholder="разные"/);
    sceneObjects.set({ files: [], rasters: [raster(1, 0.5), raster(2, 0.5)], wireframes: [], loaded: true, error: null });
    const same = render([R(1), R(2)]);
    expect(same).not.toContain('placeholder="разные"');
    expect(same).toContain('value="0,5"');
  });

  it("objects of different kinds: a plaque and no fields", () => {
    sceneObjects.set({ files: [{ ...file, layers: [layer(1, null)] }], rasters: [raster(2, 1)], wireframes: [], loaded: true, error: null });
    const html = render([L(1), R(2)]);
    expect(html).toContain("Выбраны объекты разных типов: 2");
    expect(html).toContain('data-testid="props-multi-mixed"');
    expect(html).not.toContain("props-multi-color");
    expect(html).not.toContain("<input");
  });
});
