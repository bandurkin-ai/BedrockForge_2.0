// The windows «Заполнить каркасами» / «Кодировать каркасами» and the commands `blocks.fill`, `blocks.code` (ТЗ 8.7.3; ADR 0023
// §6, решения 8, 9; Э17 17.37) on the REAL answers of the core (`core/tests/data/blocks/build_window_answers.json`, made by
// `core/tests/test_blocks_build_window_answers.py` through `POST /blocks/fill`, `POST /blocks/{id}/code` and the jobs
// `blocks.fill` / `blocks.code`): the request of the form = the body the core got; the result read as the core wrote it
// (the codes, the volumes, the warning «Каркас не пересекает сетку в плане»); the course by the stages and the cancel; the
// refusals 404 / 409 / 422 and the failure of the job in Russian in their place of the form (the row of the wireframe,
// the field, «Перечитать»).
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../src/shell/AppContext";
import type { App } from "../src/shell/app";
import { refresh } from "../src/modules/jobs/state";
import { readFill, STAGES, type FillResult, type Refusal } from "../src/modules/blocks/build/api";
import { openBuildWindow, windowOfKind } from "../src/modules/blocks/build/bind";
import { CodeWindow } from "../src/modules/blocks/build/CodeWindow";
import { FillWindow } from "../src/modules/blocks/build/FillWindow";
import { argsOfCode, argsOfFill, bodyOfCode, bodyOfFill, EMPTY_CODE, EMPTY_FILL, fillFormOfArgs, issueOfFill, rowsOfWires, wiresArg, wiresOfArgs, type CodeForm, type FillForm } from "../src/modules/blocks/build/fillForm";
import { codeLines, fillSummary, stageIndex } from "../src/modules/blocks/build/format";
import { FillResultView } from "../src/modules/blocks/build/Results";
import { cancelOp } from "../src/modules/blocks/build/run";
import { buildStore, form, putForm, runOf } from "../src/modules/blocks/build/state";
import { A, atVersion, canon, flat, IDS, installCore, newApp, teardown, type Core, type JobCase, type RefusedCase } from "./blocks-build-harness";

let app: App;
let core: Core;

beforeEach(() => {
  core = installCore();
  app = newApp();
});
afterEach(teardown);

const FILL = A.fill.jobs as Record<string, JobCase>;
const CODE = A.code.jobs as Record<string, JobCase>;
const resultOf = (c: JobCase): FillResult => readFill(c.job.result)!;
const render = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));
const refusalOfCase = (r: RefusedCase): Refusal => ({ code: String(r.body.code), message: String(r.body.message), hint: String(r.body.hint), param: typeof r.body.param === "string" ? r.body.param : null });

const FILL_FORMS: Record<string, Partial<FillForm>> = {
  both: { prototypeId: IDS.prototype, wires: [{ id: IDS.wire_a, code: "Руда", priority: "2" }, { id: IDS.wire_b, code: "", priority: "" }], mode: "both", outsideCode: "вне", name: "Заполнение" },
  inside: { prototypeId: IDS.prototype, wires: [{ id: IDS.wire_a, code: "", priority: "" }], mode: "inside", levelsOn: true, levels: ["0", "0", "0"], m: "8" },
  outside_far: { prototypeId: IDS.prototype, wires: [{ id: IDS.far, code: "", priority: "" }], mode: "inside" },
  crossing: { prototypeId: IDS.prototype, wires: [{ id: IDS.crossing, code: "", priority: "" }], mode: "inside" },
};
const fillFormOf = (name: string): FillForm => ({ ...EMPTY_FILL, ...FILL_FORMS[name] });
const CODE_FORMS: Record<string, Partial<CodeForm>> = {
  centre: { modelId: IDS.plain, wires: [{ id: IDS.wire_a, code: "", priority: "" }], mode: "inside", split: false, codeField: "ZONE" },
  split: { modelId: IDS.plain, wires: [{ id: IDS.wire_a, code: "Руда", priority: "" }], mode: "both", split: true, outsideCode: "вне", codeField: "KOD", fracField: "DOLYA", name: "С дроблением" },
};
const codeFormOf = (name: string): CodeForm => ({ ...EMPTY_CODE, ...CODE_FORMS[name] });

describe("the request of the form is the body the core got", () => {
  for (const name of Object.keys(FILL_FORMS)) {
    it(`fill ${name}: the form → the arguments → the recorded body`, () => {
      const got = bodyOfFill(argsOfFill(fillFormOf(name)));
      expect(got.ok).toBe(true);
      if (got.ok) expect(canon(got.body)).toBe(canon(FILL[name].request));
    });
  }
  for (const name of Object.keys(CODE_FORMS)) {
    it(`code ${name}: the form → the arguments → the recorded body (the version of the model goes along)`, () => {
      const version = (CODE[name].request as { expected_version: number }).expected_version;
      const got = bodyOfCode(argsOfCode(codeFormOf(name)), version);
      expect(got.ok).toBe(true);
      if (got.ok) {
        expect(canon(got.body)).toBe(canon(CODE[name].request));
        expect(got.modelId).toBe(IDS.plain);
      }
    });
  }
  it("by the centre: no `split`, no `m`, no FRAC unless it is asked; with splitting both fields", () => {
    const centre = bodyOfCode(argsOfCode(codeFormOf("centre")), 1);
    expect(centre.ok && Object.keys(centre.body).sort()).toEqual(["code_field", "expected_version", "mode", "wireframes"]);
    const withFrac = bodyOfCode(argsOfCode({ ...codeFormOf("centre"), fracOn: true, fracField: "FRAC" }), 1);
    expect(withFrac.ok && withFrac.body.frac_field).toBe("FRAC");
    const split = bodyOfCode(argsOfCode({ ...codeFormOf("split"), fracField: "FRAC", codeField: "CODE" }), 1);
    expect(split.ok && split.body.split).toBe(true);
    expect(split.ok && "frac_field" in split.body).toBe(false); // the default names are the core's
    expect(split.ok && "code_field" in split.body).toBe(false);
  });
  it("the wireframes of a command: JSON or numbers; a code that is not typed is the name of the wireframe", () => {
    expect(wiresOfArgs('[{"id": 5, "code": " Руда  1 ", "priority": 2}, 6]')).toEqual({ ok: true, wires: [{ id: 5, code: "Руда 1", priority: 2 }, { id: 6 }] });
    expect(wiresOfArgs("5; 6, 7")).toEqual({ ok: true, wires: [{ id: 5 }, { id: 6 }, { id: 7 }] });
    expect(wiresOfArgs([{ id: "8" }])).toMatchObject({ ok: true });
    const rows = rowsOfWires([{ id: 5, code: "Руда", priority: 2 }, { id: 6 }]);
    expect(wiresOfArgs(wiresArg(rows))).toEqual({ ok: true, wires: [{ id: 5, code: "Руда", priority: 2 }, { id: 6 }] });
    const flatArgs = argsOfFill(fillFormOf("both"));
    expect(typeof flatArgs.wireframes).toBe("string"); // the parameters of a command are flat
    expect(flatArgs.m).toBe(4);
    expect(flatArgs.prototype_id).toBe(IDS.prototype);
  });
  it("the arguments of a call show in the form and come back as the same body", () => {
    for (const name of Object.keys(FILL_FORMS)) {
      const args = argsOfFill(fillFormOf(name));
      const first = bodyOfFill(args);
      const again = bodyOfFill(argsOfFill(fillFormOfArgs(args)));
      expect(again.ok && first.ok && canon(again.body) === canon(first.body), name).toBe(true);
    }
  });
});

describe("a wrong form is told in Russian in its place before anything is sent", () => {
  const bad = (args: Record<string, unknown>) => {
    const got = bodyOfFill({ prototype_id: IDS.prototype, wireframes: String(IDS.wire_a), ...args });
    if (got.ok) throw new Error("expected a refusal");
    return got.issue;
  };
  it("the prototype, the list, the rows, the mode, m, the levels", () => {
    expect(bodyOfFill({ wireframes: "5" })).toMatchObject({ ok: false, issue: { field: "prototype" } });
    expect(bad({ wireframes: "" })).toMatchObject({ field: "wireframes", message: "Не выбрано ни одного каркаса." });
    expect(bad({ wireframes: "5; 5" })).toMatchObject({ field: "wireframes", index: 1, message: "Каркас №5 указан дважды." });
    expect(bad({ wireframes: '[{"id": 5, "priority": "два"}]' })).toMatchObject({ field: "wireframes", index: 0, message: "Приоритет — целое число." });
    expect(bad({ wireframes: "[oops" })).toMatchObject({ field: "wireframes" });
    expect(bad({ mode: "сбоку" })).toMatchObject({ field: "mode" });
    expect(bad({ m: 99 })).toMatchObject({ field: "m" });
    expect(bad({ m: "2,5" })).toMatchObject({ field: "m" });
    expect(bad({ levels: "1; 9; 1" })).toMatchObject({ field: "levels" });
    expect(bodyOfFill({ prototype_id: 7, wireframes: "5", mode: "оба", m: "16", levels: "3" })).toMatchObject({ ok: true, body: { mode: "both", m: 16, levels: [3, 3, 3] } });
  });
  it("the refusals of the core before the job are put in their place (the row of the wireframe, the field)", () => {
    const at = (key: string, group: "fill" | "code") => issueOfFill(refusalOfCase(A[group].refused[key]));
    expect(at("mode", "fill")).toMatchObject({ field: "mode" });
    expect(at("m", "fill")).toMatchObject({ field: "m" });
    expect(at("no_wireframes", "fill")).toMatchObject({ field: "wireframes" });
    expect(at("no_prototype", "fill")).toMatchObject({ field: "model" });
    expect(at("not_prototype", "fill")).toMatchObject({ field: "prototype" });
    expect(at("wireframe_missing", "fill")).toMatchObject({ field: "wireframes", index: 0 });
    expect(at("wireframe_open", "fill")).toMatchObject({ field: "wireframes", index: 1 });
    expect(at("levels", "fill")).toMatchObject({ field: "levels" });
    expect(at("field_exists", "code")).toMatchObject({ field: "code_field" });
    expect(at("frac_exists", "code")).toMatchObject({ field: "frac_field" });
    expect(at("empty", "code")).toMatchObject({ field: "model" });
    expect(at("split_type", "code")).toMatchObject({ field: "split" });
    expect(at("missing", "code")).toMatchObject({ field: "model" });
    expect(at("version", "code")).toMatchObject({ field: "general", reread: true });
    for (const [group, list] of [["fill", A.fill.refused], ["code", A.code.refused]] as const)
      for (const [k, r] of Object.entries<RefusedCase>(list)) {
        const i = issueOfFill(refusalOfCase(r));
        expect(i.message, `${group} ${k}`).toBe(r.body.message);
        expect(i.hint).toBe(r.body.hint);
        expect(/[а-я]/i.test(i.message)).toBe(true);
      }
  });
});

describe("the results of the core", () => {
  it("fill both: the codes, the volumes of the bodies and the rest, the stages", () => {
    const r = resultOf(FILL.both);
    expect(r).toMatchObject({ model_id: IDS.fill, name: "Заполнение", rows: 120, parents: 120, dropped: [] });
    expect(r.codes).toEqual({ Руда: 24, Зона: 0, вне: 96 });
    expect(r.volume.Руда).toBe(24000);
    expect(r.volume.вне).toBe(96000);
    expect(Object.values(r.volume).reduce((a, b) => a + b, 0)).toBe(120000); // the volume of the grid: the codes tile it
    expect(Object.keys(r.stages)).toEqual(expect.arrayContaining(["wireframes", "columns", "boundary", "split", "merge", "write"]));
    expect(codeLines(r).map(flat)).toEqual(["Руда — 24 000 м³ (24 блока)", "Зона — 0 м³ (0 блоков)", "вне — 96 000 м³ (96 блоков)"]);
    expect(flat(fillSummary(r, "fill"))).toContain("Заполнение: модель «Заполнение» (№9): 120 строк.");
  });
  it("the warning «Каркас не пересекает сетку в плане» and the empty model are shown as the core wrote them", () => {
    const r = resultOf(FILL.outside_far);
    expect(r.rows).toBe(0);
    expect(r.warnings.some((w) => w.includes("не пересекает сетку в плане"))).toBe(true);
    const html = flat(renderToStaticMarkup(createElement(FillResultView, { r, kind: "fill" })));
    expect(html).toContain('data-testid="build-fill-warnings"');
    expect(html).toContain("Каркас «Далеко» не пересекает сетку в плане: блоков с его кодом нет.");
    expect(html).toContain("В выбранном режиме ни один блок не попал в тела: модель без строк.");
    expect(html).toContain("0 строк");
  });
  it("the coding by the centre writes fields of the same model; with splitting — a new model", () => {
    const c = resultOf(CODE.centre);
    expect(c).toMatchObject({ model_id: IDS.plain, version: 2, fields: ["ZONE"] });
    const html = flat(renderToStaticMarkup(createElement(FillResultView, { r: c, kind: "code" })));
    expect(html).toContain("Модель «Модель (2)»: записано ZONE");
    expect(html).toContain("версия 2");
    const s = resultOf(CODE.split);
    expect(s).toMatchObject({ model_id: IDS.split, name: "С дроблением", fields: [], version: null });
    expect(flat(fillSummary(c, "code"))).toContain("Кодирование по центру");
    expect(flat(fillSummary(s, "code"))).toContain("Кодирование с дроблением");
    expect(s.codes).toEqual({ Руда: 24, вне: 96 });
  });
  it("the stages of the jobs are the ones of the core", () => {
    for (const k of ["prototype", "fill", "code", "regularize"] as const) expect([...STAGES[k]], k).toEqual(A.stage_names[k]);
    expect(stageIndex(STAGES.fill, "Дробление")).toBe(3);
    expect(stageIndex(STAGES.fill, "Готово")).toBe(-1);
  });
});

describe("the command blocks.fill", () => {
  it("without arguments it opens the window; the prototype of the tree is chosen", async () => {
    app.store.set({ selected: { kind: "blocks", id: IDS.prototype } });
    expect((await app.registry.execute("blocks.fill", {}, "user")).ok).toBe(true);
    expect(windowOfKind("fill")).not.toBeNull();
    expect(form("fill").prototypeId).toBe(IDS.prototype);
    const html = render(createElement(FillWindow, { winId: windowOfKind("fill")! }));
    expect(html).toContain('data-testid="fill-window"');
    expect(html).toContain("Сетка 10 м"); // the prototypes of the project…
    expect(html).not.toContain("Модель (2)</option>"); // …not the models with rows
  });
  it("it starts the job with the recorded body, follows it and answers with the codes", async () => {
    const r = await app.registry.execute("blocks.fill", argsOfFill(fillFormOf("both")), "user");
    expect(r.ok).toBe(true);
    expect(flat(r.result)).toContain("Руда — 24 000 м³ (24 блока)");
    expect(canon(core.sent.find((s) => s.method === "POST" && s.path === "/blocks/fill")?.body)).toBe(canon(FILL.both.request));
    expect(runOf("fill").phase).toBe("done");
    expect(runOf("fill").result?.model_id).toBe(IDS.fill);
    const html = flat(render(createElement(FillWindow, { winId: windowOfKind("fill")! })));
    expect(html).toContain('data-testid="build-fill-result"');
    expect(html).toContain("Новая модель «Заполнение» (№9)");
    expect(html).toContain("24 000 м³");
  });
  it("the warning of a wireframe off the grid reaches the log and the window", async () => {
    const r = await app.registry.execute("blocks.fill", argsOfFill(fillFormOf("outside_far")), "user");
    expect(r.ok).toBe(true);
    expect(r.result).toContain("Каркас «Далеко» не пересекает сетку в плане");
    expect(flat(render(createElement(FillWindow, { winId: windowOfKind("fill")! })))).toContain("не пересекает сетку в плане");
  });
  it("the course by the stages: the stage of the job is marked, «Отменить» stops it, nothing is written", async () => {
    core.hold("Дробление", 0.55);
    const run = app.registry.execute("blocks.fill", argsOfFill(fillFormOf("both")), "user");
    await new Promise((r) => setTimeout(r, 20));
    await refresh();
    const html = () => flat(render(createElement(FillWindow, { winId: windowOfKind("fill")! })));
    let h = html();
    expect(h).toContain('data-testid="build-fill-progress"');
    expect(h).toContain("55%");
    expect(h).toMatch(/<li aria-current="step"[^>]*>Дробление<\/li>/);
    expect(h).toMatch(/<li[^>]*data-done="true"[^>]*>Каркасы<\/li>/);
    expect(h).toMatch(/<li[^>]*data-done="false"[^>]*>Запись<\/li>/);
    expect(h).toContain('data-testid="build-fill-cancel"');
    await cancelOp("fill");
    const r = await run;
    expect(core.cancelled()).toBe(true);
    expect(r.ok).toBe(true);
    expect(r.result).toContain("отменено: ничего не записано");
    expect(runOf("fill").phase).toBe("cancelled");
    h = html();
    expect(h).toContain("Отменено: ничего не записано.");
    expect(h).not.toContain("build-fill-result");
  });
  it("the failure of the job (a shell that crosses itself) is told in Russian at the wireframes", async () => {
    const r = await app.registry.execute("blocks.fill", argsOfFill(fillFormOf("crossing")), "user");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Каркас «Две оболочки»");
    expect(runOf("fill").phase).toBe("failed");
    expect(buildStore.get().issues.fill).toMatchObject({ field: "wireframes", message: expect.stringContaining("Две оболочки") });
    const html = render(createElement(FillWindow, { winId: windowOfKind("fill")! }));
    expect(html).toContain('data-testid="build-fill-failed"');
    expect(html).toContain("Заполнять можно только замкнутыми телами");
  });
  it("every refusal of the core before the job stands under its field of the window", async () => {
    putForm("fill", fillFormOf("both"));
    for (const [key, field] of [["mode", "mode"], ["m", "m"], ["not_prototype", "prototype"], ["levels", "levels"], ["wireframe_open", "wireframes"]] as const) {
      core.force(A.fill.refused[key]);
      const r = await app.registry.execute("blocks.fill", argsOfFill(fillFormOf("both")), "user");
      expect(r.ok, key).toBe(false);
      expect(r.result).toContain(A.fill.refused[key].body.message);
      expect(buildStore.get().issues.fill?.field, key).toBe(field);
    }
    openBuildWindow(app, "fill");
    const html = render(createElement(FillWindow, { winId: windowOfKind("fill")! }));
    expect(html).toContain('data-testid="build-issue-fill-wireframes"');
    expect(html).toContain("не замкнут: открытых рёбер — 4");
  });
  it("a wrong call is refused before the core; a missing model is 404 in Russian", async () => {
    const r = await app.registry.execute("blocks.fill", { prototype_id: IDS.prototype, wireframes: "5; 5" }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Каркас №5 указан дважды.");
    expect(core.sent.filter((s) => s.method === "POST")).toHaveLength(0);
    const gone = await app.registry.execute("blocks.fill", { prototype_id: 99999, wireframes: "5" }, "user");
    expect(gone.ok).toBe(false);
    expect(gone.result).toContain("Блочная модель №99999 не найдена.");
  });
  it("the wireframes of the list: the open one is marked, the order and the priority are the form's", () => {
    putForm("fill", { ...fillFormOf("both"), wires: [...fillFormOf("both").wires, { id: IDS.surface, code: "", priority: "" }], prototypeId: IDS.prototype });
    openBuildWindow(app, "fill");
    const html = render(createElement(FillWindow, { winId: windowOfKind("fill")! }));
    expect(html).toContain('data-testid="build-fill-wire-2"'.replace("-2", `-${IDS.wire_a}`));
    expect(html).toContain("не замкнут");
    expect(html).toContain(`data-testid="build-fill-priority-${IDS.wire_a}"`);
    expect(html).toContain('value="2"');
    expect(html).toContain("Приоритет — целое число: больше — главнее");
  });
});

describe("the command blocks.code", () => {
  it("without arguments it opens the window; the model of the tree is chosen", async () => {
    app.store.set({ selected: { kind: "blocks", id: IDS.plain } });
    expect((await app.registry.execute("blocks.code", {}, "user")).ok).toBe(true);
    expect(form("code").modelId).toBe(IDS.plain);
    const html = render(createElement(CodeWindow, { winId: windowOfKind("code")! }));
    expect(html).toContain('data-testid="code-window"');
    expect(html).toContain('data-testid="code-centre"');
    expect(html).toContain("По центру блока (поля той же модели)");
    expect(html).toContain("С дроблением (новая модель)");
  });
  it("by the centre: the fields of the same model; the version of the list goes as `expected_version`", async () => {
    atVersion(IDS.plain, 1);
    const r = await app.registry.execute("blocks.code", argsOfCode(codeFormOf("centre")), "user");
    expect(r.ok).toBe(true);
    expect(flat(r.result)).toContain("Кодирование по центру: модель «Модель (2)»");
    expect(canon(core.sent.find((s) => s.path === `/blocks/${IDS.plain}/code`)?.body)).toBe(canon(CODE.centre.request));
    expect(runOf("code").result?.fields).toEqual(["ZONE"]);
  });
  it("with splitting: a new model, the codes and the volumes", async () => {
    atVersion(IDS.plain, 2);
    const r = await app.registry.execute("blocks.code", argsOfCode(codeFormOf("split")), "user");
    expect(r.ok).toBe(true);
    expect(flat(r.result)).toContain("Кодирование с дроблением: модель «С дроблением»");
    expect(runOf("code").result?.model_id).toBe(IDS.split);
    expect(flat(render(createElement(CodeWindow, { winId: windowOfKind("code")! })))).toContain("Новая модель «С дроблением»");
  });
  it("a model changed meanwhile (409) is told with «Перечитать»; a name in use and a second FRAC are under their fields", async () => {
    atVersion(IDS.plain, 1);
    core.force(A.code.refused.version);
    const r = await app.registry.execute("blocks.code", argsOfCode(codeFormOf("centre")), "user");
    expect(r.ok).toBe(false);
    expect(buildStore.get().issues.code).toMatchObject({ field: "general", reread: true });
    openBuildWindow(app, "code");
    expect(render(createElement(CodeWindow, { winId: windowOfKind("code")! }))).toContain('data-testid="build-reread-code"');
    putForm("code", { ...codeFormOf("centre"), codeField: "AU" });
    core.force(A.code.refused.field_exists);
    await app.registry.execute("blocks.code", argsOfCode(form("code")), "user");
    expect(buildStore.get().issues.code).toMatchObject({ field: "code_field" });
    expect(flat(render(createElement(CodeWindow, { winId: windowOfKind("code")! })))).toContain("Поле «AU» в модели уже есть.");
    putForm("code", { ...codeFormOf("split"), modelId: IDS.fill });
    core.force(A.code.refused.frac_exists);
    await app.registry.execute("blocks.code", argsOfCode(form("code")), "user");
    expect(buildStore.get().issues.code).toMatchObject({ field: "frac_field" });
  });
  it("a model with no blocks (a prototype) is refused by the core in Russian at the model", async () => {
    putForm("code", { ...codeFormOf("centre"), modelId: IDS.prototype });
    core.force(A.code.refused.empty);
    const r = await app.registry.execute("blocks.code", argsOfCode(form("code")), "user");
    expect(r.ok).toBe(false);
    expect(buildStore.get().issues.code?.field).toBe("model");
  });
});
