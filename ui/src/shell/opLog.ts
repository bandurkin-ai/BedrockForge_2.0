/**
 * Operation logs of import and export windows (ТЗ 7.9): environment, steps, requests to the core while the
 * window is open, jobs, errors with `detail` and `trace` of the core. The last 30 of the session are kept.
 */
import { Store } from "./store";
import { openWindow } from "./windows";

export const LOG_LIMIT = 30;
export const RESPONSE_LIMIT = 6000;

export interface OpLog {
  id: number;
  title: string;
  /** File name stem: bedrockforge-<kind>-ГГГГ-ММ-ДД_чч-мм-сс.txt */
  kind: string;
  started: Date;
  lines: string[];
  failed: boolean;
}

export const opLogStore = new Store<{ logs: OpLog[]; version: number }>({ logs: [], version: 0 });
let nextId = 1;
let active: OpLog[] = [];

const pad = (n: number) => String(n).padStart(2, "0");
const time = (d = new Date()) => `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${String(d.getMilliseconds()).padStart(3, "0")}`;

function touch(): void {
  opLogStore.set((s) => ({ logs: [...s.logs], version: s.version + 1 }));
}

export interface Environment {
  uiVersion?: string;
  coreVersion?: string | null;
  project?: string | null;
}

/** Starts a log for a window; it records core requests until `stop()`. */
export function startOpLog(title: string, kind: string, env: Environment = {}): OpLog {
  const log: OpLog = { id: nextId++, title, kind, started: new Date(), lines: [], failed: false };
  const nav = typeof navigator !== "undefined" ? navigator : null;
  const win = typeof window !== "undefined" ? window : null;
  write(log, `${title} — журнал операции`);
  write(log, `Время: ${log.started.toISOString()} (часовой пояс ${Intl.DateTimeFormat().resolvedOptions().timeZone})`);
  write(log, `Версии: интерфейс ${env.uiVersion ?? "—"}, ядро ${env.coreVersion ?? "—"}`);
  write(log, `Система: ${nav?.userAgent ?? "—"}; язык ${nav?.language ?? "—"}; окно ${win ? `${win.innerWidth}×${win.innerHeight}` : "—"}`);
  write(log, `Проект: ${env.project ?? "не открыт"}`);
  opLogStore.set((s) => ({ logs: [log, ...s.logs].slice(0, LOG_LIMIT), version: s.version + 1 }));
  active = [...active, log];
  return log;
}

export function stopOpLog(log: OpLog): void {
  active = active.filter((l) => l !== log);
}

function write(log: OpLog, text: string): void {
  log.lines.push(`${time()} ${text}`);
}

export function logStep(log: OpLog, text: string): void {
  write(log, text);
  touch();
}

/** An error: the message, and for core failures `detail` and `trace`; the window's log icon turns red. */
export function logError(log: OpLog, message: string, extra?: { detail?: string; trace?: string; stack?: string }): void {
  log.failed = true;
  write(log, `ОШИБКА: ${message}`);
  if (extra?.detail) write(log, `detail: ${extra.detail}`);
  if (extra?.trace) write(log, `trace:\n${extra.trace}`);
  if (extra?.stack) write(log, `стек:\n${extra.stack}`);
  touch();
}

const trim = (s: string) => (s.length > RESPONSE_LIMIT ? `${s.slice(0, RESPONSE_LIMIT)}… (обрезано, всего ${s.length} символов)` : s);

/** Called by the core client for every request while a log is active. */
export function logRequest(method: string, path: string, body: unknown, status: number, ms: number, response: string): void {
  if (!active.length) return;
  const sent = body === undefined ? "" : ` тело ${trim(JSON.stringify(body, (k, v) => (/password/i.test(k) && v ? "***" : v)))}`;
  const full = status >= 400 || /\/(import|export)/.test(path);
  for (const log of active) {
    write(log, `${method} ${path}${sent} → ${status || "нет ответа"} за ${Math.round(ms)} мс`);
    if (full && response) write(log, `ответ: ${trim(response)}`);
    if (status >= 500) log.failed = true;
  }
  touch();
}

export function logText(log: OpLog): string {
  return log.lines.join("\n");
}

export function logFileName(log: OpLog, now = log.started): string {
  const d = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}_${pad(now.getHours())}-${pad(now.getMinutes())}-${pad(now.getSeconds())}`;
  return `bedrockforge-${log.kind}-${d}.txt`;
}

export function openLogWindow(logId?: number): void {
  const log = logId === undefined ? undefined : opLogStore.get().logs.find((l) => l.id === logId);
  const [vw, vh] = typeof window === "undefined" ? [1280, 800] : [window.innerWidth, window.innerHeight];
  openWindow("op-log", log ? `Журнал: ${log.title}` : "Журналы", "normal", logId === undefined ? undefined : { logId }, vw, vh);
}
