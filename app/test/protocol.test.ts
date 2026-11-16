import path from "node:path";
import { describe, expect, it } from "vitest";
import { contentSecurityPolicy, isAllowedExternal, staticFile } from "../src/main/protocol";
import { parseReadyLine, RestartBudget } from "../src/main/ready";

describe("app:// static files", () => {
  const root = path.resolve("/srv/ui");
  it("maps / to index.html and stays inside the root", () => {
    expect(staticFile(root, "/")).toBe(path.join(root, "index.html"));
    expect(staticFile(root, "/assets/a.js")).toBe(path.join(root, "assets", "a.js"));
    expect(staticFile(root, "/../secret.txt")).toBeNull();
    expect(staticFile(root, "/%2e%2e/secret.txt")).toBeNull();
    expect(staticFile(root, "/%E0%A4%A")).toBeNull();
  });
});

describe("CSP", () => {
  it("allows only the core port for connect-src and hashes inline scripts", () => {
    const csp = contentSecurityPolicy("<script>console.log(1)</script>", 5555);
    expect(csp).toContain("connect-src 'self' http://127.0.0.1:5555");
    expect(csp).toMatch(/script-src 'self' 'sha256-[A-Za-z0-9+/=]+'/);
    expect(csp).not.toContain("unsafe-eval");
  });
});

describe("external links", () => {
  it("opens only https on allowed domains", () => {
    expect(isAllowedExternal("https://claude.ai/login")).toBe(true);
    expect(isAllowedExternal("http://claude.ai")).toBe(false);
    expect(isAllowedExternal("https://evil-claude.ai")).toBe(false);
  });
});

describe("core handshake", () => {
  it("parses BF-READY", () => {
    expect(parseReadyLine("BF-READY port=51234 version=0.0.1")).toEqual({ port: 51234, version: "0.0.1" });
    expect(parseReadyLine("BF-READY port=0 version=1")).toBeNull();
    expect(parseReadyLine("hello")).toBeNull();
  });

  it("allows 3 restarts per 5 minutes", () => {
    const b = new RestartBudget();
    expect([0, 1, 2, 3].map((t) => b.tryConsume(t * 1000))).toEqual([true, true, true, false]);
    expect(b.tryConsume(5 * 60_000 + 1000)).toBe(true);
  });
});
