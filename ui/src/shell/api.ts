/** JSON calls to the core with its {code, message, hint} errors (ТЗ 17: Russian text with a hint). */
import { bridge } from "../bridge";
import { apiUrl, authHeaders, type CoreTarget } from "./coreClient";
import { logRequest } from "./opLog";

export class CoreError extends Error {
  /** 500 of the core: exception type and text, end of the stack (ТЗ 7.9). */
  detail?: string;
  trace?: string;
  /** Machine fields of the error body (e.g. the current value and version on a 409). */
  body?: Record<string, unknown>;

  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly hint: string,
  ) {
    super(message);
  }

  /** Message and hint in one line, for logs and Claude. */
  get full(): string {
    return this.hint ? `${this.message} ${this.hint}` : this.message;
  }
}

let override: CoreTarget | null = null;
/** Tests and the browser preview set the target directly. */
export function setCoreTarget(t: CoreTarget | null): void {
  override = t;
}

export function coreTarget(): CoreTarget | null {
  return override ?? bridge()?.core() ?? null;
}

export async function call<T>(method: string, path: string, body?: unknown, f: typeof fetch = fetch): Promise<T> {
  const target = coreTarget();
  if (!target) throw new CoreError(0, "core_offline", "Ядро недоступно.", "Подождите перезапуска ядра.");
  let r: Response;
  const t0 = typeof performance !== "undefined" ? performance.now() : Date.now();
  const elapsed = () => (typeof performance !== "undefined" ? performance.now() : Date.now()) - t0;
  try {
    r = await f(apiUrl(target, path), {
      method,
      headers: authHeaders(target, body === undefined ? {} : { "Content-Type": "application/json" }),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    logRequest(method, path, body, 0, elapsed(), "");
    throw new CoreError(0, "core_offline", "Ядро недоступно.", "Подождите перезапуска ядра.");
  }
  const text = await r.text().catch(() => "");
  logRequest(method, path, body, r.status, elapsed(), text);
  let data: Record<string, unknown> | null = null;
  try {
    data = text ? (JSON.parse(text) as Record<string, unknown>) : null;
  } catch {
    data = null;
  }
  if (!r.ok) {
    const e = data ?? {};
    const err = new CoreError(r.status, String(e.code ?? "error"), String(e.message ?? `Ядро ответило ${r.status}`), String(e.hint ?? ""));
    err.detail = typeof e.detail === "string" ? e.detail : undefined;
    err.trace = typeof e.trace === "string" ? e.trace : undefined;
    err.body = e;
    throw err;
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => call<T>("GET", path),
  post: <T>(path: string, body: unknown = {}) => call<T>("POST", path, body),
  patch: <T>(path: string, body: unknown) => call<T>("PATCH", path, body),
  put: <T>(path: string, body: unknown) => call<T>("PUT", path, body),
  del: <T>(path: string) => call<T>("DELETE", path),
};
