import type { Command } from "../../shell/commands";

/** Sections and rasters (Э6, Э7). */
export const commands: Command[] = [
  { id: "section.set", title: "Задать разрез", description: "Линия разреза: начало (x0, y0), азимут az, длина, окно к наблюдателю (towards) и от него (away), м.", params: { x0: { type: "number" }, y0: { type: "number" }, az: { type: "number" }, length: { type: "number" }, towards: { type: "number" }, away: { type: "number" } }, required: ["x0", "y0", "az"], stage: "Э6" },
  { id: "section.through_hole", title: "Разрез по скважине", description: "Разрез вдоль выбранной скважины.", params: { hole_id: { type: "string" } }, stage: "Э6" },
  { id: "section.window", title: "Окно разреза", description: "Ширина окна к наблюдателю и от него, м.", params: { towards: { type: "number" }, away: { type: "number" } }, stage: "Э6" },
  { id: "section.clip", title: "Отсечение", description: "Отсекать всё за пределами окна разреза.", params: { on: { type: "boolean" } }, stage: "Э6" },
  { id: "section.step", title: "Профиль", description: "Перейти к предыдущему (-1) или следующему (1) профилю.", params: { direction: { type: "integer", enum: [-1, 1] } }, required: ["direction"], stage: "Э6" },
  { id: "section.clear", title: "Сбросить разрез", description: "Убрать линию разреза.", stage: "Э6" },
  { id: "section.draw", title: "Разрез", description: "Провести линию разреза мышью.", uiOnly: true, silent: true, stage: "Э6" },
  { id: "raster.set", title: "Растр", description: "Высота и непрозрачность растра.", params: { id: { type: "integer" }, elevation: { type: "number" }, opacity: { type: "number" } }, required: ["id"], stage: "Э7" },
  { id: "raster.place_on_section", title: "Растр на разрез", description: "Положить растр в плоскость текущего разреза.", params: { id: { type: "integer" } }, required: ["id"], confirm: true, stage: "Э7" },
];
