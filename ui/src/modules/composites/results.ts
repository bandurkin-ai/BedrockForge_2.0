/**
 * Results of sample processing in the data tree and «Свойства» (ТЗ 8.0.2, 8.2.3; ADR 0018 §2, §10–11): the results
 * of every set read from `GET /composites/results?set_id=` and kept by set, read again on `composites.changed
 * {set_id}` (a job wrote or a result was deleted) and on `drillholes.changed` (a source table changed — «устарел»
 * moves); a new project forgets them. Pure helpers: the menu of a result, «Параметры…» → the arguments of its tool
 * command, the facts of «Свойства», the mark of a table tab. No React.
 */
import { CoreError } from "../../shell/api";
import { Store } from "../../shell/store";
import { onCoreEvent } from "../jobs/state";
import { projectStore } from "../projects/state";
import { loadResults, type ResultRow, type TableRow } from "./api";
import { numberText } from "./params";
import { summaryRows } from "./tools/composite";
import { toolById } from "./tools";

export interface ResultsState {
  /** set id → its results, newest first; a set not read yet is absent */
  bySet: Record<number, ResultRow[]>;
  /** set id → the Russian text of a failed read */
  errors: Record<number, string>;
}

export const resultsStore = new Store<ResultsState>({ bySet: {}, errors: {} });

const inflight = new Map<number, Promise<void>>();
/** Grows on a new project: a read answered for the previous one is dropped. */
let epoch = 0;

/** Reads the results of a set; a set the core has no results route for (404) has none. */
export function loadSetResults(setId: number): Promise<void> {
  const running = inflight.get(setId);
  if (running) return running;
  const mine = epoch;
  const p = loadResults(setId)
    .then(
      (rows) => {
        if (mine !== epoch) return;
        resultsStore.set((s) => {
          const errors = { ...s.errors };
          delete errors[setId];
          return { bySet: { ...s.bySet, [setId]: rows }, errors };
        });
      },
      (e: unknown) => {
        if (mine !== epoch) return;
        const missing = e instanceof CoreError && e.status === 404;
        resultsStore.set((s) => ({ bySet: { ...s.bySet, [setId]: missing ? [] : (s.bySet[setId] ?? []) }, errors: missing ? s.errors : { ...s.errors, [setId]: e instanceof CoreError ? e.full : String(e) } }));
      },
    )
    .finally(() => inflight.delete(setId));
  inflight.set(setId, p);
  return p;
}

/** Forgets every set (another project). */
export function resetResults(): void {
  epoch += 1;
  inflight.clear();
  resultsStore.set({ bySet: {}, errors: {} });
}

/** The set an event is about: `composites.changed {set_id}`, `drillholes.changed {set_id, table, rev}`. */
export function eventSet(event: string, data: string): number | null {
  if (event !== "composites.changed" && event !== "drillholes.changed") return null;
  try {
    const d = JSON.parse(data) as { set_id?: unknown };
    return typeof d.set_id === "number" ? d.set_id : null;
  } catch {
    return null;
  }
}

let watching = false;

/** Keeps the read sets current (once per window; the tree calls it when it is shown). */
export function watchResults(): void {
  if (watching) return;
  watching = true;
  onCoreEvent((event, data) => {
    const id = eventSet(event, data);
    if (id !== null && resultsStore.get().bySet[id] !== undefined) void loadSetResults(id);
  });
  let uid = projectStore.get().current?.uid ?? null;
  projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    resetResults();
  });
}

export const findResult = (id: number): ResultRow | null => {
  for (const rows of Object.values(resultsStore.get().bySet)) {
    const hit = rows.find((r) => r.id === id);
    if (hit) return hit;
  }
  return null;
};

// ---- a result as the tree shows it ----

/** The columns a result wrote in place: `report.written` — `[{table, columns}]` (core) or plain names. */
export function writtenColumns(r: Pick<ResultRow, "written" | "report">): string[] {
  const raw = (Array.isArray(r.written) && r.written.length ? r.written : r.report?.written) as unknown;
  if (!Array.isArray(raw)) return [];
  const out: string[] = [];
  for (const w of raw) {
    if (typeof w === "string") out.push(w);
    else if (w && typeof w === "object" && Array.isArray((w as { columns?: unknown }).columns)) out.push(...((w as { columns: unknown[] }).columns.map(String)));
  }
  return out;
}

/** «Композиты 2 м» — the name; a result of fields — «Урезка · AU_CUT, AU_CUT_F» when it has no name. */
export function resultLabel(r: ResultRow): string {
  if (r.name.trim()) return r.name;
  const cols = writtenColumns(r);
  return cols.length ? `${r.op_title} · ${cols.join(", ")}` : r.op_title;
}

/** «устарел: изменена таблица „Пробы“»; null — fresh. */
export const staleText = (r: Pick<ResultRow, "stale" | "reason">): string | null => (r.stale ? `устарел${r.reason ? `: ${r.reason}` : ""}` : null);

/** The number of manual edits a rebuild loses (Д-26: `report.edits`). */
export const manualEdits = (r: ResultRow): number => (Array.isArray(r.report?.edits) ? (r.report.edits as unknown[]).length : 0);

/** The tool of an operation (ADR 0018 §3): the three composite methods share one window. */
export function toolOfOp(op: string): string | null {
  const id = op === "length" || op === "domain" || op === "bench" ? "composite" : op;
  return toolById(id) ? id : null;
}

/** The name of the source table of a result (`inputs.tables[0].name`). */
export function sourceTable(r: ResultRow): string | null {
  const t = (r.inputs?.tables as { name?: unknown }[] | undefined)?.[0];
  return typeof t?.name === "string" ? t.name : null;
}

const listText = (v: unknown[]): string => v.map(String).join(", ");

/**
 * «Параметры…»: the arguments of the tool command with the values the core ran with (ADR 0018 §2: `params`) —
 * flat as the commands take them: lists through commas, `domain {table, field}` → `domain`, `domain_table`,
 * `domain {wireframe_ids}` → `domain_wireframes`; the composite method from the operation; the source table by name.
 */
export function paramsArgs(r: ResultRow): Record<string, unknown> {
  const args: Record<string, unknown> = { set_id: r.set_id };
  for (const [k, v] of Object.entries(r.params ?? {})) {
    if (v === null || v === undefined || k === "replace") continue;
    if (k === "domain" && typeof v === "object" && !Array.isArray(v)) {
      const d = v as { table?: unknown; field?: unknown; wireframe_ids?: unknown };
      if (typeof d.field === "string") {
        args.domain = d.field;
        if (typeof d.table === "string") args.domain_table = d.table;
      } else if (Array.isArray(d.wireframe_ids)) args.domain_wireframes = listText(d.wireframe_ids);
    } else if (k === "others" && Array.isArray(v)) {
      args.others = v
        .map((o: { field?: unknown; price?: unknown; recovery?: unknown }) => `${String(o.field ?? "")} ${typeof o.price === "number" ? numberText(o.price) : String(o.price ?? "")} ${typeof o.recovery === "number" ? numberText(o.recovery) : String(o.recovery ?? "")}`.trim())
        .join("; ");
    } else if (Array.isArray(v)) args[k] = k === "cutoffs" || k === "m_min" || k === "m_waste_max" ? v.map((x) => (typeof x === "number" ? numberText(x) : String(x))).join("; ") : listText(v);
    else if (typeof v !== "object") args[k] = v;
  }
  if (r.op === "length" || r.op === "domain" || r.op === "bench") args.method = r.op;
  if (args.table === undefined && args.tables === undefined) {
    const t = sourceTable(r);
    if (t) args.table = t;
  }
  args.set_id = r.set_id;
  return args;
}

// ---- the menu of a result (ADR 0018 §2: «Открыть таблицу», «Параметры…», «Перестроить», «Свойства», «Удалить») ----

export type ResultAction = "open" | "params" | "rebuild" | "props" | "delete";

export interface ResultMenuItem {
  action: ResultAction;
  label: string;
  /** why the item is unavailable; null — available */
  reason: string | null;
}

export function resultMenu(r: ResultRow, readOnly: boolean): ResultMenuItem[] {
  const ro = readOnly ? "Проект открыт только для чтения" : null;
  return [
    { action: "open", label: "Открыть таблицу", reason: r.table_id === null ? "У результата нет таблицы — отчёт в «Свойствах»" : null },
    { action: "params", label: "Параметры…", reason: toolOfOp(r.op) ? ro : "Окна у этой операции нет" },
    { action: "rebuild", label: "Перестроить", reason: ro },
    { action: "props", label: "Свойства", reason: null },
    { action: "delete", label: "Удалить…", reason: ro },
  ];
}

/** «Перестроить» of a result with manual edits warns first (ADR 0018 §12). */
export const rebuildWarning = (r: ResultRow): string | null => {
  const n = manualEdits(r);
  return n ? `Ручных правок: ${n}. При перестроении они будут потеряны.` : null;
};

// ---- «Свойства» ----

export interface Fact {
  label: string;
  value: string;
}

const valueText = (v: unknown): string => {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "number") return numberText(v);
  if (typeof v === "boolean") return v ? "да" : "нет";
  if (Array.isArray(v)) return v.map(valueText).join(", ");
  if (typeof v === "object") {
    const o = v as Record<string, unknown>;
    if (typeof o.field === "string") return `${o.table ? `${String(o.table)} › ` : ""}${o.field}`;
    if (Array.isArray(o.wireframe_ids)) return `каркасы ${o.wireframe_ids.join(", ")}`;
    return Object.entries(o)
      .map(([k, x]) => `${k} ${valueText(x)}`)
      .join("; ");
  }
  return String(v);
};

/** The label of a parameter as the tool form names it, else its key. */
function paramLabel(r: ResultRow, key: string): string {
  const id = toolOfOp(r.op);
  const p = id ? toolById(id)?.params.find((x) => x.key === key) : undefined;
  return p ? `${p.label}${p.unit ? `, ${p.unit}` : ""}` : key;
}

const dateText = (iso: string | null): string => {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("ru-RU");
};

/** Source of a result: tables with their revisions and fields, the set version, the wireframes. */
export function sourceFacts(r: ResultRow): Fact[] {
  const out: Fact[] = [];
  for (const t of (r.inputs?.tables as { name?: unknown; rev?: unknown; fields?: unknown }[] | undefined) ?? []) {
    const fields = Array.isArray(t.fields) && t.fields.length ? ` · ${t.fields.join(", ")}` : "";
    out.push({ label: "Таблица", value: `${String(t.name ?? "—")} (правка ${String(t.rev ?? "—")})${fields}` });
  }
  const set = r.inputs?.set as { version?: unknown } | null | undefined;
  if (set && set.version !== undefined) out.push({ label: "Устья и инклинометрия", value: `версия ${String(set.version)}` });
  for (const m of (r.inputs?.meshes as { name?: unknown }[] | undefined) ?? []) out.push({ label: "Каркас", value: String(m.name ?? "—") });
  return out;
}

export function paramFacts(r: ResultRow): Fact[] {
  return Object.entries(r.params ?? {})
    .filter(([k]) => k !== "set_id" && k !== "replace")
    .map(([k, v]) => ({ label: paramLabel(r, k), value: valueText(v) }));
}

/** What was made, the hash, the version of the algorithm, the time. */
export function recordFacts(r: ResultRow): Fact[] {
  const out: Fact[] = [
    { label: "Операция", value: r.op_title },
    { label: "Состояние", value: staleText(r) ?? "актуален" },
  ];
  if (r.table) out.push({ label: "Таблица-результат", value: r.table });
  const cols = writtenColumns(r);
  if (cols.length) out.push({ label: "Поля на месте", value: cols.join(", ") });
  out.push(
    { label: "SHA-256 входов", value: r.sha256 || "—" },
    { label: "Версия алгоритма", value: r.algo || "—" },
    { label: "Построен", value: dateText(r.created_at) },
  );
  if (typeof r.report?.seconds === "number") out.push({ label: "Расчёт, с", value: numberText(r.report.seconds as number) });
  if (r.job_id !== null) out.push({ label: "Задание", value: `№${r.job_id}` });
  return out;
}

/** The summary of the report: «до/после» rows when the report holds one (`report.summary {before, after}`), and
 * the plain numbers and texts of the report beside it. */
export function reportFacts(r: ResultRow): { summary: ReturnType<typeof summaryRows>; facts: Fact[] } {
  const rep = r.report ?? {};
  const s = rep.summary as { before?: unknown; after?: unknown } | undefined;
  const summary = s && typeof s.before === "object" && typeof s.after === "object" ? summaryRows(s as Parameters<typeof summaryRows>[0]) : [];
  const skip = new Set(["summary", "written", "edits", "seconds", "versions"]);
  const facts = Object.entries(rep)
    .filter(([k, v]) => !skip.has(k) && (typeof v === "number" || typeof v === "string" || typeof v === "boolean"))
    .map(([k, v]) => ({ label: k, value: valueText(v) }));
  return { summary, facts };
}

// ---- the mark on a table tab ----

/** The mark of a result table on its tab and in a list: «устарел» with the reason; a fresh result — «результат». */
export function tabMark(origin: TableRow["origin"] | undefined): { text: string; title: string; stale: boolean } | null {
  if (!origin) return null;
  if (origin.stale) return { text: "устарел", title: `Результат обработки проб устарел${origin.reason ? `: ${origin.reason}` : ""}. «Перестроить» — в меню результата в дереве.`, stale: true };
  return { text: "", title: "Результат обработки проб (дерево набора › «Обработка проб»)", stale: false };
}
