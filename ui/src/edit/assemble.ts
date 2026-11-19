/**
 * «Собрать из контуров» (ТЗ 7.23; ADR 0015 §6 «Предпросмотр», §7 «Тай-линии»; BF `viz/AssembleBar.tsx`), the pure
 * part: what the preview and the operation get from the working copy. The contours go in the order they were
 * clicked (`EditSession.assemble`), with their float64 coordinates as they are (a vertex without a height goes
 * as null — the core refuses it, never takes it for 0); the tie lines are those the operation `stitch` would
 * take from the same lines (`ops/run_lines.py`): an open line marked `ROLE = TIE`, or an unmarked one of two
 * vertices — and only when both ends lie on vertices of the picked contours (≤ `TIE_SNAP` m), since a tie of other
 * contours makes the stitch refuse. «Замкнуть торцы» — flat caps at both ends, else open ends.
 *
 * `PreviewScheduler`: the preview is asked a pause (`PREVIEW_DELAY_MS`) after the last change; a newer request
 * aborts the one in flight and the answer of an older one is dropped. No three.js, no React.
 */
import { ROLE_FIELD, TIE } from "./io";
import { lineById, type EditLayer, type EditLine, type Vec3 } from "./model";

/** The pause after the last change before the preview is asked. */
export const PREVIEW_DELAY_MS = 250;
/** How far a tie end may be from a vertex of a contour, m (as `ops/run_lines.py`). */
export const TIE_SNAP = 1e-3;
/** The cap of «Замкнуть торцы» (a flat lid by earcut); open — none. */
export const CLOSED_CAP = "flat";
export const OPEN_CAP = "none";
export const CAP_DISTANCE = 0.5;

/** The lines one assembly is built from: the contours in the order clicked and the tie lines between them. */
export interface AssemblyInputs {
  contours: readonly EditLine[];
  ties: readonly EditLine[];
  closed: boolean;
}

type Coord = number | null;

/** `POST /geometry/stitch/preview`. */
export interface PreviewBody {
  contours: Coord[][][];
  ties: Coord[][][];
  start_cap: string;
  end_cap: string;
  cap_distance: number;
}

/** The parameters of the operation `stitch` (`POST /operations`). */
export interface StitchParams {
  contours: { layer_id: number; lines: number[] }[];
  start_cap: string;
  end_cap: string;
  cap_distance: number;
}

const isClosedContour = (l: EditLine | undefined): l is EditLine => !!l && l.closed && l.points.length >= 3;

export function isMarkedTie(line: EditLine): boolean {
  return !line.closed && String(line.attrs[ROLE_FIELD] ?? "").trim().toUpperCase() === TIE;
}

function onVertex(p: Vec3, contours: readonly EditLine[]): boolean {
  const lim = TIE_SNAP * TIE_SNAP;
  for (const c of contours) {
    for (const q of c.points) {
      const dx = q[0] - p[0];
      const dy = q[1] - p[1];
      const dz = q[2] - p[2];
      if (dx * dx + dy * dy + dz * dz <= lim) return true;
    }
  }
  return false;
}

/** The tie lines of these contours among the open lines of the layer. */
export function assemblyTies(layer: EditLayer, contours: readonly EditLine[]): EditLine[] {
  if (contours.length < 2) return [];
  const own = new Set(contours.map((c) => c.id));
  return layer.lines.filter((l) => {
    if (l.closed || own.has(l.id) || l.points.length < 2) return false;
    if (!isMarkedTie(l) && l.points.length !== 2) return false;
    return onVertex(l.points[0], contours) && onVertex(l.points[l.points.length - 1], contours);
  });
}

/** What the assembly is built from now; the contours that are gone or no longer closed drop out. */
export function assemblyInputs(layer: EditLayer, order: readonly number[], closed: boolean): AssemblyInputs {
  const contours = order.map((id) => lineById(layer, id)).filter(isClosedContour);
  return { contours, ties: assemblyTies(layer, contours), closed };
}

/** The same lines (the working copy shares the lines an edit did not touch) and the same caps. */
export function sameInputs(a: AssemblyInputs | null, b: AssemblyInputs | null): boolean {
  if (!a || !b) return a === b;
  const same = (x: readonly EditLine[], y: readonly EditLine[]) => x.length === y.length && x.every((l, i) => l === y[i]);
  return a.closed === b.closed && same(a.contours, b.contours) && same(a.ties, b.ties);
}

const coords = (points: readonly Vec3[]): Coord[][] => points.map((p) => p.map((c) => (Number.isFinite(c) ? c : null)));
const caps = (closed: boolean) => (closed ? CLOSED_CAP : OPEN_CAP);

export function previewBody(inputs: AssemblyInputs): PreviewBody {
  const cap = caps(inputs.closed);
  return {
    contours: inputs.contours.map((c) => coords(c.points)),
    ties: inputs.ties.map((t) => coords([t.points[0], t.points[t.points.length - 1]])),
    start_cap: cap,
    end_cap: cap,
    cap_distance: CAP_DISTANCE,
  };
}

/**
 * The operation over the saved layer: the stable numbers (`lid`) of the contours in the order clicked, then of
 * the tie lines. A line not saved yet has none — null (the caller saves the layer first).
 */
export function stitchParams(layerId: number, inputs: AssemblyInputs): StitchParams | null {
  const lines = [...inputs.contours, ...inputs.ties].map((l) => l.src);
  if (lines.some((lid) => !Number.isInteger(lid) || lid < 0)) return null;
  const cap = caps(inputs.closed);
  return { contours: [{ layer_id: layerId, lines }], start_cap: cap, end_cap: cap, cap_distance: CAP_DISTANCE };
}

// ---- the scheduler of the preview

export interface PreviewEvents<T> {
  /** the answer to the latest request */
  result(value: T): void;
  /** the latest request failed (an aborted one is not reported) */
  error(e: unknown): void;
  /** a request is waiting or in flight */
  busy(on: boolean): void;
}

export interface Timers {
  set(fn: () => void, ms: number): unknown;
  clear(handle: unknown): void;
}

const REAL_TIMERS: Timers = {
  set: (fn, ms) => setTimeout(fn, ms),
  clear: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
};

/** One preview at a time, a pause after the last change; stale answers are dropped. */
export class PreviewScheduler<T> {
  private seq = 0;
  private timer: unknown = null;
  private abort: AbortController | null = null;
  private last: AssemblyInputs | null = null;

  constructor(
    private readonly fetch: (body: PreviewBody, signal: AbortSignal) => Promise<T>,
    private readonly on: PreviewEvents<T>,
    private readonly delay = PREVIEW_DELAY_MS,
    private readonly timers: Timers = REAL_TIMERS,
  ) {}

  /** The inputs now; the same as the last — nothing; fewer than two contours — no preview (nothing is asked). */
  request(inputs: AssemblyInputs | null): void {
    const next = inputs && inputs.contours.length >= 2 ? inputs : null;
    if (sameInputs(next, this.last)) return;
    this.last = next;
    this.cancel();
    if (!next) {
      this.on.busy(false);
      return;
    }
    this.on.busy(true);
    const mine = this.seq;
    this.timer = this.timers.set(() => {
      this.timer = null;
      if (mine === this.seq) void this.send(next, mine);
    }, this.delay);
  }

  /** The inputs asked last (null — none). */
  get inputs(): AssemblyInputs | null {
    return this.last;
  }

  private async send(inputs: AssemblyInputs, mine: number): Promise<void> {
    const abort = new AbortController();
    this.abort = abort;
    let value: T;
    try {
      value = await this.fetch(previewBody(inputs), abort.signal);
    } catch (e) {
      if (mine !== this.seq || abort.signal.aborted) return;
      this.abort = null;
      this.on.busy(false);
      this.on.error(e);
      return;
    }
    if (mine !== this.seq) return; // overtaken by a newer request
    this.abort = null;
    this.on.busy(false);
    this.on.result(value);
  }

  /** Whatever waits or is in flight is not wanted any more. */
  private cancel(): void {
    this.seq++;
    if (this.timer !== null) this.timers.clear(this.timer);
    this.timer = null;
    this.abort?.abort();
    this.abort = null;
  }

  dispose(): void {
    this.cancel();
    this.last = null;
  }
}
