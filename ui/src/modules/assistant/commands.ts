import type { Command } from "../../shell/commands";

/** The sections of the window «Claude: подключение» (ADR 0029 решение 35): `assistant.connect {section}` opens it on one of them. */
export const CONNECT_SECTIONS = ["profile", "login", "model", "effort", "usage"] as const;

/**
 * Claude assistant (Э23; ТЗ 7.2, 7.32; ADR 0029 решение 35). Only `assistant.ask` is seen by Claude (and it refuses a call from itself: `bind.ts`); the others are uiOnly.
 * `silent` — as in ТЗ 7.2: `assistant.ask`, `assistant.new`. The handlers are bound by `bind.ts`.
 */
export const commands: Command[] = [
  {
    id: "assistant.ask",
    title: "Спросить Claude",
    description: "Задать вопрос ассистенту Claude в панели «Claude». Из самого ассистента не вызывается.",
    params: { prompt: { type: "string", description: "текст вопроса" } },
    required: ["prompt"],
    silent: true,
    stage: "Э23",
  },
  { id: "assistant.new", title: "Новый диалог", description: "Начать новый диалог с Claude.", uiOnly: true, silent: true, stage: "Э23" },
  { id: "assistant.stop", title: "Остановить ответ", description: "Остановить ответ Claude и выполнение его команд.", uiOnly: true, stage: "Э23" },
  {
    id: "assistant.connect",
    title: "Подключение Claude",
    description: "Окно «Claude: подключение»: профиль, вход, модель, глубина рассуждений, расход.",
    params: { section: { type: "string", enum: CONNECT_SECTIONS, description: "раздел окна" } },
    uiOnly: true,
    stage: "Э23",
  },
  { id: "assistant.history", title: "История диалогов", description: "Список диалогов Claude в проекте.", uiOnly: true, stage: "Э23" },
  {
    id: "assistant.open",
    title: "Открыть диалог",
    description: "Открыть диалог из истории и продолжить с места остановки.",
    params: { dialog_id: { type: "string", description: "номер диалога из истории" } },
    required: ["dialog_id"],
    uiOnly: true,
    stage: "Э23",
  },
  {
    id: "assistant.rename",
    title: "Переименовать диалог",
    description: "Изменить название диалога в истории.",
    params: { dialog_id: { type: "string", description: "номер диалога" }, title: { type: "string", description: "новое название, до 200 знаков" } },
    required: ["dialog_id", "title"],
    uiOnly: true,
    stage: "Э23",
  },
  {
    id: "assistant.delete",
    title: "Удалить диалог",
    description: "Удалить диалог из истории проекта. Список истории спрашивает подтверждение до вызова.",
    params: { dialog_id: { type: "string", description: "номер диалога" } },
    required: ["dialog_id"],
    uiOnly: true,
    stage: "Э23",
  },
];
