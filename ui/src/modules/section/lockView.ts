/**
 * «Зафиксировать вид» (L; ТЗ 8.1; ADR 0016 §4): the command `series.lock` for the registry of the section module —
 * its entry (`lockCommand`), its handler (`runLock`) and the reason it is unavailable. The view is orthographic
 * along v̂ of the current section with ŵ up, turning is off (the mouse, the gizmo), panning and zooming work;
 * `]` / `[` keep a point with the same (u, w) in its place (`Scene.lockView`, `viz/core/lockView.ts`).
 * A standard view (T, X, Y, iso) and the end of the section free it.
 */
import type { Command, CommandContext } from "../../shell/commands";
import { requireScene } from "../scene/sceneHost";

export const lockCommand: Command = {
  id: "series.lock",
  title: "Зафиксировать вид",
  description: "Зафиксировать вид на текущем разрезе (серии или линии разреза): ортогональная камера по нормали к плоскости, верх — ось w разреза, вращение выключено, сдвиг и масштаб работают; переход к соседнему разрезу сохраняет точку на экране. on — включить или выключить, без него — переключить.",
  params: { on: { type: "boolean" } },
};

export const needSectionToLock = (ctx: CommandContext): string | null => (!ctx.hasProject ? "Откройте или создайте проект" : ctx.hasSection ? null : "Сначала задайте разрез: линию (S) или серию");

/** The handler of `series.lock`: the Russian result for the log and Claude. */
export function runLock(args: Record<string, unknown>): string {
  const scene = requireScene();
  const on = typeof args.on === "boolean" ? args.on : !scene.locked;
  if (on === scene.locked) return on ? "Вид уже зафиксирован" : "Вид не зафиксирован";
  if (!scene.lockView(on)) throw new Error("Нет разреза для фиксации вида: проведите линию разреза (S) или выберите серию");
  return on ? "Вид зафиксирован на разрезе: вращение выключено, сдвиг и масштаб работают" : "Вид освобождён";
}
