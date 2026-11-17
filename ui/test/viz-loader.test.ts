import { describe, expect, it } from "vitest";
import { VizError } from "../src/viz/error";
import { LoadQueue, type FetchInit, type ResponseLike } from "../src/viz/load/loader";

interface Call {
  done?: boolean;
  url: string;
  init: FetchInit;
  respond: (status: number, body?: ArrayBuffer | string, etag?: string) => void;
  fail: () => void;
}

/** A fetch whose answers the test gives by hand, recording the calls in order. */
function fakeFetch() {
  const calls: Call[] = [];
  const fetch = (url: string, init: FetchInit) =>
    new Promise<ResponseLike>((resolve, reject) => {
      init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      calls.push({
        url,
        init,
        respond: (status, body, etag) =>
          resolve({
            status,
            ok: status >= 200 && status < 300,
            headers: { get: (n: string) => (n.toLowerCase() === "etag" ? etag ?? null : null) },
            arrayBuffer: async () => (body instanceof ArrayBuffer ? body : new TextEncoder().encode(body ?? "").buffer),
            text: async () => (typeof body === "string" ? body : ""),
          }),
        fail: () => reject(new TypeError("Failed to fetch")),
      });
    });
  return { calls, fetch };
}

const tick = () => new Promise((r) => setTimeout(r, 0));
const bytes = (...v: number[]) => new Uint8Array(v).buffer;

describe("download queue", () => {
  it("runs at most 4 at once, nearest to the screen centre first", async () => {
    const f = fakeFetch();
    const q = new LoadQueue({ fetch: f.fetch, concurrency: 10, headers: () => ({ "X-BF-Token": "t" }) });
    const priorities = [0.9, 0.1, 5, 0.5, 0.3, 1000, 0.2, 0.05];
    const all = priorities.map((p, i) => q.load({ url: `/p/${i}`, priority: p }));
    expect(f.calls.length).toBe(0); // requests queued together start together, by priority
    await tick();
    expect(f.calls.length).toBe(4);
    expect(q.active).toBe(4);
    expect(q.queued).toBe(4);
    expect(f.calls[0].init.headers["X-BF-Token"]).toBe("t");
    for (let k = 0; k < priorities.length; k++) {
      const c = f.calls.find((x) => !x.done)!;
      c.done = true;
      c.respond(200, bytes(k));
      await tick();
      expect(q.active).toBeLessThanOrEqual(4);
    }
    const results = await Promise.all(all);
    expect(q.peak).toBe(4);
    expect(f.calls.map((c) => c.url)).toEqual(["/p/7", "/p/1", "/p/6", "/p/4", "/p/3", "/p/0", "/p/2", "/p/5"]);
    expect(results.every((r) => r.status === 200 && r.buffer?.byteLength === 1)).toBe(true);
  });

  it("reprioritizes what is queued and merges repeated requests", async () => {
    const f = fakeFetch();
    const q = new LoadQueue({ fetch: f.fetch, concurrency: 1 });
    const a = q.load({ url: "/a", priority: 0 });
    const b = q.load({ url: "/b", priority: 5 });
    const c = q.load({ url: "/c", priority: 3 });
    expect(q.load({ url: "/b", priority: 9 })).toBe(b);
    q.reprioritize((url) => (url === "/b" ? 1 : null));
    await tick();
    f.calls[0].respond(200, bytes(1));
    await a;
    await tick();
    expect(f.calls[1].url).toBe("/b");
    f.calls[1].respond(200, bytes(2));
    await tick();
    f.calls[2].respond(200, bytes(3));
    expect((await c).url).toBe("/c");
  });

  it("revalidates with If-None-Match: 304 keeps the data (retained bytes come back)", async () => {
    const f = fakeFetch();
    const q = new LoadQueue({ fetch: f.fetch });
    const first = q.load({ url: "/m", retain: true });
    await tick();
    expect(f.calls[0].init.headers["If-None-Match"]).toBeUndefined();
    f.calls[0].respond(200, bytes(1, 2, 3), '"abc"');
    const r1 = await first;
    expect(r1).toMatchObject({ status: 200, etag: '"abc"', changed: true });
    const second = q.load({ url: "/m", retain: true });
    await tick();
    expect(f.calls[1].init.headers["If-None-Match"]).toBe('"abc"');
    f.calls[1].respond(304);
    const r2 = await second;
    expect(r2.status).toBe(304);
    expect(r2.changed).toBe(false);
    expect(r2.buffer).toBe(r1.buffer);
  });

  it("without kept bytes a 304 gives null; forget and revalidate: false load in full", async () => {
    const f = fakeFetch();
    const q = new LoadQueue({ fetch: f.fetch });
    const p1 = q.load({ url: "/part/1" });
    await tick();
    f.calls[0].respond(200, bytes(9), "e1");
    await p1;
    const p2 = q.load({ url: "/part/1" });
    await tick();
    expect(f.calls[1].init.headers["If-None-Match"]).toBe("e1");
    f.calls[1].respond(304);
    expect((await p2).buffer).toBeNull();
    const p3 = q.load({ url: "/part/1", revalidate: false });
    await tick();
    expect(f.calls[2].init.headers["If-None-Match"]).toBeUndefined();
    f.calls[2].respond(200, bytes(9), "e2");
    await p3;
    q.forget("/part/1");
    const p4 = q.load({ url: "/part/1" });
    await tick();
    expect(f.calls[3].init.headers["If-None-Match"]).toBeUndefined();
    f.calls[3].respond(200, bytes(9));
    expect((await p4).etag).toBeNull();
  });

  it("a kept buffer transferred away (detached) is not offered for a 304", async () => {
    const f = fakeFetch();
    const q = new LoadQueue({ fetch: f.fetch });
    const p1 = q.load({ url: "/m", retain: true });
    await tick();
    f.calls[0].respond(200, bytes(1, 2), "e");
    const r1 = await p1;
    expect(q.retainedBytes).toBe(2);
    structuredClone(r1.buffer, { transfer: [r1.buffer!] }); // as postMessage to a Worker
    const p2 = q.load({ url: "/m", retain: true });
    await tick();
    expect(f.calls[1].init.headers["If-None-Match"]).toBeUndefined();
    expect(q.retainedBytes).toBe(0);
    f.calls[1].respond(200, bytes(1, 2), "e");
    expect((await p2).buffer?.byteLength).toBe(2);
  });

  it("keeps at most retainBytes, least recently used dropped first", async () => {
    const f = fakeFetch();
    const q = new LoadQueue({ fetch: f.fetch, retainBytes: 5 });
    for (const url of ["/a", "/b"]) {
      const p = q.load({ url, retain: true });
      await tick();
      f.calls[f.calls.length - 1].respond(200, bytes(1, 2, 3), `e${url}`);
      await p;
    }
    expect(q.retainedBytes).toBe(3); // "/a" made room for "/b"
    const again = q.load({ url: "/a", retain: true });
    await tick();
    expect(f.calls[2].init.headers["If-None-Match"]).toBeUndefined();
    f.calls[2].respond(200, bytes(1, 2, 3), "e/a");
    await again;
    const b = q.load({ url: "/b", retain: true });
    await tick();
    expect(f.calls[3].init.headers["If-None-Match"]).toBeUndefined();
    f.calls[3].respond(200, bytes(1), "e/b");
    await b;
    expect(q.etag("/b")).toBe("e/b");
  });

  it("passes on the core's {code, message, hint}, an offline core and cancellation", async () => {
    const f = fakeFetch();
    const q = new LoadQueue({ fetch: f.fetch });
    const bad = q.load({ url: "/x" });
    await tick();
    f.calls[0].respond(404, JSON.stringify({ code: "set_not_found", message: "Набор не найден.", hint: "Обновите дерево." }));
    await expect(bad).rejects.toMatchObject({ code: "set_not_found", status: 404, hint: "Обновите дерево." });
    const html = q.load({ url: "/y" });
    await tick();
    f.calls[1].respond(500, "<html>");
    await expect(html).rejects.toMatchObject({ code: "http_error", status: 500 });
    const off = q.load({ url: "/z" });
    await tick();
    f.calls[2].fail();
    await expect(off).rejects.toMatchObject({ code: "core_offline" });

    const one = new LoadQueue({ fetch: f.fetch, concurrency: 1 });
    const running = one.load({ url: "/r" });
    const queued = one.load({ url: "/q" });
    await tick();
    one.cancel("/q");
    one.cancel("/r");
    await expect(queued).rejects.toBeInstanceOf(VizError);
    await expect(running).rejects.toMatchObject({ code: "cancelled" });
    expect(one.active + one.queued).toBe(0);
  });
});
