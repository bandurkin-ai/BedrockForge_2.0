/**
 * Why a command of the implicit modelling is unavailable (ADR 0021 п. 9: the ribbon, Ctrl+K and the assistant say the reason in words):
 * no models in the project, the model is being built, the model is not built yet. The model is the one chosen in the tree, else the only
 * one of the project; with several and none chosen the command answers by itself. No React.
 */
import type { App } from "../../shell/app";
import type { CommandContext } from "../../shell/commands";
import { implicitStore, selectedModelId } from "./state";

/** `any` — a model that exists and is not being built; `built` — and one that has results. */
export type Need = "any" | "built";

export function modelReason(app: App, need: Need): string | null {
  const st = implicitStore.get();
  if (!st.loaded) return null;
  if (!st.rows.length) return "В проекте нет неявных моделей: создайте её командой «Новая модель»";
  const id = selectedModelId(app.store.get().selected);
  const row = id !== null ? st.rows.find((m) => m.id === id) : st.rows.length === 1 ? st.rows[0] : undefined;
  if (!row) return null;
  if (row.state === "building") return `Модель «${row.name}» сейчас строится: дождитесь окончания задания во вкладке «Задания»`;
  if (need === "built" && row.stats === null) return `Модель «${row.name}» ещё не построена: сначала выполните «Обновить»`;
  return null;
}

/** The reason of a command: its own (`base`: no project, a project for reading only) first, then the state of the model. */
export const needModelFor =
  (app: App, base: (ctx: CommandContext) => string | null, need: Need) =>
  (ctx: CommandContext): string | null =>
    base(ctx) ?? modelReason(app, need);
