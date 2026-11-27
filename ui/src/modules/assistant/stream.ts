/**
 * Reading the stream of a turn (Э23; ADR 0029 решения 21, 24; docs/progress/e23-api.md §8–§10). One segment is one HTTP answer of
 * `text/event-stream`: `POST /turns` (a question), `POST /dialogs/{id}/tool_results` (the results of the window) or `GET /dialogs/{id}/stream?after=n`
 * (catch-up and continuation after a lost connection or a reloaded window).
 *
 * - frames are `id: <n>` (the number in the segment, from 1), `event: <name>`, `data: <JSON>`; `: keepalive` comments are dropped;
 * - events come to `onEvents` in batches, one per network chunk (the feed is rebuilt once per batch, not once per delta);
 * - a stream that broke before `turn.end` is continued with `GET …/stream?after=<last id>` (the core finishes the segment even if the window is gone);
 *   a frame whose number is not above the last one is dropped, so the continuation never doubles an event;
 * - `abort()` leaves the stream (the segment runs on in the core); «Стоп» is `stop()`: `POST …/cancel`, then the stream ends with `turn.end cancelled`.
 *
 * The bodies of the requests are not written into the operation logs (the dialog is the user's). No React.
 */
import { coreTarget, CoreError } from "../../shell/api";
import { apiUrl, authHeaders } from "../../shell/coreClient";
import { cancelTurn, coreErrorOf, streamPath, toolResultsPath, TURNS_PATH } from "./api";
import type { ToolResultsBody, TurnBody, TurnEndState, TurnEnvelope, TurnEvent, TurnEventName } from "./wireTypes";

// ---- frames ----

export interface SseFrame {
  /** `id:` of the frame; null — there was none */
  id: number | null;
  event: string;
  data: string;
}

/** Splits an SSE text into frames (a blank line ends one); `rest` is the unfinished tail. Comments (`:`) and frames without data are dropped. */
export function parseFrames(buffer: string): { frames: SseFrame[]; rest: string } {
  const text = buffer.includes("\r") ? buffer.replace(/\r\n/g, "\n") : buffer;
  const parts = text.split("\n\n");
  const rest = parts.pop() ?? "";
  const frames: SseFrame[] = [];
  for (const block of parts) {
    let event = "message";
    let id: number | null = null;
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("id:")) {
        const n = Number(line.slice(3).trim());
        id = Number.isInteger(n) && n >= 0 ? n : null;
      } else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
    if (data.length) frames.push({ id, event, data: data.join("\n") });
  }
  return { frames, rest };
}

const EVENTS: readonly TurnEventName[] = ["turn.start", "text.delta", "thinking", "tool.start", "tool.end", "pending", "usage", "notice", "error", "turn.end"];

/** A frame as a typed event; an unknown event name or a broken JSON gives null (the stream goes on). */
export function decodeFrame(frame: SseFrame): TurnEnvelope | null {
  if (!(EVENTS as readonly string[]).includes(frame.event)) return null;
  try {
    const data: unknown = JSON.parse(frame.data);
    if (data === null || typeof data !== "object") return null;
    return { event: frame.event, data, id: frame.id } as TurnEnvelope;
  } catch {
    return null;
  }
}

// ---- a segment ----

export type SegmentResult =
  /** `turn.end` came */
  | { kind: "end"; state: TurnEndState; lastId: number }
  /** the core refused before the stream (409 `assistant_busy`, `assistant_not_connected`, 404, 422…) */
  | { kind: "refused"; error: CoreError }
  /** the window left the stream, or the core has no running segment any more (204): read the dialog again */
  | { kind: "detached"; lastId: number }
  /** the stream broke and the continuation did not succeed (the partial text is in the feed; the dialog is read again) */
  | { kind: "broken"; lastId: number };

export interface StreamOptions {
  onEvents: (batch: TurnEnvelope[]) => void;
  fetch?: typeof fetch;
  /** continuations after a break (default 3) */
  reconnects?: number;
  /** pause before a continuation, ms, × attempt (default 500) */
  retryDelayMs?: number;
  /** no byte for this long (the core sends `: keepalive` every 15 s) counts as a break, ms (default 45000) */
  stallMs?: number;
  /** the dialog the stream belongs to when it is known before `turn.start` (a continuation); new dialogs learn it from `turn.start` */
  dialogId?: string | null;
}

export interface TurnStream {
  done: Promise<SegmentResult>;
  /** the number of the last event taken */
  readonly lastId: number;
  /** `id` of the dialog; null before `turn.start` of a new dialog */
  readonly dialogId: string | null;
  /** leaves the stream, the segment runs on in the core */
  abort: () => void;
  /** «Стоп»: `POST …/cancel`, then wait for `turn.end`; the stream is left after `graceMs` (default 3000) */
  stop: (graceMs?: number) => Promise<void>;
}

const OFFLINE = new CoreError(0, "core_offline", "Ядро недоступно.", "Подождите перезапуска ядра.");

interface Request {
  method: "GET" | "POST";
  path: string;
  body?: unknown;
}

function run(first: Request, after: number, opts: StreamOptions): TurnStream {
  const ctrl = new AbortController();
  const doFetch = opts.fetch ?? fetch;
  const reconnects = opts.reconnects ?? 3;
  const retryDelay = opts.retryDelayMs ?? 500;
  const stallMs = opts.stallMs ?? 45000;
  let lastId = after;
  let dialogId = opts.dialogId ?? null;
  let ended: TurnEndState | null = null;

  const open = async (req: Request, signal: AbortSignal): Promise<Response> => {
    const target = coreTarget();
    if (!target) throw OFFLINE;
    const headers = authHeaders(target, req.body === undefined ? { Accept: "text/event-stream" } : { Accept: "text/event-stream", "Content-Type": "application/json" });
    let leave: () => void = () => undefined;
    // the window leaving ends the wait even for an answer that never starts
    const left = new Promise<never>((_, reject) => {
      leave = () => reject(new DOMException("aborted", "AbortError"));
      signal.addEventListener("abort", leave, { once: true });
    });
    try {
      return await Promise.race([doFetch(apiUrl(target, req.path), { method: req.method, headers, body: req.body === undefined ? undefined : JSON.stringify(req.body), signal }), left]);
    } catch (e) {
      if (signal.aborted) throw e;
      throw OFFLINE;
    } finally {
      signal.removeEventListener("abort", leave);
      left.catch(() => undefined);
    }
  };

  /** Reads one answer to its end; true — the stream ended by itself or by `turn.end` (see `ended`), false — it broke. */
  const read = async (res: Response): Promise<boolean> => {
    if (!res.body) return false;
    const attempt = new AbortController();
    const onAbort = () => attempt.abort();
    ctrl.signal.addEventListener("abort", onAbort);
    let stalled: ReturnType<typeof setTimeout> | null = null;
    const arm = () => {
      if (stalled) clearTimeout(stalled);
      stalled = setTimeout(() => attempt.abort(), stallMs);
    };
    const reader = res.body.getReader();
    const cancelReader = () => void reader.cancel().catch(() => undefined);
    attempt.signal.addEventListener("abort", cancelReader);
    const decoder = new TextDecoder();
    let buf = "";
    try {
      arm();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        arm();
        buf += typeof value === "string" ? value : decoder.decode(value, { stream: true });
        const parsed = parseFrames(buf);
        buf = parsed.rest;
        const batch: TurnEnvelope[] = [];
        for (const frame of parsed.frames) {
          if (frame.id !== null && frame.id <= lastId) continue;
          const ev = decodeFrame(frame);
          if (!ev) continue;
          if (ev.id !== null) lastId = ev.id;
          if (ev.event === "turn.start") dialogId = ev.data.dialog.id;
          if (ev.event === "turn.end") ended = ev.data.state;
          batch.push(ev);
        }
        if (batch.length) opts.onEvents(batch);
        if (ended) return true;
      }
      return ended !== null;
    } catch {
      return false;
    } finally {
      if (stalled) clearTimeout(stalled);
      ctrl.signal.removeEventListener("abort", onAbort);
      attempt.signal.removeEventListener("abort", cancelReader);
      cancelReader();
    }
  };

  const done = (async (): Promise<SegmentResult> => {
    let req = first;
    for (let attempt = 0; ; attempt++) {
      let res: Response;
      try {
        res = await open(req, ctrl.signal);
      } catch (e) {
        if (ctrl.signal.aborted) return { kind: "detached", lastId };
        // the very first request got no answer: nothing was sent to the core as far as the window knows
        if (attempt >= reconnects || !dialogId) return { kind: "refused", error: e instanceof CoreError ? e : OFFLINE };
        await sleep(retryDelay * attempt, ctrl.signal);
        req = { method: "GET", path: streamPath(dialogId, lastId) };
        continue;
      }
      if (res.status === 204) return { kind: "detached", lastId };
      if (!res.ok) return { kind: "refused", error: coreErrorOf(res.status, await res.text().catch(() => "")) };
      await read(res);
      if (ended) return { kind: "end", state: ended, lastId };
      if (ctrl.signal.aborted) return { kind: "detached", lastId };
      if (attempt >= reconnects || !dialogId) return { kind: "broken", lastId };
      await sleep(retryDelay * (attempt + 1), ctrl.signal);
      if (ctrl.signal.aborted) return { kind: "detached", lastId };
      req = { method: "GET", path: streamPath(dialogId, lastId) };
    }
  })();

  const stream: TurnStream = {
    done,
    get lastId() {
      return lastId;
    },
    get dialogId() {
      return dialogId;
    },
    abort: () => ctrl.abort(),
    stop: async (graceMs = 3000) => {
      if (!dialogId) {
        ctrl.abort();
        return;
      }
      await cancelTurn(dialogId).catch(() => undefined);
      let timer: ReturnType<typeof setTimeout> | undefined;
      const grace = new Promise<void>((resolve) => {
        timer = setTimeout(resolve, graceMs);
      });
      await Promise.race([done, grace]);
      if (timer) clearTimeout(timer);
      ctrl.abort();
    },
  };
  return stream;
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (ms <= 0 || signal.aborted) return resolve();
    const t = setTimeout(done, ms);
    function done() {
      clearTimeout(t);
      signal.removeEventListener("abort", done);
      resolve();
    }
    signal.addEventListener("abort", done);
  });
}

/** `POST /turns`: a question; a new dialog (`dialog_id: null`) learns its id from `turn.start`. */
export function startTurn(body: TurnBody, opts: StreamOptions): TurnStream {
  return run({ method: "POST", path: TURNS_PATH, body }, 0, { ...opts, dialogId: body.dialog_id ?? opts.dialogId ?? null });
}

/** `POST /dialogs/{id}/tool_results`: the results of the commands of the window; the core goes on with a new segment. */
export function continueTurn(dialogId: string, body: ToolResultsBody, opts: StreamOptions): TurnStream {
  return run({ method: "POST", path: toolResultsPath(dialogId), body }, 0, { ...opts, dialogId });
}

/** `GET /dialogs/{id}/stream?after=n`: the events of the running segment after number `after` (204 — no segment: `detached`). */
export function resumeTurn(dialogId: string, after: number, opts: StreamOptions): TurnStream {
  return run({ method: "GET", path: streamPath(dialogId, after) }, after, { ...opts, dialogId });
}

export type { TurnEvent };
