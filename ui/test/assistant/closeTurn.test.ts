// The turn that cannot be continued (Э23, 23.20б; ADR 0029 решение 32): `project.delete` of this turn closes it without sending the results; a 404 `assistant_dialog_not_found`
// on the results (a project was opened meanwhile) closes it with another notice. The cards stay «Готово», no raw error, the chat is idle, the list of dialogs is read again.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setCoreTarget } from "../../src/shell/api";
import type { Command, CommandContext } from "../../src/shell/commands";
import { CommandRegistry } from "../../src/shell/registry";
import { installConfirm } from "../../src/modules/assistant/confirm";
import { PendingRunner, liveIo } from "../../src/modules/assistant/pending";
import { ask, chatStore, configureAssistant, newDialog, setPendingHandler } from "../../src/modules/assistant/state";
import type { DialogHead, ToolItem, TurnContext } from "../../src/modules/assistant/wireTypes";

const T0 = "2026-11-27T10:00:00Z";
const HEAD = (state: DialogHead["state"] = "running"): DialogHead => ({
  id: "41",
  uid: "d-1",
  title: "Q",
  created_at: T0,
  last_at: T0,
  questions: 1,
  cost_usd: null,
  tokens: { input: 0, output: 0, cache_read: 0, cache_write: 0 },
  models: [],
  state,
  temporary: false,
  project_uid: "p-1",
});
const CONTEXT: TurnContext = { project: null, set: null, hole: null, panels: [], windows: [], view: { mode: "iso", perspective: true, vexag: 1 }, section: null, selection: null, audit: null };
const ONLINE: CommandContext = { hasProject: true, hasSet: true, hasHole: true, hasObject: false, hasSection: false, editing: false, coreOnline: true };
const frame = (id: number, event: string, data: unknown): string => `id: ${id}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const enc = new TextEncoder();
const answer = (...frames: string[]): Response =>
  new Response(new ReadableStream<Uint8Array>({ start(c) { c.enqueue(enc.encode(frames.join(""))); c.close(); } }), { status: 200 });
const NOT_FOUND = { code: "assistant_dialog_not_found", message: "Диалог не найден.", hint: "Откройте список диалогов и выберите другой." };

const pendingFrames = (name: string, command: string): string[] => [
  frame(1, "turn.start", { dialog: HEAD(), segment_id: "s", model: "m", effort: "low", user_item: { kind: "user", id: "u1", at: T0, text: "Сделай" } }),
  frame(2, "pending", { id: "p-1", created_at: T0, tools: [{ tool_use_id: "toolu_a", name, command, args: {}, item_id: "c1" }] }),
  frame(3, "turn.end", { state: "pending", stop_reason: "tool_use", dialog: HEAD("pending") }),
];

const calls: string[] = [];
function stub(handler: (url: string, n: number) => Response): void {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    calls.push(String(url).replace("http://127.0.0.1:5555/api/v1/assistant", ""));
    return handler(String(url), calls.length - 1);
  }));
}

function registry(): CommandRegistry {
  const r = new CommandRegistry();
  const cmd = (id: string): Command => ({ id, title: `Команда ${id}`, description: `Описание ${id}.`, run: () => `Выполнено ${id}` });
  r.register([cmd("project.delete"), cmd("project.open")]);
  r.hooks = { ...r.hooks, context: () => ONLINE };
  installConfirm(r);
  return r;
}

beforeEach(() => {
  setCoreTarget({ baseUrl: "http://127.0.0.1:5555", token: "tok" });
  newDialog();
  configureAssistant({ context: () => CONTEXT, tools: () => [] });
  const runner = new PendingRunner(registry(), liveIo);
  setPendingHandler((p) => void runner.run(p));
});
afterEach(() => {
  setPendingHandler(null);
  vi.unstubAllGlobals();
  setCoreTarget(null);
  configureAssistant(null);
});

const listOnly = (url: string): Response | null => (url.includes("/dialogs?") || url.endsWith("/dialogs") ? new Response(JSON.stringify({ total: 0, items: [] }), { status: 200 }) : null);
const toolCard = (): ToolItem | undefined => chatStore.get().items.find((i): i is ToolItem => i.id === "c1" && i.kind === "tool");
const hasNotice = (): boolean => chatStore.get().items.some((i) => i.kind === "notice");

describe("closing a turn that cannot be continued", () => {
  it("project.delete ran in this turn: nothing is sent, the card is «Готово», the notice says the dialog is closed with the project", async () => {
    stub((url, n) => listOnly(url) ?? (n === 0 ? answer(...pendingFrames("ui_project_delete", "project.delete")) : new Response("{}", { status: 500 })));
    await ask("Удали проект");
    await vi.waitFor(() => expect(hasNotice()).toBe(true));
    const s = chatStore.get();
    expect(calls.some((c) => c.includes("tool_results"))).toBe(false);
    expect(toolCard()).toMatchObject({ status: "ok", summary: "Выполнено project.delete" });
    expect(s.items.filter((i) => i.kind === "error")).toEqual([]);
    expect(s.items.at(-1)).toMatchObject({ kind: "notice", code: "dialog_closed", message: "Диалог закрыт вместе с проектом: ответ Claude получить нельзя." });
    expect(s).toMatchObject({ status: "idle", pending: null, dialog: null, thinking: false });
    await vi.waitFor(() => expect(calls.filter((c) => c.startsWith("/dialogs")).length).toBeGreaterThan(1)); // the list is read again
  });

  it("another command and a 404 on the results: the notice «Проект сменился», no raw error", async () => {
    stub((url, n) => listOnly(url) ?? (n === 0 ? answer(...pendingFrames("ui_project_open", "project.open")) : new Response(JSON.stringify(NOT_FOUND), { status: 404 })));
    await ask("Открой другой");
    await vi.waitFor(() => expect(hasNotice()).toBe(true));
    const s = chatStore.get();
    expect(calls.some((c) => c.includes("/dialogs/41/tool_results"))).toBe(true);
    expect(toolCard()?.status).toBe("ok");
    expect(s.items.filter((i) => i.kind === "error")).toEqual([]);
    expect(s.items.at(-1)).toMatchObject({ kind: "notice", code: "project_changed", message: "Проект сменился: этот диалог остался в прежнем проекте, продолжите его там." });
    expect(s).toMatchObject({ status: "idle", pending: null, dialog: null });
  });

  it("a 404 on a new question stays an error: only the results of commands close a turn", async () => {
    chatStore.set({ dialog: HEAD("idle"), status: "idle" });
    stub((url) => listOnly(url) ?? new Response(JSON.stringify(NOT_FOUND), { status: 404 }));
    await ask("Ещё вопрос");
    await vi.waitFor(() => expect(chatStore.get().items.some((i) => i.kind === "error")).toBe(true));
    expect(hasNotice()).toBe(false);
  });
});
