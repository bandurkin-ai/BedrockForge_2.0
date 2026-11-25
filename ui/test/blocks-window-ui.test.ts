// The window «Блочная модель · …» (ТЗ 8.7.6, 6.8; ADR 0023 §11, §13; Э17 17.34): the command `blocks.settings` (a record of the registry, one
// window per model, follows the project), the five tabs drawn from the REAL answers of the core (`window_answers.json`: the details of the
// model turned by 30° with sub-blocks, the library of filters; `viz_answers.json`: the headers with the resolved range, the bins, the labels;
// `layer_fixture.json`: LOD), the restrictor of the tab «Быстрый ограничитель» and «Сохранить быстрый ограничитель как фильтр» through
// `filter.save {name, rules}` with the answers of the core to it (the same tree it accepted, a name in use — 409 in Russian).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext } from "../src/shell/AppContext";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { windowStore } from "../src/shell/windows";
import type { BlockModelInfo } from "../src/modules/blocks/api";
import { BLOCK_STYLES_KEY, blocksShowStore, colourIssue, resetShow, setPicked, setShowInfo, showText, styleIn, type StylePatch, styleOp } from "../src/modules/blocks/display";
import { filtersStore } from "../src/modules/blocks/filters/state";
import { blocksStore } from "../src/modules/blocks/state";
import { projectStore } from "../src/modules/projects/state";
import { updateView, viewStore } from "../src/modules/scene/bind";
import { decodeBlocks, type BlocksHeader } from "../src/viz/blocks/decode";
import { saveQuickAsFilter } from "../src/modules/blocks/window/actions";
import { bindModelWindow, openModelWindow, renderModelWindow, windowOfModel } from "../src/modules/blocks/window/bind";
import { commands, SETTINGS_BUTTONS } from "../src/modules/blocks/window/commands";
import { commitQuickRows, setInvert, setJoin } from "../src/modules/blocks/window/edits";
import { FilterPanel } from "../src/modules/blocks/window/FilterPanel";
import { LegendPanel } from "../src/modules/blocks/window/LegendPanel";
import { ModelWindow } from "../src/modules/blocks/window/ModelWindow";
import { PropsPanel } from "../src/modules/blocks/window/PropsPanel";
import { QuickPanel } from "../src/modules/blocks/window/QuickPanel";
import { blankRow, itemsKey, rowIssues, rowsOf } from "../src/modules/blocks/window/quick";
import { MODEL_WINDOW, modelTitle, TABS } from "../src/modules/blocks/window/state";
import { StylePanel } from "../src/modules/blocks/window/StylePanel";
import { readDetails, rotationText, subText } from "../src/modules/blocks/window/details";

const here = (p: string) => JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/blocks", p), "utf-8"));
const VIZ = here("viz_answers.json") as { cases: { name: string; header?: BlocksHeader; json?: unknown }[] };
const LAYER = here("layer_fixture.json") as { cases: { name: string; bfa1: string }[] };
const WIN = here("window_answers.json");
const CONTRACT = here("contract_answers.json");
const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const header = (name: string): BlocksHeader => VIZ.cases.find((c) => c.name === name)!.header!;

const IDS = WIN.ids as { turned: number; plain: number };
const LIST = WIN.blocks.body.block_models as BlockModelInfo[];
const TURNED = LIST.find((b) => b.id === IDS.turned)!;
const PLAIN = LIST.find((b) => b.id === IDS.plain)!;
const T = WIN.turned.body as BlockModelInfo;
const P = WIN.plain.body as BlockModelInfo;
const ROWS = WIN.filters.list.body.filters as { id: number; name: string; title: string; fields: string[] }[];
const SAVED = WIN.filters.saved;
const NAME = SAVED.body.name as string;

let app: App;
const sent: { method: string; path: string; body: Record<string, unknown> }[] = [];
let filterAnswer: (body: Record<string, unknown>) => Response;

beforeEach(() => {
  sent.length = 0;
  windowStore.set({ windows: [] });
  filterAnswer = () => json(SAVED.body);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url).pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      sent.push({ method, path, body });
      if (path.startsWith("/view/")) return json({ version: 1, value: body.value });
      if (path === "/filters" && method === "GET") return json({ filters: ROWS });
      if (path === "/filters" && method === "POST") return filterAnswer(body);
      return json({ code: "not_found", message: "Нет", hint: "" }, 404);
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  viewStore.set({ [BLOCK_STYLES_KEY]: undefined });
  app = createApp({ persist: false });
  app.store.set({ projectName: "Проект", projectReadOnly: false, selected: null });
  if (!app.registry.get("blocks.settings")) app.registry.register(commands); // the catalog names modules/blocks/window now
  bindModelWindow(app);
  blocksStore.set({ list: [TURNED, PLAIN], loaded: true, error: null, details: { [IDS.turned]: T, [IDS.plain]: P }, detailErrors: {} });
  filtersStore.set({ rows: [], loaded: true, selected: null, edit: null });
  resetShow();
  setPicked(null);
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
  projectStore.set({ current: null, version: 0 });
});

const style = (id: number) => styleIn(viewStore.get(), id);
const patch = (id: number, p: StylePatch) => updateView(BLOCK_STYLES_KEY, styleOp(id, p));
const html = (el: ReactElement) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));
/** The opening tag of the element with a test id. */
const tag = (h: string, id: string): string => new RegExp(`<[a-z]+ [^>]*data-testid="${id}"[^>]*>`).exec(h)?.[0] ?? "";
const has = (h: string, id: string): boolean => tag(h, id) !== "";
/** The text of the element with a test id: its inner HTML up to its own closing tag, without tags. */
const text = (h: string, id: string): string => {
  const open = new RegExp(`<([a-z]+) [^>]*data-testid="${id}"[^>]*>`).exec(h);
  if (!open) return "";
  const name = open[1];
  let depth = 1;
  let at = open.index + open[0].length;
  const re = new RegExp(`<(/?)${name}[ >]`, "g");
  re.lastIndex = at;
  for (let m = re.exec(h); m; m = re.exec(h)) {
    depth += m[1] ? -1 : 1;
    if (depth === 0) return h.slice(at, m.index).replace(/<[^>]+>/g, "");
  }
  return h.slice(at).replace(/<[^>]+>/g, "");
};
const attr = (h: string, id: string, a: string): string | null => new RegExp(`${a}="([^"]*)"`).exec(tag(h, id))?.[1] ?? null;
const disabled = (h: string, id: string): boolean => /\sdisabled(=|\s|>)/.test(tag(h, id));
const unescape = (s: string) => s.replace(/&gt;/g, ">").replace(/&lt;/g, "<").replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&#x27;/g, "'");

describe("blocks.settings", () => {
  it("is a record of the registry (ADR 0023 §13): Russian, bound, `model_id` only, the ribbon button names it", () => {
    const c = commands[0];
    expect(c.id).toBe("blocks.settings");
    expect(Object.keys(c.params!)).toEqual(["model_id"]);
    expect(c.uiOnly).toBe(true);
    expect(c.title).toMatch(/[А-Яа-я]/);
    expect(c.description.length).toBeGreaterThan(60);
    expect(app.registry.get("blocks.settings")!.run).toBeTypeOf("function");
    expect(app.registry.reason("blocks.settings")).toBeNull();
    for (const b of SETTINGS_BUTTONS) expect(app.registry.get(b.command)).toBeDefined();
    app.store.set({ projectName: null });
    expect(app.registry.reason("blocks.settings")).toBe("Откройте или создайте проект");
  });

  it("opens the window of the model: «Блочная модель · name»; the same model again brings it forward (one window per model)", async () => {
    const r = await app.registry.execute("blocks.settings", { model_id: IDS.turned });
    expect(r).toEqual({ ok: true, result: `Открыто окно «Блочная модель · ${TURNED.name}»` });
    const wins = windowStore.get().windows.filter((w) => w.kind === MODEL_WINDOW);
    expect(wins).toHaveLength(1);
    expect(wins[0].title).toBe(modelTitle(TURNED.name));
    expect(wins[0].props).toEqual({ model: IDS.turned });
    await app.registry.execute("blocks.settings", { model_id: IDS.turned });
    expect(windowStore.get().windows).toHaveLength(1);
    await app.registry.execute("blocks.settings", { model_id: IDS.plain });
    expect(windowStore.get().windows).toHaveLength(2);
    expect(windowOfModel(IDS.plain)).not.toBeNull();
    expect(openModelWindow(IDS.plain)).toBe(windowOfModel(IDS.plain));
  });

  it("the model of a call without one is the selected one or the only one; none — a refusal in Russian; the assistant does not open windows", async () => {
    app.store.set({ selected: { kind: "blocks", id: IDS.plain } });
    expect((await app.registry.execute("blocks.settings", {})).result).toContain(PLAIN.name);
    app.store.set({ selected: null });
    const none = await app.registry.execute("blocks.settings", {});
    expect(none).toEqual({ ok: false, result: "Не выбрана блочная модель. Выберите модель в дереве проекта или укажите model_id." });
    expect((await app.registry.execute("blocks.settings", { model_id: 99 })).result).toBe("Блочная модель №99 не найдена. Номера моделей — в дереве проекта, раздел «Блочные модели».");
    expect((await app.registry.execute("blocks.settings", { model_id: IDS.turned }, "claude")).result).toBe("Команда доступна только из интерфейса");
  });

  it("follows the project: a rename renames the window, a model that is gone closes it, another project closes them all", async () => {
    await app.registry.execute("blocks.settings", { model_id: IDS.turned });
    await app.registry.execute("blocks.settings", { model_id: IDS.plain });
    blocksStore.set({ list: [{ ...TURNED, name: "Новая" }, PLAIN] });
    expect(windowStore.get().windows.find((w) => w.props?.model === IDS.turned)!.title).toBe("Блочная модель · Новая");
    blocksStore.set({ list: [PLAIN] });
    expect(windowOfModel(IDS.turned)).toBeNull();
    expect(windowOfModel(IDS.plain)).not.toBeNull();
    projectStore.set({ current: { uid: "p1" } as never, version: 1 });
    projectStore.set({ current: { uid: "p2" } as never, version: 2 });
    expect(windowStore.get().windows).toEqual([]);
  });

  it("the window layer draws it: the five tabs of the ТЗ, the first one «Показ» (or the one asked); another kind of window is not ours", async () => {
    await app.registry.execute("blocks.settings", { model_id: IDS.turned });
    const w = windowStore.get().windows[0];
    const out = html(renderModelWindow(w) as ReactElement);
    expect(TABS.map((t) => t.title)).toEqual(["Показ", "Фильтр", "Быстрый ограничитель", "Легенда", "Свойства"]);
    for (const t of TABS) expect(text(out, `model-tab-${t.id}`)).toBe(t.title);
    expect(attr(out, "model-window", "data-tab")).toBe("show");
    expect(has(out, "style-panel")).toBe(true);
    const other = html(renderModelWindow({ ...w, props: { model: IDS.turned, tab: "props" } }) as ReactElement);
    expect(has(other, "props-panel")).toBe(true);
    expect(renderModelWindow({ ...w, kind: "filters" })).toBeNull();
    expect(html(createElement(ModelWindow, { winId: "w", modelId: 77 }))).toContain("больше нет в проекте");
  });
});

describe("«Показ»", () => {
  it("comes back as it was written: the field, the range, the logarithm, the mode, the limit, the edges", () => {
    patch(IDS.turned, { visible: true, mode: "all", limit: 2_500_000, edges: { on: false, width: 1.5 }, colour: { field: "AU", kind: "scale", from: 0.1, to: 10, log: true } });
    const h = html(createElement(StylePanel, { id: IDS.turned }));
    expect(attr(h, "style-field", "data-testid")).toBe("style-field");
    expect(h).toMatch(/<option value="AU" selected="">AU, g\/t<\/option>/);
    expect(attr(h, "style-from", "value")).toBe("0,1");
    expect(attr(h, "style-to", "value")).toBe("10");
    expect(h).toMatch(/type="checkbox" checked=""/);
    expect(attr(h, "style-mode-all", "aria-pressed")).toBe("true");
    expect(attr(h, "style-mode-shell", "aria-pressed")).toBe("false");
    expect(attr(h, "style-limit", "value")).toBe("2,5");
    expect(attr(h, "style-edge-width", "value")).toBe("1,5");
    expect(h).toMatch(/<option value="off" selected="">нет<\/option>/);
    expect(text(h, "style-show")).toBe("Скрыть");
    expect(attr(h, "style-kind-scale", "aria-pressed")).toBe("true");
  });

  it("«Взять из данных» is enabled once the core answered, and says why not before; the status tells the faces and the time", () => {
    patch(IDS.turned, { visible: true, colour: { field: "AU", kind: "scale" } });
    let h = html(createElement(StylePanel, { id: IDS.turned }));
    expect(disabled(h, "style-take")).toBe(true);
    expect(unescape(attr(h, "style-take", "title")!)).toBe("Границы придут от ядра после показа: включите «Показать в 3D».");
    expect(attr(h, "style-status", "data-state")).toBe("loading");
    setShowInfo(IDS.turned, { status: "ready", header: header("shell_scale_turned"), maxClass: 255, ms: 143, requests: 2 });
    h = html(createElement(StylePanel, { id: IDS.turned }));
    expect(disabled(h, "style-take")).toBe(false);
    expect(attr(h, "style-status", "data-state")).toBe("ready");
    expect(text(h, "style-status")).toBe(showText(blocksShowStore.get().models[IDS.turned], TURNED.name));
    expect(text(h, "style-time")).toBe("Последний запрос 143 мс; запросов к ядру: 2.");
    expect(text(h, "style-range")).toContain("Сейчас шкала: от 0,1 до 10");
    expect(has(h, "style-lod")).toBe(false);
  });

  it("«упрощено, LOD» is said under the status with the parents and the faces of the answer", () => {
    const lod = decodeBlocks(Buffer.from(LAYER.cases.find((c) => c.name === "lod")!.bfa1, "base64"));
    patch(IDS.turned, { visible: true });
    setShowInfo(IDS.turned, { status: "ready", header: lod.header, maxClass: lod.maxClass, ms: 90 });
    const h = html(createElement(StylePanel, { id: IDS.turned }));
    expect(text(h, "style-lod")).toMatch(/^Упрощено \(LOD \d\): родители \d × \d × \d, граней до упрощения [\d   ]+, показано/);
    expect(text(h, "style-status")).toContain("упрощено");
  });

  it("the breaks and the classes with their ranges; the categories with the colour, «показать» and the blocks of each", () => {
    patch(IDS.turned, { colour: { field: "AU", kind: "classes", breaks: [0.5, 1, 2] } });
    let h = html(createElement(StylePanel, { id: IDS.turned }));
    expect(attr(h, "style-breaks", "value")).toBe("0,5; 1; 2");
    expect([0, 1, 2, 3].every((k) => has(h, `style-class-${k}`))).toBe(true);
    expect(has(h, "style-class-4")).toBe(false);
    expect(h).toContain("до 0,5");
    expect(h).toContain("от 2");
    patch(IDS.turned, { colour: { field: "ROCK", kind: "categories", breaks: [], categories: { "1": { colour: null, on: false }, "2": { colour: "#e07020", on: true } } } });
    setShowInfo(IDS.turned, { status: "ready", header: header("shell_categories_hidden"), maxClass: 4 });
    h = html(createElement(StylePanel, { id: IDS.turned }));
    expect([0, 1, 2, 3, 4].every((k) => has(h, `style-cat-${k}`))).toBe(true);
    expect(attr(h, "style-cat-1", "data-on")).toBe("false");
    expect(attr(h, "style-cat-0", "data-on")).toBe("true");
    for (const label of ["руда", "порода", "окисленная", "дайка", "вода"]) expect(h).toContain(label);
    expect(h).toMatch(/value="#e07020"/);
    expect(h).toMatch(/\d+ блоков/);
    expect(has(h, "style-palette")).toBe(true);
    expect(h).toContain("Фирменные цвета");
    expect(h).not.toContain("Виридис"); // a ramp does not colour categories
  });

  it("a wrong stored colouring is told at its field in the words of the layer's status, with the field marked", () => {
    patch(IDS.turned, { colour: { field: "AU", kind: "scale", from: 5, to: 1 } });
    const h = html(createElement(StylePanel, { id: IDS.turned }));
    expect(text(h, "style-issue-to")).toBe(colourIssue(style(IDS.turned).colour, T));
    expect(attr(h, "style-to", "aria-invalid")).toBe("true");
    expect(attr(h, "style-from", "aria-invalid")).toBeNull();
    patch(IDS.turned, { colour: { field: "AU", kind: "classes", breaks: [] } });
    const b = html(createElement(StylePanel, { id: IDS.turned }));
    expect(text(b, "style-issue-breaks")).toBe("Границы градаций: от 1 до 255 чисел строго по возрастанию. Например 0,5; 1; 2.");
    patch(IDS.turned, { colour: { field: "GONE", kind: "scale" } });
    const f = html(createElement(StylePanel, { id: IDS.turned }));
    expect(text(f, "style-issue-field")).toBe("Поля «GONE» нет в этой модели. Выберите поле из списка.");
    expect(f).toContain("GONE (нет в модели)");
  });
});

describe("«Фильтр»", () => {
  beforeEach(() => filtersStore.set({ rows: ROWS as never, loaded: true, selected: null, edit: null }));

  it("the library of the project; the filter chosen is selected with its text; one that was deleted is told, not hidden", () => {
    patch(IDS.turned, { filter_id: ROWS[0].id });
    let h = html(createElement(FilterPanel, { id: IDS.turned }));
    expect(h).toContain(`<option value="${ROWS[0].id}" selected="">${ROWS[0].name}</option>`);
    expect(unescape(text(h, "filter-title"))).toContain(unescape(ROWS[0].title));
    expect(has(h, "filter-lost")).toBe(false);
    expect(disabled(h, "filter-manage")).toBe(false);
    patch(IDS.turned, { filter_id: 99 });
    h = html(createElement(FilterPanel, { id: IDS.turned }));
    expect(h).toContain('<option value="99" selected="">№99 (нет в проекте)</option>');
    expect(text(h, "filter-lost")).toBe("Фильтр №99 удалён из проекта. Выберите другой фильтр или «— без фильтра —».");
  });

  it("«Сохранить быстрый ограничитель как фильтр» is off with its reason while the restrictor is empty", () => {
    const h = html(createElement(FilterPanel, { id: IDS.turned }));
    expect(disabled(h, "filter-save-quick")).toBe(true);
    expect(unescape(attr(h, "filter-save-quick", "title")!)).toBe("Быстрый ограничитель пуст: добавьте условие на вкладке «Быстрый ограничитель».");
    expect(attr(h, "filter-name", "value")).toBe(`Ограничитель · ${TURNED.name}`);
    patch(IDS.turned, { quick: { items: [{ field: "OK", op: ">", value: 0.5 }] } });
    expect(disabled(html(createElement(FilterPanel, { id: IDS.turned })), "filter-save-quick")).toBe(false);
  });

  it("saves the restrictor through `filter.save {name, rules}` — the very tree the core accepted — and takes the filter for the model", async () => {
    patch(IDS.turned, { quick: { items: [{ field: "OK", op: ">", value: 0.5 }, { field: "AU", op: ">=", value: 1.5 }], join: "and", invert: false } });
    const r = await saveQuickAsFilter(app, IDS.turned, NAME);
    expect(r).toMatchObject({ ok: true, filterId: SAVED.body.id });
    const post = sent.find((s) => s.method === "POST" && s.path === "/filters")!;
    expect(post.body).toEqual({ name: NAME, rules: WIN.quick.rules });
    expect(style(IDS.turned).filter_id).toBe(SAVED.body.id);
    expect(style(IDS.turned).quick).toEqual({ items: [], join: "and", invert: false });
    expect(r.text).toBe(`Фильтр «${NAME}» сохранён и выбран для модели; быстрый ограничитель очищен.`);
  });

  it("an inverted restrictor is a НЕ over the tree; a model that has a filter keeps it (the new one is only in the library)", async () => {
    patch(IDS.turned, { filter_id: ROWS[0].id, quick: { items: [{ field: "OK", op: ">", value: 0.5 }, { field: "AU", op: ">=", value: 1.5 }], join: "and", invert: true } });
    filterAnswer = () => json(WIN.filters.inverted.body);
    const r = await saveQuickAsFilter(app, IDS.turned, WIN.filters.inverted.body.name);
    expect(sent.find((s) => s.method === "POST" && s.path === "/filters")!.body.rules).toEqual(WIN.quick.inverted);
    expect(r.ok).toBe(true);
    expect(style(IDS.turned).filter_id).toBe(ROWS[0].id);
    expect(style(IDS.turned).quick.items).toHaveLength(2);
    expect(r.text).toContain("Модель оставлена с прежним фильтром");
  });

  it("refusals are Russian: a name in use (409 of the core), no name, an empty or a wrong restrictor — nothing is written", async () => {
    patch(IDS.turned, { quick: { items: [{ field: "OK", op: ">", value: 0.5 }] } });
    filterAnswer = () => json(WIN.filters.twice.body, 409);
    const used = await saveQuickAsFilter(app, IDS.turned, NAME);
    expect(used.ok).toBe(false);
    expect(used.text).toBe(`${WIN.filters.twice.body.message} ${WIN.filters.twice.body.hint}`);
    expect(style(IDS.turned).filter_id).toBeNull();
    expect((await saveQuickAsFilter(app, IDS.turned, "  ")).text).toBe("У фильтра нет имени. Введите имя, например «Руда выше борта».");
    patch(IDS.turned, { quick: { items: [] } });
    expect((await saveQuickAsFilter(app, IDS.turned, NAME)).text).toBe("Быстрый ограничитель пуст: добавьте условие на вкладке «Быстрый ограничитель».");
    patch(IDS.turned, { quick: { items: [{ field: "OK", op: ">" }] } });
    expect((await saveQuickAsFilter(app, IDS.turned, NAME)).ok).toBe(false);
    expect(sent.filter((s) => s.method === "POST" && s.path === "/filters")).toHaveLength(1);
  });
});

describe("«Быстрый ограничитель»", () => {
  const row = (field: string, op: ">" | "=" | "in", value = "", list = "") => ({ field, op, value, list });

  it("the rows of the stored restrictor: field, operator, value; «И»/«ИЛИ», «обратный» and the text of the whole", () => {
    patch(IDS.turned, { quick: { items: [{ field: "OK", op: ">", value: 0.5 }, { field: "ROCK", op: "in", values: ["руда", "порода"] }], join: "or", invert: true } });
    const h = html(createElement(QuickPanel, { id: IDS.turned }));
    expect(h).toMatch(/<option value="OK" selected="">OK<\/option>/);
    expect(attr(h, "quick-row-0-value", "value")).toBe("0.5");
    expect(h).toMatch(/<option value="in" selected="">в списке<\/option>/);
    expect(h).toContain("руда\nпорода");
    expect(attr(h, "quick-join-or", "aria-pressed")).toBe("true");
    expect(attr(h, "quick-join-and", "aria-pressed")).toBe("false");
    expect(tag(h, "quick-invert")).toContain('checked=""');
    expect(unescape(text(h, "quick-summary"))).toBe("Быстрый ограничитель: НЕ (OK > 0.5 ИЛИ ROCK в списке (руда; порода)).");
    expect(has(html(createElement(QuickPanel, { id: IDS.plain })), "quick-row-0")).toBe(false);
  });

  it("the passed blocks of the last answer are shown; a field that is not in the model is told under its row", () => {
    patch(IDS.turned, { quick: { items: [{ field: "ZZ", op: ">", value: 1 }] } });
    setShowInfo(IDS.turned, { status: "ready", header: header("shell_scale_turned"), maxClass: 255 });
    const h = html(createElement(QuickPanel, { id: IDS.turned }));
    expect(text(h, "quick-row-0-issue-field")).toBe(`Поля «ZZ» нет в модели «${TURNED.name}». Поля модели: AU, OK, FLAT, EMPTYF, ROCK.`);
    expect(attr(h, "quick-row-0-field", "aria-invalid")).toBe("true");
    expect(text(h, "quick-passed")).toBe("Сейчас прошло 211 из 225 блоков.");
  });

  it("rows become the restrictor when every row is right; a blank row waits; a wrong one writes nothing and says where", () => {
    expect(rowIssues([blankRow()], T)).toEqual([]);
    const ok = commitQuickRows(IDS.turned, [row("OK", ">", "0,5"), blankRow(), row("ROCK", "in", "", "руда\nпорода\n")], T);
    expect(ok).toEqual({ written: true, issues: [], failure: null });
    expect(style(IDS.turned).quick.items).toEqual([{ field: "OK", op: ">", value: 0.5 }, { field: "ROCK", op: "in", values: ["руда", "порода"] }]);
    expect(rowsOf(style(IDS.turned).quick)[1]).toEqual({ field: "ROCK", op: "in", value: "", list: "руда\nпорода" });
    const before = itemsKey(style(IDS.turned).quick);
    const bad = commitQuickRows(IDS.turned, [row("OK", ">", "0,5"), row("AU", ">", "много"), row("", ">", "3"), row("NOPE", "=", "1")], T);
    expect(bad.written).toBe(false);
    expect(bad.issues.map((i) => [i.row, i.part])).toEqual([
      [1, "value"],
      [2, "field"],
      [3, "field"],
    ]);
    expect(bad.issues[0].text).toBe("Значение для сравнения по порядку — число. Введите число, например 0,5 или 12.");
    expect(bad.issues[2].text).toBe(`Поля «NOPE» нет в модели «${TURNED.name}». Поля модели: AU, OK, FLAT, EMPTYF, ROCK.`);
    expect(itemsKey(style(IDS.turned).quick)).toBe(before);
    expect(commitQuickRows(IDS.turned, [row("AU", "=", "")], T).issues[0]).toMatchObject({ part: "value", text: "У условия нет значения. Введите значение для сравнения." });
    expect(commitQuickRows(IDS.turned, [], T).written).toBe(true);
    expect(style(IDS.turned).quick.items).toEqual([]);
  });

  it("«И» / «ИЛИ» and «обратный» are written at once and keep the conditions", () => {
    commitQuickRows(IDS.turned, [row("OK", ">", "0,5"), row("AU", ">", "1")], T);
    expect(setJoin(IDS.turned, "or")).toBeNull();
    expect(setInvert(IDS.turned, true)).toBeNull();
    expect(style(IDS.turned).quick).toEqual({ items: [{ field: "OK", op: ">", value: 0.5 }, { field: "AU", op: ">", value: 1 }], join: "or", invert: true });
  });
});

describe("«Легенда»", () => {
  const info = (name: string, id = IDS.turned) => setShowInfo(id, { status: "ready", header: header(name), maxClass: header(name).classes - 1 });

  it("a scale: the strip with the resolved ends of the answer, «нет значения» in its own grey, never the colour of zero", () => {
    patch(IDS.turned, { colour: { field: "AU", kind: "scale", from: 0.1, to: 10, log: true } });
    info("shell_scale_turned");
    const h = html(createElement(LegendPanel, { id: IDS.turned }));
    expect(attr(h, "legend-panel", "data-state")).toBe("scale");
    expect(text(h, "legend-from")).toBe("0,1");
    expect(text(h, "legend-to")).toBe("10");
    expect(tag(h, "legend-strip")).toContain("linear-gradient");
    expect(text(h, "legend-nodata")).toContain("нет значения");
    expect(h).toContain("#9aa3b5");
    expect(text(h, "legend-range")).toContain("логарифмическая");
    expect(text(h, "legend-facts")).toContain("Прошло 211 из 225 блоков");
  });

  it("breaks: the ranges of the bins with the colours of the style; categories: the labels, a hidden one struck out", () => {
    patch(IDS.turned, { colour: { field: "AU", kind: "classes", breaks: [0.5, 1, 2], colours: ["#112233"] } });
    info("shell_classes");
    let h = html(createElement(LegendPanel, { id: IDS.turned }));
    expect([0, 1, 2, 3].map((k) => text(h, `legend-item-${k}`))).toEqual(["до 0,5", "от 0,5 до 1", "от 1 до 2", "от 2"]);
    expect(h).toContain("rgb(17,34,51)");
    patch(IDS.turned, { colour: { field: "ROCK", kind: "categories", categories: { "1": { colour: null, on: false } } } });
    info("shell_categories_hidden");
    h = html(createElement(LegendPanel, { id: IDS.turned }));
    expect([0, 1, 2, 3, 4].map((k) => text(h, `legend-item-${k}`).replace("скрыта", ""))).toEqual(["руда", "порода", "окисленная", "дайка", "вода"]);
    expect(attr(h, "legend-item-1", "data-hidden")).toBe("true");
    expect(attr(h, "legend-item-0", "data-hidden")).toBeNull();
    expect(text(h, "legend-item-1")).toContain("скрыта");
  });

  it("no colouring, no answer yet and a failed show are said, not drawn", () => {
    expect(html(createElement(LegendPanel, { id: IDS.turned }))).toContain("Легенда появится после «Показать в 3D»");
    patch(IDS.turned, { visible: true });
    expect(html(createElement(LegendPanel, { id: IDS.turned }))).toContain("Легенда появится, когда ядро ответит");
    info("shell_all_none");
    expect(html(createElement(LegendPanel, { id: IDS.turned }))).toContain("Раскраски нет: блоки показаны одним цветом");
    setShowInfo(IDS.turned, { status: "error", error: "Фильтр не найден." });
    expect(html(createElement(LegendPanel, { id: IDS.turned }))).toContain("Показ не удался");
  });
});

describe("«Свойства»", () => {
  it("the turn, the sub-blocks, the roles of the fields and the findings of the checks of the real answer", () => {
    const h = html(createElement(PropsPanel, { id: IDS.turned }));
    expect(attr(h, "props-panel", "data-state")).toBe("ready");
    const d = readDetails(T);
    expect(d).toMatchObject({ rotation: 30, div: [2, 2, 2], sub: true });
    expect(rotationText(d)).toBe("30° вокруг оси Z, против часовой стрелки при виде сверху");
    expect(subText(d)).toBe("деление блока 2 × 2 × 2; уровней деления 1 × 1 × 1");
    expect(h).toContain("30° вокруг оси Z");
    expect(h).toContain("деление блока 2 × 2 × 2");
    expect(h).toContain("родительских блоков");
    expect(text(h, "props-role-AU")).toBe("содержание");
    expect(text(h, "props-role-OK")).toBe("—");
    expect(text(h, "props-role-ROCK")).toBe("код");
    expect(h).toContain("Поля без единого значения: EMPTYF");
    expect(h).toContain("Поля с одним и тем же значением: FLAT");
    expect(h).toMatch(/Объём блоков/);
    expect(h).toContain("Золото, в пересчёте на сухую массу"); // the description is the hint of the row
    expect(h).not.toContain("Замечаний нет");
  });

  it("a model with no turn and no checks says so; a v1 answer does not make up a turn; the sub-blocks are «нет»", () => {
    const h = html(createElement(PropsPanel, { id: IDS.plain }));
    expect(h).toContain("нет (оси модели вдоль осей проекта)");
    expect(h).toContain("Проверки ещё не выполнялись.");
    expect(h).not.toContain("Замечаний нет");
    const v1 = readDetails(CONTRACT.get.body);
    expect(v1).toMatchObject({ rotation: null, checks: null, sub: false, stale: false });
    expect(rotationText(v1)).toBe("не указан");
    expect(subText(v1)).toBe("нет");
    expect(subText(readDetails({ grid: { div: [4, 4, 2] }, sub: false }))).toBe("возможны, деление блока 4 × 4 × 2 (в модели их нет)");
    expect(readDetails(CONTRACT.get_v2_sub).checks).toMatchObject({ duplicates: 0, overlaps: { n: 0, method: "exact" } });
  });

  it("a clean check says «Замечаний нет»; a model out of date is a warning with its reason", () => {
    blocksStore.set({ details: { ...blocksStore.get().details, [IDS.plain]: { ...CONTRACT.get_v2_sub, id: IDS.plain, version: P.version, fields: P.fields, stale: true, stale_reason: "изменён каркас «Руда»" } } });
    const h = html(createElement(PropsPanel, { id: IDS.plain }));
    expect(h).toContain("Замечаний нет");
    expect(text(h, "props-stale")).toBe("Модель устарела: изменён каркас «Руда». Пересчитайте её или исходные поля.");
  });

  it("«Свойства полей…» is off with a reason until `blocks.field.props` is registered, then runs it for the model", async () => {
    let h = html(createElement(PropsPanel, { id: IDS.turned }));
    expect(disabled(h, "props-field-props")).toBe(true);
    expect(text(h, "props-field-props-reason")).toBe("Недоступно: Окно «Свойства полей» ещё не подключено.");
    app.registry.register([{ id: "blocks.field.props", title: "Свойства полей", description: "Окно свойств полей блочной модели.", params: { model_id: { type: "integer", description: "модель" } } }]);
    app.registry.bind("blocks.field.props", () => "ok");
    h = html(createElement(PropsPanel, { id: IDS.turned }));
    expect(disabled(h, "props-field-props")).toBe(false);
    expect(has(h, "props-field-props-reason")).toBe(false);
  });

  it("«Выбранный блок» is the block of the last click of this model only: the cell, the centre, the size, the values (a missing one is «—»)", () => {
    const at = VIZ.cases.find((c) => c.name === "at_found")!.json as { centre: number[]; ijk: number[]; values: Record<string, unknown> };
    setPicked({ model: IDS.plain, point: [1035, 2025, 112], status: "found", answer: at as never, error: null });
    const h = html(createElement(PropsPanel, { id: IDS.plain }));
    expect(attr(h, "props-picked", "data-state")).toBe("found");
    expect(h).toContain("Ячейка (I, J, K с нуля)");
    expect(h).toContain("3, 2, 2");
    expect(h).toContain("дайка");
    expect(has(html(createElement(PropsPanel, { id: IDS.turned })), "props-picked")).toBe(false);
    setPicked({ model: IDS.plain, point: [0, 0, 0], status: "missing", answer: { found: false }, error: null });
    expect(html(createElement(PropsPanel, { id: IDS.plain }))).toContain("в этой точке блока нет");
  });
});
