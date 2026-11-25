// The window «Регуляризация», the command `blocks.regularize` and «Перестроить» (`blocks.rebuild`) (ТЗ 8.7.5; ADR 0023 §6, §9,
// решение 14, № 72; Э17 17.37) on the REAL answers of the core (`core/tests/data/blocks/build_window_answers.json`, made by
// `core/tests/test_blocks_build_window_answers.py` through `POST /blocks/{id}/regularize`, `POST /blocks/{id}/rebuild`, the jobs
// `blocks.regularize` / `blocks.fill` and `GET /blocks/{id}/fields`): the request of the form = the body the core got; the totals
// before / after as the core wrote them (a gap is a gap: no tonnes without a density; a difference is told); the refusals in
// Russian in their place of the form; «Перестроить» asks before it starts what goes away (`dropped`) and tells what went.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext } from "../src/shell/AppContext";
import type { App } from "../src/shell/app";
import { closeDialog, dialogStore, type DialogRequest } from "../src/shell/dialogs";
import { refresh } from "../src/modules/jobs/state";
import { readRegularize, STAGES, type Refusal, type RegularizeResult } from "../src/modules/blocks/build/api";
import { chooseRegModel, openBuildWindow, windowOfKind } from "../src/modules/blocks/build/bind";
import { anyDiffers, dec, rebuildQuestion, regularizeSummary, totalRows } from "../src/modules/blocks/build/format";
import { argsOfRegularize, bodyOfRegularize, EMPTY_REG, gradesArg, issueOfRegularize, parseGrades, regFormOfArgs, type RegForm } from "../src/modules/blocks/build/regForm";
import { RegularizeWindow } from "../src/modules/blocks/build/RegularizeWindow";
import { RegularizeResultView } from "../src/modules/blocks/build/Results";
import { cancelOp, droppedOf, REBUILDABLE } from "../src/modules/blocks/build/run";
import { buildStore, form, isPrototype, runOf, withBlocks } from "../src/modules/blocks/build/state";
import { commands } from "../src/modules/blocks/build/commands";
import { A, atVersion, canon, flat, IDS, installCore, newApp, teardown, type Core, type JobCase, type RefusedCase } from "./blocks-build-harness";

let app: App;
let core: Core;

beforeEach(() => {
  core = installCore();
  app = newApp();
});
afterEach(teardown);

const REG = A.regularize.jobs as Record<string, JobCase>;
const resultOf = (name: string): RegularizeResult => readRegularize(REG[name].job.result)!;
const render = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));
const refusalOfCase = (r: RefusedCase): Refusal => ({ code: String(r.body.code), message: String(r.body.message), hint: String(r.body.hint), param: typeof r.body.param === "string" ? r.body.param : null });
const tick = () => new Promise((r) => setTimeout(r, 5));

const FORMS: Record<string, Partial<RegForm>> = {
  grades: { modelId: IDS.plain, size: ["20", "20", "20"], gradesAll: false, grades: [{ field: "AU", weight: "volume" }], name: "Блоки 20" },
  all: { modelId: IDS.plain, size: ["20", "20", "10"] },
  tonnes: { modelId: IDS.plain, size: ["30", "25", "20"], gradesAll: false, grades: [{ field: "AU", weight: "tonnes" }], densityMode: "value", densityValue: "2,7" },
  categories: { modelId: IDS.fill, size: ["20", "20", "20"], catMode: "some", categories: ["CODE"], name: "Коды 20" },
};
const formOf = (name: string): RegForm => ({ ...EMPTY_REG, ...FORMS[name] });

describe("the request of the form is the body the core got", () => {
  for (const name of Object.keys(FORMS)) {
    it(`${name}: the form → the arguments → the recorded body`, () => {
      const got = bodyOfRegularize(argsOfRegularize(formOf(name)));
      expect(got.ok).toBe(true);
      if (!got.ok) return;
      expect(got.modelId).toBe(formOf(name).modelId);
      expect(canon(got.body)).toBe(canon(REG[name].request));
    });
    it(`${name}: the arguments of a call show in the form and come back as the same body`, () => {
      const args = argsOfRegularize(formOf(name));
      const back = bodyOfRegularize(argsOfRegularize(regFormOfArgs(args)));
      const first = bodyOfRegularize(args);
      expect(back.ok && first.ok && canon(back.body) === canon(first.body)).toBe(true);
    });
  }
  it("the arguments of a command are flat text: «AU:volume; CU:tonnes», «CODE; DOM», «нет», a decimal comma", () => {
    expect(parseGrades("AU:volume; CU:tonnes")).toEqual([{ field: "AU", weight: "volume" }, { field: "CU", weight: "tonnes" }]);
    expect(parseGrades("AU, CU")).toEqual([{ field: "AU", weight: "volume" }, { field: "CU", weight: "volume" }]);
    expect(parseGrades('[{"field": "AU", "weight": "tonnes"}]')).toEqual([{ field: "AU", weight: "tonnes" }]);
    expect(parseGrades("AU:weird")).toBeNull();
    expect(gradesArg([{ field: "AU", weight: "tonnes" }])).toBe("AU:tonnes");
    const got = bodyOfRegularize({ model_id: 3, size: "20", density: "2,65", categories: "нет", grades: "AU:tonnes" });
    expect(got).toEqual({ ok: true, modelId: 3, body: { size: [20, 20, 20], density: 2.65, categories: [], grades: [{ field: "AU", weight: "tonnes" }] } });
    const some = bodyOfRegularize({ model_id: 3, size: "20; 20; 10", density: "SG", categories: "CODE; DOM", name: " Блоки   20 " });
    expect(some).toMatchObject({ ok: true, body: { density: "SG", categories: ["CODE", "DOM"], name: "Блоки 20" } });
  });
  it("without a field of grades every number field goes by the volume (nothing is sent)", () => {
    const got = bodyOfRegularize(argsOfRegularize(formOf("all")));
    expect(got.ok && "grades" in got.body).toBe(false);
    expect(got.ok && "categories" in got.body).toBe(false);
  });
});

describe("a wrong form is told in Russian in its place before anything is sent", () => {
  const bad = (args: Record<string, unknown>) => {
    const got = bodyOfRegularize({ model_id: IDS.plain, size: "20", ...args });
    if (got.ok) throw new Error("expected a refusal");
    return got.issue;
  };
  it("the model, the size, the grades, the weight by the tonnes without a density, the density", () => {
    expect(bodyOfRegularize({ size: "20" })).toMatchObject({ ok: false, issue: { field: "model" } });
    expect(bad({ size: "x" })).toMatchObject({ field: "size" });
    expect(bad({ size: "20; 0; 20" })).toMatchObject({ field: "size", message: "Размер блока должен быть больше нуля." });
    expect(bad({ grades: "AU:вес" })).toMatchObject({ field: "grades" });
    expect(bad({ grades: "AU:tonnes" })).toMatchObject({ field: "density", message: "Для веса по тоннажу нужна плотность." });
    expect(bad({ density: "-2" })).toMatchObject({ field: "density" });
    expect(bad({ name: "я".repeat(401) })).toMatchObject({ field: "name" });
  });
  it("the refusals of the core are put in their place (with the words of the core)", () => {
    const at = (key: string) => issueOfRegularize(refusalOfCase(A.regularize.refused[key]));
    expect(at("size_zero")).toMatchObject({ field: "size" });
    expect(at("no_size")).toMatchObject({ field: "size" });
    expect(at("grade_missing")).toMatchObject({ field: "grades" });
    expect(at("tonnes_no_density")).toMatchObject({ field: "density" });
    expect(at("category_number")).toMatchObject({ field: "categories", index: 0 });
    expect(at("version")).toMatchObject({ field: "general", reread: true });
    expect(at("empty")).toMatchObject({ field: "model" });
    expect(at("missing")).toMatchObject({ field: "model" });
    for (const [k, r] of Object.entries<RefusedCase>(A.regularize.refused)) {
      const i = issueOfRegularize(refusalOfCase(r));
      expect(i.message, k).toBe(r.body.message);
      expect(i.hint).toBe(r.body.hint);
      expect(/[а-я]/i.test(i.message)).toBe(true);
    }
  });
});

describe("the totals before and after", () => {
  it("the volume is kept exactly; the tonnes are a gap without a density, never 0", () => {
    const r = resultOf("grades");
    expect(r).toMatchObject({ model_id: 14, name: "Блоки 20", rows: 18 });
    expect(r.totals.before.volume).toBe(r.totals.after.volume);
    expect(r.totals.before.tonnes).toBeNull();
    expect(r.totals.after.tonnes).toBeNull();
    const rows = totalRows(r);
    expect(rows.map((x) => x.label)).toEqual(["Объём", "Металл AU (по объёму)"]); // no «Тоннаж» row without a density
    expect(rows[0]).toMatchObject({ differs: false });
  });
  it("with a density the tonnes are in the table and are kept", () => {
    const r = resultOf("tonnes");
    expect(r.totals.before.tonnes).not.toBeNull();
    const rows = totalRows(r);
    expect(rows.map((x) => x.label)).toContain("Тоннаж");
    expect(rows.find((x) => x.label === "Тоннаж")?.differs).toBe(false);
    expect(rows.find((x) => x.label.startsWith("Металл AU"))?.label).toContain("по тоннажу");
  });
  it("a gap in the data is not a zero: the totals of a grade may differ, and the window says so", () => {
    const r = resultOf("grades");
    expect(anyDiffers(r)).toBe(true);
    const metal = totalRows(r).find((x) => x.label.startsWith("Металл"))!;
    expect(metal.differs).toBe(true);
    expect(metal.before).not.toBe(metal.after);
    const html = flat(renderToStaticMarkup(createElement(RegularizeResultView, { r })));
    expect(html).toContain('data-testid="build-regularize-differs"');
    expect(html).toContain("пропуск не считается нулём");
    expect(html).toMatch(/data-differs="true"/);
    expect(flat(regularizeSummary(r))).toContain("Итоги до и после расходятся");
  });
  it("categories: the volume is kept and nothing is said about a difference", () => {
    const r = resultOf("categories");
    expect(r.totals.before.metal).toEqual({});
    expect(anyDiffers(r)).toBe(false);
    const html = flat(renderToStaticMarkup(createElement(RegularizeResultView, { r })));
    expect(html).not.toContain("build-regularize-differs");
    expect(html).toContain("Новая модель «Коды 20» (№17)");
    expect(dec(120000, 1)).toBe("120 000");
  });
});

describe("the window", () => {
  it("the models with blocks (not the prototypes) are offered; a model chosen suggests twice its block", () => {
    expect(withBlocks().every((m) => !isPrototype(m) && m.cells > 0)).toBe(true);
    expect(withBlocks().some((m) => m.id === IDS.prototype)).toBe(false);
    chooseRegModel(IDS.plain);
    expect(form("regularize")).toMatchObject({ modelId: IDS.plain, size: ["20", "20", "20"], gradesAll: true });
    openBuildWindow(app, "regularize");
    const html = flat(render(createElement(RegularizeWindow, { winId: windowOfKind("regularize")! })));
    expect(html).toContain('data-testid="regularize-window"');
    expect(html).toContain("Сейчас блок 10 × 10 × 10 м.");
    expect(html).toContain("Все числовые поля, по объёму");
    expect(html).toContain("P_&lt;КОД&gt;");
    expect(html).not.toMatch(/window\.(confirm|prompt|alert)/);
  });
  it("the grades by hand: each with its weight; the density; the categories", () => {
    app.store.set({ selected: { kind: "blocks", id: IDS.plain } });
    openBuildWindow(app, "regularize");
    buildStore.set((s) => ({ forms: { ...s.forms, regularize: { ...formOf("tonnes"), densityMode: "field", densityField: "AU" } } }));
    const html = flat(render(createElement(RegularizeWindow, { winId: windowOfKind("regularize")! })));
    expect(html).toContain('data-testid="regularize-grade-AU"');
    expect(html).toContain('data-testid="regularize-weight-AU"');
    expect(html).toContain("по тоннажу");
    expect(html).toContain('data-testid="regularize-density-field"');
  });
});

describe("the command blocks.regularize", () => {
  it("without arguments it opens the window", async () => {
    expect((await app.registry.execute("blocks.regularize", {}, "user")).ok).toBe(true);
    expect(windowOfKind("regularize")).not.toBeNull();
  });
  it("it starts the job with the recorded body, follows it and answers with the totals", async () => {
    const r = await app.registry.execute("blocks.regularize", argsOfRegularize(formOf("grades")), "user");
    expect(r.ok).toBe(true);
    expect(flat(r.result)).toContain("Регуляризация: модель «Блоки 20» (№14), 18 блоков.");
    expect(flat(r.result)).toContain("Итоги до и после расходятся");
    const post = core.sent.find((s) => s.method === "POST" && s.path === `/blocks/${IDS.plain}/regularize`)!;
    expect(canon(post.body)).toBe(canon(REG.grades.request));
    expect(runOf("regularize").phase).toBe("done");
    expect(runOf("regularize").result).toEqual(resultOf("grades"));
    const html = flat(render(createElement(RegularizeWindow, { winId: windowOfKind("regularize")! })));
    expect(html).toContain('data-testid="build-regularize-totals"');
    expect(html).toContain("Металл AU (по объёму)");
  });
  it("the categories of a fill model go as the recorded request", async () => {
    const r = await app.registry.execute("blocks.regularize", argsOfRegularize(formOf("categories")), "user");
    expect(r.ok).toBe(true);
    expect(canon(core.sent.find((s) => s.path === `/blocks/${IDS.fill}/regularize`)?.body)).toBe(canon(REG.categories.request));
  });
  it("the course by the stages and the cancel: nothing is written", async () => {
    core.hold("Перекрытия", 0.5);
    const run = app.registry.execute("blocks.regularize", argsOfRegularize(formOf("all")), "user");
    await tick();
    await tick();
    await refresh();
    const h = flat(render(createElement(RegularizeWindow, { winId: windowOfKind("regularize")! })));
    expect(h).toContain('data-testid="build-regularize-progress"');
    expect(h).toMatch(/<li aria-current="step"[^>]*>Перекрытия<\/li>/);
    expect(h).toMatch(/<li[^>]*data-done="true"[^>]*>Чтение<\/li>/);
    await cancelOp("regularize");
    const r = await run;
    expect(r.result).toContain("отменена: ничего не записано");
    expect(runOf("regularize").phase).toBe("cancelled");
    expect(STAGES.regularize).toEqual(A.stage_names.regularize);
  });
  it("every refusal before the job stands under its field; a wrong call never reaches the core", async () => {
    for (const [key, field] of [["tonnes_no_density", "density"], ["category_number", "categories"], ["grade_missing", "grades"], ["empty", "model"], ["version", "general"]] as const) {
      core.force(A.regularize.refused[key]);
      const r = await app.registry.execute("blocks.regularize", argsOfRegularize(formOf("grades")), "user");
      expect(r.ok, key).toBe(false);
      expect(r.result).toContain(A.regularize.refused[key].body.message);
      expect(buildStore.get().issues.regularize?.field, key).toBe(field);
    }
    openBuildWindow(app, "regularize");
    expect(render(createElement(RegularizeWindow, { winId: windowOfKind("regularize")! }))).toContain('data-testid="build-reread-regularize"');
    const posts = core.sent.filter((s) => s.method === "POST").length;
    const bad = await app.registry.execute("blocks.regularize", { model_id: IDS.plain, size: "20; 0; 20" }, "user");
    expect(bad.ok).toBe(false);
    expect(bad.result).toContain("Размер блока должен быть больше нуля.");
    expect(core.sent.filter((s) => s.method === "POST")).toHaveLength(posts);
    const gone = await app.registry.execute("blocks.regularize", { model_id: 99999, size: "20" }, "user");
    expect(gone.result).toContain("Блочная модель №99999 не найдена.");
  });
});

// ---- «Перестроить» ----

const question = (): Extract<DialogRequest, { kind: "confirm" }> => {
  const d = dialogStore.get().queue.find((x) => x.kind === "confirm");
  if (!d || d.kind !== "confirm") throw new Error("no question");
  return d;
};
/** A call of the assistant: the registry asks the user first (`confirm`); the test is the user who agrees. */
async function asClaude(id: string, args: Record<string, unknown>) {
  const run = app.registry.execute(id, args, "claude");
  await vi.waitFor(() => expect(dialogStore.get().queue.length).toBe(1));
  const d = dialogStore.get().queue[0];
  expect(d.kind).toBe("confirm");
  answer(d, true);
  return run;
}
const answer = (d: DialogRequest, yes: boolean) => {
  if (d.kind === "confirm") d.resolve(yes);
  else if (d.kind === "message") d.resolve();
  closeDialog(d.id);
};

describe("«Перестроить» (blocks.rebuild)", () => {
  beforeEach(() => {
    core.fields = A.rebuild.fields_before.body;
    dialogStore.set({ queue: [] });
  });

  it("what goes away is counted from the makers of the fields: the fields of other operations", () => {
    const fields = (A.rebuild.fields_before.body.fields as { name: string; source: { op: string } | null }[]).map((f) => ({ name: f.name, op: f.source?.op ?? null }));
    expect(fields.map((f) => f.op)).toEqual(["fill", "fill", "calc"]);
    expect(droppedOf("fill", fields)).toEqual(A.rebuild.job.job.result.dropped); // the same as the job will say: ["X"]
    expect(droppedOf("fill", [{ name: "AU", op: null }])).toEqual([]); // an imported field has no maker: it stays
    expect(REBUILDABLE).toEqual(["fill", "code", "regularize"]);
  });
  it("the question tells the fields and the reason; the job starts only after «Перестроить»", async () => {
    core.fields = { ...A.rebuild.fields_before.body, stale: true, stale_reason: "Изменился каркас «Руда»." };
    const run = app.registry.execute("blocks.rebuild", { model_id: IDS.fill }, "user");
    await vi.waitFor(() => expect(dialogStore.get().queue.length).toBe(1));
    const q = question();
    expect(q.options.title).toBe("Перестроить модель");
    expect(q.options.message).toContain("Заполнение");
    expect(q.options.message).toContain("Поля других операций считались по старым строкам и будут удалены: X.");
    expect(q.options.message).toContain("Причина: Изменился каркас «Руда».");
    expect(q.options.okLabel).toBe("Перестроить");
    expect(q.options.danger).toBe(true);
    expect(core.sent.filter((s) => s.method === "POST")).toHaveLength(0);
    answer(q, true);
    await vi.waitFor(() => expect(dialogStore.get().queue.some((d) => d.kind === "message")).toBe(true)); // the end: what went
    const end = dialogStore.get().queue.find((d) => d.kind === "message")!;
    expect(end.kind === "message" && end.options.message).toContain("Удалены поля других операций: X.");
    expect(end.kind === "message" && end.options.message).toContain("версия 3");
    answer(end, true);
    const r = await run;
    expect(r.ok).toBe(true);
    expect(r.result).toBe("Модель «Заполнение» перестроена (версия 3). Удалены поля других операций: X.");
    const post = core.sent.find((s) => s.path === `/blocks/${IDS.fill}/rebuild`)!;
    expect(canon(post.body)).toBe(canon(A.rebuild.job.request));
    expect(buildStore.get().rebuilding).toBeNull();
  });
  it("«Отмена» in the question: nothing is started", async () => {
    const run = app.registry.execute("blocks.rebuild", { model_id: IDS.fill }, "user");
    await vi.waitFor(() => expect(dialogStore.get().queue.length).toBe(1));
    answer(question(), false);
    const r = await run;
    expect(r.result).toBe("Отменено");
    expect(core.sent.filter((s) => s.method === "POST")).toHaveLength(0);
  });
  it("the question of a model with nothing to drop says so; the call of the assistant is confirmed by the registry only", async () => {
    core.fields = { ...A.rebuild.fields_before.body, fields: (A.rebuild.fields_before.body.fields as unknown[]).slice(0, 2) };
    const q = rebuildQuestion("Заполнение", droppedOf("fill", [{ name: "CODE", op: "fill" }]), null);
    expect(q).toContain("Поля других операций у модели не найдены: ничего не удалится.");
    const r = await asClaude("blocks.rebuild", { model_id: IDS.fill });
    expect(dialogStore.get().queue).toHaveLength(0); // the question of the registry was the only one: ours is for the person at the window
    expect(r.ok).toBe(true);
    expect(r.result).toBe("Модель «Заполнение» перестроена (версия 3). Удалены поля других операций: X.");
  });
  it("the model chosen in the tree is the default; a model that cannot be rebuilt is refused in Russian", async () => {
    app.store.set({ selected: { kind: "blocks", id: IDS.plain } });
    const r = await app.registry.execute("blocks.rebuild", {}, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("нельзя перестроить");
    expect(core.sent.filter((s) => s.method === "POST")).toHaveLength(0);
    const none = await app.registry.execute("blocks.rebuild", { model_id: 99999 }, "user");
    expect(none.result).toContain("Блочная модель №99999 не найдена.");
    app.store.set({ selected: null });
    expect((await app.registry.execute("blocks.rebuild", {}, "user")).result).toContain("Не выбрана блочная модель.");
    const proto = await app.registry.execute("blocks.rebuild", { model_id: IDS.prototype }, "user");
    expect(proto.ok).toBe(false);
  });
  it("the refusals of the core (409 version_conflict, source_lost) are told in Russian with the hint", async () => {
    atVersion(IDS.fill, 2);
    for (const key of ["version", "source_lost"] as const) {
      core.force(A.rebuild.refused[key]);
      const r = await asClaude("blocks.rebuild", { model_id: IDS.fill });
      expect(r.ok, key).toBe(false);
      expect(r.result).toContain(A.rebuild.refused[key].body.message);
      expect(r.result).toContain(A.rebuild.refused[key].body.hint);
      expect(buildStore.get().rebuilding).toBeNull();
    }
  });
  it("the command is in the registry with the flat parameters and asks the assistant to confirm", () => {
    const c = commands.find((x) => x.id === "blocks.rebuild")!;
    expect(Object.keys(c.params ?? {})).toEqual(["model_id", "expected_version"]);
    expect(c.confirm).toBe(true);
    expect(commands.map((x) => x.id)).toEqual(["blocks.prototype", "blocks.fill", "blocks.code", "blocks.regularize", "blocks.rebuild"]);
    expect(commands.every((x) => x.confirm === true && !!x.description)).toBe(true);
    app.store.set({ projectReadOnly: true });
    for (const x of commands) expect(app.registry.reason(x.id), x.id).toBe("Проект открыт только для чтения");
  });
});
