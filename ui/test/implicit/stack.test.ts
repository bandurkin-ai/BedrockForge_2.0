// The stack of relations, the faults and the system of veins (Э19б, 19.38; ТЗ 7.35 Д-24, Д-31, Д-39) on the REAL answers of the core (`contract_answers.json` of 19.36:
// `model_stack`, `model_veins`, `relations`, `relations_bad`, `fault_post`, `fault_bad`, `group_faults`, `group_faults_bad`, `vein_system_post`, `vein_system_bad`,
// `delete_veins`): the readers of the numbers of the build, the draft of the stack, the four commands (the window without values, the write with them, the
// refusals of the core under their fields), the windows drawn, «Применить» of the stack.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { commandTools } from "../../src/shell/commands";
import { windowStore } from "../../src/shell/windows";
import { readInfo, readOutput } from "../../src/modules/implicit/api";
import { renderImplicitWindow } from "../../src/modules/implicit/bind";
import { commands } from "../../src/modules/implicit/commands";
import { FaultWizard } from "../../src/modules/implicit/FaultWizard";
import { faultFromGroup, faultMake, layersOf, EMPTY_FAULT } from "../../src/modules/implicit/FaultState";
import { faults, veinSystems } from "../../src/modules/implicit/groupKinds";
import { groupRanges } from "../../src/modules/implicit/groupBase";
import { formOfMeta } from "../../src/modules/implicit/form";
import { parseRelation, readFaultStats, readOffsets, readStackStats, readVeinPairs, RELATIONS, RELATION_HELP, RELATION_TITLES } from "../../src/modules/implicit/stackApi";
import { domainsOfMeta, draftOf, dropped, faultsChanged, faultsOfMeta, fullOrder, moved, relationsBody, relationsChanged, stackLines } from "../../src/modules/implicit/stackModel";
import { StackTab } from "../../src/modules/implicit/StackTab";
import { applyStack, moveDomain, setRelation, stackStore, STACK_WINDOW, toggleFault } from "../../src/modules/implicit/StackState";
import { implicitStore, loadDetails, loadRows } from "../../src/modules/implicit/state";
import { systemFromGroup, systemMake, veinsOfText } from "../../src/modules/implicit/VeinSystemState";
import { VeinSystemForm } from "../../src/modules/implicit/VeinSystemForm";
import { ensureDefaults } from "../../src/modules/implicit/wizardState";
import { R, realApp, teardown } from "./real";
import { installStack, STACK_ID, VEINS_ID, type StackCore } from "./stackHarness";

let app: App;
let core: StackCore;
beforeEach(async () => {
  core = installStack();
  app = realApp();
  await loadRows();
  await Promise.all([loadDetails(STACK_ID), loadDetails(VEINS_ID)]);
});
afterEach(teardown);

const render = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));
const META = (R.model_stack.body as { meta: Record<string, unknown> }).meta;
const DEFAULTS = R.defaults.body as { params: { choices: { relations: string[] }; limits: { veins: number } } };
const VMETA = (R.model_veins.body as { meta: Record<string, unknown> }).meta;
const sent = (method: string, re: RegExp) => core.sent.filter((s) => s.method === method && re.test(s.path));
const windows = (kind: string) => windowStore.get().windows.filter((w) => w.kind === kind);
const run = (id: string, a: Record<string, unknown> = {}) => app.registry.execute(id, { model_id: STACK_ID, ...a });

describe("what the core wrote", () => {
  it("the outputs of the stack: the regions of the wings, the common body, the codes, the line without a wireframe", () => {
    const outs = (R.model_stack.body.outputs as unknown[]).map(readOutput);
    expect(outs.filter((o) => o.region).map((o) => [o.wireframe_id, o.group, o.wing])).toEqual([[20, 5, 0], [21, 5, 1]]);
    expect(outs.filter((o) => o.wing !== null && !o.region).map((o) => o.wireframe_id)).toEqual([26, 27, 28, 29, 32, 33]);
    const veins = (R.model_veins.body.outputs as unknown[]).map(readOutput);
    expect(veins.filter((o) => o.code).map((o) => [o.wireframe_id, o.code])).toEqual([[38, "VN"], [39, "VN2"]]);
    expect(veins.find((o) => o.common)).toMatchObject({ wireframe_id: 40, group: 4 });
    const line = veins.find((o) => o.kind === "line");
    expect(line).toMatchObject({ wireframe_id: null, line_layer_id: 42, group: 4, lines: 1 });
    expect(line?.length).toBeCloseTo(89.8965, 3);
    // the lines are in the details and in `outputs`, not in the list
    expect(readInfo(R.model_veins.body).outputs.some((o) => o.kind === "line")).toBe(true);
    expect(implicitStore.get().rows.find((r) => r.id === VEINS_ID)?.output_list.some((o) => o.kind === "line")).toBe(false);
  });
  it("the numbers of the build: the stack, the faults, the offset, the pairs of the veins", () => {
    const stack = readStackStats(META)!;
    expect(stack.order).toEqual([1, 2, 3, 4]);
    expect(stack.relations).toEqual({ "1": "intrusion", "2": "conformable", "3": "conformable", "4": "conformable" });
    expect(stack.droppedTotal).toBe(62);
    expect(stack.dropped[0]).toEqual({ group: 2, by: 1, relation: "intrusion", points: 8, of: 1308 });
    expect(readFaultStats(META)).toEqual([{ id: 5, radius: null, points: 273, wingVolumes: [17640000, 19404000], balance: 0, maxResidual: expect.any(Number) }]);
    const off = readOffsets(META);
    expect(off.map((o) => [o.group, o.wing])).toEqual([[3, 0], [3, 1]]);
    expect(off[0].offset?.mean).toBeCloseTo(6.8727, 3);
    expect(off[0].byFault["5"].max).toBeCloseTo(61.8447, 3);
    expect(readVeinPairs(VMETA)).toEqual([{ group: 4, a: "VN", b: "VN2", lineLength: expect.closeTo(89.8965, 3), volumeCommon: expect.closeTo(160.7199, 3) }]);
    // a model built before Э19б has none of it: «nothing», not a zero
    expect(readStackStats({})).toBeNull();
    expect(readFaultStats({})).toEqual([]);
    expect(readVeinPairs({ stats: {} })).toEqual([]);
  });
  it("the relations of the core are named in words; «erosion» of the ТЗ is accepted as «erode»", () => {
    expect(RELATIONS).toEqual(DEFAULTS.params.choices.relations);
    expect(RELATIONS.map((r) => RELATION_TITLES[r])).toEqual(["Согласно", "Эрозия", "Налегание", "Интрузия"]);
    for (const r of RELATIONS) expect(RELATION_HELP[r]).toMatch(/[А-Яа-я]/);
    expect(parseRelation("erosion")).toBe("erode");
    expect(parseRelation("налегание")).toBe("onlap");
    expect(parseRelation("ИНТРУЗИЯ")).toBe("intrusion");
    expect(parseRelation("sideways")).toBeNull();
    expect(R.relations_bad.body.hint).toContain("erode");
  });
});

describe("the draft of the stack", () => {
  it("the domains by age, their relations and faults as the core stores them; the fault is not in the stack", () => {
    const d = draftOf(META);
    expect(d.order).toEqual([1, 2, 3, 4]);
    expect(d.relations).toEqual({ 1: "intrusion", 2: "conformable", 3: "conformable", 4: "conformable" });
    expect(d.faults).toEqual({ 1: [], 2: [], 3: [5], 4: [] });
    expect(faultsOfMeta(META).map((f) => f.name)).toEqual(["Разлом"]);
    expect(stackLines(META)).toEqual(["1. INT — Интрузия", "2. ALV — Согласно", "3. SS — Согласно; рассечена: Разлом", "4. SH — самая старая: отношение не используется"]);
  });
  it("moving: by a place, to a place; the ends stay", () => {
    expect(moved([1, 2, 3, 4], 3, -1)).toEqual([1, 3, 2, 4]);
    expect(moved([1, 2, 3, 4], 1, -1)).toEqual([1, 2, 3, 4]);
    expect(moved([1, 2, 3, 4], 4, 1)).toEqual([1, 2, 3, 4]);
    expect(dropped([1, 2, 3, 4], 4, 2)).toEqual([1, 4, 2, 3]);
    expect(dropped([1, 2, 3, 4], 1, null)).toEqual([2, 3, 4, 1]);
    expect(dropped([1, 2, 3, 4], 2, 2)).toEqual([1, 2, 3, 4]);
  });
  it("the order for the core holds every group but the faults: the shells and the veins keep their places among the domains", () => {
    const meta = { groups: [{ id: 1, name: "A", type: "domain" }, { id: 2, name: "Ж", type: "vein" }, { id: 3, name: "B", type: "domain" }, { id: 4, name: "F", type: "fault" }, { id: 5, name: "C", type: "domain" }] };
    expect(fullOrder(meta, [5, 1, 3])).toEqual([5, 2, 1, 3]);
    expect(domainsOfMeta(meta).map((g) => g.id)).toEqual([1, 3, 5]);
  });
  it("the body of PUT /relations and what changed", () => {
    const d = draftOf(META);
    expect(relationsChanged(META, d)).toBe(false);
    expect(faultsChanged(META, d)).toEqual([]);
    d.order = moved(d.order, 2, -1);
    d.relations[2] = "erode";
    d.faults[1] = [5];
    d.faults[3] = [];
    expect(relationsChanged(META, d)).toBe(true);
    expect(faultsChanged(META, d)).toEqual([1, 3]);
    expect(relationsBody(META, d)).toEqual({ order: [2, 1, 3, 4], relations: { "1": "intrusion", "2": "erode", "3": "conformable", "4": "conformable" } });
  });
});

describe("`implicit.relations`", () => {
  it("without values opens the window of the stack with the draft of the model", async () => {
    const r = await run("implicit.relations");
    expect(r.ok).toBe(true);
    expect(r.result).toContain("Открыто окно «Стек отношений» модели «Стек и разлом»");
    expect(windows(STACK_WINDOW)).toHaveLength(1);
    expect(stackStore.get().draft?.order).toEqual([1, 2, 3, 4]);
    expect(stackStore.get().modelId).toBe(STACK_ID);
    expect(sent("PUT", /relations/)).toHaveLength(0);
  });
  it("with the order and the relations writes PUT /relations: the groups by number or by name, the relations in the words of the core or of the ТЗ", async () => {
    const r = await run("implicit.relations", { order: "ALV; INT; SS; SH", relations: "ALV=erosion; 1=Налегание" });
    expect(r.ok).toBe(true);
    const put = sent("PUT", /relations$/);
    expect(put).toHaveLength(1);
    expect(put[0].path).toBe(`/implicit/models/${STACK_ID}/relations`);
    expect(put[0].body).toEqual({ expected_version: 4, order: [2, 1, 3, 4], relations: { "1": "onlap", "2": "erode", "3": "conformable", "4": "conformable" } });
    expect(r.result).toContain(`версия ${R.relations.body.version as number}`);
    expect(r.result).toContain("1. ALV — Эрозия");
    expect(r.result).toContain("Обновить");
  });
  it("only the relations: the order of the model stays", async () => {
    await run("implicit.relations", { relations: "SS=intrusion" });
    expect(sent("PUT", /relations$/)[0].body).toMatchObject({ order: [1, 2, 3, 4], relations: { "3": "intrusion", "1": "intrusion" } });
  });
  it("a group that is not there, a relation that is not, an order that is not whole — the words with the hint, nothing is sent", async () => {
    const a = await run("implicit.relations", { relations: "ZZZ=erode" });
    expect(a.ok).toBe(false);
    expect(a.result).toContain("«ZZZ» не найдена");
    const b = await run("implicit.relations", { relations: "INT=sideways" });
    expect(b.result).toContain("Допустимо: conformable (Согласно), erode (Эрозия), onlap (Налегание), intrusion (Интрузия)");
    const c = await run("implicit.relations", { order: "INT; SS" });
    expect(c.result).toContain("названо доменов 2 из 4");
    const d = await run("implicit.relations", { order: "INT; INT; SS; SH" });
    expect(d.result).toContain("два раза");
    const e = await run("implicit.relations", { order: "Разлом; INT; ALV; SS" });
    expect(e.result).toContain("не домен");
    const f = await run("implicit.relations", { relations: "Разлом=erode" });
    expect(f.result).toContain("не найдена в стеке");
    expect(sent("PUT", /relations$/)).toHaveLength(0);
  });
  it("the refusal of the core is told as it is: the words, the hint", async () => {
    core.force(`PUT /implicit/models/${STACK_ID}/relations`, { status: 422, body: R.relations_bad.body });
    const r = await run("implicit.relations", { relations: "INT=erode" });
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Отношение группы: неизвестное значение «sideways».");
    expect(r.result).toContain("Допустимо: conformable, erode, onlap, intrusion.");
    core.force(`PUT /implicit/models/${STACK_ID}/relations`, { status: 422, body: { code: "bad_relations", message: "Порядок решения замкнут в цикл.", hint: "Измените порядок или отношения." } });
    const cyc = await run("implicit.relations", { relations: "INT=onlap" });
    expect(cyc.result).toContain("Порядок решения замкнут в цикл.");
  });
  it("the reasons: no model, the model is being built; a project for reading", async () => {
    const ctx = { hasProject: true, hasSet: true, hasHole: false, hasObject: false, hasSection: false, editing: false, coreOnline: true };
    app.store.set({ selected: { kind: "implicit", id: STACK_ID } });
    for (const id of ["implicit.relations", "implicit.fault", "implicit.group_faults", "implicit.vein_system"]) expect(app.registry.reason(id, ctx), id).toBeNull();
    core.list = core.list.map((r) => (r.id === STACK_ID ? { ...r, state: "building" } : r));
    await loadRows();
    for (const id of ["implicit.relations", "implicit.fault", "implicit.group_faults", "implicit.vein_system"]) expect(app.registry.reason(id, ctx), id).toContain("строится");
    app.store.set({ projectReadOnly: true });
    expect(app.registry.reason("implicit.relations", ctx)).toBe("Проект открыт только для чтения");
  });
  it("the declarations: Russian words, the flat parameters, the assistant sees all four", () => {
    const by = Object.fromEntries(commands.map((c) => [c.id, c]));
    for (const id of ["implicit.relations", "implicit.fault", "implicit.group_faults", "implicit.vein_system"]) {
      expect(by[id].title).toMatch(/[А-Яа-я]/);
      expect(by[id].description).toMatch(/[А-Яа-я]/);
      expect(by[id].stage).toBeUndefined();
    }
    expect(by["implicit.group_faults"].required).toEqual(["group_id", "fault_ids"]);
    expect(by["implicit.fault"].params?.radius.nullable).toBe(true);
    expect(by["implicit.relations"].description).toContain("Интрузия");
    const tools = commandTools(commands).map((t) => t.name);
    for (const n of ["implicit_relations", "implicit_fault", "implicit_group_faults", "implicit_vein_system"]) expect(tools.some((t) => t.endsWith(n))).toBe(true);
  });
});

describe("`implicit.group_faults`", () => {
  it("writes the faults of a domain: by number or by name; empty — none", async () => {
    const a = await run("implicit.group_faults", { group_id: 1, fault_ids: "Разлом" });
    expect(a.ok).toBe(true);
    expect(sent("PUT", /groups\/1\/faults$/)[0].body).toEqual({ expected_version: 4, fault_ids: [5] });
    expect(a.result).toContain("Домен «INT» рассекается разломами: «Разлом»");
    const b = await run("implicit.group_faults", { group_id: 3, fault_ids: "" });
    expect(b.result).toContain("Домен «SS» больше не рассекается разломами");
    expect(sent("PUT", /groups\/3\/faults$/)[0].body).toMatchObject({ fault_ids: [] });
  });
  it("only a domain is cut, only a fault cuts; the core's refusal is told", async () => {
    expect((await run("implicit.group_faults", { group_id: 5, fault_ids: "5" })).result).toContain("не домен");
    expect((await run("implicit.group_faults", { group_id: 1, fault_ids: "SS" })).result).toContain("Разлом «SS» не найден");
    expect((await run("implicit.group_faults", { group_id: 99, fault_ids: "5" })).result).toContain("нет группы №99");
    core.force(`PUT /implicit/models/${STACK_ID}/groups/1/faults`, { status: 422, body: R.group_faults_bad.body });
    const r = await run("implicit.group_faults", { group_id: 1, fault_ids: "5" });
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Эта группа не разлом.");
    expect(r.result).toContain("Выберите группу вида «Разлом».");
  });
});

describe("`implicit.fault`", () => {
  it("without data opens the window «Разлом»; with the number of a fault — that fault with its radius, the domains it cuts", async () => {
    const a = await run("implicit.fault", { name: "Чернова" });
    expect(a.ok).toBe(true);
    expect(windows("implicit-fault")).toHaveLength(1);
    expect(faults.store.get().form).toMatchObject({ name: "Чернова", codes: [], domains: [] });
    const b = await run("implicit.fault", { group_id: 5 });
    expect(b.ok).toBe(true);
    expect(faults.store.get()).toMatchObject({ groupId: 5, form: { name: "Разлом", radius: "", domains: [3] } });
    expect(sent("POST", /faults$/)).toHaveLength(0);
  });
  it("a new fault: POST /faults with the codes, the radius; the domains it cuts get it in their faults", async () => {
    const r = await run("implicit.fault", { name: "Разлом F2", codes: "FLT", radius: 250, domains: "INT" });
    expect(r.ok).toBe(true);
    const post = sent("POST", /faults$/);
    expect(post).toHaveLength(1);
    expect(post[0].body).toEqual({ expected_version: 4, group: { name: "Разлом F2", type: "fault", codes: ["FLT"], params: { radius: 250 } } });
    const put = sent("PUT", /groups\/1\/faults$/);
    expect(put[0].body).toEqual({ expected_version: R.fault_post.body.version, fault_ids: [R.fault_post.body.group && (R.fault_post.body.group as { id: number }).id] });
    expect(r.result).toContain("Разлом «Разлом» добавлен в модели «Стек и разлом»");
    expect(r.result).toContain("рассекает доменов: 1");
  });
  it("the hand layers: «номер:роль»; a fault without codes is made of them; an infinite fault is `radius: null`", async () => {
    const r = await run("implicit.fault", { name: "Трасса", layers: "16:contact; 17:внутри; 18:outside", radius: null });
    expect(r.ok).toBe(true);
    expect(sent("POST", /faults$/)[0].body).toEqual({
      expected_version: 4,
      group: { name: "Трасса", type: "fault", codes: [], params: { radius: null } },
      manual: [{ layer_id: 16, role: "contact" }, { layer_id: 17, role: "inside" }, { layer_id: 18, role: "outside" }],
    });
    expect(layersOf("77")).toEqual([{ layerId: 77, role: "contact" }]);
    expect(layersOf("77:sideways")).toMatchObject({ param: "layers" });
    expect(layersOf("x")).toMatchObject({ code: "bad_param" });
  });
  it("the form is checked before the request: no data, a code of another group, a radius out of range", async () => {
    const model = formOfMeta("Стек и разлом", META);
    const defaults = await ensureDefaults();
    const ctx = faults.ctxOf(model, null, defaults);
    expect(faultMake({ ...EMPTY_FAULT, name: "F" }, ctx)).toMatchObject({ ok: false, issue: { param: "codes" } });
    expect(faultMake({ ...EMPTY_FAULT, name: "F", codes: ["SS"] }, ctx)).toMatchObject({ ok: false, issue: { param: "codes.0" } });
    expect(faultMake({ ...EMPTY_FAULT, name: "F", codes: ["FLT"], radius: "-5" }, ctx)).toMatchObject({ ok: false, issue: { param: "params.radius" } });
    expect(faultMake({ ...EMPTY_FAULT, name: "F", codes: ["FLT"], radius: "abc" }, ctx)).toMatchObject({ ok: false, issue: { param: "params.radius" } });
    expect(faultMake({ ...EMPTY_FAULT, name: "F", layers: [{ layerId: null, role: "contact" }] }, ctx)).toMatchObject({ ok: false, issue: { param: "layers.0" } });
    expect(faultMake({ ...EMPTY_FAULT, name: "F", codes: ["FLT"], radius: "1 000" }, ctx)).toMatchObject({ ok: true, group: { params: { radius: 1000 } } });
    expect(groupRanges(defaults).fault_radius).toMatchObject({ lo: 0, lo_open: true, unit: "м" });
    const r = await run("implicit.fault", { name: "F", codes: "FLT", radius: -5 });
    expect(r.ok).toBe(false);
    expect(r.result).toContain("вне диапазона");
    expect(sent("POST", /faults$/)).toHaveLength(0);
  });
  it("a fault of the model is read into the form: the radius, the layers of the model, the domains", () => {
    const g = faultsOfMeta(META)[0];
    expect(faultFromGroup(g, formOfMeta("", META))).toEqual({
      name: "Разлом",
      codes: [],
      radius: "",
      layers: [{ layerId: 16, role: "contact" }, { layerId: 17, role: "inside" }, { layerId: 18, role: "outside" }],
      domains: [3],
    });
  });
  it("the refusal of the core stands under its field of the window (a fault without data)", async () => {
    await run("implicit.fault", { name: "Пустой" });
    core.force(`POST /implicit/models/${STACK_ID}/faults`, { status: 422, body: R.fault_bad.body });
    const r = await run("implicit.fault", { codes: "FLT" });
    expect(r.ok).toBe(false);
    expect(r.result).toContain("У группы нет данных: ни кодов, ни ручных слоёв.");
    expect(faults.store.get().issue).toMatchObject({ param: "codes", code: "bad_param" });
  });
  it("the window drawn: the name, the codes, the layers, the radius with its range, the domains; the offset is read only", async () => {
    await run("implicit.fault", { group_id: 5 });
    const html = render(createElement(FaultWizard, { winId: "w" }));
    for (const t of ["grp-name", "grp-radius", "grp-codes", "flt-layers", "flt-layer-0", "flt-layer-add", "flt-domains", "flt-domain-3", "grp-save", "grp-save-update", "flt-offset"]) expect(html).toContain(`data-testid="${t}"`);
    expect(html).toContain("Смещение (результат построения, только чтение)");
    expect(html).toContain("среднее 6,873 м");
    expect(html).toMatch(/data-testid="flt-domain-3"[^>]*checked|checked=""[^>]*data-testid="flt-domain-3"/);
    expect(html).toContain("бесконечный");
    expect(html).toContain("Контакт разлома");
  });
});

describe("`implicit.vein_system`", () => {
  it("a new system: POST /vein_system with the veins by age (the first is the youngest); the name by default; the parameters of the veins", async () => {
    const r = await run("implicit.vein_system", { veins: "VN; VN2", offset: 5, t_min: 0.5 });
    expect(r.ok).toBe(true);
    const post = sent("POST", /vein_system$/);
    expect(post).toHaveLength(1);
    expect(post[0].body).toMatchObject({
      expected_version: 4,
      group: {
        type: "vein_system",
        params: { veins: [{ code: "VN", name: "Жила VN", bound: "hull", offset: 5, t_min: 0.5 }, { code: "VN2", name: "Жила VN2", bound: "hull", offset: 5, t_min: 0.5 }] },
      },
    });
    expect((post[0].body.group as { name: string }).name).toMatch(/^Жилы \d+$/);
    expect(r.result).toContain("первая — самая молодая");
    expect(r.result).toContain("Обновить");
  });
  it("«VN:2; VN2:1» — the number is the age: the vein with 1 is the youngest", async () => {
    expect(veinsOfText("VN:2; VN2:1; VN3:3", [])).toMatchObject([{ code: "VN2" }, { code: "VN" }, { code: "VN3" }]);
    expect(veinsOfText("VN; VN", [])).toMatchObject({ param: "params.veins" });
    expect(veinsOfText("VN:x", [])).toMatchObject({ param: "params.veins" });
    await run("implicit.vein_system", { veins: "VN:2; VN2:1", name: "Q" });
    const veins = (sent("POST", /vein_system$/)[0].body.group as { params: { veins: { code: string }[] } }).params.veins;
    expect(veins.map((v) => v.code)).toEqual(["VN2", "VN"]);
  });
  it("the form is checked: fewer than two veins, a code of another group, more than the limit; nothing is sent", async () => {
    const r = await run("implicit.vein_system", { veins: "VN" });
    expect(r.ok).toBe(false);
    expect(r.result).toContain("не меньше двух жил");
    expect((await run("implicit.vein_system", { veins: "INT; VN" })).result).toContain("Код «INT» уже есть в другой группе");
    const model = formOfMeta("Стек и разлом", META);
    const defaults = await ensureDefaults();
    const ctx = veinSystems.ctxOf(model, null, defaults);
    const many = veinsOfText(Array.from({ length: 21 }, (_, i) => `V${i}`).join("; "), []);
    expect(Array.isArray(many)).toBe(true);
    expect(systemMake({ name: "Ж", veins: many as never }, ctx)).toMatchObject({ ok: false, issue: { param: "params.veins" } });
    expect(sent("POST", /vein_system$/)).toHaveLength(0);
    expect(DEFAULTS.params.limits.veins).toBe(20);
  });
  it("the refusal of the core is told as it is", async () => {
    core.force(`POST /implicit/models/${STACK_ID}/vein_system`, { status: 422, body: R.vein_system_bad.body });
    const r = await run("implicit.vein_system", { veins: "VN; VN2" });
    expect(r.ok).toBe(false);
    expect(r.result).toContain("В системе жил нужно не меньше двух жил.");
  });
  it("a system of the model is edited by `group_id`: PUT of the group with the relation and the faults it has; the veins keep their parameters", async () => {
    const o = await app.registry.execute("implicit.vein_system", { model_id: VEINS_ID, group_id: 4 });
    expect(o.ok).toBe(true);
    expect(veinSystems.store.get().form.veins.map((v) => v.code)).toEqual(["VN", "VN2"]);
    const r = await app.registry.execute("implicit.vein_system", { model_id: VEINS_ID, group_id: 4, veins: "VN2; VN" });
    expect(r.ok).toBe(true);
    const put = sent("PUT", /groups\/4$/);
    expect(put).toHaveLength(1);
    expect((put[0].body.group as { params: { veins: { code: string }[] } }).params.veins.map((v) => v.code)).toEqual(["VN2", "VN"]);
    expect(put[0].body.group).toMatchObject({ id: 4, relation: "conformable", faults: [], type: "vein_system" });
    expect(systemFromGroup({ id: 4, name: "Жилы", type: "vein_system", codes: [], relation: "conformable", faults: [], params: { veins: [{ code: "VN", name: "Жила VN", bound: "polygon", polygon: [[0, 0], [1, 0], [1, 1]], offset: 2, t_min: 0 }] } })).toMatchObject({ veins: [{ code: "VN", bound: "polygon", offset: "2", tMin: "" }] });
  });
  it("the window drawn: the veins by age with the codes, the table of the pairs and the line of the intersection after the build", async () => {
    await app.registry.execute("implicit.vein_system", { model_id: VEINS_ID, group_id: 4 });
    const html = render(createElement(VeinSystemForm, { winId: "w" }));
    for (const t of ["vsys-vein-0", "vsys-vein-1", "vsys-code-0", "vsys-up-1", "vsys-down-0", "vsys-add", "vsys-pairs", "vsys-line-42", "grp-save"]) expect(html).toContain(`data-testid="${t}"`);
    const flat = html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").replace(/[  ]/g, " ");
    expect(flat).toContain("Жила VN × Жила VN2");
    expect(flat).toContain("89,9");
    expect(flat).toContain("160,7");
    expect(flat).toContain("первая жила самая молодая");
  });
});

describe("the window of the stack", () => {
  it("drawn: the domains by age with the relations, the check boxes of the faults, the words of what a relation does", async () => {
    await run("implicit.relations");
    const html = render(createElement(StackTab, { winId: "w" }));
    for (const id of [1, 2, 3, 4]) for (const t of [`stk-row-${id}`, `stk-relation-${id}`, `stk-up-${id}`, `stk-down-${id}`, `stk-faults-${id}`]) expect(html).toContain(`data-testid="${t}"`);
    expect(html).toContain('data-testid="stk-fault-3-5"');
    expect(html).toMatch(/checked=""[^>]*data-testid="stk-fault-3-5"|data-testid="stk-fault-3-5"[^>]*checked=""/);
    const flat = html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
    expect(flat).toContain(RELATION_HELP.intrusion);
    expect(flat).toContain("самая старая: отношение не используется");
    expect(flat).toContain("рассечена разломом:");
    expect(flat).toContain("отброшено точек данных: 62");
    expect(html).toMatch(/disabled=""[^>]*data-testid="stk-apply"/); // nothing changed — nothing to write
  });
  it("«Применить»: the order and the relations first, then the faults of each domain that changed", async () => {
    await run("implicit.relations");
    moveDomain(2, -1);
    setRelation(2, "erode");
    toggleFault(1, 5, true);
    toggleFault(3, 5, false);
    expect(stackStore.get().dirty).toBe(true);
    await applyStack(app);
    const order = core.sent.filter((s) => s.method === "PUT").map((s) => s.path);
    expect(order).toEqual([`/implicit/models/${STACK_ID}/relations`, `/implicit/models/${STACK_ID}/groups/1/faults`, `/implicit/models/${STACK_ID}/groups/3/faults`]);
    expect(core.sent.find((s) => s.path.endsWith("/relations"))?.body).toMatchObject({ order: [2, 1, 3, 4], relations: { "2": "erode", "1": "intrusion" } });
    expect(core.sent.find((s) => s.path.endsWith("/groups/1/faults"))?.body).toMatchObject({ fault_ids: [5] });
    expect(core.sent.find((s) => s.path.endsWith("/groups/3/faults"))?.body).toMatchObject({ fault_ids: [] });
    expect(stackStore.get()).toMatchObject({ saving: false, dirty: false, error: null });
    expect(stackStore.get().saved).toContain("Записано");
  });
  it("a refusal of the core stays under the buttons; the draft is kept", async () => {
    await run("implicit.relations");
    setRelation(1, "onlap");
    core.force(`PUT /implicit/models/${STACK_ID}/relations`, { status: 422, body: { code: "bad_relations", message: "Порядок решения замкнут в цикл.", hint: "Измените порядок." } });
    await applyStack(app);
    expect(stackStore.get().error).toContain("Порядок решения замкнут в цикл.");
    expect(stackStore.get().dirty).toBe(true);
    expect(stackStore.get().draft?.relations[1]).toBe("onlap");
    expect(app.store.get().statusNote).toContain("замкнут в цикл");
  });
  it("the shell's window layer knows the three windows of Э19б", async () => {
    await run("implicit.relations");
    await run("implicit.fault", { name: "Ч" });
    await run("implicit.vein_system", { name: "Ч" });
    for (const w of windowStore.get().windows) expect(renderImplicitWindow(w), w.kind).not.toBeNull();
    expect(windowStore.get().windows).toHaveLength(3);
  });
  it("a model that goes takes its windows with it", async () => {
    await run("implicit.relations");
    await run("implicit.fault", { group_id: 5 });
    await run("implicit.vein_system", { name: "Ч" });
    expect(windowStore.get().windows.map((w) => w.kind).sort()).toEqual(["implicit-fault", "implicit-stack", "implicit-vein-system"]);
    core.list = core.list.filter((r) => r.id !== STACK_ID);
    await loadRows();
    expect(windowStore.get().windows).toHaveLength(0);
    expect(stackStore.get().modelId).toBeNull();
  });
});

describe("the deletion of a model with lines", () => {
  it("tells the lines of intersection it took with the outputs", async () => {
    app.registry.hooks.confirm = async () => true;
    core.force(`DELETE /implicit/models/${VEINS_ID}`, { status: 200, body: R.delete_veins.body });
    const r = await app.registry.execute("implicit.delete", { model_id: VEINS_ID }, "claude");
    expect(r.ok).toBe(true);
    expect(r.result).toContain("вместе с каркасами-выходами (6)");
    expect(r.result).toContain("линиями пересечения (1)");
  });
});
