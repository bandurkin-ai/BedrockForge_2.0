/**
 * The types of the bodies and the answers of the Claude assistant API (ТЗ 7.32; ADR 0029; docs/progress/e23-api.md §2-§9). The forms are those of the
 * contract (`core/bf_core/assistant/schemas*.py`, samples — `core/tests/data/assistant/contract_answers.json`). A gap is `null` — never 0. The events
 * of the turn stream (§10) are in `wireTypes.ts` (23.13). No secret is ever part of an answer of the window (§1). No React, no I/O.
 */
import type { ToolDefinition } from "../../shell/commands";

export type { ToolDefinition };

// ---- limits of the contract (the core checks them; the window keeps its input within them) ----

export const LIMITS = {
  text: 20_000,
  title: 200,
  code: 4096,
  toolText: 4000,
  context: 4096,
  contextString: 200,
  contextWindows: 20,
  tools: 400,
  toolsBytes: 256 * 1024,
  toolDescription: 2000,
  page: 200,
} as const;

/** Page size of `GET /dialogs` (§7). */
export const DIALOGS_PAGE = 50;

export type Effort = "low" | "medium" | "high" | "xhigh" | "max";
export const EFFORTS: readonly Effort[] = ["low", "medium", "high", "xhigh", "max"];

export interface Tokens {
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
}

// ---- §2 status ----

export type ConnectionState = "none" | "connected" | "login";
/** `env` — the key of `ANTHROPIC_API_KEY`; `account` — the login through `ant`. */
export type ConnectionKind = "api_key" | "env" | "account";
export type SettingSource = "prefs" | "env" | "default";

export interface Named {
  id: string;
  name: string | null;
}

/** USD per 1 million tokens. */
export interface Price {
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
}

/** The price of a request above `threshold` input tokens (№ 128). */
export interface LongPrice extends Price {
  threshold: number;
}

export interface ModelInfo {
  id: string;
  title: string;
  default: boolean;
  note: string;
  price: Price;
  long: LongPrice | null;
}

export interface EffortInfo {
  id: Effort;
  title: string;
  default: boolean;
}

export interface Status {
  state: ConnectionState;
  kind: ConnectionKind | null;
  /** the masked key, e.g. `sk-ant-…a1B2`; never the key */
  mask: string | null;
  organization: Named | null;
  workspace: Named | null;
  /** the result of the last call of the API; null — there was none */
  online: boolean | null;
  checked_at: string | null;
  model: string;
  model_source: SettingSource;
  effort: Effort;
  effort_source: SettingSource;
  ant_available: boolean;
  /** the answer of the shell (`bf.claudeStorage()`), the core only passes it on */
  secure_storage: boolean;
  seal_needed: boolean;
  subscription_note: string;
  models: ModelInfo[];
  efforts: EffortInfo[];
  prices_version: string;
}

/** `PUT /prefs`; a key left out stays as it is. */
export interface PrefsBody {
  model?: string;
  effort?: Effort;
}

// ---- §3 connection ----

/** `POST /connection/key` -> `Status`. */
export interface KeyBody {
  key: string;
}

/** `POST /connection/test` (the body is `{}`). */
export interface ConnectionTest {
  ok: boolean;
  latency_ms: number;
  model: string;
  model_available: boolean;
  effort_supported: Effort[] | null;
  organization_id: string | null;
  message: string;
}

/** `POST /login/start` (the body is `{}`). */
export interface LoginStart {
  login_id: string;
  url: string;
  expires_at: string;
}

/** `POST /login/{login_id}/finish` -> `Status`. */
export interface LoginFinishBody {
  code: string;
}

// ---- §4 usage ----

export interface ModelUsage {
  model: string;
  title: string;
  requests: number;
  tokens: Tokens;
  /** null — the model has no price; never 0 */
  cost_usd: number | null;
}

export interface Period {
  requests: number;
  tokens: Tokens;
  /** the sum of the known costs; null — nothing to sum */
  cost_usd: number | null;
  /** calls of a model that has no price */
  unpriced_requests: number;
  by_model: ModelUsage[];
}

export interface Usage {
  today: Period;
  days30: Period;
  all: Period;
  prices_version: string;
  since: string | null;
}

// ---- §6 context of the screen ----

export interface Context {
  project: { uid: string; name: string } | null;
  set: { id: number; name: string; holes: number } | null;
  hole: { id: string; set_id: number } | null;
  panels: string[];
  windows: string[];
  view: { mode: "iso" | "top" | "front" | "side" | "section"; perspective: boolean; vexag: number } | null;
  section: { x0: number; y0: number; az: number; length: number; towards: number; away: number } | null;
  selection: { kind: string; id: number; name: string } | null;
  audit: string | null;
}

// ---- §7 dialogs ----

export type DialogState = "idle" | "running" | "pending" | "error";

export interface DialogHead {
  /** `"<objects.id>"` for a saved dialog, `"t-<uuid>"` for a temporary one */
  id: string;
  uid: string;
  title: string;
  created_at: string;
  last_at: string;
  questions: number;
  cost_usd: number | null;
  tokens: Tokens;
  models: string[];
  state: DialogState;
  temporary: boolean;
  project_uid: string | null;
}

export interface UserItem {
  kind: "user";
  id: string;
  at: string;
  text: string;
}

export interface AssistantItem {
  kind: "assistant";
  id: string;
  at: string;
  model: string;
  /** Markdown (the subset of ADR 0029 решение 34) */
  text: string;
  stopped: boolean;
  interrupted: boolean;
}

/** `waiting` — not answered yet; `declined` — the user refused (a confirmation) or stopped the turn. */
export type ToolStatus = "ok" | "error" | "declined" | "waiting";

export interface ToolItem {
  kind: "tool";
  id: string;
  at: string;
  tool_use_id: string;
  name: string;
  /** `data` — read by the core; `ui` — a command of the window */
  side: "data" | "ui";
  title: string;
  args: Record<string, unknown>;
  status: ToolStatus;
  /** at most 4000 characters */
  summary: string;
}

export interface NoticeItem {
  kind: "notice";
  id: string;
  at: string;
  /** `effort_lowered`, `max_tokens`, `refusal`, `context_trimmed`, `temporary`, `max_rounds` */
  code: string;
  message: string;
}

export interface ErrorItem {
  kind: "error";
  id: string;
  at: string;
  code: string;
  message: string;
  hint: string;
}

export type Item = UserItem | AssistantItem | ToolItem | NoticeItem | ErrorItem;
export type ItemKind = Item["kind"];

/** One command of the window that the model asked for (§10 `pending`). The window finds the command by `name` (`findByToolName`). */
export interface PendingTool {
  tool_use_id: string;
  name: string;
  /** the id of the command if the core knows it; do not rely on it */
  command: string | null;
  args: Record<string, unknown>;
  item_id: string;
}

export interface Pending {
  id: string;
  created_at: string;
  tools: PendingTool[];
}

export interface Dialog {
  head: DialogHead;
  items: Item[];
  pending: Pending | null;
  /** e.g. «Диалог не сохранится: проект не открыт» */
  note: string | null;
}

/** `GET /dialogs?offset=&limit=` */
export interface DialogsPage {
  total: number;
  items: DialogHead[];
}

/** `PATCH /dialogs/{id}`; 1…200 characters. */
export interface RenameBody {
  title: string;
}

// ---- §8-§9 the bodies of a turn (the answer is an SSE stream, `wireTypes.ts`) ----

/** `POST /turns`; `dialog_id: null` — a new dialog; `text` 1…20 000 characters. */
export interface TurnBody {
  dialog_id: string | null;
  text: string;
  context: Context;
  tools: ToolDefinition[];
}

export interface ToolResult {
  tool_use_id: string;
  /** false -> `tool_result.is_error` */
  ok: boolean;
  /** at most 4000 characters (the core cuts with «…») */
  text: string;
  title?: string | null;
}

/** `POST /dialogs/{id}/tool_results`: one result for every `ui` call of the `pending`, once each. */
export interface ToolResultsBody {
  pending_id: string;
  results: ToolResult[];
  context: Context;
  tools: ToolDefinition[];
}

// ---- §14 errors of the routes ----

/** The body of an error of the core (`{code, message, hint}` in Russian, plus machine fields: `param`, `detail`, `expected`, `model`). */
export interface ApiErrorBody {
  code: string;
  message: string;
  hint: string;
  param?: string;
  detail?: string;
  expected?: string[];
  model?: string;
}

/** The fixed texts of the loop (§9, §11), shared with the core (`errors.py`). */
export const TEXT = {
  declined: "Пользователь отклонил действие",
  stopped: "Пользователь остановил ход",
  skipped: "Не выполнено: пользователь продолжил диалог",
} as const;
