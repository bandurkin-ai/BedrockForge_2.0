/**
 * The sections of the wireframes on the current section of the active series (ADR 0016 §6; ТЗ 8.1.5): on a new
 * k — the packet of section k (`GET /section-series/{id}/sections/{k}/wireframes?ids=`, ≤ 200 wireframes a
 * request); the request of the old section is cancelled and its lines leave at once; an answer that came for a
 * section no longer current is not shown; a failed one is said (`onError`: {message, hint} of the core) and asked
 * again on the next `set`. Once the packet is shown and nothing happens for `idleMs` — the neighbours k ± 1, then
 * k ± 2, one at a time; up to 5 sections are kept: a prepared section is shown without a request. On activating
 * the series and on a change of its geometry or wireframes (their versions; not of its name, shadow or layer of
 * contours) — `POST …/cache` (`sections.cache`) around k, again when k has gone farther than `CACHE_REACH` from it
 * in a long series; its job done reads the neighbours anew. `mark` — the readiness of the data of the section
 * (`Scene.debug().sectionData`, the E2E of 10.26 and the benchmark `series_step`).
 *
 * No three.js and no DOM here: the loads, the timers and the clock are given (`SeriesSectionsDeps`).
 */
import { readSeriesSection, type SeriesSectionPacket } from "../../viz/mesh/section";
import type { SectionDataMark } from "../../viz/sceneTypes";

export type { SectionDataMark } from "../../viz/sceneTypes";

export const MAX_IDS = 200;
export const KEEP_SECTIONS = 5;
export const IDLE_MS = 150;
/** In a series of more than `CACHE_LONG` sections the cache of the core is asked again around k once k has gone
 * farther than `CACHE_REACH` from the k it was asked around (the job prepares a window of sections, not all). */
export const CACHE_LONG = 500;
export const CACHE_REACH = 40;

export interface SeriesTarget {
  seriesId: number;
  /** what the planes of its sections are made of (`geometryKey`): not its version — a new name or shadow keeps them */
  geometry: string;
  /** a polyline: `index` is the segment (k = 0, seg = index) */
  polyline: boolean;
  index: number;
  steps: number;
  /** the wireframes shown with triangles and their versions: `[id, version]` sorted by id */
  wireframes: readonly (readonly [number, number])[];
}

export interface SeriesSectionsDeps {
  /** GET of a path of the core: the bytes (null — nothing new: keep what is there) */
  load(path: string): Promise<ArrayBuffer | null>;
  cancel(path: string): void;
  post(path: string, body: unknown): Promise<unknown>;
  /** the lines of the current section (null — none: the old ones leave) */
  show(packet: SeriesSectionPacket | null): void;
  onMark?(mark: SectionDataMark): void;
  /** the packet of the current section failed (not a cancel): the message of the core for the window */
  onError?(error: unknown): void;
  now?(): number;
  setTimer?(fn: () => void, ms: number): unknown;
  clearTimer?(handle: unknown): void;
  idleMs?: number;
}

const NO_MARK: SectionDataMark = { series: null, k: null, seg: null, ready: false, ms: null, prepared: false, kept: 0 };

/** Paths of the packet of a section: the wireframes in groups of ≤ 200. */
export function packetPaths(t: Pick<SeriesTarget, "seriesId" | "polyline">, index: number, ids: readonly number[]): string[] {
  const out: string[] = [];
  const at = t.polyline ? `sections/0/wireframes?seg=${index}&ids=` : `sections/${index}/wireframes?ids=`;
  for (let i = 0; i < ids.length; i += MAX_IDS) out.push(`/section-series/${t.seriesId}/${at}${ids.slice(i, i + MAX_IDS).join(",")}`);
  return out;
}

/** Packets of several requests as one. */
function merge(parts: SeriesSectionPacket[]): SeriesSectionPacket {
  const out = { ...parts[0], sections: new Map(parts[0].sections), missing: [...parts[0].missing] };
  for (const p of parts.slice(1)) {
    for (const [id, s] of p.sections) out.sections.set(id, s);
    out.missing.push(...p.missing);
  }
  return out;
}

export class SeriesSections {
  private target: SeriesTarget | null = null;
  /** `${series}:${geometry}:${wireframes}` — the sections kept belong to it */
  private sig = "";
  private cached = "";
  /** the k the cache of the core was asked around */
  private cachedAround = 0;
  /** the current section whose packet failed: the next `set` asks it again */
  private failed: number | null = null;
  private readonly kept = new Map<number, SeriesSectionPacket>();
  private readonly inflight = new Map<number, { paths: string[]; promise: Promise<SeriesSectionPacket | null> }>();
  private timer: unknown = null;
  private generation = 0;
  private since = 0;
  private current: SectionDataMark = NO_MARK;
  private disposed = false;

  constructor(private readonly deps: SeriesSectionsDeps) {}

  get mark(): SectionDataMark {
    return this.current;
  }

  /** Requests in flight (tests, `idle` of the scene). */
  get loading(): number {
    return this.inflight.size;
  }

  private now(): number {
    return this.deps.now?.() ?? (typeof performance !== "undefined" ? performance.now() : Date.now());
  }

  /** The section to show: a series and its k (a polyline — its segment), or null — none (the lines leave). */
  set(target: SeriesTarget | null): void {
    if (this.disposed) return;
    const ids = target?.wireframes.map(([id]) => id) ?? [];
    const sig = target && ids.length ? `${target.seriesId}:${target.geometry}:${target.wireframes.map(([i, v]) => `${i}.${v}`).join(",")}` : "";
    const same = sig === this.sig && target?.index === this.target?.index;
    if (same && (this.target || !sig) && this.failed === null) return; // the same section, or still none
    this.failed = null;
    this.stopIdle();
    if (sig !== this.sig) {
      this.cancelAll();
      this.kept.clear();
      this.sig = sig;
    }
    this.target = sig ? target : null;
    this.generation++;
    if (!this.target) {
      this.deps.show(null);
      this.setMark(NO_MARK);
      return;
    }
    const t = this.target;
    // the series became active, its geometry or wireframes changed, or k went far in a long series: the cache
    const far = t.steps > CACHE_LONG && Math.abs(t.index - this.cachedAround) > CACHE_REACH;
    if (sig !== this.cached || far) {
      this.cached = sig;
      this.cachedAround = t.index;
      void this.deps.post(`/section-series/${t.seriesId}/cache`, { wireframe_ids: ids.slice(0, MAX_IDS), around: t.index }).catch(() => undefined);
    }
    // requests of sections that are not the one shown now nor its neighbours go
    for (const index of [...this.inflight.keys()]) if (index !== t.index) this.drop(index);
    this.since = this.now();
    const ready = this.kept.get(t.index);
    if (ready) return this.shown(t, ready, true);
    this.deps.show(null); // the lines of the old section would stand in the wrong place
    this.setMark({ ...this.base(t), ready: false, ms: null, prepared: false });
    const gen = this.generation;
    void this.fetch(t, t.index).then((p) => {
      if (gen !== this.generation || this.disposed || !p) return; // a newer section: this answer is not shown
      this.shown(t, p, false);
    });
  }

  /** The job `sections.cache` of this series is done: the neighbours kept are read anew (the files are there). */
  cacheReady(seriesId: number): void {
    const t = this.target;
    if (!t || t.seriesId !== seriesId) return;
    for (const index of [...this.kept.keys()]) if (index !== t.index) this.kept.delete(index);
    this.scheduleIdle();
  }

  /** The wireframes may have changed outside the versions (a lost context, a reconnect): everything again. */
  reset(): void {
    const t = this.target;
    this.cancelAll();
    this.kept.clear();
    this.sig = "";
    this.cached = "";
    this.target = null;
    if (t) this.set(t);
  }

  dispose(): void {
    this.disposed = true;
    this.stopIdle();
    this.cancelAll();
    this.kept.clear();
  }

  // ---------------------------------------------------------------- internals

  private base(t: SeriesTarget): Pick<SectionDataMark, "series" | "k" | "seg" | "kept"> {
    return { series: t.seriesId, k: t.polyline ? 0 : t.index, seg: t.polyline ? t.index : null, kept: this.kept.size };
  }

  private setMark(m: SectionDataMark): void {
    this.current = m;
    this.deps.onMark?.(m);
  }

  private shown(t: SeriesTarget, p: SeriesSectionPacket, prepared: boolean): void {
    this.keep(t.index, p);
    this.deps.show(p);
    this.setMark({ ...this.base(t), ready: true, ms: Math.max(0, this.now() - this.since), prepared });
    this.scheduleIdle();
  }

  private keep(index: number, p: SeriesSectionPacket): void {
    this.kept.delete(index);
    this.kept.set(index, p);
    const t = this.target;
    while (this.kept.size > KEEP_SECTIONS) {
      // the farthest from the current section goes first
      let worst = -1;
      let far = -1;
      for (const i of this.kept.keys()) {
        const d = t ? Math.abs(i - t.index) : 0;
        if (d > far) (far = d), (worst = i);
      }
      this.kept.delete(worst);
    }
  }

  private fetch(t: SeriesTarget, index: number): Promise<SeriesSectionPacket | null> {
    const known = this.inflight.get(index);
    if (known) return known.promise;
    const paths = packetPaths(t, index, t.wireframes.map(([id]) => id));
    const promise: Promise<SeriesSectionPacket | null> = Promise.all(paths.map((p) => this.deps.load(p)))
      .then((bufs) => {
        if (bufs.some((b) => b === null)) return this.kept.get(index) ?? null;
        return merge(bufs.map((b) => readSeriesSection(b!)));
      })
      .catch((e: unknown) => {
        // dropped (another section): not an error; a neighbour read ahead: asked again when it is current
        const live = this.inflight.get(index)?.promise === promise && !this.disposed;
        if (live && this.target && index === this.target.index) {
          this.failed = index;
          this.setMark({ ...this.base(this.target), ready: false, ms: null, prepared: false });
          this.deps.onError?.(e);
        }
        return null;
      })
      .finally(() => {
        if (this.inflight.get(index)?.promise === promise) this.inflight.delete(index);
      });
    this.inflight.set(index, { paths, promise });
    return promise;
  }

  private drop(index: number): void {
    const f = this.inflight.get(index);
    if (!f) return;
    this.inflight.delete(index);
    for (const p of f.paths) this.deps.cancel(p);
  }

  private cancelAll(): void {
    for (const index of [...this.inflight.keys()]) this.drop(index);
  }

  private stopIdle(): void {
    if (this.timer === null) return;
    if (this.deps.clearTimer) this.deps.clearTimer(this.timer);
    else clearTimeout(this.timer as ReturnType<typeof setTimeout>);
    this.timer = null;
  }

  /** After `idleMs` of quiet: the next neighbour not kept — k + 1, k − 1, k + 2, k − 2 — one at a time. */
  private scheduleIdle(): void {
    this.stopIdle();
    const t = this.target;
    if (!t || this.disposed) return;
    const next = [1, -1, 2, -2].map((d) => t.index + d).find((i) => i >= 0 && i < t.steps && !this.kept.has(i));
    if (next === undefined) return;
    const gen = this.generation;
    const run = () => {
      this.timer = null;
      if (gen !== this.generation || this.disposed) return;
      void this.fetch(t, next).then((p) => {
        if (gen !== this.generation || this.disposed || !p) return;
        this.keep(next, p);
        this.setMark({ ...this.current, kept: this.kept.size });
        this.scheduleIdle();
      });
    };
    const ms = this.deps.idleMs ?? IDLE_MS;
    this.timer = this.deps.setTimer ? this.deps.setTimer(run, ms) : setTimeout(run, ms);
  }
}
