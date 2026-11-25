// The window «Прототип модели» and the command `blocks.prototype` (ТЗ 8.7.2; ADR 0023 §6, № 66; Э17 17.37) on the REAL answers
// of the core (`core/tests/data/blocks/build_window_answers.json`, made by `core/tests/test_blocks_build_window_answers.py`
// through `POST /blocks/prototype/preview` and `POST /blocks/prototype`): the request of the form = the body the core got;
// the preview read as the core wrote it (nx · ny · nz = the parents, the frame of 8 corners, the disk); the refusals 404 / 422
// in Russian in their place of the form; the run of the command through a stubbed core; the frame of the 3D (the lines of
// the box and of the parents from the corners of the core).
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext } from "../src/shell/AppContext";
import type { App } from "../src/shell/app";
import { windowStore } from "../src/shell/windows";
import { MAX_PARENTS, readPreview, readPrototype, type Preview, type Refusal } from "../src/modules/blocks/build/api";
import { openBuildWindow, renderBuildWindow, requestPreview, windowOfKind } from "../src/modules/blocks/build/bind";
import { commands, CREATE_BUTTONS, FIELD_BUTTONS } from "../src/modules/blocks/build/commands";
import { diskText, dec, int, previewLines, prototypeSummary } from "../src/modules/blocks/build/format";
import { argsOfPrototype, bodyOfPrototype, issueOfPrototype, previewBody, prototypeFormOfArgs, EMPTY_PROTOTYPE, type PrototypeForm } from "../src/modules/blocks/build/forms";
import { attachFrameScene, EDGES, FRAME_KEY, frameSegments, PrototypeFrameLayer } from "../src/modules/blocks/build/frame";
import { PrototypeWindow } from "../src/modules/blocks/build/PrototypeWindow";
import { buildStore, form, PROTOTYPE_WINDOW, runOf, setForm } from "../src/modules/blocks/build/state";
import { A, canon, flat, IDS, installCore, newApp, teardown, type Core, type RefusedCase } from "./blocks-build-harness";

let app: App;
let core: Core;

beforeEach(() => {
  core = installCore();
  app = newApp();
});
afterEach(teardown);

const PV = A.prototype.previews as Record<string, RefusedCase>;
const FORMS: Record<string, Partial<PrototypeForm>> = {
  extent: { source: "extent", extent: ["0", "0", "0", "60", "50", "40"], size: ["10", "10", "10"], levels: ["1", "1", "1"] },
  objects: { source: "objects", objectIds: [IDS.wire_a, IDS.wire_b], size: ["10", "10", "5"], pad: "5", rotation: "30", levels: ["2", "2", "1"] },
  pad3: { source: "extent", extent: ["0", "0", "0", "60", "50", "40"], size: ["10", "10", "10"], pad: "5; 5; 10", levels: ["1", "1", "1"] },
  full: { source: "extent", extent: ["0", "0", "0", "60", "50", "40"], size: ["10", "10", "10"], levels: ["1", "1", "1"], full: true },
};
const formOf = (name: string): PrototypeForm => ({ ...EMPTY_PROTOTYPE, ...FORMS[name] });
const preview = (name: string): Preview => readPreview(PV[name].body)!;
const render = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));
const tick = () => new Promise((r) => setTimeout(r, 5));

describe("the request of the form is the body the core got", () => {
  for (const name of Object.keys(FORMS)) {
    it(`${name}: the form → the arguments → the body of the recorded preview`, () => {
      const got = bodyOfPrototype(argsOfPrototype(formOf(name)));
      expect(got.ok).toBe(true);
      if (!got.ok) return;
      expect(canon(previewBody(got.body))).toBe(canon(PV[name].request));
    });
    it(`${name}: the arguments of a call show in the form and come back as the same body`, () => {
      const args = argsOfPrototype(formOf(name));
      const back = prototypeFormOfArgs(args);
      const again = bodyOfPrototype(argsOfPrototype(back));
      const first = bodyOfPrototype(args);
      expect(again.ok && first.ok && canon(again.body) === canon(first.body)).toBe(true);
    });
  }
  it("the job of the prototype takes the name too; «Все родители заняты» is `full`", () => {
    const got = bodyOfPrototype(argsOfPrototype({ ...formOf("extent"), name: "Сетка 10 м" }));
    expect(got.ok && canon(got.body)).toBe(canon(A.prototype.job.request));
    const all = bodyOfPrototype(argsOfPrototype({ ...formOf("full"), name: "Все родители" }));
    expect(all.ok && canon(all.body)).toBe(canon(A.prototype.full.request));
    expect(all.ok && all.body.full).toBe(true);
  });
  it("the arguments of a command are flat text: «10», «10; 10; 5», «3, 7», a decimal comma", () => {
    const got = bodyOfPrototype({ size: "10", object_ids: "3, 7", pad: "2,5", rotation: "-30,5", levels: "3", all: "true", name: "  Сетка   А " });
    expect(got).toEqual({ ok: true, body: { size: [10, 10, 10], object_ids: [3, 7], pad: 2.5, rotation: -30.5, levels: [3, 3, 3], full: true, name: "Сетка А" } });
    const e = bodyOfPrototype({ size: "5; 5; 2,5", extent: "0; 0; 0; 100; 100; 50" });
    expect(e.ok && e.body.extent).toEqual([0, 0, 0, 100, 100, 50]);
  });
});

describe("a wrong form is told in Russian in its place before anything is sent", () => {
  const bad = (args: Record<string, unknown>) => {
    const got = bodyOfPrototype({ size: "10", extent: "0; 0; 0; 60; 50; 40", ...args });
    if (got.ok) throw new Error("expected a refusal");
    return got.issue;
  };
  it("size: not a number, zero, two numbers", () => {
    for (const size of ["x", "10; 0; 10", "10; 10"]) {
      const i = bad({ size });
      expect(i.field).toBe("size");
      expect(i.message).toMatch(/Размер блока/);
      expect(i.hint).not.toBe("");
    }
  });
  it("the box: both objects and bounds, neither, five numbers, min ≥ max", () => {
    expect(bad({ object_ids: "3" })).toMatchObject({ field: "objects" });
    expect(bodyOfPrototype({ size: "10" })).toMatchObject({ ok: false, issue: { field: "objects", message: "Не задан охват модели." } });
    expect(bad({ extent: "0; 0; 0; 5; 5" })).toMatchObject({ field: "extent" });
    expect(bad({ extent: "0; 0; 0; 0; 50; 40" })).toMatchObject({ field: "extent", message: "Минимум границы должен быть меньше максимума." });
    expect(bodyOfPrototype({ size: "10", object_ids: "a, b" })).toMatchObject({ ok: false, issue: { field: "objects" } });
  });
  it("margin, rotation, levels", () => {
    expect(bad({ pad: "-1" })).toMatchObject({ field: "pad" });
    expect(bad({ rotation: "180,5" })).toMatchObject({ field: "rotation" });
    expect(bad({ rotation: "-180" })).toMatchObject({ field: "rotation" });
    expect(bad({ levels: "1; 9; 0" })).toMatchObject({ field: "levels" });
    expect(bad({ levels: "1,5" })).toMatchObject({ field: "levels" });
    expect(bodyOfPrototype({ size: "10", extent: "0;0;0;1;1;1", rotation: "180" }).ok).toBe(true);
  });
  it("the refusals of the core are put in their place of the form (with the words of the core)", () => {
    const cases: [string, string][] = [
      ["no_extent_and_objects", "objects"],
      ["object_missing", "objects"],
      ["size_zero", "size"],
      ["pad_negative", "pad"],
      ["levels", "levels"],
      ["extent_five", "extent"],
      ["too_large", "size"],
    ];
    for (const [key, field] of cases) {
      const r = A.prototype.refused[key] as RefusedCase;
      const refusal: Refusal = { code: String(r.body.code), message: String(r.body.message), hint: String(r.body.hint), param: typeof r.body.param === "string" ? r.body.param : null };
      const form = { ...EMPTY_PROTOTYPE, source: key === "extent_five" ? ("extent" as const) : ("objects" as const) };
      const i = issueOfPrototype(refusal, form);
      expect(i.field, key).toBe(field);
      expect(i.message).toBe(r.body.message);
      expect(i.hint).toBe(r.body.hint);
      expect(/[а-я]/i.test(i.message)).toBe(true);
    }
    expect(issueOfPrototype({ code: "no_extent", message: "m", hint: "h", param: "object_ids" }, { ...EMPTY_PROTOTYPE, source: "objects" }).field).toBe("objects");
    expect(issueOfPrototype({ code: "project_read_only", message: "m", hint: "h", param: null }, EMPTY_PROTOTYPE).field).toBe("general");
  });
});

describe("the preview of the core: nx · ny · nz, the parents, the disk, the frame", () => {
  for (const name of Object.keys(FORMS)) {
    it(`${name}: nx · ny · nz = the parents of the core; 8 corners; the lines of the frame`, () => {
      const p = preview(name);
      const [nx, ny, nz] = p.grid.counts;
      expect(nx * ny * nz).toBe(p.parents);
      expect(p.frame).toHaveLength(8);
      // the box is the grid: the corner of the frame is the corner of the grid, the sides are nx · size, ny · size, nz · size
      const len = (a: number, b: number) => Math.hypot(p.frame[b][0] - p.frame[a][0], p.frame[b][1] - p.frame[a][1], p.frame[b][2] - p.frame[a][2]);
      expect(len(0, 1)).toBeCloseTo(nx * p.grid.size[0], 6);
      expect(len(0, 2)).toBeCloseTo(ny * p.grid.size[1], 6);
      expect(len(0, 4)).toBeCloseTo(nz * p.grid.size[2], 6);
      expect(p.frame[0]).toEqual(p.grid.corner);
      // the lines: 12 edges and the parents on the bottom and the top plane (nx − 1 + ny − 1 each)
      const seg = frameSegments(p);
      expect(seg.edges.length).toBe(EDGES.length * 6);
      expect(seg.grid.length / 6).toBe(2 * (nx - 1 + (ny - 1)));
      expect(seg.lines).toBe(12 + 2 * (nx - 1 + (ny - 1)));
      // the disk: an estimate of the bytes, not zero
      expect(p.disk).toBeGreaterThan(0);
    });
  }
  it("the numbers as the window writes them", () => {
    const p = preview("extent");
    const lines = Object.fromEntries(previewLines(p).map((l) => [l.label, flat(l.value)]));
    expect(lines["Блоков по осям nx · ny · nz"]).toBe("6 × 5 × 4");
    expect(lines["Родителей"]).toBe(`120 из ${int(MAX_PARENTS)}`);
    expect(lines["Размер блока, м"]).toBe("10 × 10 × 10");
    expect(lines["Субблоков на родителя"]).toBe("2 × 2 × 2");
    expect(flat(diskText(2880))).toBe("≈ 2,8 КБ");
    expect(flat(diskText(43_200_000))).toBe("≈ 41 МБ");
    expect(dec(1234.5678, 2)).toBe("1 234,57");
    expect(preview("objects").grid.rotation).toBe(30);
    expect(lines["Поворот вокруг Z"]).toBe("0°");
  });
  it("the form is asked for the preview; an answer that was overtaken is dropped", async () => {
    setForm("prototype", formOf("extent"));
    requestPreview(0);
    expect(buildStore.get().preview.phase).toBe("loading");
    setForm("prototype", formOf("pad3"));
    requestPreview(0);
    await tick();
    await tick();
    const pv = buildStore.get().preview;
    expect(pv.phase).toBe("ok");
    expect(pv.data?.parents).toBe(preview("pad3").parents);
    expect(core.previews()).toBe(1); // the first request of the pause never went out
    expect(canon(core.sent.at(-1)?.body)).toBe(canon(PV.pad3.request));
    requestPreview(0); // the same request: no new ask
    await tick();
    expect(core.previews()).toBe(1);
    setForm("prototype", { size: ["x", "10", "10"] });
    requestPreview(0);
    expect(buildStore.get().preview.phase).toBe("idle"); // a wrong form is not sent
  });
  it("a refusal of the preview stands under its field; the limit 2·10⁹ is told", async () => {
    setForm("prototype", { ...formOf("extent"), size: ["0,001", "0,001", "0,001"], extent: ["0", "0", "0", "5000", "5000", "5000"] });
    core.force(A.prototype.refused.too_large);
    requestPreview(0);
    await tick();
    await tick();
    expect(buildStore.get().preview.phase).toBe("error");
    openBuildWindow(app, "prototype");
    const html = render(createElement(PrototypeWindow, { winId: windowOfKind("prototype")! }));
    expect(html).toContain('data-testid="prototype-preview-error"');
    expect(html).toContain('data-testid="build-preview-issue-size"');
    expect(html).toContain(A.prototype.refused.too_large.body.message.replace(/\s/g, " ").slice(0, 20));
    expect(flat(html)).toMatch(/2 000 000 000/);
  });
  it("the window shows the preview: nx · ny · nz, the parents against the limit, the disk, the frame in 3D", async () => {
    setForm("prototype", formOf("extent"));
    requestPreview(0);
    await tick();
    await tick();
    openBuildWindow(app, "prototype");
    const html = flat(render(createElement(PrototypeWindow, { winId: windowOfKind("prototype")! })));
    expect(html).toContain('data-testid="prototype-preview"');
    expect(html).toContain("6 × 5 × 4");
    expect(html).toContain("120 из 2 000 000 000");
    expect(html).toContain("≈ 2,8 КБ");
    expect(html).toContain("Рамка сетки с линиями родителей показана в 3D-окне.");
    expect(html).toContain('data-testid="prototype-full"');
    expect(html).toContain("Все родители заняты");
    expect(html).not.toMatch(/window\.(confirm|prompt|alert)/);
    expect(renderBuildWindow({ ...windowStore.get().windows[0], kind: "other" })).toBeNull();
  });
});

describe("the command blocks.prototype", () => {
  it("without arguments it opens the window; the commands are in the registry with the flat parameters", async () => {
    const r = await app.registry.execute("blocks.prototype", {}, "user");
    expect(r.ok).toBe(true);
    expect(windowOfKind("prototype")).not.toBeNull();
    expect(windowStore.get().windows.map((w) => w.kind)).toEqual([PROTOTYPE_WINDOW]);
    const proto = commands.find((c) => c.id === "blocks.prototype")!;
    expect(Object.keys(proto.params ?? {})).toEqual(["size", "object_ids", "extent", "pad", "rotation", "levels", "all", "name", "folder"]);
    expect(proto.confirm).toBe(true);
    for (const c of commands) expect(Object.values(c.params ?? {}).every((p) => ["string", "number", "integer", "boolean"].includes(p.type) && !!p.description)).toBe(true);
    expect(CREATE_BUTTONS.map((b) => b.command)).toEqual(["blocks.prototype", "blocks.fill", "blocks.code"]);
    expect(FIELD_BUTTONS.map((b) => b.command)).toEqual(["blocks.regularize"]);
    expect([...CREATE_BUTTONS, ...FIELD_BUTTONS].every((b) => commands.some((c) => c.id === b.command))).toBe(true);
  });
  it("it starts the job with the recorded body, follows it and answers with the grid", async () => {
    const r = await app.registry.execute("blocks.prototype", { ...argsOfPrototype({ ...formOf("extent"), name: "Сетка 10 м" }) }, "user");
    expect(r.ok).toBe(true);
    expect(flat(r.result)).toContain("Прототип «Сетка 10 м» создан (№7): 6 × 5 × 4 = 120 родителей");
    const post = core.sent.find((s) => s.method === "POST" && s.path === "/blocks/prototype")!;
    expect(canon(post.body)).toBe(canon(A.prototype.job.request));
    expect(runOf("prototype").phase).toBe("done");
    expect(runOf("prototype").result).toEqual(readPrototype(A.prototype.job.job.result));
    expect(form("prototype").name).toBe("Сетка 10 м"); // the window shows what the call asked
    expect(prototypeSummary(runOf("prototype").result!)).toBe(r.result);
  });
  it("«Все родители заняты» goes to the core as `full`", async () => {
    const r = await app.registry.execute("blocks.prototype", argsOfPrototype({ ...formOf("full"), name: "Все родители" }), "user");
    expect(r.ok).toBe(true);
    expect(core.sent.find((s) => s.method === "POST" && s.path === "/blocks/prototype")?.body.full).toBe(true);
    expect(runOf("prototype").result?.model_id).toBe(IDS.full);
  });
  it("a wrong call is refused in Russian with the place; nothing reaches the core", async () => {
    const r = await app.registry.execute("blocks.prototype", { size: "10; 0; 10", extent: "0; 0; 0; 1; 1; 1" }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Размер блока должен быть больше нуля.");
    expect(buildStore.get().issues.prototype?.field).toBe("size");
    expect(core.sent.filter((s) => s.method === "POST")).toHaveLength(0);
  });
  it("a refusal of the core (404 / 422) comes back in Russian and is put in the form", async () => {
    core.force(A.prototype.refused.object_missing);
    const r = await app.registry.execute("blocks.prototype", { size: "10", object_ids: "999999" }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toContain(A.prototype.refused.object_missing.body.message);
    expect(buildStore.get().issues.prototype).toMatchObject({ field: "objects", message: A.prototype.refused.object_missing.body.message });
    expect(runOf("prototype").phase).toBe("failed");
  });
  it("a project open for reading only: the command is unavailable, the preview works", async () => {
    app.store.set({ projectReadOnly: true });
    expect(app.registry.reason("blocks.prototype")).toBe("Проект открыт только для чтения");
    setForm("prototype", formOf("extent"));
    requestPreview(0);
    await tick();
    await tick();
    expect(buildStore.get().preview.phase).toBe("ok");
  });
});

describe("the frame in the 3D", () => {
  it("the layer draws the box and the parents from the corners of the core, float32 from the integer origin", () => {
    const p = preview("objects"); // a model turned by 30°: the corners are not an axis-aligned box
    const layer = new PrototypeFrameLayer(p, { clip: [] });
    const shown = layer.shown();
    expect(shown.counts).toEqual(p.grid.counts);
    expect(shown.parents).toBe(p.parents);
    expect(shown.edges).toBe(12);
    expect(shown.gridLines).toBe(2 * (p.grid.counts[0] - 1 + (p.grid.counts[1] - 1)));
    expect(layer.object.children).toHaveLength(2);
    expect(layer.bbox).toEqual(p.extent);
    const first = layer.object.children[0] as unknown as { geometry: { getAttribute: (n: string) => { array: Float32Array } } };
    const rel = first.geometry.getAttribute("position").array;
    const seg = frameSegments(p).edges;
    for (let i = 0; i < seg.length; i++) expect(Math.abs(rel[i] + [layer.origin.x, layer.origin.y, layer.origin.z][i % 3] - seg[i])).toBeLessThan(1e-4);
    expect(Number.isInteger(layer.origin.x) && Number.isInteger(layer.origin.y) && Number.isInteger(layer.origin.z)).toBe(true);
    layer.dispose();
  });
  it("a finer grid is drawn with a step (at most 48 lines per direction and plane)", () => {
    const p: Preview = { ...preview("extent"), grid: { ...preview("extent").grid, counts: [1000, 1000, 2] } };
    const seg = frameSegments({ ...p, frame: [[0, 0, 0], [1000, 0, 0], [0, 1000, 0], [1000, 1000, 0], [0, 0, 2], [1000, 0, 2], [0, 1000, 2], [1000, 1000, 2]] });
    expect(seg.grid.length / 6).toBeLessThanOrEqual(2 * 2 * 48);
  });
  it("the scene gets the layer while the window is open and the preview is answered; none after it closes", async () => {
    const added: string[] = [];
    const removed: string[] = [];
    const scene = { clipPlanes: [], addLayer: (k: string) => added.push(k), removeLayer: (k: string) => removed.push(k), invalidate: vi.fn(), onRestored: () => () => undefined };
    const off = attachFrameScene(scene as never);
    expect(added).toEqual([]);
    setForm("prototype", formOf("extent"));
    requestPreview(0);
    await tick();
    await tick();
    expect(added).toEqual([]); // the window is not open: nothing is drawn
    openBuildWindow(app, "prototype");
    expect(added).toEqual([FRAME_KEY]);
    windowStore.set({ windows: [] });
    expect(removed).toEqual([FRAME_KEY]);
    off();
  });
});

