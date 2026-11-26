/**
 * «Указать на сцене» for the collar and the target of a planned hole (ТЗ 7.35 Д-34 ч. 2): a click of the 3D window puts the point of the ray under the
 * cursor on the horizontal plane at the height Z of the field (empty — the height of the view target) into the form. The tool of the scene
 * (`Scene.setTool`) takes the left click; a right click ends the pick without a point; a drag still turns the view. The ray is float64 in project
 * coordinates (`Scene.cursorRay`). Pure parts: `onPlane`, `heightOf`; the rest holds one tool at a time. No React.
 */
import { refusal } from "../../shell/objectRun";
import { Store } from "../../shell/store";
import type { Scene } from "../../viz/Scene";
import type { ToolPointer } from "../../viz/core/toolInput";
import { currentScene, onScene } from "../scene/sceneHost";
import { numberText, parseNumber } from "../composites/params";
import type { Triple } from "./plannedApi";
import { setXyz } from "./plannedState";

export type PickWhat = "collar" | "target";
export const PICK_TITLES: Record<PickWhat, string> = { collar: "устье", target: "цель" };

/** The point where a ray meets the plane `z = height`; null — the ray is (almost) parallel to it or the plane is behind the cursor. */
export function onPlane(ray: { origin: readonly number[]; dir: readonly number[] }, height: number): Triple | null {
  const dz = ray.dir[2];
  if (Math.abs(dz) < 1e-9) return null;
  const t = (height - ray.origin[2]) / dz;
  if (!(t >= 0) || !Number.isFinite(t)) return null;
  return [ray.origin[0] + ray.dir[0] * t, ray.origin[1] + ray.dir[1] * t, height];
}

/** The height of the plane: the Z typed in the field, else the height of the view target, else 0. */
export function heightOf(zText: string, targetZ: number | null): number {
  return parseNumber(zText) ?? targetZ ?? 0;
}

export interface PickState {
  what: PickWhat | null;
  /** the words under the buttons while a pick is on or after it */
  note: string | null;
}

export const pickStore = new Store<PickState>({ what: null, note: null });

let active: { scene: Scene; off: () => void } | null = null;

/** Ends the pick (the tool goes; the form keeps what was put into it). */
export function stopPick(note: string | null = null): void {
  if (active) {
    active.scene.setTool(null);
    active.off();
    active = null;
  }
  pickStore.set({ what: null, note });
}

/** Starts a pick of the collar or of the target; `zText` — the field Z (the plane of the pick). */
export function startPick(what: PickWhat, zOf: () => string): void {
  const scene = currentScene();
  if (!scene) throw refusal("scene_not_ready", "3D-окно ещё не готово: откройте проект и дождитесь первого кадра.", "Когда в 3D-окне появится сетка, повторите.");
  stopPick();
  const height = (): number => heightOf(zOf(), scene.worldCamera()?.target[2] ?? null);
  const tool = {
    pointer: (e: ToolPointer): boolean => {
      if (e.type !== "click") return false;
      if (e.button === 2) {
        stopPick("Выбор точки отменён.");
        return true;
      }
      if (e.button !== 0) return false;
      const ray = scene.cursorRay(e.x, e.y);
      const h = height();
      const p = ray ? onPlane(ray, h) : null;
      if (!p) {
        pickStore.set({ note: "Луч не пересёк плоскость: наклоните вид или задайте Z." });
        return true;
      }
      p.forEach((v, i) => setXyz(what, i, numberText(i === 2 ? v : Number(v.toFixed(2)))));
      stopPick(`${what === "collar" ? "Устье" : "Цель"} выбрано на высоте Z = ${numberText(h)} м: проверьте X и Y.`);
      return true;
    },
  };
  scene.setTool(tool);
  // a new scene (another project, a lost context) takes the tool away: the pick ends with it
  const off = onScene((s) => void (s !== scene && stopPick()));
  active = { scene, off };
  pickStore.set({ what, note: `Щёлкните по сцене: точка встанет на плоскость Z = ${numberText(height())} м. Правая кнопка — отмена.` });
}
