import type { Command } from "../../shell/commands";

const VIEW_ID = { view_id: { type: "integer" } } as const;

/** Saved views (ТЗ 7.35 Д-09, ADR 0008): the camera and the view keys of the project under a name. */
export const commands: Command[] = [
  { id: "views.save", title: "Сохранить вид", description: "Сохранить текущий вид 3D-окна: камера, разрез и отсечение, видимость слоёв, отображения, преувеличение, сетка. Имя по умолчанию «Вид N». До 200 видов в проекте.", params: { name: { type: "string" } }, stage: "Э6.1" },
  { id: "views.apply", title: "Применить вид", description: "Вернуть сохранённый вид: камера и все настройки вида разом; удалённые с тех пор объекты пропускаются.", params: VIEW_ID, required: ["view_id"], stage: "Э6.1" },
  { id: "views.update", title: "Перезаписать вид текущим", description: "Заменить сохранённый вид текущим видом 3D-окна.", params: VIEW_ID, required: ["view_id"], confirm: true, stage: "Э6.1" },
  { id: "views.rename", title: "Переименовать вид", description: "Новое название сохранённого вида.", params: { ...VIEW_ID, name: { type: "string" } }, required: ["view_id", "name"], confirm: true, stage: "Э6.1" },
  { id: "views.folder", title: "Вид в папку", description: "Переложить сохранённый вид в папку раздела «Виды» (путь «А/Б»; пусто — в корень).", params: { ...VIEW_ID, folder: { type: "string" } }, required: ["view_id"], confirm: true, stage: "Э6.1" },
  { id: "views.delete", title: "Удалить вид", description: "Удалить сохранённый вид из проекта.", params: VIEW_ID, required: ["view_id"], confirm: true, stage: "Э6.1" },
];
