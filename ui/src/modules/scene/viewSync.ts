/**
 * The project view settings (ТЗ 7.0.10, ADR 0007 §2): the project is the source, this is a cache.
 *
 * A change applies at once (the interface answers immediately), then each key is written 400 ms after its
 * last change with the version it was read at. On 409 the server value is taken and the pending changes are
 * applied to it again (an operation, not a copy: «add a hidden key» survives a change made elsewhere), one
 * retry; a second 409 keeps the server value and says so.
 */

export type ViewOp = (value: unknown) => unknown;

export interface ViewIO {
  get(): Promise<{ values: Record<string, { value: unknown; version: number }> }>;
  put(key: string, value: unknown, expectedVersion: number): Promise<{ value: unknown; version: number }>;
}

/** A 409 of the core carries the current value (ApiError.extra). */
export interface ConflictError {
  status: 409;
  body?: { value?: unknown; version?: number };
}

const isConflict = (e: unknown): e is ConflictError =>
  typeof e === "object" && e !== null && (e as { status?: unknown }).status === 409;
/** 410: the value belongs to a project that is no longer open — nothing to write, nothing to tell. */
const isGone = (e: unknown) => typeof e === "object" && e !== null && (e as { status?: unknown }).status === 410;

interface Entry {
  value: unknown;
  version: number;
  pending: ViewOp[];
  timer: ReturnType<typeof setTimeout> | null;
  chain: Promise<void>;
  /** bumped by `adopt`: an answer to a write sent before it is stale */
  epoch?: number;
}

export class ViewSync {
  private entries = new Map<string, Entry>();
  private generation = 0;

  constructor(
    private io: ViewIO,
    private onChange: (key: string, value: unknown) => void,
    private onNote: (text: string) => void = () => {},
    private delay = 400,
  ) {}

  /** Every key of the open project (a new project: the old pending writes are dropped). */
  async load(): Promise<Record<string, unknown>> {
    this.reset();
    const gen = this.generation;
    const r = await this.io.get();
    if (gen !== this.generation) return {};
    const out: Record<string, unknown> = {};
    for (const [key, v] of Object.entries(r.values)) {
      this.entries.set(key, { value: v.value, version: v.version, pending: [], timer: null, chain: Promise.resolve() });
      out[key] = v.value;
    }
    return out;
  }

  reset(): void {
    this.generation++;
    for (const e of this.entries.values()) if (e.timer) clearTimeout(e.timer);
    this.entries.clear();
  }

  value(key: string): unknown {
    return this.entries.get(key)?.value;
  }

  /** Apply a change now; it is written to the project shortly. */
  update(key: string, op: ViewOp): unknown {
    let e = this.entries.get(key);
    if (!e) {
      e = { value: undefined, version: 0, pending: [], timer: null, chain: Promise.resolve() };
      this.entries.set(key, e);
    }
    e.value = op(e.value);
    e.pending.push(op);
    this.onChange(key, e.value);
    if (e.timer) clearTimeout(e.timer);
    e.timer = setTimeout(() => void this.flush(key), this.delay);
    return e.value;
  }

  /** Values the core has just written itself (a saved view applied): taken as they are, the pending changes
   * of those keys are dropped — the view replaces them. */
  adopt(values: Record<string, { value: unknown; version: number }>): void {
    for (const [key, v] of Object.entries(values)) {
      let e = this.entries.get(key);
      if (!e) {
        e = { value: undefined, version: 0, pending: [], timer: null, chain: Promise.resolve() };
        this.entries.set(key, e);
      }
      if (e.timer) clearTimeout(e.timer);
      e.timer = null;
      e.pending = [];
      e.epoch = (e.epoch ?? 0) + 1;
      e.value = v.value;
      e.version = v.version;
      this.onChange(key, v.value);
    }
  }

  /** Write the pending changes of a key now (tests, closing the project). */
  flush(key: string): Promise<void> {
    const e = this.entries.get(key);
    if (!e) return Promise.resolve();
    if (e.timer) {
      clearTimeout(e.timer);
      e.timer = null;
    }
    const gen = this.generation;
    e.chain = e.chain.then(() => this.write(key, e, gen));
    return e.chain;
  }

  async flushAll(): Promise<void> {
    await Promise.all([...this.entries.keys()].map((k) => this.flush(k)));
  }

  private async write(key: string, e: Entry, gen: number): Promise<void> {
    const ops = e.pending;
    if (!ops.length || gen !== this.generation) return;
    e.pending = [];
    const epoch = e.epoch ?? 0;
    const stale = () => gen !== this.generation || (e.epoch ?? 0) !== epoch;
    try {
      const r = await this.io.put(key, e.value, e.version);
      if (stale()) return;
      e.version = r.version;
      this.settle(key, e, r.value);
    } catch (err) {
      if (stale() || isGone(err)) return;
      if (!isConflict(err)) {
        e.pending = [...ops, ...e.pending]; // the next change writes them too
        this.onNote("Настройки вида не сохранены в проекте: ядро недоступно.");
        return;
      }
      const server = err.body ?? {};
      const base = server.value;
      e.version = server.version ?? e.version;
      const merged = [...ops, ...e.pending].reduce((v, op) => op(v), base);
      try {
        const r = await this.io.put(key, merged, e.version);
        if (stale()) return;
        e.version = r.version;
        this.settle(key, e, r.value);
      } catch (again) {
        if (stale()) return;
        if (isConflict(again)) {
          e.version = again.body?.version ?? e.version;
          e.pending = [];
          this.settle(key, e, again.body?.value);
          this.onNote("Настройки вида изменены в другом окне: показаны сохранённые в проекте.");
        } else {
          e.pending = [...ops, ...e.pending];
          this.onNote("Настройки вида не сохранены в проекте: ядро недоступно.");
        }
      }
    }
  }

  /** The value the core keeps (normalized there), with the changes made meanwhile on top. */
  private settle(key: string, e: Entry, stored: unknown): void {
    e.value = e.pending.reduce((v, op) => op(v), stored);
    this.onChange(key, e.value);
  }
}

// ---- keys of ТЗ 7.0.10 ----------------------------------------------------------------------------

/** Kinds of `layer.show` → keys of hidden layers (`set:3`, `set:3:collars`, `wf:12`…). */
const LAYER_KEYS: Record<string, (id: number) => string> = {
  set: (id) => `set:${id}`,
  collars: (id) => `set:${id}:collars`,
  paths: (id) => `set:${id}:paths`,
  intervals: (id) => `set:${id}:intervals`,
  wireframe: (id) => `wf:${id}`,
  lines: (id) => `ln:${id}`,
  raster: (id) => `rs:${id}`,
  blocks: (id) => `bm:${id}`,
};

export function hiddenKey(layer: string, id: number): string {
  const fn = LAYER_KEYS[layer];
  if (!fn) throw new Error(`Неизвестный слой «${layer}»`);
  if (!Number.isInteger(id) || id <= 0) throw new Error("Нужен номер объекта слоя");
  return fn(id);
}

/** «Show / hide»: an operation on the list, so it merges with changes made elsewhere. */
export function toggleHidden(key: string, shown: boolean): ViewOp {
  return (v) => {
    const list = Array.isArray(v) ? v.filter((k): k is string => typeof k === "string" && k !== key) : [];
    if (!shown) list.push(key);
    return list.slice(-2000);
  };
}

/** A change of some fields of an object key (`scene`, `section`, `active`). */
export function patchFields(patch: Record<string, unknown>): ViewOp {
  return (v) => ({ ...(v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {}), ...patch });
}

/** A change of one entry of a map key (`collar_styles`, `trace_styles`, `columns`); undefined removes it. */
export function patchEntry(entry: string, value: unknown): ViewOp {
  return (v) => {
    const map = { ...(v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {}) };
    delete map[entry]; // re-inserted last: the oldest go first at the limit
    if (value !== undefined) map[entry] = value;
    return map;
  };
}

// ---- the key `swaths` (ADR 0019 §2): swath plots shown in 3D ------------------------------------------

export const SWATHS_KEY = "swaths";
export const MAX_SWATHS_SHOWN = 50;

export interface SwathEntry {
  swath: number;
  visible: boolean;
}

/** `[{swath, visible}]` as the core reads it (`viewstate/keys.py`): junk dropped, `visible` true unless false,
 * a repeated id kept once (the first), the newest 50 kept. */
export function swathEntries(value: unknown): SwathEntry[] {
  if (!Array.isArray(value)) return [];
  const out: SwathEntry[] = [];
  const seen = new Set<number>();
  for (const v of value) {
    const d = v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
    const id = d?.swath;
    if (typeof id !== "number" || !Number.isInteger(id) || id <= 0 || seen.has(id)) continue;
    seen.add(id);
    out.push({ swath: id, visible: typeof d?.visible === "boolean" ? d.visible : true });
  }
  return out.slice(-MAX_SWATHS_SHOWN);
}

/** The swath plots shown in 3D now. */
export const shownSwaths = (value: unknown): number[] => swathEntries(value).flatMap((e) => (e.visible ? [e.swath] : []));

/** Shows or hides a swath plot in 3D; a new one goes last, past the limit the oldest hidden ones go first. */
export function showSwath(swath: number, visible: boolean): ViewOp {
  return (value) => {
    const list = swathEntries(value);
    const i = list.findIndex((e) => e.swath === swath);
    if (i >= 0) list[i] = { swath, visible };
    else list.push({ swath, visible });
    while (list.length > MAX_SWATHS_SHOWN) {
      const hidden = list.findIndex((e) => !e.visible && e.swath !== swath);
      list.splice(hidden >= 0 ? hidden : 0, 1);
    }
    return list;
  };
}

/** A deleted swath plot leaves the key. */
export function dropSwathEntry(swath: number): ViewOp {
  return (value) => swathEntries(value).filter((e) => e.swath !== swath);
}
