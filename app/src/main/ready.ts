// Parsing of the core handshake line and restart policy (pure; tested with vitest).

export interface ReadyInfo {
  port: number;
  version: string;
}

/** `BF-READY port=<n> version=<v>` -> {port, version}; null for any other line. */
export function parseReadyLine(line: string): ReadyInfo | null {
  const m = /^BF-READY\s+port=(\d{1,5})\s+version=(\S+)\s*$/.exec(line.trim());
  if (!m) return null;
  const port = Number(m[1]);
  if (port < 1 || port > 65535) return null;
  return { port, version: m[2] };
}

/** Up to `max` restarts within `windowMs` (ТЗ 6.4: 3 per 5 minutes). */
export class RestartBudget {
  private stamps: number[] = [];
  constructor(
    private readonly max = 3,
    private readonly windowMs = 5 * 60_000,
  ) {}

  tryConsume(now: number): boolean {
    this.stamps = this.stamps.filter((t) => now - t < this.windowMs);
    if (this.stamps.length >= this.max) return false;
    this.stamps.push(now);
    return true;
  }
}
