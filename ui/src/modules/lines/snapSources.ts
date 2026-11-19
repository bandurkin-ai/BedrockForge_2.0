/**
 * What the snap of the string editor catches outside the working copy (ADR 0015 §4): the other visible line
 * layers (exact geometry `GET /lines/layers/{id}/geometry`, kept by version; a layer over 500 000 vertices and
 * point clouds are left out — the snap panel says so), the collars, paths and interval bounds of the visible
 * sets (`GET /viz/sets/{id}/snap` in the window of the section or the extent of the view; over 5 000 holes —
 * collars only), the sections of the visible wireframes on the section plane, and the exact triangle under the
 * cursor (`POST /wireframes/pick`, the pause and the cancel of a stale request — the session). No React.
 */
import type { EditSession } from "../../edit/session";
import { linesSnapSource, MAX_SNAP_LAYER_VERTICES, readSetSnap, wireframeSectionSource, type SnapSource } from "../../edit/snap";
import type { WireframeProbe } from "../../edit/tools";
import { bfa1Array, decodeBfa1 } from "../../viz/format/bfa1";
import { windowPlanes } from "../../viz/mesh/exact";
import type { SectionLine } from "../../viz/section/math";
import type { LineLayerInfo, WireframeInfo } from "../scene/objects";
import { getGeometry, getSetSnap, getWireframeSection, pickTriangle } from "./editApi";
import { editStore } from "./editState";

interface Loaded {
  sources: SnapSource[];
  note?: string;
}

export interface SnapInput {
  /** visible line layers of the project (the edited one among them is skipped) */
  lines(): readonly LineLayerInfo[];
  /** visible drillhole sets */
  sets(): readonly number[];
  /** visible wireframes and DTMs */
  wireframes(): readonly WireframeInfo[];
  section(): { line: SectionLine | null; clip: boolean };
  /** [xmin, ymin, xmax, ymax] of the view in project coordinates, or null */
  viewBox(): number[] | null;
}

/** The query of the snap targets of a set: the section window when clipped, else the extent of the view. */
export function setSnapQuery(section: { line: SectionLine | null; clip: boolean }, box: number[] | null): string {
  const l = section.line;
  if (l && section.clip) return `section=${[l.x0, l.y0, l.az, l.towards, l.away, ...(l.length === null ? [] : [l.length])].join(",")}`;
  if (box) return `bbox=${box.map((v) => Math.round(v * 100) / 100).join(",")}`;
  return "";
}

/** The query of the section of a wireframe on a section line (the plane of `viz/wireframes/{id}/section`). */
export function sectionQuery(l: SectionLine): string {
  const q = `x0=${l.x0}&y0=${l.y0}&az=${l.az}`;
  return l.length === null ? q : `${q}&length=${l.length}`;
}

/** Why a layer takes no part in the snap, or null. */
export function lineSkip(l: LineLayerInfo): string | null {
  if (l.cloud) return `Облако «${l.name}» в привязке не участвует`;
  if (l.point_count > MAX_SNAP_LAYER_VERTICES) return `Слой «${l.name}» больше 500 тыс. вершин: в привязке не участвует`;
  return null;
}

/** The arrays of a layer of `linegeom` as a snap source (without making a working copy of it). */
export function geometrySource(key: string, buf: ArrayBuffer): SnapSource | null {
  const doc = decodeBfa1(buf);
  const closed = bfa1Array(doc, "closed", "<u1");
  return linesSnapSource(key, bfa1Array(doc, "xyz", "<f8"), bfa1Array(doc, "offsets", "<u4", closed.length + 1), closed);
}

export class SnapFeeder {
  /** sources set in the index, by key */
  private shown = new Set<string>();
  /** loaded answers by their key (a layer — by version, a set — by the query, a section — by the line) */
  private cache = new Map<string, Promise<Loaded>>();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private abort = new AbortController();
  private disposed = false;
  /** the run whose answers may still go into the index: an older run answered late is dropped */
  private generation = 0;

  constructor(
    private readonly session: EditSession,
    private readonly input: SnapInput,
  ) {}

  /** The wireframe probe of the session for the visible wireframes (none — no probe). */
  probe(): WireframeProbe | null {
    const ids = this.input.wireframes().map((w) => w.id);
    if (!ids.length) return null;
    return async (ray, signal) => {
      const s = this.input.section();
      return pickTriangle({ origin: ray.origin, direction: ray.dir, wireframe_ids: ids, clip: windowPlanes(s.line, s.clip) }, signal);
    };
  }

  /** Something visible, the section or the view changed: the sources follow after a short pause. */
  refresh(delay = 150): void {
    if (this.disposed) return;
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.run();
    }, delay);
  }

  dispose(): void {
    this.disposed = true;
    if (this.timer) clearTimeout(this.timer);
    this.abort.abort();
    for (const key of this.shown) this.session.snapIndex.removeSource(key);
    this.shown.clear();
  }

  private load(used: Set<string>, key: string, make: () => Promise<Loaded>): Promise<Loaded> {
    used.add(key);
    let p = this.cache.get(key);
    if (!p) {
      // a failed answer is asked again by the next refresh
      p = make().catch(() => {
        this.cache.delete(key);
        return { sources: [] };
      });
      this.cache.set(key, p);
    }
    return p;
  }

  async run(): Promise<void> {
    const generation = ++this.generation;
    const edited = this.session.layerId;
    const notes: string[] = [];
    const jobs: Promise<Loaded>[] = [];
    const used = new Set<string>();
    const signal = this.abort.signal;
    for (const l of this.input.lines()) {
      if (l.id === edited) continue;
      const skip = lineSkip(l);
      if (skip) {
        notes.push(skip);
        continue;
      }
      if (!(l.point_count > 0)) continue;
      jobs.push(this.load(used, `lines:${l.id}:${l.version}`, async () => {
        const s = geometrySource(`lines:${l.id}`, await getGeometry(l.id, signal));
        return { sources: s ? [s] : [] };
      }));
    }
    const section = this.input.section();
    const query = setSnapQuery(section, this.input.viewBox());
    for (const id of this.input.sets()) {
      jobs.push(this.load(used, `set:${id}?${query}`, async () => {
        const r = readSetSnap(`set:${id}`, await getSetSnap(id, query, signal));
        return { sources: r.sources, note: r.truncated ? `Скважин в окне больше 5 000: у набора №${id} — только устья` : undefined };
      }));
    }
    if (section.line && section.clip) {
      const q = sectionQuery(section.line);
      for (const w of this.input.wireframes()) {
        if (!(w.triangle_count > 0)) continue;
        jobs.push(this.load(used, `wfsec:${w.id}:${w.version}?${q}`, async () => {
          const doc = decodeBfa1(await getWireframeSection(w.id, q, signal));
          const closed = bfa1Array(doc, "closed", "<u1");
          return { sources: [wireframeSectionSource(`wfsec:${w.id}`, bfa1Array(doc, "xyz", "<f8"), bfa1Array(doc, "offsets", "<u4", closed.length + 1), closed)] };
        }));
      }
    }
    for (const key of [...this.cache.keys()]) if (!used.has(key)) this.cache.delete(key);
    const answers = await Promise.all(jobs);
    if (this.disposed || generation !== this.generation) return;
    const sources = answers.flatMap((a) => a.sources);
    for (const a of answers) if (a.note) notes.push(a.note);
    const wanted = new Set(sources.map((s) => s.key));
    for (const key of this.shown) if (!wanted.has(key)) this.session.snapIndex.removeSource(key);
    for (const s of sources) this.session.snapIndex.setSource(s);
    this.shown = wanted;
    this.session.probe = this.probe();
    if (editStore.get().session === this.session) editStore.set({ snapNotes: [...new Set(notes)] });
    this.session.viewChanged();
    this.session.touch();
  }
}
