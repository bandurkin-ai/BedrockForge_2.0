import type { Command } from "../../shell/commands";

/** Import (Э3, Э7, Э8, Э11). */
export const commands: Command[] = [
  { id: "import.files", title: "Импорт файлов", description: "Мастер импорта с автоопределением типа.", uiOnly: true, stage: "Э3" },
  { id: "import.open", title: "Скважины", description: "Мастер импорта скважин: Collar, Survey, Assay (DAT, CSV, Excel).", uiOnly: true, stage: "Э3" },
  { id: "set.create", title: "Новый набор", description: "Создать пустой набор скважин.", params: { name: { type: "string" } }, required: ["name"], confirm: true, stage: "Э3" },
  { id: "wireframes.upload", title: "Каркасы", description: "Загрузить каркасы: Micromine TRIDB, DXF, OBJ, STL.", uiOnly: true, stage: "Э8" },
  { id: "lines.upload", title: "Линии", description: "Загрузить линии: Micromine STR, Surpac STR, DXF.", uiOnly: true, stage: "Э7" },
  { id: "rasters.upload", title: "Растры", description: "Изображение с файлом привязки или GeoTIFF.", uiOnly: true, stage: "Э7" },
  { id: "omf.import", title: "Импорт OMF", description: "OMF v1 (Leapfrog OMF 0.9).", uiOnly: true, stage: "Э11" },
  { id: "omf.export", title: "Экспорт OMF", description: "Каркасы, линии и блочные модели в один файл OMF v1.", params: { dataset_ids: { type: "string" }, filename: { type: "string" } }, stage: "Э11" },
];
