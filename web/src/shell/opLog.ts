/**
 * Journals of imports and exports, for finding what went wrong.
 *
 * Two sources go into one text file:
 * - every request to the API (`tracedFetch`, used by api.ts): method, address, what was sent,
 *   status, time and, for import/export addresses and every failure, the start of the answer;
 * - the steps an import or export dialog writes itself (`OpLog.info/warn/error/data`).
 *
 * A dialog creates its journal with `createLog`; the journal takes the requests made while it
 * was open. Journals of the session are kept (`recentLogs`), and those that started background
 * jobs are found by the job (`logForJob`) — the job journal from the server is added to the text.
 */

export type LogLevel = "info" | "warning" | "error" | "data";

export interface LogEntry {
  t: number;
  level: LogLevel;
  text: string;
}

export interface NetEntry {
  seq: number;
  t: number;
  method: string;
  url: string;
  request: string;
  status: number | null;
  ms: number | null;
  /** start of the answer (import/export addresses and failures) */
  response: string | null;
  error: string | null;
}

/** A server job journal as the text needs it (JobDetail from api.ts fits). */
export interface JobJournal {
  id: number;
  kind: string;
  title?: string | null;
  status: string;
  progress?: number;
  message?: string | null;
  error?: string | null;
  params?: unknown;
  result?: unknown;
  attempts?: number;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  log: { t: string; level: string; text: string }[];
}

const NET_LIMIT = 2000;
const BODY_LIMIT = 4000;
const RESPONSE_LIMIT = 6000;
/** Answers of these addresses are kept even when they succeed. */
const DETAILED = /\/api\/(import|uploads|exports|lines\/export|wireframes\/export|audit|styles\/import|projects\/\d+\/(imports|detect|exports|uploads|styles))|\/api\/jobs\/\d+$|\/export/;

let seq = 0;
let net: NetEntry[] = [];

const cut = (text: string, limit: number) => (text.length > limit ? `${text.slice(0, limit)}… [ещё ${text.length - limit} симв.]` : text);

function describeBody(body: unknown): string {
  if (body == null) return "";
  if (typeof body === "string") return cut(body, BODY_LIMIT);
  if (typeof FormData !== "undefined" && body instanceof FormData) {
    const parts: string[] = [];
    body.forEach((v, k) => {
      parts.push(typeof v === "string" ? `${k}=${cut(v, 200)}` : `${k}=файл «${(v as File).name}», ${(v as File).size} Б, ${(v as File).type || "тип не указан"}`);
    });
    return `FormData: ${parts.join("; ")}`;
  }
  if (typeof Blob !== "undefined" && body instanceof Blob) return `<двоичные данные ${body.size} Б>`;
  if (body instanceof ArrayBuffer) return `<двоичные данные ${body.byteLength} Б>`;
  if (ArrayBuffer.isView(body)) return `<двоичные данные ${body.byteLength} Б>`;
  return `<${Object.prototype.toString.call(body)}>`;
}

/** Drops the request trace (tests). */
export function resetTrace() {
  net = [];
  seq = 0;
}

export const traceSeq = () => seq;
export const traceSince = (from: number, to = Infinity) => net.filter((e) => e.seq > from && e.seq <= to);

/** fetch that writes the request and its answer into the trace. */
export async function tracedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  const entry: NetEntry = {
    seq: ++seq, t: Date.now(), method: (init?.method ?? (typeof input === "object" && "method" in input ? input.method : "GET")).toUpperCase(),
    url, request: describeBody(init?.body), status: null, ms: null, response: null, error: null,
  };
  net.push(entry);
  if (net.length > NET_LIMIT) net = net.slice(-NET_LIMIT);
  const start = performance.now();
  try {
    const response = await fetch(input, init);
    entry.status = response.status;
    entry.ms = Math.round(performance.now() - start);
    const type = response.headers.get("content-type") ?? "";
    const textual = /json|text|xml|problem/.test(type) || type === "";
    // Chunks of a large upload: only the failures are worth their answer.
    // Fixed reference data (the import schemas) is not repeated in every journal.
    const chunk = (entry.method === "PUT" && /\/api\/uploads\//.test(url)) || /\/api\/import\/schemas/.test(url);
    if ((!response.ok || (DETAILED.test(url) && !chunk)) && textual && !/event-stream/.test(type)) {
      // Read from a copy, without holding the caller.
      response.clone().text().then((t) => { entry.response = cut(t, RESPONSE_LIMIT); }, () => undefined);
    } else if (!textual) {
      const size = response.headers.get("content-length");
      entry.response = `<${type}${size ? `, ${size} Б` : ""}>`;
    }
    return response;
  } catch (e) {
    entry.ms = Math.round(performance.now() - start);
    entry.error = `${(e as Error).name}: ${(e as Error).message}`;
    throw e;
  }
}

// ---------------------------------------------------------------- journals of operations

let nextId = 1;
const logs: OpLog[] = [];
const byJob = new Map<number, OpLog>();
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());
export const subscribeLogs = (l: () => void) => {
  listeners.add(l);
  return () => void listeners.delete(l);
};

function stringify(value: unknown): string {
  if (value instanceof Error) return `${value.name}: ${value.message}${value.stack ? `\n${value.stack}` : ""}`;
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, (_k, v) => (v instanceof File ? { file: v.name, size: v.size, type: v.type } : v), 2) ?? String(value);
  } catch {
    return String(value);
  }
}

export class OpLog {
  readonly id = nextId++;
  readonly started = Date.now();
  finished: number | null = null;
  readonly entries: LogEntry[] = [];
  readonly jobIds: number[] = [];
  private readonly netFrom = seq;
  private netTo = Infinity;
  /** set while the text is copied: the journal does not change between two clicks */
  version = 0;

  constructor(readonly kind: "import" | "export", readonly title: string, readonly context: Record<string, unknown> = {}) {}

  private registered = false;
  private opened = false;

  private add(level: LogLevel, text: string) {
    if (!this.registered) register(this);
    this.registered = true;
    this.entries.push({ t: Date.now(), level, text });
    this.version++;
    emit();
  }

  info(text: string) { this.add("info", text); }
  warn(text: string) { this.add("warning", text); }
  /** An error, with its stack when it is an Error. */
  error(text: string, e?: unknown) { this.add("error", e === undefined ? text : `${text}: ${stringify(e)}`); }
  /** A value written out as JSON (a request, an answer, the mapping of columns). */
  data(label: string, value: unknown) { this.add("data", `${label}:\n${cut(stringify(value), 20000)}`); }

  /** Background jobs this operation started: their server journals go into the text. */
  attachJobs(ids: number[]) {
    for (const id of ids) {
      if (!this.jobIds.includes(id)) this.jobIds.push(id);
      byJob.set(id, this);
    }
    this.info(`Запущены фоновые задания: ${ids.map((id) => `№${id}`).join(", ")}`);
  }

  /** The dialog is shown: written once (React may mount a dialog twice in development). */
  open(details?: Record<string, unknown>) {
    if (this.opened) return;
    this.opened = true;
    this.add("info", `Окно открыто: ${this.title}`);
    if (details && Object.keys(details).length) this.data("Исходные данные окна", details);
  }

  /** The dialog is closed: later requests are not this operation's. */
  close() {
    if (this.finished !== null) return;
    this.finished = Date.now();
    this.netTo = seq;
    this.add("info", "Окно закрыто");
  }

  get hasErrors() {
    return this.entries.some((e) => e.level === "error") || this.requests().some((r) => r.error || (r.status !== null && r.status >= 400));
  }

  requests(): NetEntry[] {
    return traceSince(this.netFrom, this.netTo);
  }

  /** File name for the download: bedrockforge-import-2026-11-16_14-05-09.txt */
  fileName() {
    const d = new Date(this.started);
    const p = (n: number) => String(n).padStart(2, "0");
    return `bedrockforge-${this.kind}-${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}_${p(d.getHours())}-${p(d.getMinutes())}-${p(d.getSeconds())}.txt`;
  }

  /** The journal as plain text; `jobs` are the server journals of the attached jobs. */
  text(jobs: JobJournal[] = []): string {
    return renderLog(this, jobs);
  }
}

const time = (t: number) => {
  const d = new Date(t);
  return `${d.toLocaleTimeString("ru-RU", { hour12: false })}.${String(d.getMilliseconds()).padStart(3, "0")}`;
};
const stamp = (t: number) => new Date(t).toISOString();
const LEVEL: Record<string, string> = { info: "INFO ", warning: "WARN ", error: "ERROR", data: "DATA " };
const indent = (text: string, pad = "    ") => text.split("\n").map((l) => pad + l).join("\n");

function environment(): string[] {
  const nav = typeof navigator !== "undefined" ? navigator : null;
  const loc = typeof location !== "undefined" ? location : null;
  return [
    `Адрес: ${loc ? loc.origin + loc.pathname + loc.hash : "—"}`,
    `Браузер: ${nav?.userAgent ?? "—"}`,
    `Язык: ${nav?.language ?? "—"}; часовой пояс: ${Intl.DateTimeFormat().resolvedOptions().timeZone}`,
    `Экран: ${typeof window !== "undefined" ? `${window.innerWidth}×${window.innerHeight}, масштаб ${window.devicePixelRatio}` : "—"}`,
  ];
}

export function renderLog(log: OpLog, jobs: JobJournal[] = []): string {
  const out: string[] = [];
  const line = "=".repeat(78);
  out.push(line, `BedrockForge — журнал ${log.kind === "import" ? "импорта" : "экспорта"}: ${log.title}`, line);
  out.push(`Начало: ${stamp(log.started)} (${new Date(log.started).toLocaleString("ru-RU")})`);
  out.push(`Конец: ${log.finished ? stamp(log.finished) : "окно открыто"}`);
  out.push(`Журнал составлен: ${stamp(Date.now())}`);
  out.push(...environment());
  for (const [k, v] of Object.entries(log.context)) out.push(`${k}: ${typeof v === "string" ? v : stringify(v)}`);
  const requests = log.requests();
  const failed = requests.filter((r) => r.error || (r.status !== null && r.status >= 400));
  const errors = log.entries.filter((e) => e.level === "error").length;
  const warnings = log.entries.filter((e) => e.level === "warning").length;
  const failedJobs = jobs.filter((j) => log.jobIds.includes(j.id) && j.status === "failed").length;
  out.push(`Итог: ошибок ${errors}, предупреждений ${warnings}, запросов ${requests.length} (с ошибкой ${failed.length}), фоновых заданий ${log.jobIds.length}${failedJobs ? ` (с ошибкой ${failedJobs})` : ""}`);

  out.push("", "-- Шаги ".padEnd(78, "-"));
  if (!log.entries.length) out.push("(нет записей)");
  for (const e of log.entries) {
    const [first, ...rest] = e.text.split("\n");
    out.push(`${time(e.t)} ${LEVEL[e.level]} ${first}`);
    if (rest.length) out.push(indent(rest.join("\n")));
  }

  if (failed.length) {
    out.push("", "-- Запросы с ошибкой ".padEnd(78, "-"));
    for (const r of failed) out.push(...renderRequest(r));
  }

  for (const id of log.jobIds) {
    const job = jobs.find((j) => j.id === id);
    out.push("", `-- Фоновое задание №${id} (журнал сервера) `.padEnd(78, "-"));
    if (!job) {
      out.push("(журнал задания не получен с сервера)");
      continue;
    }
    out.push(...renderJob(job));
  }

  out.push("", "-- Все запросы к серверу ".padEnd(78, "-"));
  if (!requests.length) out.push("(запросов не было)");
  for (const r of requests) out.push(...renderRequest(r));
  out.push("", line, "Конец журнала", line, "");
  return out.join("\n");
}

function renderRequest(r: NetEntry): string[] {
  const out = [`${time(r.t)} #${r.seq} ${r.method} ${r.url} → ${r.error ? `сбой сети (${r.error})` : r.status ?? "нет ответа"}${r.ms !== null ? ` · ${r.ms} мс` : ""}`];
  if (r.request) out.push(indent(`запрос: ${r.request}`));
  if (r.response) out.push(indent(`ответ: ${r.response}`));
  return out;
}

export function renderJob(job: JobJournal): string[] {
  const out = [
    `Вид: ${job.kind}${job.title ? ` · ${job.title}` : ""}`,
    `Состояние: ${job.status}${job.progress !== undefined ? ` · ход ${Math.round(job.progress * 100)}%` : ""}${job.attempts !== undefined ? ` · попыток ${job.attempts}` : ""}`,
    `Создано: ${job.created_at ?? "—"} · начато: ${job.started_at ?? "—"} · завершено: ${job.finished_at ?? "—"}`,
  ];
  if (job.message) out.push(`Сообщение: ${job.message}`);
  if (job.error) out.push(`Ошибка: ${job.error}`);
  if (job.params !== undefined) out.push(`Параметры:\n${indent(stringify(job.params))}`);
  if (job.result != null) out.push(`Результат:\n${indent(cut(stringify(job.result), 20000))}`);
  out.push("Журнал:");
  if (!job.log.length) out.push("    (пуст)");
  for (const e of job.log) {
    const [first, ...rest] = e.text.split("\n");
    out.push(`    ${time(Date.parse(e.t))} ${LEVEL[e.level] ?? e.level} ${first}`);
    if (rest.length) out.push(indent(rest.join("\n"), "        "));
  }
  return out;
}

function register(log: OpLog) {
  logs.unshift(log);
  if (logs.length > 30) {
    for (const old of logs.splice(30)) for (const id of old.jobIds) byJob.delete(id);
  }
}

/**
 * A journal of an import or export; it joins the session's list (the last 30) with its first
 * entry, so a journal nobody wrote to is not listed.
 */
export function createLog(kind: "import" | "export", title: string, context: Record<string, unknown> = {}): OpLog {
  return new OpLog(kind, title, context);
}

export const recentLogs = (): readonly OpLog[] => logs;
export const logForJob = (jobId: number): OpLog | undefined => byJob.get(jobId);

/** Text of a job that no dialog of this session started (e.g. after a page reload). */
export function renderJobOnly(job: JobJournal): string {
  const line = "=".repeat(78);
  return [line, `BedrockForge — журнал фонового задания №${job.id}`, line, `Журнал составлен: ${stamp(Date.now())}`, ...environment(), "",
    ...renderJob(job), "", line, "Конец журнала", line, ""].join("\n");
}

/** Copies text to the clipboard; false when the browser refused. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Without clipboard permission (http, an old browser): through a selected text area.
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    area.remove();
    return ok;
  }
}

/** Saves text as a .txt file in the browser's downloads. */
export function saveText(text: string, name: string) {
  const href = URL.createObjectURL(new Blob(["﻿", text], { type: "text/plain;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = href;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 10_000);
}
