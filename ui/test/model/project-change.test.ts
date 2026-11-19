/** Э9, подшаг 9.38 (ревью интерфейса, finding 2): «Править треугольники» and «Собрать из контуров» do not outlive
 * the project they were started in. */
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Vec3 } from "../../src/edit/model";
import { createApp } from "../../src/shell/app";
import { editStore } from "../../src/modules/lines/editState";
import { assembleController, beginAssembly } from "../../src/modules/model/assemble";
import { cancelTriangles, startTriangles, trianglesController, trianglesStore } from "../../src/modules/model/triangles";
import { projectStore, type ProjectInfo } from "../../src/modules/projects/state";
import { sceneObjects } from "../../src/modules/scene/objects";
import type { WireframeInfo } from "../../src/modules/wireframes/state";
import type { Scene } from "../../src/viz/Scene";
import { closeApp, openApp } from "../linesHarness";

const wf: WireframeInfo = { id: 5, name: "Тело", version: 1, color: null, point_count: 4, triangle_count: 2, extent: [0, 0, 0, 10, 10, 5], dtm: null, folder: null, attrs: [], stats: { closed: false, open_edges: 4 } };

afterEach(() => {
  cancelTriangles();
  editStore.set({ session: null });
  sceneObjects.set({ files: [], rasters: [], wireframes: [], loaded: false, error: null });
  projectStore.set({ current: null });
});

describe("another project ends the tools of the 3D window", () => {
  it("«Править треугольники»: the fix stops, the tool leaves the view; a late pick changes nothing", async () => {
    projectStore.set({ current: { uid: "p1" } as ProjectInfo });
    sceneObjects.set({ wireframes: [wf], loaded: true });
    const app = createApp();
    const scene = { tool: null as unknown, setTool: vi.fn((t: unknown) => void (scene.tool = t)), cursorRay: () => ({ origin: [5, 5, 100] as Vec3, dir: [0, 0, -1] as Vec3 }), projectView: () => null };
    const waiting: ((v: unknown) => void)[] = [];
    const shown: unknown[] = [];
    startTriangles(app, 5, { scene: () => scene as unknown as Scene, pick: () => new Promise((r) => waiting.push(r)), show: (_s, d) => shown.push(d) });
    const c = trianglesController()!;
    void c.click(10, 10);
    // the same project set again: nothing ends
    projectStore.set({ current: { uid: "p1" } as ProjectInfo });
    expect(trianglesController()).toBe(c);
    projectStore.set({ current: { uid: "p2" } as ProjectInfo });
    expect(trianglesController()).toBeNull();
    expect(scene.tool).toBeNull();
    expect(shown.at(-1)).toBeNull();
    expect(trianglesStore.get()).toMatchObject({ edit: null, message: expect.stringMatching(/открыт другой проект/) });
    waiting[0]({ hit: { wireframe_id: 5, name: "Тело", triangle: 0, distance: 1, point: [5, 5, 0], vertices: [[0, 0, 0], [10, 0, 0], [0, 10, 0]], normal: [0, 0, 1], area: 50, dip: 0, dip_direction: 0 } });
    await new Promise((r) => setTimeout(r, 0));
    expect(trianglesStore.get().edit).toBeNull();
  });

  it("«Собрать из контуров»: the assembly goes with the session of the editor", async () => {
    const { app } = await openApp();
    try {
      expect((await app.registry.execute("lines.edit", { layer_id: 7 })).ok).toBe(true);
      const s = editStore.get().session!;
      beginAssembly(s, { fetch: () => new Promise(() => {}), show: () => {} });
      expect(assembleController()).not.toBeNull();
      projectStore.set({ current: { uid: "p2" } as ProjectInfo });
      expect(editStore.get().session).toBeNull();
      expect(assembleController()).toBeNull();
    } finally {
      closeApp();
    }
  });
});
