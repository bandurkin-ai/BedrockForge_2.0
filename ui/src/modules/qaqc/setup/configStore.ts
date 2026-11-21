/**
 * The setup of the project and the CRM reference as the window sees them (ADR 0020 §3, §9–10): the stored values
 * (read once the window opens, again on `qaqc.changed` of a newer version), the version a form was started from
 * (`expected_version` of its «Сохранить»), a 409 `version_conflict` — the stored values read again and every form
 * of that part reset to them, a 422 at its threshold (`param`) or at its row of the reference (`bad_crm {row}`).
 * `qaqc.config` and `qaqc.crm` are handled here; the core sends `qaqc.changed` so the tab «QAQC» computes anew.
 */
import { CoreError } from "../../../shell/api";
import { refusal } from "../../../shell/objectRun";
import { errorText } from "../../../shell/props";
import { Store } from "../../../shell/store";
import { getConfig, getCrm, putConfig, putCrm, type CrmBook, type CrmRow, type QaqcConfig } from "../setupApi";
import { applyConfigArgs, applyCrmArgs, crmKey, DEFAULT_LIMITS, LIMIT_SPECS } from "./forms";

export type Part = "config" | "crm";

export interface ConfigState {
  config: QaqcConfig | null;
  crm: CrmBook | null;
  error: string | null;
  /** a 409: the message, and a counter the forms reset on */
  conflict: { part: Part; message: string; seq: number } | null;
  /** a 422 of the setup: the threshold it names (null — the whole form) */
  configError: { param: string | null; message: string } | null;
  /** a 422 of the reference: the key of the row it names */
  crmError: { key: string | null; message: string } | null;
  /** the version each form was started from (`expected_version` of the interface's save) */
  base: Partial<Record<Part, number>>;
}

const EMPTY: ConfigState = { config: null, crm: null, error: null, conflict: null, configError: null, crmError: null, base: {} };
export const configStore = new Store<ConfigState>({ ...EMPTY });

let seq = 0;
const reads: Record<Part, number> = { config: 0, crm: 0 };

export function resetConfig(): void {
  reads.config++;
  reads.crm++;
  configStore.set({ ...EMPTY });
}

/** The stored setup with every threshold (the core may leave out the ones at their defaults). */
const full = (c: QaqcConfig): QaqcConfig => ({ ...c, types: c.types ?? {}, dl: c.dl ?? [], limits: { ...DEFAULT_LIMITS, ...c.limits }, empty_is_orig: c.empty_is_orig ?? true });

export async function loadConfig(): Promise<QaqcConfig> {
  const mine = ++reads.config;
  const c = full(await getConfig());
  if (mine === reads.config) configStore.set({ config: c, error: null });
  return c;
}

export async function loadCrm(): Promise<CrmBook> {
  const mine = ++reads.crm;
  const b = await getCrm();
  const book = { version: b.version, rows: b.rows ?? [] };
  if (mine === reads.crm) configStore.set({ crm: book, error: null });
  return book;
}

/** Reads what the window has not read yet (a failure is shown in the window). */
export function ensureLoaded(): void {
  const s = configStore.get();
  if (!s.config) void loadConfig().catch((e: unknown) => configStore.set({ error: errorText(e) }));
  if (!s.crm) void loadCrm().catch((e: unknown) => configStore.set({ error: errorText(e) }));
}

/** `qaqc.changed {what, version}` of the core: a newer version is read again (forms without edits follow it). */
export function configChanged(data: string): void {
  let d: { what?: unknown; version?: unknown } = {};
  try {
    d = JSON.parse(data) as typeof d;
  } catch {
    /* read both */
  }
  const s = configStore.get();
  const what = d.what === "crm" || d.what === "config" ? [d.what] : (["config", "crm"] as Part[]);
  for (const p of what) {
    const known = p === "config" ? s.config : s.crm;
    if (!known || (typeof d.version === "number" && d.version === known.version)) continue;
    void (p === "config" ? loadConfig() : loadCrm()).catch(() => undefined);
  }
}

/** A form starts from the stored version: its «Сохранить» expects it. */
export function setBase(part: Part, version: number | null): void {
  configStore.set((s) => ({ base: { ...s.base, [part]: version ?? undefined } }));
}

/** A 409: the stored values are read again, the forms of the part reset to them. */
async function conflict(part: Part): Promise<never> {
  try {
    await (part === "config" ? loadConfig() : loadCrm());
  } catch {
    /* the message stands */
  }
  const message = part === "config" ? "Настройку QAQC изменили в другом окне: показаны сохранённые значения, ваши правки не записаны." : "Справочник CRM изменили в другом окне: показаны сохранённые строки, ваши правки не записаны.";
  configStore.set({ conflict: { part, message, seq: ++seq } });
  throw refusal("version_conflict", message, "Повторите правку и сохраните ещё раз.");
}

const changedLimits = (a: QaqcConfig, b: Omit<QaqcConfig, "version">) => LIMIT_SPECS.filter((s) => a.limits[s.key] !== b.limits[s.key]).map((s) => `${s.key} = ${b.limits[s.key]}`);

/** `qaqc.config` (ADR 0020 §9): over the stored setup; the interface's save expects the version of its form. */
export async function runConfig(args: Record<string, unknown>, by: "user" | "claude"): Promise<string> {
  const s = configStore.get();
  const base = by === "user" && s.config ? s.config : await loadConfig();
  const next = applyConfigArgs(base, args);
  if (typeof next === "string") {
    configStore.set({ configError: { param: LIMIT_SPECS.find((x) => next.startsWith(x.title))?.key ?? null, message: next } });
    throw refusal("bad_param", `${next}.`, "Диапазоны порогов — ТЗ 8.4.2; «Пороги → По умолчанию» возвращает значения ТЗ.");
  }
  const expected = by === "user" ? (s.base.config ?? base.version) : base.version;
  let saved: QaqcConfig;
  try {
    saved = full(await putConfig(next, expected));
  } catch (e) {
    if (e instanceof CoreError && e.status === 409) return conflict("config");
    if (e instanceof CoreError && e.status === 422) configStore.set({ configError: { param: typeof e.body?.param === "string" ? e.body.param : null, message: errorText(e) } });
    throw e;
  }
  configStore.set((st) => ({ config: saved, configError: null, conflict: st.conflict?.part === "config" ? null : st.conflict, base: { ...st.base, config: saved.version } }));
  const parts = [
    ...(args.types !== undefined ? [`типов кодов: ${Object.keys(saved.types).length}`] : []),
    ...(args.dl !== undefined ? [`строк DL: ${saved.dl.length}`] : []),
    ...changedLimits(base, saved),
    ...(saved.empty_is_orig !== base.empty_is_orig ? [`пустой код — ${saved.empty_is_orig ? "ORIG" : "не распознан"}`] : []),
  ];
  return `Настройка QAQC сохранена (версия ${saved.version})${parts.length ? `: ${parts.join("; ")}` : ""}; отчёты по прежней настройке устарели`;
}

/** `qaqc.crm` (ADR 0020 §9): rows upserted by CRM_ID × element, `remove` taken out. */
export async function runCrm(args: Record<string, unknown>, by: "user" | "claude"): Promise<string> {
  const s = configStore.get();
  const base = by === "user" && s.crm ? s.crm : await loadCrm();
  const next = applyCrmArgs(base.rows, args);
  if (typeof next === "string") {
    configStore.set({ crmError: { key: null, message: next } });
    throw refusal("bad_crm", `${next}.`, "У каждой строки — CRM_ID, элемент, μ > 0 и σ > 0.");
  }
  const expected = by === "user" ? (s.base.crm ?? base.version) : base.version;
  let saved: CrmBook;
  try {
    saved = await putCrm(next, expected);
  } catch (e) {
    if (e instanceof CoreError && e.status === 409) return conflict("crm");
    if (e instanceof CoreError && e.status === 422) {
      const row = typeof e.body?.row === "number" ? next[e.body.row] : undefined;
      configStore.set({ crmError: { key: row ? crmKey(row) : null, message: errorText(e) } });
    }
    throw e;
  }
  const book = { version: saved.version, rows: saved.rows ?? next };
  configStore.set((st) => ({ crm: book, crmError: null, conflict: st.conflict?.part === "crm" ? null : st.conflict, base: { ...st.base, crm: book.version } }));
  const n = (k: (r: CrmRow) => string) => new Set(book.rows.map(k)).size;
  return `Справочник CRM сохранён (версия ${book.version}): строк ${book.rows.length}, стандартов ${n((r) => r.crm_id.toUpperCase())}, элементов ${n((r) => r.element.toUpperCase())}; отчёты по прежнему справочнику устарели`;
}

export const clearErrors = (part: Part): void => configStore.set(part === "config" ? { configError: null } : { crmError: null });
