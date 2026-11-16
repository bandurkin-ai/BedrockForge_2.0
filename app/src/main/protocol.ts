// Pure helpers of the app:// protocol (no Electron imports; tested with vitest).
import { createHash } from "node:crypto";
import path from "node:path";

export const ORIGIN = "app://bedrockforge";

const MIME: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
  ".wasm": "application/wasm",
  ".txt": "text/plain; charset=utf-8",
};

/** Resolves a request path inside the static root; null when it tries to leave it. */
export function staticFile(root: string, pathname: string): string | null {
  let rel: string;
  try {
    rel = decodeURIComponent(pathname);
  } catch {
    return null;
  }
  if (rel === "/" || rel === "") rel = "/index.html";
  if (rel.includes("\0")) return null;
  const base = path.resolve(root);
  const full = path.resolve(base, "." + rel);
  if (full !== base && !full.startsWith(base + path.sep)) return null;
  return full;
}

export function mimeOf(file: string): string {
  return MIME[path.extname(file).toLowerCase()] ?? "application/octet-stream";
}

/** Strict CSP; inline scripts are allowed only by hash; the core is reachable on its own port only. */
export function contentSecurityPolicy(html: string, corePort: number | null): string {
  const hashes: string[] = [];
  const re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi;
  let m: RegExpExecArray | null;
  while ((m = re.exec(html))) {
    if (m[1].trim()) hashes.push(`'sha256-${createHash("sha256").update(m[1]).digest("base64")}'`);
  }
  const core = corePort ? ` http://127.0.0.1:${corePort}` : "";
  return [
    "default-src 'self'",
    `script-src 'self' ${hashes.join(" ")}`.trim(),
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "worker-src 'self' blob:",
    `connect-src 'self'${core}`,
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
  ].join("; ");
}

/** Addresses opened in the system browser; everything else is refused. */
const EXTERNAL = ["anthropic.com", "claude.ai", "claude.com", "github.com"];

export function isAllowedExternal(url: string): boolean {
  try {
    const u = new URL(url);
    if (u.protocol !== "https:") return false;
    return EXTERNAL.some((d) => u.hostname === d || u.hostname.endsWith("." + d));
  } catch {
    return false;
  }
}
