import type { Command } from "../../shell/commands";

/** Background jobs (Э2). */
export const commands: Command[] = [
  { id: "jobs.list", title: "Задания", description: "Список фоновых заданий: очередь, прогресс, ошибки.", stage: "Э2" },
  { id: "jobs.retry", title: "Повторить задание", description: "Запустить упавшее задание заново с теми же файлами и параметрами (кроме заданий с паролем).", params: { job_id: { type: "integer" }, source: { type: "string" } }, required: ["job_id"], confirm: true, stage: "Э2" },
  { id: "jobs.restart_workers", title: "Перезапустить исполнителей", description: "Перезапустить процессы-исполнители фоновых заданий, если они остановлены.", stage: "Э2" },
  { id: "jobs.cancel", title: "Отменить задание", description: "Отменить фоновое задание.", params: { job_id: { type: "integer" } }, required: ["job_id"], confirm: true, stage: "Э2" },
];
