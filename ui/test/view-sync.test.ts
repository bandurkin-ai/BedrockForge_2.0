import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { hiddenKey, patchEntry, patchFields, toggleHidden, ViewSync, type ViewIO } from "../src/modules/scene/viewSync";

/** A fake core with versions and 409 like /api/v1/view. */
function fakeCore(initial: Record<string, { value: unknown; version: number }> = {}) {
  const db = new Map(Object.entries(initial));
  const puts: { key: string; value: unknown; expected: number }[] = [];
  const io: ViewIO = {
    get: async () => ({ values: Object.fromEntries(db) }),
    put: async (key, value, expected) => {
      puts.push({ key, value, expected });
      const cur = db.get(key) ?? { value: undefined, version: 0 };
      if (cur.version !== expected) throw { status: 409, body: { value: cur.value, version: cur.version } };
      const next = { value, version: cur.version + 1 };
      db.set(key, next);
      return next;
    },
  };
  return { db, puts, io };
}

describe("project view settings sync", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("applies at once and writes once after 400 ms", async () => {
    const core = fakeCore();
    const seen: unknown[] = [];
    const sync = new ViewSync(core.io, (_k, v) => seen.push(v));
    await sync.load();
    sync.update("scene", patchFields({ vexag: 2 }));
    sync.update("scene", patchFields({ grid: false }));
    expect(seen.at(-1)).toEqual({ vexag: 2, grid: false });
    expect(core.puts).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(400);
    expect(core.puts).toEqual([{ key: "scene", value: { vexag: 2, grid: false }, expected: 0 }]);
    expect(core.db.get("scene")?.version).toBe(1);
  });

  it("on 409 applies the change again to the server value (hidden keys merge)", async () => {
    const core = fakeCore({ hidden: { value: ["set:1"], version: 1 } });
    const sync = new ViewSync(core.io, () => {});
    await sync.load();
    core.db.set("hidden", { value: ["set:1", "wf:7"], version: 2 }); // changed in another window
    sync.update("hidden", toggleHidden("set:2:paths", false));
    sync.update("hidden", toggleHidden("set:1", true));
    await sync.flush("hidden");
    expect(core.db.get("hidden")).toEqual({ value: ["wf:7", "set:2:paths"], version: 3 });
    expect(sync.value("hidden")).toEqual(["wf:7", "set:2:paths"]);
  });

  it("after a second 409 keeps the server value and tells", async () => {
    const core = fakeCore({ scene: { value: { vexag: 1 }, version: 1 } });
    const notes: string[] = [];
    let n = 0;
    const io: ViewIO = {
      get: core.io.get,
      put: async () => {
        n++;
        throw { status: 409, body: { value: { vexag: 5 }, version: 4 + n } };
      },
    };
    const sync = new ViewSync(io, () => {}, (t) => notes.push(t));
    await sync.load();
    sync.update("scene", patchFields({ vexag: 3 }));
    await sync.flush("scene");
    expect(n).toBe(2);
    expect(sync.value("scene")).toEqual({ vexag: 5 });
    expect(notes[0]).toMatch(/другом окне/);
  });

  it("a new project drops the writes of the old one", async () => {
    const core = fakeCore();
    const sync = new ViewSync(core.io, () => {});
    await sync.load();
    sync.update("scene", patchFields({ vexag: 2 }));
    await sync.load();
    await vi.advanceTimersByTimeAsync(1000);
    expect(core.puts).toHaveLength(0);
  });

  it("keys of ТЗ 7.0.10 and map entries", () => {
    expect(hiddenKey("collars", 3)).toBe("set:3:collars");
    expect(hiddenKey("wireframe", 12)).toBe("wf:12");
    expect(() => hiddenKey("paths", 0)).toThrow(/номер/);
    expect(patchEntry("3", { w: 1 })({ "3": { w: 0 }, "4": {} })).toEqual({ "4": {}, "3": { w: 1 } });
    expect(patchEntry("4", undefined)({ "3": 1, "4": 2 })).toEqual({ "3": 1 });
  });
});
