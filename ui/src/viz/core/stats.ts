/**
 * The frame counter of the 3D window (ADR 0004 §5, `Ctrl+Shift+F`): frames per second — now (the last
 * second), the minimum and the mean since the counter was opened — and milliseconds per frame. Frames are
 * drawn on demand, so the rate counts only frames that follow each other (an idle view is not «0 кадров/с»):
 * a gap longer than `IDLE_MS` starts a new run. Pure: time comes as a parameter.
 */

export const IDLE_MS = 250;

export interface FpsSummary {
  /** frames per second over the last second of drawing */
  current: number;
  min: number;
  mean: number;
  /** CPU time of a frame (preparation and the draw call), mean ms */
  msPerFrame: number;
  frames: number;
}

export class FpsTracker {
  private last: number | null = null;
  /** intervals between consecutive frames within runs, ms */
  private gaps: number[] = [];
  private work: number[] = [];
  private recent: number[] = [];
  private minFps = Infinity;
  frames = 0;

  /** A frame was drawn at `now` (ms) and took `ms` of the main thread. */
  record(now: number, ms: number): void {
    this.frames++;
    this.work.push(ms);
    if (this.work.length > 10_000) this.work.shift();
    if (this.last !== null && now - this.last <= IDLE_MS && now > this.last) {
      const gap = now - this.last;
      this.gaps.push(gap);
      if (this.gaps.length > 10_000) this.gaps.shift();
      this.recent.push(now);
      while (this.recent.length && now - this.recent[0] > 1000) this.recent.shift();
      if (this.recent.length >= 5) this.minFps = Math.min(this.minFps, this.windowFps(now));
    } else {
      this.recent = [now];
    }
    this.last = now;
  }

  private windowFps(now: number): number {
    const first = this.recent[0];
    const span = now - first;
    return span > 0 ? ((this.recent.length - 1) * 1000) / span : 0;
  }

  summary(now: number): FpsSummary {
    const gaps = this.gaps;
    const meanGap = gaps.length ? gaps.reduce((s, g) => s + g, 0) / gaps.length : 0;
    const current = this.last !== null && now - this.last <= IDLE_MS && this.recent.length > 1 ? this.windowFps(this.last) : 0;
    const ms = this.work.length ? this.work.reduce((s, g) => s + g, 0) / this.work.length : 0;
    return { current, min: Number.isFinite(this.minFps) ? this.minFps : meanGap ? 1000 / meanGap : 0, mean: meanGap ? 1000 / meanGap : 0, msPerFrame: ms, frames: this.frames };
  }

  reset(): void {
    this.last = null;
    this.gaps = [];
    this.work = [];
    this.recent = [];
    this.minFps = Infinity;
    this.frames = 0;
  }
}

/** «1,2 ГБ», «340 МБ» — a size for the counter. */
export function bytesText(bytes: number): string {
  const mb = bytes / (1024 * 1024);
  if (mb >= 1024) return `${(mb / 1024).toFixed(1).replace(".", ",")} ГБ`;
  return mb >= 10 ? `${Math.round(mb)} МБ` : `${mb.toFixed(1).replace(".", ",")} МБ`;
}
