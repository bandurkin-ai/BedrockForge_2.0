// The command `blocks.slice` (ТЗ 8.7.6, 8.7.7 «Срез на разрезе»; ADR 0023 §11, §13, decision 19; e17-api §14, §16; Э17 17.32): a
// record of the registry (ribbon, Ctrl+K, the assistant) with flat parameters that changes `block_styles[id].slice` in the
// view of the project (written to the core, read back when the project opens): on / off, the plane «по разрезу», an axis of
// the project at an elevation, a point and a normal, the move along the normal by metres or by cells; a wrong value is
// refused in Russian with its hint before anything is written; the buttons of the ribbon run the same command.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { commands, SHOW_BUTTONS } from "../src/modules/blocks/commands";
import { BLOCK_STYLES_KEY, resetShow, styleIn } from "../src/modules/blocks/display";
import { filtersStore } from "../src/modules/blocks/filters/state";
import { blocksStore } from "../src/modules/blocks/state";
import type { BlockModelInfo } from "../src/modules/blocks/api";
import { viewStore, viewSyncOf } from "../src/modules/scene/bind";
import { cellStep, axisOf, modelExtent, movePlane, planeText, resetSlice, sliceStatus, type PlaneBody } from "../src/modules/blocks/sliceState";
import { runSlice } from "../src/modules/blocks/sliceCommands";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

const info = (id: number, name: string): BlockModelInfo => ({
  id, name, folder: null, version: 1, extent: null, crs: null,
  grid: { corner: [0, 0, 0], counts: [8, 6, 4], size: [10, 10, 5], order: "F", variable: false },
  cells: 177, fields: [{ name: "AU", kind: "number", unit: "г/т" }], source: null, warnings: [], size: null, created_at: "", updated_at: "",
}); // prettier-ignore
const A = info(1, "Ресурсная");
const B = info(2, "Карьер");

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
  viewStore.set({ [BLOCK_STYLES_KEY]: undefined, section: undefined });
  app = createApp({ persist: false });
  app.store.set({ projectName: "Проект", projectReadOnly: false, selected: null });
  blocksStore.set({ list: [A, B], loaded: true, error: null, details: {}, detailErrors: {} });
  filtersStore.set({ rows: [], loaded: true, selected: null, edit: null });
  resetShow();
  resetSlice();
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
});

const run = (args: Record<string, unknown> = {}) => app.registry.execute("blocks.slice", args);
const slice = (id = 1) => styleIn(viewStore.get(), id).slice;
const writes = () => sent.filter((s) => s.path === `/view/${BLOCK_STYLES_KEY}`);
const SECTION = { line: { x0: 100, y0: 200, az: 45, length: 60, towards: 25, away: 25 }, clip: true };

describe("the record of the registry", () => {
  it("is `blocks.slice`: Russian, bound, flat parameters, the assistant runs it; its ribbon buttons run the same command", () => {
    const c = commands.find((x) => x.id === "blocks.slice")!;
    expect(c).toBeDefined();
    expect(c.title).toMatch(/[А-Яа-яЁё]/);
    expect(c.description.length).toBeGreaterThan(200);
    expect(c.confirm).toBeUndefined(); // the view of the project, nothing of the data
    expect(c.uiOnly).toBeUndefined();
    expect(c.required).toBeUndefined();
    expect(Object.keys(c.params!)).toEqual(["model_id", "on", "plane", "at", "x", "y", "z", "nx", "ny", "nz", "shift", "cells"]);
    for (const p of Object.values(c.params!)) expect(["integer", "number", "string", "boolean"]).toContain(p.type); // flat: no arrays, no objects
    expect(c.params!.plane.enum).toEqual(["section", "x", "y", "z", "point"]);
    const r = app.registry.get("blocks.slice")!;
    expect(r.run).toBeTypeOf("function");
    expect(r.stage).toBeUndefined();
    expect(app.registry.reason("blocks.slice")).toBeNull();
    app.store.set({ projectName: null });
    expect(app.registry.reason("blocks.slice")).toBe("Откройте или создайте проект");
    const buttons = SHOW_BUTTONS.filter((b) => b.command === "blocks.slice");
    expect(buttons.map((b) => b.label)).toEqual(["Срез", "Срез по разрезу", "Срез выше", "Срез ниже", "Убрать срез"]);
    for (const b of buttons) {
      expect(b.hint).toMatch(/[А-Яа-яЁё]/);
      for (const k of Object.keys(b.args ?? {})) expect(Object.keys(c.params!)).toContain(k);
    }
  });
});

describe("blocks.slice", () => {
  it("the first slice with no section drawn: a horizontal plane in the middle of the model; saved in the view and in the core", async () => {
    const r = await run({ model_id: 1 });
    expect(r).toEqual({ ok: true, result: "Срез модели «Ресурсная»: по оси Z, отметка 10 м" });
    expect(slice()).toEqual({ on: true, plane: { origin: [40, 30, 10], normal: [0, 0, 1] } });
    await viewSyncOf()!.flush(BLOCK_STYLES_KEY);
    expect(writes()).toHaveLength(1);
    const stored = (writes()[0].body.value as Record<string, Record<string, unknown>>)["1"];
    expect(stored.slice).toEqual({ on: true, plane: { origin: [40, 30, 10], normal: [0, 0, 1] } });
    expect(stored.visible).toBe(false); // the slice is not the shell
    // the view comes back: the slice is what the project kept
    viewStore.set({ [BLOCK_STYLES_KEY]: undefined });
    expect(slice().on).toBe(false);
    viewStore.set({ [BLOCK_STYLES_KEY]: writes()[0].body.value });
    expect(slice()).toEqual({ on: true, plane: { origin: [40, 30, 10], normal: [0, 0, 1] } });
  });

  it("a turned model with no extent: no «middle» guessed in the local axes — a refusal with the hint; an elevation given is fine", async () => {
    const turned: BlockModelInfo = { ...info(3, "Повёрнутая"), grid: { ...A.grid, rotation: 30 } as BlockModelInfo["grid"] };
    blocksStore.set({ list: [A, B, turned], loaded: true, error: null, details: {}, detailErrors: {} });
    const bad = await run({ model_id: 3, plane: "z" });
    expect(bad.ok).toBe(false);
    expect(bad.result).toMatch(/^Охват модели неизвестен/);
    expect(bad.result).toMatch(/at=262,5/);
    expect(slice(3).on).toBe(false);
    expect((await run({ model_id: 3 })).ok).toBe(false); // the first slice would be «z in the middle»
    expect((await run({ model_id: 3, plane: "z", at: 262.5 })).result).toBe("Срез модели «Повёрнутая»: по оси Z, отметка 262,5 м");
    expect(modelExtent(turned)).toBeNull();
    expect(modelExtent({ ...turned, extent: [0, 0, 0, 80, 60, 20] })).toEqual([0, 0, 0, 80, 60, 20]);
    expect(modelExtent(A)).toEqual([0, 0, 0, 80, 60, 20]);
  });

  it("with a section drawn it is the plane «по разрезу»; off keeps the plane; on again", async () => {
    viewStore.set({ section: SECTION });
    expect((await run({ model_id: 1 })).result).toBe("Срез модели «Ресурсная»: по разрезу");
    expect(slice()).toEqual({ on: true, plane: "section" });
    expect((await run({ model_id: 1, on: false })).result).toBe("Срез модели «Ресурсная» выключен");
    expect(slice()).toEqual({ on: false, plane: "section" });
    expect((await run({ model_id: 1, plane: "z", at: 12.5 })).result).toBe("Срез модели «Ресурсная»: по оси Z, отметка 12,5 м");
    await run({ model_id: 1, on: false });
    expect((await run({ model_id: 1 })).result).toBe("Срез модели «Ресурсная»: по оси Z, отметка 12,5 м"); // the plane the user chose stays
    // the section is taken off while «по разрезу» is chosen: the call says there is no plane yet and what to do
    viewStore.set({ section: undefined });
    expect((await run({ model_id: 2, plane: "section" })).result).toBe("Срез модели «Карьер» включён по разрезу; разреза пока нет — нарисуйте линию разреза или выберите плоскость по оси (plane=z)");
    expect(slice(2)).toEqual({ on: true, plane: "section" });
  });

  it("the planes by axes, by a point and a normal; the elevation `at` of the axis plane that is there", async () => {
    expect((await run({ model_id: 1, plane: "x" })).result).toBe("Срез модели «Ресурсная»: по оси X, отметка 40 м");
    expect(slice().plane).toEqual({ origin: [40, 30, 10], normal: [1, 0, 0] });
    await run({ model_id: 1, at: 25.5 }); // the axis stays
    expect(slice().plane).toEqual({ origin: [25.5, 30, 10], normal: [1, 0, 0] });
    await run({ model_id: 1, plane: "y", at: 12 });
    expect(slice().plane).toEqual({ origin: [40, 12, 10], normal: [0, 1, 0] });
    expect((await run({ model_id: 1, plane: "point", x: 10, y: 20, z: 30, nx: 1, ny: 1, nz: 1 })).result).toBe("Срез модели «Ресурсная»: наклонная, точка (10; 20; 30), нормаль (0,58; 0,58; 0,58)");
    expect(slice().plane).toEqual({ origin: [10, 20, 30], normal: [1, 1, 1] });
    await run({ model_id: 1, plane: "z", at: 7 });
    await run({ model_id: 1, plane: "z" }); // the axis again with no elevation: the one it had
    expect(axisOf(slice().plane as PlaneBody)).toEqual({ axis: "z", at: 7 });
  });

  it("moves the plane along its normal: by metres or by cells of the model (the buttons «Срез выше» / «Срез ниже»)", async () => {
    await run({ model_id: 1, plane: "z", at: 10 });
    await run({ model_id: 1, shift: 3 });
    expect(axisOf(slice().plane as PlaneBody)).toEqual({ axis: "z", at: 13 });
    await run({ model_id: 1, cells: 1 });
    expect(axisOf(slice().plane as PlaneBody)!.at).toBe(18); // a cell is 5 m high
    await run({ model_id: 1, cells: -2, shift: 0.5 });
    expect(axisOf(slice().plane as PlaneBody)!.at).toBe(8.5);
    await run({ model_id: 1, plane: "x", at: 10 });
    await run({ model_id: 1, cells: 1 });
    expect(axisOf(slice().plane as PlaneBody)).toEqual({ axis: "x", at: 20 }); // along X a cell is 10 m
    expect(cellStep(A, { origin: [0, 0, 0], normal: [1, 1, 1] })).toBe(5); // an oblique plane: the smallest side
    expect(movePlane({ origin: [0, 0, 0], normal: [0, 0, 2] }, 3).origin).toEqual([0, 0, 3]);
    expect(planeText({ origin: [1, 2, 3], normal: [0, 0, 1] })).toBe("по оси Z, отметка 3 м");
    // the buttons of the ribbon: the first of them switches the slice on, the others work on it
    for (const b of SHOW_BUTTONS.filter((x) => x.command === "blocks.slice")) {
      viewStore.set({ [BLOCK_STYLES_KEY]: undefined });
      await run({ model_id: 2, plane: "z", at: 10 }); // a plane to move, as a user who has pressed «Срез» has
      expect((await app.registry.execute(b.command, { model_id: 2, ...b.args })).ok, b.label).toBe(true);
    }
    expect(slice(2).on).toBe(false); // «Убрать срез» was the last
  });

  it("a wrong value is refused in Russian with its hint and nothing is written", async () => {
    const before = writes().length;
    const bad = async (args: Record<string, unknown>, text: string) => {
      const r = await run({ model_id: 1, ...args });
      expect(r.ok).toBe(false);
      expect(r.result).toBe(text);
    };
    await bad({ plane: "w" }, "Параметр «plane»: допустимые значения — section, x, y, z, point");
    await bad({ plane: "point", x: 1, y: 2, nx: 0, ny: 0, nz: 1 }, "Координата Z точки плоскости: нужно число. Например 262,5.");
    await bad({ plane: "point", x: 1, y: 2, z: 3, nx: 0, ny: 0, nz: 0 }, "Нормаль плоскости нулевая. Задайте направление нормали, например nz=1 для горизонтального среза.");
    await bad({ at: 5 }, "Отметка задаётся только для среза по оси. Укажите plane=x, y или z вместе с at.");
    viewStore.set({ section: SECTION });
    await run({ model_id: 1, plane: "section" });
    await bad({ shift: 2 }, "Плоскость «по разрезу» идёт вместе с разрезом. Листайте разрезы серии или выберите срез по оси (plane=z), чтобы двигать плоскость.");
    await bad({ cells: 1 }, "Плоскость «по разрезу» идёт вместе с разрезом. Листайте разрезы серии или выберите срез по оси (plane=z), чтобы двигать плоскость.");
    expect(slice()).toEqual({ on: true, plane: "section" });
    expect((await app.registry.execute("blocks.slice", { model_id: 9 })).result).toBe("Блочная модель №9 не найдена. Номера моделей — в дереве проекта, раздел «Блочные модели».");
    await viewSyncOf()!.flush(BLOCK_STYLES_KEY);
    expect(writes().length - before).toBeLessThanOrEqual(1); // only the one good call wrote
    app.store.set({ selected: null });
    blocksStore.set({ list: [A, B] });
    expect((await app.registry.execute("blocks.slice", {})).result).toBe("Не выбрана блочная модель. Выберите модель в дереве проекта или укажите model_id.");
  });

  it("the handler itself keeps the checks the registry does before it (a call that comes round the schema)", async () => {
    const err = async (args: Record<string, unknown>): Promise<string> => {
      try {
        await runSlice(app, { model_id: 1, ...args });
        return "";
      } catch (e) {
        return `${(e as Error).message}`;
      }
    };
    expect(await err({ plane: "w" })).toContain("Плоскость «w» не известна.");
    expect(await err({ plane: "z", at: "высоко" })).toContain("Отметка плоскости: нужно число.");
    expect(await err({ plane: "z", at: "262,5" })).toBe(""); // a number as the user writes it
    expect(axisOf(slice().plane as PlaneBody)).toEqual({ axis: "z", at: 262.5 });
    expect(slice().on).toBe(true);
  });

  it("the status line of a slice: nothing — null", () => {
    expect(sliceStatus([A])).toBeNull();
  });
});
