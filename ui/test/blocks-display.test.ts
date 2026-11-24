// The display of block models in the main 3D (ТЗ 8.7.6, 6.8; ADR 0023 §11, decisions 16–19; Э17 17.31) on the REAL answers
// of the core (`core/tests/data/blocks/viz_answers.json`, made through `POST /viz/blocks/{id}/shell` and `/at` by
// `core/tests/test_blocks_viz.py`; the LOD answer — `layer_fixture.json` of 17.30): the style of a model in the view of
// the project → the body the core got (contract §12); a new request only for what changes the faces; new colours and
// look without a request and without a new geometry; an overtaken answer and a 409 `superseded` dropped without a
// word; «упрощено, LOD» in the state; a click → the face → `/at` of the core (the filter and the restrictor go with
// it) → the block as «Свойства» tell it; a project that opens shows what its view saved; the CPU of 5 million faces.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { encodeBfa1 } from "../src/edit/bfa1";
import { decodeBlocks } from "../src/viz/blocks/decode";
import { BlocksLayer } from "../src/viz/blocks/layer";
import type { ToolHandler, ToolPointer } from "../src/viz/core/toolInput";
import { viewStore } from "../src/modules/scene/bind";
import type { BlockModelInfo } from "../src/modules/blocks/api";
import type { AtFound } from "../src/modules/blocks/showApi";
import {
  BLOCK_STYLES_KEY,
  blocksShowStore,
  blocksSimplified,
  colourIssue,
  DEFAULT_LIMIT,
  legendStyleOf,
  lookOf,
  pickedBlockStore,
  pickedFacts,
  pickedText,
  readStyle,
  requestKey,
  resetShow,
  restrictOf,
  setPicked,
  shellRequest,
  showStatus,
  showText,
  styleIn,
  styleOp,
  type BlockStyle,
  type StylePatch,
} from "../src/modules/blocks/display";
import { BlocksScene, blockLayerKey, channelOf, type BlocksHost } from "../src/modules/blocks/scene";
import { blocksStore } from "../src/modules/blocks/state";
import { filtersStore } from "../src/modules/blocks/filters/state";
import { setCoreTarget } from "../src/shell/api";

interface Case {
  name: string;
  path: string;
  request: Record<string, unknown>;
  status: number;
  header?: Record<string, unknown>;
  bfa1?: string;
  json?: Record<string, unknown>;
}
const VIZ: { cases: Case[] } = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/blocks/viz_answers.json"), "utf-8"));
const LAYER: { cases: { name: string; bfa1: string }[] } = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/blocks/layer_fixture.json"), "utf-8"));
const viz = (name: string): Case => VIZ.cases.find((c) => c.name === name)!;
const bytes = (b64: string): ArrayBuffer => {
  const b = Buffer.from(b64, "base64");
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer;
};
const strip = ({ channel: _c, seq: _s, ...rest }: Record<string, unknown>) => rest;
const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const bfa1 = (b64: string) => new Response(bytes(b64), { status: 200, headers: { "Content-Type": "application/octet-stream" } });
const ver = (g: { getAttribute(n: string): unknown }, n: string): number => (g.getAttribute(n) as { version: number }).version;
const settle = async () => {
  for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0));
};

// ---- models of the answers: 1 turned by 30° with AU, OK, ROCK; 2 plain with AU, ROCK ----
const info = (id: number, fields: BlockModelInfo["fields"], version = 1): BlockModelInfo => ({
  id, name: `Модель ${id}`, folder: null, version, extent: null, crs: null,
  grid: { corner: [0, 0, 0], counts: [8, 6, 4], size: [10, 10, 5], order: "F", variable: false },
  cells: 177, fields, source: null, warnings: [], size: null, created_at: "", updated_at: "",
}); // prettier-ignore
const AU = { name: "AU", kind: "number" as const, unit: "г/т" };
const OK = { name: "OK", kind: "number" as const, unit: null };
const ROCK = { name: "ROCK", kind: "category" as const, unit: null, labels: ["руда", "порода", "окисленная", "дайка", "вода"] };
const M1 = info(1, [AU, OK, ROCK]);
const M2 = info(2, [AU, ROCK]);

// ---- a scene without WebGL: the layers, the invalidations, the handler the chain installs ----
function fakeScene() {
  const layers = new Map<string, { origin: unknown }>();
  const log = { added: [] as string[], removed: [] as string[], invalidated: 0, reveals: [] as unknown[] };
  let handler: ToolHandler | null = null;
  let restored: (() => void) | null = null;
  let ray: { origin: [number, number, number]; dir: [number, number, number] } | null = { origin: [1035, 2025, 500], dir: [0, 0, -1] };
  const host: BlocksHost = {
    addLayer: (k, l) => (layers.set(k, l), log.added.push(k), undefined),
    removeLayer: (k) => (layers.delete(k), log.removed.push(k), undefined),
    invalidate: () => void log.invalidated++,
    clipPlanes: [],
    onRestored: (fn) => ((restored = fn), () => (restored = null)),
    cursorRay: () => ray,
    projectView: () => ({ matrix: new Float64Array(16), width: 800, height: 600 }),
    setHandles: (h) => void (handler = h),
    layerChanged: () => undefined,
    reveal: (b) => (log.reveals.push(b), true),
  };
  const click = (x = 400, y = 300): boolean => handler?.pointer({ type: "click", x, y, clientX: x, clientY: y, button: 0, shift: false, ctrl: false } as ToolPointer) ?? false;
  return { host, layers, log, click, hasHandler: () => handler !== null, restore: () => restored?.(), noRay: () => void (ray = null) };
}

// ---- a small core: answers by a function the test sets; every request is kept ----
interface Sent {
  path: string;
  body: Record<string, unknown>;
}
let sent: Sent[];
let answer: (path: string, body: Record<string, unknown>) => Response | Promise<Response>;

/** The fixture the body of a request is the answer to (the same style → the same real answer). */
function realAnswer(body: Record<string, unknown>): Response {
  const c = body.colour as { kind: string; field: string | null };
  const name = c.kind === "classes" ? "shell_classes" : c.kind === "categories" ? "shell_categories_hidden" : c.kind === "scale" ? (body.restrict ? "shell_scale_turned" : "shell_scale_default_range") : "shell_all_none";
  return bfa1(viz(name).bfa1!);
}

let scene: ReturnType<typeof fakeScene>;
let blocks: BlocksScene;
let picked: number[];

const styleNow = (id: number): BlockStyle => styleIn(viewStore.get(), id);
/** What `updateView("block_styles", styleOp(id, patch))` does to the key, without the sync with the core. */
const change = (id: number, patch: StylePatch) => viewStore.set({ [BLOCK_STYLES_KEY]: styleOp(id, patch)(viewStore.get()[BLOCK_STYLES_KEY]) });
const shellCalls = () => sent.filter((s) => s.path.endsWith("/shell"));

beforeEach(() => {
  sent = [];
  picked = [];
  answer = (path, body) => (path.endsWith("/shell") ? realAnswer(body) : json({ code: "not_found", message: "Нет", hint: "" }, 404));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url).pathname.replace("/api/v1", "");
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      sent.push({ path, body });
      return answer(path, body);
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  viewStore.set({ [BLOCK_STYLES_KEY]: undefined });
  blocksStore.set({ list: [M1, M2], loaded: true, error: null, details: {}, detailErrors: {} });
  filtersStore.set({ rows: [], loaded: true, selected: null, edit: null });
  resetShow();
  setPicked(null);
  scene = fakeScene();
  blocks = new BlocksScene(scene.host, (id) => picked.push(id));
});

afterEach(() => {
  blocks.dispose();
  vi.unstubAllGlobals();
});

describe("the style in the view of the project", () => {
  it("reads what the core keeps, tolerant: the rest is the default; a model not in the view is not shown", () => {
    expect(readStyle(undefined)).toMatchObject({ visible: false, mode: "shell", filter_id: null, limit: DEFAULT_LIMIT, opacity: 1 });
    const s = readStyle({ visible: true, mode: "all", filter_id: 7, colour: { field: "AU", kind: "scale", from: 0.1, to: 10, log: true, categories: { "1": { colour: "#E07020", on: false }, x: {} } }, edges: { on: true, colour: "#202020", width: 2 }, opacity: 0.5, quick: { items: [{ field: "AU", op: ">", value: 1 }, { op: ">" }, { field: "X", op: "??" }], join: "or", invert: true }, limit: 1_000_000 });
    expect(s).toMatchObject({ visible: true, mode: "all", filter_id: 7, opacity: 0.5, limit: 1_000_000, edges: { on: true, colour: "#202020", width: 2 } });
    expect(s.colour.categories).toEqual({ "1": { colour: "#e07020", on: false } });
    expect(s.quick).toEqual({ items: [{ field: "AU", op: ">", value: 1 }], join: "or", invert: true });
    expect(readStyle({ filter_id: "7", limit: 5 })).toMatchObject({ filter_id: null, limit: DEFAULT_LIMIT });
    expect(styleNow(1).visible).toBe(false);
  });

  it("a change merges into the entry stored, keeps its unknown keys", () => {
    const first = styleOp(1, { visible: true, colour: { field: "AU", kind: "scale" } })({ "1": { visible: false, note: "x", colour: { palette: "viridis" } }, "2": { visible: true } }) as Record<string, Record<string, unknown>>;
    expect(Object.keys(first).sort()).toEqual(["1", "2"]);
    expect(first["1"]).toMatchObject({ visible: true, note: "x", mode: "shell", filter_id: null, colour: { field: "AU", kind: "scale", palette: "viridis" } });
    // another window changed the opacity meanwhile: the operation runs on what is stored, both survive
    const second = styleOp(1, { mode: "all" })({ ...first, "1": { ...first["1"], opacity: 0.4 } }) as Record<string, Record<string, unknown>>;
    expect(second["1"]).toMatchObject({ mode: "all", opacity: 0.4, visible: true });
    expect(styleOp(3, { filter_id: 5 })(undefined)).toEqual({ "3": expect.objectContaining({ filter_id: 5, visible: false }) });
  });

  it("the restrictor is the rules of the core: one condition as it is, several joined, «обратный» is НЕ", () => {
    const c = (field: string, v: number) => ({ field, op: ">" as const, value: v });
    expect(restrictOf({ items: [], join: "and", invert: false })).toBeNull();
    expect(restrictOf({ items: [c("AU", 1)], join: "and", invert: false })).toEqual({ cond: c("AU", 1) });
    expect(restrictOf({ items: [c("AU", 1), c("OK", 0)], join: "or", invert: false })).toEqual({ op: "or", items: [{ cond: c("AU", 1) }, { cond: c("OK", 0) }] });
    expect(restrictOf({ items: [c("AU", 1)], join: "and", invert: true })).toEqual({ op: "not", items: [{ cond: c("AU", 1) }] });
  });
});

describe("the request: the style → the body the core got (e17-api §12)", () => {
  const styleOf = (id: number, patch: StylePatch) => readStyle((styleOp(id, { visible: true, ...patch })(undefined) as Record<string, unknown>)[String(id)]);
  const body = (patch: StylePatch, model = M1) => shellRequest(styleOf(model.id, patch), model.fields);
  const expected = (name: string) => ({ ...strip(viz(name).request), limit: DEFAULT_LIMIT });

  it("is the request of the real answers: the scale with the log and the restrictor, the breaks, the hidden categories", () => {
    expect(body({ colour: { field: "AU", kind: "scale", from: 0.1, to: 10, log: true }, quick: { items: [{ field: "OK", op: ">", value: 0.5 }] } })).toEqual(expected("shell_scale_turned"));
    expect(body({ colour: { field: "AU", kind: "classes", breaks: [0.5, 1, 2] } })).toEqual(expected("shell_classes"));
    expect(body({ colour: { field: "ROCK", kind: "categories", categories: { "1": { colour: null, on: false }, "0": { colour: "#ff0000", on: true } } } })).toEqual(expected("shell_categories_hidden"));
    expect(body({ mode: "all" }, M2)).toEqual({ mode: "all", limit: DEFAULT_LIMIT, colour: { field: null, kind: "none" } });
    expect(body({ colour: { field: "AU", kind: "scale" } }, M2)).toEqual({ mode: "shell", limit: DEFAULT_LIMIT, colour: { field: "AU", kind: "scale" } }); // no range: the core takes min / max of the field
  });

  it("the kind follows the field (a category cannot be a scale), the range and the breaks only where they mean something", () => {
    expect(body({ colour: { field: "ROCK", kind: "scale", from: 1, to: 2 } }).colour).toEqual({ field: "ROCK", kind: "categories" });
    expect(body({ colour: { field: "AU", kind: "categories" } }).colour).toEqual({ field: "AU", kind: "scale" });
    expect(body({ colour: { field: "AU", kind: "classes", breaks: [1], from: 0, to: 5 } }).colour).toEqual({ field: "AU", kind: "classes", breaks: [1] });
    expect(body({ colour: { field: null, kind: "scale" } }).colour).toEqual({ field: null, kind: "none" });
    expect(body({ filter_id: 5, limit: 800_000, extent: [0, 0, 0, 10, 10, 10] })).toMatchObject({ filter_id: 5, limit: 800_000, extent: [0, 0, 0, 10, 10, 10] });
  });

  it("colours and look are not in it; the key tells a new request from new colours", () => {
    const a = readStyle({ visible: true, colour: { field: "AU", kind: "scale", colours: [] }, opacity: 1 });
    const b = readStyle({ visible: true, colour: { field: "AU", kind: "scale", colours: ["#ff0000"], palette: "turbo", categories: { "2": { colour: "#00ff00", on: true } } }, opacity: 0.4, edges: { on: true, colour: "#112233", width: 3 } });
    expect(requestKey(1, 1, null, shellRequest(a, M1.fields))).toBe(requestKey(1, 1, null, shellRequest(b, M1.fields)));
    expect(requestKey(1, 2, null, shellRequest(a, M1.fields))).not.toBe(requestKey(1, 1, null, shellRequest(a, M1.fields))); // a recalculated model
    expect(requestKey(1, 1, 4, shellRequest(a, M1.fields))).not.toBe(requestKey(1, 1, 3, shellRequest(a, M1.fields))); // an edited filter
    expect(legendStyleOf(b.colour).colors?.[0]).toBe("#ff0000");
    expect(legendStyleOf(b.colour).colors?.[2]).toBe("#00ff00");
    expect(lookOf(b)).toEqual({ opacity: 0.4, edges: true, edgeColor: "#112233", edgeWidth: 3 });
    expect(lookOf(readStyle({}))).toEqual({ opacity: 1 }); // the edges by the mode of the answer
  });

  it("a colouring the core would refuse is said in the same words before it is sent", () => {
    const c = (p: Partial<BlockStyle["colour"]>) => ({ ...readStyle({}).colour, ...p });
    expect(colourIssue(c({ kind: "scale" }), M1)).toMatch(/нужно поле/);
    expect(colourIssue(c({ kind: "scale", field: "ZZ" }), M1)).toMatch(/Поля «ZZ» нет в этой модели/);
    expect(colourIssue(c({ kind: "classes", field: "AU", breaks: [] }), M1)).toMatch(/Границы градаций/);
    expect(colourIssue(c({ kind: "classes", field: "AU", breaks: [2, 1] }), M1)).toMatch(/по возрастанию/);
    expect(colourIssue(c({ kind: "scale", field: "AU", from: 5, to: 1 }), M1)).toMatch(/«до» должна быть больше/);
    expect(colourIssue(c({ kind: "scale", field: "AU", from: 0, to: 1, log: true }), M1)).toMatch(/больше нуля/);
    expect(colourIssue(c({ kind: "scale", field: "AU", from: 0.1, to: 10, log: true }), M1)).toBeNull();
    expect(colourIssue(c({ kind: "none" }), M1)).toBeNull();
  });
});

describe("the layer follows the view", () => {
  it("a model shown is one request with its channel and seq, one layer `bm:<id>`, the state of the answer", async () => {
    change(1, { visible: true, colour: { field: "AU", kind: "scale", from: 0.1, to: 10, log: true }, quick: { items: [{ field: "OK", op: ">", value: 0.5 }] } });
    expect(blocks.stats().requests).toBe(1);
    expect(showStatus([M1])).toMatch(/запрос показа/);
    await settle();
    expect(sent).toHaveLength(1);
    expect(sent[0].path).toBe("/viz/blocks/1/shell");
    expect(sent[0].body).toMatchObject({ ...strip(viz("shell_scale_turned").request), limit: DEFAULT_LIMIT, channel: channelOf(1) });
    expect(typeof sent[0].body.seq).toBe("number");
    expect([...scene.layers.keys()]).toEqual([blockLayerKey(1)]);
    const shown = blocks.shown();
    expect(shown).toHaveLength(1);
    expect(shown[0]).toMatchObject({ model: 1, mode: "shell", faces: 441, facesRaw: 443, simplified: false, lod: 0, visible: true });
    const st = blocksShowStore.get().models[1];
    expect(st).toMatchObject({ status: "ready", error: null, requests: 1 });
    expect(st.header).toMatchObject({ faces: 441, passed: 211, rows: 225, empty: 14, field: "AU", colour: { kind: "scale", from: 0.1, to: 10, log: true } }); // the range as the core resolved it
    expect(showText(st, "Ресурсная")).toBe("Блоки «Ресурсная»: 441 грань; прошло 211 из 225 блоков");
    expect(blocksSimplified()).toBe(false);
    expect(scene.hasHandler()).toBe(true);
  });

  it("new colours, edges and opacity are no request and no new geometry: the texels of the legend and two uniforms", async () => {
    change(1, { visible: true, colour: { field: "AU", kind: "classes", breaks: [0.5, 1, 2] } });
    await settle();
    const layer = blocks.layerOf(1)!;
    const setStyle = vi.spyOn(layer, "setStyle");
    const setLook = vi.spyOn(layer, "setLook");
    const geo = layer.mesh.geometry;
    const before = ["iC", "iH", "iA", "iK"].map((n) => [geo.getAttribute(n).array, ver(geo, n)] as const);
    change(1, { colour: { colours: ["#ff0000", "#00ff00", "#0000ff", "#ffff00"] } });
    change(1, { opacity: 0.4, edges: { on: true, colour: "#112233", width: 2 } });
    change(1, { colour: { palette: "turbo" } }); // the name of a palette alone changes nothing drawn
    expect(sent).toHaveLength(1);
    expect(blocks.layerOf(1)).toBe(layer);
    expect(setStyle).toHaveBeenCalledTimes(1);
    expect(setLook).toHaveBeenCalledTimes(1);
    expect(layer.look).toMatchObject({ opacity: 0.4, edges: true, edgeColor: "#112233", edgeWidth: 2 });
    ["iC", "iH", "iA", "iK"].forEach((n, i) => {
      expect(geo.getAttribute(n).array).toBe(before[i][0]);
      expect(ver(geo, n)).toBe(before[i][1]); // nothing of the faces went to the GPU again
    });
    expect(blocks.stats()).toMatchObject({ requests: 1, restyles: 2 });
    expect(scene.log.added).toEqual([blockLayerKey(1)]);
  });

  it("what changes the faces is a new request — field, range, log, breaks, hidden categories, filter, restrictor, mode, limit", async () => {
    change(1, { visible: true, colour: { field: "AU", kind: "scale" } });
    await settle();
    const steps: StylePatch[] = [
      { colour: { from: 0.1, to: 10 } },
      { colour: { log: true } },
      { colour: { field: "OK" } },
      { colour: { kind: "classes", breaks: [1, 2] } },
      { colour: { breaks: [1, 3] } },
      { colour: { field: "ROCK", kind: "categories", categories: { "1": { colour: null, on: false } } } },
      { colour: { categories: { "1": { colour: null, on: true } } } },
      { filter_id: 5 },
      { quick: { items: [{ field: "OK", op: ">", value: 0 }] } },
      { mode: "all" },
      { limit: 800_000 },
    ];
    let n = 1;
    for (const step of steps) {
      change(1, step);
      n++;
      expect(sent.length, JSON.stringify(step)).toBe(n);
      await settle();
    }
    // the same request again is not sent: the same key
    change(1, { limit: 800_000 });
    change(1, { colour: { colours: ["#ff0000"] } });
    expect(sent.length).toBe(n);
    // the model recalculated (a new version) and a filter edited (its version) ask again
    blocksStore.set({ list: [{ ...M1, version: 2 }, M2] });
    expect(sent.length).toBe(n + 1);
    filtersStore.set({ rows: [{ id: 5, name: "Руда", folder: null, version: 3, text: "AU > 1", title: "", fields: ["AU"], note: null, created_at: null, updated_at: null }] });
    expect(sent.length).toBe(n + 2);
    await settle();
    expect(blocks.layerOf(1)).not.toBeNull();
    expect(scene.layers.size).toBe(1);
  });

  it("an answer overtaken by a newer request is dropped; a 409 `superseded` is not shown; the old layer stays until the new one is there", async () => {
    const pending: ((r: Response) => void)[] = [];
    answer = (path) => (path.endsWith("/shell") ? new Promise<Response>((res) => pending.push(res)) : json({}, 404));
    change(1, { visible: true, colour: { field: "AU", kind: "classes", breaks: [0.5, 1, 2] } });
    change(1, { colour: { field: "ROCK", kind: "categories" } }); // a newer request of the same channel
    expect(sent).toHaveLength(2);
    expect(sent[1].body.seq as number).toBeGreaterThan(sent[0].body.seq as number);
    expect(sent[1].body.channel).toBe(sent[0].body.channel);
    // the core cancelled the older one: 409, and nothing is said
    pending[0](json({ code: "superseded", message: "Запрос показа устарел: его сменил более новый.", hint: "Ничего делать не нужно: окно показывает последний результат." }, 409));
    await settle();
    expect(blocksShowStore.get().models[1].status).toBe("loading");
    expect(blocksShowStore.get().models[1].error).toBeNull();
    expect(scene.layers.size).toBe(0);
    pending[1](bfa1(viz("shell_categories_hidden").bfa1!));
    await settle();
    expect(blocksShowStore.get().models[1]).toMatchObject({ status: "ready", error: null });
    expect(blocks.shown()[0]).toMatchObject({ faces: 681 });
    // an answer of a request that is not the newest any more, whatever it says, changes nothing
    change(1, { colour: { field: "AU", kind: "classes", breaks: [0.5, 1, 2] } });
    change(1, { colour: { field: "ROCK", kind: "categories", categories: { "1": { colour: null, on: false } } } });
    expect(sent).toHaveLength(4);
    pending[2](bfa1(viz("shell_classes").bfa1!)); // the stale one comes after the newest was sent
    await settle();
    expect(blocks.shown()[0]).toMatchObject({ faces: 681 }); // the old layer stays
    expect(blocksShowStore.get().models[1].status).toBe("loading");
    pending[3](bfa1(viz("shell_categories_hidden").bfa1!));
    await settle();
    expect(blocksShowStore.get().models[1].status).toBe("ready");
    expect(blocks.stats().layers).toBe(1);
  });

  it("«упрощено, LOD»: the state tells the parents and the faces before; the status bar line and the flag follow", async () => {
    answer = () => bfa1(LAYER.cases.find((c) => c.name === "lod")!.bfa1);
    change(2, { visible: true, mode: "all" });
    await settle();
    const st = blocksShowStore.get().models[2];
    expect(st.header).toMatchObject({ simplified: true });
    expect(st.header!.lod).toBeGreaterThan(0);
    expect(st.header!.faces_raw).toBeGreaterThan(st.header!.faces);
    const side = 2 ** st.header!.lod;
    expect(showText(st, "Карьер")).toContain(`упрощено: родители ${side} × ${side} × ${side} (LOD ${st.header!.lod})`);
    expect(showText(st, "Карьер")).toMatch(/граней до упрощения \d/);
    expect(blocksSimplified()).toBe(true);
    expect(showStatus([M2])).toContain("Блоки «Модель 2»");
    expect(blocks.shown()[0]).toMatchObject({ simplified: true });
  });

  it("a failure of the core is its Russian text with the hint, once; the layer on the screen stays; the same request is not sent again", async () => {
    change(1, { visible: true, colour: { field: "AU", kind: "scale" } });
    await settle();
    answer = () => json({ code: "filter_field_missing", message: "В модели нет поля «DOMAIN», которое читает фильтр.", hint: "Выберите другой фильтр или добавьте поле.", param: "filter_id" }, 422);
    change(1, { filter_id: 5 });
    await settle();
    const st = blocksShowStore.get().models[1];
    expect(st.status).toBe("error");
    expect(st.error).toBe("В модели нет поля «DOMAIN», которое читает фильтр. Выберите другой фильтр или добавьте поле.");
    expect(showText(st, "М")).toContain("DOMAIN");
    expect(blocks.layerOf(1)).not.toBeNull();
    expect(blocksSimplified()).toBe(false);
    blocksStore.set({ list: [M1, M2] }); // a store that changed for nothing
    filtersStore.set({ selected: 5 });
    await settle();
    expect(sent).toHaveLength(2);
    // the filter taken off: a new request, the answer is the layer again
    answer = (_p, body) => realAnswer(body);
    change(1, { filter_id: null });
    await settle();
    expect(blocksShowStore.get().models[1]).toMatchObject({ status: "ready", error: null });
  });

  it("a colouring the core would refuse is not sent: the same words, and the model is shown as soon as it is right", async () => {
    change(1, { visible: true, colour: { field: "ZZ", kind: "scale" } });
    expect(sent).toHaveLength(0);
    expect(blocksShowStore.get().models[1]).toMatchObject({ status: "error", error: expect.stringContaining("Поля «ZZ» нет в этой модели") });
    change(1, { colour: { field: "AU" } });
    expect(sent).toHaveLength(1);
    await settle();
    expect(blocksShowStore.get().models[1].status).toBe("ready");
    change(1, { quick: { items: [{ field: "AU", op: ">" }] } }); // a condition without a value
    expect(sent).toHaveLength(1);
    expect(blocksShowStore.get().models[1].error).toMatch(/значени/i);
    change(1, { quick: { items: [{ field: "AU", op: ">", value: 1 }] } });
    expect(sent).toHaveLength(2);
  });

  it("hidden, deleted: the layer, the state and the handler go; the context lost: the layers come again from the answers, no request", async () => {
    change(1, { visible: true });
    change(2, { visible: true, mode: "all" });
    await settle();
    expect(scene.layers.size).toBe(2);
    scene.restore();
    expect(scene.layers.size).toBe(2);
    expect(sent).toHaveLength(2);
    expect(scene.log.added.filter((k) => k === blockLayerKey(1))).toHaveLength(2);
    change(2, { visible: false });
    expect([...scene.layers.keys()]).toEqual([blockLayerKey(1)]);
    expect(blocksShowStore.get().models[2]).toBeUndefined();
    blocksStore.set({ list: [M2] }); // model 1 deleted
    expect(scene.layers.size).toBe(0);
    expect(scene.hasHandler()).toBe(false);
    expect(blocksShowStore.get().models).toEqual({});
  });

  it("a project that opens shows what its view saved (shown models, their filter and colouring), nothing is shown before the list is read", async () => {
    blocks.dispose();
    blocksStore.set({ list: [], loaded: false, error: null, details: {}, detailErrors: {} });
    viewStore.set({ [BLOCK_STYLES_KEY]: { "1": { visible: true, mode: "shell", filter_id: null, colour: { field: "AU", kind: "scale", from: 0.1, to: 10, log: true }, quick: { items: [{ field: "OK", op: ">", value: 0.5 }], join: "and", invert: false } }, "2": { visible: false } } });
    const next = fakeScene();
    blocks = new BlocksScene(next.host);
    expect(sent).toHaveLength(0);
    blocksStore.set({ list: [M1, M2], loaded: true });
    await settle();
    expect(shellCalls().map((s) => s.path)).toEqual(["/viz/blocks/1/shell"]);
    expect([...next.layers.keys()]).toEqual([blockLayerKey(1)]);
    expect(shellCalls()[0].body).toMatchObject({ ...strip(viz("shell_scale_turned").request), limit: DEFAULT_LIMIT });
  });
});

describe("the click: the face → the block of the core → «Выбранный блок»", () => {
  const at = viz("at_found").json!;
  beforeEach(async () => {
    answer = (path, body) => (path.endsWith("/shell") ? realAnswer(body) : path.endsWith("/at") ? json(at) : json({}, 404));
    change(2, { visible: true, mode: "all" });
    await settle();
  });

  it("the handler never takes the click; a face found asks `/at` with the point a hair inside, the outward normal and the model's own filter and restrictor", async () => {
    expect(scene.click()).toBe(false);
    await settle();
    const calls = sent.filter((s) => s.path.endsWith("/at"));
    expect(calls).toHaveLength(1);
    expect(calls[0].path).toBe("/viz/blocks/2/at");
    const b = calls[0].body as { point: number[]; normal: number[]; filter_id?: number; restrict?: unknown };
    expect(Math.abs(b.point[0] - 1035)).toBeLessThan(1e-3);
    expect(Math.abs(b.point[1] - 2025)).toBeLessThan(1e-3);
    expect(b.point[2]).toBeGreaterThan(100);
    expect(b.point[2]).toBeLessThan(120);
    expect(b.normal).toEqual([0, 0, 1]);
    expect(b.filter_id).toBeUndefined();
    expect(b.restrict).toBeUndefined();
    expect(picked).toEqual([2]);
    change(2, { filter_id: 5, quick: { items: [{ field: "AU", op: ">", value: 1 }] } });
    await settle();
    scene.click();
    await settle();
    expect(sent.filter((s) => s.path.endsWith("/at")).at(-1)!.body).toMatchObject({ filter_id: 5, restrict: { cond: { field: "AU", op: ">", value: 1 } } });
  });

  it("the answer of the core is the panel: the cell, the centre, the size, the values with their units, «—» for no value, never 0", async () => {
    scene.click();
    await settle();
    const p = pickedBlockStore.get().picked!;
    expect(p).toMatchObject({ model: 2, status: "found", error: null });
    expect(Math.abs(p.point[0] - 1035)).toBeLessThan(1e-3);
    const facts = Object.fromEntries(pickedFacts(p, M2).map((f) => [f.label, f.value]));
    expect(facts["Ячейка (I, J, K с нуля)"]).toBe("3, 2, 2");
    expect(facts["Центр"]).toBe("X 1035, Y 2025, Z 112,5");
    expect(facts["Размер"]).toBe("10 × 10 × 5 м");
    expect(facts["Фильтр"]).toBe("блок проходит");
    expect(facts["AU"]).toBe("1,4376 г/т");
    expect(facts["ROCK"]).toBe("дайка");
    expect(pickedText(p, M2)).toContain("блок (3, 2, 2)");
    // a block without a value, hidden by the filter, with sub-blocks
    const ans = { ...at, passed: false, f: 0, values: { AU: null, ROCK: null }, sub: [1, 0, 0, 1, 1, 1] };
    const hid = pickedFacts({ ...p, answer: { ...(ans as unknown as AtFound), found: true } }, M2);
    expect(hid.find((f) => f.label === "AU")?.value).toBe("—");
    expect(hid.find((f) => f.label === "Фильтр")?.value).toBe("блок скрыт фильтром");
    expect(hid.find((f) => f.label === "Субблок")?.value).toBe("смещение 1, 0, 0; размер 1 × 1 × 1 подъячеек");
  });

  it("no block there, a failure in Russian, the newest click only; a click on nothing asks nothing", async () => {
    answer = (path) => (path.endsWith("/at") ? json(viz("at_not_found").json) : json({}, 404));
    scene.click();
    await settle();
    expect(pickedBlockStore.get().picked).toMatchObject({ status: "missing" });
    expect(pickedFacts(pickedBlockStore.get().picked, M2)[0]).toEqual({ label: "Блок", value: "в этой точке блока нет" });
    answer = () => json({ code: "block_model_not_found", message: "Блочная модель не найдена.", hint: "Обновите дерево проекта." }, 404);
    scene.click();
    await settle();
    expect(pickedBlockStore.get().picked).toMatchObject({ status: "error", error: "Блочная модель не найдена. Обновите дерево проекта." });
    // two clicks: the older answer comes last and does not overwrite
    const waiting: ((r: Response) => void)[] = [];
    answer = () => new Promise<Response>((res) => waiting.push(res));
    scene.click();
    scene.click();
    waiting[1](json({ ...at, row: 2 }));
    await settle();
    waiting[0](json({ ...at, row: 1 }));
    await settle();
    expect(pickedBlockStore.get().picked?.answer).toMatchObject({ row: 2 });
    const n = sent.length;
    scene.noRay();
    scene.click();
    await settle();
    expect(sent.length).toBe(n);
  });
});

describe("the CPU of 5 million faces (no WebGL): the frame needs no palette, a new colour is 256 texels", () => {
  /** six sides, 50 planes each of 129 × 129 unit squares: 4 992 300 faces sorted as the core sorts them */
  function huge(): ArrayBuffer {
    const per = 129;
    const planes = 50;
    const n = 6 * planes * per * per;
    const c = new Float32Array(3 * n);
    const h = new Float32Array(2 * n).fill(5);
    const a = new Uint8Array(n);
    const k = new Uint16Array(n);
    let i = 0;
    for (let side = 0; side < 6; side++) {
      const axis = side >> 1;
      const [u, v] = [(axis + 1) % 3, (axis + 2) % 3];
      for (let pl = 0; pl < planes; pl++)
        for (let q = 0; q < per; q++)
          for (let p = 0; p < per; p++, i++) {
            c[3 * i + axis] = pl * 10 - 250;
            c[3 * i + u] = p * 10 - 640 + 5;
            c[3 * i + v] = q * 10 - 640 + 5;
            a[i] = side;
            k[i] = (p * 7 + q * 3 + pl) % 256;
          }
    }
    const header = { kind: "blocks", format: 1, model: 1, version: 1, mode: "shell", corner: [512120.4, 7100010.6, 255.2], rotation: 30, origin: [0, 0, 0], faces: n, faces_raw: n, simplified: false, lod: 0, rows: n, passed: n, empty: 0, field: "AU", colour: { kind: "scale", from: 0, to: 1, log: false }, classes: 256, bins: null, labels: null };
    return encodeBfa1(header, [["c", "<f4", c], ["h", "<f4", h], ["a", "<u1", a], ["k", "<u2", k]]);
  }

  it("decode → layer → new colours → new look: the times for the report; the buffers of the faces are not touched", () => {
    const body = huge();
    const t0 = performance.now();
    const d = decodeBlocks(body);
    const t1 = performance.now();
    const layer = new BlocksLayer(d, { clip: [] }, { style: legendStyleOf(readStyle({}).colour) });
    const t2 = performance.now();
    const geo = layer.mesh.geometry;
    const versions = ["iC", "iH", "iA", "iK"].map((n) => ver(geo, n));
    const colours = Array.from({ length: 256 }, (_, j) => `#${((j * 65793) & 0xffffff).toString(16).padStart(6, "0")}`);
    const t3 = performance.now();
    for (let r = 0; r < 20; r++) layer.setStyle({ colors: colours.map((_, j) => colours[(j + r) % 256]) });
    const t4 = performance.now();
    for (let r = 0; r < 20; r++) layer.setLook({ opacity: 0.5 + r / 100, edges: r % 2 === 0 });
    const t5 = performance.now();
    const ms = (a: number, b: number) => Math.round((b - a) * 100) / 100;
    console.log(`blocks 5M faces: decode ${ms(t0, t1)} ms, layer ${ms(t1, t2)} ms, setStyle ${ms(t3, t4) / 20} ms, setLook ${ms(t4, t5) / 20} ms; the legend ${layer.shown().legendRows} × 256 texels`);
    expect(d.faces).toBe(4_992_300);
    expect(["iC", "iH", "iA", "iK"].map((n) => ver(geo, n))).toEqual(versions);
    expect(layer.shown().legendRows).toBe(1);
    expect(ms(t3, t4) / 20).toBeLessThan(5); // 256 texels, not 5 million faces
    expect(ms(t4, t5) / 20).toBeLessThan(1);
    expect(ms(t0, t1)).toBeLessThan(3000);
    layer.dispose();
  });
});
