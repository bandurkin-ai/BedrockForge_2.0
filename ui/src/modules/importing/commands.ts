import type { Command } from "../../shell/commands";

/** Import (Э3, Э7, Э7.1, Э8, Э11); the export to OMF — `modules/omf/commands.ts`. */
export const commands: Command[] = [
  {
    id: "import.files",
    title: "Импорт файлов",
    description: "Мастер импорта с автоопределением типа. kind — какие файлы предлагает окно выбора (omf — файлы OMF, blocks — блочные модели Micromine DAT и CSV, Datamine .dm и .fbm: столбцы центра и размера блока находятся по названиям, роли столбцов, единицы, коды «пусто», сетка и поворот правятся в окне); folder — папка дерева, в которую лягут объекты («Импорт/Карьер»; без неё — папка по имени файла).",
    params: { kind: { type: "string", enum: ["holes", "wireframes", "lines", "rasters", "omf", "blocks", "project"] }, folder: { type: "string" } },
    uiOnly: true,
    stage: "Э3",
  },
  { id: "import.open", title: "Скважины", description: "Мастер импорта скважин: Collar, Survey, Assay (DAT, CSV, Excel). mode: trenches — канавы по полилиниям (STR, DXF или таблица вершин; пробы — по 3D-длине).", params: { mode: { type: "string", enum: ["holes", "trenches"] } }, uiOnly: true, stage: "Э3" },
  { id: "set.create", title: "Новый набор", description: "Создать пустой набор скважин.", params: { name: { type: "string" } }, required: ["name"], confirm: true, stage: "Э3" },
  { id: "wireframes.upload", title: "Каркасы", description: "Загрузить каркасы: Micromine TRIDB, AutoCAD DXF и DWG (DWG — через ODA File Converter), Wavefront OBJ, STL, Surpac DTM с STR, Datamine (предварительно). Чертёж даёт и линии, и каркасы; после загрузки каркасы проверяются.", uiOnly: true, stage: "Э8" },
  { id: "lines.upload", title: "Линии", description: "Загрузить линии и точки: Micromine STR и PIT, Surpac STR, DXF, SHP, MIF/MID, таблицы точек XYZ.", uiOnly: true, stage: "Э7" },
  { id: "points.import", title: "Облако точек", description: "Мастер загрузки облака точек: mode las — LAS 1.2–1.4 (LAZ не читается), xyz — таблица точек XYZ, CSV или TXT облаком (колонки X, Y, Z, класс); «каждая k-я запись» прореживает большое облако.", params: { mode: { type: "string", enum: ["las", "xyz"] } }, uiOnly: true, stage: "Э7.1" },
  { id: "rasters.upload", title: "Растры", description: "Изображение с файлом привязки или GeoTIFF.", uiOnly: true, stage: "Э7" },
  { id: "omf.import", title: "Импорт OMF", description: "Импорт файла OMF v1 (Leapfrog OMF 0.9): поверхности — каркасы, линии — слои, регулярные объёмы — блочные модели; состав и что не загрузится — в мастере «Импорт файлов». OMF 2 не читается.", params: { folder: { type: "string" } }, uiOnly: true, stage: "Э11" },
];
