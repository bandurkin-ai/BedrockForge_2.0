/**
 * The window of a block model (ТЗ 8.7.6; ADR 0023 §11, §13): the command `blocks.settings`. The ribbon (the tab «Блочные
 * модели», the group «Вид»), Ctrl+K, the keys of the user and the menu of the tree run the same command. It only opens the
 * window (as ADR 0021 п. 9; `uiOnly` as in the table of §13) — what the window changes is the style of the model in the view
 * of the project, the same keys `blocks.show`, `.filter`, `.quick`, `.style` write for the assistant. `SETTINGS_BUTTONS` — the
 * button of the ribbon (`shell/ribbonConfig.ts` puts it in, by the orchestrator).
 */
import type { Command } from "../../../shell/commands";
import type { IconName } from "../../../shell/Icon";
import { MODEL } from "../commands";

export const commands: Command[] = [
  {
    id: "blocks.settings",
    title: "Настройки блочной модели",
    description:
      "Открыть окно «Блочная модель · …» (ТЗ 8.7.6): показ (оболочка или все блоки), раскраска (поле, шкала, градации, категории, палитры), рёбра, непрозрачность, лимит граней, " +
      "фильтр из библиотеки, быстрый ограничитель, легенда и свойства модели. Все изменения применяются сразу и сохраняются в виде проекта. Без model_id — выбранная модель или единственная в проекте.",
    params: { model_id: MODEL },
    uiOnly: true,
  },
];

export const SETTINGS_BUTTONS: readonly { command: string; args?: Record<string, unknown>; label: string; icon: IconName; hint: string; small?: boolean }[] = [
  { command: "blocks.settings", label: "Настройки", icon: "properties" as IconName, hint: "Окно «Блочная модель · …»: показ, раскраска, фильтр, быстрый ограничитель, легенда, свойства", small: true },
];
