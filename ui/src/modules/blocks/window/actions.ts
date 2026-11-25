/**
 * What the window «Блочная модель · …» does with a model (ТЗ 8.7.6; ADR 0023 §11, §13): a change of the style is a patch
 * written into `block_styles["<id>"]` of the view of the project (`styleOp` merges it into the stored entry, so what
 * other windows and commands set survives); the layer in the 3D window follows the view and asks the core only for what
 * changes the faces (`display.ts` `requestKey`) — colours and look never leave the interface. «Показать / Скрыть» and
 * the mode go through the registry (`blocks.show`: the log, the camera comes to the model), «Сохранить как фильтр» is
 * `filter.save {name, rules}`. A failure is a Russian text with its hint. No React.
 */
import type { App } from "../../../shell/app";
import { errorText } from "../../../shell/props";
import { updateView, viewStore } from "../../scene/bind";
import { BLOCK_STYLES_KEY, quickIssue, restrictOf, styleIn, styleOp, type BlockStyle, type StylePatch } from "../display";
import { lastSaved } from "../filters/bind";

/** Writes a patch into the style of the model; the text of a failure (no project open, …) or null. */
export function writeStyle(id: number, patch: StylePatch): string | null {
  try {
    updateView(BLOCK_STYLES_KEY, styleOp(id, patch));
    return null;
  } catch (e) {
    return errorText(e);
  }
}

export const currentStyle = (id: number): BlockStyle => styleIn(viewStore.get(), id);

/** «Показать в 3D» / «Скрыть»; the mode is the one of the form. The result of `blocks.show` through the registry. */
export async function showModel(app: App, id: number, visible: boolean, mode?: BlockStyle["mode"]): Promise<string | null> {
  const r = await app.registry.execute("blocks.show", { model_id: id, visible, ...(mode ? { mode } : {}) }, "user");
  return r.ok ? null : r.result;
}

export interface SavedQuick {
  ok: boolean;
  /** what to tell the person (done, or why not) */
  text: string;
  filterId: number | null;
}

/**
 * «Сохранить быстрый ограничитель как фильтр»: the tree of the restrictor (`restrictOf`) saved under a name by
 * `filter.save` (it goes through the checks of the window «Фильтры», a name in use is a 409 in Russian). A model with no
 * filter takes the new one and its restrictor is emptied (the same blocks pass); a model that has a filter keeps it — the
 * new filter is in the list, the choice is the person's (swapping would drop the old one's conditions).
 */
export async function saveQuickAsFilter(app: App, id: number, name: string): Promise<SavedQuick> {
  const style = currentStyle(id);
  const rule = restrictOf(style.quick);
  if (!rule) return { ok: false, text: "Быстрый ограничитель пуст: добавьте условие на вкладке «Быстрый ограничитель».", filterId: null };
  const issue = quickIssue(style.quick);
  if (issue) return { ok: false, text: issue, filterId: null };
  const clean = name.trim();
  if (!clean) return { ok: false, text: "У фильтра нет имени. Введите имя, например «Руда выше борта».", filterId: null };
  const r = await app.registry.execute("filter.save", { name: clean, rules: JSON.stringify(rule) }, "user");
  if (!r.ok) return { ok: false, text: r.result, filterId: null };
  const made = lastSaved();
  if (!made || made.name !== clean) return { ok: true, text: r.result, filterId: null };
  if (style.filter_id === null) {
    const bad = writeStyle(id, { filter_id: made.id, quick: { items: [], join: "and", invert: false } });
    if (bad) return { ok: false, text: bad, filterId: made.id };
    return { ok: true, text: `Фильтр «${made.name}» сохранён и выбран для модели; быстрый ограничитель очищен.`, filterId: made.id };
  }
  return { ok: true, text: `Фильтр «${made.name}» сохранён. Модель оставлена с прежним фильтром: выберите новый на вкладке «Фильтр».`, filterId: made.id };
}
