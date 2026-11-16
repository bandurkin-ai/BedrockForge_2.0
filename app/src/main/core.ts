// Lifecycle of the bf-core process (ТЗ 6.4): start with a random token, wait for BF-READY,
// check /health, restart on crash (3 per 5 min), graceful shutdown then kill of the tree.
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { randomBytes } from "node:crypto";
import { EventEmitter } from "node:events";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { createInterface } from "node:readline";
import { parseReadyLine, RestartBudget, type ReadyInfo } from "./ready";

export interface CoreCommand {
  file: string;
  args: string[];
  cwd: string;
}

export interface CoreInfo {
  baseUrl: string;
  token: string;
  version: string;
  port: number;
}

export interface CoreOptions {
  command: CoreCommand;
  dataDir: string;
  /** Folder of projects (ТЗ 6.5), read at every start: «Настройка» may change it. */
  projectsDir?: () => string;
  logFile: string;
  readyTimeoutMs?: number;
  healthIntervalMs?: number;
}

function httpJson(port: number, token: string, method: string, pathname: string, timeoutMs = 3000): Promise<{ status: number; body: unknown }> {
  return new Promise((resolve, reject) => {
    const req = http.request(
      { host: "127.0.0.1", port, method, path: pathname, timeout: timeoutMs, headers: { "X-BF-Token": token } },
      (res) => {
        let text = "";
        res.setEncoding("utf8");
        res.on("data", (c) => (text += c));
        res.on("end", () => {
          let body: unknown = null;
          try {
            body = JSON.parse(text);
          } catch {
            /* not JSON */
          }
          resolve({ status: res.statusCode ?? 0, body });
        });
      },
    );
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", reject);
    req.end();
  });
}

export class CoreProcess extends EventEmitter {
  private child: ChildProcess | null = null;
  private info: CoreInfo | null = null;
  private stopping = false;
  private restarting: Promise<void> | null = null;
  private readonly budget = new RestartBudget();
  /** One token per shell session: the window keeps working after a restart on the same port. */
  private readonly token = randomBytes(32).toString("hex");
  private lastPort = 0;
  private logStream: fs.WriteStream | null = null;
  private watchdog: NodeJS.Timeout | null = null;
  private misses = 0;

  constructor(private readonly opts: CoreOptions) {
    super();
  }

  current(): CoreInfo | null {
    return this.info;
  }

  private log(line: string): void {
    try {
      if (!this.logStream) {
        fs.mkdirSync(path.dirname(this.opts.logFile), { recursive: true });
        this.logStream = fs.createWriteStream(this.opts.logFile, { flags: "a" });
        this.logStream.on("error", () => (this.logStream = null));
      }
      this.logStream.write(`${new Date().toISOString()} ${line}\n`);
    } catch {
      /* logging never breaks the app */
    }
  }

  logPath(): string {
    return this.opts.logFile;
  }

  async start(port = 0): Promise<CoreInfo> {
    const token = this.token;
    const { file, args, cwd } = this.opts.command;
    const projects = this.opts.projectsDir?.();
    const fullArgs = [...args, "--port", String(port), "--token", token, "--data", this.opts.dataDir, "--watch-stdin"];
    if (projects) fullArgs.push("--projects", projects);
    this.log(`start ${file} ${args.join(" ")} (cwd ${cwd})`);
    const child = spawn(file, fullArgs, {
      cwd,
      env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" },
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    this.child = child;
    child.stderr?.setEncoding("utf8");
    child.stderr?.on("data", (d: string) => this.log(`core! ${d.trimEnd()}`));

    try {
      return await this.handshake(child, token);
    } catch (e) {
      this.killTree(child); // no orphan bf-core after a failed start
      if (this.child === child) this.child = null;
      throw e;
    }
  }

  private async handshake(child: ChildProcess, token: string): Promise<CoreInfo> {
    const ready = await new Promise<ReadyInfo>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Ядро не ответило за 30 с")), this.opts.readyTimeoutMs ?? 30_000);
      const rl = createInterface({ input: child.stdout! });
      rl.on("line", (line) => {
        const r = parseReadyLine(line);
        if (r) {
          clearTimeout(timer);
          resolve(r);
        } else {
          this.log(`core> ${line}`);
        }
      });
      child.once("exit", (code) => {
        clearTimeout(timer);
        reject(new Error(`Ядро завершилось при запуске (код ${code})`));
      });
      child.once("error", (e) => {
        clearTimeout(timer);
        reject(e);
      });
    });

    const health = await httpJson(ready.port, token, "GET", "/api/v1/health");
    const body = health.body as { status?: string; version?: string } | null;
    if (health.status !== 200 || body?.status !== "ok") throw new Error(`Ядро ответило ${health.status}`);
    if (body.version !== ready.version) throw new Error(`Версии ядра не совпадают: ${body.version} / ${ready.version}`);

    this.info = { baseUrl: `http://127.0.0.1:${ready.port}`, token, version: ready.version, port: ready.port };
    this.lastPort = ready.port;
    this.log(`ready port=${ready.port} version=${ready.version}`);
    child.once("exit", (code, signal) => void this.onExit(code, signal));
    this.startWatchdog(child);
    return this.info;
  }

  /** ADR 0002: 3 missed /health in a row → the core is restarted. */
  private startWatchdog(child: ChildProcess): void {
    this.stopWatchdog();
    this.misses = 0;
    this.watchdog = setInterval(async () => {
      const info = this.info;
      if (!info || this.child !== child) return;
      let ok = false;
      try {
        ok = (await httpJson(info.port, info.token, "GET", "/api/v1/health", 3000)).status === 200;
      } catch {
        ok = false;
      }
      if (ok) {
        if (this.misses) this.emit("state", "online");
        this.misses = 0;
        return;
      }
      this.misses++;
      this.emit("state", "offline");
      this.log(`health missed (${this.misses})`);
      if (this.misses >= 3) this.killTree(child);
    }, this.opts.healthIntervalMs ?? 5000);
  }

  private stopWatchdog(): void {
    if (this.watchdog) clearInterval(this.watchdog);
    this.watchdog = null;
  }

  private async onExit(code: number | null, signal: NodeJS.Signals | null): Promise<void> {
    if (this.restarting) return; // a restart in progress owns the process slot
    this.child = null;
    this.info = null;
    if (this.stopping) return;
    this.stopWatchdog();
    this.log(`core exited unexpectedly: code=${code} signal=${signal}`);
    this.emit("state", "offline");
    if (!this.budget.tryConsume(Date.now())) {
      this.emit("failed", new Error("Ядро не запускается: превышено число перезапусков (3 за 5 мин)"));
      return;
    }
    await this.restart();
  }

  /**
   * Restart on the previous port (the window CSP is bound to it); a busy port → a new one.
   * Only one restart runs at a time; calls during it share the same promise. `manual` — the retry of
   * the «Ядро недоступно» screen: one attempt, and only when no core is running.
   */
  restart(manual = false): Promise<void> {
    if (this.stopping) return Promise.resolve();
    if (this.restarting) return this.restarting;
    if (manual && (this.child || this.info)) return Promise.resolve();
    this.restarting = (async () => {
      const before = this.lastPort;
      try {
        const info = manual ? await this.start(before) : await this.start(before).catch(() => this.start(0));
        this.emit("restarted", info, info.port !== before);
      } catch (e) {
        this.emit("failed", e);
      } finally {
        this.restarting = null;
      }
    })();
    return this.restarting;
  }

  async stop(): Promise<void> {
    this.stopping = true;
    this.stopWatchdog();
    const child = this.child;
    const info = this.info;
    if (!child) return;
    const exited = new Promise<void>((resolve) => child.once("exit", () => resolve()));
    try {
      if (info) await httpJson(info.port, info.token, "POST", "/api/v1/shutdown", 2000);
    } catch {
      /* fall through to kill */
    }
    const timeout = new Promise<"timeout">((r) => setTimeout(() => r("timeout"), 10_000));
    if ((await Promise.race([exited, timeout])) === "timeout") this.killTree(child);
  }

  private killTree(child: ChildProcess): void {
    if (!child.pid) return;
    this.log(`killing core tree pid=${child.pid}`);
    if (process.platform === "win32") spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true });
    else child.kill("SIGKILL");
  }
}
