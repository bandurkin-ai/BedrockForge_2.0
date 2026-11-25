// The commands `lineage.*` of the registry (Э18, 18.Г3; ТЗ 7.35.4, ADR 0025 §7–10) on the answers of the real core: the
// declarations (the ribbon, Ctrl+K and the assistant read them), `lineage.status` (an object, a summary), `lineage.impact` (the
// window only when there are dependents — № 90), `lineage.pin`, `lineage.rebuild` (the plan in its own question, the job
// followed, the refusals in words, the loss of results asked about).
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { CoreError } from "../../src/shell/api";
import type { App } from "../../src/shell/app";
import { commandTools } from "../../src/shell/commands";
import { dialogStore } from "../../src/shell/dialogs";
import { moduleCommands } from "../../src/shell/catalog";
import { windowStore } from "../../src/shell/windows";
import { askImpact, IMPACT_WINDOW, isAsking, settleAsk } from "../../src/modules/lineage/impactAsk";
import { runRebuild } from "../../src/modules/lineage/bind";
import { commands, LINEAGE_BUTTONS } from "../../src/modules/lineage/commands";
import { statusOf, watch } from "../../src/modules/lineage/state";
import { answerDialog, installCore, newApp, S, teardown, waitFor, type Core } from "./harness";

let app: App;
let core: Core;
beforeEach(() => {
  core = installCore();
  app = newApp();
});
afterEach(teardown);

const requests = (route: string) => core.sent.filter((r) => `${r.method} ${r.path}` === route);
const rebuilds = () => requests("POST /lineage/rebuild");
const STUB = { kind: "object:stub" };
const execute = (id: string, args: Record<string, unknown> = {}, source: "user" | "claude" = "user") => app.registry.execute(id, args, source);
const confirmed = async (ok: boolean) => {
  await waitFor(() => dialogStore.get().queue.length > 0);
  return answerDialog(ok);
};

describe("the declarations", () => {
  const ids = commands.map((c) => c.id);
  it("the four commands of 7.35.4 are in the catalog, once", () => {
    expect(ids).toEqual(["lineage.status", "lineage.impact", "lineage.rebuild", "lineage.pin"]);
    const catalog = moduleCommands().map((c) => c.id);
    for (const id of ids) expect(catalog.filter((x) => x === id)).toHaveLength(1);
  });
  it("only the rebuild asks (confirm); all four are the assistant's too, with the object as `kind` and `id`", () => {
    const by = Object.fromEntries(commands.map((c) => [c.id, c]));
    expect(by["lineage.rebuild"].confirm).toBe(true);
    for (const id of ["lineage.status", "lineage.impact", "lineage.pin"]) expect(by[id].confirm).toBeFalsy();
    for (const c of commands) {
      expect(c.uiOnly).toBeFalsy();
      expect(Object.keys(c.params!)).toEqual(expect.arrayContaining(["kind", "id", "sub"]));
      expect(c.required).toBeUndefined(); // without them — the object chosen in the tree
    }
    const tools = commandTools(commands);
    expect(tools.map((t) => t.name)).toEqual(["ui_lineage_status", "ui_lineage_impact", "ui_lineage_rebuild", "ui_lineage_pin"]);
    expect(tools[2].description).toContain("после подтверждения");
  });
  it("the ribbon group «Зависимости» has a button for each command", () => {
    expect(LINEAGE_BUTTONS.map((b) => b.command)).toEqual(ids);
    for (const b of LINEAGE_BUTTONS) expect(b.hint.length).toBeGreaterThan(10);
  });
  it("a bound command has no stage; the writing ones wait for a writable project", () => {
    for (const id of ids) expect(app.registry.get(id)!.stage).toBeUndefined();
    app.store.set({ projectReadOnly: true });
    expect(app.registry.reason("lineage.rebuild")).toBe("Проект открыт только для чтения");
    expect(app.registry.reason("lineage.pin")).toBe("Проект открыт только для чтения");
    expect(app.registry.reason("lineage.impact")).toBeNull();
    app.store.set({ projectName: null });
    expect(app.registry.reason("lineage.status")).toBe("Откройте или создайте проект");
  });
});

describe("lineage.status", () => {
  it("an object: its state and the chain of causes in a line", async () => {
    core.world.status = "status_stale";
    const r = await execute("lineage.status", { ...STUB, id: 6 });
    expect(r.ok).toBe(true);
    expect(r.result).toBe("stub «Узел 4»: устарел. Причина: stub «Узел 3» ← stub «Узел 2» ← stub «Узел 1» ← изменено: stub «Источник».");
    expect(statusOf({ ...STUB, id: 6 })!.state).toBe("stale"); // the badge of the tree sees it too
  });
  it("the object chosen in the tree: a block model is `object:block_model`", async () => {
    app.store.set({ selected: { kind: "blocks", id: 9 } });
    const r = await execute("lineage.status");
    expect(requests("POST /lineage/status")[0].body.nodes).toEqual([{ kind: "object:block_model", id: 9 }]);
    expect(r.ok).toBe(false); // the stub core does not know it: «deleted» → not found, in words
    expect(r.result).toContain("Объект не найден");
  });
  it("no object and nothing chosen: the summary of what the tree shows", async () => {
    expect((await execute("lineage.status")).result).toContain("раскройте разделы");
    core.world.status = "status_stale";
    const stop = watch([1, 2, 3].map((id) => ({ ...STUB, id })));
    await waitFor(() => statusOf({ ...STUB, id: 3 }) !== undefined);
    expect((await execute("lineage.status")).result).toBe("Объектов в дереве: 3, устарели: 2 («Узел 1», «Узел 2»).");
    stop();
  });
  it("`id` without `kind` is told in words; a lost source is in the line", async () => {
    const r = await execute("lineage.status", { id: 3 });
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Не задан вид объекта");
    core.world.status = "status_source_lost";
    expect((await execute("lineage.status", { ...STUB, id: 2 })).result).toBe("stub «Узел 1»: источник удалён. Причина: источник удалён: stub «Источник».");
  });
});

describe("lineage.impact", () => {
  it("dependents — the window opens (once for the node), the line has the counts by kind", async () => {
    const r = await execute("lineage.impact", { ...STUB, id: 2 });
    expect(r.ok).toBe(true);
    expect(r.result).toContain("построено объектов: 5 (stub: 4; report_stub: 1)");
    const wins = windowStore.get().windows.filter((w) => w.kind === IMPACT_WINDOW);
    expect(wins).toHaveLength(1);
    expect(wins[0].title).toBe("Влияние изменения");
    expect(wins[0].props).toEqual({ kind: "object:stub", id: 2, sub: "" });
    await execute("lineage.impact", { ...STUB, id: 2 });
    expect(windowStore.get().windows.filter((w) => w.kind === IMPACT_WINDOW)).toHaveLength(1);
  });
  it("no dependents — no window (№ 90), only the words", async () => {
    const r = await execute("lineage.impact", { ...STUB, id: 6 });
    expect(r.result).toContain("ничего не построено: изменение никого не затронет");
    expect(windowStore.get().windows).toHaveLength(0);
  });
  it("the assistant gets the names of the dependents too", async () => {
    const r = await execute("lineage.impact", { ...STUB, id: 2 }, "claude");
    expect(r.result).toContain("Зависимые: stub «Узел 2»; stub «Ветка M»; stub «Узел 3»; report_stub «Отчёт»; stub «Узел 4».");
  });
  it("a node that is not there: 404 in words with the hint", async () => {
    const r = await execute("lineage.impact", { ...STUB, id: 424242 });
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Объект не найден");
    expect(r.result).toContain("Обновите дерево проекта");
  });
  it("as a question before a change: no dependents — go on at once; dependents — the window with the token, the answer of the person", async () => {
    expect(await askImpact({ ...STUB, id: 6 }, "удалить таблицу")).toBe(true);
    expect(windowStore.get().windows).toHaveLength(0);
    const asking = askImpact({ ...STUB, id: 2 }, "заменить таблицу «Пробы»");
    await waitFor(() => windowStore.get().windows.length === 1);
    const w = windowStore.get().windows[0];
    expect(w.props).toMatchObject({ kind: "object:stub", id: 2, ask: "заменить таблицу «Пробы»" });
    const token = w.props!.token as number;
    expect(isAsking(token)).toBe(true);
    settleAsk(token, true);
    expect(await asking).toBe(true);
    expect(isAsking(token)).toBe(false);
    const again = askImpact({ ...STUB, id: 2 }, "удалить");
    await waitFor(() => windowStore.get().windows.length === 2);
    settleAsk(windowStore.get().windows[1].props!.token as number, false);
    expect(await again).toBe(false);
  });
  it("the core does not answer — the change is not stopped by the graph", async () => {
    core.force("GET /lineage/impact", { status: 500, body: { code: "error", message: "x", hint: "" } });
    expect(await askImpact({ ...STUB, id: 2 }, "удалить")).toBe(true);
  });
});

describe("lineage.pin", () => {
  it("pins (the state of the node comes back), unpins with the cause told again", async () => {
    const pinned = await execute("lineage.pin", { ...STUB, id: 2, pinned: true });
    expect(requests("POST /lineage/pin")[0].body).toEqual({ node: { kind: "object:stub", id: 2 }, pinned: true });
    expect(pinned.result).toBe("Объект stub «Узел 1» закреплён: его входы уже изменились, но он не помечен «устарел».");
    expect(statusOf({ ...STUB, id: 2 })!.state).toBe("pinned");
    const free = await execute("lineage.pin", { ...STUB, id: 2, pinned: false });
    expect(free.result).toBe("Закрепление снято с объекта stub «Узел 1»: устарел. Причина: изменено: stub «Источник».");
  });
  it("without `pinned` it toggles by the state the core tells", async () => {
    core.world.status = "status_pinned";
    await execute("lineage.pin", { ...STUB, id: 2 });
    expect(requests("POST /lineage/pin")[0].body.pinned).toBe(false);
    core.world.status = "status_current";
    await execute("lineage.pin", { ...STUB, id: 2 });
    expect(requests("POST /lineage/pin")[1].body.pinned).toBe(true);
  });
  it("a project open for reading only is told before any request", async () => {
    app.store.set({ projectReadOnly: true });
    const r = await execute("lineage.pin", { ...STUB, id: 2, pinned: true });
    expect(r).toEqual({ ok: false, result: "Проект открыт только для чтения" });
    expect(requests("POST /lineage/pin")).toHaveLength(0);
  });
});

describe("lineage.rebuild", () => {
  const head = { ...STUB, id: 2 };
  it("the user: the plan in its own question (the order, what is left out), then the job to its end", async () => {
    core.world.status = "status_stale";
    const run = execute("lineage.rebuild", head);
    const q = await confirmed(true);
    expect(q.title).toBe("Перестроить с зависимыми");
    expect(q.okLabel).toBe("Перестроить");
    expect(q.message).toContain("1. stub «Узел 1» — устарел (изменено: stub «Источник»)");
    expect(q.message).toContain("2. stub «Узел 2» — устарел (stub «Узел 1» ← изменено: stub «Источник»)");
    expect(q.message).toContain("Не будут перестроены:\nreport_stub «Отчёт» — report_stub «Отчёт» нельзя перестроить автоматически");
    const r = await run;
    expect(r).toEqual({ ok: true, result: "Перестроено объектов: 5." });
    expect(rebuilds().map((x) => [x.body.dry_run, x.body.with_dependents, x.body.drop_lost])).toEqual([[true, true, false], [false, true, false]]);
    expect(app.store.get().workspace).toMatchObject({ bottom: true, bottomTab: "jobs" }); // «Задания» is shown for the job
  });
  it("«Отмена» in the question starts nothing", async () => {
    const run = execute("lineage.rebuild", head);
    await confirmed(false);
    expect(await run).toEqual({ ok: true, result: "Отменено" });
    expect(rebuilds().map((x) => x.body.dry_run)).toEqual([true]);
  });
  it("only the node: `with_dependents=false` goes to the core as it is", async () => {
    core.world.plan = "dry_run_alone";
    const run = execute("lineage.rebuild", { ...head, with_dependents: false });
    const q = await confirmed(true);
    expect(q.title).toBe("Перестроить объект");
    expect((await run).ok).toBe(true);
    expect(rebuilds().every((x) => x.body.with_dependents === false)).toBe(true);
  });
  it("the assistant is asked by the registry, not by the command: no question of its own", async () => {
    const r = await runRebuild(app, head, "claude");
    expect(r).toBe("Перестроено объектов: 5.");
    expect(dialogStore.get().queue).toHaveLength(0);
  });
  it("a pinned node is told in words before any question (409)", async () => {
    core.force("POST /lineage/rebuild", S.rebuild_pinned);
    const r = await execute("lineage.rebuild", head);
    expect(r.ok).toBe(false);
    expect(r.result).toBe("Объект «Узел 1» закреплён. Снимите закрепление, чтобы перестроить объект.");
    expect(dialogStore.get().queue).toHaveLength(0);
    expect(rebuilds()).toHaveLength(1);
  });
  it("a kind without a maker (422) and a lost source say what to do", async () => {
    core.force("POST /lineage/rebuild", S.rebuild_unsupported);
    const a = await execute("lineage.rebuild", { kind: "object:report_stub", id: 7 });
    expect(a.result).toContain("нельзя перестроить автоматически");
    expect(a.result).toContain("Перестройте его командой своего модуля");
    core.world.plan = "rebuild_dry_lost";
    const b = await execute("lineage.rebuild", { ...STUB, id: 3 });
    expect(b.ok).toBe(false);
    expect(b.result).toBe("Источник объекта «Узел 2» удалён. Создайте объект заново из существующих данных.");
  });
  it("a failed job is the error of the job in words; a cancelled one says how many were done", async () => {
    core.world.job = "job_cancelled";
    const run = execute("lineage.rebuild", head);
    await confirmed(true);
    const r = await run;
    expect(r.ok).toBe(true);
    expect(r.result).toBe("Перестроение отменено: готово 0, остальные остались «устарел».");
  });
  const LOST = { status: 409, body: { code: "dependents_lost", message: "При перестроении пропадут результаты «Урезка 5».", hint: "Подтвердите перестроение.", lost: [{ id: 4, op: "topcut", title: "Урезка", name: "Урезка 5" }, { kind: "column", name: "AU_CUT" }] } };
  it("a rebuild that loses results asks again, with the names; «Перестроить и потерять» sends `drop_lost`", async () => {
    core.lost = LOST; // the shape of composites/jobs.py `refuse_lost`
    const run = execute("lineage.rebuild", head);
    await confirmed(true);
    const q = await confirmed(true);
    expect(q.title).toBe("При перестроении пропадут");
    expect(q.message).toContain("Урезка «Урезка 5»\nколонка «AU_CUT»");
    expect(q.okLabel).toBe("Перестроить и потерять");
    expect(await run).toEqual({ ok: true, result: "Перестроено объектов: 5." });
    expect(rebuilds().map((x) => [x.body.dry_run, x.body.drop_lost])).toEqual([[true, false], [false, false], [false, true]]);
  });
  it("«Отмена» in the second question loses nothing and starts nothing", async () => {
    core.lost = LOST;
    const run = execute("lineage.rebuild", head);
    await confirmed(true);
    await confirmed(false);
    expect(await run).toEqual({ ok: true, result: "Отменено" });
    expect(rebuilds().filter((x) => x.body.drop_lost === true)).toHaveLength(0);
  });
  it("the assistant is not asked about a loss: it gets the names and must say `drop_lost`", async () => {
    core.lost = LOST;
    const e = await runRebuild(app, head, "claude").catch((x: unknown) => x);
    expect((e as CoreError).full).toBe("При перестроении пропадут: Урезка «Урезка 5»; колонка «AU_CUT». Повторите с drop_lost=true, если согласны на потерю.");
    expect(dialogStore.get().queue).toHaveLength(0);
    expect(await runRebuild(app, { ...head, drop_lost: true }, "claude")).toBe("Перестроено объектов: 5.");
    expect(rebuilds().at(-1)!.body.drop_lost).toBe(true);
  });
  it("the registry asks the user before the assistant changes data (`confirm`): «отклонил» starts nothing", async () => {
    const run = execute("lineage.rebuild", head, "claude");
    await waitFor(() => dialogStore.get().queue.length > 0);
    answerDialog(false);
    expect((await run).ok).toBe(false);
    expect(rebuilds()).toHaveLength(0);
  });
});
