// Э18 18.26: a job that is over between two reads of the list (a small import) is reported ended too — the tree of the block models
// of a small DAT file stayed empty because the interface had never seen the job running.
import { describe, expect, it, vi } from "vitest";

let answer: unknown[] = [];
vi.mock("../src/shell/api", () => ({
  api: { get: async () => ({ jobs: answer, workers: null }) },
  coreTarget: () => null,
}));
vi.mock("../src/shell/coreClient", () => ({ apiUrl: () => "", authHeaders: () => ({}) }));

const { endedNow, onJobFinished, refresh } = await import("../src/modules/jobs/state");

const job = (id: number, status: string) => ({ source: "p", id, kind: "import.objects", status });

describe("the end of a job is reported once", () => {
  it("endedNow: a change of the status to an end, or a first sight of a job that is over after the first read", () => {
    expect(endedNow("running", "done", true)).toBe(true);
    expect(endedNow("queued", "failed", true)).toBe(true);
    expect(endedNow("done", "done", true)).toBe(false);
    expect(endedNow("running", "running", true)).toBe(false);
    expect(endedNow(undefined, "done", true)).toBe(true);
    expect(endedNow(undefined, "done", false)).toBe(false); // the first read: the jobs of the past
    expect(endedNow(undefined, "queued", true)).toBe(false);
  });

  it("refresh: the first read is silent, a job born over is reported once, a job seen running is reported at its end", async () => {
    const seen: number[] = [];
    const off = onJobFinished((j) => void seen.push(j.id));
    answer = [job(1, "done")]; // what the core had before the window
    await refresh();
    expect(seen).toEqual([]);
    answer = [job(1, "done"), job(2, "done")]; // a small import: over before the window read the list
    await refresh();
    expect(seen).toEqual([2]);
    await refresh();
    expect(seen).toEqual([2]);
    answer = [job(1, "done"), job(2, "done"), job(3, "running")];
    await refresh();
    expect(seen).toEqual([2]);
    answer = [job(1, "done"), job(2, "done"), job(3, "done")];
    await refresh();
    expect(seen).toEqual([2, 3]);
    off();
  });
});
