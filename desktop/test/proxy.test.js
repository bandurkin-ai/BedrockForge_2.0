"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { isServerPath, staticFile, normalizeServer, contentSecurityPolicy, forwardHeaders, isAllowedExternal } = require("../src/proxy");

test("server paths go to the API, the rest is static", () => {
  for (const p of ["/api", "/api/health", "/api/projects/1/jobs/events", "/mcp", "/mcp/x"]) assert.equal(isServerPath(p), true, p);
  for (const p of ["/", "/index.html", "/assets/api.js", "/apiary", "/mcpx"]) assert.equal(isServerPath(p), false, p);
});

test("static files cannot leave the root", () => {
  const root = path.resolve("/srv/web");
  assert.equal(staticFile(root, "/"), path.join(root, "index.html"));
  assert.equal(staticFile(root, "/assets/a.js"), path.join(root, "assets", "a.js"));
  assert.equal(staticFile(root, "/../secret.txt"), null);
  assert.equal(staticFile(root, "/%2e%2e/%2e%2e/etc/passwd"), null);
  assert.equal(staticFile(root, "/%E0%A4%A"), null);
});

test("server address is normalised to http(s) origin", () => {
  assert.equal(normalizeServer("localhost:8000"), "http://localhost:8000");
  assert.equal(normalizeServer(" http://192.168.1.5:8000/api/ "), "http://192.168.1.5:8000");
  assert.equal(normalizeServer("https://bf.example"), "https://bf.example");
  assert.equal(normalizeServer("file:///c:/x"), null);
  assert.equal(normalizeServer("javascript:alert(1)"), null);
  assert.equal(normalizeServer(""), null);
});

test("CSP allows inline scripts only by hash", () => {
  const csp = contentSecurityPolicy('<script>var a=1;</script><script type="module" src="/x.js"></script>');
  assert.match(csp, /script-src 'self' 'sha256-[A-Za-z0-9+/=]+'/);
  assert.doesNotMatch(csp, /script-src[^;]*unsafe/);
  assert.match(csp, /connect-src 'self'/);
});

test("hop-by-hop and origin headers are not forwarded", () => {
  const h = forwardHeaders([["Host", "app"], ["Origin", "app://bedrockforge"], ["Content-Type", "text/plain"], ["Range", "bytes=0-1"], ["Connection", "keep-alive"]]);
  assert.deepEqual(h, { "Content-Type": "text/plain", Range: "bytes=0-1" });
});

test("only https addresses of known services open in the system browser", () => {
  assert.equal(isAllowedExternal("https://console.anthropic.com/settings/keys"), true);
  assert.equal(isAllowedExternal("https://claude.ai/oauth"), true);
  assert.equal(isAllowedExternal("http://claude.ai"), false);
  assert.equal(isAllowedExternal("https://evil.com/?claude.ai"), false);
  assert.equal(isAllowedExternal("https://claude.ai.evil.com"), false);
  assert.equal(isAllowedExternal("file:///c:/windows"), false);
});
