// The commands of the window Claude asked for (Э23, 23.15; ADR 0029 решения 22–23, 26; docs/progress/e23-api.md §9–§11): in order, by `registry.execute(…, "claude")`, found by the `name` of
// the tool (not by `command`), a result of ≤ 4000 characters, `confirm` waits for the card, «Стоп» leaves the rest unrun, a dialog opened with `pending` waits for «Выполнить» / «Отклонить».
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Command, CommandContext } from "../../src/shell/commands";
import { CommandRegistry, DECLINED } from "../../src/shell/registry";
import { answerConfirm, confirmStore, declineAllConfirms, installConfirm } from "../../src/modules/assistant/confirm";
import {
  buttonsItemId,
  clipResult,
  NO_SUCH_COMMAND,
  PendingRunner,
  pendingStore,
  RESULT_MAX,
  statusOf,
  STOPPED,
  type PendingIo,
} from "../../src/modules/assistant/pending";
import type { Item, Pending, ToolItem, ToolResultIn, ToolStatus } from "../../src/modules/assistant/wireTypes";

const ONLINE: CommandContext = { hasProject: true, hasSet: true, hasHole: true, hasObject: false, hasSection: false, editing: false, coreOnline: true };
const T0 = "2026-11-26T10:00:00Z";

const toolItem = (id: string, name: string, status: ToolStatus = "waiting"): ToolItem => ({ kind: "tool", id, at: T0, tool_use_id: `toolu_${id}`, name, side: "ui", title: name, args: {}, status, summary: "" });
const pendingOf = (...tools: { name: string; args?: Record<string, unknown>; command?: string | null }[]): Pending => ({
  id: "p-1",
  created_at: T0,
  tools: tools.map((t, i) => ({ tool_use_id: `toolu_i${i}`, name: t.name, command: (t.command === undefined ? t.name : t.command) as string, args: t.args ?? {}, item_id: `i${i}` })),
});

interface Harness {
  io: PendingIo;
  marks: [string, ToolStatus, string][];
  submitted: ToolResultIn[][];
  working: boolean[];
  /** the pending the dialog waits for now (null — it is gone) */
  state: { pending: Pending | null; items: Item[] };
}

function harness(pending: Pending | null, submit: PendingIo["submit"] | null = null): Harness {
  const h = { marks: [], submitted: [], working: [], state: { pending, items: pending ? pending.tools.map((t) => toolItem(t.item_id, t.name)) : [] } } as unknown as Harness;
  h.io = {
    chat: () => ({ pending: h.state.pending, items: h.state.items, status: "pending" }),
    mark: (id, status, summary) => h.marks.push([id, status, summary]),
    working: (on) => h.working.push(on),
    submit: submit ?? ((results) => (h.submitted.push(results), Promise.resolve(null))),
  };
  return h;
}

function registry(commands: Command[]): CommandRegistry {
  const r = new CommandRegistry();
  r.register(commands);
  r.hooks = { ...r.hooks, context: () => ONLINE };
  installConfirm(r);
  return r;
}

const cmd = (id: string, run: Command["run"], extra: Partial<Command> = {}): Command => ({ id, title: `Команда ${id}`, description: `Описание команды ${id}.`, run, ...extra });

afterEach(() => declineAllConfirms());

describe("the pure parts", () => {
  it("a result is cut to 4000 characters with «…»; a short one stays", () => {
    expect(clipResult("ok")).toBe("ok");
    expect(clipResult("a".repeat(RESULT_MAX))).toHaveLength(RESULT_MAX);
    const cut = clipResult("a".repeat(RESULT_MAX + 500));
    expect(cut).toHaveLength(RESULT_MAX);
    expect(cut.endsWith("…")).toBe(true);
  });

  it("the card shows «отклонено» for a refusal of the user and «ошибка» for any other failure", () => {
    expect(statusOf({ ok: true, text: "x" })).toBe("ok");
    expect(statusOf({ ok: false, text: DECLINED })).toBe("declined");
    expect(statusOf({ ok: false, text: STOPPED })).toBe("declined");
    expect(statusOf({ ok: false, text: "Не задан параметр «id»" })).toBe("error");
  });
});

describe("PendingRunner.run", () => {
  it("runs the commands in order by the name of the tool, with the source «Claude», and sends the results", async () => {
    const order: string[] = [];
    const r = registry([
      cmd("hole.select", (a) => (order.push(`select:${String(a.hole_id)}`), `Выбрана скважина ${String(a.hole_id)}`), { params: { hole_id: { type: "string" } }, required: ["hole_id"] }),
      cmd("section.through_hole", () => (order.push("section"), "Разрез через скважину")),
    ]);
    // `command: null` — the core may not know the registry id; the window finds the command by the name of the tool (`_` of the id may be a dot or an underscore)
    const pending = pendingOf({ name: "ui_hole_select", args: { hole_id: "DH-012" }, command: null }, { name: "ui_section_through_hole", command: null });
    const h = harness(pending);
    const rep = await new PendingRunner(r, h.io).run(pending);

    expect(order).toEqual(["select:DH-012", "section"]);
    expect(rep.submitted).toBe(true);
    expect(h.submitted).toEqual([
      [
        { tool_use_id: "toolu_i0", ok: true, text: "Выбрана скважина DH-012", title: "Команда hole.select" },
        { tool_use_id: "toolu_i1", ok: true, text: "Разрез через скважину", title: "Команда section.through_hole" },
      ],
    ]);
    expect(h.marks).toEqual([
      ["i0", "ok", "Выбрана скважина DH-012"],
      ["i1", "ok", "Разрез через скважину"],
    ]);
    expect(r.entries().map((e) => [e.commandId, e.source])).toEqual([
      ["section.through_hole", "claude"],
      ["hole.select", "claude"],
    ]);
    expect(h.working).toEqual([true]); // the panel shows «Стоп» while the commands run
    expect(pendingStore.get().running).toBeNull();
  });

  it("a command of a different shape of name is not guessed: unknown or uiOnly — «Нет такой команды», the run goes on", async () => {
    const run = vi.fn(() => "x");
    const r = registry([cmd("assistant.new", run, { uiOnly: true }), cmd("hole.select", () => "Выбрана")]);
    const pending = pendingOf({ name: "ui_assistant_new" }, { name: "ui_no_such" }, { name: "ui_hole_select" });
    const h = harness(pending);
    await new PendingRunner(r, h.io).run(pending);
    expect(run).not.toHaveBeenCalled();
    expect(h.submitted[0].map((x) => [x.ok, x.text])).toEqual([
      [false, NO_SUCH_COMMAND],
      [false, NO_SUCH_COMMAND],
      [true, "Выбрана"],
    ]);
    expect(h.marks.map((m) => m[1])).toEqual(["error", "error", "ok"]);
  });

  it("wrong arguments and an unavailable command are a text for Claude, not an exception", async () => {
    const r = registry([
      cmd("hole.select", () => "Выбрана", { params: { hole_id: { type: "string" } }, required: ["hole_id"] }),
      cmd("table.open", () => "Открыта", { unavailable: () => "Нет активного набора скважин" }),
    ]);
    const pending = pendingOf({ name: "ui_hole_select", args: {} }, { name: "ui_table_open" });
    const h = harness(pending);
    await new PendingRunner(r, h.io).run(pending);
    expect(h.submitted[0].map((x) => x.text)).toEqual(["Не задан параметр «hole_id»", "Нет активного набора скважин"]);
    expect(h.submitted[0].every((x) => !x.ok)).toBe(true);
  });

  it("a command that throws is a result with its message; the next command still runs", async () => {
    const r = registry([
      cmd("a.fail", () => {
        throw new Error("Сбой чтения");
      }),
      cmd("a.ok", () => "Хорошо"),
    ]);
    const pending = pendingOf({ name: "ui_a_fail" }, { name: "ui_a_ok" });
    const h = harness(pending);
    await new PendingRunner(r, h.io).run(pending);
    expect(h.submitted[0].map((x) => [x.ok, x.text])).toEqual([
      [false, "Сбой чтения"],
      [true, "Хорошо"],
    ]);
  });

  it("the text of a result is cut to 4000 characters", async () => {
    const r = registry([cmd("logs.last", () => "я".repeat(9000))]);
    const pending = pendingOf({ name: "ui_logs_last" });
    const h = harness(pending);
    await new PendingRunner(r, h.io).run(pending);
    expect(h.submitted[0][0].text).toHaveLength(RESULT_MAX);
    expect(h.marks[0][2]).toHaveLength(RESULT_MAX);
  });

  it("`confirm` waits for «Разрешить» before it runs; «Отклонить» — «Пользователь отклонил действие», the run goes on", async () => {
    const del = vi.fn(() => "Удалён");
    const r = registry([cmd("project.delete", del, { confirm: true }), cmd("hole.select", () => "Выбрана")]);
    const pending = pendingOf({ name: "ui_project_delete" }, { name: "ui_hole_select" });
    const h = harness(pending);
    const done = new PendingRunner(r, h.io).run(pending);
    await vi.waitFor(() => expect(confirmStore.get().queue).toHaveLength(1));
    expect(del).not.toHaveBeenCalled();
    expect(h.submitted).toEqual([]);
    answerConfirm(confirmStore.get().queue[0].id, false);
    await done;
    expect(del).not.toHaveBeenCalled();
    expect(h.submitted[0].map((x) => [x.ok, x.text])).toEqual([
      [false, "Пользователь отклонил действие"],
      [true, "Выбрана"],
    ]);
    expect(h.marks[0]).toEqual(["i0", "declined", "Пользователь отклонил действие"]);
  });

  it("`confirm` allowed: the command runs and the card is «ok»", async () => {
    const del = vi.fn(() => "Удалён");
    const r = registry([cmd("project.delete", del, { confirm: true })]);
    const pending = pendingOf({ name: "ui_project_delete" });
    const h = harness(pending);
    const done = new PendingRunner(r, h.io).run(pending);
    await vi.waitFor(() => expect(confirmStore.get().queue).toHaveLength(1));
    answerConfirm(confirmStore.get().queue[0].id, true);
    await done;
    expect(del).toHaveBeenCalledTimes(1);
    expect(h.marks[0]).toEqual(["i0", "ok", "Удалён"]);
  });

  it("«Стоп»: the question is declined, the rest are «Пользователь остановил ход», the results are sent all the same", async () => {
    const second = vi.fn(() => "x");
    const r = registry([cmd("project.delete", () => "Удалён", { confirm: true }), cmd("hole.select", second)]);
    const pending = pendingOf({ name: "ui_project_delete" }, { name: "ui_hole_select" });
    const h = harness(pending);
    const runner = new PendingRunner(r, h.io);
    const done = runner.run(pending);
    await vi.waitFor(() => expect(confirmStore.get().queue).toHaveLength(1));
    expect(runner.active).toBe(true);
    runner.stop();
    const rep = await done;
    expect(second).not.toHaveBeenCalled();
    expect(rep.submitted).toBe(true);
    expect(h.submitted[0].map((x) => [x.ok, x.text])).toEqual([
      [false, "Пользователь отклонил действие"],
      [false, "Пользователь остановил ход"],
    ]);
    expect(h.marks.map((m) => m[1])).toEqual(["declined", "declined"]);
    expect(runner.active).toBe(false);
  });

  it("a stop between commands skips the rest; a stop with no run does nothing", async () => {
    const third = vi.fn(() => "x");
    let runner!: PendingRunner;
    const r = registry([cmd("a.one", () => (runner.stop(), "Первая")), cmd("a.two", third)]);
    const pending = pendingOf({ name: "ui_a_one" }, { name: "ui_a_two" });
    const h = harness(pending);
    runner = new PendingRunner(r, h.io);
    runner.stop(); // nothing runs yet
    await runner.run(pending);
    expect(third).not.toHaveBeenCalled();
    expect(h.submitted[0].map((x) => x.text)).toEqual(["Первая", "Пользователь остановил ход"]);
  });

  it("the pending that changed under the run (a new question, another dialog) is not answered", async () => {
    const h = harness(null);
    const pending = pendingOf({ name: "ui_a_one" }, { name: "ui_a_two" });
    h.state.pending = pending;
    h.state.items = pending.tools.map((t) => toolItem(t.item_id, t.name));
    const second = vi.fn(() => "x");
    const r = registry([cmd("a.one", () => ((h.state.pending = null), "Первая")), cmd("a.two", second)]);
    const rep = await new PendingRunner(r, h.io).run(pending);
    expect(second).not.toHaveBeenCalled();
    expect(rep).toMatchObject({ submitted: false, skipped: "stale" });
    expect(h.submitted).toEqual([]);
  });

  it("a second run while one goes on is refused; when nothing can be sent the panel waits again", async () => {
    let release!: () => void;
    const r = registry([cmd("a.slow", () => new Promise<string>((res) => (release = () => res("Готово"))))]);
    const pending = pendingOf({ name: "ui_a_slow" });
    const h = harness(pending, () => null);
    const runner = new PendingRunner(r, h.io);
    const first = runner.run(pending);
    expect(pendingStore.get().running).toBe("p-1");
    expect(await runner.run(pending)).toMatchObject({ skipped: "busy", submitted: false });
    release();
    const rep = await first;
    expect(rep.submitted).toBe(false);
    expect(h.working).toEqual([true, false]);
  });
});

describe("a dialog opened with `pending` (решение 23)", () => {
  it("«Отклонить»: every command is declined, the model is told so, nothing runs", () => {
    const run = vi.fn(() => "x");
    const r = registry([cmd("hole.select", run)]);
    const pending = pendingOf({ name: "ui_hole_select" }, { name: "ui_project_delete" });
    const h = harness(pending);
    const rep = new PendingRunner(r, h.io).decline(pending);
    expect(run).not.toHaveBeenCalled();
    expect(rep.submitted).toBe(true);
    expect(h.submitted[0].map((x) => [x.tool_use_id, x.ok, x.text])).toEqual([
      ["toolu_i0", false, "Пользователь отклонил действие"],
      ["toolu_i1", false, "Пользователь отклонил действие"],
    ]);
    expect(h.marks.map((m) => m[1])).toEqual(["declined", "declined"]);
  });

  it("the buttons are on the first card that still waits, when no run goes on; none otherwise", () => {
    const pending = pendingOf({ name: "ui_a" }, { name: "ui_b" });
    const chat = { pending, items: pending.tools.map((t) => toolItem(t.item_id, t.name)), status: "pending" as const };
    expect(buttonsItemId(chat, false)).toBe("i0");
    expect(buttonsItemId(chat, true)).toBeNull(); // the commands run: no buttons
    expect(buttonsItemId({ ...chat, status: "running" }, false)).toBeNull();
    expect(buttonsItemId({ ...chat, pending: null }, false)).toBeNull();
    const first = { ...chat, items: [toolItem("i0", "ui_a", "ok"), toolItem("i1", "ui_b")] };
    expect(buttonsItemId(first, false)).toBe("i1");
    expect(buttonsItemId({ ...chat, items: chat.items.map((i) => ({ ...i, status: "declined" }) as Item) }, false)).toBeNull();
  });
});
