/**
 * Line layers, rasters, wireframes and DTMs in the scene of the open project (ADR 0011 §4): the three sources
 * tied to the lists of the project (`objects.ts`), the hidden keys in force and the floor — the level of what
 * has no height of its own (a raster without an elevation, lines without Z): a metre under the lowest hole,
 * line or surface. A DTM that carries a raster takes the texture of the raster's layer, and the flat raster
 * is hidden or cut out under it. A click that hit no hole selects the line, the point, the surface or the
 * raster under the cursor: it is kept in `pickedObject`, marked in orange, and passed to `object.select`
 * once that command has a handler. A lazy chunk with three.js, loaded with the scene.
 */
import type { App } from "../../shell/app";
import type { LayerHit, Scene } from "../../viz/Scene";
import type { LinePick } from "../../viz/lines/layer";
import type { MeshPick, MeshProbe } from "../../viz/mesh/layer";
import type { RasterPick } from "../../viz/rasters/layer";
import { floorLevel } from "../../viz/rasters/placement";
import type { HolesSource } from "./loaders";
import { LinesSource } from "./linesSource";
import { lineLayers, pickedObject, sceneObjects, type PickedObject } from "./objects";
import { RastersSource } from "./rastersSource";
import { NO_OBJECTS, sceneUi } from "./sceneHost";
import { WireframesSource } from "./wireframesSource";

export interface ObjectSources {
  lines: LinesSource;
  rasters: RastersSource;
  wireframes: WireframesSource;
  /** nothing of lines, rasters and surfaces is loading or waiting */
  readonly idle: boolean;
  /** the answer of the scene to a click that hit no hole */
  pick(hit: LayerHit | null): void;
  /** for the E2E hook `window.__bfScene.state()`; `wireframes.layers` — surfaces in the scene */
  state(): { lines: ReturnType<LinesSource["stats"]>; rasters: ReturnType<RastersSource["stats"]>; wireframes: ReturnType<WireframesSource["stats"]>; picked: PickedObject | null; floor: number };
  /** E2E: the surface of a wireframe over a plan point as drawn now, and the level every part is drawn at */
  probe(id: number, x: number, y: number): MeshProbe | null;
  forceLevel(level: number | null): void;
  dispose(): void;
}

export function connectObjects(app: App, scene: Scene, holes: HolesSource): ObjectSources {
  const hidden = () => app.store.get().view.hidden;
  let floor = 0;
  const lines: LinesSource = new LinesSource(scene, { layers: () => lineLayers(), hidden, floor: () => floor, onContent: () => refloor() });
  const rasters: RastersSource = new RastersSource(scene, { rasters: () => sceneObjects.get().rasters, hidden, floor: () => floor, drapes: () => wireframes.drapes() });
  // after the rasters: before a frame the raster picks its texture, then the DTM that carries it takes it
  const wireframes: WireframesSource = new WireframesSource(scene, {
    wireframes: () => sceneObjects.get().wireframes,
    rasters: () => sceneObjects.get().rasters,
    hidden,
    opacity: () => app.store.get().view.wireframeOpacity,
    texture: (id) => rasters.textureOf(id),
    onDrapes: () => rasters.sync(),
  });

  /** Under the lowest hole, line or surface with heights; hidden ones count only when nothing is visible. */
  function refloor(): void {
    const h = new Set(hidden());
    const boxes = holes.manifestList().flatMap((m) => m.parts.map((p) => ({ zmin: p.bbox[2], visible: !h.has(`set:${m.setId}`) })));
    // surfaces count by the extent of the list: the floor does not wait for their geometry
    const surfaces = sceneObjects.get().wireframes.flatMap((w) => (typeof w.extent?.[2] === "number" ? [{ zmin: w.extent[2], visible: !h.has(`wf:${w.id}`) }] : []));
    const z = floorLevel([...boxes, ...lines.extents(), ...surfaces]);
    if (z === floor) return;
    floor = z;
    lines.refloor();
    rasters.sync();
  }

  /** The picked line is drawn over in orange, the picked surface glows. */
  const mark = (picked: PickedObject | null) => {
    lines.select(picked?.kind === "lines" ? { layerId: picked.id, line: picked.line } : null);
    wireframes.select(picked?.kind === "wireframe" ? picked.id : null);
  };
  const select = (picked: PickedObject | null) => {
    if (picked === null && pickedObject.get().picked === null) return;
    pickedObject.set({ picked });
    mark(picked);
    // the command gets its handler with the properties panel (ADR 0011 §5); until then the store is the result
    if (picked && app.registry.reason("object.select") === null) void app.run("object.select", { kind: picked.kind, id: picked.id });
  };

  const off: (() => void)[] = [];
  let lists = sceneObjects.get();
  off.push(
    sceneObjects.subscribe(() => {
      const next = sceneObjects.get();
      if (next.files === lists.files && next.rasters === lists.rasters && next.wireframes === lists.wireframes) return;
      const surfaces = next.wireframes !== lists.wireframes;
      lists = next;
      lines.sync();
      wireframes.sync();
      rasters.sync();
      if (surfaces) refloor();
      // the picked object is gone with its layer, raster or wireframe
      const p = pickedObject.get().picked;
      const there = p === null || (p.kind === "lines" ? lineLayers(next).some((l) => l.id === p.id) : (p.kind === "raster" ? next.rasters : next.wireframes).some((o) => o.id === p.id));
      if (!there) select(null);
    }),
  );
  // the pick was dropped from outside («Снять выбор»): the marks go too
  off.push(
    pickedObject.subscribe(() => {
      if (pickedObject.get().picked === null) mark(null);
    }),
  );
  let view = app.store.get().view;
  off.push(
    app.store.subscribe(() => {
      const v = app.store.get().view;
      const shown = v.hidden !== view.hidden;
      const opacity = v.wireframeOpacity !== view.wireframeOpacity;
      view = v;
      if (!shown) {
        if (opacity) wireframes.sync();
        return;
      }
      lines.sync();
      wireframes.sync();
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
      wireframes.restore();
    }),
  );
  sceneUi.set(NO_OBJECTS);
  refloor();
  lines.sync();
  wireframes.sync();
  rasters.sync();
  mark(pickedObject.get().picked);

  return {
    lines,
    rasters,
    wireframes,
    get idle() {
      const o = sceneObjects.get();
      return lines.idle && rasters.idle && wireframes.idle && (o.loaded || o.error !== null);
    },
    pick(hit) {
      if (!hit) return select(null);
      if (hit.key.startsWith("ln:")) {
        const h = hit.hit as LinePick;
        select({ kind: "lines", id: h.layerId, line: h.line, on: h.on, xyz: h.xyz });
      } else if (hit.key.startsWith("rs:")) {
        const h = hit.hit as RasterPick;
        select({ kind: "raster", id: h.rasterId, xyz: h.xyz });
      } else if (hit.key.startsWith("wf:")) {
        const h = hit.hit as MeshPick;
        select({ kind: "wireframe", id: h.wireframeId, xyz: h.xyz });
      } else select(null);
    },
    state: () => ({ lines: lines.stats(), rasters: rasters.stats(), wireframes: wireframes.stats(), picked: pickedObject.get().picked, floor }),
    probe: (id, x, y) => wireframes.probe(id, x, y),
    forceLevel: (level) => wireframes.forceLevel(level),
    dispose() {
      off.forEach((fn) => fn());
      lines.dispose();
      rasters.dispose();
      wireframes.dispose();
    },
  };
}
