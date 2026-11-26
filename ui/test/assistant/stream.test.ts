import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setCoreTarget } from "../../src/shell/api";
import { startOpLog, stopOpLog, opLogStore } from "../../src/shell/opLog";
import { coreErrorOf, saveKey } from "../../src/modules/assistant/api";
import { continueTurn, decodeFrame, parseFrames, resumeTurn, startTurn, type SegmentResult } from "../../src/modules/assistant/stream";
import type { DialogHead, TurnBody, TurnContext, TurnEnvelope } from "../../src/modules/assistant/wireTypes";

const T0 = "2026-11-26T10:00:00Z";
const HEAD: DialogHead = {
  id: "41",
  uid: "d-1",
  title: "Q",
  created_at: T0,
  last_at: T0,
  questions: 1,
  cost_usd: null,
  tokens: { input: 0, output: 0, cache_read: 0, cache_write: 0 },
  models: [],
  state: "running",
  temporary: false,
  project_uid: null,
};
const CONTEXT: TurnContext = { project: null, set: null, hole: null, panels: [], windows: [], view: { mode: "iso", perspective: true, vexag: 1 }, section: null, selection: null, audit: null };
const BODY: TurnBody = { dialog_id: null, text: "Привет", context: CONTEXT, tools: [] };

const frame = (id: number, event: string, data: unknown): string => `id: ${id}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const START = frame(1, "turn.start", { dialog: HEAD, segment_id: "s-1", model: "claude-opus-5-5", effort: "medium" });
const DELTA = (id: number, text: string) => frame(id, "text.delta", { item_id: "a1", text });
const END = (id: number, state = "idle") => frame(id, "turn.end", { state, stop_reason: "end_turn", dialog: { ...HEAD, state: state === "idle" ? "idle" : state } });

const enc = new TextEncoder();
/** An answer of the core that sends the pieces one by one (bytes, a split frame included). */
function sse(pieces: string[], status = 200): Response {
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      for (const p of pieces) c.enqueue(enc.encode(p));
      c.close();
    },
  });
  return new Response(body, { status, headers: { "Content-Type": "text/event-stream" } });
}
/** An answer that stays open until the reader leaves. */
function open(pieces: string[]): Response {
  return new Response(
    new ReadableStream<Uint8Array>({
      start(c) {
        for (const p of pieces) c.enqueue(enc.encode(p));
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

interface Call {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: unknown;
}
let calls: Call[] = [];
function stubFetch(handler: (c: Call, i: number) => Response | Promise<Response>): void {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const c: Call = { url, method: init.method ?? "GET", headers: (init.headers ?? {}) as Record<string, string>, body: init.body ? JSON.parse(String(init.body)) : undefined };
      calls.push(c);
      return handler(c, calls.length - 1);
    }),
  );
}

beforeEach(() => setCoreTarget({ baseUrl: "http://127.0.0.1:5555", token: "tok" }));
afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
});

const collect = () => {
  const batches: TurnEnvelope[][] = [];
  return { batches, onEvents: (b: TurnEnvelope[]) => void batches.push(b), all: () => batches.flat() };
};

describe("frames", () => {
  it("number, name, data; comments and frames without data are dropped", () => {
    const { frames, rest } = parseFrames(`: keepalive\n\n${DELTA(7, "привет")}id: 8\nevent: thinking\ndata: {"state":`);
    expect(frames).toEqual([{ id: 7, event: "text.delta", data: '{"item_id":"a1","text":"привет"}' }]);
    expect(rest).toBe('id: 8\nevent: thinking\ndata: {"state":');
  });

  it("CRLF, a data line split in two, a frame without a number", () => {
    const { frames } = parseFrames('event: error\r\ndata: {"a":\r\ndata: 1}\r\n\r\n');
    expect(frames).toEqual([{ id: null, event: "error", data: '{"a":\n1}' }]);
  });

  it("a number that is not a number is none", () => {
    expect(parseFrames("id: x\nevent: e\ndata: 1\n\n").frames[0].id).toBeNull();
    expect(parseFrames("id: -3\nevent: e\ndata: 1\n\n").frames[0].id).toBeNull();
  });

  it("decode: known events with their number; unknown names and broken JSON are skipped", () => {
    expect(decodeFrame({ id: 4, event: "text.delta", data: '{"item_id":"a","text":"б"}' })).toEqual({ event: "text.delta", data: { item_id: "a", text: "б" }, id: 4 });
    expect(decodeFrame({ id: 1, event: "future.event", data: "{}" })).toBeNull();
    expect(decodeFrame({ id: 1, event: "text.delta", data: "{broken" })).toBeNull();
    expect(decodeFrame({ id: 1, event: "text.delta", data: "42" })).toBeNull();
  });
});

describe("startTurn", () => {
  it("sends the question with the token and reads the stream to turn.end; events come per chunk", async () => {
    const whole = START + DELTA(2, "При") + DELTA(3, "вет") + END(4);
    // the bytes are cut inside a frame and inside a Cyrillic letter
    const bytes = enc.encode(whole);
    const cut = [0, 70, 71, 140, bytes.length].map((v, i, a) => (i ? bytes.slice(a[i - 1], v) : null)).filter((b): b is Uint8Array => b !== null);
    stubFetch(() => new Response(new ReadableStream<Uint8Array>({ start(c) { for (const b of cut) c.enqueue(b); c.close(); } }), { status: 200 }));
    const got = collect();
    const s = startTurn(BODY, { onEvents: got.onEvents });
    const res = await s.done;
    expect(res).toEqual({ kind: "end", state: "idle", lastId: 4 });
    expect(got.all().map((e) => [e.id, e.event])).toEqual([[1, "turn.start"], [2, "text.delta"], [3, "text.delta"], [4, "turn.end"]]);
    expect(got.batches.length).toBeLessThanOrEqual(cut.length);
    expect(s.dialogId).toBe("41");
    expect(s.lastId).toBe(4);
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({ url: "http://127.0.0.1:5555/api/v1/assistant/turns", method: "POST", body: BODY });
    expect(calls[0].headers["X-BF-Token"]).toBe("tok");
    expect(calls[0].headers.Accept).toBe("text/event-stream");
  });

  it("a refusal before the stream is a CoreError with the text of the core", async () => {
    stubFetch(() => new Response(JSON.stringify({ code: "assistant_busy", message: "Ответ ещё идёт", hint: "Дождитесь конца" }), { status: 409 }));
    const got = collect();
    const res = await startTurn(BODY, { onEvents: got.onEvents }).done;
    expect(res.kind).toBe("refused");
    if (res.kind !== "refused") return;
    expect(res.error).toMatchObject({ status: 409, code: "assistant_busy", message: "Ответ ещё идёт", hint: "Дождитесь конца" });
    expect(got.all()).toEqual([]);
  });

  it("no core: refused core_offline, nothing sent", async () => {
    setCoreTarget(null);
    stubFetch(() => sse([]));
    const res = await startTurn(BODY, { onEvents: () => undefined, reconnects: 0 }).done;
    expect(res.kind === "refused" && res.error.code).toBe("core_offline");
    expect(calls).toHaveLength(0);
  });

  it("a network failure of the first request is refused, not retried (the question may not have reached the core)", async () => {
    stubFetch(() => {
      throw new TypeError("fetch failed");
    });
    const res = await startTurn(BODY, { onEvents: () => undefined, retryDelayMs: 1 }).done;
    expect(res.kind === "refused" && res.error.code).toBe("core_offline");
    expect(calls).toHaveLength(1);
  });
});

describe("a stream that breaks", () => {
  it("is continued with GET …/stream?after=<last number>; a frame that came twice is taken once", async () => {
    stubFetch((_c, i) => (i === 0 ? sse([START + DELTA(2, "раз "), DELTA(3, "два ").slice(0, 20)]) : sse([DELTA(3, "два "), DELTA(4, "три"), END(5)])));
    const got = collect();
    const s = startTurn(BODY, { onEvents: got.onEvents, retryDelayMs: 1 });
    const res = await s.done;
    expect(res).toEqual({ kind: "end", state: "idle", lastId: 5 });
    expect(calls.map((c) => [c.method, c.url.replace("http://127.0.0.1:5555", "")])).toEqual([
      ["POST", "/api/v1/assistant/turns"],
      ["GET", "/api/v1/assistant/dialogs/41/stream?after=2"],
    ]);
    expect(got.all().map((e) => e.id)).toEqual([1, 2, 3, 4, 5]);
    const texts = got.all().filter((e) => e.event === "text.delta").map((e) => (e.event === "text.delta" ? e.data.text : ""));
    expect(texts).toEqual(["раз ", "два ", "три"]);
  });

  it("the continuation gets 204: the segment is over in the core, the window reads the dialog", async () => {
    stubFetch((_c, i) => (i === 0 ? sse([START + DELTA(2, "а")]) : new Response(null, { status: 204 })));
    const res = await startTurn(BODY, { onEvents: () => undefined, retryDelayMs: 1 }).done;
    expect(res).toEqual({ kind: "detached", lastId: 2 });
  });

  it("gives up after the continuations: broken", async () => {
    stubFetch((_c, i) => (i === 0 ? sse([START]) : sse([])));
    const res = await startTurn(BODY, { onEvents: () => undefined, retryDelayMs: 1, reconnects: 2 }).done;
    expect(res).toEqual({ kind: "broken", lastId: 1 });
    expect(calls).toHaveLength(3);
  });

  it("a stream of a new dialog that broke before turn.start cannot be continued: broken", async () => {
    stubFetch(() => sse([]));
    const res = await startTurn(BODY, { onEvents: () => undefined, retryDelayMs: 1 }).done;
    expect(res).toEqual({ kind: "broken", lastId: 0 });
    expect(calls).toHaveLength(1);
  });

  it("a stream that is silent for stallMs is a break", async () => {
    stubFetch(() => open([START]));
    const res = await startTurn({ ...BODY, dialog_id: "41" }, { onEvents: () => undefined, stallMs: 30, reconnects: 0 }).done;
    expect(res).toEqual({ kind: "broken", lastId: 1 });
  });
});

describe("resume and continue", () => {
  it("resumeTurn asks for the events after n; a comment is not an event", async () => {
    stubFetch(() => sse([": keepalive\n\n", DELTA(8, "x"), END(9)]));
    const got = collect();
    const res = await resumeTurn("41", 7, { onEvents: got.onEvents }).done;
    expect(res).toEqual({ kind: "end", state: "idle", lastId: 9 });
    expect(calls[0]).toMatchObject({ method: "GET", url: "http://127.0.0.1:5555/api/v1/assistant/dialogs/41/stream?after=7" });
    expect(got.all().map((e) => e.id)).toEqual([8, 9]);
  });

  it("resumeTurn: 204 — no segment", async () => {
    stubFetch(() => new Response(null, { status: 204 }));
    expect(await resumeTurn("41", 0, { onEvents: () => undefined }).done).toEqual({ kind: "detached", lastId: 0 });
  });

  it("an id of a dialog is escaped in the path", async () => {
    stubFetch(() => new Response(null, { status: 204 }));
    await resumeTurn("t-ab/cd", 0, { onEvents: () => undefined }).done;
    expect(calls[0].url).toContain("/dialogs/t-ab%2Fcd/stream");
  });

  it("continueTurn posts the results to the dialog; a new segment is numbered from 1 again", async () => {
    stubFetch(() => sse([frame(1, "turn.start", { dialog: HEAD, segment_id: "s-2", model: "m", effort: "low" }), END(2)]));
    const body = { pending_id: "p-1", results: [{ tool_use_id: "toolu_a", ok: true, text: "Выбрана скважина DH-012" }], context: CONTEXT, tools: [] };
    const got = collect();
    const res: SegmentResult = await continueTurn("41", body, { onEvents: got.onEvents }).done;
    expect(res).toEqual({ kind: "end", state: "idle", lastId: 2 });
    expect(calls[0]).toMatchObject({ method: "POST", url: "http://127.0.0.1:5555/api/v1/assistant/dialogs/41/tool_results", body });
  });

  it("a conflict on the results is a refusal with the field of the core", async () => {
    stubFetch(() => new Response(JSON.stringify({ code: "assistant_pending_mismatch", message: "Результаты не совпадают", hint: "", expected: ["toolu_a"] }), { status: 409 }));
    const res = await continueTurn("41", { pending_id: "p-1", results: [], context: CONTEXT, tools: [] }, { onEvents: () => undefined }).done;
    expect(res.kind === "refused" && res.error.body).toMatchObject({ expected: ["toolu_a"] });
  });
});

describe("leaving and stopping", () => {
  it("abort leaves the stream: detached, no further request", async () => {
    stubFetch(() => open([START]));
    const got = collect();
    const s = startTurn({ ...BODY, dialog_id: "41" }, { onEvents: got.onEvents, retryDelayMs: 1 });
    await vi.waitFor(() => expect(got.all()).toHaveLength(1));
    s.abort();
    expect(await s.done).toEqual({ kind: "detached", lastId: 1 });
    expect(calls).toHaveLength(1);
  });

  it("abort before the answer comes", async () => {
    stubFetch(() => new Promise<Response>(() => undefined));
    const s = startTurn(BODY, { onEvents: () => undefined });
    s.abort();
    expect(await s.done).toEqual({ kind: "detached", lastId: 0 });
  });

  it("stop: cancel is posted, the stream is read to turn.end cancelled", async () => {
    let finish: () => void = () => undefined;
    stubFetch((c) => {
      if (c.url.endsWith("/cancel")) {
        finish();
        return new Response(null, { status: 204 });
      }
      return new Response(
        new ReadableStream<Uint8Array>({
          start(ctrl) {
            ctrl.enqueue(enc.encode(START + DELTA(2, "дол")));
            finish = () => {
              ctrl.enqueue(enc.encode(END(3, "cancelled")));
              ctrl.close();
            };
          },
        }),
        { status: 200 },
      );
    });
    const got = collect();
    const s = startTurn(BODY, { onEvents: got.onEvents });
    await vi.waitFor(() => expect(s.dialogId).toBe("41"));
    await s.stop(2000);
    expect(await s.done).toEqual({ kind: "end", state: "cancelled", lastId: 3 });
    expect(calls.map((c) => c.method + " " + c.url.replace("http://127.0.0.1:5555/api/v1/assistant", ""))).toEqual(["POST /turns", "POST /dialogs/41/cancel"]);
  });

  it("stop: a core that does not end the stream is left after the grace time", async () => {
    stubFetch((c) => (c.url.endsWith("/cancel") ? new Response(null, { status: 204 }) : open([START])));
    const s = startTurn(BODY, { onEvents: () => undefined, reconnects: 0 });
    await vi.waitFor(() => expect(s.dialogId).toBe("41"));
    await s.stop(20);
    expect((await s.done).kind).toBe("detached");
  });
});

describe("the client", () => {
  it("the body of a refusal becomes CoreError; a text that is not JSON still gives a message", () => {
    expect(coreErrorOf(422, JSON.stringify({ code: "assistant_bad_key", message: "Ключ отклонён", hint: "Проверьте ключ" }))).toMatchObject({ status: 422, code: "assistant_bad_key", hint: "Проверьте ключ" });
    expect(coreErrorOf(502, "<html>bad gateway</html>")).toMatchObject({ status: 502, code: "error", message: "Ядро ответило 502" });
  });

  it("a key does not get into the log of an operation window", async () => {
    stubFetch(() => new Response(JSON.stringify({ state: "connected" }), { status: 200 }));
    const log = startOpLog("Импорт", "import");
    try {
      await saveKey("sk-ant-api03-SECRETSECRETSECRET1234");
      expect(calls[0].body).toEqual({ key: "sk-ant-api03-SECRETSECRETSECRET1234" });
      expect(log.lines.join("\n")).not.toContain("SECRETSECRET");
      expect(opLogStore.get().logs.some((l) => l.lines.join("\n").includes("SECRETSECRET"))).toBe(false);
    } finally {
      stopOpLog(log);
    }
  });
});
