import type { Command } from "../../shell/commands";

/** Claude assistant (Э23). */
export const commands: Command[] = [
  { id: "assistant.ask", title: "Спросить Claude", description: "Отправить запрос ассистенту.", params: { prompt: { type: "string" } }, required: ["prompt"], silent: true, stage: "Э23" },
  { id: "assistant.new", title: "Новый диалог", description: "Начать новый диалог.", uiOnly: true, silent: true, stage: "Э23" },
];
