import type { Command } from "../../shell/commands";

/** Sections (Э6); the commands of rasters are in `modules/rasters/commands.ts`. */
export const commands: Command[] = [
  { id: "section.set", title: "Задать разрез", description: "Линия разреза: начало (x0, y0), азимут az, длина, окно к наблюдателю (towards) и от него (away), м.", params: { x0: { type: "number" }, y0: { type: "number" }, az: { type: "number" }, length: { type: "number" }, towards: { type: "number" }, away: { type: "number" } }, required: ["x0", "y0", "az"], stage: "Э6" },
  { id: "section.through_hole", title: "Разрез через скважину", description: "Разрез через устье по азимуту скважины в плане (от устья к забою): вся скважина в плоскости разреза; у вертикальной азимут прежний. Без hole_id — выбранная скважина.", params: { hole_id: { type: "string" }, set_id: { type: "integer" } }, stage: "Э6" },
  { id: "section.auto", title: "Авто-разрез", description: "Разрез поперёк простирания устьев видимых наборов через их центр; включает отсечение и вид в плоскость.", stage: "Э6" },
  { id: "section.window", title: "Окно разреза", description: "Окно разреза: от (к наблюдателю, towards) и до (от наблюдателя, away), м; clip — отсечение.", params: { towards: { type: "number" }, away: { type: "number" }, clip: { type: "boolean" } }, stage: "Э6" },
  { id: "section.clip", title: "Отсечение", description: "Отсекать всё за пределами окна разреза.", params: { on: { type: "boolean" } }, stage: "Э6" },
  { id: "section.step", title: "Профиль", description: "Перейти к предыдущему (-1) или следующему (1) профилю.", params: { direction: { type: "integer", enum: [-1, 1] } }, required: ["direction"], stage: "Э6" },
  { id: "section.clear", title: "Сбросить разрез", description: "Убрать линию разреза.", stage: "Э6" },
  { id: "section.draw", title: "Разрез", description: "Провести линию разреза мышью: два щелчка в виде или на мини-плане.", uiOnly: true, silent: true, stage: "Э6" },
  { id: "section.plan", title: "План разреза", description: "Показать или скрыть мини-план с линией разреза.", params: { open: { type: "boolean" } }, uiOnly: true, stage: "Э6" },
];
