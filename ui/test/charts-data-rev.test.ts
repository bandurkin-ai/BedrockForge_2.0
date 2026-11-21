// An open chart computes again after an edit of its set's data (ADR 0019 §2 «Данные», Т7.28-1): the revision of
// the set's data is in the key of the window's calculation, as the swath's `rev`; `drillholes.changed` of the set
// sends the same settings again, of another set — not. The core stream is a stub.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const coreEvents = new Set<(event: string, data: string) => void>();
vi.mock("../src/modules/jobs/state", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/modules/jobs/state")>()),
  onCoreEvent: (fn: (event: string, data: string) => void) => {
    coreEvents.add(fn);
    return () => coreEvents.delete(fn);
  },
}));

import { histogramCalculator, histogramRequest } from "../src/charts/histogram/model";
import type { HistogramResult } from "../src/modules/charts/api";
import { defaultSettings } from "../src/modules/charts/settings";
import { chartsStore, dataChanged, dataRev } from "../src/modules/charts/state";
import { createApp } from "../src/shell/app";

const fire = (event: string, data: unknown) => coreEvents.forEach((fn) => fn(event, JSON.stringify(data)));

describe("the revision of a set's data", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("offline"))));
    chartsStore.set({ components: {}, rev: 0, revs: {} });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("dataChanged: of a set — its revision, of all — every one; Q-Q counts both sets", () => {
    expect(dataRev({ set_id: 3 })).toBe(0);
    dataChanged(3);
    expect(dataRev({ set_id: 3 })).toBe(1);
    expect(dataRev({ set_id: 4 })).toBe(0);
    expect(dataRev({ set_id: 4, y_set_id: 3 })).toBe(1);
    dataChanged();
    expect(dataRev({ set_id: 4 })).toBe(1);
  });

  it("the window is open → drillholes.changed of its set → a new request with the same settings", async () => {
    createApp({ persist: false }); // binds the charts: the core events
    const load = vi.fn((_body: unknown) => Promise.resolve({} as HistogramResult));
    const applied: unknown[] = [];
    const calc = histogramCalculator((r) => applied.push(r), () => undefined, undefined, load);
    const body = histogramRequest({ ...defaultSettings("histogram", 3), table: "пробы", field: "AU" });
    expect(body).not.toBeNull();
    calc.request(body!);
    await vi.advanceTimersByTimeAsync(300);
    expect(load).toHaveBeenCalledTimes(1);
    calc.request(body!); // the same settings, the same data — not again
    await vi.advanceTimersByTimeAsync(300);
    expect(load).toHaveBeenCalledTimes(1);
    fire("drillholes.changed", { set_id: 4 }); // another set
    await vi.advanceTimersByTimeAsync(300);
    expect(load).toHaveBeenCalledTimes(1);
    fire("drillholes.changed", { set_id: 3 });
    await vi.advanceTimersByTimeAsync(300);
    expect(load).toHaveBeenCalledTimes(2);
    expect(load.mock.calls[1][0]).toEqual(body);
    expect(applied).toHaveLength(2);
    calc.stop();
    fire("drillholes.changed", { set_id: 3 }); // closed — nothing
    await vi.advanceTimersByTimeAsync(300);
    expect(load).toHaveBeenCalledTimes(2);
  });
});
