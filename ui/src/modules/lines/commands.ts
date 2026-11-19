import { SNAP_KINDS } from "../../edit/snap";
import { TOOLS } from "../../edit/tools";
import type { Command } from "../../shell/commands";
import { LINES_FORMAT_IDS } from "./export";
import { MODIFY_IDS } from "./modify";

/** String editor (Э9; handlers — `editCommands.ts`), the export of layers (Э7) and the map of differences of a point cloud (Э7.1, ADR 0014 §5;
 * handlers — `bind.ts`). */
export const commands: Command[] = [
  { id: "lines.new_layer", title: "Новый слой", description: "Пустой слой линий в новом файле и сразу режим правки (редактор стрингов). name — название слоя (станет кодом стринга в STR); без него название спрашивается в окне.", params: { name: { type: "string" } }, stage: "Э9" },
  { id: "lines.save", title: "Сохранить слой", description: "Сохранить правку слоя: новая версия в журнале версий. Если слой сохранён после начала правки — отказ, ничего не затирается. note — заметка к версии.", params: { note: { type: "string" } }, confirm: true, stage: "Э9" },
  { id: "lines.edit", title: "Редактировать", description: "Открыть слой линий на правку в редакторе стрингов (3D-окно). layer_id — слой; без него — выбранный слой линий. Открытая правка другого слоя завершается (с вопросом о несохранённом).", params: { layer_id: { type: "integer" } }, stage: "Э9" },
  { id: "lines.tool", title: "Инструмент", description: "Инструмент редактора стрингов: select — выбор и перемещение, line — линия, polygon — полигон, point — точка, tie — тай-линия, insert — вставить вершину.", params: { tool: { type: "string", enum: TOOLS.map((t) => t.id) } }, required: ["tool"], stage: "Э9" },
  { id: "lines.add_line", title: "Добавить линию", description: "Добавить в слой на правке линию по координатам проекта: points — JSON-массив точек [[x, y, z], …] (точка [x, y] ложится на Z панели), closed — полигон. Только в режиме правки (lines.edit или lines.new_layer); записывает в проект lines.save.", params: { points: { type: "string" }, closed: { type: "boolean" } }, required: ["points"], stage: "Э9" },
  { id: "lines.undo", title: "Отменить", description: "Отменить последнюю правку в редакторе стрингов.", stage: "Э9" },
  { id: "lines.redo", title: "Повторить", description: "Повторить отменённую правку в редакторе стрингов.", stage: "Э9" },
  { id: "lines.measure", title: "Измерить", description: "Измерения выбранных линий и всего слоя на правке, с несохранёнными правками: вершины, длина 3D и в плане, периметр, площадь полигона в его плоскости и в плане.", stage: "Э9" },
  {
    id: "lines.modify",
    title: "Правка линий",
    description:
      "Правка выбранных линий в редакторе стрингов. action: close — замкнуть, open — разомкнуть, break — разорвать в выбранной вершине, join — соединить две, reverse — развернуть, translate — сдвинуть на dx, dy, dz, copy — копия на dx, dy, dz, offset — копия со смещением distance (у полигона больше нуля — наружу), simplify — проредить с допуском tolerance (Дуглас–Пекер), densify — сгустить с шагом step, smooth — сгладить (iterations проходов), delete — удалить выбранное, finish_line — закончить рисуемую линию. Без чисел для действий с числами открывается окно «Правка выбранного».",
    params: { action: { type: "string", enum: MODIFY_IDS }, dx: { type: "number" }, dy: { type: "number" }, dz: { type: "number" }, distance: { type: "number" }, tolerance: { type: "number" }, step: { type: "number" }, iterations: { type: "integer" } },
    required: ["action"],
    stage: "Э9",
  },
  {
    id: "lines.snap",
    title: "Привязка",
    description: `Привязка редактора стрингов: enabled — вкл/выкл; kinds — цели через запятую (${SNAP_KINDS.join(", ")}); radius — радиус, px; project — на разрезе проецировать цели скважин и каркасов в плоскость разреза; grid — шаг сетки, м.`,
    params: { enabled: { type: "boolean" }, kinds: { type: "string" }, radius: { type: "integer" }, project: { type: "boolean" }, grid: { type: "number" } },
    stage: "Э9",
  },
  {
    id: "lines.export",
    title: "Экспорт линий",
    description:
      "Слои линий и точек в файл: Micromine STR (str), Surpac STR (surpac), DXF (dxf), CSV (csv) или Micromine DAT (dat); форматы STR пишут только линии. Облако точек — отдельно, в LAS (las: по исходному файлу или новый) или CSV (X;Y;Z;класс;h). layer_id — один слой, file_id — все слои файла линий, без них — выбранный слой. Открывает окно «Экспорт линий» с этим форматом и слоями: файл выбирает пользователь, экспорт идёт как задание.",
    params: { format: { type: "string", enum: LINES_FORMAT_IDS }, layer_id: { type: "integer" }, file_id: { type: "integer" } },
    stage: "Э7",
  },
  {
    id: "cloud.dz",
    title: "Карта разностей",
    description:
      "Отклонение точек облака от поверхности: dz = Z точки − высота каркаса или ЦТМ под ней (над ней); у облака появляется режим цвета «Отклонение от ЦТМ», в «Свойствах» — СКО, P95 и наибольшее по модулю отклонение по точкам земли (класс 2) и число точек вне поверхности. Прежние отклонения облака заменяются. Только cloud_id (или без параметров) — открыть окно «Карта разностей» (поверхность по умолчанию — та, с которой сравнивали в прошлый раз, или последняя ЦТМ из этого облака); с wireframe_id (каркас или ЦТМ из раздела «Каркасы» или «ЦТМ») — сразу задание «Карта разностей» (ход — на вкладке «Задания»). Без cloud_id — выбранное облако или единственное облако проекта.",
    params: { cloud_id: { type: "integer" }, wireframe_id: { type: "integer" } },
    stage: "Э7.1",
  },
  { id: "lines.finish", title: "Завершить", description: "Выйти из режима правки; при несохранённых изменениях — вопрос о сохранении.", uiOnly: true, stage: "Э9" },
  { id: "lines.move_selected", title: "Перенести в другой слой", description: "Перенести выбранные линии слоя на правке в другой слой (target_id): оба слоя получают новую версию; несохранённые правки слоя сохраняются вместе с переносом, новые линии сначала сохраните. Без target_id — окно выбора слоя.", params: { target_id: { type: "integer" } }, confirm: true, stage: "Э9" },
  { id: "lines.versions", title: "Версии", description: "Журнал версий слоя: когда, кто, линии и вершины «было → стало»; возврат к версии.", params: { layer_id: { type: "integer" } }, uiOnly: true, stage: "Э9" },
  { id: "lines.restore", title: "Вернуть версию", description: "Вернуть слой линий к версии version журнала: создаётся новая версия с геометрией той версии, журнал не стирается. Слой на правке перечитывается (несохранённые правки пропадают).", params: { layer_id: { type: "integer" }, version: { type: "integer" } }, required: ["layer_id", "version"], confirm: true, stage: "Э9" },
];
