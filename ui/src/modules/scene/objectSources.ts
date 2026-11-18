/**
 * Line layers and rasters in the scene of the open project (ADR 0011 §4): the two sources tied to the lists
 * of the project (`objects.ts`), the hidden keys in force and the floor — the level of what has no height of
 * its own (a raster without an elevation, lines without Z): a metre under the lowest hole or line. A click
 * that hit no hole selects the line, the point or the raster under the cursor: it is kept in `pickedObject`,
 * drawn over in orange, and passed to `object.select` once that command has a handler.
 * A lazy chunk with three.js, loaded with the scene.
 */
import type { App } from "../../shell/app";
import type { LayerHit, Scene } from "../../viz/Scene";
import type { LinePick } from "../../viz/lines/layer";
import type { RasterPick } from "../../viz/rasters/layer";
import { floorLevel } from "../../viz/rasters/placement";
import type { HolesSource } from "./loaders";
import { LinesSource } from "./linesSource";
import { lineLayers, pickedObject, sceneObjects, type PickedObject } from "./objects";
import { RastersSource } from "./rastersSource";
import { NO_OBJECTS, sceneUi } from "./sceneHost";

export interface ObjectSources {
  lines: LinesSource;
  rasters: RastersSource;
  /** nothing of lines and rasters is loading or waiting */
  readonly idle: boolean;
  /** the answer of the scene to a click that hit no hole */
  pick(hit: LayerHit | null): void;
  /** for the E2E hook `window.__bfScene.state()` */
  state(): { lines: ReturnType<LinesSource["stats"]>; rasters: ReturnType<RastersSource["stats"]>; picked: PickedObject | null; floor: number };
  dispose(): void;
}

export function connectObjects(app: App, scene: Scene, holes: HolesSource): ObjectSources {
  const hidden = () => app.store.get().view.hidden;
  let floor = 0;
  const lines: LinesSource = new LinesSource(scene, { layers: () => lineLayers(), hidden, floor: () => floor, onContent: () => refloor() });
  const rasters = new RastersSource(scene, { rasters: () => sceneObjects.get().rasters, hidden, floor: () => floor });

  /** Under the lowest hole or line with heights; hidden ones count only when nothing is visible. */
  function refloor(): void {
    const h = new Set(hidden());
    const boxes = holes.manifestList().flatMap((m) => m.parts.map((p) => ({ zmin: p.bbox[2], visible: !h.has(`set:${m.setId}`) })));
    const z = floorLevel([...boxes, ...lines.extents()]);
    if (z === floor) return;
    floor = z;
    lines.refloor();
    rasters.sync();
  }

  const select = (picked: PickedObject | null) => {
    if (picked === null && pickedObject.get().picked === null) return;
    pickedObject.set({ picked });
    lines.select(picked?.kind === "lines" ? { layerId: picked.id, line: picked.line } : null);
    // the command gets its handler with the properties panel (ADR 0011 §5); until then the store is the result
    if (picked && app.registry.reason("object.select") === null) void app.run("object.select", { kind: picked.kind, id: picked.id });
  };

  const off: (() => void)[] = [];
  let lists = sceneObjects.get();
  off.push(
    sceneObjects.subscribe(() => {
      const next = sceneObjects.get();
      if (next.files === lists.files && next.rasters === lists.rasters) return;
      lists = next;
      lines.sync();
      rasters.sync();
      // the picked object is gone with its layer or raster
      const p = pickedObject.get().picked;
      if (p && !(p.kind === "lines" ? lineLayers(next).some((l) => l.id === p.id) : next.rasters.some((r) => r.id === p.id))) select(null);
    }),
  );
  let view = app.store.get().view;
  off.push(
    app.store.subscribe(() => {
      const v = app.store.get().view;
      if (v.hidden === view.hidden) {
        view = v;
        return;
      }
      view = v;
      lines.sync();
      rasters.sync();
      refloor();
    }),
  );
  // the holes in the scene changed (a set loaded, removed): the floor follows
  let content = sceneUi.get().content;
  off.push(
    sceneUi.subscribe(() => {
      const c = sceneUi.get().content;
      if (c === content) return;
      content = c;
      refloor();
    }),
  );
  off.push(
    scene.onRestored(() => {
      lines.restore();
      rasters.restore();
    }),
  );
  sceneUi.set(NO_OBJECTS);
  refloor();
  lines.sync();
  rasters.sync();
  const before = pickedObject.get().picked;
  if (before?.kind === "lines") lines.select({ layerId: before.id, line: before.line });

  return {
    lines,
    rasters,
    get idle() {
      const o = sceneObjects.get();
      return lines.idle && rasters.idle && (o.loaded || o.error !== null);
    },
    pick(hit) {
      if (!hit) return select(null);
      if (hit.key.startsWith("ln:")) {
        const h = hit.hit as LinePick;
        select({ kind: "lines", id: h.layerId, line: h.line, on: h.on, xyz: h.xyz });
      } else if (hit.key.startsWith("rs:")) {
        const h = hit.hit as RasterPick;
        select({ kind: "raster", id: h.rasterId, xyz: h.xyz });
      } else select(null);
    },
    state: () => ({ lines: lines.stats(), rasters: rasters.stats(), picked: pickedObject.get().picked, floor }),
    dispose() {
      off.forEach((fn) => fn());
      lines.dispose();
      rasters.dispose();
    },
  };
}
