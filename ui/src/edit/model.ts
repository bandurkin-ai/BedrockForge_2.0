/**
 * The working copy of a line layer while it is being edited (ADR 0015 §3; ported from BF `edit/model.ts`).
 * Everything here is plain data in float64 project coordinates: nothing is rounded or narrowed, a vertex
 * without a height (NaN Z) keeps it. No three.js and no React in `ui/src/edit` (test `edit/purity`).
 */
import type { Vec3 } from "../viz/core/vec3";

export type { Vec3 };
export type AttrValue = string | number | null;

/**
 * `id` is stable inside an editing session; `src` is the stable number (`lid`) of the stored line the line
 * comes from, −1 for a new one. A closed line does NOT repeat its first point.
 */
export interface EditLine {
  id: number;
  points: Vec3[];
  closed: boolean;
  attrs: Record<string, AttrValue>;
  src: number;
}

/** Immutable: every operation returns a new layer and shares the lines it did not touch. */
export interface EditLayer {
  lines: EditLine[];
  nextId: number;
}

/** `line` is EditLine.id. */
export interface VertexRef {
  line: number;
  index: number;
}

export interface Selection {
  lines: number[];
  vertices: VertexRef[];
}

export const EMPTY_SELECTION: Selection = { lines: [], vertices: [] };

/** A new line has no stored source. */
export const NEW_LINE = -1;

export function emptyLayer(): EditLayer {
  return { lines: [], nextId: 1 };
}

function attrValue(v: unknown): AttrValue | undefined {
  if (v === null) return null;
  if (typeof v === "string") return v;
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (v === undefined) return undefined;
  if (typeof v === "boolean" || typeof v === "bigint") return String(v);
  try {
    return JSON.stringify(v) ?? null;
  } catch {
    return String(v);
  }
}

/** Attribute values as the working copy keeps them (text, finite number or null). */
export function cleanAttrs(src: Record<string, unknown> | undefined): Record<string, AttrValue> {
  const attrs: Record<string, AttrValue> = {};
  if (src) {
    for (const key of Object.keys(src)) {
      const v = attrValue(src[key]);
      if (v !== undefined) attrs[key] = v;
    }
  }
  return attrs;
}

/**
 * Builds a layer from the arrays. `offsets` holds the first vertex of every line plus the end (lines + 1
 * values); `closed` one flag per line; `lid` the stable numbers of the lines (default: the position, as a
 * layer that was never edited has them). Coordinates are copied as they are.
 */
export function fromArrays(
  vertices: Float64Array,
  offsets: ArrayLike<number>,
  closed: ArrayLike<number>,
  attributes: Record<string, unknown>[],
  lid?: ArrayLike<number>,
): EditLayer {
  const count = Math.max(0, offsets.length - 1);
  const lines: EditLine[] = new Array(count);
  for (let i = 0; i < count; i++) {
    const from = offsets[i];
    const to = offsets[i + 1];
    const points: Vec3[] = new Array(Math.max(0, to - from));
    for (let k = from; k < to; k++) points[k - from] = [vertices[3 * k], vertices[3 * k + 1], vertices[3 * k + 2]];
    lines[i] = { id: i + 1, points, closed: !!closed[i], attrs: cleanAttrs(attributes[i]), src: lid ? lid[i] : i };
  }
  return { lines, nextId: count + 1 };
}

/** The exact inverse of `fromArrays`: every float64 bit survives the round trip. */
export function toArrays(layer: EditLayer): {
  vertices: Float64Array;
  offsets: Uint32Array;
  closed: Uint8Array;
  attributes: Record<string, AttrValue>[];
  src: Int32Array;
} {
  let total = 0;
  for (const line of layer.lines) total += line.points.length;
  const vertices = new Float64Array(total * 3);
  const offsets = new Uint32Array(layer.lines.length + 1);
  const closed = new Uint8Array(layer.lines.length);
  const src = new Int32Array(layer.lines.length);
  const attributes: Record<string, AttrValue>[] = new Array(layer.lines.length);
  let k = 0;
  layer.lines.forEach((line, i) => {
    offsets[i] = k;
    for (const p of line.points) {
      vertices[3 * k] = p[0];
      vertices[3 * k + 1] = p[1];
      vertices[3 * k + 2] = p[2];
      k++;
    }
    closed[i] = line.closed ? 1 : 0;
    src[i] = line.src;
    attributes[i] = { ...line.attrs };
  });
  offsets[layer.lines.length] = k;
  return { vertices, offsets, closed, attributes, src };
}

export function lineById(layer: EditLayer, id: number): EditLine | undefined {
  const lines = layer.lines;
  for (let i = 0; i < lines.length; i++) if (lines[i].id === id) return lines[i];
  return undefined;
}

export function vertexAt(layer: EditLayer, ref: VertexRef): Vec3 | undefined {
  return lineById(layer, ref.line)?.points[ref.index];
}

export function vertexCount(layer: EditLayer): number {
  let n = 0;
  for (const line of layer.lines) n += line.points.length;
  return n;
}

/** The edit limit of ADR 0015 §3 (decision 4): a larger layer opens read-only. */
export const MAX_EDIT_VERTICES = 1_000_000;

/** Rough memory of one vertex of the working copy (a 3-array of doubles and its slot). */
const VERTEX_BYTES = 64;
const LINE_BYTES = 96;

/**
 * Bytes the history keeps for a step from `prev` to `next`: the lines of `prev` that `next` does not share
 * (by identity). An unchanged layer costs nothing.
 */
export function layerStepBytes(prev: EditLayer, next: EditLayer): number {
  if (prev === next) return 0;
  const kept = new Set<EditLine>(next.lines);
  let bytes = 0;
  for (const line of prev.lines) if (!kept.has(line)) bytes += LINE_BYTES + line.points.length * VERTEX_BYTES;
  return bytes;
}

interface Step<T> {
  /** the state BEFORE the step */
  state: T;
  label: string;
  bytes: number;
}

export interface HistoryLimits<T> {
  /** steps kept (default 200) */
  steps?: number;
  /** bytes of replaced data kept over all steps (default 256 MB) */
  bytes?: number;
  /** bytes a step from `prev` to `next` keeps alive */
  cost?: (prev: T, next: T) => number;
}

export const HISTORY_STEPS = 200;
export const HISTORY_BYTES = 256 * 1024 * 1024;

/**
 * Undo / redo inside an editing session. States are compared by identity (===): the layer is immutable,
 * so an operation that changed nothing returns the same object and makes no step.
 *
 * A live drag calls `replace` on every mouse move: the present changes, the committed state underneath
 * stays, and the single step appears on `push` / `commit` (or the drag is dropped by `cancel`).
 * The oldest steps are dropped past `steps` or past `bytes` of replaced data (the newest step always stays).
 */
export class History<T> {
  private past: Step<T>[] = [];
  private future: Step<T>[] = [];
  private base: T;
  private live: T | undefined;
  private hasLive = false;
  private saved: T;
  private readonly limit: number;
  private readonly maxBytes: number;
  private readonly cost: ((prev: T, next: T) => number) | undefined;
  private pastBytes = 0;

  constructor(initial: T, limits: number | HistoryLimits<T> = {}) {
    const l = typeof limits === "number" ? { steps: limits } : limits;
    this.base = initial;
    this.saved = initial;
    this.limit = Math.max(1, l.steps ?? HISTORY_STEPS);
    this.maxBytes = l.bytes ?? HISTORY_BYTES;
    this.cost = l.cost;
  }

  get present(): T {
    return this.hasLive ? (this.live as T) : this.base;
  }

  /** The last committed state (the present without a live `replace`). */
  get committed(): T {
    return this.base;
  }

  /** True between `replace` and the following push / commit / cancel. */
  get pending(): boolean {
    return this.hasLive;
  }

  /** Steps that can be undone. */
  get depth(): number {
    return this.past.length;
  }

  /** Bytes of replaced data the undo steps keep (by `cost`). */
  get bytes(): number {
    return this.pastBytes;
  }

  /** A new step; drops the redo tail. A state identical (===) to the committed one is ignored. */
  push(next: T, label: string): void {
    this.dropLive();
    if (next === this.base) return;
    const bytes = this.cost ? this.cost(this.base, next) : 0;
    this.past.push({ state: this.base, label, bytes });
    this.pastBytes += bytes;
    while (this.past.length > 1 && (this.past.length > this.limit || this.pastBytes > this.maxBytes)) {
      this.pastBytes -= (this.past.shift() as Step<T>).bytes;
    }
    this.base = next;
    this.future = [];
  }

  /** Replaces the present without a new step (live drag). */
  replace(next: T): void {
    this.live = next;
    this.hasLive = true;
  }

  /** Turns the state set by `replace` into a step. */
  commit(label: string): void {
    if (this.hasLive) this.push(this.live as T, label);
  }

  /**
   * Puts `state` in place of the committed one without a step and marks it saved (after a save the working
   * copy takes the stable numbers the core gave its lines; the steps before stay undoable).
   */
  rebase(state: T): void {
    this.dropLive();
    this.base = state;
    this.saved = state;
  }

  /** Drops the state set by `replace`: the present is the committed state again. */
  cancel(): void {
    this.dropLive();
  }

  /** Returns the label of the step undone, or null. An uncommitted `replace` is dropped first. */
  undo(): string | null {
    this.dropLive();
    const step = this.past.pop();
    if (!step) return null;
    this.pastBytes -= step.bytes;
    this.future.push({ state: this.base, label: step.label, bytes: step.bytes });
    this.base = step.state;
    return step.label;
  }

  redo(): string | null {
    this.dropLive();
    const step = this.future.pop();
    if (!step) return null;
    this.past.push({ state: this.base, label: step.label, bytes: step.bytes });
    this.pastBytes += step.bytes;
    this.base = step.state;
    return step.label;
  }

  get canUndo(): boolean {
    return this.past.length > 0;
  }

  get canRedo(): boolean {
    return this.future.length > 0;
  }

  /** The present differs from the state at the last markSaved() (or the initial one). */
  get dirty(): boolean {
    return this.present !== this.saved;
  }

  markSaved(): void {
    this.saved = this.present;
  }

  get undoLabel(): string | null {
    return this.past.length ? this.past[this.past.length - 1].label : null;
  }

  get redoLabel(): string | null {
    return this.future.length ? this.future[this.future.length - 1].label : null;
  }

  private dropLive(): void {
    this.live = undefined;
    this.hasLive = false;
  }
}

/** The history of an editing session: ≤ 200 steps and ≤ 256 MB of replaced lines (ADR 0015 §3). */
export function layerHistory(initial: EditLayer, limits: Omit<HistoryLimits<EditLayer>, "cost"> = {}): History<EditLayer> {
  return new History(initial, { ...limits, cost: layerStepBytes });
}
