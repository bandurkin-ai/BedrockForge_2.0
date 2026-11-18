import type { Command } from "../../shell/commands";

const RASTER = { type: "integer", description: "Номер растра; без него — выбранный растр (или единственный растр проекта)" } as const;
const EDGE = (what: string) => ({ type: "number", description: `${what} растра в вертикальной плоскости; шесть краёв задаются вместе` }) as const;

/** Rasters (ТЗ 7.19; ADR 0011 §5): «Свойства», «Сдвинуть», «На текущий разрез», export (handlers — `bind.ts`). */
export const commands: Command[] = [
  {
    id: "raster.set",
    title: "Высота и непрозрачность растра",
    description:
      "Свойства растра: elevation — высота растра в плане, м (null — под самым низким объектом), opacity — непрозрачность 0,05…1; x1, y1, x2, y2, z_top, z_bottom — края растра в вертикальной плоскости (левый, правый, верх, низ). Без значений — открыть «Свойства» растра.",
    params: {
      raster_id: RASTER,
      elevation: { type: "number", nullable: true },
      opacity: { type: "number" },
      x1: EDGE("Левый край X"),
      y1: EDGE("Левый край Y"),
      x2: EDGE("Правый край X"),
      y2: EDGE("Правый край Y"),
      z_top: EDGE("Верх Z"),
      z_bottom: EDGE("Низ Z"),
    },
    stage: "Э7",
  },
  {
    id: "raster.shift",
    title: "Сдвинуть растр по Z",
    description: "Сдвинуть растр по высоте на dz метров: плюс — вверх, минус — вниз; у вертикального растра сдвигаются все углы. У растра в плане без высоты сдвигать нечего. Без dz сдвиг спрашивается в окне.",
    params: { raster_id: RASTER, dz: { type: "number" } },
    confirm: true,
    stage: "Э7",
  },
  {
    id: "raster.place_on_section",
    title: "Растр на текущий разрез",
    description:
      "Поставить растр (и растр в плане) в плоскость линии разреза: края — по линии, верх и низ — по высотам данных; z_top — отметка верхнего края, тогда низ — по пропорциям изображения. Прежняя привязка заменяется. Нужна проведённая линия разреза.",
    params: { raster_id: RASTER, z_top: { type: "number", nullable: true } },
    confirm: true,
    stage: "Э7",
  },
  {
    id: "rasters.export",
    title: "Экспорт растра",
    description:
      "Растр в файл: исходный файл с привязкой рядом (source: world-файл и .prj) или один GeoTIFF (geotiff). Открывает окно «Экспорт растра» с выбором файла; с path (полный путь к файлу) экспорт запускается сразу как задание.",
    params: { raster_id: RASTER, format: { type: "string", enum: ["source", "geotiff"] }, path: { type: "string", description: "Полный путь к файлу; без него откроется окно" } },
    stage: "Э7",
  },
];
