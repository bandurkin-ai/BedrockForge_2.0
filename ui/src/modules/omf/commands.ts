import type { Command } from "../../shell/commands";

/** The export to OMF v1 (ТЗ 7.24; ADR 0017 §4, decision 9); the import of OMF is «Импорт файлов» (`omf.import`). */
export const commands: Command[] = [
  {
    id: "omf.export",
    title: "Экспорт OMF",
    description:
      "Каркасы и ЦТМ, файлы линий и регулярные блочные модели в один файл OMF v1 (Leapfrog OMF 0.9). Без dataset_ids — окно «Экспорт OMF»: объекты, «Сохранить как…», ход. " +
      "С dataset_ids (номера объектов через запятую) от ассистента — после подтверждения сразу задание: файл ‹filename›.omf в папке Документы\\BedrockForge\\Экспорт (путь не принимается).",
    params: { dataset_ids: { type: "string", description: "номера объектов через запятую, например «7, 12»" }, filename: { type: "string", description: "имя файла без папки" } },
    stage: "Э11",
  },
];
