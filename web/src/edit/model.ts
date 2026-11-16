/**
 * The working copy of a line layer while it is being edited.
 * Everything here is plain data in float64 project coordinates: nothing is rounded or narrowed.
 */

export type Vec3 = [number, number, number];
export type AttrValue = string | number | null;

/** `id` is stable inside an editing session; a closed line does NOT repeat its first point. */
export interface EditLine {
  id: number;
  points: Vec3[];
  closed: boolean;
  attrs: Record<string, AttrValue>;
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

/**
 * Builds a layer from the stored arrays. `offsets` holds the first vertex of every line plus the end
 * (lines + 1 values); `closed` one flag per line. Coordinates are copied as they are.
 */
export function fromArrays(
  vertices: Float64Array,
  offsets: ArrayLike<number>,
  closed: ArrayLike<number>,
  attributes: Record<string, unknown>[],
): EditLayer {
  const count = Math.max(0, offsets.length - 1);
  const lines: EditLine[] = new Array(count);
  for (let i = 0; i < count; i++) {
    const from = offsets[i];
    const to = offsets[i + 1];
    const points: Vec3[] = new Array(Math.max(0, to - from));
    for (let k = from; k < to; k++) points[k - from] = [vertices[3 * k], vertices[3 * k + 1], vertices[3 * k + 2]];
    const attrs: Record<string, AttrValue> = {};
    const src = attributes[i];
    if (src) {
      for (const key of Object.keys(src)) {
        const v = attrValue(src[key]);
        if (v !== undefined) attrs[key] = v;
      }
    }
    lines[i] = { id: i + 1, points, closed: !!closed[i], attrs };
  }
  return { lines, nextId: count + 1 };
}

/** The exact inverse of `fromArrays`: every float64 bit survives the round trip. */
export function toArrays(layer: EditLayer): {
  vertices: Float64Array;
  offsets: Uint32Array;
  closed: Uint8Array;
  attributes: Record<string, AttrValue>[];
} {
  let total = 0;
  for (const line of layer.lines) total += line.points.length;
  const vertices = new Float64Array(total * 3);
  const offsets = new Uint32Array(layer.lines.length + 1);
  const closed = new Uint8Array(layer.lines.length);
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
    attributes[i] = { ...line.attrs };
  });
  offsets[layer.lines.length] = k;
  return { vertices, offsets, closed, attributes };
}

export function lineById(layer: EditLayer, id: number): EditLine | undefined {
  const lines = layer.lines;
  for (let i = 0; i < lines.length; i++) if (lines[i].id === id) return lines[i];
  return undefined;
}

export function vertexAt(layer: EditLayer, ref: VertexRef): Vec3 | undefined {
  return lineById(layer, ref.line)?.points[ref.index];
}

interface Step<T> {
  /** the state BEFORE the step */
  state: T;
  label: string;
}

/**
 * Undo / redo inside an editing session. States are compared by identity (===): the layer is immutable,
 * so an operation that changed nothing returns the same object and makes no step.
 *
 * A live drag calls `replace` on every mouse move: the present changes, the committed state underneath
 * stays, and the single step appears on `push` / `commit` (or the drag is dropped by `cancel`).
 */
export class History<T> {
  private past: Step<T>[] = [];
  private future: Step<T>[] = [];
  private base: T;
  private live: T | undefined;
  private hasLive = false;
  private saved: T;
  private readonly limit: number;

  constructor(initial: T, limit = 500) {
    this.base = initial;
    this.saved = initial;
    this.limit = Math.max(1, limit);
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

  /** A new step; drops the redo tail. A state identical (===) to the committed one is ignored. */
  push(next: T, label: string): void {
    this.dropLive();
    if (next === this.base) return;
    this.past.push({ state: this.base, label });
    if (this.past.length > this.limit) this.past.shift();
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

  /** Drops the state set by `replace`: the present is the committed state again. */
  cancel(): void {
    this.dropLive();
  }

  /** Returns the label of the step undone, or null. An uncommitted `replace` is dropped first. */
  undo(): string | null {
    this.dropLive();
    const step = this.past.pop();
    if (!step) return null;
    this.future.push({ state: this.base, label: step.label });
    this.base = step.state;
    return step.label;
  }

  redo(): string | null {
    this.dropLive();
    const step = this.future.pop();
    if (!step) return null;
    this.past.push({ state: this.base, label: step.label });
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
