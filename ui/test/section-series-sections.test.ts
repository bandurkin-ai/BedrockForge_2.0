import { describe, expect, it } from "vitest";
import { encodeBfa1 } from "../src/edit/bfa1";
import { KEEP_SECTIONS, packetPaths, SeriesSections, type SeriesTarget } from "../src/modules/section/seriesSections";
import { readSeriesSection, type SeriesSectionPacket } from "../src/viz/mesh/section";
import type { SectionDataMark } from "../src/viz/sceneTypes";

/** BFA1 `series_section` of section k: wireframe id gets one closed square at y = 5000 + 25k (ids without lines too). */
function packet(path: string): ArrayBuffer {
  const m = /sections\/(\d+)\/wireframes\?(?:seg=(\d+)&)?ids=([\d,]*)/.exec(path)!;
  const k = Number(m[1]);
  const seg = m[2] === undefined ? null : Number(m[2]);
  const ids = m[3].split(",").filter(Boolean).map(Number);
  const xyz: number[] = [];
  const offsets = [0];
  const closed: number[] = [];
  const wf: number[] = [];
  for (const id of ids) {
    if (id % 2 === 0) continue; // the plane misses the even ones
    const y = 5000 + 25 * (seg ?? k);
    xyz.push(id, y, 0, id + 10, y, 0, id + 10, y, -10, id, y, -10);
    offsets.push(offsets.at(-1)! + 4);
    closed.push(1);
    wf.push(id);
  }
  return encodeBfa1({ kind: "series_section", format: 1, series: 7, key: "abc", k, seg, point: [1000, 5000 + 25 * k, 0], normal: [0, 1, 0], clip: null, wireframes: ids, missing: [], lines: closed.length, points: xyz.length / 3 }, [
    ["xyz", "<f8", Float64Array.from(xyz)],
    ["offsets", "<u4", Uint32Array.from(offsets)],
    ["closed", "<u1", Uint8Array.from(closed)],
    ["wf", "<u4", Uint32Array.from(wf)],
  ]);
}

/** A fake core and fake time: loads wait until answered, timers run when the clock passes them. */
function harness(cancellable = true) {
  let now = 0;
  const timers: { at: number; fn: () => void; id: number }[] = [];
  let tid = 0;
  const pending = new Map<string, (b: ArrayBuffer | null) => void>();
  const loads: string[] = [];
  const cancelled: string[] = [];
  const posts: { path: string; body: unknown }[] = [];
  const shown: (SeriesSectionPacket | null)[] = [];
  const marks: SectionDataMark[] = [];
  const s = new SeriesSections({
    load: (path) => {
      loads.push(path);
      return new Promise((res) => pending.set(path, res));
    },
    cancel: (path) => {
      cancelled.push(path);
      if (cancellable) pending.delete(path);
    },
    post: async (path, body) => void posts.push({ path, body }),
    show: (p) => shown.push(p),
    onMark: (m) => marks.push(m),
    now: () => now,
    setTimer: (fn, ms) => (timers.push({ at: now + ms, fn, id: ++tid }), tid),
    clearTimer: (h) => {
      const i = timers.findIndex((t) => t.id === h);
      if (i >= 0) timers.splice(i, 1);
    },
    idleMs: 150,
  });
  const flush = () => new Promise((r) => setTimeout(r, 0));
  const answer = async (match: string, ms = 0) => {
    now += ms;
    for (const [path, res] of [...pending]) if (path.includes(match)) (pending.delete(path), res(packet(path)));
    await flush();
  };
  const tick = async (ms: number) => {
    now += ms;
    for (const t of timers.filter((x) => x.at <= now)) (timers.splice(timers.indexOf(t), 1), t.fn());
    await flush();
  };
  return { s, loads, cancelled, posts, shown, marks, pending, answer, tick, flush };
}

const target = (index: number, extra: Partial<SeriesTarget> = {}): SeriesTarget => ({ seriesId: 7, version: 1, polyline: false, index, steps: 10, wireframes: [[1, 1], [2, 1], [3, 4]], ...extra });

describe("the sections of wireframes on a series", () => {
  it("BFA1 series_section split per wireframe, float64 kept; the paths of a packet ≤ 200 wireframes each", () => {
    const p = readSeriesSection(packet("/section-series/7/sections/2/wireframes?ids=1,2,3"));
    expect(p).toMatchObject({ series: 7, key: "abc", k: 2, seg: null, point: [1000, 5050, 0], missing: [] });
    expect([...p.sections.keys()]).toEqual([1, 2, 3]);
    expect(p.sections.get(2)!.closed.length).toBe(0); // asked, no line
    expect(Array.from(p.sections.get(3)!.xyz.slice(0, 3))).toEqual([3, 5050, 0]);
    expect(Array.from(p.sections.get(3)!.offsets)).toEqual([0, 4]);
    const ids = Array.from({ length: 450 }, (_, i) => i + 1);
    const paths = packetPaths({ seriesId: 7, polyline: false }, 4, ids);
    expect(paths).toHaveLength(3);
    expect(paths[0]).toBe(`/section-series/7/sections/4/wireframes?ids=${ids.slice(0, 200).join(",")}`);
    expect(packetPaths({ seriesId: 7, polyline: true }, 2, [5])).toEqual(["/section-series/7/sections/0/wireframes?seg=2&ids=5"]);
  });

  it("the packet of k shown; the neighbours by idle time; a prepared section — without a request", async () => {
    const h = harness();
    h.s.set(target(3));
    expect(h.shown).toEqual([null]); // the old lines leave at once
    expect(h.loads).toEqual(["/section-series/7/sections/3/wireframes?ids=1,2,3"]);
    expect(h.posts).toEqual([{ path: "/section-series/7/cache", body: { wireframe_ids: [1, 2, 3], around: 3 } }]);
    expect(h.s.mark).toMatchObject({ series: 7, k: 3, ready: false });
    await h.answer("sections/3/", 40);
    expect(h.shown.at(-1)?.k).toBe(3);
    expect(h.s.mark).toMatchObject({ series: 7, k: 3, seg: null, ready: true, ms: 40, prepared: false });
    // nothing for 150 ms: k + 1, then k − 1, k + 2, k − 2 — one at a time
    expect(h.loads).toHaveLength(1);
    for (const k of [4, 2, 5, 1]) {
      await h.tick(150);
      expect(h.loads.at(-1)).toContain(`sections/${k}/`);
      await h.answer(`sections/${k}/`);
    }
    await h.tick(1000);
    expect(h.loads).toHaveLength(5);
    expect(h.s.mark.kept).toBe(KEEP_SECTIONS);
    // `]`: section 4 was prepared — shown at once, no request
    h.s.set(target(4));
    expect(h.loads).toHaveLength(5);
    expect(h.shown.at(-1)?.k).toBe(4);
    expect(h.s.mark).toMatchObject({ k: 4, ready: true, ms: 0, prepared: true });
    // the series is the same: the cache is not asked again
    expect(h.posts).toHaveLength(1);
  });

  it("a newer section cancels the old request; an answer of a section no longer current is dropped", async () => {
    const h = harness();
    h.s.set(target(1));
    h.s.set(target(2));
    expect(h.cancelled).toEqual(["/section-series/7/sections/1/wireframes?ids=1,2,3"]);
    expect(h.shown).toEqual([null, null]);
    // the answer for 1 comes anyway (it was already on its way): not shown
    h.s.set(target(5));
    h.pending.set("/section-series/7/sections/2/wireframes?ids=1,2,3", () => undefined);
    await h.answer("sections/5/");
    expect(h.shown.filter((p) => p !== null).map((p) => p!.k)).toEqual([5]);
    // the in-flight promise of an old section resolving late changes nothing
    const before = h.shown.length;
    await h.answer("sections/2/");
    expect(h.shown.length).toBe(before);
    expect(h.s.mark).toMatchObject({ k: 5, ready: true });
  });

  it("an answer that comes after its section was left is not shown (the cancel came too late)", async () => {
    const h = harness(false);
    h.s.set(target(1));
    h.s.set(target(2));
    await h.answer("sections/1/"); // the old answer arrives anyway
    expect(h.shown).toEqual([null, null]);
    expect(h.s.mark).toMatchObject({ k: 2, ready: false });
    await h.answer("sections/2/", 30);
    expect(h.shown.at(-1)?.k).toBe(2);
    expect(h.s.mark).toMatchObject({ k: 2, ready: true, ms: 30 });
  });

  it("a change of the wireframes (versions) or of the series asks the cache again and drops the kept sections", async () => {
    const h = harness();
    h.s.set(target(0));
    await h.answer("sections/0/");
    await h.tick(150);
    await h.answer("sections/1/");
    h.s.set(target(0, { wireframes: [[1, 1], [2, 1], [3, 5]] }));
    expect(h.posts).toHaveLength(2);
    expect(h.loads.at(-1)).toContain("sections/0/"); // asked again: the kept lines were of the old geometry
    h.s.set(target(0, { version: 2, wireframes: [[1, 1], [2, 1], [3, 5]] }));
    expect(h.posts).toHaveLength(3);
    // no wireframes shown — nothing to cut, the lines leave
    h.s.set(target(0, { wireframes: [] }));
    expect(h.shown.at(-1)).toBeNull();
    expect(h.s.mark.series).toBeNull();
    h.s.set(null);
    expect(h.loads.filter((p) => p.includes("sections/0/"))).toHaveLength(3);
  });

  it("the job of the cache done reads the neighbours anew; a polyline moves by segments", async () => {
    const h = harness();
    h.s.set(target(0, { polyline: true, steps: 3 }));
    expect(h.loads).toEqual(["/section-series/7/sections/0/wireframes?seg=0&ids=1,2,3"]);
    await h.answer("seg=0");
    expect(h.s.mark).toMatchObject({ k: 0, seg: 0, ready: true });
    await h.tick(150);
    await h.answer("seg=1");
    h.s.cacheReady(99); // another series: nothing
    await h.tick(150);
    const n = h.loads.length;
    h.s.cacheReady(7);
    await h.tick(150);
    expect(h.loads.length).toBe(n + 1);
    expect(h.loads.at(-1)).toContain("seg=1");
  });
});
