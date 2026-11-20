/**
 * The exact triangle under the cursor from the core (`POST /wireframes/pick`, ADR 0015 §4; moved out of
 * `session.ts`): asked after a pause of `PROBE_DELAY_MS` once the cursor stops, an earlier request still on its
 * way aborted, the last answer kept for its pixel (a click within 3 px uses it). No three.js.
 */
import type { SnapHit, ViewTransform } from "./snap";
import { PROBE_DELAY_MS, WF_KINDS, wireframeHit, type SessionView, type SnapSettings, type WireframeProbe } from "./tools";

export interface ProbeHost {
  readonly probe: WireframeProbe | null;
  readonly snap: SnapSettings;
  readonly view: SessionView | null;
}

type At = { x: number; y: number; clientX: number; clientY: number };

const near = (a: { x: number; y: number }, b: { x: number; y: number }) => Math.hypot(a.x - b.x, a.y - b.y) <= 3;

export class CoreProbe {
  private probed: { x: number; y: number; hit: SnapHit | null } | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private abort: AbortController | null = null;

  constructor(
    private readonly host: ProbeHost,
    private readonly transform: () => ViewTransform | null,
  ) {}

  /** The probe is wanted: the snap is on with a wireframe kind and there are wireframes to ask. */
  wanted(): boolean {
    const s = this.host.snap;
    return s.enabled && !!this.host.probe && s.kinds.some((k) => WF_KINDS.includes(k));
  }

  /** The core has answered for this pixel (within 3 px). */
  answered(at: { x: number; y: number }): boolean {
    return !!this.probed && near(this.probed, at);
  }

  /** The answer kept for this pixel, or null. */
  hitAt(at: { x: number; y: number }): SnapHit | null {
    return this.probed && near(this.probed, at) ? this.probed.hit : null;
  }

  /** Asks the core for the triangle under the cursor; an earlier request still on its way is aborted. */
  async run(e: At): Promise<SnapHit | null> {
    const ray = this.host.view?.ray(e.clientX, e.clientY);
    const t = this.transform();
    const probe = this.host.probe;
    if (!ray || !t || !probe) return null;
    this.abort?.abort();
    const abort = new AbortController();
    this.abort = abort;
    let hit: SnapHit | null = null;
    try {
      const found = await probe(ray, abort.signal);
      const s = this.host.snap;
      if (found) hit = wireframeHit(found.corners, found.name, e.x, e.y, ray, t, new Set(s.kinds), s.radius);
    } catch {
      hit = null;
    }
    if (abort.signal.aborted) return null;
    this.abort = null;
    this.probed = { x: e.x, y: e.y, hit };
    return hit;
  }

  /** After the pause the core is asked; `then` runs once it has answered. */
  schedule(at: At, then: () => void): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.run(at).then(then), PROBE_DELAY_MS);
  }

  /** The view changed: the kept answer is stale. */
  forget(): void {
    this.probed = null;
  }

  /** No pause running, no request on its way, no answer kept. */
  stop(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.abort?.abort();
    this.abort = null;
    this.probed = null;
  }
}
