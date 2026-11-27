import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setCoreTarget } from "../../src/shell/api";
import { ask, chatStore, configureAssistant, newDialog, setPendingHandler, stopTurn, submitResults } from "../../src/modules/assistant/state";
import type { AssistantItem, DialogHead, Item, TurnContext } from "../../src/modules/assistant/wireTypes";

const T0 = "2026-11-26T10:00:00Z";
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
  project_uid: null,
});
const CONTEXT: TurnContext = { project: null, set: null, hole: null, panels: [], windows: [], view: { mode: "iso", perspective: true, vexag: 1 }, section: null, selection: null, audit: null };
const frame = (id: number, event: string, data: unknown): string => `id: ${id}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const enc = new TextEncoder();
const answer = (...frames: string[]): Response =>
  new Response(new ReadableStream<Uint8Array>({ start(c) { c.enqueue(enc.encode(frames.join(""))); c.close(); } }), { status: 200 });

const calls: { url: string; body: unknown }[] = [];
function stub(handler: (url: string, n: number) => Response): void {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    calls.push({ url: String(url).replace("http://127.0.0.1:5555/api/v1/assistant", ""), body: init.body ? JSON.parse(String(init.body)) : undefined });
    return handler(String(url), calls.length - 1);
  }));
}
const settled = () => vi.waitFor(() => expect(chatStore.get().status).not.toBe("running"));

beforeEach(() => {
  setCoreTarget({ baseUrl: "http://127.0.0.1:5555", token: "tok" });
  newDialog();
  setPendingHandler(null);
  configureAssistant({ context: () => CONTEXT, tools: () => [], titleOf: (t) => (t.command === "hole.select" ? "Выбрать скважину" : null) });
});
afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
  configureAssistant(null);
});

describe("ask", () => {
  it("shows the question at once, fills the answer, ends idle", async () => {
    stub((url) =>
      url.endsWith("/dialogs") || url.includes("/dialogs?")
        ? new Response(JSON.stringify({ total: 0, items: [] }), { status: 200 })
        : answer(
            frame(1, "turn.start", { dialog: HEAD(), segment_id: "s", model: "m", effort: "low", user_item: { kind: "user", id: "u1", at: T0, text: "Привет" } }),
            frame(2, "text.delta", { item_id: "a1", text: "Здравствуйте" }),
            frame(3, "turn.end", { state: "idle", stop_reason: "end_turn", dialog: HEAD("idle") }),
          ),
    );
    expect(await ask("  Привет  ")).toMatchObject({ ok: true });
    expect(chatStore.get().items[0]).toMatchObject({ kind: "user", text: "Привет" });
    expect(chatStore.get().status).toBe("running");
    await settled();
    const s = chatStore.get();
    expect(s.items.map((i) => i.id)).toEqual(["u1", "a1"]);
    expect((s.items[1] as AssistantItem).text).toBe("Здравствуйте");
    expect(s.dialog?.id).toBe("41");
    expect(calls[0]).toMatchObject({ url: "/turns", body: { dialog_id: null, text: "Привет", context: CONTEXT, tools: [] } });
  });

  it("an empty question and a question while an answer runs are refused without a request", async () => {
    stub(() => answer());
    expect(await ask("   ")).toMatchObject({ ok: false });
    chatStore.set({ status: "running" });
    expect(await ask("ещё")).toMatchObject({ ok: false, message: "Ответ ещё идёт" });
    expect(calls).toHaveLength(0);
  });

  it("a refusal of the core stays in the feed after the question", async () => {
    stub(() => new Response(JSON.stringify({ code: "assistant_not_connected", message: "Claude не подключён", hint: "Откройте «Claude: подключение»" }), { status: 409 }));
    await ask("Привет");
    await settled();
    const s = chatStore.get();
    expect(s.status).toBe("idle");
    expect(s.items.map((i) => i.kind)).toEqual(["user", "error"]);
    expect(s.items[1]).toMatchObject({ code: "assistant_not_connected", hint: "Откройте «Claude: подключение»" });
  });
});

describe("pending", () => {
  const pendingFrames = (): string[] => [
    frame(1, "turn.start", { dialog: HEAD(), segment_id: "s", model: "m", effort: "low", user_item: { kind: "user", id: "u1", at: T0, text: "Выбери" } }),
    frame(2, "pending", { id: "p-1", created_at: T0, tools: [{ tool_use_id: "toolu_a", name: "ui_hole_select", command: "hole.select", args: { hole_id: "DH-012" }, item_id: "c1" }] }),
    frame(3, "turn.end", { state: "pending", stop_reason: "tool_use", dialog: HEAD("pending") }),
  ];

  it("a live segment that ends pending calls the handler; the results go to the same dialog and the next segment goes on", async () => {
    const handler = vi.fn();
    setPendingHandler(handler);
    stub((url, n) =>
      n === 0
        ? answer(...pendingFrames())
        : url.includes("/tool_results")
          ? answer(
              frame(1, "turn.start", { dialog: HEAD(), segment_id: "s2", model: "m", effort: "low" }),
              frame(2, "text.delta", { item_id: "a2", text: "Готово" }),
              frame(3, "turn.end", { state: "idle", stop_reason: "end_turn", dialog: HEAD("idle") }),
            )
          : new Response(JSON.stringify({ total: 0, items: [] }), { status: 200 }),
    );
    await ask("Выбери");
    await vi.waitFor(() => expect(handler).toHaveBeenCalledTimes(1));
    expect(handler.mock.calls[0][0].tools[0].command).toBe("hole.select");
    expect(handler.mock.calls[0][1]).toBe("41");
    const s = chatStore.get();
    expect(s.status).toBe("pending");
    expect((s.items.find((i) => i.id === "c1") as Item & { title: string }).title).toBe("Выбрать скважину");

    const done = submitResults([{ tool_use_id: "toolu_a", ok: true, text: "Выбрана скважина DH-012" }]);
    expect(done).not.toBeNull();
    await done;
    const end = chatStore.get();
    expect(end.status).toBe("idle");
    expect(end.pending).toBeNull();
    expect(end.items.map((i) => i.id)).toEqual(["u1", "c1", "a2"]);
    const post = calls.find((c) => c.url === "/dialogs/41/tool_results");
    expect(post?.body).toMatchObject({ pending_id: "p-1", results: [{ tool_use_id: "toolu_a", ok: true }] });
  });

  it("submitResults without pending does nothing", () => {
    stub(() => answer());
    expect(submitResults([])).toBeNull();
    expect(calls).toHaveLength(0);
  });

  it("a new question while commands wait declines them", async () => {
    stub((url, n) => (n === 0 ? answer(...pendingFrames()) : url.includes("/turns") ? answer(frame(1, "turn.end", { state: "idle", stop_reason: null, dialog: HEAD("idle") })) : new Response(JSON.stringify({ total: 0, items: [] }))));
    await ask("Выбери");
    await vi.waitFor(() => expect(chatStore.get().status).toBe("pending"));
    await ask("Нет, другое");
    expect(chatStore.get().pending).toBeNull();
    expect((chatStore.get().items.find((i) => i.id === "c1") as Item & { status: string }).status).toBe("declined");
    await settled();
    expect(calls.filter((c) => c.url === "/turns")[1].body).toMatchObject({ dialog_id: "41" });
  });
});

describe("stop and new dialog", () => {
  it("stop posts cancel to the dialog", async () => {
    chatStore.set({ dialog: HEAD(), status: "running" });
    stub(() => new Response(null, { status: 204 }));
    await stopTurn();
    expect(calls).toEqual([{ url: "/dialogs/41/cancel", body: {} }]);
  });

  it("a new dialog empties the feed and leaves the running stream", async () => {
    stub(() => new Response(new ReadableStream<Uint8Array>({ start(c) { c.enqueue(enc.encode(frame(1, "turn.start", { dialog: HEAD(), segment_id: "s", model: "m", effort: "low" }))); } }), { status: 200 }));
    await ask("Долго");
    await vi.waitFor(() => expect(chatStore.get().dialog?.id).toBe("41"));
    newDialog();
    expect(chatStore.get()).toMatchObject({ dialog: null, items: [], status: "idle", pending: null });
  });
});
