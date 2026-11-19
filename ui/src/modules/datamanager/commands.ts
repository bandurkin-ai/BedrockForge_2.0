import type { Command } from "../../shell/commands";

/** «Менеджер данных» (Д-20; ADR 0017 §6): the data of the project as a catalog — folders, a table, actions. */
export const commands: Command[] = [
  {
    id: "data.manager",
    title: "Менеджер данных",
    description: "Окно «Менеджер данных»: данные проекта по папкам и типам, поиск, сортировка, импорт в папку, перенос, экспорт выбранного.",
    uiOnly: true,
    stage: "Э11",
  },
];
