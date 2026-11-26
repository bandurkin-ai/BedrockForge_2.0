// Claude assistant, shell side (ADR 0029 decisions 9 and 12; ТЗ 6.10, S5): the connection secret is kept on disk only
// as `assistant/connection.bin`, encrypted by `safeStorage` (DPAPI) of this process. The core holds the secret in
// memory only: after BF-READY (and after every core restart) the shell hands it over with `PUT /connection/secret`;
// after a new connection or a token rotation the core raises `seal_needed` and the shell takes `GET /connection/secret`
// and encrypts it (`seal`). The «shell only» routes are called WITHOUT an `Origin` header (a window always sends one).
// The secret never goes to a log of the shell: only status codes are written.
import fs from "node:fs";
import http from "node:http";
import path from "node:path";

/** The part of Electron's `safeStorage` used here (replaced in tests). */
export interface SafeStorageLike {
  isEncryptionAvailable(): boolean;
  encryptString(plain: string): Buffer;
  decryptString(data: Buffer): string;
  /** Linux only: "basic_text" is not encryption, "unknown" — not chosen yet. */
  getSelectedStorageBackend?(): string;
}

export interface CoreEndpoint {
  port: number;
  token: string;
}

export interface HttpResult {
  status: number;
  body: unknown;
}

export type HttpFn = (ep: CoreEndpoint, method: string, pathname: string, body?: unknown) => Promise<HttpResult>;

export interface ClaudeDeps {
  storage: SafeStorageLike;
  /** `<userData>/assistant/connection.bin`. */
  file: string;
  /** The running core, or null while it is down. */
  core: () => CoreEndpoint | null;
  /** BF_E2E=1: a plain «test» store is allowed when there is no encryption. */
  e2e: boolean;
  log: (line: string) => void;
  platform?: string;
  /** Replaced in tests; the default talks to 127.0.0.1 with the launch token and no `Origin`. */
  request?: HttpFn;
  /** Period of the `seal_needed` check, ms (0 — no polling). */
  pollMs?: number;
  /** Pause between attempts to hand the secret to a core that has just started, ms. */
  retryMs?: number;
}

export interface SealResult {
  ok: boolean;
  message?: string;
}

export interface StorageInfo {
  /** The secret can be stored (real encryption, or the E2E test store). `false` — `claudeSeal` refuses. */
  secure: boolean;
  backend: string;
}

const PREFIX = "/api/v1/assistant";
const TEST_MARK = "BFTEST1\n";
const NO_STORAGE_MESSAGE = "Хранилище ключей ОС недоступно: задайте ANTHROPIC_API_KEY";
const MAX_FILE_BYTES = 256 * 1024;

/** The core is called without `Origin` (shell-only routes) and with the launch token. */
export const coreRequest: HttpFn = (ep, method, pathname, body) =>
  new Promise((resolve, reject) => {
    const payload = body === undefined ? null : Buffer.from(JSON.stringify(body), "utf8");
    const headers: Record<string, string | number> = { "X-BF-Token": ep.token };
    if (payload) {
      headers["Content-Type"] = "application/json";
      headers["Content-Length"] = payload.length;
    }
    const req = http.request({ host: "127.0.0.1", port: ep.port, method, path: pathname, timeout: 5000, headers }, (res) => {
      let text = "";
      res.setEncoding("utf8");
      res.on("data", (c) => (text += c));
      res.on("end", () => {
        let parsed: unknown = null;
        try {
          parsed = text ? JSON.parse(text) : null;
        } catch {
          /* not JSON */
        }
        resolve({ status: res.statusCode ?? 0, body: parsed });
      });
    });
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", reject);
    if (payload) req.write(payload);
    req.end();
  });

interface Stored {
  kind: "api_key" | "account";
  secret: Record<string, unknown>;
}

function isObject(x: unknown): x is Record<string, unknown> {
  return typeof x === "object" && x !== null && !Array.isArray(x);
}

/** `BF_ANT_PATH` for the core: `resources/ant/ant.exe` when the build has it; a value set by the caller wins. */
export function antEnv(resourcesPath: string, env: NodeJS.ProcessEnv = process.env, exists: (p: string) => boolean = fs.existsSync): Record<string, string> {
  if (env.BF_ANT_PATH) return {};
  const exe = path.join(resourcesPath, "ant", "ant.exe");
  return exists(exe) ? { BF_ANT_PATH: exe } : {};
}

export class ClaudeLink {
  private readonly request: HttpFn;
  private timer: NodeJS.Timeout | null = null;
  private chain: Promise<unknown> = Promise.resolve();
  private lastNote = "";

  constructor(private readonly d: ClaudeDeps) {
    this.request = d.request ?? coreRequest;
  }

  private encrypted(): boolean {
    try {
      if (!this.d.storage.isEncryptionAvailable()) return false;
      const b = this.d.storage.getSelectedStorageBackend?.();
      return !(this.d.platform ?? process.platform).startsWith("linux") || (b !== undefined && b !== "basic_text" && b !== "unknown");
    } catch {
      return false;
    }
  }

  /** Synchronous answer for the window: can the key be kept (`secure`) and by what (`backend`). */
  storage(): StorageInfo {
    let backend: string;
    try {
      backend = this.d.storage.getSelectedStorageBackend?.() ?? ((this.d.platform ?? process.platform) === "win32" ? "dpapi" : "os");
    } catch {
      backend = "unknown";
    }
    if (this.encrypted()) return { secure: true, backend };
    if (this.d.e2e) return { secure: true, backend: "e2e_plain" };
    return { secure: false, backend };
  }

  private note(line: string): void {
    if (line === this.lastNote) return; // a repeated condition is logged once
    this.lastNote = line;
    this.d.log(`claude: ${line}`);
  }

  /** Runs `fn` after the previous seal/push finished: the file is never written by two calls at once. */
  private serial<T>(fn: () => Promise<T>): Promise<T> {
    const next = this.chain.then(fn, fn);
    this.chain = next.catch(() => undefined);
    return next;
  }

  /** The stored secret, or null (no file, not readable, damaged, a test file outside E2E). Never throws. */
  private readStored(): Stored | null {
    let raw: Buffer;
    try {
      const st = fs.statSync(this.d.file);
      if (!st.isFile() || st.size > MAX_FILE_BYTES) return null;
      raw = fs.readFileSync(this.d.file);
    } catch {
      return null;
    }
    let text: string;
    try {
      if (raw.subarray(0, TEST_MARK.length).toString("latin1") === TEST_MARK) {
        if (!this.d.e2e) return null;
        text = raw.subarray(TEST_MARK.length).toString("utf8");
      } else {
        if (!this.encrypted()) return null;
        text = this.d.storage.decryptString(raw);
      }
      const data: unknown = JSON.parse(text);
      if (!isObject(data) || (data.kind !== "api_key" && data.kind !== "account") || !isObject(data.secret)) throw new Error("shape");
      return { kind: data.kind, secret: data.secret };
    } catch {
      this.note("connection.bin не читается (повреждён или создан другим пользователем) — подключение нужно ввести заново");
      return null;
    }
  }

  private writeStored(s: Stored): void {
    const json = JSON.stringify({ v: 1, kind: s.kind, secret: s.secret });
    const data = this.encrypted() ? this.d.storage.encryptString(json) : Buffer.concat([Buffer.from(TEST_MARK, "latin1"), Buffer.from(json, "utf8")]);
    const tmp = `${this.d.file}.${process.pid}.tmp`;
    fs.mkdirSync(path.dirname(this.d.file), { recursive: true });
    try {
      fs.writeFileSync(tmp, data, { mode: 0o600 });
      fs.renameSync(tmp, this.d.file);
    } catch (e) {
      try {
        fs.rmSync(tmp, { force: true });
      } catch {
        /* ignore */
      }
      throw e;
    }
  }

  /**
   * `window.bf.claudeSeal()`: takes the secret of the connection made in the window from the core, encrypts it into
   * `connection.bin` and confirms the revision. Nothing to store (a key of the environment) is not an error.
   */
  seal(): Promise<SealResult> {
    return this.serial(async () => {
      if (!this.storage().secure) return { ok: false, message: NO_STORAGE_MESSAGE };
      const ep = this.d.core();
      if (!ep) return { ok: false, message: "Вычислительное ядро недоступно: ключ не сохранён" };
      let got: HttpResult;
      try {
        got = await this.request(ep, "GET", `${PREFIX}/connection/secret`);
      } catch {
        return { ok: false, message: "Ядро не ответило: ключ не сохранён" };
      }
      if (got.status === 204) return { ok: true };
      const b = got.body;
      if (got.status !== 200 || !isObject(b) || (b.kind !== "api_key" && b.kind !== "account") || !isObject(b.secret) || typeof b.revision !== "number") {
        this.note(`GET connection/secret → ${got.status}`);
        return { ok: false, message: "Ядро не отдало ключ для сохранения" };
      }
      try {
        this.writeStored({ kind: b.kind, secret: b.secret });
      } catch (e) {
        this.note(`connection.bin не записан: ${(e as NodeJS.ErrnoException).code ?? "error"}`);
        return { ok: false, message: "Не удалось записать ключ в хранилище профиля" };
      }
      try {
        const r = await this.request(ep, "POST", `${PREFIX}/connection/sealed`, { revision: b.revision });
        if (r.status !== 200 && r.status !== 204) this.note(`POST connection/sealed → ${r.status}`);
      } catch {
        // The file is written; the next check of `seal_needed` repeats the confirmation.
        this.note("POST connection/sealed не дошёл до ядра");
      }
      return { ok: true };
    });
  }

  /** `window.bf.claudeForget()`: «Выйти» — the encrypted copy is removed (the window has already called `DELETE /connection`). */
  async forget(): Promise<void> {
    await this.serial(async () => {
      try {
        fs.rmSync(this.d.file, { force: true });
      } catch (e) {
        this.note(`connection.bin не удалён: ${(e as NodeJS.ErrnoException).code ?? "error"}`);
      }
    });
  }

  /** After BF-READY and after every restart of the core: hand the stored secret over, then watch `seal_needed`. */
  onCoreReady(): Promise<void> {
    return this.serial(async () => {
      this.startPolling();
      const stored = this.readStored();
      if (!stored) return;
      for (let attempt = 0; attempt < 3; attempt++) {
        const ep = this.d.core();
        if (!ep) return;
        try {
          const r = await this.request(ep, "PUT", `${PREFIX}/connection/secret`, {
            kind: stored.kind,
            secret: stored.secret,
            secure_storage: this.storage().secure,
          });
          if (r.status !== 200) this.note(`PUT connection/secret → ${r.status}`);
          return;
        } catch {
          await new Promise((res) => setTimeout(res, this.d.retryMs ?? 500));
        }
      }
      this.note("ядро не приняло подключение (нет ответа)");
    });
  }

  private startPolling(): void {
    const ms = this.d.pollMs ?? 30_000;
    if (this.timer || ms <= 0) return;
    this.timer = setInterval(() => void this.checkSeal(), ms);
    this.timer.unref?.();
  }

  /** The core rotated a token (or the window was closed before sealing): `seal_needed` in `/status` → seal. */
  async checkSeal(): Promise<void> {
    const ep = this.d.core();
    if (!ep || !this.storage().secure) return;
    try {
      const r = await this.request(ep, "GET", `${PREFIX}/status`);
      if (r.status === 200 && isObject(r.body) && r.body.seal_needed === true) await this.seal();
    } catch {
      /* the core is restarting: the next tick */
    }
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }
}
