// The panel «Claude» (Э23, 23.14; ТЗ 7.32 «Панель чата»; ADR 0029 решение 33; docs/progress/e23-api.md §2, §7, §13): the stream of a turn on the screen (the deltas are
// one answer), the cards of `data_*`, the states («не подключён», «нет связи» with a readable history, «временный диалог»), the quick requests, the input
// (Enter, Shift+Enter, «Стоп» → `stopTurn`). The events are those of the contract written by hand (`reduce.test.ts`), the feed is made by the reducer of 23.13.
import { createElement, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { EMPTY_CONTEXT } from "../../src/shell/commands";
import { CommandRegistry } from "../../src/shell/registry";
import { INITIAL_STATE, Store, type AppState } from "../../src/shell/store";
import { setCoreTarget } from "../../src/shell/api";
import { Composer, composerKey, ComposerView, rowsOf, type ComposerViewProps } from "../../src/modules/assistant/Composer";
import { ChatPanel, ChatView, chatActions, dialogMeta, modelLine, panelState, OFFLINE_TEXT, type ChatViewProps } from "../../src/modules/assistant/ChatPanel";
import { quickItems } from "../../src/modules/assistant/QuickPrompts";
import { applyEvents, EMPTY_CHAT, fromDialog, type ChatState } from "../../src/modules/assistant/reduce";
import * as state from "../../src/modules/assistant/state";
import type { AssistantStatus, Dialog, DialogHead, Item, ToolItem, TurnEnvelope } from "../../src/modules/assistant/wireTypes";

// `useSyncExternalStore` of the shell hooks has no server snapshot: the string renderer reads the stores once instead
vi.mock("../../src/shell/useStore", () => ({
  useStore: (store: { get: () => unknown }, select: (s: unknown) => unknown) => select(store.get()),
  useRegistryVersion: () => 0,
}));

// `stopTurn` and `ask` are spies that keep the rest of the module; `useState` is stubbed only while a test calls `Composer` as a function (`hooks.on`)
const hooks = vi.hoisted(() => ({ on: false, values: [] as unknown[], set: (() => undefined) as (v: unknown) => void }));
vi.mock("react", async (orig) => {
  const r = await orig<typeof import("react")>();
  return {
    ...r,
    useState: ((init: unknown) => (hooks.on ? [hooks.values.length ? hooks.values.shift() : init, hooks.set] : r.useState(init as never))) as typeof r.useState,
  };
});
vi.mock("../../src/modules/assistant/state", async (orig) => {
  const m = await orig<typeof import("../../src/modules/assistant/state")>();
  return { ...m, stopTurn: vi.fn(async () => undefined), ask: vi.fn(async () => ({ ok: true as const })) };
});

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
const ev = <E extends TurnEnvelope["event"]>(event: E, data: Extract<TurnEnvelope, { event: E }>["data"]): TurnEnvelope => ({ event, data, id: null }) as TurnEnvelope;
const seg = (...evs: TurnEnvelope[]): TurnEnvelope[] => evs.map((e, i) => ({ ...e, id: i + 1 }) as TurnEnvelope);
const user = (text: string): Item => ({ kind: "user", id: "u1", at: T0, text });
const start = () => ev("turn.start", { dialog: head(), segment_id: "s-1", model: "claude-opus-5-5", effort: "medium", user_item: user("Дай сводку") as never });
const delta = (text: string) => ev("text.delta", { item_id: "a1", text });
const end = (over: Partial<DialogHead> = {}) => ev("turn.end", { state: "idle", stop_reason: "end_turn", dialog: head({ state: "idle", ...over }) });

const STATUS = (over: Partial<AssistantStatus> = {}): AssistantStatus => ({
  state: "connected",
  kind: "api_key",
  mask: "sk-ant-…a1B2",
  organization: null,
  workspace: null,
  online: true,
  checked_at: T0,
  model: "claude-opus-5-5",
  model_source: "default",
  effort: "medium",
  effort_source: "default",
  ant_available: false,
  secure_storage: true,
  seal_needed: false,
  subscription_note: "Подписка Claude (Pro, Max, Team) панелью не используется: нужен ключ API или вход аккаунтом Anthropic Console.",
  models: [{ id: "claude-opus-5-5", title: "Opus 5.5", default: true, note: "", price: { input: 5, output: 25, cache_read: 0.5, cache_write: 6.25 }, long: null }],
  efforts: [{ id: "medium", title: "Средняя", default: true }],
  prices_version: "2026-11",
  ...over,
});

const WITH_HOLE = { ...EMPTY_CONTEXT, hasProject: true, hasHole: true, coreOnline: true };
const ACTIONS = () => ({ connect: vi.fn(), newDialog: vi.fn(), history: vi.fn(), quick: vi.fn() });
const NO_REASONS = { connect: null, newDialog: null, history: null };

function props(chat: ChatState, over: Partial<ChatViewProps> = {}): ChatViewProps {
  return { chat, status: STATUS(), statusError: null, quick: quickItems(WITH_HOLE, null, null), reasons: NO_REASONS, actions: ACTIONS(), ...over };
}
const draw = (p: ChatViewProps): string => renderToStaticMarkup(createElement(ChatView, p));

type El = ReactElement<{ children?: ReactNode; onClick?: () => void; onKeyDown?: (e: unknown) => void; "data-testid"?: string }>;
const PURE = new Set(["Banners", "HeadButton"]);
/** Every element of a tree; the components of this panel that have no hooks are called, the rest stay elements. */
function all(node: ReactNode, out: El[] = []): El[] {
  if (Array.isArray(node)) node.forEach((n) => all(n, out));
  else if (isValidElement(node)) {
    const el = node as El;
    out.push(el);
    if (typeof el.type === "function" && PURE.has(el.type.name)) all((el.type as (p: unknown) => ReactNode)(el.props), out);
    else all(el.props.children, out);
  }
  return out;
}
const byId = (node: ReactNode, id: string): El[] => all(node).filter((e) => e.props["data-testid"] === id);

describe("the stream of a turn on the screen", () => {
  it("the deltas are one answer; the answer is streaming until the turn ends", () => {
    const live = applyEvents(EMPTY_CHAT, seg(start(), delta("Проект "), delta("содержит **48** "), delta("скважин.")));
    const out = draw(props(live));
    expect(out).toContain("Дай сводку");
    expect(out).toContain("Проект содержит <strong>48</strong> скважин.");
    expect(out).toContain('data-streaming="true"');
    expect(out).toContain('aria-busy="true"');
    expect(out).not.toContain("Claude думает…");
    const done = applyEvents(live, [{ ...end({ questions: 1, cost_usd: 0.0412 }), id: 5 } as TurnEnvelope]);
    const out2 = draw(props(done));
    expect(out2).toContain('data-streaming="false"');
    expect(out2).toContain("Проект содержит <strong>48</strong> скважин.");
    expect(out2).toContain("Вопросов: 1 · ≈ $0,0412");
  });

  it("after the question, before the first word, Claude thinks", () => {
    const s = applyEvents(EMPTY_CHAT, seg(start()));
    expect(draw(props(s))).toContain("Claude думает…");
    const t = applyEvents(EMPTY_CHAT, seg(start(), ev("thinking", { state: "start" })));
    expect(draw(props(t))).toContain("Claude думает…");
  });

  it("the cards of data_* between the question and the answer, with the title of the core", () => {
    const card: ToolItem = { kind: "tool", id: "t1", at: T0, tool_use_id: "toolu_1", name: "data_summary", side: "data", title: "Сводка проекта", args: { set_id: 3 }, status: "waiting", summary: "" };
    const running = applyEvents(EMPTY_CHAT, seg(start(), ev("tool.start", { item: card })));
    expect(draw(props(running))).toContain("Выполняется…");
    const finished = applyEvents(running, [{ ...ev("tool.end", { item_id: "t1", status: "ok", summary: '{"holes":48}' }), id: 3 } as TurnEnvelope, { ...delta("Готово."), id: 4 } as TurnEnvelope]);
    const out = draw(props(finished));
    expect(out).toContain("Сводка проекта");
    expect(out).toContain('data-status="ok"');
    expect(out).toContain("Получено: 12 зн.");
    expect(out.indexOf("Сводка проекта")).toBeLessThan(out.indexOf("Готово."));
  });

  it("the text of an answer with HTML stays text in the panel", () => {
    const s = applyEvents(EMPTY_CHAT, seg(start(), delta('<img src=x onerror="alert(1)"> и <script>alert(2)</script>'), end()));
    const out = draw(props(s));
    expect(out).not.toContain("<img");
    expect(out).not.toContain("<script");
  });
});

describe("the states", () => {
  it("the status of the connection → the state of the panel (ТЗ 6.10)", () => {
    expect(panelState(null, null)).toMatchObject({ mode: "loading" });
    expect(panelState(null, "Ядро недоступно.")).toEqual({ mode: "unreachable", blocked: "Ядро недоступно." });
    expect(panelState(STATUS({ state: "none", kind: null, mask: null }), null).mode).toBe("none");
    expect(panelState(STATUS({ state: "login" }), null).mode).toBe("login");
    expect(panelState(STATUS({ online: false }), null)).toEqual({ mode: "offline", blocked: "Ассистент недоступен без интернета" });
    expect(panelState(STATUS({ online: null }), null)).toEqual({ mode: "ready", blocked: null });
    expect(panelState(STATUS(), null)).toEqual({ mode: "ready", blocked: null });
  });

  it("not connected: «Подключить Claude» runs assistant.connect, nothing can be asked", () => {
    const p = props(EMPTY_CHAT, { status: STATUS({ state: "none", kind: null, mask: null }) });
    const out = draw(p);
    expect(out).toContain("Claude не подключён");
    expect(out).toContain("Подключить Claude");
    expect(out).toContain("Подписка Claude (Pro, Max, Team) панелью не используется");
    expect(out).not.toContain('data-testid="quick-prompts"');
    expect(out).toMatch(/<textarea[^>]*disabled/);
    byId(ChatView(p), "connect")[0].props.onClick?.();
    expect(p.actions.connect).toHaveBeenCalledTimes(1);
  });

  it("no internet: the text of ТЗ 6.10, the history is read, nothing is asked", () => {
    const d: Dialog = { head: head({ state: "idle", questions: 1 }), items: [user("Дай сводку"), { kind: "assistant", id: "a1", at: T0, model: "claude-opus-5-5", text: "Ответ из истории.", stopped: false, interrupted: false }], pending: null, note: null };
    const out = draw(props(fromDialog(d), { status: STATUS({ online: false }) }));
    expect(out).toContain(OFFLINE_TEXT);
    expect(OFFLINE_TEXT).toBe("Ассистент недоступен без интернета");
    expect(out).toContain("Ответ из истории.");
    expect(out).toContain("Дай сводку");
    expect(out).toMatch(/<textarea[^>]*disabled/);
    expect(out).toContain("Ассистент недоступен без интернета</div>");
  });

  it("an empty dialog of a connected panel: the three quick requests that run the command", () => {
    const p = props(EMPTY_CHAT);
    const out = draw(p);
    expect(out).toContain('data-testid="chat-empty"');
    expect(out.match(/data-testid="quick-prompt"/g)).toHaveLength(3);
    expect(draw(props(applyEvents(EMPTY_CHAT, seg(start(), delta("x")))))).not.toContain('data-testid="quick-prompts"');
  });

  it("a temporary dialog: the mark and the reason of the core", () => {
    const d: Dialog = { head: head({ state: "idle", temporary: true, id: "t-1" }), items: [user("Привет")], pending: null, note: "Диалог не сохранится: проект не открыт" };
    const out = draw(props(fromDialog(d)));
    expect(out).toContain("Временный диалог.");
    expect(out).toContain("Диалог не сохранится: проект не открыт");
    expect(draw(props(EMPTY_CHAT))).not.toContain("Временный диалог");
  });

  it("the head: the model and the depth, «Новый диалог» and «История» are commands", () => {
    expect(modelLine(STATUS())).toBe("Opus 5.5 · Средняя");
    expect(modelLine(STATUS({ model: "claude-x", effort: "high" }))).toBe("claude-x · high");
    expect(modelLine(null)).toBe("Модель не определена");
    const p = props(EMPTY_CHAT);
    const out = draw(p);
    expect(out).toContain("Opus 5.5 · Средняя");
    expect(out).toContain("Новый диалог");
    expect(out).toContain("История");
    byId(ChatView(p), "chat-new")[0].props.onClick?.();
    byId(ChatView(p), "chat-history")[0].props.onClick?.();
    expect(p.actions.newDialog).toHaveBeenCalledTimes(1);
    expect(p.actions.history).toHaveBeenCalledTimes(1);
    const off = props(EMPTY_CHAT, { reasons: { connect: null, newDialog: "Функция появится на этапе Э23", history: null } });
    byId(ChatView(off), "chat-new")[0].props.onClick?.();
    expect(off.actions.newDialog).not.toHaveBeenCalled();
    expect(draw(off)).toContain("Недоступно: Функция появится на этапе Э23");
  });

  it("the price of an unknown model is not 0", () => {
    expect(dialogMeta(head({ questions: 2, cost_usd: null }))).toBe("Вопросов: 2 · стоимость не определена");
    expect(dialogMeta(head({ questions: 2, cost_usd: 1.5 }))).toBe("Вопросов: 2 · ≈ $1,50");
    expect(dialogMeta(head({ questions: 0 }))).toBeNull();
    expect(dialogMeta(null)).toBeNull();
  });

  it("the slots of 23.15: the buttons of a waiting card and what follows the feed", () => {
    const card: ToolItem = { kind: "tool", id: "t9", at: T0, tool_use_id: "toolu_9", name: "ui_hole_select", side: "ui", title: "Выбрать скважину", args: {}, status: "waiting", summary: "" };
    const s = applyEvents(EMPTY_CHAT, seg(start(), ev("tool.start", { item: card })));
    const out = draw(props(s, { renderToolActions: (it) => createElement("button", { type: "button" }, `Выполнить ${it.title}`), tail: createElement("div", null, "Подтвердите действие") }));
    expect(out).toContain("Выполнить Выбрать скважину");
    expect(out).toContain("Подтвердите действие");
  });
});

describe("the input", () => {
  const cprops = (over: Partial<ComposerViewProps> = {}): ComposerViewProps => ({ value: "Вопрос", onChange: vi.fn(), onSend: vi.fn(), onStop: vi.fn(), running: false, blocked: null, ...over });
  const key = (k: string, extra: Record<string, unknown> = {}) => ({ key: k, shiftKey: false, ctrlKey: false, altKey: false, metaKey: false, nativeEvent: { isComposing: false }, preventDefault: vi.fn(), ...extra });

  it("Enter sends, Shift+Enter is a new line, an input method that composes is left alone", () => {
    expect(composerKey({ key: "Enter" })).toBe("send");
    expect(composerKey({ key: "Enter", shiftKey: true })).toBe("none");
    expect(composerKey({ key: "Enter", isComposing: true })).toBe("none");
    expect(composerKey({ key: "a" })).toBe("none");
    const p = cprops();
    const area = byId(ComposerView(p), "composer-input")[0];
    const enter = key("Enter");
    area.props.onKeyDown?.(enter);
    expect(p.onSend).toHaveBeenCalledTimes(1);
    expect(enter.preventDefault).toHaveBeenCalled();
    const shift = key("Enter", { shiftKey: true });
    area.props.onKeyDown?.(shift);
    expect(p.onSend).toHaveBeenCalledTimes(1);
    expect(shift.preventDefault).not.toHaveBeenCalled();
  });

  it("an empty question, a blocked panel and a running turn do not send", () => {
    for (const over of [{ value: "  \n " }, { blocked: "Ассистент недоступен без интернета" }, { running: true }]) {
      const p = cprops(over);
      byId(ComposerView(p), "composer-input")[0].props.onKeyDown?.(key("Enter"));
      expect(p.onSend, JSON.stringify(over)).not.toHaveBeenCalled();
    }
  });

  it("during a turn «Стоп» takes the place of «Отправить»", () => {
    const p = cprops({ running: true });
    const tree = ComposerView(p);
    expect(byId(tree, "composer-send")).toHaveLength(0);
    byId(tree, "composer-stop")[0].props.onClick?.();
    expect(p.onStop).toHaveBeenCalledTimes(1);
    const idle = renderToStaticMarkup(createElement(ComposerView, cprops()));
    expect(idle).toContain("Отправить");
    expect(idle).not.toContain("Стоп");
    expect(renderToStaticMarkup(createElement(ComposerView, cprops({ running: true })))).toContain("Стоп");
  });

  it("the field is as long as the text, within limits; the reason is the placeholder of a blocked field", () => {
    expect(rowsOf("")).toBe(2);
    expect(rowsOf("a\nb\nc\nd")).toBe(4);
    expect(rowsOf("\n".repeat(50))).toBe(8);
    const out = renderToStaticMarkup(createElement(ComposerView, cprops({ value: "", blocked: "Claude не подключён" })));
    expect(out).toContain('placeholder="Claude не подключён"');
    expect(out).toMatch(/<textarea[^>]*disabled/);
    expect(out).toContain('maxLength="20000"');
  });

  describe("connected to the state of the assistant", () => {
    beforeEach(() => {
      hooks.on = true;
      hooks.set = vi.fn();
      vi.mocked(state.stopTurn).mockClear();
      vi.mocked(state.ask).mockClear();
    });
    afterEach(() => {
      hooks.on = false;
    });

    it("«Стоп» calls stopTurn", () => {
      hooks.values = ["", null];
      const el = Composer({ running: true, blocked: null }) as ReactElement<ComposerViewProps>;
      el.props.onStop();
      expect(state.stopTurn).toHaveBeenCalledTimes(1);
    });

    it("sending asks the question trimmed and clears the field", async () => {
      hooks.values = ["  Дай сводку \n", null];
      const el = Composer({ running: false, blocked: null }) as ReactElement<ComposerViewProps>;
      el.props.onSend();
      await vi.waitFor(() => expect(hooks.set).toHaveBeenCalledWith(""));
      expect(state.ask).toHaveBeenCalledWith("Дай сводку");
    });

    it("a refusal of ask keeps the text and shows the reason", async () => {
      vi.mocked(state.ask).mockResolvedValueOnce({ ok: false, message: "Ответ ещё идёт", hint: "Нажмите «Стоп»." });
      hooks.values = ["Вопрос", null];
      const el = Composer({ running: false, blocked: null }) as ReactElement<ComposerViewProps>;
      el.props.onSend();
      await vi.waitFor(() => expect(hooks.set).toHaveBeenCalledWith({ message: "Ответ ещё идёт", hint: "Нажмите «Стоп»." }));
      expect(hooks.set).not.toHaveBeenCalledWith("");
    });
  });
});

describe("connected to the registry", () => {
  it("the buttons of the panel are the commands of the registry", () => {
    const run = vi.fn(async () => undefined);
    const a = chatActions({ run });
    a.connect();
    a.newDialog();
    a.history();
    a.quick("Дай сводку");
    expect(run.mock.calls).toEqual([["assistant.connect"], ["assistant.new"], ["assistant.history"], ["assistant.ask", { prompt: "Дай сводку" }]]);
  });

  function app(): App {
    const registry = new CommandRegistry();
    registry.register([{ id: "assistant.ask", title: "Спросить Claude", description: "d", params: { prompt: { type: "string" } }, required: ["prompt"], silent: true, run: () => undefined }]);
    registry.hooks.context = () => ({ ...EMPTY_CONTEXT, hasHole: true });
    return { registry, store: new Store<AppState>({ ...INITIAL_STATE, holeId: "DH-012", core: "online" }), run: async () => undefined } as unknown as App;
  }

  beforeEach(() => {
    setCoreTarget({ baseUrl: "http://127.0.0.1:5555", token: "tok" });
    state.newDialog();
  });
  afterEach(() => {
    state.statusStore.set({ status: null, error: null });
    state.newDialog();
    setCoreTarget(null);
  });

  it("the panel in the dock follows the stores of the assistant", () => {
    const draw2 = () => renderToStaticMarkup(createElement(AppContext.Provider, { value: app() }, createElement(ChatPanel)));
    state.statusStore.set({ status: STATUS({ state: "none", kind: null, mask: null }), error: null });
    expect(draw2()).toContain("Подключить Claude");
    state.statusStore.set({ status: STATUS(), error: null });
    const ready = draw2();
    expect(ready).toContain('data-mode="ready"');
    expect(ready).toContain("Opus 5.5 · Средняя");
    expect(ready).toContain('data-testid="chat-empty"');
    state.chatStore.set(applyEvents(EMPTY_CHAT, seg(start(), delta("Привет!"))));
    expect(draw2()).toContain("Привет!");
    state.statusStore.set({ status: STATUS({ online: false }), error: null });
    expect(draw2()).toContain("Ассистент недоступен без интернета");
    expect(draw2()).toContain("Привет!");
  });
});
