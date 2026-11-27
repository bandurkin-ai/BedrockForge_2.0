// The properties of the fields of a block model and the commands `blocks.field.props`, `blocks.field.rebuild`,
// `blocks.field.delete` (ТЗ 8.7.2, 8.7.5; ADR 0023 §2, §7, §9; № 74; Э17 17.36) on the REAL answers of the core
// (`core/tests/data/blocks/fields_window_answers.json`, made by `core/tests/test_blocks_fields_window_answers.py` through
// `GET /blocks/{id}/fields`, `PATCH`, `DELETE` and `…/rebuild`): the list read as the core wrote it («устарел», «Где
// используется», the makers of the fields); the edit of the unit, the description and the role as the body the core got;
// a field in use or the field of the volume is not deleted — told with the consumers; «Перестроить» of a stale field; the
// refusals 404 / 409 / 422 / 423 in Russian under the row they are about; a field is not renamed (№ 74).
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../src/shell/AppContext";
import type { App } from "../src/shell/app";
import { dialogStore } from "../src/shell/dialogs";
import { answerClaude, noClaudeCards } from "./claude-confirm";
import { readFields, readUsage } from "../src/modules/blocks/fields/api";
import { windowOfFields } from "../src/modules/blocks/fields/bind";
import { loadBlocks } from "../src/modules/blocks/state";
import { CalcWindow } from "../src/modules/blocks/fields/CalcWindow";
import { commands } from "../src/modules/blocks/fields/commands";
import { editOf, patchOfEdit, refreshProps, rowPlace } from "../src/modules/blocks/fields/propsBind";
import { deleteReason, fieldLine, RENAME_HINT, rolesFor, roleTitle, sourceText, usageLine, usageNames } from "../src/modules/blocks/fields/propsText";
import { currentRun, fieldsStore, setPropsEdit } from "../src/modules/blocks/fields/state";
import { A, answer, answerDialog, canon, IDS, install, json, posts, stub, uninstall, without } from "./blocks-fields-kit";

type Case = { request: Record<string, unknown>; name: string; status: number; body?: Record<string, any>; job?: Record<string, any> };
const F = A.fields as Record<string, any>;
const LISTS = { list: F.list.body, stale: F.list_stale.body, rebuilt: F.list_rebuilt.body, views: F.list_views.body };
const rowOf = (list: { fields: any[] }, name: string) => readFields(list)!.fields.find((f) => f.name === name)!;
const BASE = `/blocks/${IDS.model}/fields`;

let app: App;
beforeEach(async () => {
  app = install();
  stub.fields = LISTS.stale;
  stub.version = LISTS.stale.version;
  await loadBlocks(); // the interface runs over the version it read
  app.store.set({ selected: { kind: "blocks", id: IDS.model } });
});
afterEach(() => uninstall());

const sentOf = (method: string, re?: RegExp) => stub.sent.filter((x) => x.method === method && x.path.startsWith(BASE) && (!re || re.test(x.path)));
const reads = () => stub.sent.filter((x) => x.method === "GET" && x.path === BASE);

describe("the list of the fields as the core wrote it", () => {
  it("is read: the kind, the dtype, the unit, the role, the maker, the states; a gap is null, not 0", () => {
    const list = readFields(LISTS.stale)!;
    expect(list).toMatchObject({ id: IDS.model, name: "Месторождение", stale: false, staleReason: null });
    expect(list.fields).toHaveLength(LISTS.stale.fields.length);
    const au = rowOf(LISTS.stale, "AU");
    expect(au).toMatchObject({ kind: "number", dtype: "f8", unit: "г/т", role: "grade", description: null, file: expect.stringMatching(/^fld:/) });
    const dom = rowOf(LISTS.stale, "DOM");
    expect(dom).toMatchObject({ kind: "category", dtype: "cat", labelsCount: 3, unit: null, source: null, rebuildable: false });
    const ore = rowOf(LISTS.stale, "ORE");
    expect(ore).toMatchObject({ dtype: "i1", stale: true, rebuildable: true, deletable: true });
    expect(readFields(null)).toBeNull();
    expect(readFields({ id: 1 })).toBeNull();
  });

  it("«устарел»: the field made of fields that changed, with the reason of the core; a field made of nothing that changed is not", () => {
    const stale = readFields(LISTS.stale)!.fields.filter((f) => f.stale).map((f) => f.name);
    expect(stale).toEqual(["AU2", "GRADE", "ORE", "AU10", "AU_N", "CLASS", "CLASS_M", "K", "K2", "K3"]);
    expect(rowOf(LISTS.stale, "AU2").staleReason).toMatch(/Изменились поля или фильтр/);
    expect(rowOf(LISTS.stale, "CLASS").staleReason).toMatch(/каркасы/);
    expect(rowOf(LISTS.stale, "CU10").stale).toBe(false);
    expect(rowOf(LISTS.stale, "DENS").stale).toBe(false);
    // rebuilt: AU2 is fresh again, the others are still told
    expect(rowOf(LISTS.rebuilt, "AU2").stale).toBe(false);
    expect(rowOf(LISTS.rebuilt, "ORE").stale).toBe(true);
  });

  it("«Где используется»: the fields that read it, once each (the core may name a field twice), the views of the project", () => {
    const raw = LISTS.stale.fields.find((f: any) => f.name === "CU").usage as { name: string }[];
    expect(raw.filter((u) => u.name === "CU10")).toHaveLength(2); // the filter and the rule
    const cu = rowOf(LISTS.stale, "CU");
    expect(cu.usage.filter((u) => u.name === "CU10")).toHaveLength(1);
    expect(cu.usage.length).toBeLessThan(raw.length);
    const au10 = rowOf(LISTS.views, "AU10");
    expect(au10.usage.map((u) => [u.kind, u.name, u.role])).toEqual([
      ["view", "Вид проекта · Месторождение", "colour"],
      ["view", "Вид проекта · Месторождение", "restrictor"],
      ["view", "Мой вид", "colour"],
    ]);
    expect(au10.usage.map(usageLine)).toEqual(["Вид проекта · Месторождение: раскраска", "Вид проекта · Месторождение: быстрый ограничитель", "Мой вид: раскраска"]);
    expect(usageNames(au10.usage)).toBe("«Вид проекта · Месторождение», «Мой вид»");
    expect(usageLine({ kind: "field", id: null, name: "AU2", role: "source" })).toBe("поле «AU2» считается из этого поля");
    expect(readUsage([{ kind: "field", name: "A", role: "source" }, { kind: "field", name: "A", role: "source" }, { name: null }, null])).toHaveLength(1);
    expect(readUsage(undefined)).toEqual([]);
  });

  it("where the field came from: the file, the calculator with its text, the density by its way, the categories by their scheme", () => {
    expect(sourceText(rowOf(LISTS.stale, "DOM"))).toBe("из файла модели");
    expect(sourceText(rowOf(LISTS.stale, "AU2"))).toBe("Калькулятор: IF(AU > 1, AU * 3, 0)");
    expect(sourceText(rowOf(LISTS.stale, "DENS_F"))).toBe("Плотность (по полю)");
    expect(sourceText(rowOf(LISTS.stale, "DENS_T"))).toBe("Плотность (таблица по домену)");
    expect(sourceText(rowOf(LISTS.stale, "K"))).toBe("Категории по схеме «my»");
    expect(sourceText(rowOf(LISTS.stale, "K2"))).toBe("Категории по своим правилам");
  });

  it("why a field is not deleted: it is in use (the consumers), or it is the field of the volume; a free field is", () => {
    expect(deleteReason(rowOf(LISTS.stale, "AU2"))).toBeNull();
    expect(deleteReason(rowOf(LISTS.stale, "FR"))).toBe("Поле «FR» задаёт объём блоков. Снимите роль «доля объёма», затем удалите его.");
    const used = deleteReason(rowOf(LISTS.stale, "AU"))!;
    expect(used).toMatch(/^Поле «AU» используется: «AU2», «GRADE», /);
    expect(used).toMatch(/и ещё \d+\. Пока ссылки есть/);
    expect(deleteReason(rowOf(LISTS.views, "AU10"))).toBe("Поле «AU10» используется: «Вид проекта · Месторождение», «Мой вид». Пока ссылки есть, оно не удаляется.");
  });

  it("the roles: a number — содержание, плотность, доля объёма; a category — код каркаса, категория; the titles are Russian", () => {
    expect(rolesFor("number")).toEqual(["grade", "density", "frac"]);
    expect(rolesFor("category")).toEqual(["code", "class"]);
    expect(rolesFor("number").concat(rolesFor("category")).map(roleTitle)).toEqual(["содержание", "плотность", "доля объёма", "код каркаса", "категория"]);
    expect(roleTitle(null)).toBe("без роли");
  });

  it("the line for the assistant: the kind, the unit, the role, the maker, the state, where it is used", () => {
    const line = fieldLine(rowOf(LISTS.stale, "AU2"), "Месторождение");
    expect(line).toBe("Поле «AU2» модели «Месторождение»: число f8; единица не задана; роль: без роли; источник: Калькулятор: IF(AU > 1, AU * 3, 0); устарел (Изменились поля или фильтр, из которых считалось, или алгоритм.); нигде не используется");
    expect(fieldLine(rowOf(LISTS.views, "AU10"), "М")).toContain("используется: Вид проекта · Месторождение: раскраска; Вид проекта · Месторождение: быстрый ограничитель; Мой вид: раскраска");
    expect(fieldLine(rowOf(LISTS.stale, "DOM"), "М")).toContain("категория, подписей 3");
  });
});

describe("the window", () => {
  const html = () => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(CalcWindow, { winId: windowOfFields()! })));

  it("opens on its tab with the model of the tree, reads the list, and shows every field with its state, its buttons and the hint on the name (№ 74)", async () => {
    const r = await app.registry.execute("blocks.field.props", {}, "user");
    expect(r).toEqual({ ok: true, result: "Открыто окно «Поля блочной модели»: свойства полей" });
    expect(fieldsStore.get()).toMatchObject({ tab: "props", modelId: IDS.model });
    await new Promise((r2) => setTimeout(r2, 20));
    expect(reads().length).toBeGreaterThan(0);
    const h = html();
    for (const id of ["props-panel", "props-table", "props-row-AU", "props-row-AU2", "props-stale-AU2", "props-unit-AU", "props-description-AU", "props-role-AU", "props-save-AU", "props-rebuild-AU2", "props-delete-AU2", "props-usage-AU", "props-source-AU2", "props-rename-hint", "props-reload"]) expect(h, id).toContain(`data-testid="${id}"`);
    expect(h).toContain("устарел");
    expect(h).toContain(RENAME_HINT);
    expect(h).toMatch(/data-testid="props-name-AU"[^>]*>AU</);
    expect(h).toMatch(/title="Переименовать поле нельзя[^"]*"[^>]*data-testid="props-name-AU"/);
    expect(h).not.toMatch(/<input[^>]*data-testid="props-name-/); // the name is not edited
    expect(h).toMatch(/data-testid="props-rebuild-DOM"/);
    expect(h).toMatch(/disabled=""[^>]*data-testid="props-rebuild-DOM"/); // an imported field cannot be rebuilt
    expect(h).toMatch(/aria-disabled="true"[^>]*data-testid="props-delete-AU"/); // a field in use cannot be deleted
    expect(h).toMatch(/title="Поле «AU» используется: [^"]*"[^>]*aria-disabled="true"/);
    expect(h).toContain("Где используется (10)");
    expect(h).toContain("Нигде не используется");
    expect(h).toContain("категория (3)");
    // the roles of a category are not the roles of a number
    const dom = h.slice(h.indexOf('data-testid="props-role-DOM"'), h.indexOf('data-testid="props-source-DOM"'));
    expect(dom).toContain("код каркаса");
    expect(dom).not.toContain("доля объёма");
  });

  it("an edited row is marked and its «Сохранить» is on; the others are off", async () => {
    await app.registry.execute("blocks.field.props", {}, "user");
    await refreshProps(IDS.model);
    const f = rowOf(LISTS.stale, "CU");
    setPropsEdit("CU", editOf(f), { unit: "проц." });
    const h = html();
    expect(h).toMatch(/data-dirty="true"[^>]*data-testid="props-row-CU"|data-testid="props-row-CU"[^>]*data-dirty="true"/);
    expect(h).toMatch(/data-testid="props-save-CU"/);
    expect(h).not.toMatch(/disabled=""[^>]*data-testid="props-save-CU"/);
    expect(h).toMatch(/disabled=""[^>]*data-testid="props-save-AU"/);
    expect(patchOfEdit(f, fieldsStore.get().props.edits.CU)).toEqual({ unit: "проц." });
    expect(patchOfEdit(f, { unit: "г/т", description: "", role: "grade" })).toEqual({ unit: "г/т" });
    expect(patchOfEdit(f, { ...editOf(f), role: "" })).toEqual({ role: null });
  });

  it("the usage of a field opens in the row; the model that is stale is told", async () => {
    stub.fields = { ...LISTS.views, stale: true, stale_reason: "изменён каркас «Тело»" };
    await app.registry.execute("blocks.field.props", {}, "user");
    await refreshProps(IDS.model);
    fieldsStore.set((s) => ({ props: { ...s.props, usageOpen: "AU10" } }));
    const h = html();
    expect(h).toContain('data-testid="props-usage-list-AU10"');
    expect(h).toContain("Мой вид: раскраска");
    expect(h).toContain('data-testid="props-model-stale"');
    expect(h).toContain("изменён каркас «Тело»");
  });
});

describe("the command `blocks.field.props`", () => {
  const route = (name: string, c: Case) => stub.routes.push((m, p) => (m === "PATCH" && p === `${BASE}/${name}` ? json(c.body, c.status) : undefined));
  /** null and "" are the same «clear» for the core; the body of the interface says it one way */
  const same = (b: Record<string, unknown>) => canon(Object.fromEntries(Object.entries(b).map(([k, v]) => [k, v === "" ? null : v])));

  it("is in the registry: a Russian title and a description for the assistant; confirm; the window is the default; there is no name to rename with", () => {
    const c = app.registry.get("blocks.field.props")!;
    expect(commands.map((x) => x.id)).toEqual(["blocks.calc", "blocks.calc.check", "blocks.density", "blocks.classify", "blocks.scheme.save", "blocks.field.info", "blocks.field.props", "blocks.field.rebuild", "blocks.field.delete"]);
    expect(c.run).toBeTypeOf("function");
    expect(c.confirm).toBe(true);
    expect(c.title).toBe("Свойства поля блочной модели");
    expect(c.description).toMatch(/Поле переименовать нельзя/);
    expect(c.required ?? []).toEqual([]);
    expect(Object.keys(c.params!)).toEqual(["model_id", "field", "unit", "description", "role"]);
    expect(Object.keys(app.registry.get("blocks.field.rebuild")!.params!)).toEqual(["model_id", "field"]);
    expect(app.registry.get("blocks.field.delete")!.confirm).toBe(true);
  });

  it("`blocks.field.info` without a field lists the fields of the model: the model, its version, every field in a line; no confirmation, no write", async () => {
    const r = await app.registry.execute("blocks.field.info", { model_id: IDS.model }, "claude");
    expect(r.ok, r.result).toBe(true);
    const lines = r.result.split("\n");
    expect(lines[0]).toBe(`Модель «Месторождение», версия ${LISTS.stale.version}: полей ${LISTS.stale.fields.length}`);
    expect(lines).toHaveLength(LISTS.stale.fields.length + 1);
    expect(lines).toContain("AU: число f8, г/т, роль: содержание, используется: 10");
    expect(lines).toContain("DOM: категория, подписей 3, используется: 5");
    expect(lines.find((l) => l.startsWith("AU2:"))).toBe("AU2: число f8, устарел");
    expect(lines).toContain("FR: число f8, роль: доля объёма");
    expect(app.registry.get("blocks.field.info")!.confirm).toBeUndefined();
    expect(app.registry.get("blocks.field.info")!.required).toEqual(["model_id"]);
    expect(sentOf("PATCH").concat(sentOf("DELETE"), sentOf("POST"))).toEqual([]);
  });

  it("`blocks.field.props` without the properties to change opens the window on its tab and writes nothing", async () => {
    const r = await app.registry.execute("blocks.field.props", { model_id: IDS.model, field: "AU" }, "user");
    expect(r).toEqual({ ok: true, result: "Открыто окно «Поля блочной модели»: свойства полей" });
    expect(fieldsStore.get().tab).toBe("props");
    expect(sentOf("PATCH")).toEqual([]);
  });

  it("`blocks.field.info` of one field: the line for the assistant, nothing is written", async () => {
    const r = await app.registry.execute("blocks.field.info", { model_id: IDS.model, field: "AU2" }, "claude");
    expect(r.ok, r.result).toBe(true);
    expect(r.result).toBe(fieldLine(rowOf(LISTS.stale, "AU2"), "Месторождение"));
    expect(r.result).toContain("устарел");
    expect(sentOf("PATCH")).toEqual([]);
    expect(windowOfFields()).toBeNull();
    const gone = await app.registry.execute("blocks.field.info", { model_id: IDS.model, field: "NOPE" }, "claude");
    expect(gone.result).toBe("В модели «Месторождение» нет поля «NOPE». Имена полей — во вкладке «Свойства полей» окна «Поля блочной модели».");
    const none = await app.registry.execute("blocks.field.info", { model_id: 999, field: "AU" }, "claude");
    expect(none.result).toBe("Блочная модель №999 не найдена. Номера моделей — в разделе «Блочные модели» дерева.");
  });

  const WRITES: [string, string, Record<string, unknown>][] = [
    ["patch_text", "CU", { unit: "проц.", description: "Медь" }],
    ["patch_clear", "CU", { unit: "", description: "" }],
    ["patch_role", "FR", { role: "frac" }],
    ["patch_role_density", "SG", { role: "density" }],
    ["patch_role_clear", "SG", { role: "" }],
  ];
  for (const [name, field, args] of WRITES) {
    it(`${name}: the body of the recorded request over the version read, the list read again, the line for the log`, async () => {
      const c = F[name] as Case;
      route(field, c);
      stub.version = (c.request.expected_version as number) ?? stub.version;
      await (await import("../src/modules/blocks/state")).loadBlocks();
      const r = await app.registry.execute("blocks.field.props", { model_id: IDS.model, field, ...args }, "user");
      expect(r.ok, r.result).toBe(true);
      const sent = sentOf("PATCH");
      expect(sent).toHaveLength(1);
      expect(sent[0].path).toBe(`${BASE}/${field}`);
      expect(sent[0].body.expected_version).toBe(c.request.expected_version);
      expect(same(without(sent[0].body, "expected_version"))).toBe(same(without(c.request, "expected_version")));
      expect("name" in sent[0].body).toBe(false); // a field is not renamed
      expect(r.result).toMatch(new RegExp(`^Поле «${field}» модели «Месторождение» изменено: `));
      expect(r.result).toContain(`версия модели ${c.body!.version}`);
      expect(reads().length).toBeGreaterThan(0); // the list is read again
    });
  }

  it("the window's «Сохранить»: only the keys that changed go; the row is cleared after the write", async () => {
    route("CU", F.patch_text);
    await app.registry.execute("blocks.field.props", {}, "user");
    await refreshProps(IDS.model);
    const f = rowOf(LISTS.stale, "CU");
    setPropsEdit("CU", editOf(f), { unit: "проц.", description: "Медь" });
    const r = await app.registry.execute("blocks.field.props", { model_id: IDS.model, field: "CU", unit: "проц.", description: "Медь" }, "user");
    expect(r.ok, r.result).toBe(true);
    expect(fieldsStore.get().props.edits.CU).toBeUndefined();
    expect(sentOf("PATCH").map((x) => Object.keys(x.body).sort())).toEqual([["description", "expected_version", "unit"]]);
  });

  const REFUSED: [string, string, Record<string, unknown>, string][] = [
    ["frac_twice", "FR2", { role: "frac" }, "frac_exists"],
    ["role_invalid", "AU", { role: "class" }, "role_invalid"],
    ["role_unknown", "AU", { role: "weird" }, "role_invalid"],
    ["not_found", "NOPE", { unit: "г/т" }, "block_field_not_found"],
  ];
  for (const [name, field, args, code] of REFUSED) {
    it(`${name}: the words and the hint of the core, under the row of the field`, async () => {
      const c = F[name] as Case;
      expect(c.body!.code).toBe(code);
      expect(c.body!.message).toMatch(/[А-Яа-яЁё]/);
      expect(c.body!.hint).not.toBe("");
      route(field, c);
      const r = await app.registry.execute("blocks.field.props", { model_id: IDS.model, field, ...args }, "user");
      expect(r.ok).toBe(false);
      expect(r.result).toBe(`${c.body!.message} ${c.body!.hint}`);
      expect(fieldsStore.get().issue).toMatchObject({ tab: "props", place: rowPlace(field), message: c.body!.message, hint: c.body!.hint });
      await app.registry.execute("blocks.field.props", {}, "user");
      await refreshProps(IDS.model);
      fieldsStore.set({ issue: { tab: "props", place: rowPlace("AU"), param: null, message: c.body!.message, hint: c.body!.hint } });
      const h = renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(CalcWindow, { winId: windowOfFields()! })));
      expect(h).toContain('data-testid="props-issue-AU"');
      expect(h).toContain(`Поле «AU»: ${c.body!.message} ${c.body!.hint}`);
    });
  }

  it("a text too long is told before the core is asked; the field is not renamed: the name is not a key of the command", async () => {
    const r = await app.registry.execute("blocks.field.props", { model_id: IDS.model, field: "CU", unit: "я".repeat(41) }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toBe("Единица: не больше 40 знаков. Сократите текст.");
    expect(F.unit_long.body.message).toMatch(/[А-Яа-яЁё]/);
    expect(sentOf("PATCH")).toEqual([]);
    // what the core says of a rename it is not asked for (№ 74): the words are there for any client
    expect(F.rename.body.code).toBe("field_rename_unsupported");
    expect(F.rename.body.message).toMatch(/переименовать нельзя/);
    expect(F.rename.body.hint).toContain("Создайте поле калькулятором под новым именем и удалите старое.");
    expect(RENAME_HINT).toContain("Создайте поле калькулятором под новым именем и удалите старое.");
  });

  it("the model changed elsewhere (409 version_conflict): the list is read again and the person is told to repeat", async () => {
    route("CU", F.conflict);
    const r = await app.registry.execute("blocks.field.props", { model_id: IDS.model, field: "CU", unit: "х" }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toBe(`${F.conflict.body.message} ${F.conflict.body.hint} Данные модели обновлены — повторите действие.`);
    expect(stub.sent.filter((x) => x.method === "GET" && x.path === "/blocks").length).toBeGreaterThan(0);
    expect(reads().length).toBeGreaterThan(0);
  });

  it("a project open for reading: the list is read, every write is told before the core is asked; the core's 423 when it turns read-only meanwhile", async () => {
    app.store.set({ projectReadOnly: true });
    const read = await app.registry.execute("blocks.field.info", { model_id: IDS.model, field: "AU" }, "user");
    expect(read.ok).toBe(true);
    const w = await app.registry.execute("blocks.field.props", { model_id: IDS.model, field: "CU", unit: "х" }, "user");
    expect(w.ok).toBe(false);
    expect(w.result).toMatch(/только для чтения/);
    for (const [cmd, extra] of [["blocks.field.rebuild", {}], ["blocks.field.delete", {}]] as const) {
      const x = await app.registry.execute(cmd, { model_id: IDS.model, field: "AU2", ...extra }, "user");
      expect(x.ok).toBe(false);
      expect(x.result).toMatch(/только для чтения/);
    }
    expect(stub.sent.filter((x) => x.method !== "GET")).toEqual([]);
    app.store.set({ projectReadOnly: false });
    route("CU", { ...F.patch_text, status: A.read_only.patch.status, body: A.read_only.patch.body });
    const late = await app.registry.execute("blocks.field.props", { model_id: IDS.model, field: "CU", unit: "х" }, "user");
    expect(late.result).toBe(`${A.read_only.patch.body.message} ${A.read_only.patch.body.hint}`);
    expect(A.read_only.fields.status).toBe(200);
  });
});

describe("the command `blocks.field.delete`", () => {
  const del = (name: string, c: { status: number; body: any }) => stub.routes.push((m, p) => (m === "DELETE" && p === `${BASE}/${name}` ? json(c.body, c.status) : undefined));

  it("a free field: the question in our own window, the body of the recorded request, the line for the log, the list read again", async () => {
    stub.fields = LISTS.list;
    stub.version = LISTS.list.version;
    await (await import("../src/modules/blocks/state")).loadBlocks();
    del("AU_K", F.delete_ok);
    const run = app.registry.execute("blocks.field.delete", { model_id: IDS.model, field: "AU_K" }, "user");
    const asked = await answerDialog(true);
    expect(asked).toBe("Удалить поле|Удалить поле «AU_K» из модели «Месторождение»? Его значения пропадут: вернуть поле можно только пересчётом.");
    const r = await run;
    expect(r.ok, r.result).toBe(true);
    const sent = sentOf("DELETE");
    expect(sent).toHaveLength(1);
    expect(sent[0].query).toBe(`expected_version=${LISTS.list.version}`);
    expect(r.result).toBe(`Поле «AU_K» удалено из модели «Месторождение»; версия модели ${F.delete_ok.body.version}`);
    expect(reads().length).toBeGreaterThan(1);
  });

  it("«Нет»: nothing is deleted", async () => {
    stub.fields = LISTS.list;
    del("AU_K", F.delete_ok);
    const run = app.registry.execute("blocks.field.delete", { model_id: IDS.model, field: "AU_K" }, "user");
    await answerDialog(false);
    expect(await run).toEqual({ ok: true, result: "Отменено" });
    expect(sentOf("DELETE")).toEqual([]);
  });

  it("a field in use is not deleted: the consumers are told in our own window and in the answer; the core is not asked", async () => {
    const run = app.registry.execute("blocks.field.delete", { model_id: IDS.model, field: "AU" }, "user");
    const told = await answerDialog(true);
    const r = await run;
    expect(told).toMatch(/^Поле не удалено\|Поле «AU» используется: «AU2», «GRADE», /);
    expect(told).toContain("поле «AU2» считается из этого поля");
    expect(told).toContain("Уберите эти ссылки");
    expect(r.ok).toBe(false);
    expect(r.result).toMatch(/^Поле «AU» используется: «AU2», «GRADE», .* Удалите или измените зависимые поля и виды\.$/);
    expect(sentOf("DELETE")).toEqual([]);
    expect(fieldsStore.get().issue).toMatchObject({ tab: "props", place: rowPlace("AU") });
  });

  it("the assistant is told the same and is not shown a window", async () => {
    const run = app.registry.execute("blocks.field.delete", { model_id: IDS.model, field: "AU" }, "claude");
    await answerClaude(true); // the confirmation of the assistant: a card of the chat
    const r = await run;
    expect(r.ok).toBe(false);
    expect(r.result).toMatch(/^Поле «AU» используется/);
    expect(dialogStore.get().queue).toEqual([]);
    noClaudeCards();
  });

  it("the field of the volume is not deleted until its role is taken off: the words say how", async () => {
    const run = app.registry.execute("blocks.field.delete", { model_id: IDS.model, field: "FR" }, "user");
    const told = await answerDialog(true);
    const r = await run;
    expect(told).toBe("Поле не удалено|Поле «FR» задаёт объём блоков. Снимите роль «доля объёма», затем удалите его.");
    expect(r.result).toBe("Поле «FR» задаёт объём блоков. Снимите роль «доля объёма», затем удалите его. Снимите роль в свойствах поля.");
    expect(F.delete_required.body.code).toBe("field_required");
    expect(F.delete_required.body.message).toBe("Поле «FR» задаёт объём блоков, удалить его нельзя.");
    expect(sentOf("DELETE")).toEqual([]);
  });

  it("a field that began to be used after the list was read: the core says so (422 field_in_use), the consumers are told, the list is read again", async () => {
    stub.fields = LISTS.list; // AU10 is free in this list
    del("AU10", F.delete_used_by_view);
    const run = app.registry.execute("blocks.field.delete", { model_id: IDS.model, field: "AU10" }, "user");
    await answerDialog(true); // «Удалить?»
    const told = await answerDialog(true); // the consumers
    const r = await run;
    expect(sentOf("DELETE")).toHaveLength(1);
    expect(told).toContain("используется: «Вид проекта · Месторождение», «Мой вид»");
    expect(r.ok).toBe(false);
    expect(r.result).toBe(`${F.delete_used_by_view.body.message} ${F.delete_used_by_view.body.hint}`);
    expect(fieldsStore.get().issue).toMatchObject({ tab: "props", place: rowPlace("AU10"), param: null });
  });

  it("the other refusals: the model changed (409) — read again; a field that is not there (404)", async () => {
    stub.fields = LISTS.list;
    del("AU_K", F.delete_conflict);
    const run = app.registry.execute("blocks.field.delete", { model_id: IDS.model, field: "AU_K" }, "user");
    await answerDialog(true);
    const r = await run;
    expect(r.result).toBe(`${F.delete_conflict.body.message} ${F.delete_conflict.body.hint} Данные модели обновлены — повторите действие.`);
    const gone = app.registry.execute("blocks.field.delete", { model_id: IDS.model, field: "NOPE" }, "claude");
    await answerClaude(true); // the confirmation of the assistant
    const missing = await gone;
    expect(missing.ok).toBe(false);
    expect(missing.result).toBe("В модели «Месторождение» нет поля «NOPE». Имена полей — во вкладке «Свойства полей» окна «Поля блочной модели».");
    expect(F.delete_not_found.body.code).toBe("block_field_not_found");
  });
});

describe("the command `blocks.field.rebuild`", () => {
  const rebuild = (name: string, c: Case) => stub.routes.push((m, p) => (m === "POST" && p === `${BASE}/${name}/rebuild` ? answer(c as never) : undefined));

  it("a stale field: the question, the body of the recorded request, the job, the result of the maker, the line for the log", async () => {
    rebuild("AU2", F.rebuild);
    stub.fields = LISTS.rebuilt;
    const run = app.registry.execute("blocks.field.rebuild", { model_id: IDS.model, field: "AU2" }, "user");
    const asked = await answerDialog(true);
    expect(asked).toBe("Перестроить поле|Поле «AU2» модели «Месторождение» будет рассчитано заново по тем же параметрам и записано поверх прежнего. Продолжить?");
    const r = await run;
    expect(r.ok, r.result).toBe(true);
    const sent = posts(/rebuild$/);
    expect(sent).toHaveLength(1);
    expect(sent[0].path).toBe(`${BASE}/AU2/rebuild`);
    expect(sent[0].body).toEqual({ expected_version: stub.version });
    expect(canon(without(sent[0].body, "expected_version"))).toBe(canon(without(F.rebuild.request, "expected_version")));
    expect(currentRun().phase).toBe("done");
    expect(currentRun().op).toBe("rebuild");
    expect(currentRun().result).toMatchObject({ field: "AU2", rows: 24, missing: 2 });
    expect(r.result).toMatch(/^Поле «AU2» перестроено в модели «Месторождение»: блоков: 24, прошло ограничитель: 24, без значения: 2; от 0 до 15, среднее 4,022727; версия модели 28$/);
    // the list is read again: AU2 is no longer «устарел»
    const h = renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(CalcWindow, { winId: windowOfFields()! })));
    expect(h).not.toContain('data-testid="props-stale-AU2"');
    expect(h).toContain('data-testid="props-stale-ORE"');
    expect(h).toContain('data-testid="fields-result"');
  });

  it("the density is made again with its two fields", async () => {
    rebuild("DENS_F", F.rebuild_density);
    const run = app.registry.execute("blocks.field.rebuild", { model_id: IDS.model, field: "DENS_F" }, "user");
    await answerDialog(true);
    const r = await run;
    expect(r.ok, r.result).toBe(true);
    expect(r.result).toMatch(/^Плотность «DENS_F» и источник «DENS_F_SRC» перестроена в модели «Месторождение»: /);
  });

  it("«Нет»: nothing is rebuilt; the assistant is asked by the registry, not by a window of ours", async () => {
    rebuild("AU2", F.rebuild);
    const run = app.registry.execute("blocks.field.rebuild", { model_id: IDS.model, field: "AU2" }, "user");
    await answerDialog(false);
    expect(await run).toEqual({ ok: true, result: "Отменено" });
    expect(posts(/rebuild$/)).toEqual([]);
    const claude = app.registry.execute("blocks.field.rebuild", { model_id: IDS.model, field: "AU2" }, "claude");
    await answerClaude(true); // the confirmation of the assistant
    expect((await claude).ok).toBe(true);
    expect(dialogStore.get().queue).toEqual([]);
    expect(posts(/rebuild$/)).toHaveLength(1);
  });

  const REFUSED: [string, string][] = [
    ["not_rebuildable", "SLOPE"],
    ["rebuild_source_lost", "CU10"],
    ["rebuild_not_found", "NOPE"],
    ["rebuild_conflict", "AU2"],
  ];
  for (const [name, field] of REFUSED) {
    it(`${name}: the words and the hint of the core, under the row of the field`, async () => {
      const c = F[name] as Case;
      const e = c.body!;
      expect(e.message).toMatch(/[А-Яа-яЁё]/);
      expect(e.hint).not.toBe("");
      stub.routes.push((m, p) => (m === "POST" && p === `${BASE}/${field}/rebuild` ? json(c.body, c.status) : undefined));
      const run = app.registry.execute("blocks.field.rebuild", { model_id: IDS.model, field }, "claude");
      await answerClaude(true);
      const r = await run;
      expect(r.ok).toBe(false);
      expect(r.result).toBe(name === "rebuild_conflict" ? `${e.message} ${e.hint} Данные модели обновлены — повторите действие.` : `${e.message} ${e.hint}`);
      expect(fieldsStore.get().issue).toMatchObject({ tab: "props", place: rowPlace(field) });
      expect(currentRun().phase).toBe("failed");
    });
  }

  it("a source that is gone (the filter was deleted): 409 source_lost says so; the field stays as it was", () => {
    expect(F.rebuild_source_lost.body).toMatchObject({ code: "source_lost" });
    expect(F.rebuild_source_lost.body.message).toBe("Исходных данных больше нет: Фильтр не найден.");
    expect(F.filter_delete.status).toBe(200);
  });
});
