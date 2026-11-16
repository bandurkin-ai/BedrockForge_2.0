/**
 * Registry of commands and the single way to run them (ТЗ 7.2): argument check, availability,
 * confirmation of data-changing calls from Claude, command log. Pure: React and dialogs are injected.
 */
import { EMPTY_CONTEXT, unavailableReason, validateArgs, type Command, type CommandContext, type CommandSource } from "./commands";

export interface LogEntry {
  id: number;
  time: Date;
  source: CommandSource;
  commandId: string;
  title: string;
  args: Record<string, unknown>;
  result: string;
  ok: boolean;
}

export interface RunResult {
  ok: boolean;
  result: string;
}

export interface RegistryHooks {
  /** Current state used for availability. */
  context: () => CommandContext;
  /** Asks the user to approve a command requested by Claude. */
  confirm: (command: Command, args: Record<string, unknown>) => Promise<boolean>;
}

export const LOG_LIMIT = 200;
export const DECLINED = "Пользователь отклонил действие";

export class CommandRegistry {
  private readonly commands = new Map<string, Command>();
  private log: LogEntry[] = [];
  private nextLogId = 1;
  private readonly listeners = new Set<() => void>();
  private ver = 0;
  hooks: RegistryHooks = { context: () => EMPTY_CONTEXT, confirm: async () => false };

  register(commands: Command[]): void {
    for (const c of commands) {
      if (this.commands.has(c.id)) throw new Error(`Command ${c.id} is registered twice`);
      this.commands.set(c.id, c);
    }
    this.emit();
  }

  /** Replaces the handler of a declared command (modules bind handlers to live state). */
  bind(id: string, run: Command["run"], unavailable?: Command["unavailable"]): void {
    const c = this.commands.get(id);
    if (!c) throw new Error(`Unknown command ${id}`);
    this.commands.set(id, { ...c, run, unavailable: unavailable ?? c.unavailable, stage: undefined });
    this.emit();
  }

  get(id: string): Command | undefined {
    return this.commands.get(id);
  }

  list(): Command[] {
    return [...this.commands.values()];
  }

  reason(id: string, ctx: CommandContext = this.hooks.context()): string | null {
    const c = this.commands.get(id);
    return c ? unavailableReason(c, ctx) : "Неизвестная команда";
  }

  entries(): readonly LogEntry[] {
    return this.log;
  }

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  /** Changes on every registration, binding and log entry (for useSyncExternalStore). */
  version = (): number => this.ver;

  private emit(): void {
    this.ver++;
    for (const fn of this.listeners) fn();
  }

  private record(c: Command, args: Record<string, unknown>, source: CommandSource, r: RunResult): RunResult {
    if (c.silent) return r;
    const entry: LogEntry = { id: this.nextLogId++, time: new Date(), source, commandId: c.id, title: c.title, args, result: r.result, ok: r.ok };
    this.log = [entry, ...this.log].slice(0, LOG_LIMIT);
    this.emit();
    return r;
  }

  /** Runs a command; never throws: errors come back as a Russian text. */
  async execute(id: string, args: Record<string, unknown> = {}, source: CommandSource = "user"): Promise<RunResult> {
    const c = this.commands.get(id);
    if (!c) return { ok: false, result: `Неизвестная команда «${id}»` };
    if (source === "claude" && c.uiOnly) return this.record(c, args, source, { ok: false, result: "Команда доступна только из интерфейса" });
    const bad = validateArgs(c, args);
    if (bad) return this.record(c, args, source, { ok: false, result: bad });
    const reason = unavailableReason(c, this.hooks.context());
    if (reason) return this.record(c, args, source, { ok: false, result: reason });
    if (source === "claude" && c.confirm && !(await this.hooks.confirm(c, args))) {
      return this.record(c, args, source, { ok: false, result: DECLINED });
    }
    try {
      const out = await c.run!(args, source);
      return this.record(c, args, source, { ok: true, result: out || "Выполнено" });
    } catch (e) {
      return this.record(c, args, source, { ok: false, result: e instanceof Error ? e.message : String(e) });
    }
  }
}

/** Formats arguments for the log: key=value. */
export function formatArgs(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(", ");
}
