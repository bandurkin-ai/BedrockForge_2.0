// The slice of block models in the main 3D (ТЗ 8.7.6, 8.7.7 «Срез на разрезе»; ADR 0023 §11, decision 19; Э17 17.32) on the
// REAL answers of the core (`core/tests/data/blocks/viz_answers.json`: `slice_z`, `slice_categories` through `POST
// /viz/blocks/{id}/slice`): the style → the body the core got (contract §12); a new request only for the plane, the
// field, the hidden categories, the filter, the restrictor and the versions; the range, the palette and the opacity are
// baked again without a request; an overtaken answer and a 409 `superseded` dropped without a word; the plane «по
// разрезу» follows the section and is a backdrop, and with no section nothing is asked; an empty slice; a failure said
// in Russian; a lost context; the style saved in the view and read back.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { viewStore } from "../src/modules/scene/bind";
import { encodeBfa1 } from "../src/edit/bfa1";
import type { BlockModelInfo } from "../src/modules/blocks/api";
import { BLOCK_STYLES_KEY, readStyle, styleIn, styleOp, type BlockStyle, type StylePatch } from "../src/modules/blocks/display";
import { filtersStore } from "../src/modules/blocks/filters/state";
import { sliceChannelOf, sliceLayerKey, SliceScene, slicedStyles, type SliceHost } from "../src/modules/blocks/sliceScene";
import { axisPlane, planeOf, resetSlice, sliceInfo, sliceKey, sliceRequest, sliceText, type PlaneBody } from "../src/modules/blocks/sliceState";
import { blocksStore } from "../src/modules/blocks/state";
import { setCoreTarget } from "../src/shell/api";

interface Case {
  name: string;
  request: Record<string, unknown>;
  bfa1: string;
}
const VIZ: { cases: Case[] } = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/blocks/viz_answers.json"), "utf-8"));
const viz = (name: string): Case => VIZ.cases.find((c) => c.name === name)!;
const bytes = (b64: string): ArrayBuffer => {
  const b = Buffer.from(b64, "base64");
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer;
};
const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const bfa1 = (b64: string) => new Response(bytes(b64), { status: 200, headers: { "Content-Type": "application/octet-stream" } });
const strip = ({ channel: _c, seq: _s, ...rest }: Record<string, unknown>) => rest;
const settle = async () => {
  for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0));
};

const info = (id: number, fields: BlockModelInfo["fields"], version = 1): BlockModelInfo => ({
  id, name: `Модель ${id}`, folder: null, version, extent: [0, 0, 0, 80, 60, 20], crs: null,
  grid: { corner: [0, 0, 0], counts: [8, 6, 4], size: [10, 10, 5], order: "F", variable: false },
  cells: 177, fields, source: null, warnings: [], size: null, created_at: "", updated_at: "",
}); // prettier-ignore
const AU = { name: "AU", kind: "number" as const, unit: "г/т" };
const OK = { name: "OK", kind: "number" as const, unit: null };
const ROCK = { name: "ROCK", kind: "category" as const, unit: null, labels: ["руда", "порода", "окисленная", "дайка", "вода"] };
const M1 = info(1, [AU, OK, ROCK]);
const M2 = info(2, [AU, ROCK]);

/** The planes of the real answers. */
const Z1: PlaneBody = { origin: [512132.05063509464, 7100040.4205080755, 262.7], normal: [0, 0, 1] };
const Z2: PlaneBody = { origin: [1035, 2025, 112], normal: [0, 0, 1] };

function fakeScene() {
  const layers = new Map<string, { origin: unknown; setLook?: unknown }>();
  const log = { added: [] as string[], removed: [] as string[], invalidated: 0 };
  let restored: (() => void) | null = null;
  const host: SliceHost = {
    addLayer: (k, l) => (layers.set(k, l), log.added.push(k), undefined),
    removeLayer: (k) => (layers.delete(k), log.removed.push(k), undefined),
    invalidate: () => void log.invalidated++,
    clipPlanes: [],
    onRestored: (fn) => ((restored = fn), () => (restored = null)),
    layerChanged: () => undefined,
  };
  return { host, layers, log, restore: () => restored?.() };
}

interface Sent {
  path: string;
  body: Record<string, unknown>;
}
let sent: Sent[];
let answer: (path: string, body: Record<string, unknown>) => Response | Promise<Response>;
let scene: ReturnType<typeof fakeScene>;
let slices: SliceScene;

const sliceCalls = () => sent.filter((s) => s.path.endsWith("/slice"));
const styleNow = (id: number): BlockStyle => styleIn(viewStore.get(), id);
const change = (id: number, patch: StylePatch) => viewStore.set({ [BLOCK_STYLES_KEY]: styleOp(id, patch)(viewStore.get()[BLOCK_STYLES_KEY]) });
const on = (id: number, plane: PlaneBody | "section", patch: StylePatch = {}) => change(id, { slice: { on: true, plane }, ...patch });
const layerOf = (id: number) => slices.layerOf(id);

beforeEach(() => {
  sent = [];
  answer = (path, body) => {
    if (!path.endsWith("/slice")) return json({ code: "not_found", message: "Нет", hint: "" }, 404);
    const field = body.field as string | undefined;
    if (field === undefined) return new Response(plain());
    return bfa1(viz(path.includes("/viz/blocks/2/") || field === "ROCK" ? "slice_categories" : "slice_z").bfa1);
  };
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
  viewStore.set({ [BLOCK_STYLES_KEY]: undefined, section: undefined });
  blocksStore.set({ list: [M1, M2], loaded: true, error: null, details: {}, detailErrors: {} });
  filtersStore.set({ rows: [], loaded: true, selected: null, edit: null });
  resetSlice();
  scene = fakeScene();
  slices = new SliceScene(scene.host);
});

afterEach(() => {
  slices.dispose();
  vi.unstubAllGlobals();
});

describe("the style → the request (contract §12)", () => {
  const body = (patch: StylePatch, model = M1, plane = Z1) => sliceRequest(readStyle((styleOp(model.id, { slice: { on: true, plane }, ...patch })(undefined) as Record<string, unknown>)[String(model.id)]), model.fields, plane);

  it("is the request of the real answers: the field of the colouring, the restrictor, the hidden codes", () => {
    const expected = (name: string) => ({ ...strip(viz(name).request), max_px: 4096 });
    expect(body({ colour: { field: "AU", kind: "scale" }, quick: { items: [{ field: "OK", op: ">", value: 0.5 }] } })).toEqual(expected("slice_z"));
    expect(body({ colour: { field: "ROCK", kind: "categories", categories: { "1": { colour: null, on: false } } } }, M2, Z2)).toEqual(expected("slice_categories"));
    expect(body({}, M1)).toEqual({ plane: Z1, max_px: 4096 }); // no colouring: no field to read
    expect(body({ filter_id: 3 })).toEqual({ plane: Z1, filter_id: 3, max_px: 4096 });
  });

  it("the key holds what the raster depends on: the plane, the field, the hidden codes, the filter, the versions — not the colours", () => {
    const key = (patch: StylePatch, version = 1, fv: number | null = null, plane = Z1) => sliceKey(1, version, fv, body(patch, M1, plane));
    const base = key({ colour: { field: "AU", kind: "scale" } });
    expect(key({ colour: { field: "AU", kind: "scale", from: 1, to: 5, log: true, palette: "magma", colours: ["#ff0000"] }, opacity: 0.3, edges: { on: true } })).toBe(base);
    expect(key({ colour: { field: "AU", kind: "classes", breaks: [1, 2] } })).toBe(base); // the classes are the client's
    expect(key({ colour: { field: "OK", kind: "scale" } })).not.toBe(base);
    expect(key({ colour: { field: "AU", kind: "scale" } }, 2)).not.toBe(base);
    expect(key({ colour: { field: "AU", kind: "scale" } }, 1, 4)).not.toBe(base);
    expect(key({ colour: { field: "AU", kind: "scale" }, quick: { items: [{ field: "OK", op: ">", value: 1 }] } })).not.toBe(base);
    expect(key({ colour: { field: "AU", kind: "scale" } }, 1, null, { origin: [512132.05063509464, 7100040.4205080755, 263.7], normal: [0, 0, 1] })).not.toBe(base);
  });

  it("the plane «по разрезу» is the plane of the section of the view; no section — no plane", () => {
    expect(planeOf("section", viewStore.get())).toBeNull();
    viewStore.set({ section: { line: { x0: 100, y0: 200, az: 90, length: 50, towards: 25, away: 25 }, clip: true } });
    const p = planeOf("section", viewStore.get())!;
    expect(p.origin).toEqual([100, 200, 0]);
    expect(p.normal[0]).toBeCloseTo(0, 12); // a section along X (az 90°) looks along Y
    expect(Math.abs(p.normal[1])).toBeCloseTo(1, 12);
    expect(p.normal[2]).toBeCloseTo(0, 12);
    expect(planeOf({ origin: [1, 2, 3], normal: [0, 0, 2] }, viewStore.get())).toEqual({ origin: [1, 2, 3], normal: [0, 0, 2] });
    viewStore.set({ section: undefined });
    expect(axisPlane("z", 7, M1)).toEqual({ origin: [40, 30, 7], normal: [0, 0, 1] });
    expect(axisPlane("x", null, M1)).toEqual({ origin: [40, 30, 10], normal: [1, 0, 0] });
  });
});

describe("the layer in the scene", () => {
  it("a slice switched on is one request with the channel and the number, one layer `bms:<id>` with the raster of the real answer", async () => {
    expect(slicedStyles(viewStore.get()).size).toBe(0);
    on(1, Z1, { colour: { field: "AU", kind: "scale" } });
    await settle();
    expect(sliceCalls()).toHaveLength(1);
    const call = sliceCalls()[0];
    expect(call.path).toBe("/viz/blocks/1/slice");
    expect(call.body).toMatchObject({ plane: Z1, field: "AU", max_px: 4096, channel: sliceChannelOf(1) });
    expect(typeof call.body.seq).toBe("number");
    expect(scene.log.added).toEqual([sliceLayerKey(1)]);
    expect(slices.shown()).toEqual([expect.objectContaining({ key: "bms:1", model: 1, raster: "subcells", width: 12, height: 10, withValue: 77, withoutValue: 11, kind: "scale", look: { opacity: 1, backdrop: false } })]);
    expect(sliceInfo(1)).toMatchObject({ status: "ready", requests: 1, error: null });
    expect(sliceInfo(1)!.header).toMatchObject({ width: 12, height: 10 });
    expect(sliceText(sliceInfo(1)!, "Ресурсная")).toBe("Срез «Ресурсная»: растр 12 × 10 пикселей (подъячейки), 33 блока на плоскости; по оси Z, отметка 262,7 м");
    expect(slices.stats()).toMatchObject({ requests: 1, layers: 1 });
  });

  it("the slice does not need the shell: a model that is not visible is sliced; two models are two layers", async () => {
    on(1, Z1, { visible: false });
    on(2, Z2, { colour: { field: "ROCK", kind: "categories" } });
    await settle();
    expect(slices.shown().map((s) => s.key)).toEqual(["bms:1", "bms:2"]);
    expect(layerOf(2)!.shown()).toMatchObject({ raster: "cells", width: 8, height: 6, kind: "categories" });
    expect(layerOf(1)!.shown().kind).toBe("none"); // no field of the colouring: one colour
  });

  it("asks again only for the plane, the field, the filter, the restrictor, the hidden codes and the versions; colours need no request", async () => {
    on(1, Z1, { colour: { field: "AU", kind: "scale" } });
    await settle();
    const layer = layerOf(1)!;
    const texture = layer.texture!;
    const before = Uint8Array.from(texture.image.data as Uint8Array);
    // the range, the log, the palette, the opacity, the breaks, the colours of the user: baked again, no request, the same layer and texture
    change(1, { colour: { from: 0.1, to: 10, log: true } });
    change(1, { colour: { colours: Array.from({ length: 256 }, () => "#00ff00") } });
    change(1, { opacity: 0.4 });
    change(1, { colour: { kind: "classes", breaks: [0.5, 1, 2] } });
    await settle();
    expect(sliceCalls()).toHaveLength(1);
    expect(layerOf(1)).toBe(layer);
    expect(layer.texture).toBe(texture);
    expect(Uint8Array.from(texture.image.data as Uint8Array)).not.toEqual(before);
    expect(layer.shown()).toMatchObject({ kind: "classes", look: { opacity: 0.4, backdrop: false } });
    expect(slices.stats().restyles).toBeGreaterThanOrEqual(4);
    expect(scene.log.added).toEqual(["bms:1"]);
    // the same style again: nothing
    const restyles = slices.stats().restyles;
    change(1, { colour: { kind: "classes", breaks: [0.5, 1, 2] } });
    expect(slices.stats().restyles).toBe(restyles);
    expect(sliceCalls()).toHaveLength(1);

    change(1, { slice: { plane: axisPlaneOf(263.7) } });
    await settle();
    expect(sliceCalls()).toHaveLength(2);
    change(1, { colour: { field: "OK", kind: "scale" } });
    await settle();
    expect(sliceCalls()).toHaveLength(3);
    expect(sliceCalls()[2].body.field).toBe("OK");
    change(1, { filter_id: 5 });
    await settle();
    expect(sliceCalls()).toHaveLength(4);
    expect(sliceCalls()[3].body.filter_id).toBe(5);
    change(1, { quick: { items: [{ field: "AU", op: ">", value: 1 }] } });
    await settle();
    expect(sliceCalls()).toHaveLength(5);
    expect(sliceCalls()[4].body.restrict).toEqual({ cond: { field: "AU", op: ">", value: 1 } });
    blocksStore.set({ list: [info(1, M1.fields, 2), M2] });
    await settle();
    expect(sliceCalls()).toHaveLength(6);
    // a hidden category is a new request (the blocks drop out of the raster)
    change(1, { colour: { field: "ROCK", kind: "categories", categories: { "1": { colour: null, on: false } } } });
    await settle();
    expect(sliceCalls()).toHaveLength(7);
    expect(sliceCalls()[6].body).toMatchObject({ field: "ROCK", hidden: [1] });
  });

  it("the plane of a section is a backdrop under the holes; it follows the section; no section — nothing asked, the state says so", async () => {
    on(1, "section", { colour: { field: "AU", kind: "scale" } });
    await settle();
    expect(sliceCalls()).toHaveLength(0);
    expect(layerOf(1)).toBeNull();
    expect(sliceInfo(1)).toMatchObject({ status: "noplane", plane: null });
    expect(sliceText(sliceInfo(1)!, "Ресурсная")).toContain("нет разреза");

    viewStore.set({ section: { line: { x0: 512100, y0: 7100000, az: 30, length: 80, towards: 25, away: 25 }, clip: true } });
    await settle();
    expect(sliceCalls()).toHaveLength(1);
    const plane = sliceCalls()[0].body.plane as PlaneBody;
    expect(plane.origin).toEqual([512100, 7100000, 0]);
    expect(Math.hypot(...plane.normal)).toBeCloseTo(1, 12);
    expect(layerOf(1)!.shown().look.backdrop).toBe(true);
    expect(sliceInfo(1)).toMatchObject({ status: "ready" });

    // the next section of the series (here: another azimuth) is a new plane and a new request; the same section — none
    viewStore.set({ section: { line: { x0: 512100, y0: 7100000, az: 30, length: 80, towards: 25, away: 25 }, clip: false } });
    await settle();
    expect(sliceCalls()).toHaveLength(1);
    viewStore.set({ section: { line: { x0: 512100, y0: 7100000, az: 60, length: 80, towards: 25, away: 25 }, clip: true } });
    await settle();
    expect(sliceCalls()).toHaveLength(2);
    // the section is gone: the layer goes with it
    viewStore.set({ section: undefined });
    await settle();
    expect(layerOf(1)).toBeNull();
    expect(scene.log.removed).toContain("bms:1");
    expect(sliceInfo(1)!.status).toBe("noplane");
  });

  it("an explicit plane is an ordinary surface, not a backdrop", async () => {
    on(1, Z1);
    await settle();
    expect(layerOf(1)!.shown().look.backdrop).toBe(false);
  });

  it("an answer overtaken by a newer request is dropped; a 409 `superseded` is not shown; the old layer stays until the new one is there", async () => {
    const pending: ((r: Response) => void)[] = [];
    answer = () => new Promise<Response>((resolveFn) => pending.push(resolveFn));
    on(1, Z1);
    await settle();
    change(1, { slice: { plane: axisPlaneOf(264) } });
    await settle();
    expect(sliceCalls()).toHaveLength(2);
    expect((sliceCalls()[1].body.seq as number) > (sliceCalls()[0].body.seq as number)).toBe(true);
    // the older one is answered 409 (the core stopped it), then the newer one arrives
    pending[0](json({ code: "superseded", message: "Запрос среза устарел: его сменил более новый.", hint: "Ничего делать не нужно." }, 409));
    await settle();
    expect(sliceInfo(1)).toMatchObject({ status: "loading", error: null });
    expect(scene.log.added).toEqual([]);
    pending[1](bfa1(viz("slice_z").bfa1));
    await settle();
    expect(sliceInfo(1)).toMatchObject({ status: "ready", error: null });
    expect(scene.log.added).toEqual(["bms:1"]);
    // a new request while a layer is there: the old stays, and an answer of an overtaken request is not installed
    change(1, { slice: { plane: axisPlaneOf(265) } });
    await settle();
    change(1, { slice: { plane: axisPlaneOf(266) } });
    await settle();
    expect(layerOf(1)).not.toBeNull();
    const a = pending.length - 2;
    pending[a](bfa1(viz("slice_z").bfa1)); // the overtaken request's answer arrives late
    await settle();
    expect(scene.log.added).toEqual(["bms:1"]);
    pending[a + 1](bfa1(viz("slice_z").bfa1));
    await settle();
    expect(scene.log.added).toEqual(["bms:1", "bms:1"]);
    expect(slices.stats().superseded).toBe(0); // dropped before they counted: a newer request of ours was out
  });

  it("a 409 `superseded` on the newest request (the channel was taken by someone else): it is asked again, twice at most, never shown", async () => {
    let n = 0;
    answer = (path) => (path.endsWith("/slice") && ++n === 1 ? json({ code: "superseded", message: "Запрос среза устарел.", hint: "" }, 409) : bfa1(viz("slice_z").bfa1));
    on(1, Z1, { colour: { field: "AU", kind: "scale" } });
    await settle();
    expect(sliceCalls()).toHaveLength(2);
    expect(sliceCalls()[1].body.seq).not.toBe(sliceCalls()[0].body.seq);
    expect(sliceInfo(1)).toMatchObject({ status: "ready", error: null });
    expect(slices.stats().superseded).toBe(1);
    expect(layerOf(1)).not.toBeNull();
  });

  it("an empty slice (the plane misses the model) leaves no layer; the state says so; a later plane that hits draws again", async () => {
    answer = (path) =>
      path.endsWith("/slice")
        ? new Response(encodeBfa1({ kind: "blocks_slice", format: 1, model: 1, version: 1, plane: Z1, raster: "sampled", width: 0, height: 0, frame: { origin: [0, 0, 0], u: [1, 0, 0], v: [0, 1, 0], du: 1, dv: 1 }, passed: 0, field: null, field_kind: null, labels: null, warnings: [] }, [["value", "<f4", new Float32Array(0)], ["m", "<u1", new Uint8Array(0)]])) // prettier-ignore
        : json({}, 404);
    on(1, Z1);
    await settle();
    expect(layerOf(1)).toBeNull();
    expect(scene.log.added).toEqual([]);
    expect(sliceInfo(1)).toMatchObject({ status: "empty", error: null });
    expect(sliceText(sliceInfo(1)!, "Ресурсная")).toContain("не пересекает блоки модели");
    answer = (path) => (path.endsWith("/slice") ? bfa1(viz("slice_z").bfa1) : json({}, 404));
    change(1, { slice: { plane: axisPlaneOf(250) } });
    await settle();
    expect(layerOf(1)).not.toBeNull();
    expect(sliceInfo(1)!.status).toBe("ready");
  });

  it("a refusal of the core is said in Russian with its hint and is not sent again until something changes; a bad style is said before any request", async () => {
    answer = () => json({ code: "bad_param", param: "plane.normal", message: "Нормаль плоскости нулевая.", hint: "Задайте направление нормали." }, 422);
    on(1, { origin: [0, 0, 0], normal: [0, 0, 0] });
    await settle();
    expect(sliceInfo(1)).toMatchObject({ status: "error" });
    expect(sliceInfo(1)!.error).toContain("Нормаль плоскости нулевая.");
    expect(sliceCalls()).toHaveLength(1);
    change(1, { opacity: 0.5 });
    await settle();
    expect(sliceCalls()).toHaveLength(1);
    expect(sliceText(sliceInfo(1)!, "Ресурсная")).toContain("Нормаль плоскости нулевая.");
    change(1, { colour: { field: "NOPE", kind: "scale" } });
    await settle();
    expect(sliceCalls()).toHaveLength(1); // «Поля «NOPE» нет в этой модели» — said here, not sent
    expect(sliceInfo(1)!.error).toContain("NOPE");
  });

  it("switched off or the model gone: the layer and the state go; a lost context makes the layer again from the answer kept, no request", async () => {
    on(1, Z1, { colour: { field: "AU", kind: "scale" } });
    await settle();
    scene.restore();
    expect(scene.log.added).toEqual(["bms:1", "bms:1"]);
    expect(sliceCalls()).toHaveLength(1);
    expect(layerOf(1)!.shown()).toMatchObject({ width: 12, height: 10 });
    change(1, { slice: { on: false } });
    await settle();
    expect(layerOf(1)).toBeNull();
    expect(scene.layers.size).toBe(0);
    expect(sliceInfo(1)).toBeUndefined();
    on(1, Z1);
    await settle();
    blocksStore.set({ list: [M2] });
    await settle();
    expect(layerOf(1)).toBeNull();
    expect(scene.layers.size).toBe(0);
  });

  it("the slice is in the view of the project: `block_styles[id].slice`, read back as it was written", () => {
    on(1, Z1);
    const entry = (viewStore.get()[BLOCK_STYLES_KEY] as Record<string, Record<string, unknown>>)["1"];
    expect(entry.slice).toEqual({ on: true, plane: Z1 });
    expect(styleNow(1).slice).toEqual({ on: true, plane: Z1 });
    change(1, { slice: { plane: "section" } });
    expect((viewStore.get()[BLOCK_STYLES_KEY] as Record<string, Record<string, unknown>>)["1"].slice).toEqual({ on: true, plane: "section" });
    expect(readStyle({ slice: { on: true, plane: { origin: [1, 2], normal: [0, 0, 1] } } }).slice).toEqual({ on: true, plane: "section" }); // a broken plane — the default
    expect(readStyle({}).slice).toEqual({ on: false, plane: "section" });
  });
});

/** The answer to a request with no field: a raster of 2 × 2 blocks, one colour. */
function plain(): ArrayBuffer {
  const head = { kind: "blocks_slice", format: 1, model: 1, version: 1, plane: Z1, raster: "cells", width: 2, height: 2, frame: { origin: [512100, 7100000, 262.7], u: [1, 0, 0], v: [0, 1, 0], du: 10, dv: 10 }, passed: 4, field: null, field_kind: null, labels: null, warnings: [] }; // prettier-ignore
  return encodeBfa1(head, [["value", "<f4", Float32Array.of(NaN, NaN, NaN, NaN)], ["m", "<u1", Uint8Array.of(1, 1, 1, 0)]]);
}

/** The horizontal plane of the model 1 at a height (the real frame of `slice_z` at UTM). */
function axisPlaneOf(z: number): PlaneBody {
  return { origin: [Z1.origin[0], Z1.origin[1], z], normal: [0, 0, 1] };
}
