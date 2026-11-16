/** Minimal core client; generated OpenAPI types replace the hand-written ones on Э1. */

export interface CoreTarget {
  baseUrl: string;
  token: string;
}

export interface Health {
  status: string;
  version: string;
  mode: string;
}

export function apiUrl(target: CoreTarget, path: string): string {
  const base = target.baseUrl.replace(/\/+$/, "");
  const rel = path.startsWith("/") ? path : `/${path}`;
  return `${base}/api/v1${rel}`;
}

export function authHeaders(target: CoreTarget, extra: Record<string, string> = {}): Record<string, string> {
  return { "X-BF-Token": target.token, ...extra };
}

export async function fetchHealth(target: CoreTarget, f: typeof fetch = fetch): Promise<Health> {
  const r = await f(apiUrl(target, "/health"), { headers: authHeaders(target) });
  if (!r.ok) throw new Error(`Ядро ответило ${r.status}`);
  return (await r.json()) as Health;
}
