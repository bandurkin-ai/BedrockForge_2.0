/**
 * The client of the graph of dependencies (Д-35; ADR 0025 §5, §7–10; подшаг 18.Г3). A node is `{kind, id, sub?}`:
 * `dh_set`, `dh_table`, `dh_result`, `wireframe`, `bm_field` (id — the model, sub — the field) or `object:<kind>`.
 *
 * - `POST /lineage/status {nodes}` (≤ 5000) → `{items: [{kind, id, sub, state, name, reason, changed}]}` in the order asked;
 * - `GET /lineage/impact?kind&id&sub` → `{total, by_kind, nodes: [{kind, id, sub, name, depth}], truncated}`;
 * - `GET /lineage/origin?kind&id&sub` → `{node, inputs: [{kind, id, sub, name, fp, fp_now, state, reason}]}`;
 * - `POST /lineage/pin {node, pinned}` → the status of the node (the event `lineage.changed {nodes}`);
 * - `POST /lineage/rebuild {node, with_dependents, drop_lost, dry_run}` → 200 `{plan, skipped}` (dry run) or 202
 *   `{job_id, source, id, plan, skipped}`; 409 `lineage_node_building | lineage_node_pinned | dependents_lost {lost} |
 *   source_lost`, 422 `lineage_rebuild_unsupported`, 423 (the project is read-only).
 *
 * Errors are `{code, message, hint}` in Russian. A gap of an answer stays `null`. No React.
 */
import { api, CoreError } from "../../shell/api";
import { startedOf, type Started } from "../qaqc/setupApi";

export type { Started };
export { refusalOf } from "../blocks/report/api";

export interface LNode {
  kind: string;
  id: number;
  sub?: string;
}

export type LState = "current" | "stale" | "source_lost" | "pinned" | "building" | "deleted";
export const STATES: readonly LState[] = ["current", "stale", "source_lost", "pinned", "building", "deleted"];

/** The words of the states (ТЗ Д-35: «актуален», «устарел», «источник удалён», «закреплён»). */
export const STATE_TITLES: Record<LState, string> = {
  current: "актуален",
  stale: "устарел",
  source_lost: "источник удалён",
  pinned: "закреплён",
  building: "считается",
  deleted: "удалён",
};

/** The kinds of nodes in the words of the core (`lineage/providers.py LABELS`). */
export const KIND_TITLES: Record<string, string> = {
  dh_set: "набор скважин",
  dh_table: "таблица",
  dh_result: "результат обработки",
  wireframe: "каркас",
  bm_field: "поле блочной модели",
  "object:variogram_model": "модель вариограммы",
  "object:trend": "тренд",
  "object:qaqc_data": "данные QAQC",
  "object:qaqc_report": "отчёт QAQC",
  "object:block_model": "блочная модель",
  "object:estimation": "параметры оценки",
  "object:simulation": "моделирование",
  "object:implicit_model": "неявная модель",
  "object:resource_report": "отчёт по ресурсам",
  "object:pit_scenario": "сценарий карьера",
};

export const kindTitle = (kind: string): string => KIND_TITLES[kind] ?? kind.replace(/^object:/, "");

export const nodeKey = (n: LNode): string => `${n.kind}:${n.id}:${n.sub ?? ""}`;

export const sameNode = (a: LNode, b: LNode): boolean => nodeKey(a) === nodeKey(b);

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

export interface LStatus extends LNode {
  sub: string;
  state: LState;
  name: string;
  reason: string | null;
  /** pinned: the inputs changed since (it would be «устарел») */
  changed: boolean;
}

const stateOf = (v: unknown): LState => (STATES as readonly unknown[]).includes(v) ? (v as LState) : "deleted";

export function readNode(raw: unknown): LNode {
  const o = obj(raw);
  return { kind: str(o.kind) ?? "", id: isNum(o.id) ? o.id : 0, sub: str(o.sub) ?? "" };
}

export function readStatus(raw: unknown): LStatus {
  const o = obj(raw);
  const n = readNode(o);
  return { ...n, sub: n.sub ?? "", state: stateOf(o.state), name: str(o.name) ?? "", reason: str(o.reason), changed: o.changed === true };
}

/** The causes of a reason from the nearest input to the source («таблица «A» ← модель «B» ← изменено: …»). */
export const chainOf = (reason: string | null): string[] => (reason ? reason.split(" ← ").filter(Boolean) : []);

/** The text of the tooltip of a state: the state and the chain of the causes, one cause on a line. */
export function stateTitle(s: Pick<LStatus, "state" | "reason" | "changed">): string {
  const head = s.state === "pinned" ? (s.changed ? "закреплён (входы изменились)" : "закреплён") : STATE_TITLES[s.state];
  const chain = chainOf(s.reason);
  return chain.length ? `${head}\n${chain.map((c, i) => (i === 0 ? c : `← ${c}`)).join("\n")}` : head;
}

export const MAX_NODES = 5000;

// ---- status ----

/** The states of the nodes in the order asked (one request per 5000). */
export async function postStatus(nodes: readonly LNode[]): Promise<LStatus[]> {
  const out: LStatus[] = [];
  for (let i = 0; i < nodes.length; i += MAX_NODES) {
    const chunk = nodes.slice(i, i + MAX_NODES).map((n) => ({ kind: n.kind, id: n.id, ...(n.sub ? { sub: n.sub } : {}) }));
    const r = await api.post<Record<string, unknown>>("/lineage/status", { nodes: chunk });
    out.push(...arr(r.items).map(readStatus));
  }
  return out;
}

// ---- impact and origin ----

export interface Dependent extends LNode {
  sub: string;
  name: string;
  /** 1 — built from the node itself */
  depth: number;
}

export interface Impact {
  total: number;
  byKind: Record<string, number>;
  nodes: Dependent[];
  truncated: boolean;
}

const query = (n: LNode): string => `kind=${encodeURIComponent(n.kind)}&id=${n.id}${n.sub ? `&sub=${encodeURIComponent(n.sub)}` : ""}`;

export function readImpact(raw: unknown): Impact {
  const o = obj(raw);
  const byKind: Record<string, number> = {};
  for (const [k, v] of Object.entries(obj(o.by_kind))) if (isNum(v)) byKind[k] = v;
  const nodes = arr(o.nodes).map((x) => {
    const d = obj(x);
    const n = readNode(d);
    return { ...n, sub: n.sub ?? "", name: str(d.name) ?? "", depth: isNum(d.depth) ? d.depth : 1 };
  });
  return { total: isNum(o.total) ? o.total : nodes.length, byKind, nodes, truncated: o.truncated === true };
}

export async function getImpact(node: LNode): Promise<Impact> {
  return readImpact(await api.get<unknown>(`/lineage/impact?${query(node)}`));
}

export interface OriginInput extends LNode {
  sub: string;
  name: string;
  /** the fingerprint the input had when the node was built (null — not kept) */
  fp: string | null;
  /** the fingerprint now (null — the input is gone) */
  fpNow: string | null;
  state: LState;
  reason: string | null;
}

export interface Origin {
  node: LStatus;
  inputs: OriginInput[];
}

export function readOrigin(raw: unknown): Origin {
  const o = obj(raw);
  const inputs = arr(o.inputs).map((x) => {
    const d = obj(x);
    const n = readNode(d);
    return { ...n, sub: n.sub ?? "", name: str(d.name) ?? "", fp: str(d.fp), fpNow: str(d.fp_now), state: stateOf(d.state), reason: str(d.reason) };
  });
  return { node: readStatus(o.node), inputs };
}

export async function getOrigin(node: LNode): Promise<Origin> {
  return readOrigin(await api.get<unknown>(`/lineage/origin?${query(node)}`));
}

// ---- pin ----

export async function postPin(node: LNode, pinned: boolean): Promise<LStatus> {
  return readStatus(await api.post<unknown>("/lineage/pin", { node: { kind: node.kind, id: node.id, ...(node.sub ? { sub: node.sub } : {}) }, pinned }));
}

// ---- rebuild ----

export interface PlanItem extends LNode {
  sub: string;
  name: string;
  state: LState;
  reason: string | null;
  /** `rebuild` — a job of its own; `covered` — made by the job of the item `coveredBy` (a table and its result) */
  action: "rebuild" | "covered";
  coveredBy: number | null;
}

export interface Skipped extends LNode {
  sub: string;
  name: string;
  /** `source_lost` or `unsupported` */
  state: string;
  reason: string;
}

export interface Plan {
  plan: PlanItem[];
  skipped: Skipped[];
}

export interface RebuildOptions {
  withDependents?: boolean;
  dropLost?: boolean;
}

function readPlan(raw: unknown): Plan {
  const o = obj(raw);
  const plan = arr(o.plan).map((x): PlanItem => {
    const d = obj(x);
    const n = readNode(d);
    return { ...n, sub: n.sub ?? "", name: str(d.name) ?? "", state: stateOf(d.state), reason: str(d.reason), action: d.action === "covered" ? "covered" : "rebuild", coveredBy: isNum(d.covered_by) ? d.covered_by : null };
  });
  const skipped = arr(o.skipped).map((x): Skipped => {
    const d = obj(x);
    const n = readNode(d);
    return { ...n, sub: n.sub ?? "", name: str(d.name) ?? "", state: str(d.state) ?? "unsupported", reason: str(d.reason) ?? "" };
  });
  return { plan, skipped };
}

const rebuildBody = (node: LNode, o: RebuildOptions, dry: boolean) => ({
  node: { kind: node.kind, id: node.id, ...(node.sub ? { sub: node.sub } : {}) },
  with_dependents: o.withDependents !== false,
  drop_lost: o.dropLost === true,
  dry_run: dry,
});

/** The plan of «Перестроить с зависимыми»: nothing is started (dry run). */
export async function planRebuild(node: LNode, o: RebuildOptions = {}): Promise<Plan> {
  return readPlan(await api.post<unknown>("/lineage/rebuild", rebuildBody(node, o, true)));
}

export interface RebuildStarted extends Plan {
  job: Started;
}

/** Starts the job `lineage.rebuild` (202). `dependents_lost {lost}` — repeat with `dropLost`. */
export async function startRebuild(node: LNode, o: RebuildOptions = {}): Promise<RebuildStarted> {
  const r = await api.post<Record<string, unknown>>("/lineage/rebuild", rebuildBody(node, o, false));
  return { ...readPlan(r), job: startedOf(r) };
}

/** What a rebuild would lose (`dependents_lost`): the names for the question to the person. */
export interface LostItem {
  name: string;
  title: string;
}

export function lostOf(e: unknown): LostItem[] | null {
  if (!(e instanceof CoreError) || e.code !== "dependents_lost") return null;
  return arr(e.body?.lost).map((x) => {
    const d = obj(x);
    const kind = d.kind === "column" ? "колонка" : (str(d.title) ?? "результат");
    return { name: str(d.name) ?? "", title: kind };
  });
}
