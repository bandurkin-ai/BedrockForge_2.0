/** The lists of line layers and rasters of the open project and their keys of the view settings. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { setCoreTarget } from "../src/shell/api";
import { projectStore, type ProjectInfo } from "../src/modules/projects/state";
import { effectiveHidden } from "../src/modules/scene/hideAll";
import { lineLayers, loadSceneObjects, objectViewKeys, sceneObjects, type LineFileInfo, type RasterInfo } from "../src/modules/scene/objects";

const layer = (id: number, file: number) => ({ id, file_id: file, name: `Слой ${id}`, version: 1, color: null, points_only: false }) as LineFileInfo["layers"][number];
const files = [
  { id: 1, name: "a.str", layers: [layer(11, 1), layer(12, 1)] },
  { id: 2, name: "b.dxf", layers: [layer(21, 2)] },
] as LineFileInfo[];
const rasters = [{ id: 31, name: "Ортофото", version: 1 }] as RasterInfo[];

function answer(lines: unknown, images: unknown) {
  return vi.fn(async (url: RequestInfo | URL) => new Response(JSON.stringify(String(url).endsWith("/lines/files") ? { files: lines } : { rasters: images }), { status: 200 }));
}

describe("objects of the scene", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setCoreTarget(null);
    projectStore.set({ current: null });
    sceneObjects.set({ files: [], rasters: [], loaded: false, error: null });
  });

  it("layers of every file and rasters give the keys `ln:` and `rs:`; «Очистить окно» hides them too", () => {
    expect(lineLayers({ files }).map((l) => l.id)).toEqual([11, 12, 21]);
    const keys = objectViewKeys({ files, rasters });
    expect(keys).toEqual(["ln:11", "ln:12", "ln:21", "rs:31"]);
    expect(effectiveHidden([], { on: true, shown: ["ln:12"] }, keys).sort()).toEqual(["ln:11", "ln:21", "rs:31"]);
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
