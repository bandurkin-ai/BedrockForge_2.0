import type { Command } from "../../shell/commands";

/** Projects (Э2). */
export const commands: Command[] = [
  { id: "project.new", title: "Новый проект", description: "Создать пустой проект и открыть его.", params: { name: { type: "string" } }, confirm: true, stage: "Э2" },
  { id: "project.demo", title: "Демо-проект", description: "Учебный проект: 48 скважин, пробы, аудит, каркасы рудного тела.", confirm: true, stage: "Э2" },
  { id: "project.rename", title: "Переименовать проект", description: "Новое название и описание проекта.", params: { name: { type: "string" }, description: { type: "string" } }, confirm: true, stage: "Э2" },
  { id: "project.delete", title: "Удалить проект", description: "Удалить текущий проект со всеми данными.", confirm: true, stage: "Э2" },
  { id: "project.manage", title: "Проекты", description: "Окно управления проектами: название, описание, состав, все проекты.", stage: "Э2" },
  { id: "project.export", title: "Экспорт проекта", description: "Весь проект одним файлом .bfggis.", params: { project_id: { type: "integer" }, filename: { type: "string" }, include_view: { type: "boolean" }, start: { type: "boolean" } }, stage: "Э2" },
  { id: "project.import", title: "Импорт проекта", description: "Открыть проект из файла .bfggis.", params: { path: { type: "string" } }, uiOnly: true, stage: "Э2" },
  { id: "dataset.move", title: "В папку", description: "Переместить объект в папку дерева (путь через «/», пусто — в корень).", params: { kind: { type: "string" }, id: { type: "integer" }, folder: { type: "string" } }, required: ["kind", "id", "folder"], confirm: true, stage: "Э3" },
  { id: "data.refresh", title: "Обновить", description: "Перечитать данные проекта из ядра.", stage: "Э2" },
];
