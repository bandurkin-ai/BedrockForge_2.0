/**
 * The own snap targets of the working copy (ADR 0015 §4) in pieces of about `CHUNK_VERTICES` vertices, one
 * source of the screen index each (`edit:<n>`): an edit rebuilds only the pieces whose lines changed, not the
 * whole layer (a line of a layer of 1 million vertices: ~0.1 ms instead of ~50 ms). The layer is immutable and
 * shares the lines an edit did not touch, so what changed is found by identity: the common head and tail of the
 * two line lists are skipped, the rest is compared by set. A replaced line stays in its piece (same id).
 */
import type { EditLayer, EditLine, VertexRef } from "./model";
import { layerSnapSource, type SnapSource } from "./snap";

/** Vertices a piece takes before a new one is started (a line is never split). */
export const CHUNK_VERTICES = 16_384;

interface Chunk {
  key: string;
  lines: Set<EditLine>;
  vertices: number;
  dirty: boolean;
}

/** What the screen index must change: sources to set (new or replaced) and keys to remove. */
export interface OwnChanges {
  set: SnapSource[];
  removed: string[];
}

export class OwnSnapSource {
  private readonly chunks = new Set<Chunk>();
  private readonly chunkOf = new Map<EditLine, Chunk>();
  private lines: readonly EditLine[] = [];
  private layer: EditLayer | null = null;
  /** the piece new lines go to */
  private open: Chunk | null = null;
  private serial = 0;
  /** the piece built without a dragged vertex, and that vertex */
  private excluded: { chunk: Chunk; ref: VertexRef } | null = null;

  constructor(private readonly prefix = "edit") {}

  /** The sources for `layer`; `exclude` leaves out a vertex being dragged with its two segments. */
  update(layer: EditLayer, exclude?: VertexRef): OwnChanges {
    if (layer === this.layer && !exclude && !this.excluded) return { set: [], removed: [] };
    this.diff(layer.lines);
    this.layer = layer;
    if (this.excluded) {
      this.excluded.chunk.dirty = true;
      this.excluded = null;
    }
    if (exclude) {
      const line = layer.lines.find((l) => l.id === exclude.line);
      const chunk = line && this.chunkOf.get(line);
      if (chunk) {
        chunk.dirty = true;
        this.excluded = { chunk, ref: exclude };
      }
    }
    const out: OwnChanges = { set: [], removed: [] };
    for (const c of [...this.chunks]) {
      if (!c.dirty) continue;
      c.dirty = false;
      if (!c.lines.size) {
        this.chunks.delete(c);
        if (this.open === c) this.open = null;
        out.removed.push(c.key);
        continue;
      }
      const ex = this.excluded?.chunk === c ? this.excluded.ref : undefined;
      out.set.push(layerSnapSource({ lines: [...c.lines], nextId: 0 }, c.key, ex));
    }
    return out;
  }

  /** Keys of all the pieces (the session removes them when it closes). */
  keys(): string[] {
    return [...this.chunks].map((c) => c.key);
  }

  private diff(next: readonly EditLine[]): void {
    const prev = this.lines;
    this.lines = next;
    let i = 0;
    const n = Math.min(prev.length, next.length);
    while (i < n && prev[i] === next[i]) i++;
    let ep = prev.length;
    let en = next.length;
    while (ep > i && en > i && prev[ep - 1] === next[en - 1]) {
      ep--;
      en--;
    }
    if (i === ep && i === en) return;
    const kept = new Set<EditLine>();
    for (let k = i; k < en; k++) kept.add(next[k]);
    // a line replaced by another version of itself keeps its piece
    const home = new Map<number, Chunk>();
    for (let k = i; k < ep; k++) {
      const line = prev[k];
      if (kept.has(line)) continue;
      const c = this.chunkOf.get(line);
      if (!c) continue;
      c.lines.delete(line);
      c.vertices -= line.points.length;
      c.dirty = true;
      this.chunkOf.delete(line);
      home.set(line.id, c);
    }
    for (let k = i; k < en; k++) {
      const line = next[k];
      if (!this.chunkOf.has(line)) this.add(line, home.get(line.id));
    }
  }

  private add(line: EditLine, home?: Chunk): void {
    let c = home && this.chunks.has(home) ? home : this.open;
    if (!c || (c !== home && c.vertices > 0 && c.vertices + line.points.length > CHUNK_VERTICES)) {
      c = { key: `${this.prefix}:${this.serial++}`, lines: new Set(), vertices: 0, dirty: true };
      this.chunks.add(c);
      this.open = c;
    }
    c.lines.add(line);
    c.vertices += line.points.length;
    c.dirty = true;
    this.chunkOf.set(line, c);
  }
}
