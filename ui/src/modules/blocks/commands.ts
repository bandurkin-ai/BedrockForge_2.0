/**
 * Commands of block models (ADR 0017 §3). Rename, delete and select are the common `object.*` with
 * `kind: "blocks"` (`wireframes/objectCommands.ts`); «Свойства» is `object.select`. The display in 3D is Э17:
 * the command is declared with its stage, so «Показать в 3D» of the tree is disabled with the reason.
 */
import type { Command } from "../../shell/commands";

export const commands: Command[] = [
  { id: "blocks.show", title: "Показать блочную модель в 3D", description: "Показ блочной модели в 3D-окне: ячейки, раскраска по полю, фильтр.", params: { id: { type: "integer" } }, stage: "Э17" },
];
