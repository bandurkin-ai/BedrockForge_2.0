// What the person sees (Э18, 18.Г3; Д-35): the badge of a state with the chain of causes in its tooltip, «Происхождение»,
// «Зависимые», the window «Влияние изменения» (as a question and as a view with the plan of «Перестроить с зависимыми») — drawn
// from the answers of the real core; and the steps of the window (`impactFlow.ts`): the plan, the start, the loss, the end.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { readImpact, readOrigin, readStatus, type LNode } from "../../src/modules/lineage/api";
import { DependentsView } from "../../src/modules/lineage/DependentsPanel";
import { ImpactView, type ImpactViewProps } from "../../src/modules/lineage/ImpactWindow";
import { planPhase, runPhase, type Phase } from "../../src/modules/lineage/impactFlow";
import { LineageBadge, StateMark } from "../../src/modules/lineage/LineageBadge";
import { LineageProps } from "../../src/modules/lineage/LineageProps";
import { OriginView, shortFp } from "../../src/modules/lineage/OriginPanel";
import { planLines, skippedLines } from "../../src/modules/lineage/rebuild";
import { lineageStore } from "../../src/modules/lineage/state";
import { dialogStore } from "../../src/shell/dialogs";
import { answerDialog, installCore, newApp, R, S, teardown, waitFor, type Core } from "./harness";

let app: App;
let core: Core;
beforeEach(() => {
  core = installCore();
  app = newApp();
});
afterEach(teardown);

const render = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));
const flat = (s: string): string => s.replace(/[  ]/g, " ").replace(/&quot;/g, '"').replace(/&#x27;/g, "'");
const stub = (id: number): LNode => ({ kind: "object:stub", id });
/** the states of the tree as the core told them (`status_stale` of the chain of stubs) */
const preload = (answer: string) => {
  const statuses = Object.fromEntries((S[answer].body.items as unknown[]).map((i) => readStatus(i)).map((s) => [`${s.kind}:${s.id}:`, s]));
  lineageStore.set({ statuses, version: 1 });
};

describe("the badge", () => {
  it("«устарел» is a word in the warm colour; the tooltip is the chain of causes, one on a line", () => {
    const html = flat(render(createElement(StateMark, { status: readStatus(S.status_stale.body.items[4]) })));
    expect(html).toContain("устарел");
    expect(html).toContain('data-state="stale"');
    expect(html).toContain('title="устарел\nstub «Узел 2»\n← stub «Узел 1»\n← изменено: stub «Источник»"');
    expect(html).toContain('role="img"');
  });
  it("«актуален» is a quiet dot with no word (the tooltip has it); «закреплён» says the inputs changed; «источник удалён» names the source", () => {
    const current = flat(render(createElement(StateMark, { status: readStatus(S.status_current.body.items[1]) })));
    expect(current).toContain('aria-label="актуален"');
    expect(current).not.toContain("<span>актуален</span>");
    const pinned = flat(render(createElement(StateMark, { status: readStatus(S.status_pinned.body.items[1]) })));
    expect(pinned).toContain("закреплён · входы изменились");
    const lost = flat(render(createElement(StateMark, { status: readStatus(S.status_source_lost.body.items[1]) })));
    expect(lost).toContain("источник удалён");
    expect(lost).toContain("источник удалён: stub «Источник»");
    expect(render(createElement(StateMark, { status: readStatus(S.status_current.body.items[1]), hideCurrent: true }))).toBe("");
  });
  it("a row of the tree: the badge of a node the cache knows; nothing while the answer has not come, nothing for a deleted node", () => {
    expect(render(createElement(LineageBadge, { node: stub(3) }))).toBe("");
    preload("status_stale");
    const html = flat(render(createElement(LineageBadge, { node: stub(3) })));
    expect(html).toContain('data-testid="lineage-badge-object:stub-3"');
    expect(html).toContain("устарел");
    preload("status_source_lost");
    expect(render(createElement(LineageBadge, { node: stub(1) }))).toBe(""); // `deleted`
  });
});

describe("«Происхождение»", () => {
  it("the real chain: the model of variography is built from the table of composites and the set of holes", () => {
    const origin = readOrigin(R.origin_model_stale.body);
    const html = flat(render(createElement(OriginView, { status: origin.node, origin })));
    expect(html).toContain("Происхождение");
    expect(html).toContain("изменены траектории");
    expect(html).toContain("Композиты 2 м");
    expect(html).toContain("Скважины");
    expect(html).toContain("таблица");
    expect(html).toContain("набор скважин");
    expect(html).toContain("версия &lt;token&gt;:1"); // the version the input has now
    expect(html).toContain("устарел"); // the table of composites is «устарел» itself: the chain goes on
  });
  it("an input whose version changed says «было … → стало …»; an input that is gone says so", () => {
    const base = readOrigin(S.origin_n2_stale.body);
    const changed = { ...base, inputs: [{ ...base.inputs[0], fp: "11", fpNow: "12" }, { ...base.inputs[0], id: 9, name: "Исчез", fp: "3", fpNow: null, state: "deleted" as const }] };
    const html = flat(render(createElement(OriginView, { status: base.node, origin: changed })));
    expect(html).toContain("было 11 → стало 12");
    expect(html).toContain("входа нет");
    expect(shortFp("66114c790e7dc:1")).toBe("66114c790e7dc:…");
    expect(shortFp(null)).toBe("—");
  });
  it("a source has no inputs and says so", () => {
    const origin = readOrigin(S.origin_s0.body);
    expect(flat(render(createElement(OriginView, { status: origin.node, origin })))).toContain("не построен из других объектов проекта");
  });
});

describe("«Зависимые»", () => {
  const view = (impact: ReturnType<typeof readImpact> | null, readOnly = false) => flat(render(createElement(DependentsView, { impact, readOnly, onImpact: () => undefined, onRebuild: () => undefined })));
  it("the number, the counts by kind, the nearest ones with their kind", () => {
    const html = view(readImpact(R.impact_samples.body));
    expect(html).toContain("Построено из объекта: 3");
    expect(html).toContain("результат обработки");
    expect(html).toContain("модель вариограммы");
    expect(html).toContain("Модель AU");
    expect(html).not.toContain("…и ещё");
  });
  it("a long list is cut with «…и ещё»; nothing built — the words; a read-only project cannot rebuild", () => {
    const many = readImpact({ total: 20, by_kind: { dh_table: 20 }, nodes: Array.from({ length: 20 }, (_, i) => ({ kind: "dh_table", id: i + 1, sub: "", name: `T${i}`, depth: 1 })), truncated: false });
    expect(view(many)).toContain("…и ещё 12");
    expect(view(readImpact(S.impact_leaf.body))).toContain("Из этого объекта ничего не построено");
    expect(view(readImpact(S.impact_leaf.body))).toMatch(/disabled=""[^>]*data-testid="lineage-open-impact"/);
    expect(view(readImpact(S.impact_n1.body), true)).toMatch(/disabled=""[^>]*data-testid="lineage-rebuild-panel"/);
    expect(view(readImpact(S.impact_n1.body), false)).not.toMatch(/disabled=""[^>]*data-testid="lineage-rebuild-panel"/);
  });
  it("«Свойства»: the pin, «Происхождение», «Зависимые» in one block (the data come after the first draw)", () => {
    preload("status_pinned");
    const html = flat(render(createElement(LineageProps, { node: stub(2) })));
    expect(html).toContain("Закрепить (не помечать «устарел»)");
    expect(html).toMatch(/type="checkbox"[^>]*checked/);
    expect(html).toContain("Происхождение");
    expect(html).toContain("Зависимые");
  });
});

describe("the window «Влияние изменения»", () => {
  const impact = readImpact(S.impact_n1.body);
  const props = (p: Partial<ImpactViewProps> = {}): ImpactViewProps => ({
    node: stub(2),
    status: readStatus(S.status_stale.body.items[1]),
    impact,
    error: null,
    ask: null,
    phase: { t: "idle" },
    readOnly: false,
    onPlan: () => undefined,
    onRun: () => undefined,
    onGoOn: () => undefined,
    onCancel: () => undefined,
    onClose: () => undefined,
    ...p,
  });
  const draw = (p: Partial<ImpactViewProps> = {}) => flat(render(createElement(ImpactView, props(p))));
  it("as a view: what is built from the object, the counts, the dependents with their depth; «Перестроить с зависимыми» and «Закрыть»", () => {
    const html = draw();
    expect(html).toContain("stub «Узел 1»");
    expect(html).toContain("Из этого объекта построено объектов: 5");
    expect(html).toContain("изменение не блокируется");
    expect(html).toContain("прямой");
    expect(html).toContain("через 2");
    expect(html).toContain("Перестроить с зависимыми");
    expect(html).toContain("Закрыть");
    expect(html).not.toContain("Продолжить");
  });
  it("as a question before a change: «Продолжить» and «Отмена», what is going to be done is in the lead", () => {
    const html = draw({ ask: "заменить таблицу «Пробы»" });
    expect(html).toContain("Вы собираетесь заменить таблицу «Пробы». Из этого объекта построено объектов: 5.");
    expect(html).toContain("Продолжить");
    expect(html).toContain("Отмена");
    expect(html).not.toContain("Перестроить с зависимыми");
  });
  it("no dependents (the state changed after the window opened): the words, no list", () => {
    const html = draw({ impact: readImpact(S.impact_leaf.body) });
    expect(html).toContain("Из этого объекта ничего не построено: изменение никого не затронет.");
    expect(html).not.toContain("lineage-dependents");
  });
  it("a read-only project cannot rebuild", () => {
    const html = draw({ readOnly: true });
    expect(html).toContain("Проект открыт только для чтения.");
    expect(html).toMatch(/disabled=""[^>]*data-testid="lineage-rebuild"/);
  });
  it("the progress and the end are shown as they come", () => {
    expect(draw({ phase: { t: "running", text: "Задание №5 запущено: ход — во вкладке «Задания»." } })).toContain("Задание №5 запущено");
    expect(draw({ phase: { t: "end", ok: true, text: "Перестроено объектов: 5." } })).toContain("Перестроено объектов: 5.");
    expect(draw({ phase: { t: "error", text: "Объект «Узел 1» закреплён. Снимите закрепление." } })).toContain("lineage-error");
  });
});

describe("the steps of the window", () => {
  it("the plan of the real chain: the table is made with its result, the model after it", async () => {
    core.world.plan = "real:dry_run";
    const phase = await planPhase({ kind: "dh_result", id: 1 });
    expect(phase.t).toBe("plan");
    if (phase.t !== "plan") return;
    expect(planLines(phase.plan)).toEqual([
      "1. результат обработки «Композиты 2 м» — устарел (изменена таблица «Пробы»)",
      "2. таблица «Композиты 2 м» — устарел (результат обработки «Композиты 2 м» ← изменена таблица «Пробы»); создаётся вместе с «Композиты 2 м»",
      "3. модель вариограммы «Модель AU» — устарел (изменены траектории)",
    ]);
    expect(skippedLines(phase.plan)).toEqual([]);
    const html = flat(render(createElement(ImpactView, { node: { kind: "dh_result", id: 1 }, status: undefined, impact: readImpact(R.impact_samples.body), error: null, ask: null, phase, readOnly: false, onPlan: () => undefined, onRun: () => undefined, onGoOn: () => undefined, onCancel: () => undefined, onClose: () => undefined })));
    expect(html).toContain("Порядок построения");
    expect(html).toContain("Заданий: 2");
    expect(html).toContain("создаётся вместе с «Композиты 2 м»");
    expect(html).toContain("Запустить перестроение");
  });
  it("the plan of the stubs names what is left out", async () => {
    const phase = await planPhase(stub(2));
    if (phase.t !== "plan") throw new Error("no plan");
    expect(skippedLines(phase.plan)).toEqual(["report_stub «Отчёт» — report_stub «Отчёт» нельзя перестроить автоматически"]);
    const html = flat(render(createElement(ImpactView, { node: stub(2), status: undefined, impact: readImpact(S.impact_n1.body), error: null, ask: null, phase, readOnly: false, onPlan: () => undefined, onRun: () => undefined, onGoOn: () => undefined, onCancel: () => undefined, onClose: () => undefined })));
    expect(html).toContain("Не будут перестроены (останутся как есть)");
    expect(html).toContain("нельзя перестроить автоматически");
  });
  it("a refusal of the plan is the text of the refusal; a lost source cannot be started", async () => {
    core.force("POST /lineage/rebuild", S.rebuild_unsupported);
    const phase = await planPhase({ kind: "object:report_stub", id: 7 });
    expect(phase).toEqual({ t: "error", text: expect.stringContaining("нельзя перестроить автоматически") });
    core.world.plan = "rebuild_dry_lost";
    const lost = await planPhase(stub(3));
    if (lost.t !== "plan") throw new Error("no plan");
    const html = flat(render(createElement(ImpactView, { node: stub(3), status: undefined, impact: readImpact(S.impact_n1.body), error: null, ask: null, phase: lost, readOnly: false, onPlan: () => undefined, onRun: () => undefined, onGoOn: () => undefined, onCancel: () => undefined, onClose: () => undefined })));
    expect(html).toContain("Источник объекта удалён: перестроить его нельзя");
    expect(html).toMatch(/disabled=""[^>]*data-testid="lineage-run"/);
  });
  it("the run: «running» with the job on the way, then the end with what was rebuilt", async () => {
    const plan = (await planPhase(stub(2))) as Extract<Phase, { t: "plan" }>;
    const shown: Phase[] = [];
    const end = await runPhase(app, stub(2), plan.plan, false, (p) => shown.push(p));
    expect(shown.map((p) => p.t === "running" && p.text)).toEqual(["Запуск… ход — во вкладке «Задания»."]);
    expect(end).toEqual({ t: "end", ok: true, text: "Перестроено объектов: 5." });
  });
  it("the run goes through the command `lineage.rebuild` of the registry: in its log, no second question about the plan", async () => {
    const plan = (await planPhase(stub(2))) as Extract<Phase, { t: "plan" }>;
    const end = await runPhase(app, stub(2), plan.plan, false, () => undefined);
    expect(end).toMatchObject({ t: "end", ok: true });
    const entry = app.registry.entries().find((e) => e.commandId === "lineage.rebuild");
    expect(entry).toMatchObject({ source: "user", ok: true, args: { kind: "object:stub", id: 2, drop_lost: false, confirmed: true } });
    expect(dialogStore.get().queue).toHaveLength(0); // the plan was shown in the window: nothing is asked again
    expect(core.sent.filter((r) => r.path === "/lineage/rebuild").map((r) => r.body.dry_run)).toEqual([true, true, false]);
  });
  it("without `confirmed` the same command asks about the plan (a call from the ribbon or from the keys)", async () => {
    const run = app.registry.execute("lineage.rebuild", { kind: "object:stub", id: 2 }, "user");
    await waitFor(() => dialogStore.get().queue.length > 0);
    expect(dialogStore.get().queue[0].options).toMatchObject({ title: "Перестроить с зависимыми" });
    answerDialog(false);
    expect(await run).toEqual({ ok: true, result: "Отменено" });
  });
  it("a window that is not writable or a refusal before the job: the text of the refusal, not a silence", async () => {
    app.store.set({ projectReadOnly: true });
    const plan = (await planPhase(stub(2))) as Extract<Phase, { t: "plan" }>;
    const end = await runPhase(app, stub(2), plan.plan, false, () => undefined);
    expect(end.t).toBe("error");
  });
  it("a job that was cancelled is not an error but not «done» either; a failed start is the refusal in words", async () => {
    const plan = (await planPhase(stub(2))) as Extract<Phase, { t: "plan" }>;
    core.world.job = "job_cancelled";
    const cancelled = await runPhase(app, stub(2), plan.plan, false, () => undefined);
    expect(cancelled).toMatchObject({ t: "end", ok: false });
    core.force("POST /lineage/rebuild", S.rebuild_pinned);
    // the dry run goes first in the window; the start answers the refusal of the core
    core.sent.length = 0;
    const refused = await runPhase(app, stub(2), plan.plan, false, () => undefined);
    expect(refused).toEqual({ t: "error", text: "Объект «Узел 1» закреплён. Снимите закрепление, чтобы перестроить объект." });
  });
  it("a loss: the window keeps the plan and lists the names; «Перестроить и потерять» sends `drop_lost`", async () => {
    const plan = (await planPhase(stub(2))) as Extract<Phase, { t: "plan" }>;
    core.lost = { status: 409, body: { code: "dependents_lost", message: "При перестроении пропадут результаты «Урезка 5».", hint: "Подтвердите.", lost: [{ id: 4, op: "topcut", title: "Урезка", name: "Урезка 5" }] } };
    const lost = await runPhase(app, stub(2), plan.plan, false, () => undefined);
    expect(lost).toEqual({ t: "lost", plan: plan.plan, lost: [{ name: "Урезка 5", title: "Урезка" }] });
    const html = flat(render(createElement(ImpactView, { node: stub(2), status: undefined, impact: readImpact(S.impact_n1.body), error: null, ask: null, phase: lost, readOnly: false, onPlan: () => undefined, onRun: () => undefined, onGoOn: () => undefined, onCancel: () => undefined, onClose: () => undefined })));
    expect(html).toContain("При перестроении пропадут");
    expect(html).toContain("Урезка «Урезка 5»");
    expect(html).toContain("Перестроить и потерять перечисленное");
    const end = await runPhase(app, stub(2), plan.plan, true, () => undefined);
    expect(end).toMatchObject({ t: "end", ok: true });
    expect(core.sent.filter((r) => r.path === "/lineage/rebuild").at(-1)!.body.drop_lost).toBe(true);
  });
});
