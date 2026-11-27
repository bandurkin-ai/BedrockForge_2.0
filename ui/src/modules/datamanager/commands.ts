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
  {
    id: "data.delete",
    title: "Удалить данные",
    description:
      "Удалить из проекта несколько объектов данных одним действием: ids — номера через запятую, как в менеджере данных (каркасы, ЦТМ, растры, файлы линий и облака точек, блочные модели; наборы скважин здесь не удаляются). Один объект — также object.delete. " +
      "Меняет данные: выполняется после подтверждения пользователем в окне со списком имён удаляемых объектов. Отменить удаление нельзя.",
    params: { ids: { type: "string", description: "номера объектов через запятую" } },
    required: ["ids"],
    confirm: true, // S10 (ТЗ 5): a deletion asked by Claude is confirmed in the chat first (23.15)
    stage: "Э11",
  },
];
