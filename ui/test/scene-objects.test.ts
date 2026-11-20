/** The lists of line layers, rasters and wireframes of the open project and their keys of the view settings. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { setCoreTarget } from "../src/shell/api";
import { projectStore, type ProjectInfo } from "../src/modules/projects/state";
import { effectiveHidden } from "../src/modules/scene/hideAll";
import { lineLayers, loadSceneObjects, objectViewKeys, sceneObjects, type LineFileInfo, type RasterInfo, type WireframeInfo } from "../src/modules/scene/objects";

const layer = (id: number, file: number) => ({ id, file_id: file, name: `Слой ${id}`, version: 1, color: null, points_only: false }) as LineFileInfo["layers"][number];
const files = [
  { id: 1, name: "a.str", layers: [layer(11, 1), layer(12, 1)] },
  { id: 2, name: "b.dxf", layers: [layer(21, 2)] },
] as LineFileInfo[];
const rasters = [{ id: 31, name: "Ортофото", version: 1 }] as RasterInfo[];
const dtm = { raster_id: 31, clip: true, tint: "#ffffff", opacity: 1, shade: true };
const wireframes = [
  { id: 41, name: "Рельеф", version: 2, color: null, extent: [0, 0, 250, 10, 10, 300], dtm },
  { id: 42, name: "Рудное тело", version: 1, color: "#f46a1a", extent: [0, 0, 100, 10, 10, 200], dtm: null },
] as WireframeInfo[];

function answer(lines: unknown, images: unknown, meshes: unknown = []) {
  return vi.fn(async (url: RequestInfo | URL) => {
    const u = String(url);
    return new Response(JSON.stringify(u.endsWith("/lines/files") ? { files: lines } : u.endsWith("/wireframes") ? { wireframes: meshes } : { rasters: images }), { status: 200 });
  });
}

describe("objects of the scene", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setCoreTarget(null);
    projectStore.set({ current: null });
    sceneObjects.set({ files: [], rasters: [], wireframes: [], loaded: false, error: null });
  });

  it("layers of every file and rasters give the keys `ln:` and `rs:`; «Очистить окно» hides them too", () => {
    expect(lineLayers({ files }).map((l) => l.id)).toEqual([11, 12, 21]);
    const keys = objectViewKeys({ files, rasters });
    expect(keys).toEqual(["ln:11", "ln:12", "ln:21", "rs:31"]);
    expect(effectiveHidden([], { on: true, shown: ["ln:12"] }, keys).sort()).toEqual(["ln:11", "ln:21", "rs:31"]);
    // wireframes and DTMs: `wf:`
    expect(objectViewKeys({ files, rasters, wireframes })).toEqual(["ln:11", "ln:12", "ln:21", "rs:31", "wf:41", "wf:42"]);
  });

  it("wireframes come with the settings of a DTM; a changed setting gives a new list, the others keep theirs", async () => {
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    projectStore.set({ current: { uid: "p1" } as ProjectInfo });
    vi.stubGlobal("fetch", answer(files, rasters, wireframes));
    await loadSceneObjects();
    const first = sceneObjects.get();
    expect(first.wireframes.map((w) => [w.id, w.dtm?.raster_id ?? null])).toEqual([[41, 31], [42, null]]);
    expect(objectViewKeys()).toEqual(["ln:11", "ln:12", "ln:21", "rs:31", "wf:41", "wf:42"]);
    await loadSceneObjects();
    expect(sceneObjects.get().wireframes).toBe(first.wireframes);
    // «Вернуть в каркасы»: the settings and the raster are gone
    vi.stubGlobal("fetch", answer(files, rasters, [{ ...wireframes[0], version: 3, dtm: null }, wireframes[1]]));
    await loadSceneObjects();
    expect(sceneObjects.get().wireframes).not.toBe(first.wireframes);
    expect(sceneObjects.get().wireframes[0].dtm).toBeNull();
    expect(sceneObjects.get().files).toBe(first.files);
    expect(sceneObjects.get().rasters).toBe(first.rasters);
  });

  it("a read overtaken by a newer one waits for it: the caller gets the fresh lists (a new layer opened at once)", async () => {
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    projectStore.set({ current: { uid: "p1" } as ProjectInfo });
    let call = 0;
    let release: () => void = () => undefined;
    const slow = new Promise<void>((r) => (release = r));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: RequestInfo | URL) => {
        const u = String(url);
        // the first read of the lists answers late with the old ones; the second, started after it, has the new layer
        const n = u.endsWith("/lines/files") ? ++call : call;
        if (n === 2 && u.endsWith("/lines/files")) await slow;
        const lines = n === 1 ? [files[1]] : files;
        return new Response(JSON.stringify(u.endsWith("/lines/files") ? { files: lines } : u.endsWith("/wireframes") ? { wireframes: [] } : { rasters: [] }), { status: 200 });
      }),
    );
    const mine = loadSceneObjects(); // the caller's read (old lists)
    const other = loadSceneObjects(); // started by an event — overtakes it, answers later
    let done = false;
    void mine.then(() => (done = true));
    await new Promise((r) => setTimeout(r, 20));
    expect(done).toBe(false); // not before the newer read
    release();
    await Promise.all([mine, other]);
    expect(lineLayers().map((l) => l.id)).toEqual([11, 12, 21]);
  });

  it("the lists are read for the open project; unchanged lists keep their identity", async () => {
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    projectStore.set({ current: { uid: "p1" } as ProjectInfo });
    vi.stubGlobal("fetch", answer(files, rasters));
    await loadSceneObjects();
    const first = sceneObjects.get();
    expect(first.loaded).toBe(true);
    expect(objectViewKeys()).toEqual(["ln:11", "ln:12", "ln:21", "rs:31"]);
    await loadSceneObjects();
    expect(sceneObjects.get().files).toBe(first.files);
    expect(sceneObjects.get().rasters).toBe(first.rasters);
    // a layer recoloured in the project: a new list of files, the same list of rasters
    vi.stubGlobal("fetch", answer([{ ...files[0], layers: [{ ...files[0].layers[0], color: "#ff0000", version: 2 }] }], rasters));
    await loadSceneObjects();
    expect(sceneObjects.get().files).not.toBe(first.files);
    expect(sceneObjects.get().rasters).toBe(first.rasters);
    expect(lineLayers()[0].color).toBe("#ff0000");
  });

  it("without a project the lists are empty; a failed read keeps the lists and tells the reason", async () => {
    await loadSceneObjects();
    expect(sceneObjects.get()).toMatchObject({ files: [], rasters: [], loaded: false });
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    projectStore.set({ current: { uid: "p1" } as ProjectInfo });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ code: "no_project", message: "Проект не открыт.", hint: "Откройте проект." }), { status: 409 })));
    await loadSceneObjects();
    expect(sceneObjects.get().error).toBe("Проект не открыт. Откройте проект.");
  });
});
