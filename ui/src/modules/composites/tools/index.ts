/** The nine tools of «Анализ → Обработка проб» in the order of the ribbon (ADR 0018 §10). */
import type { ToolSpec } from "../params";
import { coding } from "./coding";
import { composite } from "./composite";
import { conditions } from "./conditions";
import { coords } from "./coords";
import { declus } from "./declus";
import { equivalent } from "./equivalent";
import { merge } from "./merge";
import { ore } from "./ore";
import { topcut } from "./topcut";

export const TOOLS: readonly ToolSpec[] = [composite, coding, merge, coords, topcut, equivalent, declus, ore, conditions];

export const toolById = (id: string): ToolSpec | undefined => TOOLS.find((t) => t.id === id);
export const toolByCommand = (command: string): ToolSpec | undefined => TOOLS.find((t) => t.command === command);
