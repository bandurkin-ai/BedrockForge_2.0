/**
 * The client of the Claude assistant routes (Э23; ADR 0029; docs/progress/e23-api.md §2–§9; core — `assistant/router.py`). The routes of a turn
 * (`POST /turns`, `POST /dialogs/{id}/tool_results`, `GET /dialogs/{id}/stream`) answer a stream and are read by `stream.ts`.
 *
 * The routes «только оболочка» (`PUT|GET /connection/secret`, `POST /connection/sealed`) belong to the main process, not to the window.
 * The key and the login code go through `quiet`: the common client writes request bodies into the log of an open import window (ТЗ 7.9), and a
 * secret must not get there. No React.
 */
import { api, CoreError, coreTarget } from "../../shell/api";
import { apiUrl, authHeaders } from "../../shell/coreClient";
import type {
  AssistantStatus,
  Dialog,
  DialogHead,
  DialogList,
  LoginStarted,
  PrefsBody,
  TestResult,
  UsageReport,
} from "./wireTypes";

const OFFLINE = () => new CoreError(0, "core_offline", "Ядро недоступно.", "Подождите перезапуска ядра.");

/** The `{code, message, hint, …}` body of a refusal of the core as `CoreError` (the fields of the body are in `err.body`). */
export function coreErrorOf(status: number, text: string): CoreError {
  let e: Record<string, unknown> = {};
  try {
    const parsed: unknown = text ? JSON.parse(text) : null;
    if (parsed && typeof parsed === "object") e = parsed as Record<string, unknown>;
  } catch {
    e = {};
  }
  const err = new CoreError(status, String(e.code ?? "error"), String(e.message ?? `Ядро ответило ${status}`), String(e.hint ?? ""));
  err.detail = typeof e.detail === "string" ? e.detail : undefined;
  err.body = e;
  return err;
}

/** A call that writes nothing about its body into the operation logs. */
async function quiet<T>(method: string, path: string, body: unknown): Promise<T> {
  const target = coreTarget();
  if (!target) throw OFFLINE();
  let r: Response;
  try {
    r = await fetch(apiUrl(target, path), { method, headers: authHeaders(target, { "Content-Type": "application/json" }), body: JSON.stringify(body) });
  } catch {
    throw OFFLINE();
  }
  const text = await r.text().catch(() => "");
  if (!r.ok) throw coreErrorOf(r.status, text);
  try {
    return (text ? JSON.parse(text) : null) as T;
  } catch {
    throw new CoreError(r.status, "bad_answer", "Ядро вернуло ответ в непонятной форме.", "Обновите программу.");
  }
}

const BASE = "/assistant";
const id = (dialogId: string) => encodeURIComponent(dialogId);

// ---- status, prefs, usage (§2, §4) ----

export const getStatus = (): Promise<AssistantStatus> => api.get(`${BASE}/status`);
export const putPrefs = (prefs: PrefsBody): Promise<AssistantStatus> => api.put(`${BASE}/prefs`, prefs);
export const getUsage = (): Promise<UsageReport> => api.get(`${BASE}/usage`);

// ---- connection (§3) ----

/** `POST /connection/key`: the key is checked before it is kept (422 `assistant_bad_key_format`, `assistant_bad_key`; 503 `assistant_offline`). */
export const saveKey = (key: string): Promise<AssistantStatus> => quiet("POST", `${BASE}/connection/key`, { key });
export const testConnection = (): Promise<TestResult> => api.post(`${BASE}/connection/test`, {});
export const disconnect = (): Promise<AssistantStatus> => api.del(`${BASE}/connection`);

export const loginStart = (): Promise<LoginStarted> => api.post(`${BASE}/login/start`, {});
/** 422 `assistant_login_code` (a wrong code), 404 `assistant_login_gone` (the login expired). */
export const loginFinish = (loginId: string, code: string): Promise<AssistantStatus> => quiet("POST", `${BASE}/login/${id(loginId)}/finish`, { code });
export const loginCancel = (loginId: string): Promise<null> => api.del(`${BASE}/login/${id(loginId)}`);

// ---- dialogs (§7) ----

export const listDialogs = (offset = 0, limit = 50): Promise<DialogList> => api.get(`${BASE}/dialogs?offset=${offset}&limit=${limit}`);
/** The last non-empty dialog of the project; null when there is none (204). */
export const lastDialog = (): Promise<DialogHead | null> => api.get<DialogHead | null>(`${BASE}/dialogs/last`);
export const getDialog = (dialogId: string): Promise<Dialog> => api.get(`${BASE}/dialogs/${id(dialogId)}`);
export const renameDialog = (dialogId: string, title: string): Promise<DialogHead> => api.patch(`${BASE}/dialogs/${id(dialogId)}`, { title });
export const deleteDialog = (dialogId: string): Promise<null> => api.del(`${BASE}/dialogs/${id(dialogId)}`);

/** `POST /dialogs/{id}/cancel` → 204 (also when no segment is running). The stream ends with `turn.end {state: "cancelled"}`. */
export const cancelTurn = (dialogId: string): Promise<null> => api.post(`${BASE}/dialogs/${id(dialogId)}/cancel`, {});

// ---- paths for stream.ts ----

export const TURNS_PATH = `${BASE}/turns`;
export const toolResultsPath = (dialogId: string): string => `${BASE}/dialogs/${id(dialogId)}/tool_results`;
export const streamPath = (dialogId: string, after: number): string => `${BASE}/dialogs/${id(dialogId)}/stream?after=${Math.max(0, Math.floor(after))}`;
