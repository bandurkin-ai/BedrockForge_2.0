import type { Command } from "../../shell/commands";

/** Projects (Э2). */
export const commands: Command[] = [
  { id: "project.new", title: "Новый проект", description: "Создать пустой проект и открыть его.", params: { name: { type: "string" } }, confirm: true, stage: "Э2" },
  { id: "project.demo", title: "Демо-проект", description: "Учебный проект: 48 скважин, пробы, аудит, каркасы рудного тела.", confirm: true, stage: "Э2" },
  { id: "project.open", title: "Открыть проект", description: "Открыть проект по его uid (список проектов — окно «Проекты»).", params: { uid: { type: "string" } }, required: ["uid"], stage: "Э2" },
  { id: "project.rename", title: "Переименовать проект", description: "Новое название и описание проекта.", params: { name: { type: "string" }, description: { type: "string" } }, confirm: true, stage: "Э2" },
  { id: "project.delete", title: "Удалить проект", description: "Удалить текущий проект со всеми данными.", confirm: true, stage: "Э2" },
  { id: "project.manage", title: "Проекты", description: "Окно управления проектами: название, описание, состав, все проекты.", stage: "Э2" },
  {
    id: "project.export",
    title: "Экспорт проекта",
    description:
      "Без параметров — окно экспорта всего проекта одним файлом .bfggis. С formats, folder или types — объекты проекта в файлы форматов по типам (Д-17): " +
      "formats — «тип=формат» через запятую (drillhole_set=csv|xlsx|dat, wireframe и dtm=dxf|dwg|tridb|obj|stl|dtm|omf, line_file=str|dxf|surpac|omf, cloud=las|csv, raster=source|geotiff, block_model=omf; не названные — по умолчанию); " +
      "types — какие типы через запятую (по умолчанию все, где есть объекты); folder — папка, в которой создаётся «‹Проект› — экспорт ГГГГ-ММ-ДД». " +
      "С folder задание запускается после подтверждения пользователем; без folder открывается окно «Объекты в файлы» с этим выбором.",
    params: {
      formats: { type: "string", description: "«тип=формат» через запятую, например wireframe=dxf, line_file=str" },
      folder: { type: "string", description: "папка назначения (внутри неё создаётся новая папка экспорта)" },
      types: { type: "string", description: "типы через запятую: drillhole_set, wireframe, dtm, line_file, cloud, raster, block_model" },
      project_id: { type: "integer" },
      filename: { type: "string" },
      include_view: { type: "boolean" },
      start: { type: "boolean" },
    },
    stage: "Э2",
  },
  { id: "project.import", title: "Импорт проекта", description: "Открыть проект из файла .bfggis.", params: { path: { type: "string" } }, uiOnly: true, stage: "Э2" },
  { id: "dataset.move", title: "В папку", description: "Переместить объект в папку дерева (путь через «/», пусто — в корень).", params: { kind: { type: "string" }, id: { type: "integer" }, folder: { type: "string" } }, required: ["kind", "id", "folder"], confirm: true, stage: "Э3" },
  { id: "data.refresh", title: "Обновить", description: "Перечитать данные проекта из ядра.", stage: "Э2" },
];
