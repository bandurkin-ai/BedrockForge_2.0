/**
 * The command of «Перепроверить» of a block model (e17-api §11, §15, §16; Э17 17.41): `blocks.check {model_id}` — the job
 * `blocks.check` of the core over the stored model (the handler is `check.ts`, the button — `checkButton.tsx`). Flat
 * parameters; no `confirm` (it writes only `meta.checks`, the version of the model stays); open to the assistant;
 * unavailable in «только просмотр». Declared here, not in `commands.ts`, until the orchestrator names this file in
 * `shell/catalog.ts`. Pure: no React.
 */
import type { Command } from "../../shell/commands";

export const commands: Command[] = [
  {
    id: "blocks.check",
    title: "Перепроверить блочную модель",
    description:
      "Пересчитывает проверки блочной модели (7.31, Т8.7-5): повторы блоков, перекрытия (точно или по объёму родителя), поля без единого значения, поля с одним значением, объём блоков и примеры строк. " +
      "Ядро читает модель заново и записывает итог в её свойства; версия модели не меняется, поля и ячейки не трогаются. Автоматически проверки пересчитываются только при импорте — команда нужна после заполнения, регуляризации, кодирования или правки полей. " +
      "Если модель изменили, пока шла проверка, ответ — «версия изменилась»: перечитайте модель и повторите. Недоступна, когда проект открыт только для чтения. Без model_id — выбранная модель или единственная в проекте.",
    params: { model_id: { type: "integer", description: "номер блочной модели (раздел «Блочные модели» дерева); без него — выбранная модель или единственная в проекте" } },
  },
];
