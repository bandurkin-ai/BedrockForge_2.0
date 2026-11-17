import { describe, expect, it } from "vitest";
import { DEFAULT_STRIPLOG, type StripLogSettings, type Track } from "../src/charts/striplog/model";
import { dropDisplay, displayEntries, isShown, showDisplay } from "../src/modules/downhole/displays";
import { arrange, cleanForSave, firstLayer, formOf, layerFor, mapRefs, sameSettings, templateLayers, uniqueName } from "../src/modules/downhole/formModel";
import { EditHistory, historyKey } from "../src/modules/downhole/history";
import { nameKey } from "../src/modules/downhole/tables";
import { defaultStyleFor, nameMatches, resolveTracks, type StyleRec } from "../src/viz/downhole/styles";
import type { IntervalTableInfo } from "../src/viz/downhole/types";

const assay: IntervalTableInfo = {
  key: "пробы", id: 3, name: "Пробы", builtin: false, row_count: 10, kind: "interval",
  columns: [{ name: "AU", kind: "number", unit: "г/т" }, { name: "CU", kind: "number", unit: "%" }, { name: "Номер пробы", kind: "text", unit: "" }],
};
const cut: IntervalTableInfo = {
  key: "урезка", id: 7, name: "Урезка", builtin: false, row_count: 4, kind: "interval",
  columns: [{ name: "LITH", kind: "text", unit: "" }, { name: "AU_CUT", kind: "number", unit: "г/т" }],
};
const events: IntervalTableInfo = { key: "структуры", id: 9, name: "Структуры", builtin: false, row_count: 2, kind: "event", columns: [{ name: "TYPE", kind: "text", unit: "" }] };
const tables = [assay, cut, events];

const auStyle: StyleRec = { id: 11, uid: "u11", name: "Au лента", level: "layer", data_kind: "number", default_for: ["AU*"], body: { type: "color", form: "panel", colorBy: "classes", width: 45, side: "left" } };

describe("undo history of the form", () => {
  it("undoes and redoes steps; a new change drops the redo list", () => {
    const h = new EditHistory<number>();
    h.record(0, undefined, 0); // 0 → 1
    h.record(1, undefined, 10); // 1 → 2
    expect(h.undo(2)).toBe(1);
    expect(h.undo(1)).toBe(0);
    expect(h.undo(0)).toBeNull();
    expect(h.redo(0)).toBe(1);
    expect(h.canRedo).toBe(true);
    h.record(1, undefined, 20); // 1 → 5
    expect(h.canRedo).toBe(false);
    expect(h.redo(5)).toBeNull();
  });

  it("joins quick changes of one control into one step, but not slow ones or another control", () => {
    const h = new EditHistory<string>(100, 1500);
    h.record("a", "width", 0);
    h.record("b", "width", 500);
    h.record("c", "width", 1000); // one slider drag: a → d
    expect(h.undo("d")).toBe("a");
    expect(h.canUndo).toBe(false);
    h.record("a", "width", 5000);
    h.record("b", "width", 7000); // too late: a new step
    h.record("c", "color", 7100); // another control
    expect(h.undo("d")).toBe("c");
    expect(h.undo("c")).toBe("b");
    expect(h.undo("b")).toBe("a");
  });

  it("keeps at most `limit` steps", () => {
    const h = new EditHistory<number>(3);
    for (let i = 0; i < 10; i++) h.record(i, undefined, i * 10_000);
    expect([h.undo(10), h.undo(9), h.undo(8), h.undo(7)]).toEqual([9, 8, 7, null]);
  });

  it("reads Ctrl+Z, Ctrl+Y, Ctrl+Shift+Z and the Russian layout", () => {
    const k = (key: string, shiftKey = false, ctrlKey = true) => historyKey({ key, ctrlKey, metaKey: false, shiftKey });
    expect([k("z"), k("Z", true), k("y"), k("я"), k("н"), k("z", false, false), k("s")]).toEqual(["undo", "redo", "redo", "undo", "redo", null, null]);
  });
});

describe("default style of a field by mask", () => {
  it("AU* applies to AU_CUT of another table when the layer is added", () => {
    const l = layerFor(tables, "урезка", "AU_CUT", 1, [auStyle]);
    expect(l?.auto?.uid).toBe("u11");
    expect(l?.track).toMatchObject({ table: "урезка", field: "AU_CUT", type: "color", form: "panel", side: "left", style: { uid: "u11", linked: false, auto: true } });
    // the same style for AU of the samples; CU and text fields stay as they are
    expect(layerFor(tables, "пробы", "AU", 0, [auStyle])?.auto?.uid).toBe("u11");
    expect(layerFor(tables, "пробы", "CU", 0, [auStyle])?.auto).toBeNull();
    expect(layerFor(tables, "урезка", "LITH", 0, [auStyle])?.track.style).toBeUndefined();
  });

  it("takes the most exact mask: the exact name, then fewer stars, then a longer literal part, then the newer style", () => {
    const s = (id: number, masks: string[], updated_at = "2026-01-01"): StyleRec => ({ ...auStyle, id, uid: `u${id}`, default_for: masks, updated_at } as StyleRec);
    expect(defaultStyleFor("AU_CUT", "number", [s(1, ["*"]), s(2, ["AU*"]), s(3, ["AU_*"])])?.id).toBe(3);
    expect(defaultStyleFor("AU_CUT", "number", [s(1, ["AU_CUT"]), s(2, ["AU_*"])])?.id).toBe(1);
    expect(defaultStyleFor("AU_CUT", "number", [s(1, ["*U*"]), s(2, ["A?_*"])])?.id).toBe(2);
    expect(defaultStyleFor("AU_CUT", "number", [s(1, ["AU*"], "2026-01-01"), s(2, ["AU*"], "2026-02-01")])?.id).toBe(2);
    expect(nameMatches("A?", "AU")).toBe(true);
    expect(nameMatches("A?", "AUX")).toBe(false);
    expect(defaultStyleFor("AU_CUT", "text", [s(1, ["AU*"])])).toBeNull();
  });

  it("starts a new display of a form with the asked field or the first suitable one", () => {
    expect(firstLayer("grade", tables, [])?.track).toMatchObject({ table: "пробы", field: "AU", type: "bars", log: true, colorBy: "single" });
    expect(firstLayer("lith", tables, [])?.track).toMatchObject({ table: "пробы", field: "Номер пробы", type: "color_text", form: "tube" });
    expect(firstLayer("event", tables, [])?.track).toMatchObject({ table: "структуры", field: "TYPE", type: "event" });
    expect(firstLayer("grade", tables, [auStyle], { table: "Урезка", field: "au_cut" })?.track).toMatchObject({ table: "урезка", field: "AU_CUT", style: { uid: "u11" } });
    expect(firstLayer("grade", tables, [], { table: "Пробы", field: "NOPE" })).toBeNull();
    expect(firstLayer("event", [assay], [])).toBeNull();
  });
});

describe("templates, arranging and saving", () => {
  const track = (p: Partial<Track>): Track => ({ id: "t", table: "пробы", field: "AU", type: "bars", width: 90, color: "#ff0000", min: "", max: "", log: false, boundaries: false, ...p });
  const display = (tracks: Track[], extra: Partial<StripLogSettings> = {}): StripLogSettings => ({ ...DEFAULT_STRIPLOG, allHoles: true, tracks, ...extra });

  it("applies a template to the table that has its fields, by name without regard to case", () => {
    const tpl: StyleRec = { id: 20, uid: "u20", name: "Шаблон", level: "display", data_kind: "mixed", default_for: [], body: { tracks: [{ field: "lith", type: "color", form: "tube" }, { field: "au_cut", type: "line", color: "#00ff00" }, { field: "AS", type: "bars" }] } };
    const r = templateLayers(tpl, tables);
    expect(r?.table.key).toBe("урезка");
    expect(r?.tracks.map((t) => [t.table, t.field, t.type])).toEqual([["урезка", "LITH", "color"], ["урезка", "AU_CUT", "line"]]);
    expect(new Set(r?.tracks.map((t) => t.id)).size).toBe(2);
    expect(templateLayers({ ...tpl, body: { tracks: [{ field: "XX" }] } }, tables)).toBeNull();
    expect(templateLayers({ ...tpl, body: { tracks: [{ field: "XX" }] } }, tables, assay)?.tracks).toEqual([]);
  });

  it("arranges layers: the tube on the hole, the rest right and left by turns", () => {
    const s = arrange(display([track({ id: "a", type: "color", form: "tube", offsetM: 3 }), track({ id: "b", split: 1 }), track({ id: "c", side: "both" }), track({ id: "d", type: "event" })]));
    expect(s.tracks.map((t) => [t.id, t.side, t.offsetM, t.split])).toEqual([["a", undefined, undefined, undefined], ["b", "right", undefined, null], ["c", "left", undefined, null], ["d", undefined, undefined, undefined]]);
  });

  it("saves without the mark of an automatic style, names without a clash, links mapped", () => {
    const s = display([track({ style: { uid: "u11", name: "Au", linked: true, auto: true } })], { template: { uid: "u20", name: "T", linked: false } });
    expect(cleanForSave(s).tracks[0].style).toEqual({ uid: "u11", name: "Au", linked: true, auto: undefined });
    expect(uniqueName("AU · столбцы", ["au · столбцы", "AU · столбцы (2)"])).toBe("AU · столбцы (3)");
    expect(uniqueName("CU", ["AU"])).toBe("CU");
    const dropped = mapRefs(s, (r) => (r?.uid === "u11" ? undefined : r));
    expect(dropped.tracks[0].style).toBeUndefined();
    expect(dropped.template?.uid).toBe("u20");
    expect(sameSettings(s, JSON.parse(JSON.stringify(s)))).toBe(true);
    expect(sameSettings(s, cleanForSave(s))).toBe(false);
    expect(formOf(display([track({ type: "color_text" })]))).toBe("lith");
    expect(formOf(display([track({})], { form: "event" }))).toBe("event");
  });

  it("a linked layer redraws when its style changes; a deleted style leaves the copy", () => {
    const linked = display([track({ style: { uid: "u11", name: "Au", linked: true } })]);
    expect(resolveTracks(linked, [auStyle]).tracks[0].type).toBe("color");
    expect(resolveTracks(linked, [{ ...auStyle, body: { ...auStyle.body, type: "line" } }]).tracks[0].type).toBe("line");
    expect(resolveTracks(linked, []).tracks[0]).toMatchObject({ type: "bars", style: { linked: false } });
  });
});

describe("displays switched on in the view", () => {
  it("shows, hides and drops displays by operations", () => {
    let v: unknown = [{ chart: 3, visible: true }, { chart: 3, visible: false }, { chart: "x" }, null];
    expect(displayEntries(v)).toEqual([{ chart: 3, visible: true }]);
    v = showDisplay(5, true)(v);
    v = showDisplay(3, false)(v);
    expect(v).toEqual([{ chart: 3, visible: false }, { chart: 5, visible: true }]);
    expect(isShown(v, 5)).toBe(true);
    expect(isShown(v, 3)).toBe(false);
    expect(dropDisplay(3)(v)).toEqual([{ chart: 5, visible: true }]);
  });

  it("keeps at most 200 entries, dropping hidden ones first", () => {
    let v: unknown = Array.from({ length: 200 }, (_, i) => ({ chart: i + 1, visible: i !== 50 }));
    v = showDisplay(1000, true)(v);
    const list = displayEntries(v);
    expect(list).toHaveLength(200);
    expect(list.some((e) => e.chart === 51)).toBe(false);
    expect(list[list.length - 1]).toEqual({ chart: 1000, visible: true });
  });

  it("keys tables by the name key of the core", () => {
    expect(nameKey("  Пробы ")).toBe("пробы");
    expect(nameKey("Урезка")).toBe(nameKey("урезка"));
  });
});
