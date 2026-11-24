// The wizard of «Импорт файлов» with a block file (ТЗ 7.31, 8.7.2; ADR 0023 §7; Э17 17.38) through a stubbed core that
// answers with the REAL answers (`core/tests/data/blocks/import_window_answers.json`): what the wizard sends when the
// user chooses a role, a unit, a code of «пусто», the grid or the turn (the body the core got when the answers were
// made), what the window shows of the composition (the columns, the grid, the check of three centres, the turn, the
// warnings, the refusal with its rows), the start of the job and the commands that open the wizard.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BlocksStep } from "../src/modules/blocks/import/BlocksStep";
import { emptyDraft, patchDraft, type BlocksInfo } from "../src/modules/blocks/import/model";
import { commands as importCommands } from "../src/modules/importing/commands";
import { canStartObjects, ObjectsWizard, totalsText, type Composition, type FileOk } from "../src/modules/importing/objects";
import { FileBlock } from "../src/modules/importing/ObjectsStep";
import { setCoreTarget } from "../src/shell/api";
import { startOpLog } from "../src/shell/opLog";

const A = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/blocks/import_window_answers.json"), "utf-8"));
type Case = { request: Record<string, unknown>; answer: { status: number; body: unknown } };
const INSPECT = A.inspect as Record<string, Case>;

const canon = (v: unknown): string => JSON.stringify(v, (_k, x: unknown) => (x && typeof x === "object" && !Array.isArray(x) ? Object.fromEntries(Object.entries(x).sort(([a], [b]) => a.localeCompare(b))) : x));
const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const ws = (text: string): string => text.replace(/ /g, " ");
/** The option chosen in the select of the page: the select by its `aria-label`, its `selected` option. */
const chosen = (page: string, label: string): string | null => {
  const select = new RegExp(`<select[^>]*aria-label="${label}"[^>]*>(.*?)</select>`).exec(page);
  const option = select ? /<option value="([^"]*)" selected/.exec(select[1]) : null;
  return option ? option[1] : null;
};
const text = (html: string): string => ws(html.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/\s+/g, " "));

const sent: { method: string; path: string; body: Record<string, unknown> }[] = [];

beforeEach(() => {
  sent.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url).pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      sent.push({ method, path, body });
      if (path === "/import/objects/inspect") {
        const found = Object.values(INSPECT).find((c) => canon(c.request) === canon(body));
        return found ? json(found.answer.body, found.answer.status) : json({ code: "unexpected", message: `Неожиданный запрос ${canon(body)}`, hint: "" }, 500);
      }
      if (path === "/import/objects" && method === "POST") return json({ source: "proj", id: 7 });
      return json({ code: "not_found", message: "Нет", hint: "" }, 404);
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
});

const wizard = () => new ObjectsWizard(startOpLog("Импорт файлов", "import"));
const first = (name: string) => Object.keys((INSPECT[name].request.options ?? {}) as object)[0] ?? (INSPECT[name].request.files as string[])[0];
const file = (w: ObjectsWizard, i = 0): FileOk => w.store.get().composition!.files[i] as FileOk;
/** The card of a file of the composition (`ObjectsStep` draws one for each file). */
const html = (w: ObjectsWizard, i = 0) => {
  const s = w.store.get();
  const f = s.composition!.files[i];
  return renderToStaticMarkup(createElement(FileBlock, { wizard: w, file: f, options: s.options[f.path] ?? {}, cloudMode: false, onSettings: () => undefined }));
};

describe("the composition of a block file", () => {
  it("a DAT: the card with its columns, roles, units, the grid and the check of three centres", async () => {
    const w = wizard();
    await w.add([(INSPECT.dat.request.files as string[])[0]]);
    expect(file(w).kind).toBe("block_model");
    const page = text(html(w));
    expect(page).toContain("Блочная модель · Micromine DAT · блоков 103 · родителей 103");
    const html1 = html(w);
    expect(["EAST", "RL", "_EAST", "_RL", "AU", "ROCK"].map((c) => chosen(html1, `Роль столбца ${c}`))).toEqual(["x", "z", "dx", "dz", "grade", "code"]);
    expect(page).toContain("Содержание · пусто: −99");
    expect(page).toContain("Определена по файлу: 6 × 5 × 4 родителей по 10 × 20 × 5 м; начало 1 000,5; 2 000,25; −50.");
    expect(page).toContain("Три центра файла (строки 1, 52, 103) совпали с сеткой: расхождение 0 м.");
    expect(page).toContain("В файле встречается −99");
    expect(page).toContain("Принять как «пусто»");
    expect(page).toContain("Пустые значения (−99 и другие коды из списка выше) становятся пропусками, а не нулями.");
    expect(ws(totalsText(w.store.get().composition!.totals))).toBe("Файлов: 1 · блочных моделей: 1 · ячеек: 103");
    expect(page).not.toContain("слоёв: 0");
    expect(canStartObjects(w.store.get())).toBe(true);
  });

  it("a Datamine with sub-blocks shows the division; a turned CSV shows the turn «предварительно»", async () => {
    const dm = wizard();
    await dm.add([(INSPECT.sub_dm.request.files as string[])[0]]);
    const page = text(html(dm));
    expect(page).toContain("Datamine DM · блоков 170 · родителей 19 · с субблоками");
    expect(page).toContain("Деление на субблоки");
    expect(page).toContain("Размер родительского блока взят по самому большому блоку: 8 × 8 × 4.");
    const turned = wizard();
    const path = first("turned_csv");
    turned.store.set({ paths: [path], options: { [path]: { blocks: patchDraft(null, { corner: ["466167.5", "4584527.5", "202.5"], size: ["8", "8", "4"], rotation: "30" }) } } });
    await turned.inspect();
    expect(canon(sent.at(-1)!.body)).toBe(canon(INSPECT.turned_csv.request));
    const tp = text(html(turned));
    expect(tp).toContain("Поворот 30° по соглашению Micromine");
    expect(tp).toContain("предварительно");
    expect(tp).toContain("строки 1, 86, 170) совпали с сеткой");
    expect(tp).toContain("Разделитель");
  });

  it("the refusal of the core: the message, the rows from 1 and the hint; the import does not start by itself", async () => {
    const w = wizard();
    const path = first("off_grid");
    w.store.set({ paths: [path], options: { [path]: { blocks: patchDraft(null, { corner: ["1025.5", "2000.25", "-50"], size: ["10", "20", "5"] }) } } });
    await w.inspect();
    const page = text(html(w));
    expect(page).toContain("Блоков, которые нельзя уложить в сетку модели: 103");
    expect(page).toContain("Строки данных (с 1): 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20 и другие");
    expect(page).toContain("Проверьте начало и размер родителя в мастере");
    expect(page).toContain("Сетку по файлу определить не удалось");
    expect(canStartObjects(w.store.get())).toBe(false);
  });

  it("a table the core did not take for a model: the refusal and «блочной моделью»; chosen, it is read as one", async () => {
    const w = wizard();
    const path = (INSPECT.not_detected.request.files as string[])[0];
    await w.add([path]);
    expect(file(w).ok).toBe(false);
    const before = text(html(w));
    expect(before).toContain("В файле не найдены колонки координат X и Y.");
    expect(before).toContain("блочной моделью");
    await w.setOptions(path, { blocks: emptyDraft() });
    expect(canon(sent.at(-1)!.body)).toBe(canon(INSPECT.no_roles.request));
    expect(w.store.get().composition!.files[0].ok).toBe(true);
    const after = text(html(w));
    expect(after).toContain("Не найдены столбцы центра блока: X, Y, Z.");
    expect(after).toContain("Укажите столбцы центров");
    expect(canStartObjects(w.store.get())).toBe(false);
    await w.setOptions(path, { blocks: patchDraft(emptyDraft(), { columns: { A: "x", B: "y", C: "z" } }) });
    expect(canon(sent.at(-1)!.body)).toBe(canon(INSPECT.roles_chosen.request));
    expect(canStartObjects(w.store.get())).toBe(true);
    expect(chosen(html(w), "Роль столбца D")).toBe("field");
    expect(["A", "B", "C"].map((c) => chosen(html(w), `Роль столбца ${c}`))).toEqual(["x", "y", "z"]);
  });

  it("a table of points is offered «блочной моделью»; a block file is not offered it", async () => {
    const w = wizard();
    await w.add([(INSPECT.points.request.files as string[])[0]]);
    expect(text(html(w))).toContain("блочной моделью");
    const b = wizard();
    await b.add([(INSPECT.dat.request.files as string[])[0]]);
    expect(html(b)).not.toContain("Блочной моделью model.dat");
  });
});

describe("the answers of the user reach the core as the recorded body", () => {
  it("roles, a unit, the code of «пусто» and the name: the request of `dat_asked`", async () => {
    const w = wizard();
    const path = first("dat_asked");
    await w.add([path]);
    const draft = { ...emptyDraft(), columns: { AU: "grade" as const, ROCK: "class" as const }, units: { AU: "%" }, empty: "-99" };
    await w.setOptions(path, { blocks: draft, name: "Моя модель" });
    expect(canon(sent.at(-1)!.body)).toBe(canon(INSPECT.dat_asked.request));
    const info = file(w).blocks as BlocksInfo;
    expect(info.fields.map((f) => [f.name, f.role, f.unit])).toEqual([["AU", "grade", "%"], ["ROCK", "class", null]]);
    const page = html(w);
    expect(chosen(page, "Роль столбца ROCK")).toBe("class");
    expect(chosen(page, "Роль столбца AU")).toBe("grade");
    expect(page).toContain('aria-label="Единица AU" placeholder="—" value="%"');
    expect(text(page)).not.toContain("Принять как «пусто»"); // the code is taken
  });

  it("a skipped column is not loaded", async () => {
    const w = wizard();
    const path = first("dat_skipped");
    await w.add([path]);
    await w.setOptions(path, { blocks: patchDraft(null, { columns: { ROCK: "skip" } }) });
    expect(canon(sent.at(-1)!.body)).toBe(canon(INSPECT.dat_skipped.request));
    expect(file(w).blocks!.fields.map((f) => f.name)).toEqual(["AU"]);
    expect(chosen(html(w), "Роль столбца ROCK")).toBe("skip");
  });

  it("the start posts the same options and follows the job", async () => {
    const w = wizard();
    const path = first("dat_asked");
    await w.add([path]);
    await w.setOptions(path, { blocks: { ...emptyDraft(), columns: { AU: "grade", ROCK: "class" }, units: { AU: "%" }, empty: "-99" }, name: "Моя модель" });
    const r = await w.start();
    expect(r).toEqual({ source: "proj", id: 7 });
    const post = sent.filter((s) => s.path === "/import/objects" && s.method === "POST").at(-1)!;
    expect(post.body.options).toEqual({ [path]: { name: "Моя модель", blocks: (INSPECT.dat_asked.request.options as Record<string, { blocks: unknown }>)[path].blocks } });
    expect(post.body.files).toEqual([path]);
  });

  it("a wrong typed angle is told in the card and stops the start", async () => {
    const w = wizard();
    const path = first("dat");
    await w.add([path]);
    w.store.set({ options: { [path]: { blocks: patchDraft(null, { rotation: "abc" }) } } });
    expect(text(html(w))).toContain("«abc» — не число. Введите угол в градусах, например 30.");
    expect(canStartObjects(w.store.get())).toBe(false);
  });
});

describe("the step on its own", () => {
  it("every column has a role to choose, a number column — a unit; the select of a text column has no numbers", () => {
    const info = (INSPECT.dat.answer.body as Composition).files[0] as FileOk;
    const w = wizard();
    const page = renderToStaticMarkup(createElement(BlocksStep, { wizard: w, file: { ...info, blocks: info.blocks! }, options: {} }));
    expect(page).toContain('aria-label="Роль столбца EAST"');
    expect(page).toContain('aria-label="Единица AU"');
    expect(page).not.toContain('aria-label="Единица ROCK"');
    expect(page).toContain('aria-label="Коды пустого"');
    expect(page).toContain('aria-label="Поворот вокруг Z"');
    expect(page).toContain('aria-label="Название модели model.dat"');
    const select = /<select[^>]*aria-label="Роль столбца ROCK"[^>]*>(.*?)<\/select>/.exec(page)![1];
    expect(select).not.toContain("Центр X");
    expect(select).toContain("Код (категория)");
  });

  it("the delimiter is offered for a text file only", () => {
    const csv = (INSPECT.csv.answer.body as Composition).files[0] as FileOk;
    const dat = (INSPECT.dat.answer.body as Composition).files[0] as FileOk;
    const render = (f: FileOk) => renderToStaticMarkup(createElement(BlocksStep, { wizard: wizard(), file: { ...f, blocks: f.blocks! }, options: {} }));
    expect(render(csv)).toContain("Разделитель");
    expect(render(dat)).not.toContain("Разделитель");
  });
});

describe("the command", () => {
  it("`import.files` takes `kind: blocks` and tells what it offers", () => {
    const cmd = importCommands.find((c) => c.id === "import.files")!;
    expect((cmd.params as Record<string, { enum?: string[] }>).kind.enum).toContain("blocks");
    expect(cmd.description).toContain("blocks — блочные модели");
  });
});
