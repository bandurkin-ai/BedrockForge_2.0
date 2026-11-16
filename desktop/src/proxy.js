// Pure helpers of the app:// protocol: what is a static file, what goes to the server, the CSP.
// Kept free of Electron so they run under `node --test`.
"use strict";

const path = require("node:path");
const crypto = require("node:crypto");

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".wasm": "application/wasm",
  ".txt": "text/plain; charset=utf-8",
};

/** Paths served by the server (Docker `api`), everything else is the built interface. */
function isServerPath(pathname) {
  return pathname === "/api" || pathname.startsWith("/api/") || pathname === "/mcp" || pathname.startsWith("/mcp/");
}

/** Resolves a request path inside the static root; null when it tries to leave it. */
function staticFile(root, pathname) {
  let rel;
  try {
    rel = decodeURIComponent(pathname);
  } catch {
    return null;
  }
  if (rel === "/" || rel === "") rel = "/index.html";
  const full = path.resolve(root, "." + rel);
  const base = path.resolve(root);
  if (full !== base && !full.startsWith(base + path.sep)) return null;
  return full;
}

function mimeOf(file) {
  return MIME[path.extname(file).toLowerCase()] ?? "application/octet-stream";
}

/** Normalises a server address typed by the user: http(s) only, no path, no trailing slash. */
function normalizeServer(input) {
  const text = String(input ?? "").trim();
  if (!text) return null;
  let url;
  try {
    url = new URL(/^[a-z]+:\/\//i.test(text) ? text : `http://${text}`);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  return `${url.protocol}//${url.host}`;
}

/** Strict CSP for the interface; inline scripts of index.html are allowed by their hashes. */
function contentSecurityPolicy(html) {
  const hashes = [];
  const re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi;
  let m;
  while ((m = re.exec(html))) {
    if (m[1].trim()) hashes.push(`'sha256-${crypto.createHash("sha256").update(m[1]).digest("base64")}'`);
  }
  return [
    "default-src 'self'",
    `script-src 'self' ${hashes.join(" ")}`.trim(),
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "worker-src 'self' blob:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
  ].join("; ");
}

/** Hop-by-hop headers are not forwarded in either direction. */
const HOP = new Set(["connection", "keep-alive", "proxy-connection", "transfer-encoding", "upgrade", "te", "trailer", "host", "origin", "referer"]);

function forwardHeaders(entries) {
  const out = {};
  for (const [k, v] of entries) if (!HOP.has(k.toLowerCase())) out[k] = v;
  return out;
}

/** Addresses opened in the system browser (Claude sign-in, documentation); everything else is refused. */
const EXTERNAL = ["anthropic.com", "claude.ai", "claude.com", "github.com"];

function isAllowedExternal(url) {
  try {
    const u = new URL(url);
    if (u.protocol !== "https:") return false;
    return EXTERNAL.some((d) => u.hostname === d || u.hostname.endsWith("." + d));
  } catch {
    return false;
  }
}

module.exports = { isServerPath, staticFile, mimeOf, normalizeServer, contentSecurityPolicy, forwardHeaders, isAllowedExternal };
