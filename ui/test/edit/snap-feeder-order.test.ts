/** Э9, подшаг 9.38 (ревью интерфейса, finding 4): the answers of `SnapFeeder.run` come in any order — a run answered
 * after a newer one does not put its stale sources into the index. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { asLoaded } from "../../src/edit/io";
import { emptyLayer } from "../../src/edit/model";
import type { SnapSource } from "../../src/edit/snap";
import { EditSession } from "../../src/edit/session";
import { SnapFeeder } from "../../src/modules/lines/snapSources";
import type { LineLayerInfo } from "../../src/modules/scene/objects";
import { setCoreTarget } from "../../src/shell/api";
import { layer, linegeom } from "../linesHarness";

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
});

describe("SnapFeeder: the order of the answers", () => {
  it("an older run answered last is dropped: the index keeps the newer layer version", async () => {
    const waiting: ((r: Response) => void)[] = [];
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((r) => waiting.push(r))));
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    const s = new EditSession(asLoaded(7, 3, emptyLayer()), "Контуры");
    const set: SnapSource[] = [];
    const real = s.snapIndex.setSource.bind(s.snapIndex);
    s.snapIndex.setSource = (src) => (set.push(src), real(src));
    let shown = [layer(9, "Дороги", { version: 1, point_count: 2 })] as unknown as LineLayerInfo[];
    const feeder = new SnapFeeder(s, { lines: () => shown, sets: () => [], wireframes: () => [], section: () => ({ line: null, clip: false }), viewBox: () => null });
    const first = feeder.run();
    shown = [layer(9, "Дороги", { version: 2, point_count: 2 })] as unknown as LineLayerInfo[];
    const second = feeder.run();
    await vi.waitFor(() => expect(waiting).toHaveLength(2));
    waiting[1](new Response(linegeom(9, 2, [{ pts: [[100, 0, 0], [110, 0, 0]] }])));
    await second;
    waiting[0](new Response(linegeom(9, 1, [{ pts: [[0, 0, 0], [10, 0, 0]] }])));
    await first;
    const last = set.filter((x) => x.key === "lines:9").at(-1)!;
    expect(last.segments!.xyz[0]).toBe(100);
    expect(set.filter((x) => x.key === "lines:9")).toHaveLength(1);
    feeder.dispose();
  });
});
