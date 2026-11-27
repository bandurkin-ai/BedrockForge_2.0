import { describe, expect, it } from "vitest";
import {
  addLocalUser,
  applyEvents,
  dropPending,
  EMPTY_CHAT,
  fromDialog,
  isRunning,
  setToolResult,
  type ChatState,
} from "../../src/modules/assistant/reduce";
import type { AssistantItem, Dialog, DialogHead, Item, ToolItem, TurnEnvelope } from "../../src/modules/assistant/wireTypes";

// The streams of the contract (docs/progress/e23-api.md §15: stream_text, stream_data_tools, stream_pending, stream_after_results,
// stream_confirm, stream_offline, stream_cancelled), written out by hand; the answers of the core replace them in 23.11.

const T0 = "2026-11-26T10:00:00Z";
const ZERO = { input: 0, output: 0, cache_read: 0, cache_write: 0 };
const head = (over: Partial<DialogHead> = {}): DialogHead => ({
  id: "41",
  uid: "d-1",
  title: "Сводка по проекту",
  created_at: T0,
  last_at: T0,
  questions: 1,
  cost_usd: null,
  tokens: ZERO,
  models: ["claude-opus-5-5"],
  state: "running",
  temporary: false,
  project_uid: "p-1",
  ...over,
});

const ev = <E extends TurnEnvelope["event"]>(event: E, data: Extract<TurnEnvelope, { event: E }>["data"], id: number | null = null): TurnEnvelope =>
  ({ event, data, id }) as TurnEnvelope;
/** A segment: the events are numbered from 1 as the core numbers them. */
const seg = (...evs: TurnEnvelope[]): TurnEnvelope[] => evs.map((e, i) => ({ ...e, id: i + 1 }) as TurnEnvelope);
const user = (text: string, id = "u1"): Item => ({ kind: "user", id, at: T0, text });
const start = (user_item?: Item) => ev("turn.start", { dialog: head(), segment_id: "s-1", model: "claude-opus-5-5", effort: "medium", user_item: user_item as never }, 1);
const delta = (item_id: string, text: string) => ev("text.delta", { item_id, text });
const end = (state: "idle" | "pending" | "error" | "cancelled", over: Partial<DialogHead> = {}) =>
  ev("turn.end", { state, stop_reason: state === "idle" ? "end_turn" : null, dialog: head({ state: state === "cancelled" ? "idle" : state, ...over }) });
const dataTool = (id: string, name: string, title: string, args: Record<string, unknown> = {}): ToolItem => ({
  kind: "tool",
  id,
  at: T0,
  tool_use_id: `toolu_${id}`,
  name,
  side: "data",
  title,
  args,
  status: "waiting",
  summary: "",
});
const texts = (s: ChatState): string[] => s.items.map((i) => (i.kind === "assistant" || i.kind === "user" ? i.text : `${i.kind}:${i.kind === "tool" ? i.status : ""}`));

describe("stream_text", () => {
  const events = seg(
    start(user("Дай сводку по проекту")),
    ev("thinking", { state: "start" }),
    ev("thinking", { state: "end" }),
    delta("a1", "В проекте "),
    delta("a1", "**48** скважин, "),
    delta("a1", "Au 1,25 г/т."),
    ev("usage", {
      call: { model: "claude-opus-5-5", input: 1200, output: 80, cache_read: 0, cache_write: 900, cost_usd: 0.0123, long: false },
      dialog: { cost_usd: 0.0123, tokens: { input: 1200, output: 80, cache_read: 0, cache_write: 900 }, questions: 1 },
    }),
    end("idle", { cost_usd: 0.0123, questions: 1 }),
  );

  it("fills the feed and ends idle", () => {
    const s = applyEvents(addLocalUser(EMPTY_CHAT, "Дай сводку по проекту", "1"), events);
    expect(s.items).toHaveLength(2);
    expect(s.items[0]).toEqual(user("Дай сводку по проекту"));
    const a = s.items[1] as AssistantItem;
    expect(a).toMatchObject({ kind: "assistant", id: "a1", model: "claude-opus-5-5", text: "В проекте **48** скважин, Au 1,25 г/т.", stopped: false, interrupted: false });
    expect(s.status).toBe("idle");
    expect(s.thinking).toBe(false);
    expect(s.pending).toBeNull();
    expect(s.usage?.call.cost_usd).toBe(0.0123);
    expect(s.dialog?.cost_usd).toBe(0.0123);
    expect(s.stopReason).toBe("end_turn");
  });

  it("the question shown at once is replaced by the one of the core, not doubled", () => {
    const local = addLocalUser(EMPTY_CHAT, "Дай сводку по проекту", "7");
    expect(local.items[0].id).toBe("local-7");
    expect(isRunning(local)).toBe(true);
    const s = applyEvents(local, events.slice(0, 1));
    expect(s.items).toHaveLength(1);
    expect(s.items[0].id).toBe("u1");
    expect(s.status).toBe("running");
  });

  it("thinking comes and goes; the first text ends it", () => {
    let s = applyEvents(EMPTY_CHAT, seg(start(), ev("thinking", { state: "start" })));
    expect(s.thinking).toBe(true);
    s = applyEvents(s, [delta("a1", "Да")]);
    expect(s.thinking).toBe(false);
  });

  it("a gap of the cost stays a gap", () => {
    const s = applyEvents(
      EMPTY_CHAT,
      seg(start(), ev("usage", { call: { model: "x-new", input: 1, output: 1, cache_read: 0, cache_write: 0, cost_usd: null, long: false }, dialog: { cost_usd: null, tokens: ZERO, questions: 1 } })),
    );
    expect(s.usage?.call.cost_usd).toBeNull();
    expect(s.dialog?.cost_usd).toBeNull();
  });
});

describe("stream_data_tools", () => {
  it("cards of the data tools go waiting → ok / error, the text goes after them", () => {
    const s = applyEvents(
      EMPTY_CHAT,
      seg(
        start(user("Есть ли находки аудита?")),
        ev("tool.start", { item: dataTool("t1", "data_audit", "Итог аудита") }),
        ev("tool.start", { item: dataTool("t2", "data_findings", "Находки аудита", { severity: "E" }) }),
        ev("tool.end", { item_id: "t1", status: "ok", summary: "Ошибок 3, предупреждений 12" }),
        ev("tool.end", { item_id: "t2", status: "error", summary: "Нет активного набора скважин: укажите set_id" }),
        delta("a1", "Есть 3 ошибки."),
        end("idle"),
      ),
    );
    expect(s.items.map((i) => i.kind)).toEqual(["user", "tool", "tool", "assistant"]);
    expect(s.items[1]).toMatchObject({ id: "t1", status: "ok", summary: "Ошибок 3, предупреждений 12", title: "Итог аудита" });
    expect(s.items[2]).toMatchObject({ id: "t2", status: "error", args: { severity: "E" } });
  });

  it("a tool.end of an unknown card is ignored", () => {
    const s = applyEvents(EMPTY_CHAT, seg(start(), ev("tool.end", { item_id: "nope", status: "ok", summary: "" })));
    expect(s.items).toHaveLength(0);
  });
});

describe("stream_pending, stream_after_results", () => {
  const pendingEv = ev("pending", {
    id: "p-1",
    created_at: T0,
    tools: [
      { tool_use_id: "toolu_a", name: "ui_hole_select", command: "hole.select", args: { hole_id: "DH-012" }, item_id: "c1" },
      { tool_use_id: "toolu_b", name: "ui_section_through_hole", command: "section.through_hole", args: { hole_id: "DH-012" }, item_id: "c2" },
    ],
  });
  const first = (): ChatState =>
    applyEvents(
      EMPTY_CHAT,
      seg(start(user("Покажи скважину DH-012 на разрезе")), delta("a1", "Выбираю скважину и строю разрез."), pendingEv, ev("usage", { call: { model: "claude-opus-5-5", input: 1, output: 1, cache_read: 0, cache_write: 0, cost_usd: 0.001, long: false }, dialog: { cost_usd: 0.001, tokens: ZERO, questions: 1 } }), end("pending")),
      { titleOf: (t) => (t.command === "hole.select" ? "Выбрать скважину" : null) },
    );

  it("makes a waiting card of each command of the window, in order; pending is kept for pending.ts", () => {
    const s = first();
    expect(s.status).toBe("pending");
    expect(s.pending?.id).toBe("p-1");
    const cards = s.items.filter((i): i is ToolItem => i.kind === "tool");
    expect(cards.map((c) => [c.id, c.side, c.status, c.title])).toEqual([
      ["c1", "ui", "waiting", "Выбрать скважину"],
      ["c2", "ui", "waiting", "section.through_hole"],
    ]);
    expect(cards[0].args).toEqual({ hole_id: "DH-012" });
    expect(s.dialog?.state).toBe("pending");
  });

  it("the cards change locally, the next segment clears pending and goes on in the same feed", () => {
    let s = first();
    s = setToolResult(s, "c1", "ok", "Выбрана скважина DH-012");
    s = setToolResult(s, "c2", "error", "Нет разреза");
    expect(s.items.filter((i) => i.kind === "tool").map((i) => (i as ToolItem).status)).toEqual(["ok", "error"]);
    s = applyEvents(s, seg(ev("turn.start", { dialog: head(), segment_id: "s-2", model: "claude-opus-5-5", effort: "medium" }, 1), delta("a2", "Готово."), end("idle")));
    expect(s.pending).toBeNull();
    expect(s.status).toBe("idle");
    expect(s.items.map((i) => i.kind)).toEqual(["user", "assistant", "tool", "tool", "assistant"]);
    expect(s.items[4]).toMatchObject({ id: "a2", text: "Готово." });
  });

  it("a new question while commands wait: they are declined and pending is dropped", () => {
    const s = dropPending(first(), "Не выполнено: пользователь продолжил диалог");
    expect(s.pending).toBeNull();
    expect(s.items.filter((i) => i.kind === "tool").map((i) => (i as ToolItem).status)).toEqual(["declined", "declined"]);
    expect(dropPending(s)).toBe(s);
  });

  it("a card the user answered is not turned back", () => {
    let s = first();
    s = setToolResult(s, "c1", "ok", "Выбрана");
    s = dropPending(s);
    expect((s.items.find((i) => i.id === "c1") as ToolItem).status).toBe("ok");
    expect((s.items.find((i) => i.id === "c2") as ToolItem).status).toBe("declined");
  });
});

describe("stream_confirm", () => {
  it("a command that needs a confirmation is a card like the others", () => {
    const s = applyEvents(
      EMPTY_CHAT,
      seg(
        start(user("Удали проект")),
        ev("pending", { id: "p-2", created_at: T0, tools: [{ tool_use_id: "toolu_x", name: "ui_project_delete", command: "project.delete", args: {}, item_id: "c9" }] }),
        end("pending"),
      ),
    );
    const s2 = setToolResult(s, "c9", "declined", "Пользователь отклонил действие");
    expect(s2.items[1]).toMatchObject({ kind: "tool", status: "declined", summary: "Пользователь отклонил действие", name: "ui_project_delete" });
  });
});

describe("stream_offline, stream_cancelled", () => {
  it("an error item and turn.end error", () => {
    const s = applyEvents(
      addLocalUser(EMPTY_CHAT, "Привет", "1"),
      seg(
        start(user("Привет")),
        ev("error", { item: { kind: "error", id: "e1", at: T0, code: "assistant_offline", message: "Ассистент недоступен без интернета", hint: "Проверьте подключение к интернету или прокси; история диалогов доступна" } }),
        end("error"),
      ),
    );
    expect(s.status).toBe("error");
    expect(s.items[1]).toMatchObject({ kind: "error", code: "assistant_offline", hint: expect.stringContaining("история диалогов") });
  });

  it("a lost connection marks the partial answer", () => {
    const s = applyEvents(
      EMPTY_CHAT,
      seg(
        start(user("Расскажи")),
        delta("a1", "Начало отве"),
        ev("error", { item: { kind: "error", id: "e1", at: T0, code: "assistant_interrupted", message: "Связь прервалась", hint: "Нажмите «Повторить»" } }),
        end("error"),
      ),
    );
    expect((s.items[1] as AssistantItem).interrupted).toBe(true);
    expect((s.items[1] as AssistantItem).text).toBe("Начало отве");
  });

  it("cancelled: the answer is stopped, nothing keeps spinning", () => {
    const s = applyEvents(
      EMPTY_CHAT,
      seg(
        start(user("Долгий вопрос")),
        ev("tool.start", { item: dataTool("t1", "data_table", "Таблица набора") }),
        delta("a1", "Смотрю…"),
        ev("pending", { id: "p-3", created_at: T0, tools: [{ tool_use_id: "toolu_y", name: "ui_view_top", command: "view.top", args: {}, item_id: "c1" }] }),
        end("cancelled"),
      ),
    );
    expect(s.status).toBe("cancelled");
    expect(s.pending).toBeNull();
    expect((s.items.find((i) => i.id === "a1") as AssistantItem).stopped).toBe(true);
    expect(s.items.find((i) => i.id === "t1")).toMatchObject({ status: "error", summary: "Прервано" });
    expect(s.items.find((i) => i.id === "c1")).toMatchObject({ status: "declined" });
  });

  it("notices join the feed", () => {
    const s = applyEvents(EMPTY_CHAT, seg(start(), ev("notice", { item: { kind: "notice", id: "n1", at: T0, code: "max_tokens", message: "Ответ обрезан: попросите продолжить" } })));
    expect(s.items[0]).toMatchObject({ kind: "notice", code: "max_tokens" });
  });
});

describe("idempotence", () => {
  it("an event with a number that was taken is ignored; a created item is not doubled", () => {
    const evs = seg(start(user("Привет")), delta("a1", "раз "), delta("a1", "два "), delta("a1", "три"));
    let s = applyEvents(EMPTY_CHAT, evs);
    s = applyEvents(s, evs.slice(2));
    expect(texts(s)).toEqual(["Привет", "раз два три"]);
    // the same card twice (a replay of tool.start) is one card
    const t = ev("tool.start", { item: dataTool("t1", "data_tree", "Дерево проекта") }, 5);
    s = applyEvents(s, [t, { ...t, id: 6 }]);
    expect(s.items.filter((i) => i.kind === "tool")).toHaveLength(1);
  });

  it("a batch gives the same state as the events one by one", () => {
    const evs = seg(start(user("Q")), delta("a1", "а"), ev("tool.start", { item: dataTool("t1", "data_tree", "Дерево") }), ev("tool.end", { item_id: "t1", status: "ok", summary: "ok" }), delta("a2", "б"), end("idle"));
    const one = evs.reduce((s, e) => applyEvents(s, [e]), EMPTY_CHAT);
    expect(applyEvents(EMPTY_CHAT, evs)).toEqual(one);
  });

  it("the state it was given is not changed", () => {
    const before = applyEvents(EMPTY_CHAT, seg(start(user("Q")), delta("a1", "а")));
    const snapshot = JSON.stringify(before);
    applyEvents(before, [delta("a1", "бв"), ev("tool.start", { item: dataTool("t1", "data_tree", "Дерево") }), end("idle")]);
    expect(JSON.stringify(before)).toBe(snapshot);
  });
});

describe("2000 pieces of text do not rebuild the feed", () => {
  const history: Item[] = Array.from({ length: 300 }, (_, i) =>
    i % 2 === 0 ? user(`вопрос ${i}`, `u${i}`) : ({ kind: "assistant", id: `a${i}`, at: T0, model: "m", text: `ответ ${i}`, stopped: false, interrupted: false } as Item),
  );
  const base: ChatState = { ...EMPTY_CHAT, dialog: head(), items: history, status: "running" };

  it("one by one: only the item that grows is replaced, the others keep their identity", () => {
    let s = applyEvents(base, seg(start()));
    const items0 = s.items;
    const t = performance.now();
    for (let i = 0; i < 2000; i++) s = applyEvents(s, [ev("text.delta", { item_id: "new", text: "слово " }, i + 2)]);
    const ms = performance.now() - t;
    expect(s.items).toHaveLength(301);
    expect((s.items[300] as AssistantItem).text).toBe("слово ".repeat(2000));
    for (let i = 0; i < 300; i++) expect(s.items[i], `item ${i}`).toBe(items0[i]);
    expect(ms).toBeLessThan(500);
  });

  it("a batch copies the feed once, whatever its size", () => {
    const s0 = applyEvents(base, seg(start()));
    const batch = Array.from({ length: 2000 }, (_, i) => ev("text.delta", { item_id: "new", text: "x" }, i + 2));
    const t = performance.now();
    const s = applyEvents(s0, batch);
    const ms = performance.now() - t;
    expect((s.items[300] as AssistantItem).text).toHaveLength(2000);
    for (let i = 0; i < 300; i++) expect(s.items[i]).toBe(s0.items[i]);
    expect(ms).toBeLessThan(100);
  });

  it("a delta of an old answer replaces that one item", () => {
    const s0 = applyEvents(base, seg(start()));
    const s = applyEvents(s0, [ev("text.delta", { item_id: "a1", text: "!" }, 2)]);
    expect((s.items[1] as AssistantItem).text).toBe("ответ 1!");
    expect(s.items[0]).toBe(s0.items[0]);
    expect(s.items[2]).toBe(s0.items[2]);
  });
});

describe("a dialog read from the core", () => {
  const dialog = (state: DialogHead["state"], pending: Dialog["pending"] = null): Dialog => ({ head: head({ state }), items: [user("Q"), dataTool("t1", "data_tree", "Дерево")], pending, note: "Диалог не сохранится: проект не открыт" });

  it("idle, running and pending", () => {
    expect(fromDialog(dialog("idle"))).toMatchObject({ status: "idle", pending: null, note: "Диалог не сохранится: проект не открыт", usage: null });
    expect(fromDialog(dialog("running")).status).toBe("running");
    const p = { id: "p-9", created_at: T0, tools: [] };
    const s = fromDialog(dialog("pending", p));
    expect(s.status).toBe("pending");
    expect(s.pending).toBe(p);
    expect(s.items).toHaveLength(2);
    expect(fromDialog(dialog("error")).status).toBe("error");
  });

  it("the feed is a copy: the answer of the core is not changed by the stream", () => {
    const d = dialog("running");
    const s = applyEvents(fromDialog(d), seg(ev("text.delta", { item_id: "a1", text: "x" }, 2)));
    expect(d.items).toHaveLength(2);
    expect(s.items).toHaveLength(3);
  });
});
