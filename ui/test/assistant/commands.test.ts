// The registry as the assistant sees it (Э23, 23.15; ТЗ 7.2 «Приёмка», 7.32; ADR 0029 решения 25, 26, 35, вопрос № 135): a command added only to the registry is a tool of Claude;
// `uiOnly` is not seen; `confirm` from Claude does not run without «Разрешить»; a refusal is «Пользователь отклонил действие»; wrong arguments are a text, not an exception;
// the log gets the source «Claude», `silent` is not written; the commands that delete, replace or clear data have `confirm` (the exceptions are a list, here).
import { afterEach, describe, expect, it, vi } from "vitest";
import { moduleCommands } from "../../src/shell/catalog";
import { commandTools, findByToolName, toolName, type Command, type CommandContext } from "../../src/shell/commands";
import { CommandRegistry, DECLINED } from "../../src/shell/registry";
import { commands as assistantCommands, CONNECT_SECTIONS } from "../../src/modules/assistant/commands";
import { answerConfirm, confirmStore, declineAllConfirms, installConfirm } from "../../src/modules/assistant/confirm";

const ONLINE: CommandContext = { hasProject: true, hasSet: true, hasHole: true, hasObject: false, hasSection: false, editing: false, coreOnline: true };

function registryWith(commands: Command[]): CommandRegistry {
  const r = new CommandRegistry();
  r.register(commands);
  r.hooks = { ...r.hooks, context: () => ONLINE };
  return r;
}

afterEach(() => declineAllConfirms());

describe("the commands of the assistant (ADR 0029 решение 35)", () => {
  const byId = new Map(assistantCommands.map((c) => [c.id, c]));

  it("are the eight of the decision; only `assistant.ask` is seen by Claude", () => {
    expect([...byId.keys()].sort()).toEqual(["assistant.ask", "assistant.connect", "assistant.delete", "assistant.history", "assistant.new", "assistant.open", "assistant.rename", "assistant.stop"]);
    expect(assistantCommands.filter((c) => !c.uiOnly).map((c) => c.id)).toEqual(["assistant.ask"]);
  });

  it("`silent` is exactly the pair of ТЗ 7.2: `assistant.ask`, `assistant.new`", () => {
    expect(assistantCommands.filter((c) => c.silent).map((c) => c.id).sort()).toEqual(["assistant.ask", "assistant.new"]);
  });

  it("are in the catalog of the application once, with Russian titles and descriptions", () => {
    const all = moduleCommands();
    for (const c of assistantCommands) {
      expect(all.filter((x) => x.id === c.id), c.id).toHaveLength(1);
      expect(c.title, c.id).toMatch(/[А-Яа-яЁё]/);
      expect(c.description.length, c.id).toBeGreaterThan(5);
    }
  });

  it("`assistant.connect` takes a section of the window; `assistant.open|rename|delete` need the dialog", () => {
    expect(byId.get("assistant.connect")?.params?.section.enum).toEqual([...CONNECT_SECTIONS]);
    for (const id of ["assistant.open", "assistant.rename", "assistant.delete"]) expect(byId.get(id)?.required, id).toContain("dialog_id");
    expect(byId.get("assistant.rename")?.required).toContain("title");
  });
});

describe("ТЗ 7.2 «Приёмка»: the registry is the source of the tools", () => {
  const hello: Command = { id: "test.hello", title: "Привет", description: "Тестовая команда реестра.", params: { name: { type: "string" } }, required: ["name"], run: (a) => `Привет, ${String(a.name)}` };

  it("a command added only to the registry is a tool, with no other change", () => {
    const r = registryWith([]);
    expect(commandTools(r.list()).map((t) => t.name)).not.toContain("ui_test_hello");
    r.register([hello]);
    const tool = commandTools(r.list()).find((t) => t.name === "ui_test_hello");
    expect(tool?.input_schema.required).toEqual(["name"]);
    expect(findByToolName(r.list(), "ui_test_hello")?.id).toBe("test.hello");
    expect(toolName("test.hello")).toBe("ui_test_hello");
  });

  it("`uiOnly` is not seen by Claude and is refused when it asks", async () => {
    const run = vi.fn(() => "x");
    const r = registryWith([{ id: "test.hidden", title: "Скрытая", description: "Только для интерфейса.", uiOnly: true, run }]);
    expect(commandTools(r.list()).map((t) => t.name)).not.toContain("ui_test_hidden");
    expect(findByToolName(r.list(), "ui_test_hidden")).toBeUndefined();
    const res = await r.execute("test.hidden", {}, "claude");
    expect(res.ok).toBe(false);
    expect(run).not.toHaveBeenCalled();
    expect((await r.execute("test.hidden", {}, "user")).ok).toBe(true);
  });

  it("the tools of the real catalog carry the assistant's `ask` and none of its uiOnly commands", () => {
    const names = commandTools(moduleCommands()).map((t) => t.name);
    expect(names).toContain("ui_assistant_ask");
    for (const c of assistantCommands.filter((x) => x.uiOnly)) expect(names, c.id).not.toContain(toolName(c.id));
  });

  it("`confirm` from Claude does not run without «Разрешить»; a refusal is «Пользователь отклонил действие»", async () => {
    const run = vi.fn(() => "Удалено");
    const r = registryWith([{ id: "test.drop", title: "Удалить всё", description: "Удаляет тестовые данные.", confirm: true, params: { id: { type: "integer" } }, required: ["id"], run }]);
    installConfirm(r);

    const refused = r.execute("test.drop", { id: 7 }, "claude");
    await vi.waitFor(() => expect(confirmStore.get().queue).toHaveLength(1));
    expect(run).not.toHaveBeenCalled();
    answerConfirm(confirmStore.get().queue[0].id, false);
    expect(await refused).toEqual({ ok: false, result: DECLINED });
    expect(DECLINED).toBe("Пользователь отклонил действие");
    expect(run).not.toHaveBeenCalled();

    const allowed = r.execute("test.drop", { id: 7 }, "claude");
    await vi.waitFor(() => expect(confirmStore.get().queue).toHaveLength(1));
    answerConfirm(confirmStore.get().queue[0].id, true);
    expect(await allowed).toEqual({ ok: true, result: "Удалено" });
    expect(run).toHaveBeenCalledTimes(1);
  });

  it("the user's own call of a `confirm` command is not asked again (the confirmation is for Claude)", async () => {
    const run = vi.fn(() => "Готово");
    const r = registryWith([{ id: "test.drop", title: "Удалить", description: "Удаляет тестовые данные.", confirm: true, run }]);
    installConfirm(r);
    expect((await r.execute("test.drop", {}, "user")).ok).toBe(true);
    expect(confirmStore.get().queue).toHaveLength(0);
  });

  it("wrong arguments are a Russian text, not an exception, and the command does not run", async () => {
    const run = vi.fn(() => "x");
    const r = registryWith([{ ...hello, run }]);
    for (const args of [{}, { name: 5 }, { name: "a", extra: 1 }] as Record<string, unknown>[]) {
      const res = await r.execute("test.hello", args, "claude");
      expect(res.ok).toBe(false);
      expect(res.result).toMatch(/[А-Яа-яЁё]/);
    }
    expect((await r.execute("test.hello", null as never, "claude")).result).toBe("Аргументы должны быть объектом");
    expect((await r.execute("test.nothing", {}, "claude")).ok).toBe(false);
    expect(run).not.toHaveBeenCalled();
  });

  it("the log keeps the source «Claude»; a `silent` command is not written", async () => {
    const r = registryWith([hello, { id: "test.quiet", title: "Тихая", description: "Не пишется в журнал.", silent: true, run: () => "ok" }]);
    await r.execute("test.hello", { name: "мир" }, "claude");
    await r.execute("test.hello", { name: "я" }, "user");
    await r.execute("test.quiet", {}, "claude");
    expect(r.entries().map((e) => [e.commandId, e.source])).toEqual([
      ["test.hello", "user"],
      ["test.hello", "claude"],
    ]);
  });
});

describe("ТЗ 7.2 + ADR 0029 решение 25: the flags of the commands that destroy data", () => {
  const all = moduleCommands();
  const visible = all.filter((c) => !c.uiOnly); // a uiOnly command cannot be asked by Claude at all
  const words = (id: string) => id.split(/[._]/);

  /**
   * The commands of the names «удалить / заменить / очистить / сбросить» that do NOT need `confirm`, with the reason. The list is closed: a new command of this kind
   * must have `confirm`, or be added here by a decision.
   */
  const NO_CONFIRM_REASON: Record<string, string> = {
    "scene.clear": "очищает окно 3D (слои скрываются), данные проекта целы",
    "section.clear": "сбрасывает линию разреза вида; вид — не данные проекта",
    "selection.clear": "снимает выбор, ничего не меняет в данных",
    // a gap against ТЗ 7.2: the handler asks itself (a modal list of names, ADR 0029 решение 27), but without `confirm` the chat card is not shown — reported to the orchestrator
    "data.delete": "своё модальное подтверждение со списком имён в обработчике (datamanager); флаг `confirm` не задан",
  };

  it("every command that deletes, replaces or clears has `confirm` (the exceptions are the explicit list)", () => {
    const destructive = visible.filter((c) => words(c.id).some((w) => ["delete", "remove", "replace", "clear", "purge", "wipe", "reset", "drop", "erase", "truncate"].includes(w)));
    expect(destructive.length).toBeGreaterThan(15); // the rule looks at the real catalog, not at an empty list
    const without = destructive.filter((c) => !c.confirm).map((c) => c.id).sort();
    expect(without).toEqual(Object.keys(NO_CONFIRM_REASON).sort());
  });

  it("the exceptions exist (a deleted command does not stay in the list) and have no `confirm`", () => {
    for (const id of Object.keys(NO_CONFIRM_REASON)) {
      const c = all.find((x) => x.id === id);
      expect(c, id).toBeDefined();
      expect(c?.confirm, id).toBeFalsy();
    }
  });

  it("the assistant's own commands that delete (`assistant.delete`) are uiOnly: Claude cannot ask for them", () => {
    expect(assistantCommands.find((c) => c.id === "assistant.delete")?.uiOnly).toBe(true);
    expect(visible.some((c) => c.id === "assistant.delete")).toBe(false);
  });

  it("the commands that export files are as in ТЗ 7.2 (вопрос № 135): no `confirm`; adding it later does not break the rule", () => {
    const exporting = visible.filter((c) => words(c.id).includes("export"));
    const KNOWN = ["blocks.export", "lines.export", "omf.export", "planned.export", "project.export", "rasters.export", "resources.export", "variography.export", "wireframe.export"];
    for (const id of ["project.export", "omf.export", "lines.export", "wireframe.export"]) expect(exporting.map((c) => c.id), id).toContain(id);
    // an export is either confirmed, or on the list of № 135 — a new unconfirmed export is a question to the customer
    const loose = exporting.filter((c) => !c.confirm).map((c) => c.id);
    for (const id of loose) expect(KNOWN, id).toContain(id);
  });
});
