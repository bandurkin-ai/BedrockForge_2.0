// The tree and «Свойства» of the models of Э19б (19.38) on the REAL answers of the core (`model_stack`, `model_veins`): the branches «Разломы», «Стек», «Линии»,
// the outputs of the wings under one row, the bodies of the regions of a fault marked «область», the common body of a system of veins, the line of intersection
// (a layer of strings, no wireframe) with its check box of 3D; the menus; the facts of the stack, the faults, the offset and the pairs of the veins; the time of
// the tree of a model with 20 veins and 50 outputs.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { lineageStore } from "../../src/modules/lineage/state";
import { readRow, type ModelInfo, type ModelRow, type OutputInfo } from "../../src/modules/implicit/api";
import { ImplicitProps } from "../../src/modules/implicit/ImplicitProps";
import { ImplicitTree } from "../../src/modules/implicit/ImplicitTree";
import { faultRows, offsetRows, outputKindText, pairRows, pairsOf, stackSummary } from "../../src/modules/implicit/stackFacts";
import { readVeinPairs } from "../../src/modules/implicit/stackApi";
import { implicitStore, loadDetails, loadRows } from "../../src/modules/implicit/state";
import { branchMenu, groupMenu, modelMenu, needsDetails, outputsOf, treeItems, type TreeItem } from "../../src/modules/implicit/treeModel";
import { R, realApp, teardown } from "./real";
import { installStack, listRowOf, STACK_ID, VEINS_ID, type StackCore } from "./stackHarness";

let app: App;
let core: StackCore;
beforeEach(async () => {
  core = installStack();
  app = realApp();
  await loadRows();
});
afterEach(() => {
  lineageStore.set({ statuses: {}, version: 0 });
  teardown();
});

const render = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));
const flat = (s: string): string => s.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").replace(/[\u00a0\u202f]/g, " ");
const rows = () => implicitStore.get().rows;
const details = () => implicitStore.get().details;
const META = (R.model_stack.body as { meta: Record<string, unknown> }).meta;
const VMETA = (R.model_veins.body as { meta: Record<string, unknown> }).meta;
const names = (items: TreeItem[]): string[] =>
  items.map((i) => {
    const head = `${i.level} `;
    switch (i.kind) {
      case "model":
        return `${head}${i.row.name}`;
      case "branch":
        return `${head}[${i.branch} ${i.count}]`;
      case "group":
        return `${head}g:${i.group.name}`;
      case "fault":
        return `${head}f:${i.group.name}${i.text ? ` (${i.text})` : ""}`;
      case "stackgroup":
        return `${head}s:${i.position}.${i.group.name}${i.relation ? ` ${i.relation}` : ""}`;
      case "wings":
        return `${head}w:${i.name}${i.region ? " область" : ""} ${i.count}`;
      default:
        return `${head}o:${i.output.name.replace("Стек и разлом · ", "").replace("Система жил · ", "")}${i.output.region ? " ·область" : ""}${i.output.common ? " ·общее" : ""}`;
    }
  });

describe("the tree of the stack and the fault", () => {
  it("without the details (the list only): the stack of the domains, the fault, the wings under one row, the regions found by the group of the fault", () => {
    const items = treeItems(rows().filter((r) => r.id === STACK_ID), {});
    expect(names(items)).toEqual([
      "2 Стек и разлом",
      "3 [data 5]",
      "4 g:INT",
      "4 g:ALV",
      "4 g:SS",
      "4 g:SH",
      "4 g:Разлом",
      "3 [faults 1]",
      "4 f:Разлом",
      "3 [stack 4]",
      "4 s:1.INT",
      "4 s:2.ALV",
      "4 s:3.SS",
      "4 s:4.SH",
      "3 [surface 6]",
      "4 o:Разлом · поверхность",
      "4 o:INT · поверхность",
      "4 o:ALV · поверхность",
      "4 w:SS 2",
      "5 o:SS, крыло 0 · поверхность",
      "5 o:SS, крыло 1 · поверхность",
      "4 o:SH · поверхность",
      "3 [body 9]",
      "4 w:Разлом область 2",
      "5 o:Разлом, крыло 0 (область) ·область",
      "5 o:Разлом, крыло 1 (область) ·область",
      "4 o:INT",
      "4 o:ALV",
      "4 w:SS 2",
      "5 o:SS, крыло 0",
      "5 o:SS, крыло 1",
      "4 o:SH",
      "4 w:Прочее 2",
      "5 o:Прочее, крыло 0",
      "5 o:Прочее, крыло 1",
    ]);
  });
  it("with the details: the relations of the stack (the oldest has none), the radius of the fault and how many domains it cuts", async () => {
    await loadDetails(STACK_ID);
    const items = treeItems(rows().filter((r) => r.id === STACK_ID), {}, details());
    const n = names(items);
    expect(n).toContain("4 f:Разлом (бесконечный, рассекает доменов: 1)");
    expect(n).toContain("4 s:1.INT Интрузия");
    expect(n).toContain("4 s:3.SS Согласно");
    expect(n).toContain("4 s:4.SH");
    // the details of another version are not taken: the list is the truth until they are read again
    const stale = treeItems(rows().filter((r) => r.id === STACK_ID), {}, { [STACK_ID]: { ...(details()[STACK_ID] as ModelInfo), version: 1 } });
    expect(names(stale)).toContain("4 s:1.INT");
  });
  it("a closed wings row hides its outputs; the keys do not repeat", () => {
    const all = treeItems(rows(), {});
    expect(new Set(all.map((i) => i.key)).size).toBe(all.length);
    const closed = treeItems(rows(), { [`m${STACK_ID}:body:w5`]: false });
    expect(closed.filter((i) => i.kind === "output" && i.output.region)).toHaveLength(0);
    expect(closed.filter((i) => i.kind === "wings")).toHaveLength(all.filter((i) => i.kind === "wings").length);
  });
  it("the outputs: the regions of a fault from the list, the lines and the common body from the details of the same version only", async () => {
    const row = rows().find((r) => r.id === STACK_ID) as ModelRow;
    expect(outputsOf(row).filter((o) => o.region).map((o) => o.wireframe_id)).toEqual([20, 21]);
    const vrow = rows().find((r) => r.id === VEINS_ID) as ModelRow;
    expect(outputsOf(vrow).some((o) => o.kind === "line" || o.common)).toBe(false);
    await loadDetails(VEINS_ID);
    const full = outputsOf(vrow, details()[VEINS_ID]);
    expect(full.filter((o) => o.kind === "line").map((o) => o.line_layer_id)).toEqual([42]);
    expect(full.find((o) => o.common)?.wireframe_id).toBe(40);
    expect(outputsOf(vrow, { ...(details()[VEINS_ID] as ModelInfo), version: 99 }).some((o) => o.kind === "line")).toBe(false);
  });
  it("what needs the details: a fault, a system of veins, two domains", () => {
    expect(rows().every((r) => needsDetails(r))).toBe(true);
    expect(needsDetails({ group_list: [{ id: 1, name: "A", type: "domain" }] })).toBe(false);
    expect(needsDetails({ group_list: [{ id: 1, name: "A", type: "vein_system" }] })).toBe(true);
  });
});

describe("the tree of the system of veins: the common body, the line of intersection", () => {
  it("«Линии» appears when the details tell of a line; the common body is a row of its own", async () => {
    const list = treeItems(rows().filter((r) => r.id === VEINS_ID), {});
    expect(names(list).some((x) => x.includes("[line"))).toBe(false); // the list has no lines
    await loadDetails(VEINS_ID);
    const items = treeItems(rows().filter((r) => r.id === VEINS_ID), {}, details());
    const n = names(items);
    expect(n).toContain("3 [line 1]");
    expect(n).toContain("4 o:Жилы · пересечение VN × VN2".replace("Жилы · ", ""));
    expect(n).toContain("4 o:Жилы · общее тело ·общее");
    expect(n).toContain("4 o:Жилы · Жила VN");
    expect(n.indexOf("4 o:Жилы · Жила VN2")).toBeLessThan(n.indexOf("4 o:Жилы · общее тело ·общее"));
    const line = items.find((i) => i.kind === "output" && i.branch === "line");
    expect(line && line.kind === "output" ? line.output.line_layer_id : null).toBe(42);
    expect(line && line.kind === "output" ? line.output.wireframe_id : 0).toBeNull();
  });
  it("drawn: the line has the check box of 3D (`layer.show {layer: lines}`), the common body the word «общее», the region — «область»", async () => {
    await Promise.all([loadDetails(STACK_ID), loadDetails(VEINS_ID)]);
    const html = render(createElement(ImplicitTree));
    expect(html).toContain('data-testid="tree-implicit-line-42"');
    expect(html).toContain("Система жил · пересечение VN × VN2: показать в 3D");
    expect(html).toContain('data-testid="tree-implicit-out-40"');
    const text = flat(html);
    expect(text).toContain("общее");
    expect(text).toContain("область");
    expect(text).toContain("Разломы");
    expect(text).toContain("Стек");
    expect(text).toContain("Линии");
    expect(text).toContain("Разлом: области крыльев");
    for (const t of [`tree-implicit-${STACK_ID}-fault-5`, `tree-implicit-${STACK_ID}-stack-1`, `tree-implicit-${STACK_ID}-wings-5`, `tree-implicit-${VEINS_ID}-line`]) expect(html).toContain(`data-testid="${t}`);
    expect(html).toContain("Область — часть пространства по одну сторону разлома, не порода");
    expect(html).toContain("Общее тело — объединение жил: повторяет их объёмы, в сумму не входит.");
  });
  it("the check box of the line follows the hidden layers of the 3D window", async () => {
    await loadDetails(VEINS_ID);
    const on = render(createElement(ImplicitTree));
    expect(on).toMatch(/<input[^>]*checked=""[^>]*aria-label="Система жил · пересечение VN × VN2: показать в 3D"|aria-label="Система жил · пересечение VN × VN2: показать в 3D"[^>]*checked=""/);
    app.store.set({ view: { ...app.store.get().view, hidden: ["ln:42"] } });
    expect(render(createElement(ImplicitTree))).not.toMatch(/checked=""[^>]*aria-label="Система жил · пересечение/);
  });
});

describe("the menus", () => {
  const reason = (id: string) => app.registry.reason(id);
  const ran: [string, Record<string, unknown>][] = [];
  const go = (id: string, args: Record<string, unknown>) => void ran.push([id, args]);
  it("the menu of the model has the fault, the system of veins and the stack; of the branches «Данные», «Разломы», «Стек» — their commands", () => {
    const row = readRow(core.list[0]);
    expect(modelMenu(row, reason, go, () => undefined).filter((i) => !i.separator).map((i) => i.label)).toEqual(expect.arrayContaining(["Разлом…", "Система жил…", "Стек отношений…"]));
    expect(branchMenu(STACK_ID, "faults", reason, go).map((i) => i.label)).toEqual(["Новый разлом…", "Стек отношений…"]);
    expect(branchMenu(STACK_ID, "stack", reason, go).map((i) => i.label)).toEqual(["Стек отношений…"]);
    expect(branchMenu(STACK_ID, "data", reason, go).map((i) => i.label)).toEqual(expect.arrayContaining(["Разлом…", "Система жил…"]));
    ran.length = 0;
    for (const i of branchMenu(STACK_ID, "faults", reason, go)) i.run?.();
    expect(ran).toEqual([["implicit.fault", { model_id: STACK_ID }], ["implicit.relations", { model_id: STACK_ID }]]);
  });
  it("the menu of a group: «Изменить…» opens the window of its kind with this group; a domain — the wizard and the stack", () => {
    ran.length = 0;
    const fault = groupMenu(STACK_ID, { id: 5, name: "Разлом", type: "fault" }, reason, go);
    expect(fault.map((i) => i.label)).toEqual(["Изменить «Разлом»…"]);
    fault[0].run?.();
    const system = groupMenu(VEINS_ID, { id: 4, name: "Жилы", type: "vein_system" }, reason, go);
    system[0].run?.();
    expect(ran).toEqual([["implicit.fault", { model_id: STACK_ID, group_id: 5 }], ["implicit.vein_system", { model_id: VEINS_ID, group_id: 4 }]]);
    expect(groupMenu(STACK_ID, { id: 1, name: "INT", type: "domain" }, reason, go).map((i) => i.label)).toEqual(["Домены литологии…", "Стек отношений…"]);
  });
});

describe("«Свойства»: the stack, the faults, the offset, the veins", () => {
  it("the facts of the model with a fault: the regions are «область», the offset is read only", () => {
    const f = faultRows(META);
    expect(f).toHaveLength(1);
    expect(f[0]).toMatchObject({ id: 5, name: "Разлом", radius: "бесконечный", cuts: ["SS"], built: true });
    expect(f[0].regions.map((x) => x.replace(/[\u00a0\u202f]/g, " "))).toEqual(["крыло 0: 17 640 000 м³ (область)", "крыло 1: 19 404 000 м³ (область)"]);
    const o = offsetRows(META);
    expect(o.map((x) => [x.group, x.wing, x.faults])).toEqual([["SS", "крыло 0", "Разлом"], ["SS", "крыло 1", "Разлом"]]);
    expect(o[0].mean).toBe("6,87");
    expect(o[0].max).toBe("61,84");
    expect(stackSummary(META).dropped).toContain("62");
    expect(stackSummary(META).lines[0]).toBe("1. INT — Интрузия");
    // a model that was not built: the gaps, not zeros
    expect(faultRows({ ...META, stats: undefined })[0]).toMatchObject({ points: "—", balance: "—", regions: [], built: false });
    expect(offsetRows({})).toEqual([]);
  });
  it("the pairs of the veins: the pair, the length of the line, the volume of the common zone", () => {
    const rowsOfPairs = pairRows(readVeinPairs(VMETA), new Map([["VN", "Жила VN"], ["VN2", "Жила VN2"]]));
    expect(rowsOfPairs).toEqual([{ pair: "Жила VN × Жила VN2", length: "89,9", common: "160,7" }]);
    expect(pairsOf(VMETA, 4)).toHaveLength(1);
    expect(pairsOf(VMETA, 7)).toHaveLength(0);
    expect(outputKindText({ kind: "body", region: true, common: false, code: null, wing: 1 })).toBe("область крыла 1");
    expect(outputKindText({ kind: "body", region: false, common: true, code: null, wing: null })).toBe("общее тело");
    expect(outputKindText({ kind: "body", region: false, common: false, code: "VN", wing: null })).toBe("тело жилы VN");
    expect(outputKindText({ kind: "line", region: false, common: false, code: null, wing: null })).toBe("линия пересечения");
  });
  it("the panel of the model with a fault drawn: the stack by age, the faults with the regions, the offset", async () => {
    await loadDetails(STACK_ID);
    const html = render(createElement(ImplicitProps, { id: STACK_ID }));
    for (const t of ["props-implicit-stack", "props-implicit-faults", "props-implicit-fault-5", "props-implicit-offset", "props-implicit-relations", "props-implicit-fault", "props-implicit-vein_system"]) expect(html).toContain(`data-testid="${t}"`);
    const text = flat(html);
    expect(text).toContain("1. INT — Интрузия");
    expect(text).toContain("3. SS — Согласно; рассечена: Разлом");
    expect(text).toContain("17 640 000 м³ (область)");
    expect(text).toContain("Смещение по разлому (только чтение)");
    expect(text).toContain("6,87");
    expect(text).toContain("область крыла 0");
    expect(text).toContain("не входят");
    expect(html).not.toContain("props-implicit-system-");
  });
  it("the panel of the model with a system of veins: the table of the pairs and the line to show in 3D", async () => {
    await loadDetails(VEINS_ID);
    const html = render(createElement(ImplicitProps, { id: VEINS_ID }));
    expect(html).toContain('data-testid="props-implicit-system-4"');
    expect(html).toContain('data-testid="vsys-pairs"');
    expect(html).toContain('data-testid="vsys-line-42"');
    const text = flat(html);
    expect(text).toContain("Жила VN × Жила VN2");
    expect(text).toContain("160,7");
    expect(text).toContain("общее тело");
    expect(text).toContain("линия пересечения");
    expect(html).not.toContain('data-testid="props-implicit-faults"');
  });
});

describe("20 veins and 50 outputs", () => {
  it("the tree of such a model is drawn in milliseconds", async () => {
    const info = structuredClone(R.model_veins.body) as unknown as ModelInfo & { meta: Record<string, unknown> };
    const veins = Array.from({ length: 20 }, (_, i) => ({ code: `V${i}`, name: `Жила V${i}`, bound: "hull", polygon: null, offset: 0, t_min: 0 }));
    const meta = info.meta as { groups: Record<string, unknown>[] };
    meta.groups = meta.groups.map((g) => (g.type === "vein_system" ? { ...g, codes: veins.map((v) => v.code), params: { veins } } : g));
    const outs: OutputInfo[] = [
      ...veins.map((v, i) => ({ wireframe_id: 100 + i, kind: "body", group: 4, wing: null, threshold: null, name: `Система жил · Жилы · ${v.name}`, volume: 1000 + i, area: 100, triangles: 500, region: false, common: false, code: v.code, line_layer_id: null, lines: null, length: null, pair: null })),
      { wireframe_id: 130, kind: "body", group: 4, wing: null, threshold: null, name: "Система жил · Жилы · общее тело", volume: 9000, area: 900, triangles: 900, region: false, common: true, code: null, line_layer_id: null, lines: null, length: null, pair: null },
      ...Array.from({ length: 29 }, (_, i) => ({ wireframe_id: null, kind: "line", group: 4, wing: null, threshold: null, name: `Система жил · пересечение V${i} × V${i + 1}`, volume: null, area: null, triangles: null, region: false, common: false, code: null, line_layer_id: 200 + i, lines: 1, length: 10 + i, pair: null })),
    ];
    expect(outs).toHaveLength(50);
    (info as unknown as { outputs: unknown[] }).outputs = outs;
    const row = listRowOf(info as unknown as Record<string, unknown>);
    row.outputs = outs.filter((o) => o.wireframe_id !== null).map((o) => ({ wireframe_id: o.wireframe_id, kind: o.kind, group: o.group, wing: o.wing, threshold: null, name: o.name }));
    core.list = [row];
    await loadRows();
    implicitStore.set((s) => ({ details: { ...s.details, [VEINS_ID]: { ...(info as unknown as ModelInfo), version: info.version } } }));
    const rowsNow = rows();
    const t0 = performance.now();
    for (let k = 0; k < 10; k++) treeItems(rowsNow, {}, details());
    const model = (performance.now() - t0) / 10;
    const items = treeItems(rowsNow, {}, details());
    expect(items.filter((i) => i.kind === "output")).toHaveLength(50);
    expect(items.some((i) => i.kind === "branch" && i.branch === "line" && i.count === 29)).toBe(true);
    const t1 = performance.now();
    const html = render(createElement(ImplicitTree));
    const draw = performance.now() - t1;
    expect(html.match(/data-testid="tree-implicit-line-\d+"/g)).toHaveLength(29);
    // eslint-disable-next-line no-console
    console.log(`implicit tree, the model with 20 veins and 50 outputs: rows ${model.toFixed(2)} ms, section ${draw.toFixed(1)} ms`);
    expect(model).toBeLessThan(20);
    expect(draw).toBeLessThan(500);
  });
});
