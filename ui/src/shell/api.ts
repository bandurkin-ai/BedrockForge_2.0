/** JSON calls to the core with its {code, message, hint} errors (ТЗ 17: Russian text with a hint). */
import { bridge } from "../bridge";
import { apiUrl, authHeaders, type CoreTarget } from "./coreClient";

export class CoreError extends Error {
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
  try {
    r = await f(apiUrl(target, path), {
      method,
      headers: authHeaders(target, body === undefined ? {} : { "Content-Type": "application/json" }),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new CoreError(0, "core_offline", "Ядро недоступно.", "Подождите перезапуска ядра.");
  }
  const data = (await r.json().catch(() => null)) as Record<string, unknown> | null;
  if (!r.ok) {
    const e = data ?? {};
    throw new CoreError(r.status, String(e.code ?? "error"), String(e.message ?? `Ядро ответило ${r.status}`), String(e.hint ?? ""));
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
