// The helpers of the E2E of the Claude assistant (Э23, 23.20; ТЗ 7.32; ADR 0029 decisions 8, 11, 36): the mock of the Anthropic API
// (`core/tests/assistant/mock_anthropic.py`, a process on 127.0.0.1), the fake `ant` (`fake_ant.py`) and the checks that go over the files
// of the profile. Nothing here goes to the internet: the core gets `BF_ASSISTANT_BASE_URL` of the mock and `BF_ANT_PATH` of the fake CLI.
// Shared by `assistant.spec.ts` (one spec = one app per launch, one mock for the whole file).
import { expect, type Locator, type Page } from "@playwright/test";
import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { coreApi, runCommand } from "./helpers";

export const CORE_DIR = path.resolve(__dirname, "..", "..", "core");
/** The test key the mock accepts (`TEST_API_KEY` of mock_anthropic.py) and one it refuses with 401. */
export const TEST_KEY = "sk-ant-test-0000000000000000000000";
export const WRONG_KEY = "sk-ant-test-wrong0000000000000000";
/** The code the fake `ant` accepts (`DEFAULT_CODE` of fake_ant.py) and its tokens (the defaults of fake_ant.py, accepted by the mock). */
export const LOGIN_CODE = "TEST-CODE-123456";
export const ACCOUNT_TOKENS = ["sk-ant-oat01-test-access-000000000000", "sk-ant-ort01-test-refresh-00000000000"];

/** The Python of the core as the shell finds it (app/src/main/paths.ts): BF_CORE_PYTHON, the venv of `core/`, else `python`. */
export function corePython(): string {
  if (process.env.BF_CORE_PYTHON) return process.env.BF_CORE_PYTHON;
  const venv = process.platform === "win32" ? path.join(CORE_DIR, ".venv", "Scripts", "python.exe") : path.join(CORE_DIR, ".venv", "bin", "python");
  return fs.existsSync(venv) ? venv : "python";
}

/** A free port of 127.0.0.1 (the mock is started on it, so a restarted mock keeps the address the core was given). */
export function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.once("error", reject);
    s.listen(0, "127.0.0.1", () => {
      const { port } = s.address() as net.AddressInfo;
      s.close(() => resolve(port));
    });
  });
}

export interface MockRequest {
  index: number;
  method: string;
  path: string;
  status: number | null;
  auth: { kind: string; ok: boolean };
  stream: boolean;
  matched: boolean | null;
  body: { model?: string; messages?: { role: string; content: unknown }[] } & Record<string, unknown>;
}

export class MockAnthropic {
  private proc: ChildProcess | null = null;
  private dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-mock-"));
  readonly log: string[] = [];

  constructor(
    readonly port: number,
    private readonly scenarios: string[],
  ) {}

  get url(): string {
    return `http://127.0.0.1:${this.port}`;
  }

  /** Starts `python -m tests.assistant.mock_anthropic` in `core/` and waits for its port file. */
  async start(): Promise<void> {
    const portFile = path.join(this.dir, `port-${Date.now()}.txt`);
    const args = ["-m", "tests.assistant.mock_anthropic", "--port", String(this.port), "--port-file", portFile];
    for (const s of this.scenarios) args.push("--scenario", s);
    const proc = spawn(corePython(), args, { cwd: CORE_DIR, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, PYTHONUNBUFFERED: "1" } });
    this.proc = proc;
    proc.stdout?.on("data", (d: Buffer) => this.log.push(String(d)));
    proc.stderr?.on("data", (d: Buffer) => this.log.push(String(d)));
    await expect
      .poll(() => (fs.existsSync(portFile) ? Number(fs.readFileSync(portFile, "utf8").trim()) : 0), { timeout: 30_000, intervals: [100, 200, 400], message: `the mock did not start: ${this.log.join("")}` })
      .toBe(this.port);
  }

  /** SIGTERM and wait for the exit: the port is closed afterwards (the core sees a refused connection). */
  async stop(): Promise<void> {
    const proc = this.proc;
    this.proc = null;
    if (!proc || proc.exitCode !== null) return;
    const done = new Promise<void>((r) => proc.once("exit", () => r()));
    proc.kill("SIGTERM");
    await Promise.race([done, new Promise<void>((r) => setTimeout(r, 10_000))]);
    if (proc.exitCode === null) proc.kill("SIGKILL");
  }

  private async call<T>(method: string, route: string, body?: unknown): Promise<T> {
    const r = await fetch(`${this.url}${route}`, { method, headers: body === undefined ? {} : { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
    return (r.status === 204 ? null : await r.json()) as T;
  }

  /** The journal of the mock: every request it received (secrets are redacted by the mock itself). */
  async requests(): Promise<MockRequest[]> {
    return (await this.call<{ requests: MockRequest[] }>("GET", "/__requests")).requests;
  }

  async messages(): Promise<MockRequest[]> {
    return (await this.requests()).filter((r) => r.path === "/v1/messages");
  }

  async clear(): Promise<void> {
    await this.call("DELETE", "/__requests");
  }

  /** Replaces the scenario (by names of `core/tests/data/assistant/scenarios`). */
  async useScenario(...names: string[]): Promise<void> {
    await this.call("POST", "/__scenario", { names });
  }

  async fault(reply: Record<string, unknown>, times?: number): Promise<void> {
    await this.call("POST", "/__fault", { reply, times });
  }
}

/** The mock with the scenarios of the whole spec on a free port; `await mock.start()` before the launch of the app. */
export async function newMock(...scenarios: string[]): Promise<MockAnthropic> {
  return new MockAnthropic(await freePort(), scenarios);
}

/** `python -m tests.assistant.fake_ant --make-launcher DIR` → the path for `BF_ANT_PATH` (a script that runs the fake `ant`). */
export function makeFakeAnt(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-ant-"));
  const out = execFileSync(corePython(), ["-m", "tests.assistant.fake_ant", "--make-launcher", dir], { cwd: CORE_DIR, encoding: "utf8" });
  const launcher = out.trim().split(/\r?\n/).pop()!;
  expect(fs.existsSync(launcher), `the launcher of the fake ant: ${out}`).toBe(true);
  return launcher;
}

/** The environment of the app for the assistant: the mock as the API, the fake `ant`, the test store of the key (BF_E2E). */
export function assistantEnv(mock: MockAnthropic, ant: string, extra: Record<string, string> = {}): Record<string, string> {
  return {
    BF_E2E: "1",
    BF_ASSISTANT_BASE_URL: mock.url,
    BF_ANT_PATH: ant,
    FAKE_ANT_BASE_URL: mock.url, // written to the profile of the fake login (the core ignores it for an account, but the file is complete)
    ANTHROPIC_API_KEY: "", // a key of the environment would be the connection of kind `env` — the test starts without
    ...extra,
  };
}

// ---- the screen ----

export const dock = (page: Page) => page.getByRole("complementary", { name: "Правая панель" });
export const chat = (page: Page) => page.getByTestId("chat-panel");
export const connectionWindow = (page: Page) => page.getByRole("dialog", { name: "Claude: подключение" });

export async function online(page: Page): Promise<void> {
  await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
}

/** The right dock on the tab «Claude» (the command `panel.set` as a click on the tab is). */
export async function showChat(page: Page): Promise<void> {
  expect((await runCommand(page, "panel.set", { panel: "assistant", open: true })).ok).toBe(true);
  await expect(chat(page)).toBeVisible();
}

/** The bottom dock on «Журнал команд» → its table. */
export async function showLog(page: Page): Promise<Locator> {
  expect((await runCommand(page, "panel.set", { panel: "bottom", tab: "log", open: true })).ok).toBe(true);
  const table = page.getByRole("table", { name: "Журнал команд" });
  await expect(table).toBeVisible();
  return table;
}

export async function openConnection(page: Page): Promise<Locator> {
  // the link of the status bar (works without a project: the hook of the 3D window exists only with one)
  if ((await connectionWindow(page).count()) === 0) await page.getByTestId("claude-status").click();
  const win = connectionWindow(page);
  await expect(win).toBeVisible();
  return win;
}

export async function closeConnection(page: Page): Promise<void> {
  const win = connectionWindow(page);
  if ((await win.count()) === 0) return;
  await win.getByRole("button", { name: /Закрыть/ }).first().click();
  await expect(win).toHaveCount(0);
}

/** A question through the composer of the panel (Enter sends). */
export async function ask(page: Page, text: string): Promise<void> {
  const input = page.getByTestId("composer-input");
  await expect(input).toBeEnabled({ timeout: 30_000 });
  await input.fill(text);
  await input.press("Enter");
}

/** The answer of the model is complete: the stop button is gone and `text` is in the feed. */
export async function answered(page: Page, text: string | RegExp, timeout = 45_000): Promise<void> {
  await expect(chat(page).getByTestId("msg-assistant").filter({ hasText: text }).last()).toBeVisible({ timeout });
  await expect(page.getByTestId("composer-stop")).toHaveCount(0, { timeout });
}

// ---- the core ----

export interface UsageModel {
  model: string;
  title: string;
  requests: number;
  tokens: { input: number; output: number; cache_read: number; cache_write: number };
  cost_usd: number | null;
}
export interface UsagePeriod extends Omit<UsageModel, "model" | "title"> {
  by_model: UsageModel[];
}
export interface Usage {
  today: UsagePeriod;
  days30: UsagePeriod;
  all: UsagePeriod;
}

export const usage = async (page: Page) => {
  const r = await coreApi<Usage>(page, "GET", "/assistant/usage");
  expect(r.status, JSON.stringify(r.body)).toBe(200);
  return r.body;
};

/** The cost as the window prints it (`fmtUsd` of ui/src/modules/assistant/UsageTable.tsx): «≈ $0,4123», «≈ $12,35», «—». */
export const money = (v: number | null): string => (v === null ? "—" : `≈ $${v.toFixed(v < 1 ? 4 : 2).replace(".", ",")}`);
/** The number as the table prints it (ru-RU, no-break spaces between the thousands). */
export const num = (n: number): string => new Intl.NumberFormat("ru-RU").format(n);

// ---- the files ----

/** Every file under `roots` (symbolic links are not followed); a file that cannot be read is skipped by the caller. */
export function walk(roots: string[]): string[] {
  const out: string[] = [];
  const visit = (p: string): void => {
    let st: fs.Stats;
    try {
      st = fs.lstatSync(p);
    } catch {
      return;
    }
    if (st.isSymbolicLink()) return;
    if (st.isDirectory()) {
      for (const n of fs.readdirSync(p)) visit(path.join(p, n));
    } else if (st.isFile()) out.push(p);
  };
  roots.forEach(visit);
  return out;
}

/** The files (of `roots`) that contain `secret` as text, as UTF-8 or as UTF-16 (a Windows log); a file of a gigabyte is not read whole. */
export function filesWith(roots: string[], secret: string): string[] {
  const utf8 = Buffer.from(secret, "utf8");
  const utf16 = Buffer.from(secret, "utf16le");
  const hits: string[] = [];
  for (const f of walk(roots)) {
    try {
      if (fs.statSync(f).size > 256 * 1024 * 1024) continue;
      const data = fs.readFileSync(f);
      if (data.includes(utf8) || data.includes(utf16)) hits.push(f);
    } catch {
      /* a file locked by the app: not a place a secret is kept */
    }
  }
  return hits;
}
