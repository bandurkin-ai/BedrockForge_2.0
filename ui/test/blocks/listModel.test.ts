// A model kept as a LIST of blocks (ADR 0030 решение 8, подшаг 27.4г; замечание А6): the tree and «Свойства» call it
// «список блоков» (no grid of parents, no OMF), the command that needs the grid — the slice — refuses it with the way
// out «Регуляризуйте модель», and the others are left as they are.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BlockModelInfo } from "../../src/modules/blocks/api";
import { BLOCK_STYLES_KEY, resetShow, styleIn } from "../../src/modules/blocks/display";
import { gridFacts } from "../../src/modules/blocks/props";
import { blocksStore } from "../../src/modules/blocks/state";
import { resetSlice } from "../../src/modules/blocks/sliceState";
import { blockMenu, blockRow, blockSummary } from "../../src/modules/blocks/tree";
import { viewStore } from "../../src/modules/scene/bind";
import { setCoreTarget } from "../../src/shell/api";
import { createApp, type App } from "../../src/shell/app";
import { EMPTY_CONTEXT, NEEDS_GRID, listReason, unavailableReason, type Command } from "../../src/shell/commands";
import { bindBlocks } from "../../src/modules/blocks/bind";

const info = (over: Partial<BlockModelInfo> = {}): BlockModelInfo => ({
  id: 7, name: "Геомет", folder: null, version: 1, extent: [1000, 2000, 100, 1200, 2400, 160], crs: null,
  grid: { corner: [1000, 2000, 100], counts: [20, 40, 6], size: [10, 10, 10], order: "F", variable: false },
  cells: 1012620, fields: [{ name: "AU", kind: "number", unit: "г/т" }], source: null, warnings: [], size: 1024, created_at: "", updated_at: "",
  ...over,
}); // prettier-ignore
const LIST = info({ layout: "list" });
const plain = (t: string) => t.replace(/[  ]/g, " ");

describe("the tree and the properties call a list «список блоков»", () => {
  it("the row says it holds blocks, not cells of a grid", () => {
    expect(plain(blockSummary(LIST))).toBe("список блоков, 1 012 620 блоков, 1 поле");
    expect(blockSummary(info())).toContain("20 × 40 × 6");
    expect(plain(blockRow(LIST).title)).toContain("список блоков, 1 012 620 блоков");
  });

  it("the menu of a list has no OMF export (OMF is the grid of cells); the others stay", () => {
    const labels = (m: ReturnType<typeof blockMenu>) => m.map((x) => ("label" in x ? x.label : null));
    expect(labels(blockMenu(LIST))).not.toContain("Экспорт OMF…");
    expect(labels(blockMenu(LIST))).toContain("Экспорт…");
    expect(labels(blockMenu(info()))).toContain("Экспорт OMF…");
  });

  it("«Свойства»: «Структура: список блоков (без сетки субблоков)», the blocks and the enclosing grid", () => {
    const facts = gridFacts(LIST);
    expect(facts.find((f) => f.label === "Структура")?.value).toBe("список блоков (без сетки субблоков)");
    expect(plain(String(facts.find((f) => f.label === "Блоков")?.value))).toBe("1 012 620");
    expect(String(facts.find((f) => f.label === "Охватывающая сетка")?.value)).toContain("только для поиска блока под точкой");
    expect(facts.some((f) => f.label === "Занято ячеек" || f.label === "Субблоки")).toBe(false);
    expect(gridFacts(info()).some((f) => f.label === "Структура")).toBe(false);
  });
});

describe("the slice refuses a list", () => {
  let app: App;
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ version: 1, value: {} }), { status: 200, headers: { "Content-Type": "application/json" } })));
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    viewStore.set({ [BLOCK_STYLES_KEY]: undefined, section: undefined });
    app = createApp({ persist: false });
    app.store.set({ projectName: "Проект", projectReadOnly: false, selected: null });
    blocksStore.set({ list: [LIST, info({ id: 8, name: "Сетка" })], loaded: true, error: null, details: {}, detailErrors: {} });
    resetShow();
    resetSlice();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    setCoreTarget(null);
  });

  it("with a hint to regularize; nothing is written to the view; a grid model still slices; «убрать срез» is allowed", async () => {
    const refused = await app.registry.execute("blocks.slice", { model_id: 7, plane: "z", at: 120 });
    expect(refused.ok).toBe(false);
    expect(JSON.stringify(refused)).toContain("Регуляризуйте модель");
    expect(styleIn(viewStore.get(), 7).slice.on).toBe(false);
    const ok = await app.registry.execute("blocks.slice", { model_id: 8, plane: "z", at: 120 });
    expect(ok.ok).toBe(true);
    expect((await app.registry.execute("blocks.slice", { model_id: 7, on: false })).ok).toBe(true);
  });
});

describe("the commands that need the grid are unavailable while a list of blocks is selected", () => {
  const ctx = { ...EMPTY_CONTEXT, hasProject: true, hasObject: true, coreOnline: true };
  const cmd = (id: string): Command => ({ id, title: id, description: id, run: () => undefined });

  it("the slice, the estimation, the resources, the pit, the filling, the coding, the comparison and OMF say why", () => {
    for (const id of ["blocks.slice", "estimate.run", "resources.report", "pit.run", "blocks.fill", "blocks.code", "estimate.compare", "omf.export"]) {
      expect(NEEDS_GRID[id], id).toBeTruthy();
      expect(unavailableReason(cmd(id), { ...ctx, listModel: true }), id).toMatch(/недоступна для модели-списка блоков\. Регуляризуйте модель/);
      expect(unavailableReason(cmd(id), ctx), id).toBeNull(); // a grid model: as before
    }
    expect(listReason(cmd("blocks.slice"), { ...ctx, listModel: true })).toContain("«Срез»");
    expect(unavailableReason(cmd("blocks.show"), { ...ctx, listModel: true })).toBeNull(); // the display, the filters, the report, the export stay
    expect(unavailableReason(cmd("blocks.export"), { ...ctx, listModel: true })).toBeNull();
  });

  it("the module follows the selection: a list selected — `listModel`; another model or nothing — not", () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } })));
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    const app = createApp({ persist: false });
    app.store.set({ projectName: "Проект", selected: null, listModel: false });
    blocksStore.set({ list: [LIST, info({ id: 8, name: "Сетка" })], loaded: true, error: null, details: {}, detailErrors: {} });
    const off = (bindBlocks(app), () => undefined);
    app.store.set({ selected: { kind: "blocks", id: 7 } });
    expect(app.store.get().listModel).toBe(true);
    expect(unavailableReason(app.registry.get("blocks.slice")!, { ...ctx, listModel: app.store.get().listModel })).toContain("Регуляризуйте модель");
    app.store.set({ selected: { kind: "blocks", id: 8 } });
    expect(app.store.get().listModel).toBe(false);
    app.store.set({ selected: { kind: "blocks", id: 7 } });
    app.store.set({ selected: null });
    expect(app.store.get().listModel).toBe(false);
    off();
    vi.unstubAllGlobals();
    setCoreTarget(null);
  });
});
