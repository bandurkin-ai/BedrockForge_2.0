import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { antEnv, ClaudeLink, type SafeStorageLike } from "../src/main/claude";

const KEY = "sk-ant-test-0000000000000000000000";
const TOKEN = "launch-token";

/** Reversible stand-in for DPAPI: the bytes are scrambled, so the key is never in the file as text. */
function fakeStorage(opts: { available?: boolean; backend?: string } = {}): SafeStorageLike {
  const scramble = (b: Buffer) => Buffer.from(b.map((x) => x ^ 0x5a));
  return {
    isEncryptionAvailable: () => opts.available ?? true,
    encryptString: (s) => scramble(Buffer.from(s, "utf8")),
    decryptString: (b) => scramble(b).toString("utf8"),
    getSelectedStorageBackend: () => opts.backend ?? "gnome_libsecret",
  };
}

interface Seen {
  method: string;
  url: string;
  headers: http.IncomingHttpHeaders;
  body: string;
}

/** A tiny fake of the assistant routes of the core on 127.0.0.1. */
class FakeCore {
  seen: Seen[] = [];
  secret: { kind: string; secret: Record<string, unknown>; revision: number } | null = null;
  sealedWith: number | null = null;
  sealNeeded = false;
  private server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      this.seen.push({ method: req.method ?? "", url: req.url ?? "", headers: req.headers, body });
      const send = (status: number, obj?: unknown) => {
        res.writeHead(status, { "content-type": "application/json" });
        res.end(obj === undefined ? "" : JSON.stringify(obj));
      };
      const u = req.url ?? "";
      if (u.endsWith("/connection/secret") && req.method === "GET") return this.secret ? send(200, this.secret) : send(204);
      if (u.endsWith("/connection/secret") && req.method === "PUT") return send(200, { ok: true });
      if (u.endsWith("/connection/sealed")) {
        this.sealedWith = JSON.parse(body).revision;
        this.sealNeeded = false;
        return send(200, { ok: true });
      }
      if (u.endsWith("/status")) return send(200, { seal_needed: this.sealNeeded });
      send(404);
    });
  });
  port = 0;
  async start(): Promise<void> {
    await new Promise<void>((r) => this.server.listen(0, "127.0.0.1", r));
    this.port = (this.server.address() as { port: number }).port;
  }
  async stop(): Promise<void> {
    await new Promise<void>((r) => this.server.close(() => r()));
  }
}

let dir: string;
let file: string;
let core: FakeCore;
let logs: string[];
let up: boolean;

beforeEach(async () => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-claude-"));
  file = path.join(dir, "assistant", "connection.bin");
  core = new FakeCore();
  await core.start();
  logs = [];
  up = true;
});

afterEach(async () => {
  await core.stop();
  fs.rmSync(dir, { recursive: true, force: true });
});

function link(over: Partial<ConstructorParameters<typeof ClaudeLink>[0]> = {}): ClaudeLink {
  return new ClaudeLink({
    storage: fakeStorage(),
    file,
    core: () => (up ? { port: core.port, token: TOKEN } : null),
    e2e: false,
    log: (l) => logs.push(l),
    platform: "linux",
    pollMs: 0,
    retryMs: 5,
    ...over,
  });
}

describe("seal and hand over", () => {
  it("encrypts the secret to connection.bin and hands it back to a restarted core", async () => {
    core.secret = { kind: "api_key", secret: { key: KEY }, revision: 7 };
    const l = link();
    expect(await l.seal()).toEqual({ ok: true });
    expect(core.sealedWith).toBe(7);

    const raw = fs.readFileSync(file);
    expect(raw.toString("utf8")).not.toContain(KEY); // not in the file as text

    core.seen = [];
    await link().onCoreReady(); // a new process of the shell: reads the file back
    const put = core.seen.find((s) => s.method === "PUT");
    expect(put?.url).toBe("/api/v1/assistant/connection/secret");
    expect(JSON.parse(put!.body)).toEqual({ kind: "api_key", secret: { key: KEY }, secure_storage: true });
  });

  it("round-trips an account secret with several fields", async () => {
    const secret = { access_token: "a", expires_at: "2026-11-26T10:00:00Z", refresh_token: "r", client_id: "c", organization_id: "org_1", workspace_id: "w_1" };
    core.secret = { kind: "account", secret, revision: 2 };
    await link().seal();
    core.seen = [];
    await link().onCoreReady();
    expect(JSON.parse(core.seen.find((s) => s.method === "PUT")!.body)).toMatchObject({ kind: "account", secret });
  });

  it("hands the secret over only after the core is ready, and sends no Origin", async () => {
    core.secret = { kind: "api_key", secret: { key: KEY }, revision: 1 };
    await link().seal();
    core.seen = [];
    up = false; // the core has not printed BF-READY yet
    const l = link();
    await l.onCoreReady();
    expect(core.seen).toHaveLength(0);
    up = true;
    await l.onCoreReady(); // after BF-READY / restart
    expect(core.seen.map((s) => s.method)).toEqual(["PUT"]);
    for (const s of core.seen) {
      expect(s.headers.origin).toBeUndefined();
      expect(s.headers["x-bf-token"]).toBe(TOKEN);
    }
  });

  it("does not call the core when nothing is stored", async () => {
    await link().onCoreReady();
    expect(core.seen).toHaveLength(0);
  });

  it("204 from the core (a key of the environment) means nothing to store", async () => {
    expect(await link().seal()).toEqual({ ok: true });
    expect(fs.existsSync(file)).toBe(false);
    expect(core.sealedWith).toBeNull();
  });

  it("seals when /status says seal_needed, and not otherwise", async () => {
    core.secret = { kind: "api_key", secret: { key: KEY }, revision: 3 };
    const l = link();
    await l.checkSeal();
    expect(fs.existsSync(file)).toBe(false);
    core.sealNeeded = true;
    await l.checkSeal();
    expect(core.sealedWith).toBe(3);
    expect(fs.existsSync(file)).toBe(true);
  });

  it("forget removes the encrypted copy", async () => {
    core.secret = { kind: "api_key", secret: { key: KEY }, revision: 1 };
    const l = link();
    await l.seal();
    await l.forget();
    expect(fs.existsSync(file)).toBe(false);
    await l.forget(); // already gone: no error
  });
});

describe("no encryption", () => {
  it("refuses to save when the backend is basic_text", async () => {
    core.secret = { kind: "api_key", secret: { key: KEY }, revision: 1 };
    const l = link({ storage: fakeStorage({ backend: "basic_text" }) });
    expect(l.storage()).toEqual({ secure: false, backend: "basic_text" });
    const r = await l.seal();
    expect(r.ok).toBe(false);
    expect(r.message).toBe("Хранилище ключей ОС недоступно: задайте ANTHROPIC_API_KEY");
    expect(fs.existsSync(file)).toBe(false);
    expect(core.seen).toHaveLength(0);
  });

  it("refuses to save when encryption is unavailable", async () => {
    const l = link({ storage: fakeStorage({ available: false }), platform: "win32" });
    expect((await l.seal()).ok).toBe(false);
    expect(l.storage().secure).toBe(false);
  });

  it("BF_E2E=1 uses a plain test store that is not read outside E2E", async () => {
    core.secret = { kind: "api_key", secret: { key: KEY }, revision: 4 };
    const e2e = link({ storage: fakeStorage({ available: false }), e2e: true });
    expect(e2e.storage()).toEqual({ secure: true, backend: "e2e_plain" });
    expect(await e2e.seal()).toEqual({ ok: true });
    expect(fs.readFileSync(file, "utf8").startsWith("BFTEST1\n")).toBe(true);
    core.seen = [];
    await e2e.onCoreReady();
    expect(core.seen.some((s) => s.method === "PUT")).toBe(true);

    core.seen = [];
    await link({ storage: fakeStorage({ available: false }), e2e: false }).onCoreReady();
    expect(core.seen).toHaveLength(0); // the plaintext test file is ignored in a real run
  });

  it("a real encryption is used in E2E when it is available", async () => {
    core.secret = { kind: "api_key", secret: { key: KEY }, revision: 1 };
    await link({ e2e: true }).seal();
    expect(fs.readFileSync(file, "utf8")).not.toContain("BFTEST1");
  });
});

describe("damaged files and failures", () => {
  it.each([
    ["garbage bytes", Buffer.from([1, 2, 3, 4, 5])],
    ["not JSON after decryption", Buffer.from("not json".split("").map((c) => c.charCodeAt(0) ^ 0x5a))],
    ["wrong shape", Buffer.from(JSON.stringify({ kind: "x", secret: 1 }).split("").map((c) => c.charCodeAt(0) ^ 0x5a))],
    ["empty", Buffer.alloc(0)],
  ])("%s: no crash, nothing sent", async (_name, bytes) => {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, bytes);
    await expect(link().onCoreReady()).resolves.toBeUndefined();
    expect(core.seen).toHaveLength(0);
  });

  it("a decryptString that throws is survived", async () => {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, Buffer.from("zzz"));
    const st = fakeStorage();
    st.decryptString = () => {
      throw new Error("Decryption failed");
    };
    await expect(link({ storage: st }).onCoreReady()).resolves.toBeUndefined();
    expect(core.seen).toHaveLength(0);
  });

  it("a sealing error from the core or an unreachable core is reported, not thrown", async () => {
    up = false;
    expect((await link().seal()).ok).toBe(false);
    up = true;
    const dead = new FakeCore();
    await dead.start();
    const port = dead.port;
    await dead.stop();
    const r = await new ClaudeLink({ storage: fakeStorage(), file, core: () => ({ port, token: TOKEN }), e2e: false, log: () => undefined, platform: "linux", pollMs: 0 }).seal();
    expect(r.ok).toBe(false);
    expect(fs.existsSync(file)).toBe(false);
  });

  it("the secret is never written to the log of the shell", async () => {
    core.secret = { kind: "api_key", secret: { key: KEY }, revision: 1 };
    const l = link();
    await l.seal();
    fs.writeFileSync(file, Buffer.from("broken"));
    await link().onCoreReady();
    core.secret = { kind: "bogus", secret: { key: KEY }, revision: 2 };
    await l.seal();
    expect(logs.length).toBeGreaterThan(0);
    expect(logs.join("\n")).not.toContain(KEY);
  });
});

describe("BF_ANT_PATH", () => {
  it("points at resources/ant/ant.exe when the file exists, and leaves a set value alone", () => {
    const exe = path.join("/res", "ant", "ant.exe");
    expect(antEnv("/res", {}, (p) => p === exe)).toEqual({ BF_ANT_PATH: exe });
    expect(antEnv("/res", {}, () => false)).toEqual({});
    expect(antEnv("/res", { BF_ANT_PATH: "/fake/ant" }, () => true)).toEqual({});
  });
});
