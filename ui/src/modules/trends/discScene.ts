/**
 * The discs of the structural trends in the main 3D (ТЗ 7.35 Д-15; ADR 0026 решение 27): a structural trend shown (the view
 * key `ellipsoids`, `trend.show` — the key is the same as of the ellipsoids: «показать в 3D») — a layer `trend:discs:<id>`
 * (`viz/discs/layer.ts`: a disc at every measurement or face, the colour of the trend, the diameter `size`). The discs are
 * read from the core (`GET /trends/{id}/discs`) once per version of the trend; a trend saved again is read again, a size
 * changed — the discs are rebuilt from what was read, a colour changed — repainted. A source that is gone (409) or a trend
 * hidden — the layer goes. A lazy chunk with three.js (`scene.ts` attaches it with the ellipsoids). `trendsStore.discs` tells
 * the window «Показано N из M».
 */
import { bridge } from "../../bridge";
import { errorText } from "../../shell/props";
import { buildDiscs } from "../../viz/discs/build";
import { DiscLayer } from "../../viz/discs/layer";
import type { Scene } from "../../viz/Scene";
import { viewStore } from "../scene/bind";
import { sceneUi } from "../scene/sceneHost";
import { loadDiscs, type Discs, type Trend } from "./api";
import { ELLIPSOIDS_KEY, setDiscInfo, shownObjects, trendsStore } from "./state";

export const discLayerKey = (id: number): string => `trend:discs:${id}`;

/** The disc is a circle of the diameter `size`. */
export const discRadius = (t: Pick<Trend, "size">): number => t.size / 2;

interface Shown {
  layer: DiscLayer;
  version: number;
  size: number;
  color: string;
}

interface Read {
  version: number;
  discs: Discs;
}

export class DiscScene {
  private readonly shown = new Map<number, Shown>();
  private readonly read = new Map<number, Read>();
  /** reads in flight: id → the version asked */
  private readonly asked = new Map<number, number>();
  /** versions refused (a source that is gone): not asked again until the trend is saved again */
  private readonly refused = new Map<number, number>();
  /** versions and sizes with nothing to draw (no measurement has a position): said once, not rebuilt */
  private readonly empty = new Map<number, string>();
  private readonly stops: (() => void)[] = [];
  private disposed = false;

  constructor(
    private readonly scene: Scene,
    private readonly load: (id: number) => Promise<Discs> = loadDiscs,
  ) {
    const sync = () => this.sync();
    this.stops.push(trendsStore.subscribe(sync), viewStore.subscribe(sync), scene.onRestored(() => this.restore()));
    this.sync();
  }

  /** The structural trends to draw now. */
  wanted(): Trend[] {
    const trends = trendsStore.get().trends;
    return shownObjects(viewStore.get()[ELLIPSOIDS_KEY]).flatMap((id) => trends.filter((t) => t.id === id && t.kind === "structural" && !t.sourceLost));
  }

  /** For the E2E hook and the tests: the discs drawn. */
  stats(): { id: number; count: number; skipped: number; radius: number; color: string; opacity: number }[] {
    return [...this.shown.entries()].map(([id, s]) => ({ id, ...s.layer.stats() }));
  }

  /** `sync` is running: an event of the store it causes itself (`setDiscInfo`) asks for one more round, not a nested one */
  private busy = false;
  private again = false;

  sync(): void {
    if (this.disposed) return;
    if (this.busy) {
      this.again = true;
      return;
    }
    this.busy = true;
    try {
      do {
        this.again = false;
        this.round();
      } while (this.again && !this.disposed);
    } finally {
      this.busy = false;
    }
  }

  private round(): void {
    const want = this.wanted();
    const ids = new Set(want.map((t) => t.id));
    let changed = false;
    for (const id of [...this.shown.keys()]) if (!ids.has(id)) changed = this.drop(id) || changed;
    for (const m of [this.read, this.refused, this.empty] as Map<number, unknown>[]) for (const id of [...m.keys()]) if (!ids.has(id)) m.delete(id);
    for (const t of want) {
      const have = this.read.get(t.id);
      if (!have || have.version !== t.version) {
        this.ask(t);
        continue;
      }
      if (this.empty.get(t.id) === `${t.version}:${t.size}`) continue;
      const cur = this.shown.get(t.id);
      if (cur && cur.version === t.version && cur.size === t.size) {
        if (cur.color !== t.color) {
          cur.layer.setColor(t.color);
          cur.color = t.color;
          changed = true;
        }
        continue;
      }
      if (cur) this.drop(t.id);
      this.show(t, have.discs);
      changed = true;
    }
    if (changed) this.scene.invalidate();
  }

  private ask(t: Trend): void {
    if (this.asked.get(t.id) === t.version || this.refused.get(t.id) === t.version) return;
    this.asked.set(t.id, t.version);
    this.load(t.id).then(
      (discs) => {
        if (this.asked.get(t.id) === t.version) this.asked.delete(t.id);
        if (this.disposed) return;
        this.read.set(t.id, { version: t.version, discs });
        this.sync();
      },
      (e: unknown) => {
        if (this.asked.get(t.id) === t.version) this.asked.delete(t.id);
        if (this.disposed) return;
        this.refused.set(t.id, t.version);
        sceneUi.set({ error: `Диски тренда «${t.name}» не показаны в 3D: ${errorText(e)}` });
      },
    );
  }

  private show(t: Trend, discs: Discs): void {
    try {
      const build = buildDiscs(discs, discRadius(t));
      if (!build.count) {
        this.empty.set(t.id, `${t.version}:${t.size}`);
        setDiscInfo(t.id, { n: 0, total: discs.total, thinned: discs.thinned, skipped: build.skipped });
        sceneUi.set({ error: `У тренда «${t.name}» нет дисков для показа: нет замеров с положением и нормалью` });
        return;
      }
      const layer = new DiscLayer(build, t.color, { clip: this.scene.clipPlanes });
      this.shown.set(t.id, { layer, version: t.version, size: t.size, color: t.color });
      this.scene.addLayer(discLayerKey(t.id), layer);
      setDiscInfo(t.id, { n: build.count, total: discs.total, thinned: discs.thinned, skipped: build.skipped });
    } catch (e) {
      sceneUi.set({ error: `Диски тренда «${t.name}» не показаны в 3D: ${errorText(e)}` });
    }
  }

  private drop(id: number): boolean {
    const had = this.shown.delete(id); // first: the store's event below calls `sync` again
    if (had) this.scene.removeLayer(discLayerKey(id), false); // the scene disposes the layer it removes
    setDiscInfo(id, null);
    return had;
  }

  private restore(): void {
    this.shown.clear(); // the scene dropped its layers with the context
    this.sync();
  }

  dispose(): void {
    this.disposed = true;
    this.stops.forEach((fn) => fn());
    for (const id of [...this.shown.keys()]) this.drop(id);
    this.scene.invalidate();
  }
}

/** Attaches the discs to a scene; gives what detaches them (the hook of the E2E: `window.__bfTrendDiscs`). */
export function attachDiscScene(scene: Scene, load?: (id: number) => Promise<Discs>): { dispose: () => void; scene: DiscScene } {
  const s = new DiscScene(scene, load);
  const hook = bridge()?.flags?.().e2e === true && typeof window !== "undefined";
  if (hook) window.__bfTrendDiscs = { discs: () => s.stats() };
  return {
    scene: s,
    dispose: () => {
      s.dispose();
      if (hook) delete window.__bfTrendDiscs;
    },
  };
}

declare global {
  interface Window {
    __bfTrendDiscs?: unknown;
  }
}
