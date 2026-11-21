/**
 * The swath plots shown in the main 3D (ТЗ 7.30 «3D», 7.35 Д-01; ADR 0019 §2, §6): the view key `swaths` says
 * which; for each — a scene layer `swath:<id>` (`viz/swath/layer.ts`) of the cuboid, its swaths and the plot over
 * it. An open window gives its settings as edited and its last answer (no second request); a swath without a
 * window is computed here by its id (`POST /stats/swath {swath_id}`) and again after an edit of data. A build is
 * made again only when the answer or the look changed; the swath under the cursor of a window is lit without one.
 * After a lost context the layers come again. A lazy chunk with three.js (`bind.ts` attaches it to every scene).
 */
import { niceTicks } from "../../charts/column/scale";
import { formatNumber } from "../../charts/core/axisScale";
import { errorText } from "../../shell/props";
import type { Scene } from "../../viz/Scene";
import { buildSwath, type BuildResult } from "../../viz/swath/build";
import { SwathLayer, type SwathLayerContext } from "../../viz/swath/layer";
import { viewStore } from "../scene/bind";
import { sceneUi } from "../scene/sceneHost";
import { shownSwaths, SWATHS_KEY } from "../scene/viewSync";
import { computeSwath, type SwathResult } from "./api";
import { normalizeSwath, shownRange, type SwathSettings } from "./model";
import { liveStore, swathsStore } from "./state";

export const swathLayerKey = (id: number): string => `swath:${id}`;

interface Shown {
  /** what the layer was built from */
  result: SwathResult | null;
  look: string;
  layer: SwathLayer | null;
  /** the request of a swath without a window: its version and the revision of the data */
  asked: string;
}

/** The look of a swath in 3D (colours, lines, bars, range, plot) — what a build depends on besides the answer. */
const lookKey = (s: SwathSettings, count: number): string => JSON.stringify([s.series.map((x) => [x.color, x.line, x.bars]), shownRange(s.range, count), s.view]);

export class SwathScene {
  private readonly shown = new Map<number, Shown>();
  private readonly own = new Map<number, SwathResult>();
  private readonly stops: (() => void)[] = [];
  private disposed = false;

  /** `labels` — the texts of the plots (tests give them without a canvas). */
  constructor(
    private readonly scene: Scene,
    private readonly opts: { labels?: () => SwathLayerContext["labels"] } = {},
  ) {
    const sync = () => this.sync();
    this.stops.push(swathsStore.subscribe(sync), liveStore.subscribe(sync), viewStore.subscribe(sync), scene.onRestored(() => this.restore()));
    this.sync();
  }

  /** For the E2E hook and the tests: the swaths drawn and the swath lit. */
  stats(): { id: number; count: number; lit: number | null; chart: boolean }[] {
    return [...this.shown.entries()].flatMap(([id, s]) => (s.layer ? [{ id, count: s.layer.build.count, lit: s.layer.highlighted, chart: !!s.layer.build.chart }] : []));
  }

  sync(): void {
    if (this.disposed) return;
    const { swaths, rev, hover } = swathsStore.get();
    const live = liveStore.get().live;
    const wanted = new Set(shownSwaths(viewStore.get()[SWATHS_KEY]).filter((id) => swaths.some((r) => r.id === id)));
    let changed = false;
    for (const id of [...this.shown.keys()]) if (!wanted.has(id)) changed = this.drop(id) || changed;
    for (const id of [...this.own.keys()]) if (!wanted.has(id) || live[id]) this.own.delete(id);
    for (const id of wanted) {
      const row = swaths.find((r) => r.id === id)!;
      const mine = live[id];
      const settings = mine?.settings ?? normalizeSwath(row.settings);
      let st = this.shown.get(id);
      if (!st) {
        st = { result: null, look: "", layer: null, asked: "" };
        this.shown.set(id, st);
      }
      if (!mine) {
        const asked = `${row.version}:${rev}`;
        if (st.asked !== asked) {
          st.asked = asked;
          void this.ask(id, asked);
        }
      } else st.asked = "";
      const result = mine ? mine.result : (this.own.get(id) ?? null);
      // an answer for another list of series is on its way out: nothing to draw the lines with
      if (!result || result.series.length !== settings.series.length) continue;
      const look = lookKey(settings, result.count);
      if (st.result !== result || st.look !== look || !st.layer) {
        if (st.layer) this.scene.removeLayer(swathLayerKey(id), false);
        st.result = result;
        st.look = look;
        st.layer = this.make(result, settings);
        if (st.layer) this.scene.addLayer(swathLayerKey(id), st.layer);
        changed = true;
      }
      const lit = hover?.id === id ? hover.index : null;
      if (st.layer && st.layer.highlighted !== lit) {
        st.layer.setHighlight(lit);
        changed = true;
      }
    }
    if (changed) this.scene.invalidate();
  }

  private make(result: SwathResult, s: SwathSettings): SwathLayer | null {
    try {
      const build = buildSwath(result as unknown as BuildResult, {
        series: s.series,
        range: shownRange(s.range, result.count),
        chart3d: s.view.chart3d,
        height3d: s.view.height3d,
        ticks: (lo, hi) => niceTicks(lo, hi, 5),
        format: (v, d) => formatNumber(v, d),
      });
      const host = typeof document !== "undefined" ? document.documentElement : null;
      const background = host ? getComputedStyle(host).getPropertyValue("--scene-bg").trim() || undefined : undefined;
      return new SwathLayer(build, { clip: this.scene.clipPlanes, resolution: this.scene.resolution, background, labels: this.opts.labels?.() });
    } catch (e) {
      sceneUi.set({ error: `Swath plot не показан в 3D: ${errorText(e)}` });
      return null;
    }
  }

  private async ask(id: number, asked: string): Promise<void> {
    try {
      const r = await computeSwath({ swath_id: id });
      if (this.disposed || this.shown.get(id)?.asked !== asked) return;
      this.own.set(id, r);
      this.sync();
    } catch (e) {
      if (!this.disposed && this.shown.get(id)?.asked === asked) sceneUi.set({ error: `Swath plot не показан в 3D: ${errorText(e)}` });
    }
  }

  private drop(id: number): boolean {
    const st = this.shown.get(id);
    this.shown.delete(id);
    if (!st?.layer) return false;
    this.scene.removeLayer(swathLayerKey(id), false);
    return true;
  }

  /** After a lost context the scene dropped every layer: they are built again from the answers kept. */
  private restore(): void {
    for (const st of this.shown.values()) st.layer = null;
    this.sync();
  }

  dispose(): void {
    this.disposed = true;
    this.stops.forEach((fn) => fn());
    for (const id of [...this.shown.keys()]) this.drop(id);
    this.scene.invalidate();
  }
}

/** Attaches the swaths to a scene; gives what detaches them. */
export function attachSwathScene(scene: Scene): () => void {
  const s = new SwathScene(scene);
  return () => s.dispose();
}
