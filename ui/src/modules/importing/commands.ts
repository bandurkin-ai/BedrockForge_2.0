import type { Command } from "../../shell/commands";

/** Import (Э3, Э7, Э7.1, Э8, Э11). */
export const commands: Command[] = [
  { id: "import.files", title: "Импорт файлов", description: "Мастер импорта с автоопределением типа.", params: { kind: { type: "string", enum: ["holes", "wireframes", "lines", "rasters", "omf", "project"] } }, uiOnly: true, stage: "Э3" },
  { id: "import.open", title: "Скважины", description: "Мастер импорта скважин: Collar, Survey, Assay (DAT, CSV, Excel). mode: trenches — канавы по полилиниям (STR, DXF или таблица вершин; пробы — по 3D-длине).", params: { mode: { type: "string", enum: ["holes", "trenches"] } }, uiOnly: true, stage: "Э3" },
  { id: "set.create", title: "Новый набор", description: "Создать пустой набор скважин.", params: { name: { type: "string" } }, required: ["name"], confirm: true, stage: "Э3" },
  { id: "wireframes.upload", title: "Каркасы", description: "Загрузить каркасы: Micromine TRIDB, AutoCAD DXF и DWG (DWG — через ODA File Converter), Wavefront OBJ, STL, Surpac DTM с STR, Datamine (предварительно). Чертёж даёт и линии, и каркасы; после загрузки каркасы проверяются.", uiOnly: true, stage: "Э8" },
  { id: "lines.upload", title: "Линии", description: "Загрузить линии и точки: Micromine STR и PIT, Surpac STR, DXF, SHP, MIF/MID, таблицы точек XYZ.", uiOnly: true, stage: "Э7" },
  { id: "points.import", title: "Облако точек", description: "Мастер загрузки облака точек: mode las — LAS 1.2–1.4 (LAZ не читается), xyz — таблица точек XYZ, CSV или TXT облаком (колонки X, Y, Z, класс); «каждая k-я запись» прореживает большое облако.", params: { mode: { type: "string", enum: ["las", "xyz"] } }, uiOnly: true, stage: "Э7.1" },
  { id: "rasters.upload", title: "Растры", description: "Изображение с файлом привязки или GeoTIFF.", uiOnly: true, stage: "Э7" },
  { id: "omf.import", title: "Импорт OMF", description: "OMF v1 (Leapfrog OMF 0.9).", uiOnly: true, stage: "Э11" },
  { id: "omf.export", title: "Экспорт OMF", description: "Каркасы, линии и блочные модели в один файл OMF v1.", params: { dataset_ids: { type: "string" }, filename: { type: "string" } }, stage: "Э11" },
];
