/**
 * Handlers of the display of block models (ТЗ 8.7.6; ADR 0023 §11, §13; docs/progress/e17-api.md §14, §16): `blocks.show`
 * (shown or hidden, «Оболочка / Все блоки»), `blocks.filter` (a filter of the library for the model), `blocks.quick`
 * (the quick restrictor «поле — оператор — значение»), `blocks.style` (the field, the scale, the breaks, the hidden
 * categories, the edges, the opacity, the limit of faces) and `blocks.pick` (the block at a point, as a click does);
 * the declarations are `commands.ts`. Every handler changes the style of the model in the view of the project
 * (`block_styles`, saved with the project, restored when it opens) or asks the core; the layer in the 3D window
 * (`scene.ts`, a lazy chunk) follows the view and asks the core again only for what changes the faces (`display.ts`
 * `requestKey`). The ribbon, Ctrl+K, the keys and the assistant run these same commands; a call without a model takes
 * the model selected in the tree, or the only one of the project. A failure is a Russian text with its hint, a missing
 * value is never 0.
 */
import type { App } from "../../shell/app";
import { CoreError } from "../../shell/api";
import { needProject, noted, refusal } from "../../shell/objectRun";
import { projectStore } from "../projects/state";
import { updateView, viewStore } from "../scene/bind";
import { onScene } from "../scene/sceneHost";
import type { BlockModelInfo } from "./api";
import { filterOf } from "./filters/bind";
import type { CondBody } from "./filters/constructor";
import {
  BLOCK_STYLES_KEY,
  blocksShowStore,
  colourIssue,
  hiddenCodes,
  kindFor,
  MAX_LIMIT,
  MIN_LIMIT,
  mergeStyle,
  numberOf,
  pickedText,
  quickIssue,
  resetShow,
  revealOnShow,
  setPicked,
  showText,
  styleIn,
  styleOp,
  type BlockStyle,
  type QuickRestrict,
  type StylePatch,
} from "./display";
import { lookupBlock } from "./pickBlock";
import { OPS } from "./commands";
import { blockById, blocksStore, loadBlocks } from "./state";

// ------------------------------------------------------------------------------------------------------- the handlers

const no = (code: string, message: string, hint: string): CoreError => refusal(code, message, hint);

/** The model of a call: its `model_id` (or the old `id`), else the one selected in the tree, else the only one. */
export async function modelOf(app: App, a: Record<string, unknown>): Promise<BlockModelInfo> {
  if (!blocksStore.get().loaded) await loadBlocks();
  const given = a.model_id ?? a.id;
  if (given !== undefined && given !== null) {
    const found = typeof given === "number" ? blockById(given) : undefined;
    if (!found) throw no("block_model_not_found", `Блочная модель №${String(given)} не найдена.`, "Номера моделей — в дереве проекта, раздел «Блочные модели».");
    return found;
  }
  const sel = app.store.get().selected;
  if (sel?.kind === "blocks") {
    const found = blockById(sel.id);
    if (found) return found;
  }
  const list = blocksStore.get().list;
  if (list.length === 1) return list[0];
  throw no("model_not_chosen", list.length ? "Не выбрана блочная модель." : "В проекте нет блочных моделей.", list.length ? "Выберите модель в дереве проекта или укажите model_id." : "Загрузите блочную модель: «+» у раздела «Блочные модели».");
}

const modeText = (s: BlockStyle): string => (s.mode === "all" ? "все блоки" : "оболочка");

/** Writes the patch into the view of the project and gives the style as it is now. */
function apply(id: number, patch: StylePatch): BlockStyle {
  updateView(BLOCK_STYLES_KEY, styleOp(id, patch));
  return styleIn(viewStore.get(), id);
}

/** A text of the restrictor: «AU > 1 И DOMAIN = 2», with «НЕ (…)» when inverted. */
export function quickText(q: QuickRestrict): string {
  if (!q.items.length) return "нет";
  const sym: Record<string, string> = { empty: "пусто", notempty: "не пусто", in: "в списке" };
  const one = (c: CondBody) => (c.op === "empty" || c.op === "notempty" ? `${c.field} ${sym[c.op]}` : c.op === "in" ? `${c.field} в списке (${(c.values ?? []).join("; ")})` : `${c.field} ${c.op} ${String(c.value ?? "")}`);
  const body = q.items.map(one).join(q.join === "or" ? " ИЛИ " : " И ");
  return q.invert ? `НЕ (${body})` : body;
}

const isGiven = (a: Record<string, unknown>, k: string): boolean => a[k] !== undefined;
const listOf = (text: unknown): string[] => (typeof text === "string" ? text.split(";").map((s) => s.trim()).filter(Boolean) : []);
/** A value of a condition: a number when it reads as one, else the label of a category. */
const scalarOf = (text: string): number | string => numberOf(text) ?? text.trim();

export async function runShow(app: App, a: Record<string, unknown>): Promise<string> {
  const model = await modelOf(app, a);
  const visible = a.visible !== false;
  const before = styleIn(viewStore.get(), model.id);
  if (visible && !before.visible) revealOnShow.add(model.id);
  const patch: StylePatch = { visible, ...(isGiven(a, "mode") && { mode: a.mode as BlockStyle["mode"] }) };
  const style = apply(model.id, patch);
  return visible ? `Блочная модель «${model.name}» показана в 3D (${modeText(style)})` : `Блочная модель «${model.name}» скрыта в 3D`;
}

export async function runFilter(app: App, a: Record<string, unknown>): Promise<string> {
  const model = await modelOf(app, a);
  if (!isGiven(a, "filter_id")) {
    const r = await app.registry.execute("filters.manage", {}, "user");
    return r.ok ? `Открыто окно «Фильтры»: выберите фильтр для модели «${model.name}» (blocks.filter с filter_id)` : r.result;
  }
  const before = styleIn(viewStore.get(), model.id);
  if (a.filter_id === null) {
    apply(model.id, { filter_id: null });
    return `Фильтр модели «${model.name}» снят`;
  }
  const f = await filterOf(a.filter_id); // 404 — «Фильтр №N не найден»
  if (!before.visible) revealOnShow.add(model.id);
  apply(model.id, { filter_id: f.id, visible: true });
  return `Фильтр «${f.name}» привязан к модели «${model.name}»; показаны блоки, которые он пропускает`;
}

export async function runQuick(app: App, a: Record<string, unknown>): Promise<string> {
  const model = await modelOf(app, a);
  const cur = styleIn(viewStore.get(), model.id).quick;
  let q: QuickRestrict = { ...cur, items: [...cur.items] };
  if (a.clear === true) q = { items: [], join: "and", invert: false };
  if (isGiven(a, "join")) q.join = a.join === "or" ? "or" : "and";
  if (isGiven(a, "invert")) q.invert = a.invert === true;
  if (isGiven(a, "field") || isGiven(a, "op") || isGiven(a, "value")) {
    const field = typeof a.field === "string" ? a.field.trim() : "";
    if (!field) throw no("bad_param", "У условия нет поля.", "Укажите поле модели, например AU.");
    if (!model.fields.some((f) => f.name === field)) throw no("block_field_not_found", `Поля «${field}» нет в модели «${model.name}».`, `Поля модели: ${model.fields.map((f) => f.name).join(", ") || "нет"}.`);
    const op = (typeof a.op === "string" ? a.op : ">") as CondBody["op"];
    if (!OPS.includes(op)) throw no("bad_param", `Оператор «${String(a.op)}» не известен.`, `Допустимы: ${OPS.join(" ")}.`);
    const text = typeof a.value === "string" ? a.value : isGiven(a, "value") ? String(a.value) : "";
    const cond: CondBody = { field, op };
    if (op === "in") cond.values = listOf(text).map(scalarOf);
    else if (op !== "empty" && op !== "notempty") {
      if (!text.trim()) throw no("bad_param", "У условия нет значения.", "Введите число или подпись категории, например 0,5.");
      cond.value = scalarOf(text);
    }
    q.items = a.append === true ? [...q.items, cond] : [cond];
  }
  const issue = quickIssue(q);
  if (issue) throw no("bad_param", issue, "Проверьте условие ограничителя.");
  apply(model.id, { quick: q });
  return q.items.length ? `Быстрый ограничитель модели «${model.name}»: ${quickText(q)}` : `Быстрый ограничитель модели «${model.name}» снят`;
}

export async function runStyle(app: App, a: Record<string, unknown>): Promise<string> {
  const model = await modelOf(app, a);
  const cur = styleIn(viewStore.get(), model.id);
  const patch: StylePatch = {};
  const colour: NonNullable<StylePatch["colour"]> = {};
  const num = (k: string, what: string): number | null => {
    if (a[k] === null) return null;
    const n = numberOf(a[k]);
    if (n === null) throw no("bad_param", `${what}: нужно число.`, "Например 0,5.");
    return n;
  };
  if (isGiven(a, "field")) {
    colour.field = a.field === null || a.field === "" ? null : String(a.field);
    const f = model.fields.find((x) => x.name === colour.field);
    if (colour.field && !f) throw no("block_field_not_found", `Поля «${colour.field}» нет в модели «${model.name}».`, `Поля модели: ${model.fields.map((x) => x.name).join(", ") || "нет"}.`);
    // a field given without a kind: a scale for a number, the codes for a category; no field — no colouring
    if (!isGiven(a, "kind")) colour.kind = colour.field ? kindFor(cur.colour.kind === "none" ? "scale" : cur.colour.kind, f) : "none";
  }
  if (isGiven(a, "kind")) colour.kind = a.kind as NonNullable<StylePatch["colour"]>["kind"];
  if (isGiven(a, "from")) colour.from = num("from", "Граница шкалы «от»");
  if (isGiven(a, "to")) colour.to = num("to", "Граница шкалы «до»");
  if (isGiven(a, "log")) colour.log = a.log === true;
  if (isGiven(a, "breaks")) {
    const parts = listOf(a.breaks);
    const nums = parts.map((p) => numberOf(p));
    if (nums.some((n) => n === null)) throw no("bad_param", "Границы градаций: нужны числа через «;».", "Например 0,5; 1; 2.");
    colour.breaks = nums as number[];
  }
  if (isGiven(a, "hidden")) {
    const codes = listOf(a.hidden).map((p) => numberOf(p));
    if (codes.some((n) => n === null || !Number.isInteger(n) || n < 0)) throw no("bad_param", "Скрытые категории: нужны коды (целые числа) через «;».", "Например 1; 3.");
    const off = new Set(codes as number[]);
    const cats = { ...cur.colour.categories };
    for (const code of new Set([...Object.keys(cats).map(Number), ...off])) cats[String(code)] = { colour: cats[String(code)]?.colour ?? null, on: !off.has(code) };
    colour.categories = cats;
  }
  if (Object.keys(colour).length) patch.colour = colour;
  if (isGiven(a, "mode")) patch.mode = a.mode as BlockStyle["mode"];
  if (isGiven(a, "opacity")) {
    const o = num("opacity", "Непрозрачность");
    if (o === null || o < 0.05 || o > 1) throw no("bad_param", "Непрозрачность — число от 0,05 до 1.", "Например 0,6.");
    patch.opacity = o;
  }
  if (isGiven(a, "edges") || isGiven(a, "edge_color")) {
    const color = isGiven(a, "edge_color") ? /^#[0-9a-f]{6}$/i.exec(String(a.edge_color))?.[0].toLowerCase() : undefined;
    if (isGiven(a, "edge_color") && !color) throw no("bad_param", "Цвет рёбер не в виде #RRGGBB.", "Например #1B222B.");
    patch.edges = { ...(isGiven(a, "edges") && { on: a.edges === true }), ...(color && { colour: color }) };
  }
  if (isGiven(a, "limit")) {
    const l = a.limit;
    if (typeof l !== "number" || !Number.isInteger(l) || l < MIN_LIMIT || l > MAX_LIMIT) throw no("bad_param", "Лимит граней: от 500 000 до 10 000 000.", "Задайте число граней в этих пределах.");
    patch.limit = l;
  }
  const next = mergeStyle(cur, patch);
  const issue = colourIssue(next.colour, model);
  if (issue) throw no("bad_param", issue, "Проверьте настройки раскраски.");
  if (!Object.keys(patch).length) return `Раскраска модели «${model.name}» не изменена: ничего не указано`;
  apply(model.id, patch);
  const c = next.colour;
  const kinds = { none: "", scale: "шкала", classes: "градации", categories: "категории" };
  const how = c.kind === "none" || !c.field ? "одним цветом" : `по полю ${c.field} (${kinds[c.kind]})`;
  const off = c.kind === "categories" ? hiddenCodes(c).length : 0;
  return `Блочная модель «${model.name}»: ${how}, ${modeText(next)}${off ? `, скрытых категорий ${off}` : ""}`;
}

export async function runPick(app: App, a: Record<string, unknown>): Promise<string> {
  const model = await modelOf(app, a);
  const point = (["x", "y", "z"] as const).map((k) => numberOf(a[k]));
  if (point.some((v) => v === null)) throw no("bad_param", "Координаты точки — три числа x, y, z.", "Например x=512120,4 y=7100010,6 z=262,7.");
  const fields = isGiven(a, "fields") ? listOf(a.fields) : undefined;
  const got = await lookupBlock(model.id, point as [number, number, number], fields?.length ? { fields } : {});
  if (got.status === "error") throw no("pick_failed", got.error ?? "Блок не найден.", "");
  return pickedText(got, model);
}

// ------------------------------------------------------------------------------------------------------------ binding

/**
 * Binds the handlers, the status notes and the layers of the scene; gives what unbinds them. A failure of the display
 * of a model (a filter the core refused, a field that is gone) is said in the status bar once, with the hint.
 */
export function bindShow(app: App): () => void {
  const r = app.registry;
  r.bind("blocks.show", noted(app, (a) => runShow(app, a)), needProject);
  r.bind("blocks.filter", noted(app, (a) => runFilter(app, a)), needProject);
  r.bind("blocks.quick", noted(app, (a) => runQuick(app, a)), needProject);
  r.bind("blocks.style", noted(app, (a) => runStyle(app, a)), needProject);
  r.bind("blocks.pick", noted(app, (a) => runPick(app, a)), needProject);
  const stops: (() => void)[] = [];
  let uid: string | null = projectStore.get().current?.uid ?? null;
  stops.push(
    projectStore.subscribe(() => {
      const cur = projectStore.get().current?.uid ?? null;
      if (cur === uid) return;
      uid = cur;
      resetShow();
      setPicked(null);
      revealOnShow.clear();
    }),
  );
  // a failed display is said once in the status bar
  const said = new Map<number, string>();
  stops.push(
    blocksShowStore.subscribe(() => {
      for (const info of Object.values(blocksShowStore.get().models)) {
        const was = said.get(info.model);
        if (info.status === "error" && info.error && was !== info.error) {
          said.set(info.model, info.error);
          app.store.set({ statusNote: showText(info, blockById(info.model)?.name ?? `№${info.model}`) });
        } else if (info.status !== "error") said.delete(info.model);
      }
    }),
  );
  // the layers: a lazy chunk with three.js, attached to every new scene
  let detach: (() => void) | null = null;
  let generation = 0;
  stops.push(
    onScene((scene) => {
      const mine = ++generation;
      detach?.();
      detach = null;
      if (!scene) return;
      void import("./scene").then((m) => {
        if (mine !== generation) return;
        detach = m.attachBlocksScene(scene, (id) => {
          const sel = app.store.get().selected;
          if (sel?.kind === "blocks" && sel.id === id) return;
          if (app.registry.reason("object.select") === null) void app.run("object.select", { kind: "blocks", id });
        });
      });
    }),
  );
  return () => {
    generation++;
    detach?.();
    detach = null;
    stops.forEach((fn) => fn());
  };
}

