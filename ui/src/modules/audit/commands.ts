import type { Command } from "../../shell/commands";

/** Drillhole audit (Э5). */
export const commands: Command[] = [
  { id: "audit.run", title: "Запустить аудит", description: "27 правил по устьям, инклинометрии и пробам активного набора.", confirm: true, stage: "Э5" },
  { id: "audit.export", title: "Отчёт Excel", description: "Отчёт аудита в Excel.", uiOnly: true, stage: "Э5" },
];
