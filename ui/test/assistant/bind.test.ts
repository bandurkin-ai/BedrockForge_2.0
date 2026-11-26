// The assistant joined to the application (Э23, 23.15; ADR 0029 решения 22, 26, 35): `bindAssistant` binds the commands `assistant.*`, replaces the confirmation of the registry by the
// card of the chat, and tells the chat what a request carries (the context, the tools of the registry — a command added to the registry is a tool with no other change).
// The routes of the core are a stub of `fetch`; nothing reaches a network.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setCoreTarget } from "../../src/shell/api";
import { createApp, type App } from "../../src/shell/app";
import { DECLINED } from "../../src/shell/registry";
import type { Command } from "../../src/shell/commands";
import { FROM_CLAUDE, NO_WINDOW, bindAssistant, askUnavailable, showPanel } from "../../src/modules/assistant/bind";
import { CONNECT_SECTIONS, commands as assistantCommands } from "../../src/modules/assistant/commands";
import { answerConfirm, confirmStore, declineAllConfirms } from "../../src/modules/assistant/confirm";
import { pendingActive } from "../../src/modules/assistant/pending";
import { chatStore, dialogsStore, newDialog, statusStore } from "../../src/modules/assistant/state";
import type { AssistantStatus, Dialog, DialogHead, ToolDefinition } from "../../src/modules/assistant/wireTypes";

const T0 = "2026-11-26T10:00:00Z";
const HEAD = (over: Partial<DialogHead> = {}): DialogHead => ({
  id: "41",
  uid: "d-1",
  title: "Сводка по проекту",
  created_at: T0,
  last_at: T0,
  questions: 1,
  cost_usd: null,
  tokens: { input: 0, output: 0, cache_read: 0, cache_write: 0 },
  models: [],
  state: "idle",
  temporary: false,
  project_uid: null,
  ...over,
});
const enc = new TextEncoder();
const frame = (id: number, event: string, data: unknown): string => `id: ${id}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const stream = (...frames: string[]): Response => new Response(new ReadableStream<Uint8Array>({ start(c) { c.enqueue(enc.encode(frames.join(""))); c.close(); } }), { status: 200 });
const json = (body: unknown, status = 200): Response => new Response(JSON.stringify(body), { status });

interface Call {
  method: string;
  path: string;
  body: Record<string, unknown> | undefined;
}
const calls: Call[] = [];
let route: (c: Call) => Response = () => json({});

function stubCore(): void {
  calls.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const c: Call = { method: init.method ?? "GET", path: String(url).replace("http://127.0.0.1:5555/api/v1/assistant", ""), body: init.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined };
      calls.push(c);
      return route(c);
    }),
  );
}

const endTurn = (): Response =>
  stream(
    frame(1, "turn.start", { dialog: HEAD({ state: "running" }), segment_id: "s", model: "m", effort: "low", user_item: { kind: "user", id: "u1", at: T0, text: "Привет" } }),
    frame(2, "turn.end", { state: "idle", stop_reason: "end_turn", dialog: HEAD() }),
  );

let app: App;
let dispose: () => void;
let originalConfirm: App["registry"]["hooks"]["confirm"];
const opened: [string, Record<string, unknown> | undefined][] = [];

beforeEach(() => {
  setCoreTarget({ baseUrl: "http://127.0.0.1:5555", token: "tok" });
  stubCore();
  route = (c) => (c.path.startsWith("/dialogs") && c.method === "GET" ? json({ total: 0, items: [] }) : c.path === "/turns" ? endTurn() : json({}));
  app = createApp({ persist: false });
  app.store.set({ core: "online" });
  originalConfirm = app.registry.hooks.confirm;
  opened.length = 0;
  newDialog();
  statusStore.set({ status: null, error: null });
  dispose = bindAssistant(app, { openWindow: (id, props) => void opened.push([id, props]) });
});
afterEach(() => {
  dispose();
  declineAllConfirms();
  vi.unstubAllGlobals();
  setCoreTarget(null);
  statusStore.set({ status: null, error: null });
});

const statusOf = (over: Partial<AssistantStatus>): AssistantStatus => ({ state: "connected", online: true, ...over }) as AssistantStatus;
const panelOpen = () => app.store.get().workspace.right && app.store.get().workspace.rightTab === "assistant";

describe("the commands are bound", () => {
  it("all eight are bound once the core is up (the stage mark of the stubs is gone; `stop` waits for an answer to stop)", () => {
    for (const c of assistantCommands) expect(app.registry.reason(c.id), c.id).toBe(c.id === "assistant.stop" ? "Ответ не идёт" : null);
  });

  it("without the core they say why", () => {
    app.store.set({ core: "offline" });
    for (const id of ["assistant.ask", "assistant.connect", "assistant.history", "assistant.open", "assistant.rename", "assistant.delete"]) expect(app.registry.reason(id), id).toBe("Ядро не запущено");
    expect(app.registry.reason("assistant.new")).toBeNull();
  });

  it("`assistant.ask` says why a question cannot be asked: not connected, a login, no internet", () => {
    const ok = { coreOnline: true };
    expect(askUnavailable(ok)).toBeNull();
    statusStore.set({ status: statusOf({ state: "none" }) });
    expect(askUnavailable(ok)).toContain("не подключён");
    statusStore.set({ status: statusOf({ state: "login" }) });
    expect(askUnavailable(ok)).toContain("вход");
    statusStore.set({ status: statusOf({ online: false }) });
    expect(askUnavailable(ok)).toBe("Ассистент недоступен без интернета");
    statusStore.set({ status: statusOf({ online: null }) });
    expect(askUnavailable(ok)).toBeNull();
  });
});

describe("assistant.ask", () => {
  it("from Claude is a refusal «Нельзя вызвать из ассистента» and sends nothing", async () => {
    const r = await app.registry.execute("assistant.ask", { prompt: "Привет" }, "claude");
    expect(r).toEqual({ ok: false, result: FROM_CLAUDE });
    expect(FROM_CLAUDE).toBe("Нельзя вызвать из ассистента");
    expect(calls.filter((c) => c.path === "/turns")).toEqual([]);
  });

  it("from the user opens the panel «Claude» and sends the question with the context and the tools of the registry", async () => {
    expect(panelOpen()).toBe(false);
    const r = await app.registry.execute("assistant.ask", { prompt: "  Привет  " }, "user");
    expect(r.ok).toBe(true);
    expect(panelOpen()).toBe(true);
    const turn = calls.find((c) => c.path === "/turns");
    expect(turn?.body?.text).toBe("Привет");
    expect(turn?.body?.dialog_id).toBeNull();
    expect(turn?.body?.context).toMatchObject({ project: null, hole: null, view: { mode: "iso" } });
    const names = (turn?.body?.tools as ToolDefinition[]).map((t) => t.name);
    expect(names).toContain("ui_assistant_ask");
    expect(names).toContain("ui_hole_select");
    expect(names).not.toContain("ui_assistant_new"); // uiOnly
    expect(app.registry.entries().filter((e) => e.commandId === "assistant.ask")).toEqual([]); // silent
  });

  it("a command added to the registry after the binding is a tool of the next question, with no other change", async () => {
    app.registry.register([{ id: "test.later", title: "Позже", description: "Добавлена только в реестр.", run: () => "ok" }]);
    await app.registry.execute("assistant.ask", { prompt: "q" });
    const turn = calls.find((c) => c.path === "/turns");
    expect((turn?.body?.tools as ToolDefinition[]).map((t) => t.name)).toContain("ui_test_later");
  });

  it("an empty question is a Russian refusal, not an exception", async () => {
    const r = await app.registry.execute("assistant.ask", { prompt: "   " }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toMatch(/[А-Яа-яЁё]/);
  });
});

describe("the confirmation of Claude is a card of the chat", () => {
  const drop = (run: Command["run"]): Command => ({ id: "test.drop", title: "Удалить тест", description: "Удаляет тестовые данные.", confirm: true, run, params: { id: { type: "integer" } }, required: ["id"] });

  it("opens the panel «Claude», shows the card, and runs only after «Разрешить»", async () => {
    const run = vi.fn(() => "Удалено");
    app.registry.register([drop(run)]);
    expect(panelOpen()).toBe(false);
    const res = app.registry.execute("test.drop", { id: 5 }, "claude");
    await vi.waitFor(() => expect(confirmStore.get().queue).toHaveLength(1));
    expect(panelOpen()).toBe(true);
    expect(confirmStore.get().queue[0]).toMatchObject({ title: "Удалить тест", args: ["id = 5"] });
    expect(run).not.toHaveBeenCalled();
    answerConfirm(confirmStore.get().queue[0].id, true);
    expect(await res).toEqual({ ok: true, result: "Удалено" });
    expect(app.registry.entries()[0]).toMatchObject({ commandId: "test.drop", source: "claude", ok: true });
  });

  it("«Отклонить» is «Пользователь отклонил действие» and the log says so", async () => {
    const run = vi.fn(() => "Удалено");
    app.registry.register([drop(run)]);
    const res = app.registry.execute("test.drop", { id: 5 }, "claude");
    await vi.waitFor(() => expect(confirmStore.get().queue).toHaveLength(1));
    answerConfirm(confirmStore.get().queue[0].id, false);
    expect(await res).toEqual({ ok: false, result: DECLINED });
    expect(run).not.toHaveBeenCalled();
    expect(app.registry.entries()[0]).toMatchObject({ source: "claude", ok: false, result: DECLINED });
  });

  it("closing the panel while Claude waits is «Отклонить»", async () => {
    app.registry.register([drop(() => "Удалено")]);
    const res = app.registry.execute("test.drop", { id: 5 }, "claude");
    await vi.waitFor(() => expect(confirmStore.get().queue).toHaveLength(1));
    app.store.set((s) => ({ workspace: { ...s.workspace, right: false } }));
    expect((await res).result).toBe(DECLINED);
    expect(confirmStore.get().queue).toEqual([]);
  });

  it("«Новый диалог» is «Отклонить»", async () => {
    app.registry.register([drop(() => "Удалено")]);
    const res = app.registry.execute("test.drop", { id: 5 }, "claude");
    await vi.waitFor(() => expect(confirmStore.get().queue).toHaveLength(1));
    expect((await app.registry.execute("assistant.new", {}, "user")).ok).toBe(true);
    expect((await res).result).toBe(DECLINED);
  });

  it("the user's own call is not asked", async () => {
    const run = vi.fn(() => "Удалено");
    app.registry.register([drop(run)]);
    expect((await app.registry.execute("test.drop", { id: 5 }, "user")).ok).toBe(true);
    expect(confirmStore.get().queue).toEqual([]);
  });
});

describe("assistant.new, assistant.stop", () => {
  it("`new` empties the chat, opens the panel and is not written to the log", async () => {
    chatStore.set({ items: [{ kind: "user", id: "u", at: T0, text: "x" }], dialog: HEAD() });
    const r = await app.registry.execute("assistant.new", {}, "user");
    expect(r).toEqual({ ok: true, result: "Начат новый диалог" });
    expect(chatStore.get().items).toEqual([]);
    expect(chatStore.get().dialog).toBeNull();
    expect(panelOpen()).toBe(true);
    expect(app.registry.entries()).toEqual([]);
  });

  it("`stop` is unavailable when nothing runs; while an answer runs it asks the core to cancel", async () => {
    expect(app.registry.reason("assistant.stop")).toBe("Ответ не идёт");
    chatStore.set({ status: "running", dialog: HEAD({ state: "running" }) });
    expect(app.registry.reason("assistant.stop")).toBeNull();
    const r = await app.registry.execute("assistant.stop", {}, "user");
    expect(r).toEqual({ ok: true, result: "Ответ остановлен" });
    expect(calls.some((c) => c.method === "POST" && c.path === "/dialogs/41/cancel")).toBe(true);
    expect(pendingActive()).toBe(false);
  });

  it("Claude cannot call them (uiOnly)", async () => {
    for (const id of ["assistant.new", "assistant.stop", "assistant.connect", "assistant.history", "assistant.open", "assistant.rename", "assistant.delete"]) {
      expect((await app.registry.execute(id, { dialog_id: "1", title: "t" }, "claude")).result, id).toBe("Команда доступна только из интерфейса");
    }
  });
});

describe("the windows of the assistant", () => {
  it("`connect` and `history` open the windows through the point of the application", async () => {
    expect((await app.registry.execute("assistant.connect", {})).ok).toBe(true);
    expect((await app.registry.execute("assistant.connect", { section: "model" })).ok).toBe(true);
    expect((await app.registry.execute("assistant.history", {})).ok).toBe(true);
    expect(opened).toEqual([["assistant.connect", undefined], ["assistant.connect", { section: "model" }], ["assistant.history", undefined]]);
    expect(CONNECT_SECTIONS).toContain("model");
  });

  it("a section the window does not have is refused with the list", async () => {
    const r = await app.registry.execute("assistant.connect", { section: "nope" });
    expect(r.ok).toBe(false);
    expect(r.result).toContain("допустимые значения");
    expect(opened).toEqual([]);
  });

  it("without the point of the application the answer is «Окно ассистента ещё не готово»", async () => {
    dispose();
    dispose = bindAssistant(app);
    const r = await app.registry.execute("assistant.connect", {});
    expect(r).toEqual({ ok: false, result: NO_WINDOW });
  });
});

describe("the dialogs of the history", () => {
  const dialog = (over: Partial<DialogHead> = {}): Dialog => ({ head: HEAD(over), items: [{ kind: "user", id: "u1", at: T0, text: "Привет" }], pending: null, note: null });

  it("`open` reads the dialog into the chat and shows the panel", async () => {
    route = (c) => (c.path === "/dialogs/41" ? json(dialog()) : json({ total: 0, items: [] }));
    const r = await app.registry.execute("assistant.open", { dialog_id: "41" });
    expect(r).toEqual({ ok: true, result: "Открыт диалог «Сводка по проекту»" });
    expect(chatStore.get().dialog?.id).toBe("41");
    expect(panelOpen()).toBe(true);
  });

  it("`open` of a dialog that is not there is a text, not an exception", async () => {
    route = (c) => (c.path === "/dialogs/99" ? json({ code: "assistant_dialog_not_found", message: "Диалог не найден.", hint: "" }, 404) : json({ total: 0, items: [] }));
    const r = await app.registry.execute("assistant.open", { dialog_id: "99" });
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Диалог не открыт");
  });

  it("`rename` sends the title and renames the open dialog in the chat; an empty title is refused", async () => {
    chatStore.set({ dialog: HEAD() });
    route = (c) => (c.method === "PATCH" ? json(HEAD({ title: "Новое имя" })) : json({ total: 0, items: [] }));
    const r = await app.registry.execute("assistant.rename", { dialog_id: "41", title: "  Новое имя " });
    expect(r).toEqual({ ok: true, result: "Диалог переименован: «Новое имя»" });
    expect(calls.find((c) => c.method === "PATCH")).toMatchObject({ path: "/dialogs/41", body: { title: "Новое имя" } });
    expect(chatStore.get().dialog?.title).toBe("Новое имя");
    const bad = await app.registry.execute("assistant.rename", { dialog_id: "41", title: "   " });
    expect(bad).toEqual({ ok: false, result: "Название диалога не должно быть пустым" });
  });

  it("`delete` removes the dialog; the open one is left for a new chat; the list is read again", async () => {
    chatStore.set({ dialog: HEAD(), items: [{ kind: "user", id: "u", at: T0, text: "x" }] });
    route = (c) => (c.method === "DELETE" ? new Response(null, { status: 204 }) : json({ total: 0, items: [] }));
    const r = await app.registry.execute("assistant.delete", { dialog_id: "41" });
    expect(r).toEqual({ ok: true, result: "Диалог удалён" });
    expect(calls.some((c) => c.method === "DELETE" && c.path === "/dialogs/41")).toBe(true);
    expect(chatStore.get().dialog).toBeNull();
    await vi.waitFor(() => expect(calls.some((c) => c.method === "GET" && c.path.startsWith("/dialogs?"))).toBe(true));
    expect(dialogsStore.get().error).toBeNull();
  });

  it("a refusal of the core is its text with the hint", async () => {
    route = () => json({ code: "assistant_dialog_not_found", message: "Диалог не найден.", hint: "Обновите список." }, 404);
    expect((await app.registry.execute("assistant.delete", { dialog_id: "7" })).result).toBe("Диалог не найден. Обновите список.");
  });
});

describe("showPanel and the undoing", () => {
  it("opens the right dock on «Claude» once and leaves an open one alone", () => {
    showPanel(app);
    expect(panelOpen()).toBe(true);
    const w = app.store.get().workspace;
    showPanel(app);
    expect(app.store.get().workspace).toBe(w);
  });

  it("`dispose` puts the confirmation of the application back", async () => {
    expect(app.registry.hooks.confirm).not.toBe(originalConfirm); // the card of the chat
    dispose();
    expect(app.registry.hooks.confirm).toBe(originalConfirm);
    dispose = bindAssistant(app, {});
  });
});
