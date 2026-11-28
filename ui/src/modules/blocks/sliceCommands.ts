/**
 * The handler of `blocks.slice` (ТЗ 8.7.6, 8.7.7 «Срез на разрезе»; ADR 0023 §11, §13, decision 19; e17-api §14, §16): the
 * slice of a block model by a plane — switched on or off, the plane «по разрезу» (the section of the view, a series
 * included: the slice is the backdrop under the holes), an axis of the project at an elevation, a point and a normal,
 * and the move of the plane along its normal by metres or by cells (the buttons «Срез выше» / «Срез ниже»). The
 * declaration is `commands.ts`. The handler changes `block_styles[id].slice` in the view of the project (saved with
 * the project, restored when it opens); the layer (`sliceScene.ts`, a lazy chunk) follows the view. The colouring is
 * the one of `blocks.style`; the filter and the restrictor — of the model (`blocks.filter`, `blocks.quick`). A wrong
 * value is refused in Russian with its hint before anything is written.
 */
import type { App } from "../../shell/app";
import { needProject, noted, refusal } from "../../shell/objectRun";
import { projectStore } from "../projects/state";
import { updateView, viewStore } from "../scene/bind";
import { onScene } from "../scene/sceneHost";
import { BLOCK_STYLES_KEY, numberOf, styleIn, styleOp, type SlicePlane } from "./display";
import { modelOf } from "./showCommands";
import { AXES, axisOf, axisPlane, cellStep, movePlane, planeOf, planeText, resetSlice, sectionPlaneBody, sliceStatus, sliceStore, type Axis, type PlaneBody } from "./sliceState";
import { blocksStore } from "./state";

const no = refusal;
const given = (a: Record<string, unknown>, k: string): boolean => a[k] !== undefined && a[k] !== null && a[k] !== "";

function num(a: Record<string, unknown>, k: string, what: string): number {
  const n = numberOf(a[k]);
  if (n === null) throw no("bad_param", `${what}: нужно число.`, "Например 262,5.");
  return n;
}

/** The plane the call asks for (`plane` = section | x | y | z | point), or null when it does not say. */
function planeFromCall(a: Record<string, unknown>, cur: SlicePlane, model: Parameters<typeof axisPlane>[2]): SlicePlane | null {
  if (!given(a, "plane")) return null;
  const kind = String(a.plane);
  if (kind === "section") return "section";
  if ((AXES as readonly string[]).includes(kind)) {
    const axis = kind as Axis;
    const keep = cur !== "section" ? axisOf(cur) : null;
    const at = given(a, "at") ? num(a, "at", "Отметка плоскости") : keep && keep.axis === axis ? keep.at : null;
    return axisPlane(axis, at, model);
  }
  if (kind === "point") {
    const origin = (["x", "y", "z"] as const).map((k) => num(a, k, `Координата ${k.toUpperCase()} точки плоскости`));
    const normal = (["nx", "ny", "nz"] as const).map((k) => num(a, k, `Составляющая ${k} нормали плоскости`));
    if (!(Math.hypot(...normal) > 0)) throw no("bad_param", "Нормаль плоскости нулевая.", "Задайте направление нормали, например nz=1 для горизонтального среза.");
    return { origin: origin as PlaneBody["origin"], normal: normal as PlaneBody["normal"] };
  }
  throw no("bad_param", `Плоскость «${kind}» не известна.`, "Допустимы: section (по разрезу), x, y, z (по оси), point (точка и нормаль).");
}

export async function runSlice(app: App, a: Record<string, unknown>): Promise<string> {
  const model = await modelOf(app, a);
  const keys = viewStore.get();
  const cur = styleIn(keys, model.id).slice;
  const on = a.on !== false;
  if (on && model.layout === "list") throw no("block_list_unsupported", `Срез недоступен для модели-списка блоков «${model.name}».`, "Регуляризуйте модель: Блочные модели → Регуляризация.");
  let plane: SlicePlane = planeFromCall(a, cur.plane, model) ?? cur.plane;
  if (!given(a, "plane") && given(a, "at")) {
    const keep = plane !== "section" ? axisOf(plane) : null;
    if (!keep) throw no("bad_param", "Отметка задаётся только для среза по оси.", "Укажите plane=x, y или z вместе с at.");
    plane = axisPlane(keep.axis, num(a, "at", "Отметка плоскости"), model);
  }
  // the first slice with no section drawn: a horizontal plane in the middle of the model, not a plane that is not there
  if (on && !cur.on && plane === "section" && !given(a, "plane") && !sectionPlaneBody(keys)) plane = axisPlane("z", null, model);
  if (given(a, "shift") || given(a, "cells")) {
    if (plane === "section") throw no("slice_section_plane", "Плоскость «по разрезу» идёт вместе с разрезом.", "Листайте разрезы серии или выберите срез по оси (plane=z), чтобы двигать плоскость.");
    const d = (given(a, "shift") ? num(a, "shift", "Сдвиг плоскости") : 0) + (given(a, "cells") ? num(a, "cells", "Сдвиг плоскости в ячейках") * cellStep(model, plane) : 0);
    plane = movePlane(plane, d);
  }
  updateView(BLOCK_STYLES_KEY, styleOp(model.id, { slice: { on, plane } }));
  if (!on) return `Срез модели «${model.name}» выключен`;
  const now = planeOf(plane, viewStore.get());
  return now ? `Срез модели «${model.name}»: ${plane === "section" ? "по разрезу" : planeText(now)}` : `Срез модели «${model.name}» включён по разрезу; разреза пока нет — нарисуйте линию разреза или выберите плоскость по оси (plane=z)`;
}

/** Binds `blocks.slice`, the status notes and the layers of the scene; gives what unbinds them. */
export function bindSlice(app: App): () => void {
  app.registry.bind("blocks.slice", noted(app, (a) => runSlice(app, a)), needProject);
  const stops: (() => void)[] = [];
  let uid: string | null = projectStore.get().current?.uid ?? null;
  stops.push(
    projectStore.subscribe(() => {
      const cur = projectStore.get().current?.uid ?? null;
      if (cur === uid) return;
      uid = cur;
      resetSlice();
    }),
  );
  // a failed or impossible slice is said once in the status bar
  const said = new Map<number, string>();
  stops.push(
    sliceStore.subscribe(() => {
      for (const info of Object.values(sliceStore.get().models)) {
        const bad = info.status === "error" ? info.error : info.status === "noplane" ? "нет разреза" : null;
        const was = said.get(info.model);
        if (bad && was !== bad) {
          said.set(info.model, bad);
          app.store.set({ statusNote: sliceStatus(blocksStore.get().list.filter((b) => b.id === info.model), { models: { [info.model]: info } }) ?? bad });
        } else if (!bad) said.delete(info.model);
      }
    }),
  );
  // the layers: a lazy chunk with three.js, attached to every new scene
  let detach: (() => void) | null = null;
  let generation = 0;
  stops.push(
    onScene((scene) => {
      const mine = ++generation;
      detach?.();
      detach = null;
      if (!scene) return;
      void import("./sliceScene").then((m) => {
        if (mine !== generation) return;
        detach = m.attachSliceScene(scene);
      });
    }),
  );
  return () => {
    generation++;
    detach?.();
    detach = null;
    stops.forEach((fn) => fn());
  };
}
