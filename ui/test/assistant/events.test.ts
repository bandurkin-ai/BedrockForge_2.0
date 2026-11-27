// The events of the bus and the reads of the assistant (Э23, 23.21в): one subscription however many callers (`bind.ts`, the connection window), the reads of the dialogs
// asked for together are one, the dialogs and the status are read without a trace in the log of an import window (the talk of the user is not for the log), «Проверить связь».
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setCoreTarget } from "../../src/shell/api";
import { startOpLog, stopOpLog } from "../../src/shell/opLog";
import { checkConnection, dialogsStore, refreshDialogs, refreshStatus, startAssistantEvents, statusStore } from "../../src/modules/assistant/state";
import { getDialog, getStatus, listDialogs, renameDialog, testConnection } from "../../src/modules/assistant/api";

const bus = vi.hoisted(() => ({ listeners: new Set<(event: string, data: string) => void>(), subscribed: 0 }));
vi.mock("../../src/modules/jobs/state", async (orig) => {
  const m = await orig<typeof import("../../src/modules/jobs/state")>();
  return {
    ...m,
    onCoreEvent: (fn: (event: string, data: string) => void) => {
      bus.subscribed++;
      bus.listeners.add(fn);
      return () => bus.listeners.delete(fn);
    },
  };
});
const emit = (event: string): void => bus.listeners.forEach((fn) => fn(event, ""));

const json = (body: unknown, status = 200): Response => new Response(JSON.stringify(body), { status });
const calls: string[] = [];
let route: (path: string, method: string) => Response | Promise<Response> = () => json({});

beforeEach(() => {
  setCoreTarget({ baseUrl: "http://127.0.0.1:5555", token: "tok" });
  calls.length = 0;
  bus.subscribed = 0;
  route = () => json({});
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const path = String(url).replace("http://127.0.0.1:5555/api/v1/assistant", "");
      calls.push(`${init.method ?? "GET"} ${path}`);
      return route(path, init.method ?? "GET");
    }),
  );
  statusStore.set({ status: null, error: null });
  dialogsStore.set({ items: [], total: 0, error: null });
});
afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
});

describe("startAssistantEvents", () => {
  it("is one subscriber however many callers: an event reads the status once", async () => {
    route = (path) => (path === "/status" ? json({ state: "connected", online: true }) : json({ total: 0, items: [] }));
    const stopA = startAssistantEvents();
    const stopB = startAssistantEvents();
    expect(bus.subscribed).toBe(1);
    expect(bus.listeners.size).toBe(1);
    emit("assistant.connection");
    await vi.waitFor(() => expect(statusStore.get().status).not.toBeNull());
    expect(calls.filter((c) => c === "GET /status")).toHaveLength(1);
    stopB();
    stopB(); // a second stop of the same caller changes nothing
    expect(bus.listeners.size).toBe(1);
    stopA();
    expect(bus.listeners.size).toBe(0);
    const stopC = startAssistantEvents();
    expect(bus.listeners.size).toBe(1);
    stopC();
  });
});

describe("refreshDialogs", () => {
  it("the requests that come while a read goes on are one more read, not one each (and not an answer that began before the news)", async () => {
    route = () => json({ total: 1, items: [{ id: "1" }] });
    await Promise.all([refreshDialogs(), refreshDialogs(), refreshDialogs()]);
    expect(calls.filter((c) => c.startsWith("GET /dialogs"))).toHaveLength(2);
    expect(dialogsStore.get().total).toBe(1);
    await refreshDialogs();
    expect(calls.filter((c) => c.startsWith("GET /dialogs"))).toHaveLength(3);
  });
});

describe("the talk of the user is not written into the log of an open window", () => {
  it("the dialogs, their rename and the status leave no request in the log; an ordinary call does", async () => {
    const log = startOpLog("Импорт", "import");
    const n0 = log.lines.length;
    route = (path) => json(path.startsWith("/dialogs/") ? { head: {}, items: [{ kind: "user", text: "секретный вопрос" }] } : { total: 0, items: [] });
    await getDialog("41");
    await listDialogs();
    await renameDialog("41", "Новое название");
    await getStatus().catch(() => undefined);
    expect(log.lines.length).toBe(n0);
    expect(log.lines.join("\n")).not.toContain("секретный вопрос");
    // the control: a call through the common client is written
    await testConnection().catch(() => undefined);
    expect(log.lines.length).toBeGreaterThan(n0);
    stopOpLog(log);
  });
});

describe("checkConnection («Проверить связь»)", () => {
  it("tests the connection and reads the status; a refused test is not an error of the panel", async () => {
    route = (path) => (path === "/connection/test" ? json({ code: "assistant_offline", message: "m", hint: "h" }, 503) : json({ state: "connected", online: false }));
    await checkConnection();
    expect(calls).toEqual(["POST /connection/test", "GET /status"]);
    expect(statusStore.get().status).toMatchObject({ online: false });
    expect(statusStore.get().error).toBeNull();
  });

  it("a status that cannot be read is an error of the status, shown as «Нет связи с ядром»", async () => {
    route = () => json({ code: "x", message: "m", hint: "" }, 500);
    await refreshStatus();
    expect(statusStore.get()).toMatchObject({ status: null });
    expect(statusStore.get().error).toBeTruthy();
  });
});
