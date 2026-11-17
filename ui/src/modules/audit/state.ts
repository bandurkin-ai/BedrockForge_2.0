/** Audit of the active set (ТЗ 7.12): the last result, thresholds, findings with filters. No React. */
import { api, CoreError } from "../../shell/api";
import { Store } from "../../shell/store";
import { tablesStore } from "../tables/state";

export type Severity = "error" | "warning" | "info";

export interface RuleSummary {
  code: string;
  severity: Severity;
  table: "collar" | "survey" | "assay";
  title: string;
  description: string;
  rows: number;
  holes: number;
}

export interface AuditResult {
  run_id: number;
  table: string | null;
  table_name: string;
  config: AuditConfig;
  summary: RuleSummary[];
  totals: Record<Severity, number>;
  stale: boolean;
  created_at: string;
}

export interface AuditConfig {
  depth_tolerance: number;
  dogleg_per_30m: number;
  isolated_factor: number;
  z_jump: number;
  assay_outlier_z: number;
  planned_markers: string[];
}

export interface Finding {
  code: string;
  severity: Severity;
  table: string;
  hole: string | null;
  count: number;
  examples: Record<string, unknown>[];
}

export interface AuditState {
  setId: number | null;
  result: AuditResult | null;
  settings: { config: AuditConfig; table: string | null } | null;
  tables: { table: string; name: string; kind: string; rows: number }[];
  filters: { severity: Severity | ""; table: string; code: string; hole: string };
  findings: Finding[];
  total: number;
  error: string | null;
  loading: boolean;
}

export const SEVERITY_TITLES: Record<Severity, string> = { error: "Критично", warning: "Предупреждение", info: "Информация" };
export const TABLE_TITLES: Record<string, string> = { collar: "Устья", survey: "Инклинометрия", assay: "Пробы" };

export const auditStore = new Store<AuditState>({
  setId: null, result: null, settings: null, tables: [], filters: { severity: "", table: "", code: "", hole: "" },
  findings: [], total: 0, error: null, loading: false,
});

const text = (e: unknown) => (e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e));

export async function loadAudit(setId: number | null = tablesStore.get().setId): Promise<void> {
  if (setId === null) {
    auditStore.set({ setId: null, result: null, settings: null, tables: [], findings: [], total: 0 });
    return;
  }
  auditStore.set({ setId, loading: true });
  try {
    await loadFieldNames();
    const r = await api.get<{ result: AuditResult | null; settings: AuditState["settings"]; tables: AuditState["tables"] }>(`/audit/sets/${setId}`);
    auditStore.set({ result: r.result, settings: r.settings, tables: r.tables, error: null });
    await loadFindings();
  } catch (e) {
    auditStore.set({ error: text(e) });
  } finally {
    auditStore.set({ loading: false });
  }
}

export const FINDINGS_PAGE = 200;
/** The latest request wins: an older answer arriving late never replaces a newer one. */
let request = 0;

/** Findings by the filters; `more` appends the next page (offset = shown). */
export async function loadFindings(more = false): Promise<void> {
  const s = auditStore.get();
  if (s.setId === null || !s.result) {
    auditStore.set({ findings: [], total: 0 });
    return;
  }
  const q = new URLSearchParams({ limit: String(FINDINGS_PAGE), offset: String(more ? s.findings.length : 0) });
  for (const [k, v] of Object.entries(s.filters)) if (v) q.set(k === "hole" ? "hole_prefix" : k, v.trim());
  const mine = ++request;
  try {
    const r = await api.get<{ total: number; findings: Finding[] }>(`/audit/sets/${s.setId}/findings?${q}`);
    if (mine !== request) return;
    auditStore.set((st) => ({ findings: more ? [...st.findings, ...r.findings] : r.findings, total: r.total, error: null }));
  } catch (e) {
    if (mine === request) auditStore.set({ error: text(e) });
  }
}

let typing: ReturnType<typeof setTimeout> | null = null;

/** Filters; the hole field waits for a pause in typing (no request per key). */
export async function setFilter(patch: Partial<AuditState["filters"]>): Promise<void> {
  auditStore.set((s) => ({ filters: { ...s.filters, ...patch } }));
  if (typing) clearTimeout(typing);
  if ("hole" in patch && Object.keys(patch).length === 1) {
    await new Promise<void>((resolve) => {
      typing = setTimeout(() => {
        typing = null;
        void loadFindings().then(resolve);
      }, 300);
    });
    return;
  }
  await loadFindings();
}

export async function saveSettings(config: Partial<AuditConfig>, table: string | null): Promise<string | null> {
  const s = auditStore.get();
  try {
    const r = await api.put<AuditState["settings"]>(`/audit/sets/${s.setId}/settings`, { config: { ...s.settings?.config, ...config }, table });
    auditStore.set({ settings: r });
    return null;
  } catch (e) {
    return text(e);
  }
}

export async function runAudit(): Promise<{ source: string; id: number }> {
  const s = auditStore.get();
  if (s.setId === null) throw new Error("Нет активного набора скважин");
  try {
    return await api.post<{ source: string; id: number }>(`/audit/sets/${s.setId}/run`, {});
  } catch (e) {
    throw new Error(text(e));
  }
}

/** «Аудит: 3 критично, 12 предупреждений (устарел)» for the status bar. */
export function statusText(r: AuditResult | null): string {
  if (!r) return "Аудит не запускался";
  const parts = [`${r.totals.error} критично`, `${r.totals.warning} предупр.`, `${r.totals.info} инф.`];
  return `Аудит: ${parts.join(", ")}${r.stale ? " (устарел)" : ""}`;
}

/** Russian names of the fields of examples (from /audit/rules). */
let fieldNames: Record<string, string> = {};
export async function loadFieldNames(): Promise<void> {
  if (Object.keys(fieldNames).length) return;
  try {
    fieldNames = (await api.get<{ fields: Record<string, string> }>("/audit/rules")).fields;
  } catch {
    /* keys are shown as they are */
  }
}

export function exampleText(e: Record<string, unknown>, names: Record<string, string> = fieldNames): string {
  return Object.entries(e)
    .filter(([k, v]) => k !== "row_id" && v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${names[k] ?? k}: ${typeof v === "number" ? v.toLocaleString("ru-RU", { maximumFractionDigits: 4 }) : String(v)}`)
    .join(", ");
}
