import type { Command } from "../../shell/commands";

/** Background jobs (Э2). */
export const commands: Command[] = [
  { id: "jobs.list", title: "Задания", description: "Список фоновых заданий: очередь, прогресс, ошибки.", stage: "Э2" },
  { id: "jobs.retry", title: "Повторить задание", description: "Запустить упавшее задание заново с теми же файлами и параметрами (кроме заданий с паролем).", params: { job_id: { type: "integer" }, source: { type: "string" } }, required: ["job_id"], confirm: true, stage: "Э2" },
  { id: "jobs.restart_workers", title: "Перезапустить исполнителей", description: "Перезапустить процессы-исполнители фоновых заданий, если они остановлены.", stage: "Э2" },
  { id: "jobs.clear_finished", title: "Очистить выполненные задания", description: "Убрать из списка «Задания» готовые и отменённые задания вместе с их журналами (с флагом include_failed — и завершившиеся ошибкой). Задания в очереди и выполняемые не трогаются; файлы экспортов остаются на диске.", params: { include_failed: { type: "boolean" } }, confirm: true, stage: "Э6.1" },
  { id: "jobs.cancel", title: "Отменить задание", description: "Отменить фоновое задание.", params: { job_id: { type: "integer" } }, required: ["job_id"], confirm: true, stage: "Э2" },
];
