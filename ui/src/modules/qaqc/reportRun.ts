/**
 * The building of a QAQC report (ADR 0020 §7, §8, §10, §11): «расчёт» — the job `qaqc.report` of
 * `POST /qaqc/reports` (or of «Перестроить»); «графики» — each chart of `GET …/charts` drawn by the tab's
 * components by `report_id` and sent as PNG × 2 (`PUT …/images/{key}`), «37 из 120»; «XLSX» — the job
 * `qaqc.report_file` of `POST …/files {kind: "xlsx"}`; then, when a path is given, `POST …/save`. One run at a time;
 * «Отменить» on any step: the job of the step is cancelled (the core leaves nothing of a cancelled calculation),
 * between charts the loop stops. «Повторить» after a failure or a cancel goes on from the step it stopped at; the
 * charts sent before are not drawn again (the keys sent are kept for the session and the version of the report).
 * Also: the list of the reports of the project, their titles, «Сохранить XLSX…» and the answers for the assistant.
 * The steps take their calls from `RunDeps` (the tests give stubs). No React.
 */
import { bridge } from "../../bridge";
import { errorText } from "../../shell/props";
import { refusal } from "../../shell/objectRun";
import { int } from "./format";
import { Store } from "../../shell/store";
import { cancelJob, jobsStore, type Job } from "../jobs/state";
import { createReport, getReport, listReports, putReportImage, rebuildReport, reportCharts, reportFile, saveReport, type QaqcReport, type ReportChart, type ReportChartList, type ReportStale, type ReportStarted, type StartedJob } from "./api";
import { reportDrawer } from "./reportCharts";
import { startedOf, waitJob, type Started } from "./setupApi";

export type ChartsMode = "all" | "flagged" | "none";
export const CHARTS_MODES: readonly ChartsMode[] = ["all", "flagged", "none"];
export const CHARTS_TITLES: Record<ChartsMode, string> = { all: "все графики", flagged: "с предупреждениями и отказами", none: "без графиков" };

export type RunStep = "calc" | "charts" | "xlsx" | "save";
export const STEP_TITLES: Record<RunStep, string> = { calc: "Расчёт", charts: "Графики", xlsx: "Файл XLSX", save: "Сохранение" };

export interface CreateBody {
  data_id: number;
  name?: string;
  elements?: string[];
  filter: Record<string, unknown>;
  charts: ChartsMode;
}

/** What a run does: a new report, «Перестроить» of one, or the rest of a run that stopped. */
export type RunSpec = ({ kind: "create"; body: CreateBody } | { kind: "rebuild"; reportId: number } | { kind: "resume"; reportId: number; from: Exclude<RunStep, "calc"> }) & { name: string; charts: ChartsMode; path?: string | null };

export interface ReportRun {
  spec: RunSpec;
  reportId: number | null;
  step: RunStep;
  state: "running" | "done" | "failed" | "cancelled";
  /** the job of the step (the progress is in the list of jobs) */
  job: Started | null;
  /** charts drawn and sent (with those sent before), of `total`; `omitted` — over the limit of 200 */
  done: number;
  total: number;
  omitted: number;
  /** charts with nothing to draw */
  empty: number;
  cancelling: boolean;
  error: string | null;
  /** where the file was saved */
  path: string | null;
}

export interface RunDeps {
  create(body: CreateBody): Promise<ReportStarted>;
  rebuild(id: number): Promise<ReportStarted>;
  wait(job: Started): Promise<Pick<Job, "status" | "error" | "result">>;
  cancel(job: Started): Promise<void>;
  charts(id: number): Promise<ReportChartList>;
  /** the drawer of the charts `todo` of a report (it may forget a calculation once its charts are drawn) */
  drawer(id: number, todo: readonly ReportChart[]): (chart: ReportChart) => Promise<Uint8Array | null>;
  put(id: number, key: string, png: Uint8Array): Promise<unknown>;
  file(id: number): Promise<StartedJob>;
  save(id: number, path: string): Promise<unknown>;
}

/**
 * Waits for a job; a job seen in the list of jobs that is gone from it (cleared, another project, the core
 * restarted) ends the wait as a failure instead of waiting for ever. `waitJob` of the setup has no cancel yet.
 */
export function waitOrLost(job: Started, wait: (job: Started) => Promise<Pick<Job, "status" | "error" | "result">> = waitJob, store: Pick<typeof jobsStore, "get" | "subscribe"> = jobsStore): Promise<Pick<Job, "status" | "error" | "result">> {
  return new Promise((resolve, reject) => {
    let seen = false;
    const has = () => store.get().jobs.some((j) => j.source === job.source && j.id === job.id);
    const off = store.subscribe(() => {
      if (has()) seen = true;
      else if (seen) {
        off();
        reject(refusal("job_lost", `Задание №${job.id} пропало из списка заданий.`, "Нажмите «Повторить»."));
      }
    });
    seen = has();
    wait(job).then(
      (j) => {
        off();
        resolve(j);
      },
      (e: unknown) => {
        off();
        reject(e);
      },
    );
  });
}

export const DEFAULT_DEPS: RunDeps = {
  create: createReport,
  rebuild: rebuildReport,
  wait: (job) => waitOrLost(job),
  cancel: (job) => cancelJob(job),
  charts: reportCharts,
  drawer: (id, todo) => reportDrawer(id, todo),
  put: putReportImage,
  file: (id) => reportFile(id, "xlsx"),
  save: (id, path) => saveReport(id, "xlsx", path),
};

export const runStore = new Store<{ run: ReportRun | null }>({ run: null });

/** The keys of the charts sent, by report: they are not drawn again by «Повторить». */
const sent = new Map<number, Set<string>>();

/** The control of one run: its cancel, the job of its step; `abandon` stops it at once (another project). */
interface Control {
  cancel: boolean;
  job: Started | null;
  deps: RunDeps;
  abandoned: Promise<never>;
  abandon: () => void;
}
let control: Control | null = null;

const update = (patch: Partial<ReportRun>) => runStore.set((s) => (s.run ? { run: { ...s.run, ...patch } } : s));
export const isRunning = (): boolean => runStore.get().run?.state === "running";

class Stop extends Error {
  constructor(readonly how: "cancelled" | "failed" | "abandoned", message = "") {
    super(message);
  }
}

function newControl(deps: RunDeps): Control {
  let abandon = () => undefined as void;
  const abandoned = new Promise<never>((_, reject) => {
    abandon = () => reject(new Stop("abandoned"));
  });
  abandoned.catch(() => undefined);
  return { cancel: false, job: null, deps, abandoned, abandon };
}

/**
 * Stops the run at once without waiting for its job (another project, the core restarted): it is forgotten, its
 * answers change nothing; the job itself is cancelled when the core still has it.
 */
export function abandonRun(): void {
  const ctl = control;
  control = null;
  runStore.set({ run: null });
  if (!ctl) return;
  ctl.cancel = true;
  if (ctl.job) void ctl.deps.cancel(ctl.job).catch(() => undefined);
  ctl.abandon();
}

/** Waits for the job of a step; a cancel or a failure stops the run, an abandon — at once. */
async function step(ctl: Control, set: (p: Partial<ReportRun>) => void, started: StartedJob): Promise<Pick<Job, "status" | "error" | "result">> {
  const job = startedOf(started as unknown as Record<string, unknown>);
  ctl.job = job;
  set({ job });
  if (ctl.cancel) await ctl.deps.cancel(job).catch(() => undefined);
  const j = await Promise.race([ctl.deps.wait(job), ctl.abandoned]);
  ctl.job = null;
  set({ job: null });
  if (j.status === "cancelled" || ctl.cancel) throw new Stop("cancelled");
  if (j.status !== "done") throw new Stop("failed", j.error ? `${j.error.message}${j.error.hint ? ` ${j.error.hint}` : ""}` : "Задание завершилось ошибкой: подробности — во вкладке «Задания».");
  return j;
}

const checkCancel = (ctl: Control) => {
  if (ctl.cancel) throw new Stop("cancelled");
};

async function drawCharts(ctl: Control, set: (p: Partial<ReportRun>) => void, id: number): Promise<void> {
  const list = await Promise.race([ctl.deps.charts(id), ctl.abandoned]);
  const keys = sent.get(id) ?? new Set<string>();
  sent.set(id, keys);
  set({ total: list.charts.length, omitted: list.omitted, done: 0, empty: 0 });
  const todo = list.charts.filter((c) => !keys.has(c.key));
  const draw = ctl.deps.drawer(id, todo);
  let done = list.charts.length - todo.length;
  let empty = 0;
  set({ done });
  for (const chart of todo) {
    checkCancel(ctl);
    const png = await Promise.race([draw(chart), ctl.abandoned]);
    if (png) await Promise.race([ctl.deps.put(id, chart.key, png), ctl.abandoned]);
    else empty++;
    keys.add(chart.key);
    done++;
    set({ done, empty });
  }
}

/**
 * Runs the steps of the spec; resolves with the run as it ended (done, failed or cancelled; an abandoned one — as
 * it was left). A second run while one goes on is refused.
 */
export async function runReport(spec: RunSpec, deps: RunDeps = DEFAULT_DEPS): Promise<ReportRun> {
  if (isRunning()) throw refusal("report_running", "Отчёт QAQC уже формируется.", "Дождитесь конца или нажмите «Отменить».");
  const ctl = newControl(deps);
  control = ctl;
  // only the run that is current writes to the store: an abandoned one changes nothing
  const set = (p: Partial<ReportRun>) => {
    if (control === ctl) update(p);
  };
  const reportId = spec.kind === "create" ? null : spec.reportId;
  const first: RunStep = spec.kind === "resume" ? spec.from : "calc";
  let last: ReportRun = { spec, reportId, step: first, state: "running", job: null, done: 0, total: 0, omitted: 0, empty: 0, cancelling: false, error: null, path: null };
  runStore.set({ run: last });
  let id = reportId;
  let at: RunStep = first;
  try {
    if (at === "calc") {
      const started = await Promise.race([spec.kind === "rebuild" ? deps.rebuild(spec.reportId) : deps.create((spec as { body: CreateBody }).body), ctl.abandoned]);
      id = started.report_id ?? id;
      set({ reportId: id });
      if (id !== null) sent.delete(id); // a new version: its charts are not sent yet
      await step(ctl, set, started);
      at = "charts";
    }
    if (id === null) throw new Stop("failed", "Ядро не вернуло номер отчёта.");
    if (at === "charts") {
      set({ step: "charts" });
      if (spec.charts !== "none") await drawCharts(ctl, set, id);
      at = "xlsx";
    }
    if (at === "xlsx") {
      checkCancel(ctl);
      set({ step: "xlsx" });
      await step(ctl, set, await Promise.race([deps.file(id), ctl.abandoned]));
      at = "save";
    }
    if (at === "save" && spec.path) {
      checkCancel(ctl);
      set({ step: "save" });
      await Promise.race([deps.save(id, spec.path), ctl.abandoned]);
      set({ path: spec.path });
    }
    set({ state: "done", step: at, cancelling: false });
  } catch (e) {
    const how = e instanceof Stop ? e.how : "failed";
    if (how === "abandoned") return { ...last, state: "cancelled", step: at, reportId: id };
    set({ state: how === "cancelled" ? "cancelled" : "failed", step: at, cancelling: false, error: how === "cancelled" ? null : e instanceof Stop ? e.message : errorText(e), reportId: id });
  } finally {
    if (control === ctl) control = null;
  }
  last = runStore.get().run ?? { ...last, state: "cancelled", step: at };
  return last;
}

/** «Отменить»: the job of the step is cancelled; between the charts the loop stops after the one being drawn. */
export async function cancelRun(): Promise<void> {
  if (!control || !isRunning()) return;
  control.cancel = true;
  update({ cancelling: true });
  if (control.job) await control.deps.cancel(control.job).catch(() => undefined);
}

/** The spec of «Повторить»: a calculation that stopped is made again; else the run goes on from its step. */
export function retrySpec(run: ReportRun): RunSpec {
  if (run.step === "calc" || run.reportId === null) return run.spec.kind === "resume" ? { ...run.spec, kind: "rebuild", reportId: run.spec.reportId } : run.spec;
  return { kind: "resume", reportId: run.reportId, from: run.step, name: run.spec.name, charts: run.spec.charts, path: run.spec.path };
}

/** «Расчёт…», «Графики 37 из 120», «Файл XLSX…», «Готово», «Отменено на шаге …», «Ошибка на шаге …». */
export function runText(run: ReportRun): string {
  const where = run.step === "charts" ? `${STEP_TITLES.charts} ${run.done.toLocaleString("ru-RU")} из ${run.total.toLocaleString("ru-RU")}` : STEP_TITLES[run.step];
  if (run.state === "running") return run.cancelling ? `${where} — отмена…` : run.step === "charts" ? where : `${where}…`;
  if (run.state === "done") return run.path ? `Готово: сохранено ${run.path}` : "Готово";
  if (run.state === "cancelled") return `Отменено: ${where}`;
  return `Ошибка: ${where}${run.error ? ` — ${run.error}` : ""}`;
}

export function resetRun(): void {
  if (!isRunning()) runStore.set({ run: null });
}

// ---- the reports of the project ----

export const reportsStore = new Store<{ list: QaqcReport[] | null; error: string | null; selected: number | null }>({ list: null, error: null, selected: null });

export async function loadReports(): Promise<QaqcReport[] | null> {
  try {
    const list = await listReports();
    const sel = reportsStore.get().selected;
    reportsStore.set({ list, error: null, selected: sel !== null && list.some((r) => r.id === sel) ? sel : null });
    return list;
  } catch (e) {
    reportsStore.set({ error: errorText(e) });
    return null;
  }
}

export function selectReport(id: number | null): void {
  reportsStore.set({ selected: id });
}

export function resetReports(): void {
  abandonRun();
  sent.clear();
  reportsStore.set({ list: null, error: null, selected: null });
}

export const reportsByName = (list: readonly QaqcReport[]): QaqcReport[] => [...list].sort((a, b) => a.name.localeCompare(b.name, "ru"));

const STALE: Record<Exclude<ReportStale, null>, string> = {
  data: "устарел: данные изменились",
  config: "устарел: изменились настройка или справочник CRM",
  source_deleted: "устарел: данные удалены",
};
export const staleTitle = (s: ReportStale | undefined): string => (s ? (STALE[s] ?? `устарел: ${s}`) : "");

const STATES: Record<string, string> = { building: "строится", ready: "готов", failed: "ошибка" };
export const reportStateTitle = (state: string): string => STATES[state] ?? state;

export const hasXlsx = (r: Pick<QaqcReport, "files">): boolean => r.files.some((f) => f.kind === "xlsx");

/** The reason «Перестроить» is not available, or null. */
export function rebuildReason(r: Pick<QaqcReport, "stale" | "state">): string | null {
  if (r.stale === "source_deleted") return "Данные отчёта удалены: перестроить нельзя, отчёт по снимку остаётся";
  if (r.state === "building") return "Отчёт строится";
  return null;
}

/** «PDF» of the report: not in Э14 (ADR 0020 §8). */
export const PDF_REASON = "Появится на этапе Э22";

export const fileNameOf = (name: string): string => `${name.replace(/[\\/:*?"<>|]/g, "_").trim() || "Отчёт QAQC"}.xlsx`;

/**
 * «Сохранить XLSX…»: the file of the report is made first when it has none (the job `qaqc.report_file`), then
 * copied to the path given or chosen in our save dialog; null — the dialog was cancelled.
 */
export async function saveXlsx(report: QaqcReport, path: string | null | undefined, deps: RunDeps = DEFAULT_DEPS): Promise<string | null> {
  if (report.state === "building") throw refusal("report_building", `Отчёт «${report.name}» ещё строится.`, "Дождитесь конца построения.");
  if (report.state === "failed") throw refusal("report_failed", `Отчёт «${report.name}» не построен.`, "Перестройте отчёт.");
  const target = path?.trim() || (await bridge()?.pickSave(fileNameOf(report.name), "xlsx")) || null;
  if (!target) return null;
  if (!hasXlsx(report)) {
    if (isRunning()) throw refusal("report_running", "Отчёт QAQC уже формируется.", "Дождитесь конца или нажмите «Отменить».");
    const run = await runReport({ kind: "resume", reportId: report.id, from: "xlsx", name: report.name, charts: report.charts ?? "flagged", path: target }, deps);
    if (run.state === "cancelled") return null;
    if (run.state === "failed") throw refusal("report_file_failed", `Файл XLSX отчёта «${report.name}» не записан.`, run.error ?? "Подробности — во вкладке «Задания».");
    return target;
  }
  await deps.save(report.id, target);
  return target;
}

/** The report by id from the list (read again when it is not there). */
export async function findReport(id: number): Promise<QaqcReport> {
  const known = reportsStore.get().list?.find((r) => r.id === id);
  if (known) return known;
  try {
    return await getReport(id);
  } catch {
    throw refusal("qaqc_report_not_found", `Отчёт QAQC № ${id} не найден.`, "Список отчётов — в разделе дерева «Отчёты».");
  }
}

interface ElementPart {
  batches?: { total?: number; failed?: number };
  summary?: { batches?: { total?: number; failed?: number } };
  [key: string]: unknown;
}

/** The totals of the summary for the assistant: batches and their failures, the criteria of duplicates. */
export function summaryText(summary: Record<string, unknown> | undefined): string {
  const elements = ((summary ?? {}) as { elements?: Record<string, ElementPart> }).elements ?? {};
  const parts: string[] = [];
  for (const [el, part] of Object.entries(elements)) {
    const b = part.summary?.batches ?? part.batches;
    const bits: string[] = [];
    if (b && typeof b.total === "number") bits.push(`партий ${int(b.total)}, отказ ${int(b.failed)}`);
    for (const [key, v] of Object.entries(part)) {
      if (!key.startsWith("duplicates:") || !v || typeof v !== "object") continue;
      const passed = (v as { passed?: boolean | null }).passed;
      bits.push(`${key.slice(11)} — критерий ${passed === true ? "выполнен" : passed === false ? "не выполнен" : "не оценён"}`);
    }
    if (bits.length) parts.push(`${el}: ${bits.join("; ")}`);
  }
  return parts.join(" · ");
}

/** The report in one line: name, number, state, «устарел», elements, charts. */
export function reportLine(r: QaqcReport): string {
  const stale = staleTitle(r.stale);
  return `Отчёт QAQC «${r.name}» (№ ${r.id}, версия ${r.version}): ${reportStateTitle(r.state)}${stale ? `, ${stale}` : ""}; данные «${r.data_name}», элементы ${r.elements.join(", ") || "—"}; рисунков ${r.images}${hasXlsx(r) ? ", файл XLSX готов" : ""}`;
}
