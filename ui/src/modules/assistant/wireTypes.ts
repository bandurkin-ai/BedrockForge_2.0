/**
 * Wire types of the Claude assistant (Э23; ADR 0029; docs/progress/e23-api.md §2–§10): the items of the feed, the events of a turn stream, the
 * bodies of the requests the window sends. Written by 23.13 on the contract; `types.ts` (23.1, the replies of the other routes) may supersede the
 * types of §2–§7 when the branches are merged. No React, no runtime code.
 *
 * A gap is `null`, never 0 (a cost of an unknown model is `cost_usd: null`).
 */

// ---- shared ----

export interface TokenCounts {
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
}

export type DialogState = "idle" | "running" | "pending" | "error";

/** Head of a dialog (§7). `id` is `"<objects.id>"` for a saved one and `"t-<uuid>"` for a temporary one. */
export interface DialogHead {
  id: string;
  uid: string;
  title: string;
  created_at: string;
  last_at: string;
  questions: number;
  cost_usd: number | null;
  tokens: TokenCounts;
  models: string[];
  state: DialogState;
  temporary: boolean;
  project_uid: string | null;
}

// ---- items of the feed (§7) ----

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
  /** Markdown (a subset, `markdown.ts`) */
  text: string;
  stopped: boolean;
  interrupted: boolean;
}

export type ToolSide = "data" | "ui";
export type ToolStatus = "ok" | "error" | "declined" | "waiting";

export interface ToolItem {
  kind: "tool";
  id: string;
  at: string;
  tool_use_id: string;
  name: string;
  side: ToolSide;
  title: string;
  args: Record<string, unknown>;
  status: ToolStatus;
  /** ≤ 4000 characters */
  summary: string;
}

export interface NoticeItem {
  kind: "notice";
  id: string;
  at: string;
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

/** One command of the window the model asked for (§10); `command` is the registry id (`findByToolName(name)` on the window side). */
export interface PendingTool {
  tool_use_id: string;
  name: string;
  command: string;
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
  note: string | null;
}

export interface DialogList {
  total: number;
  items: DialogHead[];
}

// ---- events of a turn (§10) ----

export type TurnEndState = "idle" | "pending" | "error" | "cancelled";

export interface UsageCall {
  model: string;
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
  cost_usd: number | null;
  long: boolean;
}

export interface UsageDialog {
  cost_usd: number | null;
  tokens: TokenCounts;
  questions: number;
}

export type TurnEvent =
  | { event: "turn.start"; data: { dialog: DialogHead; segment_id: string; model: string; effort: string; user_item?: UserItem } }
  | { event: "text.delta"; data: { item_id: string; text: string } }
  | { event: "thinking"; data: { state: "start" | "end" } }
  | { event: "tool.start"; data: { item: ToolItem } }
  | { event: "tool.end"; data: { item_id: string; status: ToolStatus; summary: string } }
  | { event: "pending"; data: Pending }
  | { event: "usage"; data: { call: UsageCall; dialog: UsageDialog } }
  | { event: "notice"; data: { item: NoticeItem } }
  | { event: "error"; data: { item: ErrorItem } }
  | { event: "turn.end"; data: { state: TurnEndState; stop_reason: string | null; dialog: DialogHead } };

export type TurnEventName = TurnEvent["event"];

/** An event with its number in the segment (`id:` of the SSE frame, from 1); null — the frame had none. */
export type TurnEnvelope = TurnEvent & { id: number | null };

// ---- bodies of the requests (§5.1, §6, §8, §9) ----

export interface ToolDefinition {
  name: string;
  description: string;
  input_schema: { type: "object"; properties: Record<string, unknown>; required?: string[]; additionalProperties?: boolean };
}

/** The context of the screen (§6), ≤ 4 KB of JSON. */
export interface TurnContext {
  project: { uid: string; name: string } | null;
  set: { id: number; name: string; holes: number } | null;
  hole: { id: string; set_id: number } | null;
  panels: string[];
  windows: string[];
  view: { mode: "iso" | "top" | "front" | "side" | "section"; perspective: boolean; vexag: number };
  section: { x0: number; y0: number; az: number; length: number; towards: number; away: number } | null;
  selection: { kind: string; id: number; name: string } | null;
  audit: string | null;
}

export interface TurnBody {
  dialog_id: string | null;
  text: string;
  context: TurnContext;
  tools: ToolDefinition[];
}

export interface ToolResultIn {
  tool_use_id: string;
  ok: boolean;
  /** ≤ 4000 characters (the core cuts with «…») */
  text: string;
  title?: string;
}

export interface ToolResultsBody {
  pending_id: string;
  results: ToolResultIn[];
  context: TurnContext;
  tools: ToolDefinition[];
}

// ---- status, prefs, usage (§2–§4) ----

export interface ModelPrice {
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
}

export interface ModelInfo {
  id: string;
  title: string;
  default?: boolean;
  note: string;
  price: ModelPrice;
  long: (ModelPrice & { threshold: number }) | null;
}

export interface EffortInfo {
  id: string;
  title: string;
  default?: boolean;
}

export type ConnectionState = "none" | "connected" | "login";
export type ConnectionKind = "api_key" | "env" | "account";

export interface AssistantStatus {
  state: ConnectionState;
  kind: ConnectionKind | null;
  mask: string | null;
  organization: { id: string; name: string | null } | null;
  workspace: { id: string; name: string | null } | null;
  /** the result of the last call of the API; null — there was none */
  online: boolean | null;
  checked_at: string | null;
  model: string;
  model_source: "prefs" | "env" | "default";
  effort: string;
  effort_source: "prefs" | "env" | "default";
  ant_available: boolean;
  secure_storage: boolean;
  seal_needed: boolean;
  subscription_note: string;
  models: ModelInfo[];
  efforts: EffortInfo[];
  prices_version: string;
}

export interface PrefsBody {
  model?: string;
  effort?: string;
}

export interface TestResult {
  ok: boolean;
  latency_ms: number;
  model: string;
  model_available: boolean;
  effort_supported: string[] | null;
  organization_id: string | null;
  message: string;
}

export interface LoginStarted {
  login_id: string;
  url: string;
  expires_at: string;
}

export interface UsageModelRow {
  model: string;
  title: string;
  requests: number;
  tokens: TokenCounts;
  cost_usd: number | null;
}

export interface UsagePeriod {
  requests: number;
  tokens: TokenCounts;
  cost_usd: number | null;
  /** requests of an unknown model (their cost is not in `cost_usd`) */
  unpriced_requests?: number;
  by_model: UsageModelRow[];
}

export interface UsageReport {
  today: UsagePeriod;
  days30: UsagePeriod;
  all: UsagePeriod;
  prices_version: string;
  since: string | null;
}
