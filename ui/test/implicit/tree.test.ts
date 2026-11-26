// The section «Неявные модели» of the data tree (Э19, 19.23) on the REAL answers of the core (`models`, `model`, `model_stale`, `model_error`): the readers of the
// list (groups and outputs come with it), the rows of the tree — the model, «Данные», «Поверхности», «Тела» — the words of the state, the menu (commands of the
// registry with the reasons), `object.select {kind: "implicit"}`, the section drawn, and 100 models (the time of the list).
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { kindTitle } from "../../src/modules/lineage/api";
import { lineageStore, nodeOfSelected } from "../../src/modules/lineage/state";
import { readInfo, readRow, type ModelRow } from "../../src/modules/implicit/api";
import { ImplicitTree } from "../../src/modules/implicit/ImplicitTree";
import { implicitStore, loadRows } from "../../src/modules/implicit/state";
import { branchOfOutput, modelMenu, modelTitleText, stateText, statsText, treeItems } from "../../src/modules/implicit/treeModel";
import { implicitViewStore, setPoints } from "../../src/modules/implicit/viewState";
import { installReal, LIST, MODEL_ID, R, realApp, teardown, type RealCore } from "./real";

let app: App;
let core: RealCore;
beforeEach(async () => {
  core = installReal();
  app = realApp();
  await loadRows();
});
afterEach(() => {
  lineageStore.set({ statuses: {}, version: 0 });
  teardown();
});

const render = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));
const ROW = readRow(LIST.models[0]);

describe("the list as the core wrote it", () => {
  it("a row keeps the groups and the outputs of the list, the numbers and the state", () => {
    expect(ROW).toMatchObject({ id: MODEL_ID, name: "Литология", version: 5, state: "ready", stale: false, source_lost: false, groups: 2, outputs: 5, stats: { points: 3316, patches: 16 } });
    expect(ROW.group_list).toEqual([
      { id: 1, name: "INT", type: "domain" },
      { id: 3, name: "SS", type: "domain" },
    ]);
    expect(ROW.output_list.map((o) => [o.wireframe_id, o.kind, o.group, o.name])).toEqual([
      [7, "surface", 1, "Литология · INT · поверхность"],
      [8, "body", 1, "Литология · INT"],
      [9, "surface", 3, "Литология · SS · поверхность"],
      [10, "body", 3, "Литология · SS"],
      [11, "body", null, "Литология · Прочее"],
    ]);
    // the list has no volumes: a gap, not 0
    expect(ROW.output_list[0]).toMatchObject({ volume: null, area: null, triangles: null });
    expect(implicitStore.get().rows.map((r) => r.id)).toEqual([MODEL_ID]);
  });
  it("the branches: a surface, a body, «Прочее» is a body without a group", () => {
    expect(ROW.output_list.map(branchOfOutput)).toEqual(["surface", "body", "surface", "body", "body"]);
  });
  it("the stale model and the model that failed — as the core answers", () => {
    const stale = readInfo(R.model_stale.body);
    expect(stale).toMatchObject({ stale: true, stale_reason: "изменены параметры", state: "ready", version: 6 });
    expect(stateText({ ...ROW, stale: true, stale_reason: stale.stale_reason })).toBe("устарела: изменены параметры");
    const failed = readInfo(R.model_error.body);
    expect(failed).toMatchObject({ state: "error", outputs: [], error: { code: "no_contacts", group: 2 } });
    expect(stateText({ ...ROW, state: "error" })).toBe("ошибка построения");
    expect(stateText({ ...ROW, state: "building" })).toBe("считается");
    expect(stateText({ ...ROW, source_lost: true })).toBe("источник удалён");
    expect(stateText(ROW)).toBe("");
  });
  it("the numbers and the tooltip of a row", () => {
    expect(statsText(ROW).replace(/\s/g, " ")).toMatch(/^3 316 точек · 16 участков · \d+(,\d)? с$/);
    expect(statsText({ stats: null })).toBe("не построена");
    const t = modelTitleText(ROW);
    expect(t).toContain("Модель «Литология», версия 5");
    expect(t).toContain("Групп 2, каркасов-выходов 5");
    expect(t).toContain("Двойной щелчок — «Свойства»");
    expect(modelTitleText({ ...ROW, stale: true, stale_reason: "изменены параметры" })).toContain("Устарела: изменены параметры");
  });
  it("the lineage knows the node of the model", () => {
    expect(kindTitle("object:implicit_model")).toBe("неявная модель");
    expect(nodeOfSelected({ kind: "implicit", id: MODEL_ID })).toEqual({ kind: "object:implicit_model", id: MODEL_ID });
  });
});

describe("the rows of the tree", () => {
  it("a model, «Данные» with the groups, «Стек» with the domains by age, «Поверхности» and «Тела» with the outputs; no «Линии» and no «Разломы» without them", () => {
    const items = treeItems(implicitStore.get().rows, {});
    expect(items.map((i) => `${i.level} ${i.kind === "model" ? i.row.name : i.kind === "branch" ? `[${i.branch} ${i.count}]` : i.kind === "group" ? `g:${i.group.name}` : i.kind === "stackgroup" ? `s:${i.position}.${i.group.name}` : i.kind === "output" ? `o:${i.output.name}` : i.kind}`)).toEqual([
      "2 Литология",
      "3 [data 2]",
      "4 g:INT",
      "4 g:SS",
      "3 [stack 2]",
      "4 s:1.INT",
      "4 s:2.SS",
      "3 [surface 2]",
      "4 o:Литология · INT · поверхность",
      "4 o:Литология · SS · поверхность",
      "3 [body 3]",
      "4 o:Литология · INT",
      "4 o:Литология · SS",
      "4 o:Литология · Прочее",
    ]);
  });
  it("a closed model or branch hides what is under it; the keys are the same on every read", () => {
    const rows = implicitStore.get().rows;
    expect(treeItems(rows, { [`m${MODEL_ID}`]: false }).map((i) => i.kind)).toEqual(["model"]);
    const noSurfaces = treeItems(rows, { [`m${MODEL_ID}:surface`]: false });
    expect(noSurfaces.filter((i) => i.kind === "output")).toHaveLength(3);
    expect(treeItems(rows, {}).map((i) => i.key)).toEqual(treeItems(rows, {}).map((i) => i.key));
  });
  it("a model without a build has the branches of the data, the stack and the outputs, the outputs empty", () => {
    const fresh: ModelRow = { ...ROW, id: 99, name: "Новая", stats: null, outputs: 0, output_list: [] };
    const items = treeItems([fresh], {});
    expect(items.filter((i) => i.kind === "branch").map((i) => (i.kind === "branch" ? [i.branch, i.count] : null))).toEqual([["data", 2], ["stack", 2], ["surface", 0], ["body", 0]]);
  });
  it("the models are by name, the same names — by number; with more than eight they start closed", () => {
    const many = Array.from({ length: 9 }, (_, i) => ({ ...ROW, id: 100 - i, name: i % 2 ? "Б" : "А" }));
    const items = treeItems(many, {});
    expect(items.every((i) => i.kind === "model")).toBe(true);
    expect(items.map((i) => (i.kind === "model" ? i.row.id : 0))).toEqual([92, 94, 96, 98, 100, 93, 95, 97, 99]);
    expect(treeItems(many, { m100: true }).filter((i) => i.kind === "branch")).toHaveLength(4);
  });
});

describe("the menu of a model runs the commands of the registry", () => {
  it("each item — its command with the number of the model; «Обновить список» reads the list", () => {
    const ran: [string, Record<string, unknown>][] = [];
    let refreshed = 0;
    const items = modelMenu(ROW, (id) => app.registry.reason(id), (id, args) => void ran.push([id, args]), () => void refreshed++);
    const labels = items.filter((i) => !i.separator).map((i) => i.label);
    expect(labels).toEqual(["Домены литологии…", "Оболочки содержаний…", "Жила…", "Расширение каркаса…", "Разлом…", "Система жил…", "Стек отношений…", "Обновить модель", "В каркас…", "Проверка по данным…", "Опора данных…", "Сверка со скважинами…", "Обновить список", "Удалить…"]);
    for (const i of items) if (!i.separator && i.reason == null) i.run?.();
    expect(ran.map((r) => r[0])).toEqual(["implicit.domains", "implicit.shells", "implicit.vein", "implicit.expand", "implicit.fault", "implicit.vein_system", "implicit.relations", "implicit.update", "implicit.to_wireframe", "implicit.check", "implicit.support", "implicit.compare_holes", "implicit.delete"]);
    expect(ran.every(([, a]) => a.model_id === MODEL_ID)).toBe(true);
    expect(refreshed).toBe(1);
  });
  it("the commands of 19.25 say why they are not yet; a model not built has nothing to copy or check", () => {
    const items = modelMenu(ROW, (id) => app.registry.reason(id), () => undefined, () => undefined);
    const by = (label: string) => items.find((i) => i.label === label)!;
    expect(by("Проверка по данным…").reason ?? null).toBeNull();
    const fresh = modelMenu({ ...ROW, stats: null }, (id) => app.registry.reason(id), () => undefined, () => undefined);
    expect(fresh.find((i) => i.label === "В каркас…")!.reason).toBe("Модель ещё не построена: сначала «Обновить модель»");
    const busy = modelMenu({ ...ROW, state: "building" }, (id) => app.registry.reason(id), () => undefined, () => undefined);
    expect(busy.find((i) => i.label === "Обновить модель")!.reason).toBe("Модель сейчас строится");
  });
  it("without a project the commands say why", () => {
    const items = modelMenu(ROW, (id) => app.registry.reason(id, { hasProject: false, hasSet: false, hasHole: false, hasObject: false, hasSection: false, editing: false, coreOnline: true }), () => undefined, () => undefined);
    expect(items.find((i) => i.label === "Обновить модель")!.reason).toBeTruthy();
  });
});

describe("a click chooses the model", () => {
  it("`object.select {kind: implicit}` marks the model for «Свойства»; one that is not in the list is refused in Russian", async () => {
    const ok = await app.registry.execute("object.select", { kind: "implicit", id: MODEL_ID });
    expect(ok).toEqual({ ok: true, result: "Выбрана неявная модель «Литология»" });
    expect(ok.result).toBe("Выбрана неявная модель «Литология»");
    expect(app.store.get().selected).toEqual({ kind: "implicit", id: MODEL_ID });
    const bad = await app.registry.execute("object.select", { kind: "implicit", id: 999 });
    expect(bad.ok).toBe(false);
    expect(bad.result).toContain("не найдена");
  });
  it("a model that leaves the list leaves the selection", async () => {
    await app.registry.execute("object.select", { kind: "implicit", id: MODEL_ID });
    core.list = [];
    await loadRows();
    expect(app.store.get().selected).toBeNull();
  });
});

describe("the section drawn", () => {
  it("the model with its branches, the groups with a check box, the outputs; the state of the model", () => {
    const html = render(createElement(ImplicitTree));
    for (const t of ["tree-implicit-6", "tree-implicit-6-data", "tree-implicit-6-surface", "tree-implicit-6-body", "tree-implicit-6-group-1", "tree-implicit-6-group-3", "tree-implicit-out-7", "tree-implicit-out-11"]) expect(html).toContain(`data-testid="${t}"`);
    expect(html).toContain("Данные");
    expect(html).toContain("Поверхности");
    expect(html).toContain("Тела");
    expect(html).toContain("INT: точки данных в 3D");
    expect(html).toContain("Литология · Прочее");
    expect(html).not.toContain("tree-implicit-error-");
  });
  it("an error of the build is a word in the row; an empty project says how to begin", async () => {
    core.list = [{ ...LIST.models[0], state: "error", stats: null, outputs: [] }];
    await loadRows();
    expect(render(createElement(ImplicitTree))).toContain('data-testid="tree-implicit-error-6"');
    core.list = [];
    await loadRows();
    expect(render(createElement(ImplicitTree))).toContain("Нет моделей — «+» открывает мастер «Новая модель»");
  });
  it("a group shown in 3D has its check box on", async () => {
    await setPoints(MODEL_ID, 1, true);
    expect(implicitViewStore.get().points).toEqual(["6:1"]);
    const html = render(createElement(ImplicitTree));
    expect(html).toMatch(/<input[^>]*checked=""[^>]*aria-label="INT: точки данных в 3D"|aria-label="INT: точки данных в 3D"[^>]*checked=""/);
    expect(html).not.toMatch(/checked=""[^>]*aria-label="SS: точки данных/);
  });
});

describe("100 models", () => {
  it("the rows and the section are made in milliseconds", async () => {
    const base = LIST.models[0];
    core.list = Array.from({ length: 100 }, (_, i) => ({ ...base, id: 1000 + i, name: `Модель ${String(100 - i).padStart(3, "0")}` }));
    await loadRows();
    const rows = implicitStore.get().rows;
    expect(rows).toHaveLength(100);
    const t0 = performance.now();
    for (let k = 0; k < 10; k++) treeItems(rows, {});
    const flat = (performance.now() - t0) / 10;
    // all open: 100 × (1 + 4 + groups + domains of the stack + outputs) rows
    const open = Object.fromEntries(rows.map((r) => [`m${r.id}`, true]));
    expect(treeItems(rows, open)).toHaveLength(100 * 14);
    const t1 = performance.now();
    const html = render(createElement(ImplicitTree));
    const draw = performance.now() - t1;
    expect(html.match(/data-testid="tree-implicit-\d+"/g)).toHaveLength(100);
    // closed (more than eight): the models only; the list is by name
    expect(html.indexOf("Модель 001")).toBeLessThan(html.indexOf("Модель 002"));
    // eslint-disable-next-line no-console
    console.log(`implicit tree, 100 models: rows ${flat.toFixed(2)} ms, section ${draw.toFixed(1)} ms`);
    expect(flat).toBeLessThan(50);
    expect(draw).toBeLessThan(1500);
  });
});
