import { afterEach, describe, expect, it, vi } from "vitest";
import { uploadResumable, type Dataset } from "./api";
import { folderTree } from "./shell/DataTree";
import { bounds, load } from "./viz/geometryLoader";

const ds = (id: number, folder: string): Dataset =>
  ({ id, kind: "wireframes", name: `d${id}`, folder, created_at: "", meta: {}, counts: null, components: null, audit: null, wireframes: [], lines: null, rasters: null });

describe("folders of the tree", () => {
  it("nests by path and keeps top-level objects at the root", () => {
    const root = folderTree([ds(1, ""), ds(2, "Карьер"), ds(3, "Карьер/2026"), ds(4, "Карьер/2026")]);
    expect(root.items.map((d) => d.id)).toEqual([1]);
    const pit = root.folders.get("Карьер")!;
    expect(pit.items.map((d) => d.id)).toEqual([2]);
    expect(pit.folders.get("2026")!.path).toBe("Карьер/2026");
    expect(pit.folders.get("2026")!.items.map((d) => d.id)).toEqual([3, 4]);
  });
});

describe("geometry buffers", () => {
  it("measures float32 x, y, z bounds", () => {
    expect(bounds(new Float32Array([1, 2, 3, -1, 5, 0, 4, -2, 9]))).toEqual({ min: [-1, -2, 0], max: [4, 5, 9] });
    expect(bounds(new Float32Array([]))).toBeNull();
  });

  it("loads raw buffers without decoding", async () => {
    const data = new Float32Array([0, 1, 2, 3, 4, 5]);
    vi.stubGlobal("fetch", vi.fn(async () => new Response(data.buffer.slice(0))));
    const r = await load({ id: 1, urls: ["/api/blobs/a"], boundsOf: 0 });
    expect(Array.from(new Float32Array(r.buffers![0]))).toEqual([0, 1, 2, 3, 4, 5]);
    expect(r.bounds).toEqual({ min: [0, 1, 2], max: [3, 4, 5] });
  });
});

describe("resumable upload", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends chunks with checksums, follows the server's position and retries server errors", async () => {
    const file = new File([new Uint8Array(25).map((_, i) => i)], "pit.tridb");
    const received: number[] = [];
    let position = 0;
    let failedOnce = false;
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return Response.json({ id: "u1", project_id: 7, filename: "pit.tridb", size: 25, received: 0, status: "receiving", chunk_size: 10 }, { status: 201 });
      }
      const offset = Number(new URL(url, "http://x").searchParams.get("offset"));
      const body = new Uint8Array(init!.body as ArrayBuffer);
      expect((init!.headers as Record<string, string>)["X-Chunk-SHA256"]).toMatch(/^[0-9a-f]{64}$/);
      if (offset === 10 && !failedOnce) {
        failedOnce = true;
        position = 20; // the chunk did arrive although the answer was lost
        return new Response("", { status: 502 });
      }
      if (offset !== position) return Response.json({ detail: { message: "не с того места", received: position } }, { status: 409 });
      received.push(offset);
      position = offset + body.length;
      return Response.json({ id: "u1", project_id: 7, filename: "pit.tridb", size: 25, received: position, status: position === 25 ? "complete" : "receiving", chunk_size: 10 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const progress: number[] = [];
    const info = await uploadResumable(7, file, (sent) => progress.push(sent));
    expect(info.status).toBe("complete");
    expect(received).toEqual([0, 20]); // 10..20 was not sent twice
    expect(progress.at(-1)).toBe(25);
  }, 10000);
});

describe("import wizard", async () => {
  const { planJobs, sortFiles } = await import("./shell/ImportWizard");
  const row = (upload: string, filename: string, kind: string, chosen: string | null, sidecars?: string[]) =>
    ({ upload, filename, size: 1, status: "complete", kind, format: "", note: "", chosen, sidecars }) as never;

  it("sends object files up and leaves tables to the drillhole wizard", () => {
    const f = (n: string) => new File(["x"], n);
    const r = sortFiles([f("Pit.TRIDB"), f("holes.dat"), f("map.jpg"), f("map.jgw"), f("notes.docx")]);
    expect(r.objects.map((x) => x.name)).toEqual(["Pit.TRIDB", "map.jpg", "map.jgw"]);
    expect(r.tables.map((x) => x.name)).toEqual(["holes.dat"]);
    expect(r.unknown.map((x) => x.name)).toEqual(["notes.docx"]);
  });

  it("plans one job per wireframe or line file and one for images with their georeference", () => {
    const plan = planJobs([
      row("a", "pit.tridb", "wireframes", "wireframes"),
      row("b", "faults.dxf", "wireframes", "lines"),
      row("c", "skip.dxf", "lines", null),
      row("d", "map.jpg", "rasters", "rasters", ["map.jgw"]),
      row("e", "map.jgw", "sidecar", "rasters"),
      row("f", "stray.tab", "sidecar", "rasters"),
    ]);
    expect(plan).toEqual([
      { kind: "rasters", uploads: ["d", "e"], names: ["map.jpg", "map.jgw"] },
      { kind: "wireframes", uploads: ["a"], names: ["pit.tridb"] },
      { kind: "lines", uploads: ["b"], names: ["faults.dxf"] },
    ]);
    expect(planJobs([row("e", "map.jgw", "sidecar", "rasters")])).toEqual([]);
  });
});
