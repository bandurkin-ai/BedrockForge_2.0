// The window «Блочная модель · …» (ТЗ 8.7.6, 6.8; ADR 0023 §11, decisions 16–19; Э17 17.34): what its controls write into the style of the
// model (`block_styles`) is the request the core was read against — the REAL answers of the routes (`viz_answers.json`: the request of
// every case, its header with the resolved range, the labels, the bins; `layer_fixture.json`: LOD; `window_answers.json`: the details of a
// model turned by 30° with sub-blocks, the library of filters). «Взять из данных», the breaks, the categories and «скрыть», the palettes
// (colours never ask the core), the limit of faces, the words of a wrong value in Russian at its field, the status «упрощено, LOD», the
// reading of the details (turn, sub-blocks, roles, checks) and what a reopened project brings back.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import type { BlockModelInfo } from "../src/modules/blocks/api";
import {
  BLOCK_STYLES_KEY,
  blocksShowStore,
  colourIssue,
  hiddenCodes,
  legendStyleOf,
  readStyle,
  requestKey,
  resetShow,
  setShowInfo,
  shellRequest,
  showText,
  styleIn,
} from "../src/modules/blocks/display";
import { blocksStore } from "../src/modules/blocks/state";
import { filtersStore } from "../src/modules/blocks/filters/state";
import { updateView, viewStore, viewSyncOf } from "../src/modules/scene/bind";
import { decodeBlocks, type BlocksHeader } from "../src/viz/blocks/decode";
import { legendItems } from "../src/viz/blocks/legend";
import {
  commitBound,
  commitBreaks,
  commitLimit,
  commitWidth,
  edgesChoice,
  resetRange,
  setCategoriesShown,
  setCategory,
  setClassColour,
  setEdgeColour,
  setEdges,
  setField,
  setKind,
  setLog,
  setMode,
  setOpacity,
  setPalette,
  takeRange,
} from "../src/modules/blocks/window/edits";
import { PALETTES, paletteColours, palettesFor, PROJECT_PALETTE } from "../src/modules/blocks/window/palettes";
import {
  breaksText,
  categoryRows,
  classCount,
  classRows,
  classText,
  colourIssueAt,
  effectiveKind,
  limitText,
  lodText,
  paletteShown,
  parseBound,
  parseLimit,
  parseWidth,
  rangeText,
  takeFromData,
  takeReason,
  timeText,
} from "../src/modules/blocks/window/style";

const here = (p: string) => JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/blocks", p), "utf-8"));
const VIZ = here("viz_answers.json") as { cases: { name: string; request: Record<string, unknown>; header?: BlocksHeader }[] };
const LAYER = here("layer_fixture.json") as { cases: { name: string; bfa1: string }[] };
const WIN = here("window_answers.json");
const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const viz = (name: string) => VIZ.cases.find((c) => c.name === name)!;
const requestOf = (name: string) => {
  const { channel: _c, seq: _s, ...rest } = viz(name).request;
  return rest;
};

const IDS = WIN.ids as { turned: number; plain: number };
// the list has the grid and the fields; the details — the labels and the statistics
const LIST = WIN.blocks.body.block_models as BlockModelInfo[];
const TURNED = LIST.find((b) => b.id === IDS.turned)!;
const PLAIN = LIST.find((b) => b.id === IDS.plain)!;
const T = WIN.turned.body as BlockModelInfo;
const P = WIN.plain.body as BlockModelInfo;

let app: App;
const sent: { method: string; path: string; body: Record<string, unknown> }[] = [];

beforeEach(() => {
  sent.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url).pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      sent.push({ method, path, body });
      if (path.startsWith("/view/")) return json({ version: 1, value: body.value });
      return json({ code: "not_found", message: "Нет", hint: "" }, 404);
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  viewStore.set({ [BLOCK_STYLES_KEY]: undefined });
  app = createApp({ persist: false });
  app.store.set({ projectName: "Проект", projectReadOnly: false, selected: null });
  blocksStore.set({ list: [TURNED, PLAIN], loaded: true, error: null, details: { [IDS.turned]: T, [IDS.plain]: P }, detailErrors: {} });
  filtersStore.set({ rows: [], loaded: true, selected: null, edit: null });
  resetShow();
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
});

const style = (id: number) => styleIn(viewStore.get(), id);
const model = (id: number) => blocksStore.get().details[id];
/** The request of the display as the layer would send it now (without the limit: the recorded requests have the core's default). */
const request = (id: number) => {
  const { limit: _l, ...rest } = shellRequest(style(id), model(id).fields);
  return rest;
};
const key = (id: number) => requestKey(id, 1, null, shellRequest(style(id), model(id).fields));
const header = (name: string): BlocksHeader => viz(name).header!;
const AU = T.fields.find((f) => f.name === "AU")!;

describe("what the controls write is the request the core was read against", () => {
  it("scale: field, «от», «до», logarithm and the quick restrictor give the recorded request (the turned model, 30°)", async () => {
    expect(setField(1, T, "AU")).toBeNull();
    expect(style(1).colour).toMatchObject({ field: "AU", kind: "scale", from: null, to: null });
    expect(commitBound(1, T, "from", "0,1")).toBeNull();
    expect(commitBound(1, T, "to", "10")).toBeNull();
    expect(setLog(1, T, true)).toBeNull();
    expect((await app.registry.execute("blocks.quick", { model_id: 1, field: "OK", op: ">", value: "0,5" })).ok).toBe(true);
    expect(request(1)).toEqual(requestOf("shell_scale_turned"));
  });

  it("scale without bounds is the request of «диапазон по умолчанию»: the core takes the minimum and the maximum", () => {
    setField(2, P, "AU");
    expect(request(2)).toEqual({ ...requestOf("shell_scale_default_range"), mode: "shell" });
    expect(style(2).colour).toMatchObject({ from: null, to: null, kind: "scale" });
  });

  it("breaks: «0,5; 1; 2» is the recorded request of the classes; a field of a number is a scale unless «Градации» is chosen", () => {
    setField(1, T, "AU");
    expect(setKind(1, T, "classes")).toBeNull();
    expect(commitBreaks(1, T, "0,5; 1; 2")).toBeNull();
    expect(request(1)).toEqual(requestOf("shell_classes"));
    expect(style(1).colour.breaks).toEqual([0.5, 1, 2]);
    expect(breaksText(style(1).colour.breaks)).toBe("0,5; 1; 2");
    // back to a scale: the breaks are not sent
    setKind(1, T, "scale");
    expect(request(1).colour).toEqual({ field: "AU", kind: "scale" });
  });

  it("categories: the field of a category is coloured by codes; «скрыть» is `hidden` of the recorded request, «показать все» takes it off", () => {
    setField(1, T, "ROCK");
    expect(style(1).colour.kind).toBe("categories");
    expect(setCategory(1, 1, { on: false })).toBeNull();
    expect(request(1)).toEqual(requestOf("shell_categories_hidden"));
    expect(hiddenCodes(style(1).colour)).toEqual([1]);
    setCategoriesShown(1, [0, 1, 2, 3, 4], false);
    expect(hiddenCodes(style(1).colour)).toEqual([0, 1, 2, 3, 4]);
    setCategoriesShown(1, [0, 1, 2, 3, 4], true);
    expect(hiddenCodes(style(1).colour)).toEqual([]);
    expect((request(1).colour as { hidden?: number[] }).hidden).toBeUndefined();
  });

  it("«все блоки» without colouring; the mode is the request's `mode`", () => {
    expect(setMode(2, "all")).toBeNull();
    expect(request(2)).toMatchObject({ mode: "all" });
    expect(style(2).mode).toBe("all");
    expect(style(2).visible).toBe(false); // the mode of a hidden model does not show it
  });

  it("the field of a category keeps its range off the request and a number never gets categories: the kind follows the field", () => {
    setField(1, T, "AU");
    commitBound(1, T, "to", "5");
    setField(1, T, "ROCK");
    expect(style(1).colour).toMatchObject({ field: "ROCK", kind: "categories", from: null, to: null, breaks: [] });
    setField(1, T, "AU");
    expect(style(1).colour).toMatchObject({ field: "AU", kind: "scale", from: null, to: null });
    expect(setField(1, T, null)).toBeNull();
    expect(request(1).colour).toEqual({ field: null, kind: "none" });
    const bad = setField(1, T, "NOPE");
    expect(bad).toEqual({ field: "field", text: "Поля «NOPE» нет в этой модели. Выберите поле из списка." });
  });
});

describe("«Взять из данных»", () => {
  it("takes the range the core resolved (the header of the last answer) — the very floats — and the request then carries it", () => {
    setField(2, P, "AU");
    const h = header("shell_scale_default_range");
    setShowInfo(2, { status: "ready", header: h, maxClass: 255 });
    const info = blocksShowStore.get().models[2];
    expect(takeReason(style(2).colour, P.fields[0], info)).toBeNull();
    expect(takeRange(2, P, info)).toBeNull();
    expect(style(2).colour.from).toBe(h.colour.from);
    expect(style(2).colour.to).toBe(h.colour.to);
    expect(typeof h.colour.from).toBe("number");
    expect(request(2).colour).toEqual({ field: "AU", kind: "scale", from: h.colour.from, to: h.colour.to });
    expect(rangeText(info)).toBe(`от ${(h.colour.from as number).toLocaleString("ru-RU", { maximumFractionDigits: 4 })} до ${(h.colour.to as number).toLocaleString("ru-RU", { maximumFractionDigits: 4 })}`);
    expect(takeFromData(style(2).colour, P.fields[0], info)).toEqual({ colour: { from: h.colour.from, to: h.colour.to } });
    // «Сбросить»: the core takes the minimum and the maximum again
    expect(resetRange(2)).toBeNull();
    expect(style(2).colour).toMatchObject({ from: null, to: null });
  });

  it("says why it cannot, in Russian: no answer yet, another field, not a scale, no field", () => {
    setField(2, P, "AU");
    const none = takeRange(2, P, undefined);
    expect(none).toEqual({ field: "from", text: "Границы придут от ядра после показа: включите «Показать в 3D»." });
    setShowInfo(2, { status: "ready", header: header("shell_categories_hidden"), maxClass: 4 });
    const other = takeReason(style(2).colour, P.fields[0], blocksShowStore.get().models[2]);
    expect(other).toBe("Показ ещё считается для другого поля: подождите обновления.");
    setKind(2, P, "classes");
    expect(takeReason(style(2).colour, P.fields[0], blocksShowStore.get().models[2])).toBe("Границы берутся для раскраски шкалой по числовому полю.");
    setField(2, P, null);
    expect(takeReason(style(2).colour, undefined, undefined)).toBe("Границы берутся для раскраски шкалой по числовому полю.");
    expect(style(2).colour.from).toBeNull(); // nothing was written
  });

  it("a logarithmic scale of the answer keeps the flag; a log scale cannot start at zero — refused, not written", () => {
    setField(1, T, "AU");
    expect(setLog(1, T, true)).toBeNull();
    expect(commitBound(1, T, "from", "0")).toEqual({ field: "from", text: "Логарифмическая шкала требует границы «от» больше нуля. Задайте границу больше нуля или выключите логарифмическую шкалу." });
    expect(style(1).colour.from).toBeNull();
    expect(commitBound(1, T, "from", "0,1")).toBeNull();
    expect(rangeText({ model: 1, status: "ready", header: header("shell_scale_turned"), maxClass: 255, warnings: [], error: null, ms: 1, requests: 1 })).toMatch(/от 0,1 до 10 \(логарифмическая шкала\)/);
  });
});

describe("the words of a wrong value, at its field", () => {
  it("a bound that is not a number, a range backwards, breaks out of order or not numbers are not written", () => {
    setField(1, T, "AU");
    expect(parseBound("много", "from")).toMatchObject({ ok: false, issue: { field: "from" } });
    expect(commitBound(1, T, "to", "около пяти")).toMatchObject({ field: "to", text: expect.stringMatching(/Граница шкалы «до»: нужно число/) });
    commitBound(1, T, "from", "5");
    expect(commitBound(1, T, "to", "3")).toEqual({ field: "to", text: "Граница шкалы «до» должна быть больше границы «от»." });
    expect(style(1).colour).toMatchObject({ from: 5, to: null });
    // the other bound is moved first: the range stays right
    expect(commitBound(1, T, "to", "30")).toBeNull();
    expect(commitBound(1, T, "from", "40")).toMatchObject({ field: "from" });
    setKind(1, T, "classes");
    expect(commitBreaks(1, T, "2; 1")).toEqual({ field: "breaks", text: "Границы градаций: от 1 до 255 чисел строго по возрастанию. Например 0,5; 1; 2." });
    expect(commitBreaks(1, T, "0,5; два")).toEqual({ field: "breaks", text: "Границы градаций: нужны числа через «;». Например 0,5; 1; 2." });
    expect(style(1).colour.breaks).toEqual([]);
  });

  it("a stored style that is wrong is told at its field with the very words of the layer's status (`colourIssue`)", () => {
    const wrong = [
      readStyle({ colour: { kind: "scale", field: null } }).colour,
      readStyle({ colour: { kind: "scale", field: "NOPE" } }).colour,
      readStyle({ colour: { kind: "classes", field: "AU", breaks: [] } }).colour,
      readStyle({ colour: { kind: "classes", field: "AU", breaks: [2, 1] } }).colour,
      readStyle({ colour: { kind: "scale", field: "AU", from: 5, to: 1 } }).colour,
      readStyle({ colour: { kind: "scale", field: "AU", from: 0, log: true } }).colour,
    ];
    const at = wrong.map((c) => colourIssueAt(c, T));
    expect(at.map((i) => i?.field)).toEqual(["field", "field", "breaks", "breaks", "to", "from"]);
    wrong.forEach((c, i) => expect(at[i]!.text).toBe(colourIssue(c, T)));
    expect(colourIssueAt(readStyle({ colour: { kind: "scale", field: "AU" } }).colour, T)).toBeNull();
    // an edit of another field is not blamed for the old fault
    updateView(BLOCK_STYLES_KEY, () => ({ "1": { colour: { kind: "scale", field: "AU", from: 5, to: 1 } } }));
    expect(setLog(1, T, false)).toBeNull();
    expect(commitBound(1, T, "to", "0")).toMatchObject({ field: "to" });
    expect(commitBound(1, T, "to", "9")).toBeNull();
  });

  it("the limit of faces is in millions, 0,5–10; the width of the edges, the opacity and a colour — wrong ones are refused", () => {
    expect(parseLimit("0,5")).toEqual({ ok: true, value: 500_000 });
    expect(parseLimit("10")).toEqual({ ok: true, value: 10_000_000 });
    expect(limitText(5_000_000)).toBe("5");
    expect(limitText(500_000)).toBe("0,5");
    for (const t of ["0,4", "11", "много", ""]) expect(parseLimit(t)).toMatchObject({ ok: false, issue: { field: "limit", text: "Лимит граней — от 0,5 до 10 млн. Задайте число граней в этих пределах, например 5." } });
    expect(commitLimit(1, "12")).toMatchObject({ field: "limit" });
    expect(style(1).limit).toBe(5_000_000);
    expect(commitLimit(1, "2,5")).toBeNull();
    expect(style(1).limit).toBe(2_500_000);
    expect(commitWidth(1, "0")).toMatchObject({ field: "edge_width" });
    expect(parseWidth("")).toEqual({ ok: true, value: null });
    expect(commitWidth(1, "1,5")).toBeNull();
    expect(style(1).edges.width).toBe(1.5);
    expect(setOpacity(1, 0.01)).toEqual({ field: "general", text: "Непрозрачность — число от 0,05 до 1. Например 0,6." });
    expect(setOpacity(1, 0.6)).toBeNull();
    expect(style(1).opacity).toBe(0.6);
    expect(setEdgeColour(1, "красный")).toEqual({ field: "general", text: "Цвет рёбер — в виде #RRGGBB, например #1B222B." });
    expect(setEdgeColour(1, "#1B222B")).toBeNull();
    expect(style(1).edges.colour).toBe("#1b222b");
    expect(setCategory(1, 0, { colour: "синий" })).toEqual({ field: "general", text: "Цвет — в виде #RRGGBB, например #1F6BFF." });
    expect(setClassColour(1, 0, "x")).toMatchObject({ field: "general" });
  });
});

describe("the colours never ask the core; what decides the faces does", () => {
  it("palette, a colour, the edges, the opacity — the same request key; the field, the range, the breaks, hide, the mode, the limit — a new one", () => {
    setField(1, T, "AU");
    commitBound(1, T, "from", "0,1");
    const base = key(1);
    expect(setPalette(1, T, "ramp.heat")).toBeNull();
    setClassColour(1, 5, "#112233");
    setEdges(1, "on");
    setEdgeColour(1, "#222222");
    setOpacity(1, 0.4);
    expect(edgesChoice(style(1))).toBe("on");
    expect(key(1)).toBe(base);
    commitBound(1, T, "to", "9");
    const ranged = key(1);
    expect(ranged).not.toBe(base);
    setMode(1, "all");
    expect(key(1)).not.toBe(ranged);
    setMode(1, "shell");
    commitLimit(1, "3");
    expect(key(1)).not.toBe(ranged);
    setEdges(1, "mode");
    expect(style(1).edges.on).toBeNull();
    setField(1, T, "ROCK");
    const cats = key(1);
    setCategory(1, 2, { colour: "#e07020" });
    expect(key(1)).toBe(cats);
    setCategory(1, 2, { on: false });
    expect(key(1)).not.toBe(cats);
  });

  it("a palette fills the colours of the legend: a scale — 256, breaks — one each, categories — one per code; «Шкала проекта» takes them off", () => {
    setField(1, T, "AU");
    expect(paletteShown(style(1).colour)).toBe(PROJECT_PALETTE);
    setPalette(1, T, "ramp.viridis");
    expect(style(1).colour.colours).toHaveLength(256);
    expect(style(1).colour.palette).toBe("ramp.viridis");
    expect(paletteShown(style(1).colour)).toBe("ramp.viridis");
    expect(style(1).colour.colours).toEqual(paletteColours("ramp.viridis", "scale", 256));
    expect(style(1).colour.colours[0]).toBe("#440254");
    expect(legendStyleOf(style(1).colour).colors).toHaveLength(256);
    // «Градации»: the chosen palette is filled again for the new classes (they would be coloured by the old 256)
    setKind(1, T, "classes");
    expect(style(1).colour.colours).toEqual([]); // no breaks yet: no classes
    commitBreaks(1, T, "0,5; 1; 2");
    expect(style(1).colour.colours).toHaveLength(4);
    expect(style(1).colour.colours).toEqual(paletteColours("ramp.viridis", "classes", 4));
    commitBreaks(1, T, "0,5; 1");
    expect(style(1).colour.colours).toHaveLength(3);
    // a field of a category: its codes get the palette's colours, «показать» stays
    setField(1, T, "ROCK");
    expect(setPalette(1, T, "set.brand")).toBeNull();
    expect(Object.keys(style(1).colour.categories)).toEqual(["0", "1", "2", "3", "4"]);
    setCategory(1, 3, { on: false });
    setPalette(1, T, "set.soft");
    expect(style(1).colour.categories["3"]).toEqual({ colour: "#76b7b2", on: false });
    expect(style(1).colour.colours).toEqual([]);
    expect(setPalette(1, T, PROJECT_PALETTE)).toBeNull();
    expect(Object.values(style(1).colour.categories).every((c) => c.colour === null)).toBe(true);
    expect(style(1).colour.categories["3"].on).toBe(false);
    expect(paletteShown(style(1).colour)).toBe(PROJECT_PALETTE);
  });

  it("the palettes that fit: a ramp does not colour categories, the discrete ones colour classes and categories; the names are Russian", () => {
    expect(palettesFor("scale").map((p) => p.id)).toEqual([PROJECT_PALETTE, "ramp.viridis", "ramp.heat", "ramp.cool", "ramp.diverging", "ramp.grey"]);
    expect(palettesFor("categories").map((p) => p.id)).toEqual([PROJECT_PALETTE, "set.brand", "set.soft"]);
    expect(palettesFor("classes").map((p) => p.id)).toEqual(PALETTES.map((p) => p.id));
    expect(PALETTES.every((p) => /[А-Яа-я]/.test(p.title))).toBe(true);
    expect(paletteColours("ramp.grey", "classes", 2)).toEqual(["#f2f2f2", "#1e222b"]);
    expect(paletteColours(PROJECT_PALETTE, "scale", 256)).toBeNull();
    expect(paletteColours("ramp.heat", "categories", 3)).toBeNull();
  });
});

describe("the rows of the window read from the answers", () => {
  it("the categories of the answer with the colour, «показать» and the blocks of each (statistics of the field)", () => {
    setField(1, T, "ROCK");
    setCategory(1, 1, { on: false });
    setCategory(1, 2, { colour: "#E07020" });
    const rock = T.fields.find((f) => f.name === "ROCK")!;
    const rows = categoryRows(rock, header("shell_categories_hidden").labels, style(1).colour);
    expect(rows.map((r) => r.label)).toEqual(["руда", "порода", "окисленная", "дайка", "вода"]);
    expect(rows.map((r) => r.on)).toEqual([true, false, true, true, true]);
    expect(rows[2]).toMatchObject({ colour: "#e07020", custom: true });
    expect(rows[0].custom).toBe(false);
    expect(rows[0].colour).toMatch(/^#[0-9a-f]{6}$/);
    expect(rows.reduce((a, r) => a + (r.blocks ?? 0), 0)).toBe((rock.stats as { count: number }).count);
    expect(rows.every((r) => r.blocks !== null)).toBe(true);
    // a code the labels do not know is still a row
    expect(categoryRows(rock, null, { ...style(1).colour, categories: { "7": { colour: null, on: false } } }).map((r) => r.label).slice(-2)).toEqual(["код 6", "код 7"]);
  });

  it("the classes of the breaks: «до 0,5», «от 0,5 до 1», …, «от 2» — the bins of the answer", () => {
    setField(1, T, "AU");
    setKind(1, T, "classes");
    commitBreaks(1, T, "0,5; 1; 2");
    const rows = classRows(style(1).colour);
    expect(rows.map((r) => classText(r))).toEqual(["до 0,5", "от 0,5 до 1", "от 1 до 2", "от 2"]);
    expect(rows.map((r) => [r.from, r.to])).toEqual(header("shell_classes").bins);
    setClassColour(1, 2, "#AA0000");
    expect(classRows(style(1).colour)[2]).toMatchObject({ colour: "#aa0000", custom: true });
    expect(classRows(readStyle({}).colour)).toEqual([]);
    expect(classCount(style(1).colour, AU)).toBe(4);
    expect(effectiveKind(style(1).colour, AU)).toBe("classes");
  });

  it("the legend of the answer follows the colours of the style: legendItems with legendStyleOf", () => {
    setField(1, T, "ROCK");
    setCategory(1, 0, { colour: "#112233" });
    const h = header("shell_categories_hidden");
    const items = legendItems(h, 4, legendStyleOf(style(1).colour));
    expect(items.map((i) => i.label)).toEqual(h.labels);
    expect(items[0].color).toEqual([0x11, 0x22, 0x33]);
  });
});

describe("«упрощено, LOD» and the time", () => {
  it("a simplified answer says it, with the parents and the faces; the time and the requests of the model", () => {
    const lod = LAYER.cases.find((c) => c.name === "lod")!;
    const d = decodeBlocks(Buffer.from(lod.bfa1, "base64"));
    expect(d.header.simplified).toBe(true);
    setShowInfo(1, { status: "ready", header: d.header, maxClass: d.maxClass, ms: 187, requests: 4 });
    const info = blocksShowStore.get().models[1];
    const side = 2 ** d.header.lod;
    const text = lodText(info)!;
    expect(text).toMatch(new RegExp(`^Упрощено \\(LOD ${d.header.lod}\\): родители ${side} × ${side} × ${side}`));
    expect(text).toContain("граней до упрощения");
    expect(timeText(info)).toBe("Последний запрос 187 мс; запросов к ядру: 4.");
    expect(showText(info, "Повёрнутая")).toContain("упрощено");
  });

  it("a model drawn as it is, a failure and an answer not yet come: no «упрощено»", () => {
    expect(lodText(undefined)).toBeNull();
    setShowInfo(1, { status: "ready", header: header("shell_classes"), maxClass: 3, ms: null });
    expect(lodText(blocksShowStore.get().models[1])).toBeNull();
    expect(timeText(blocksShowStore.get().models[1])).toBeNull();
    const lod = decodeBlocks(Buffer.from(LAYER.cases.find((c) => c.name === "lod")!.bfa1, "base64"));
    setShowInfo(1, { status: "error", header: lod.header, error: "Фильтр не найден." });
    expect(lodText(blocksShowStore.get().models[1])).toBeNull();
  });
});

describe("what a reopened project brings back", () => {
  it("the style the core keeps is the style of the window: every control's value comes back as it was written", async () => {
    setField(1, T, "AU");
    setKind(1, T, "classes");
    commitBreaks(1, T, "0,5; 1; 2");
    setPalette(1, T, "ramp.cool");
    setMode(1, "all");
    setEdges(1, "off");
    commitLimit(1, "2,5");
    setOpacity(1, 0.5);
    await app.registry.execute("blocks.quick", { model_id: 1, field: "OK", op: ">", value: "0,5" });
    await viewSyncOf()!.flush(BLOCK_STYLES_KEY);
    const stored = sent.filter((s) => s.path === `/view/${BLOCK_STYLES_KEY}`).at(-1)!.body.value;
    const was = style(1);
    viewStore.set({ [BLOCK_STYLES_KEY]: undefined });
    expect(style(1).colour.field).toBeNull();
    viewStore.set({ [BLOCK_STYLES_KEY]: stored });
    expect(style(1)).toEqual(was);
    expect(style(1)).toMatchObject({ mode: "all", limit: 2_500_000, opacity: 0.5, colour: { field: "AU", kind: "classes", breaks: [0.5, 1, 2], palette: "ramp.cool" }, edges: { on: false } });
    expect(style(1).colour.colours).toHaveLength(4);
    expect(style(1).quick.items).toEqual([{ field: "OK", op: ">", value: 0.5 }]);
  });

  it("an entry the window did not write is read tolerantly: a wrong palette, a wrong colour or a limit out of the range are the defaults", () => {
    const s = readStyle({ colour: { field: "AU", kind: "scale", palette: "", colours: ["#zzzzzz", "#ABCDEF"], categories: { "1": { colour: "red", on: false } } }, limit: 3, opacity: 7 });
    expect(s.colour.palette).toBe("viridis"); // the name of a style nothing was chosen for: not a palette of the window
    expect(s.colour.colours).toEqual([null, "#abcdef"]);
    expect(s.colour.categories["1"]).toEqual({ colour: null, on: false });
    expect(s.limit).toBe(5_000_000);
    expect(s.opacity).toBe(1);
    expect(paletteShown(s.colour)).toBe(PROJECT_PALETTE);
    expect(paletteShown(readStyle({ colour: { palette: "ramp.heat" } }).colour)).toBe("ramp.heat");
  });
});
