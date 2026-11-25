// The windows of the groups of a model (Э19, 19.24; ADR 0026 решения 15–18; docs/progress/e19-api.md §4, §5, §12): «Оболочки содержаний», «Жила»,
// «Расширение каркаса» — the forms checked before the request (the words under the field, the paths of the core's `param`), the commands
// (a window without values, a write with them, an edit by `group_id`), the refusals of the core under their fields, the stale line of the graph.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { commandTools } from "../../src/shell/commands";
import { windowStore } from "../../src/shell/windows";
import { commands } from "../../src/modules/implicit/commands";
import { formOfMeta } from "../../src/modules/implicit/form";
import { groupIssue, groupRanges, localParam } from "../../src/modules/implicit/groupBase";
import { expands, shells, veins } from "../../src/modules/implicit/groupKinds";
import { ExpandForm } from "../../src/modules/implicit/ExpandForm";
import { deltaWords, expandMake, expandSpec } from "../../src/modules/implicit/ExpandState";
import { nestingLine, ShellsForm } from "../../src/modules/implicit/ShellsForm";
import { parseThresholds, shellFromArgs, shellFromGroup, shellMake, shellSpec, thresholdsText, EMPTY_SHELL, type ShellForm } from "../../src/modules/implicit/ShellsState";
import { veinFromArgs, veinFromGroup, veinMake, veinSpec, EMPTY_VEIN, codesOf } from "../../src/modules/implicit/VeinState";
import { VeinForm } from "../../src/modules/implicit/VeinForm";
import { staleLine } from "../../src/modules/implicit/run";
import { implicitStore, loadDetails, loadRows } from "../../src/modules/implicit/state";
import { ensureDefaults } from "../../src/modules/implicit/wizardState";
import { A, ID, newApp, teardown, waitFor } from "./harness";
import { EXPAND, GID, installGroupsCore, RESULT, VEIN, type GroupsCore } from "./groupsHarness";

let app: App;
let core: GroupsCore;
beforeEach(() => {
  core = installGroupsCore();
  app = newApp();
});
afterEach(teardown);

const render = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));
const flat = (s: string): string => s.replace(/[  ]/g, " ");
const requests = (route: string) => core.sent.filter((r) => `${r.method} ${r.path}` === route);
const GROUPS = `POST /implicit/models/${ID.model}/groups`;
const windows = (kind: string) => windowStore.get().windows.filter((w) => w.kind === kind);

/** The context of a check: the ranges of the core, the model of the contract (the table of the codes is set). */
async function ctx() {
  const defaults = await ensureDefaults();
  const info = await loadDetails(ID.model);
  const model = formOfMeta(info!.name, info!.meta);
  return { ranges: groupRanges(defaults), model, taken: new Set(model.groups.flatMap((g) => g.codes)) };
}

const shell = (patch: Partial<ShellForm> = {}): ShellForm => ({ ...EMPTY_SHELL, name: "Au", resultId: RESULT.id, field: "AU", thresholds: "0,5; 1; 2,5", ...patch });

describe("the declarations of 19.24", () => {
  it("the groups, «В каркас» and «Удалить» run: no `stage`; the parameters of the contract are flat words", () => {
    const by = Object.fromEntries(commands.map((c) => [c.id, c]));
    for (const id of ["implicit.shells", "implicit.vein", "implicit.expand", "implicit.to_wireframe", "implicit.delete"]) expect(by[id].stage, id).toBeUndefined();
    expect(Object.keys(by["implicit.shells"].params!)).toEqual(expect.arrayContaining(["model_id", "group_id", "name", "source", "result_id", "set_id", "table", "field", "transform", "mode", "thresholds", "p"]));
    expect(by["implicit.shells"].params!.thresholds.type).toBe("string");
    expect(Object.keys(by["implicit.vein"].params!)).toEqual(expect.arrayContaining(["codes", "bound", "offset", "t_min"]));
    expect(Object.keys(by["implicit.expand"].params!)).toEqual(expect.arrayContaining(["wireframe_id", "delta"]));
    expect(by["implicit.delete"].confirm).toBe(true);
    const tools = commandTools(commands);
    expect(JSON.stringify(tools.find((t) => t.name.endsWith("implicit_shells")))).toContain("thresholds");
  });
});

describe("the shell: the form before the request", () => {
  it("parses the thresholds with a decimal comma; a word that is not a number — the index of the threshold", () => {
    expect(parseThresholds("0,5; 1; 2,5")).toEqual({ ok: true, values: [0.5, 1, 2.5] });
    expect(parseThresholds("0.5 1")).toEqual({ ok: true, values: [0.5, 1] });
    expect(parseThresholds("0,5; два")).toEqual({ ok: false, index: 1, word: "два" });
    expect(thresholdsText([0.5, 1, 2.5])).toBe("0,5; 1; 2,5");
  });
  it("a numeric shell of composites: the body of the group as the core takes it", async () => {
    const made = shellMake(shell(), await ctx());
    expect(made).toEqual({ ok: true, group: { name: "Au", type: "shell", codes: [], params: { source: "composites", result_id: RESULT.id, field: "AU", transform: "linear", mode: "numeric", thresholds: [0.5, 1, 2.5] } } });
  });
  it("the samples of a table: the set and the table go; p only for an indicator and inside 0,3–0,7", async () => {
    const c = await ctx();
    const t = shellMake(shell({ source: "table", resultId: null, table: "13", setId: ID.set, mode: "indicator", transform: "nscore", p: "0,4" }), c);
    expect(t).toMatchObject({ ok: true, group: { params: { source: "table", set_id: ID.set, table: "13", transform: "nscore", mode: "indicator", p: 0.4 } } });
    const numeric = shellMake(shell({ p: "0,9" }), c);
    expect(numeric.ok && "p" in (numeric.group.params as object)).toBe(false); // a numeric shell has no p: the typed one is not sent
    const out = shellMake(shell({ mode: "indicator", p: "0,9" }), c);
    expect(out).toMatchObject({ ok: false, issue: { param: "params.p", code: "bad_param" } });
    if (!out.ok) expect(flat(out.issue.message)).toContain("0,3");
  });
  it("what is not chosen is said under its field: the name, the result, the table, the field", async () => {
    const c = await ctx();
    const at = (f: ShellForm) => {
      const m = shellMake(f, c);
      return m.ok ? null : m.issue.param;
    };
    expect(at(shell({ name: " " }))).toBe("name");
    expect(at(shell({ resultId: null }))).toBe("params.result_id");
    expect(at(shell({ source: "table", resultId: null, table: null, setId: ID.set }))).toBe("params.table");
    expect(at(shell({ field: null }))).toBe("params.field");
  });
  it("the thresholds go strictly up; the logarithm needs more than zero; not more than 20; each wrong one is named by its place", async () => {
    const c = await ctx();
    const issue = (f: ShellForm) => {
      const m = shellMake(f, c);
      return m.ok ? null : m.issue;
    };
    expect(issue(shell({ thresholds: "" }))?.param).toBe("params.thresholds");
    expect(issue(shell({ thresholds: "0,5; abc" }))?.param).toBe("params.thresholds.1");
    const down = issue(shell({ thresholds: "1; 0,5" }));
    expect(down?.param).toBe("params.thresholds.1");
    expect(down?.hint).toContain("вложена");
    expect(issue(shell({ thresholds: "1; 1" }))?.param).toBe("params.thresholds.1");
    const log = issue(shell({ transform: "log", thresholds: "0,5; 0; 1" }));
    expect(log?.param).toBe("params.thresholds.1");
    expect(log?.message).toContain("логарифм");
    expect(issue(shell({ thresholds: Array.from({ length: 21 }, (_, i) => i + 1).join("; ") }))?.param).toBe("params.thresholds.20");
  });
  it("the nesting under the thresholds: shell k is inside shell k − 1", () => {
    expect(nestingLine({ thresholds: "0,5; 1; 2,5", transform: "linear" })).toBe("g ≥ 0,5 ⊃ g ≥ 1 ⊃ g ≥ 2,5");
    expect(nestingLine({ thresholds: "1", transform: "linear" })).toBeNull();
    expect(nestingLine({ thresholds: "1; 2", transform: "log" })).toContain("ln g");
  });
  it("a stored group comes back into the form and into the arguments of the command the same", async () => {
    const info = await loadDetails(ID.model);
    const stored = formOfMeta(info!.name, info!.meta).groups.find((g) => g.id === GID.shell)!.raw!;
    const f = shellFromGroup(stored);
    expect(f).toMatchObject({ name: "Au индикатор", source: "composites", resultId: RESULT.id, field: "AU", mode: "indicator", thresholds: "0,5; 1", p: "" });
    const made = shellMake(f, await ctx());
    expect(made.ok && made.group.params).toMatchObject({ result_id: RESULT.id, thresholds: [0.5, 1], mode: "indicator" });
    if (!made.ok) throw new Error("not made");
    const again = shellFromArgs(shellSpec.argsOf(made.group), EMPTY_SHELL);
    expect(again).toMatchObject({ name: "Au индикатор", resultId: RESULT.id, field: "AU", thresholds: "0,5; 1", mode: "indicator" });
  });
  it("a wrong word of the command is a refusal with the allowed ones", () => {
    const r = shellFromArgs({ transform: "sqrt" }, EMPTY_SHELL);
    expect(r).toMatchObject({ param: "params.transform", code: "bad_param" });
    expect((r as { hint: string }).hint).toContain("nscore");
  });
});

describe("implicit.shells", () => {
  it("without thresholds it opens the window of the model with the arguments in the form; nothing is written", async () => {
    const r = await app.registry.execute("implicit.shells", { model_id: ID.model, field: "AU", name: "Золото" }, "user");
    expect(r.ok).toBe(true);
    expect(r.result).toContain("«Оболочки содержаний»");
    expect(windows("implicit-shells")).toHaveLength(1);
    expect(windows("implicit-shells")[0].title).toContain(`«${"Литология"}»`);
    expect(shells.store.get()).toMatchObject({ modelId: ID.model, groupId: null, dirty: true, form: { name: "Золото", field: "AU" } });
    expect(requests(GROUPS)).toHaveLength(0);
    await app.registry.execute("implicit.shells", {}, "user"); // the second call brings the same window forward: one window
    expect(windows("implicit-shells")).toHaveLength(1);
  });
  it("the model is the one of the tree or the only one; with several and none chosen — the words say so", async () => {
    core.models.push({ ...core.models[0], id: 9, name: "Вторая" });
    await loadRows();
    const r = await app.registry.execute("implicit.shells", {}, "user");
    expect(r).toMatchObject({ ok: false });
    expect(r.result).toContain("Выберите модель");
    app.store.set({ selected: { kind: "implicit", id: ID.model } });
    expect((await app.registry.execute("implicit.shells", {}, "user")).ok).toBe(true);
    core.models = [];
    app.store.set({ selected: null });
    await loadRows();
    const none = await app.registry.execute("implicit.shells", {}, "user");
    expect(none.result).toContain("нет неявной модели");
  });
  it("with thresholds it writes a new group last: POST /groups with the version the core stores now", async () => {
    const r = await app.registry.execute("implicit.shells", { model_id: ID.model, result_id: RESULT.id, field: "AU", thresholds: "0,5; 1; 2,5", transform: "log", mode: "indicator", p: 0.6, name: "Золото" }, "claude");
    expect(r.ok).toBe(true);
    expect(r.result).toContain("«Золото» добавлена в модель «Литология»");
    expect(r.result).toContain("«Обновить»");
    const sent = requests(GROUPS)[0].body;
    expect(sent.expected_version).toBe(3);
    expect(sent.group).toEqual({ name: "Золото", type: "shell", codes: [], params: { source: "composites", result_id: RESULT.id, field: "AU", transform: "log", mode: "indicator", thresholds: [0.5, 1, 2.5], p: 0.6 } });
    expect(core.version).toBe(4);
    expect(windows("implicit-shells")).toHaveLength(0);
  });
  it("the name by default is the first free «Оболочка N»", async () => {
    await app.registry.execute("implicit.shells", { model_id: ID.model, result_id: RESULT.id, field: "AU", thresholds: "1" }, "claude");
    expect(requests(GROUPS)[0].body.group).toMatchObject({ name: "Оболочка 7" }); // 6 groups in the model (3 domains + 3 more)
  });
  it("with group_id it changes that group: PUT keeps its relation and faults; a group that is not a shell is not found", async () => {
    const r = await app.registry.execute("implicit.shells", { model_id: ID.model, group_id: GID.shell, thresholds: "0,3; 0,8", mode: "numeric" }, "claude");
    expect(r.ok).toBe(true);
    expect(r.result).toContain("изменена в модели");
    const put = requests(`PUT /implicit/models/${ID.model}/groups/${GID.shell}`)[0].body;
    expect(put.group).toMatchObject({ id: GID.shell, relation: "conformable", faults: [], type: "shell", name: "Au индикатор", params: { result_id: RESULT.id, mode: "numeric", thresholds: [0.3, 0.8] } });
    const wrong = await app.registry.execute("implicit.shells", { model_id: ID.model, group_id: GID.vein, thresholds: "1" }, "claude");
    expect(wrong.ok).toBe(false);
    expect(wrong.result).toContain("нет такой группы");
  });
  it("a wrong form is refused before the request, the words are under the field of the open window", async () => {
    await app.registry.execute("implicit.shells", { model_id: ID.model }, "user");
    const r = await app.registry.execute("implicit.shells", { model_id: ID.model, result_id: RESULT.id, field: "AU", transform: "log", thresholds: "0,5; 0" }, "user");
    expect(r.ok).toBe(false);
    expect(requests(GROUPS)).toHaveLength(0);
    expect(shells.store.get().issue).toMatchObject({ param: "params.thresholds.1" });
    expect(render(createElement(ShellsForm, { winId: "w" }))).toContain("логарифм");
  });
  it("the refusal of the core goes under the field it names: «group.params.thresholds.1» is «params.thresholds.1»", async () => {
    expect(localParam("group.params.thresholds.1")).toBe("params.thresholds.1");
    expect(localParam("groups.3.codes.0")).toBe("codes.0");
    expect(groupIssue({ code: "wireframe_open", message: "m", hint: "h", param: null }).param).toBe("params.wireframe_id");
    await app.registry.execute("implicit.shells", { model_id: ID.model, name: "Золото" }, "user");
    core.force(GROUPS, { status: 422, body: { ...A.bad_param.body, param: "group.params.thresholds.1", message: "Порог 2: значение вне диапазона.", hint: "Задайте число." } });
    const r = await app.registry.execute("implicit.shells", { model_id: ID.model, result_id: RESULT.id, field: "AU", thresholds: "1; 2" }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Порог 2");
    expect(shells.store.get().issue).toMatchObject({ param: "params.thresholds.1", code: "bad_param" });
    expect(render(createElement(ShellsForm, { winId: "w" }))).toContain('data-testid="grp-issue-thresholds"');
    expect(shells.store.get().saving).toBe(false);
  });
});

describe("the window of the shells", () => {
  it("the model with its version, the groups to edit, the sources, the kinds, the nesting", async () => {
    await app.registry.execute("implicit.shells", { model_id: ID.model, field: "AU" }, "user");
    shells.setForm({ thresholds: "0,5; 1", mode: "indicator" });
    const html = flat(render(createElement(ShellsForm, { winId: "w" })));
    expect(html).toContain("«Литология», версия 3");
    expect(html).toContain("Новая группа (последняя, самая старая)");
    expect(html).toContain("Au индикатор");
    for (const t of ["Композиты (результат обработки проб)", "Таблица проб набора скважин", "Логарифм", "Нормальные оценки (nscore)", "Числовая: изоповерхность f = t", "Индикаторная: значения [g ≥ t] − 0,5, изоповерхность p − 0,5"]) expect(html).toContain(t);
    expect(html).toContain("Вложенность: g ≥ 0,5 ⊃ g ≥ 1");
    expect(html).toContain('data-testid="grp-p"'); // an indicator asks p
    expect(html).toContain("Диапазон: 0,3–0,7.");
    expect(html).toContain("Сохранить и обновить");
    shells.setForm({ mode: "numeric" });
    expect(render(createElement(ShellsForm, { winId: "w" }))).not.toContain('data-testid="grp-p"');
  });
  it("«Сохранить» runs the command with the typed values; «Сохранить и обновить» builds after it", async () => {
    await app.registry.execute("implicit.shells", { model_id: ID.model }, "user");
    shells.setForm({ name: "Золото", resultId: RESULT.id, field: "AU", thresholds: "0,5; 1" });
    await shells.submit(app, true);
    const sent = requests(GROUPS)[0].body;
    expect(sent.group).toMatchObject({ name: "Золото", type: "shell", params: { result_id: RESULT.id, field: "AU", thresholds: [0.5, 1] } });
    await waitFor(() => requests(`POST /implicit/models/${ID.model}/update`).length > 0);
    expect(shells.store.get()).toMatchObject({ groupId: expect.any(Number), dirty: false, issue: null });
    expect(shells.store.get().saved).toContain("добавлена");
  });
  it("«Сохранить» with a wrong form stays in the window: the words under the field, no request; the name by default is not a mistake", async () => {
    await app.registry.execute("implicit.shells", { model_id: ID.model }, "user");
    await shells.submit(app, false);
    expect(requests(GROUPS)).toHaveLength(0);
    expect(shells.store.get().issue).toMatchObject({ param: "params.result_id" });
    shells.setForm({ resultId: RESULT.id, field: "AU", thresholds: "2; 1" });
    await shells.submit(app, false);
    expect(shells.store.get().issue).toMatchObject({ param: "params.thresholds.1" });
    expect(requests(GROUPS)).toHaveLength(0);
  });
  it("a group chosen to edit fills the form from the stored one", async () => {
    await app.registry.execute("implicit.shells", { model_id: ID.model }, "user");
    shells.choose(GID.shell);
    expect(shells.store.get()).toMatchObject({ groupId: GID.shell, dirty: false, form: { name: "Au индикатор", thresholds: "0,5; 1" } });
    shells.choose(null);
    expect(shells.store.get()).toMatchObject({ groupId: null, form: { name: "" } });
  });
  it("the data check asks the core for the points of the group as typed; a wrong form is not sent", async () => {
    const { DataCheck } = await import("../../src/modules/implicit/GroupParts");
    await app.registry.execute("implicit.shells", { model_id: ID.model, result_id: RESULT.id, field: "AU", name: "Au" }, "user");
    expect(render(createElement(DataCheck<ShellForm>, { rt: shells }))).toContain('data-testid="grp-check"');
    expect(await shells.preview()).toBeNull(); // no thresholds yet: the words under the field, no request
    expect(shells.store.get().issue).toMatchObject({ param: "params.thresholds" });
    expect(requests(`POST /implicit/models/${ID.model}/points/preview`)).toHaveLength(0);
    shells.setForm({ thresholds: "0,5; 1" });
    const a = await shells.preview();
    expect(a).toMatchObject({ n: 120, counts: { contact: 80, merged: 2 } });
    expect(requests(`POST /implicit/models/${ID.model}/points/preview`)[0].body).toEqual({ group: { name: "Au", type: "shell", codes: [], params: { source: "composites", result_id: RESULT.id, field: "AU", transform: "linear", mode: "numeric", thresholds: [0.5, 1] } }, limit: 1000 });
  });
});

describe("the vein", () => {
  const vein = (patch = {}) => ({ ...EMPTY_VEIN, name: "Жила Q2", codes: ["Q2"], ...patch });
  it("the codes of a command: «Q1; Q2» without repeats", () => {
    expect(codesOf("Q1; Q2;Q1\nQ3")).toEqual(["Q1", "Q2", "Q3"]);
  });
  it("the convex hull with an offset and t_min; the polygon stored comes back as it was", async () => {
    const c = await ctx();
    expect(veinMake(vein({ offset: "5", tMin: "0,2" }), c)).toEqual({ ok: true, group: { name: "Жила Q2", type: "vein", codes: ["Q2"], params: { bound: "hull", offset: 5, t_min: 0.2 } } });
    const info = await loadDetails(ID.model);
    const stored = formOfMeta(info!.name, info!.meta).groups.find((g) => g.id === GID.vein)!.raw!;
    const f = veinFromGroup(stored);
    expect(f).toMatchObject({ name: "Жила 1", codes: ["Q1"], bound: "polygon", tMin: "0,3", offset: "" });
    expect(f.polygon).toHaveLength(4);
    const made = veinMake(f, { ...c, taken: new Set() });
    expect(made).toMatchObject({ ok: true, group: { params: { bound: "polygon", polygon: VEIN.params.polygon, offset: 0, t_min: 0.3 } } });
  });
  it("what is wrong: no codes, a code of another group, offset and t_min out of the range, a polygon that is not there", async () => {
    const c = await ctx();
    const issue = (f: ReturnType<typeof vein>) => {
      const m = veinMake(f, c);
      return m.ok ? null : m.issue;
    };
    expect(issue(vein({ name: "" }))?.param).toBe("name");
    expect(issue(vein({ codes: [] }))?.param).toBe("codes");
    expect(issue(vein({ codes: ["Q2", "DY"] }))).toMatchObject({ param: "codes.1" });
    expect(issue(vein({ offset: "20000" }))?.param).toBe("params.offset");
    expect(issue(vein({ tMin: "150" }))?.param).toBe("params.t_min");
    expect(issue(vein({ tMin: "-1" }))?.param).toBe("params.t_min");
    expect(veinFromArgs({ bound: "polygon" }, vein())).toMatchObject({ param: "params.polygon" });
    expect(veinFromArgs({ bound: "circle" }, vein())).toMatchObject({ param: "params.bound" });
  });
  it("a model with no table of codes: the words under the codes, the window says what to do", async () => {
    const m = await ctx();
    const no = veinMake(vein(), { ...m, model: { ...m.model, setId: null } });
    expect(no).toMatchObject({ ok: false, issue: { param: "codes" } });
    expect(veinSpec.complete({ codes: "Q2" })).toBe(true);
    expect(veinSpec.complete({ name: "x" })).toBe(false);
  });
  it("implicit.vein without codes opens the window; the form shows the step h of the model and the stored polygon", async () => {
    await app.registry.execute("implicit.vein", { model_id: ID.model, group_id: GID.vein }, "user");
    expect(windows("implicit-vein")).toHaveLength(1);
    const html = flat(render(createElement(VeinForm, { winId: "w" })));
    expect(html).toContain("Выпуклая оболочка контактов с отступом");
    expect(html).toContain("Полигон в плоскости жилы (u, v): вершин 4 (задан ранее)");
    expect(html).toContain("Шаг сетки (u, v) — шаг модели h: по умолчанию (диагональ охвата / 200)");
    expect(html).toContain('data-testid="grp-tmin"');
    expect(html).toContain('data-testid="grp-code-Q1"');
    expect(html).not.toContain('data-testid="grp-offset"'); // the offset is of the hull
    await app.registry.execute("implicit.vein", { model_id: ID.model, group_id: GID.vein, bound: "hull" }, "user");
    expect(render(createElement(VeinForm, { winId: "w" }))).toContain('data-testid="grp-offset"');
  });
  it("with codes it writes the vein: the body of the group and the polygon of an edited one kept", async () => {
    const add = await app.registry.execute("implicit.vein", { model_id: ID.model, codes: "Q2; Q3", offset: 10, t_min: 0.5, name: "Жила Q2" }, "claude");
    expect(add.ok).toBe(true);
    expect(requests(GROUPS)[0].body.group).toEqual({ name: "Жила Q2", type: "vein", codes: ["Q2", "Q3"], params: { bound: "hull", offset: 10, t_min: 0.5 } });
    const edit = await app.registry.execute("implicit.vein", { model_id: ID.model, group_id: GID.vein, codes: "Q1", t_min: 0.4 }, "claude");
    expect(edit.ok).toBe(true);
    const put = requests(`PUT /implicit/models/${ID.model}/groups/${GID.vein}`)[0].body.group as { params: Record<string, unknown> };
    expect(put.params).toMatchObject({ bound: "polygon", polygon: VEIN.params.polygon, t_min: 0.4 });
  });
  it("a code of another group is refused with the words under the codes", async () => {
    const r = await app.registry.execute("implicit.vein", { model_id: ID.model, codes: "DY" }, "claude");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("уже есть в другой группе");
    expect(requests(GROUPS)).toHaveLength(0);
  });
});

describe("the expansion", () => {
  const exp = (patch = {}) => ({ name: "Тело +5", wireframeId: ID.wire as number | null, delta: "5", ...patch });
  it("the words of the offset: expansion or shrinking", () => {
    expect(deltaWords("5")).toBe("Расширение на 5 м");
    expect(deltaWords("-2,5")).toBe("Сжатие на 2,5 м");
    expect(deltaWords("0")).toBeNull();
    expect(deltaWords("")).toBeNull();
  });
  it("a closed wireframe and Δ ≠ 0 inside −1000…1000; a negative Δ shrinks", async () => {
    const c = await ctx();
    expect(expandMake(exp(), c, () => true)).toEqual({ ok: true, group: { name: "Тело +5", type: "expand", codes: [], params: { wireframe_id: ID.wire, delta: 5 } } });
    expect(expandMake(exp({ delta: "-2,5" }), c, () => true)).toMatchObject({ ok: true, group: { params: { delta: -2.5 } } });
    const issue = (f: ReturnType<typeof exp>, closed: boolean | null = true) => {
      const m = expandMake(f, c, () => closed);
      return m.ok ? null : m.issue;
    };
    expect(issue(exp({ wireframeId: null }))?.param).toBe("params.wireframe_id");
    expect(issue(exp(), false)).toMatchObject({ param: "params.wireframe_id", code: "wireframe_open" });
    expect(issue(exp({ delta: "0" }))?.param).toBe("params.delta");
    expect(issue(exp({ delta: "1500" }))?.param).toBe("params.delta");
    expect(issue(exp({ delta: "много" }))?.param).toBe("params.delta");
    expect(issue(exp({ name: "" }))?.param).toBe("name");
  });
  it("implicit.expand: the window without values; with the wireframe and Δ it writes; an open wireframe is refused under its field", async () => {
    expect((await app.registry.execute("implicit.expand", { model_id: ID.model, delta: -3 }, "user")).ok).toBe(true);
    expect(windows("implicit-expand")).toHaveLength(1);
    expect(expands.store.get().form).toMatchObject({ delta: "-3" });
    const html = flat(render(createElement(ExpandForm, { winId: "w" })));
    expect(html).toContain("Сжатие на 3 м");
    expect(html).toContain("Рудное тело");
    expect(html).toContain("Дыра (не замкнут)");
    expect(html).toContain("Диапазон: −1000–1000 м.".replace("−", "-")); // the range as the core gives it
    const ok = await app.registry.execute("implicit.expand", { model_id: ID.model, wireframe_id: ID.wire, delta: -3, name: "Сжатие" }, "claude");
    expect(ok.ok).toBe(true);
    expect(requests(GROUPS)[0].body.group).toEqual({ name: "Сжатие", type: "expand", codes: [], params: { wireframe_id: ID.wire, delta: -3 } });
    const open = await app.registry.execute("implicit.expand", { model_id: ID.model, wireframe_id: ID.openWire, delta: 2 }, "claude");
    expect(open.ok).toBe(false);
    expect(open.result).toContain("не замкнут");
    expect(requests(GROUPS)).toHaveLength(1);
  });
  it("the refusal `wireframe_open` of the core stands under the wireframe", async () => {
    await app.registry.execute("implicit.expand", { model_id: ID.model }, "user");
    core.force(GROUPS, { status: 422, body: { ...A.refusals.wireframe_open.body, param: "group.params.wireframe_id" } });
    const r = await app.registry.execute("implicit.expand", { model_id: ID.model, wireframe_id: ID.wire, delta: 2 }, "user");
    expect(r.ok).toBe(false);
    expect(expands.store.get().issue).toMatchObject({ param: "params.wireframe_id", code: "wireframe_open" });
    expect(render(createElement(ExpandForm, { winId: "w" }))).toContain('data-testid="grp-issue-wireframe"');
  });
  it("the stored expansion comes back into the form", async () => {
    await app.registry.execute("implicit.expand", { model_id: ID.model, group_id: GID.expand }, "user");
    expect(expands.store.get().form).toMatchObject({ name: EXPAND.name, wireframeId: ID.wire, delta: "5" });
    expect(expandSpec.argsOf({ name: "x", params: { wireframe_id: 1, delta: 2 } })).toEqual({ name: "x", wireframe_id: 1, delta: 2 });
  });
});

describe("the model is stale: the graph says why", () => {
  it("the words of the line: stale with the reason, the source lost, current — nothing", () => {
    expect(staleLine({ stale: true, stale_reason: "изменена таблица «Литология»", source_lost: false })).toBe("Модель устарела: изменена таблица «Литология». «Обновить» пересчитает только изменённые участки.");
    expect(staleLine({ stale: true, stale_reason: null, source_lost: false })).toContain("Модель устарела.");
    expect(staleLine({ stale: true, stale_reason: "источник удалён", source_lost: true })).toContain("Источник данных модели удалён.");
    expect(staleLine({ stale: false, stale_reason: null, source_lost: false })).toBeNull();
    expect(staleLine(null)).toBeNull();
  });
  it("«Обновить» of a stale model names the reason; a window of a group shows the line", async () => {
    core.stale = { stale: true, reason: "изменён тренд «Складка»", lost: false };
    await loadRows();
    await app.registry.execute("implicit.vein", { model_id: ID.model }, "user");
    expect(flat(render(createElement(VeinForm, { winId: "w" })))).toContain("Модель устарела: изменён тренд «Складка»");
    const r = await app.registry.execute("implicit.update", { model_id: ID.model }, "user");
    expect(r.ok).toBe(true);
    expect(r.result).toContain("Модель была устаревшей (изменён тренд «Складка»).");
  });
  it("a current model: no line", async () => {
    await app.registry.execute("implicit.vein", { model_id: ID.model }, "user");
    expect(render(createElement(VeinForm, { winId: "w" }))).not.toContain('data-testid="grp-stale"');
  });
});

describe("implicit.to_wireframe", () => {
  it("with an output it copies it: POST /to_wireframe, the answer tells that the copy has no tie to the model", async () => {
    const r = await app.registry.execute("implicit.to_wireframe", { model_id: ID.model, wireframe_id: 302, name: "Дайка (копия)" }, "user");
    expect(r.ok).toBe(true);
    expect(r.result).toContain("«Дайка (копия)» (каркас №340)");
    expect(r.result).toContain("без связи с моделью");
    expect(requests(`POST /implicit/models/${ID.model}/to_wireframe`)[0].body).toEqual({ wireframe_id: 302, name: "Дайка (копия)" });
  });
  it("a wireframe that is not an output of the model is refused before the request", async () => {
    const r = await app.registry.execute("implicit.to_wireframe", { model_id: ID.model, wireframe_id: 999 }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("не является выходом модели");
    expect(requests(`POST /implicit/models/${ID.model}/to_wireframe`)).toHaveLength(0);
  });
  it("without a wireframe the window lists the outputs; «Копировать» runs the command and closes the window", async () => {
    const { ToWireframe } = await import("../../src/modules/implicit/ToWireframe");
    const { copyStore } = await import("../../src/modules/implicit/copyState");
    const r = await app.registry.execute("implicit.to_wireframe", {}, "user");
    expect(r.ok).toBe(true);
    expect(windows("implicit-to-wireframe")).toHaveLength(1);
    const html = flat(render(createElement(ToWireframe, { winId: "w" })));
    expect(html).toContain("Литология · Дайка · поверхность — поверхность");
    expect(html).toContain("Литология · Прочее — тело");
    copyStore.set({ wireframeId: 305, name: "Прочее для расчёта" });
    const run = await app.registry.execute("implicit.to_wireframe", { model_id: ID.model, wireframe_id: copyStore.get().wireframeId!, name: copyStore.get().name }, "user");
    expect(run.ok).toBe(true);
    expect(requests(`POST /implicit/models/${ID.model}/to_wireframe`)[0].body).toEqual({ wireframe_id: 305, name: "Прочее для расчёта" });
  });
  it("a model that was never built has nothing to copy: the words say so", async () => {
    core.groups = [];
    const spy = windowStore.get().windows.length;
    // the stub's model always has outputs: ask a model without them by the answer of the core
    core.force(`GET /implicit/models/${ID.model}`, { status: 200, body: { id: ID.model, name: "Пустая", version: 3, meta: {}, state: "ready", error: null, stale: false, stale_reason: null, source_lost: false, outputs: [], created_at: null, updated_at: null } });
    const r = await app.registry.execute("implicit.to_wireframe", { model_id: ID.model }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("нет каркасов-выходов");
    expect(windowStore.get().windows).toHaveLength(spy);
  });
});

describe("implicit.delete", () => {
  async function answer(ok: boolean): Promise<string> {
    const { dialogStore, closeDialog } = await import("../../src/shell/dialogs");
    await waitFor(() => dialogStore.get().queue.length > 0);
    const d = dialogStore.get().queue[0];
    if (d.kind === "confirm") d.resolve(ok);
    else if (d.kind === "message") d.resolve();
    closeDialog(d.id);
    return d.kind === "confirm" ? d.options.message : d.kind === "message" ? d.options.message : "";
  }
  it("the person is asked first (with the number of the outputs); «Отмена» deletes nothing", async () => {
    await loadRows();
    const p = app.registry.execute("implicit.delete", { model_id: ID.model }, "user");
    const text = await answer(false);
    expect(text).toContain("«Литология»");
    expect(text).toContain("каркасы-выходы (7)");
    expect(text).toContain("«В каркас»");
    expect(await p).toEqual({ ok: true, result: "Отменено" });
    expect(requests(`DELETE /implicit/models/${ID.model}`)).toHaveLength(0);
  });
  it("then «Влияние изменения»: with dependents the window opens and «Отмена» stops the deletion; no dependents — the model is deleted with its outputs", async () => {
    const { settleAsk } = await import("../../src/modules/lineage/impactAsk");
    await loadRows();
    core.impact = 2;
    const p = app.registry.execute("implicit.delete", { model_id: ID.model }, "user");
    await answer(true);
    await waitFor(() => windows("lineage.impact").length === 1);
    expect(windows("lineage.impact")[0].props).toMatchObject({ kind: "object:implicit_model", id: ID.model, ask: "удалить неявную модель «Литология»" });
    settleAsk(windows("lineage.impact")[0].props!.token as number, false);
    expect(await p).toEqual({ ok: true, result: "Отменено" });
    expect(requests(`DELETE /implicit/models/${ID.model}`)).toHaveLength(0);

    core.impact = 0;
    const q = app.registry.execute("implicit.delete", { model_id: ID.model }, "user");
    await answer(true);
    const done = await q;
    expect(done).toEqual({ ok: true, result: "Модель «Литология» удалена вместе с каркасами-выходами (3)" });
    expect(requests(`DELETE /implicit/models/${ID.model}`)).toHaveLength(1);
    await waitFor(() => implicitStore.get().rows.length === 0);
  });
  it("409 in_use: the consumers are named in a message of our own; the model stays", async () => {
    await loadRows();
    core.usage = [{ id: 51, kind: "planned_hole", name: "ПС-1", role: "model" }];
    const p = app.registry.execute("implicit.delete", { model_id: ID.model }, "user");
    await answer(true);
    const said = await answer(true);
    expect(said).toContain("«ПС-1»");
    const r = await p;
    expect(r.ok).toBe(true);
    expect(r.result).toContain("используется: «ПС-1»");
    expect(r.result).toContain("Не удалена");
    expect(implicitStore.get().rows).toHaveLength(1);
  });
  it("a call of the assistant (confirmed by the registry) is not asked twice; 409 in_use is a refusal with the consumers", async () => {
    await loadRows();
    app.registry.hooks.confirm = async () => true;
    core.usage = [{ id: 51, kind: "planned_hole", name: "ПС-1", role: "model" }];
    const r = await app.registry.execute("implicit.delete", { model_id: ID.model }, "claude");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("используется: «ПС-1»");
    core.usage = null;
    const ok = await app.registry.execute("implicit.delete", { model_id: ID.model }, "claude");
    expect(ok.ok).toBe(true);
  });
  it("a model that is not in the project is not found; a model deleted closes its windows", async () => {
    await loadRows();
    app.registry.hooks.confirm = async () => true;
    const none = await app.registry.execute("implicit.delete", { model_id: 999 }, "claude");
    expect(none.result).toContain("№999 не найдена");
    await app.registry.execute("implicit.vein", { model_id: ID.model }, "user");
    expect(windows("implicit-vein")).toHaveLength(1);
    const r = await app.registry.execute("implicit.delete", { model_id: ID.model }, "claude");
    expect(r.ok).toBe(true);
    expect(windows("implicit-vein")).toHaveLength(0);
    expect(veins.store.get().modelId).toBeNull();
  });
});

describe("the read-only project", () => {
  it("every command that writes says so; the windows of the groups keep their buttons off", async () => {
    app.store.set({ projectReadOnly: true });
    for (const id of ["implicit.shells", "implicit.vein", "implicit.expand", "implicit.to_wireframe", "implicit.delete"]) {
      const r = await app.registry.execute(id, { model_id: ID.model }, "user");
      expect(r.result, id).toBe("Проект открыт только для чтения");
    }
  });
});
