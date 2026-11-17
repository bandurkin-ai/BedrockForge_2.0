/**
 * Frames on demand (ADR 0004 §1; BF `requestRender` 2799): `invalidate()` asks for one
 * `requestAnimationFrame`; frames go on only while an animation (a smooth turn) runs. Every change bumps a
 * counter, and a frame drawn after the last change makes the view idle — «кадр готов» for the E2E hook.
 * While the context is lost the loop is paused.
 */

export type Animation = (now: number) => boolean;

export class FrameLoop {
  private requested = false;
  private raf = 0;
  private readonly animations = new Set<Animation>();
  private seq = 0;
  private drawnSeq = -1;
  private stopped = false;
  paused = false;
  /** frames drawn since the start */
  frames = 0;
  lastFrameMs = 0;

  constructor(private readonly draw: (now: number) => void) {}

  /** Something changed: one more frame. */
  invalidate(): void {
    this.seq++;
    this.schedule();
  }

  /** Runs `step` before every frame until it returns false (a newer turn returns false for the old one). */
  animate(step: Animation): void {
    this.animations.add(step);
    this.invalidate();
  }

  get animating(): boolean {
    return this.animations.size > 0;
  }

  /** No frame pending, no animation, the last change drawn. */
  get idle(): boolean {
    return !this.requested && this.animations.size === 0 && this.drawnSeq === this.seq && this.frames > 0;
  }

  pause(on: boolean): void {
    this.paused = on;
    if (on && this.raf) {
      cancelAnimationFrame(this.raf);
      this.raf = 0;
      this.requested = false;
    }
    if (!on) this.invalidate();
  }

  dispose(): void {
    this.stopped = true;
    if (this.raf) cancelAnimationFrame(this.raf);
    this.animations.clear();
  }

  private schedule(): void {
    if (this.requested || this.paused || this.stopped) return;
    this.requested = true;
    this.raf = requestAnimationFrame((now) => this.tick(now));
  }

  private tick(now: number): void {
    this.requested = false;
    this.raf = 0;
    if (this.paused || this.stopped) return;
    for (const step of [...this.animations]) {
      let more = false;
      try {
        more = step(now);
      } catch {
        more = false;
      }
      if (!more) this.animations.delete(step);
    }
    const seq = this.seq;
    const t0 = performance.now();
    this.draw(now);
    this.lastFrameMs = performance.now() - t0;
    this.drawnSeq = seq;
    this.frames++;
    if (this.animations.size) {
      this.seq++;
      this.schedule();
    }
  }
}
