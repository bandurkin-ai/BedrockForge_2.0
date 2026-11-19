/**
 * «Показать загруженное» (ТЗ 7.13, 7.8): an import of files holds the view as soon as the list of jobs shows it
 * queued or running — the first view of the project does not refit to everything when its layers arrive (they
 * come with `objects.changed`, before the end of the job is seen). Once the import has ended well
 * (`importing/afterImport.ts`, `onObjectsImported`) and the lists of the project hold the line files, rasters
 * and wireframes it stored (`result.objects`), the view is fitted to those of them that are not seen
 * (`Scene.reveal`). Objects near what
 * is on the screen leave the view as it is; a file far away (466 000 / 4 584 000 against the demo) is shown.
 * Extents come from the lists of the core, not from the geometry: the view does not wait for the parts. A line
 * or raster without heights counts at the floor of the scene. No three.js.
 */
import type { SceneObjects } from "./objects";

/** Objects not listed after this long are shown without them (deleted meanwhile, an older list). */
const WAIT_MS = 5000;

/** The line files, rasters and wireframes a finished import of files stored. */
export function importedObjects(job: { kind: string; status: string; result?: Record<string, unknown> | null }): number[] {
  if (job.kind !== "import.objects" || job.status !== "done") return [];
  const ids = job.result?.objects;
  return Array.isArray(ids) ? [...new Set(ids.filter((v): v is number => Number.isInteger(v) && v > 0))] : [];
}

type Bbox6 = [number, number, number, number, number, number];

const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** [xmin, ymin, zmin, xmax, ymax, zmax] of an extent of the core; heights unknown — at `floor`. */
function bbox(e: readonly (number | null)[] | null | undefined, floor: number): Bbox6 | null {
  if (!e || e.length < 6 || ![e[0], e[1], e[3], e[4]].every(num)) return null;
  const z0 = num(e[2]) ? e[2] : floor;
  const z1 = num(e[5]) ? e[5] : floor;
  return [e[0] as number, e[1] as number, Math.min(z0, z1), e[3] as number, e[4] as number, Math.max(z0, z1)];
}

/** The extents of `ids` in the lists (a line file — every layer of it) and how many of them are not listed. */
export function extentsOf(ids: number[], lists: Pick<SceneObjects, "files" | "rasters" | "wireframes">, floor: number): { boxes: Bbox6[]; missing: number } {
  const boxes: Bbox6[] = [];
  let missing = 0;
  // the lists once by id: an import of hundreds of wireframes into a project of thousands
  const byId = <T extends { id: number }>(list: readonly T[]) => new Map(list.map((o) => [o.id, o]));
  const wireframes = byId(lists.wireframes);
  const files = byId(lists.files);
  const rasters = byId(lists.rasters);
  for (const id of ids) {
    const w = wireframes.get(id);
    const f = w ? undefined : files.get(id);
    const r = w || f ? undefined : rasters.get(id);
    if (w) {
      const b = bbox(w.extent, floor);
      if (b) boxes.push(b);
    } else if (f) {
      for (const l of f.layers) {
        const b = bbox(l.extent, floor);
        if (b) boxes.push(b);
      }
    } else if (r) {
      const corners = (r.georef?.corners3d ?? []).filter((c) => num(c[0]) && num(c[1]));
      if (corners.length) {
        const xs = corners.map((c) => c[0] as number);
        const ys = corners.map((c) => c[1] as number);
        const zs = corners.map((c) => c[2]).filter(num);
        const z = zs.length ? zs : [r.elevation ?? floor];
        boxes.push([Math.min(...xs), Math.min(...ys), Math.min(...z), Math.max(...xs), Math.max(...ys), Math.max(...z)]);
      }
    } else missing++;
  }
  return { boxes, missing };
}

export interface ShowLoadedDeps {
  hold(): void;
  reveal(boxes: Bbox6[]): boolean;
  lists(): Pick<SceneObjects, "files" | "rasters" | "wireframes">;
  floor(): number;
  /** The job belongs to the open project (not to one closed while it ran). */
  ours(job: { source: string }): boolean;
}

/** Waits for the objects of the last import in the lists, then shows them if they are not seen. */
export class ShowLoaded {
  private pending: number[] | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private readonly held = new Set<string>();

  constructor(private readonly deps: ShowLoadedDeps) {}

  /** The list of jobs changed: an import of files of the open project queued or running holds the view (once
   * per job). */
  jobs(list: readonly { source: string; id: number; kind: string; status: string }[]): void {
    for (const j of list) {
      if (j.kind !== "import.objects" || (j.status !== "queued" && j.status !== "running") || !this.deps.ours(j)) continue;
      const key = `${j.source}:${j.id}`;
      if (this.held.has(key)) continue;
      this.held.add(key);
      this.deps.hold();
    }
  }

  /** An import of files ended well. */
  imported(job: Parameters<typeof importedObjects>[0]): void {
    const ids = importedObjects(job);
    if (!ids.length) return;
    this.deps.hold();
    this.pending = ids;
    this.clearTimer();
    this.timer = setTimeout(() => this.sync(true), WAIT_MS);
    this.sync();
  }

  /** The lists changed (or the wait is over: `force`). */
  sync(force = false): void {
    if (!this.pending) return;
    const { boxes, missing } = extentsOf(this.pending, this.deps.lists(), this.deps.floor());
    if (missing && !force) return;
    this.pending = null;
    this.clearTimer();
    if (boxes.length) this.deps.reveal(boxes);
  }

  dispose(): void {
    this.pending = null;
    this.clearTimer();
  }

  private clearTimer(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }
}
