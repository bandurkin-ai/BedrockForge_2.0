// The window «Шаблоны форм отчёта» (Э20, 20.14; ADR 0027 решения 16–19, 29; вопросы № 105, 106) on the answers of the core
// (`core/tests/data/resources/contract_answers.json`: templates, template_get, template_gkz, template_put, template_delete, version_conflict): the list,
// the template → the draft of the editor → the body of `PUT` (the same as the core's), the refusals 409 / 422 in Russian with a hint, the templates
// of the box read-only, the copy, the deletion after the question in our own window, the sample of the form against the form of the core.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { closeDialog, dialogStore } from "../../src/shell/dialogs";
import { windowStore } from "../../src/shell/windows";
import { listTemplates } from "../../src/modules/resources/api";
import { openTemplatesWindow, renderTemplatesWindow, windowOfTemplates } from "../../src/modules/resources/bindTemplates";
import { resourcesStore } from "../../src/modules/resources/state";
import { previewForm } from "../../src/modules/resources/templatePreview";
import { bodyOfDraft, copyDraft, draftOf, moveItem, newTemplateId, readTemplate, removeLabel, renameLabel, toggleMember } from "../../src/modules/resources/templates";
import { chooseTemplate, copyTemplate, newTemplate, removeTemplate, saveTemplate, startEditor } from "../../src/modules/resources/templatesActions";
import { putDraft, resetTemplates, setDraft, TEMPLATES_WINDOW, templatesStore } from "../../src/modules/resources/templatesState";
import { answer, FILE, json, newApp, teardown, waitFor } from "./harness";

type Sent = { method: string; path: string; query: string; body: Record<string, unknown> };
interface TplCore {
  sent: Sent[];
  force: (route: string, a: { status: number; body: Record<string, unknown> }) => void;
}

/** The stub of the templates of the core: the box answers with the contract bodies; a template of the user is kept as it was written. */
function installTemplates(): TplCore {
  const sent: Sent[] = [];
  const forced = new Map<string, { status: number; body: Record<string, unknown> }>();
  const own = new Map<string, Record<string, unknown>>();
  const box: Record<string, Record<string, unknown>> = { jorc: answer("template_get").body.template as Record<string, unknown>, gkz: answer("template_gkz").body.template as Record<string, unknown> };
  const row = (t: Record<string, unknown>) => ({ id: t.id, name: t.name, builtin: t.builtin === true, version: t.version });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = new URL(url);
      const path = u.pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      sent.push({ method, path, query: u.search, body });
      const route = `${method} ${path}`;
      const f = forced.get(route);
      if (f) {
        forced.delete(route);
        return json(f.body, f.status);
      }
      if (route === "GET /resources/templates") return json({ templates: [...Object.values(box), ...[...own.values()].sort((a, b) => String(a.name).localeCompare(String(b.name)))].map(row) });
      const m = path.match(/^\/resources\/templates\/([^/]+)$/);
      if (m) {
        const id = decodeURIComponent(m[1]);
        const have = box[id] ?? own.get(id);
        if (method === "GET") return have ? json({ template: have }) : json(answer("template_not_found").body, 404);
        if (method === "PUT") {
          const t = body.template as Record<string, unknown>;
          const version = (have ? (have.version as number) : 0) + 1;
          if ((body.expected_version ?? null) !== (have ? have.version : null)) return json(answer("version_conflict").body, 409);
          const stored = { ...t, id, version, builtin: id in box };
          if (id in box) box[id] = stored;
          else own.set(id, stored);
          return json({ id, version });
        }
        if (method === "DELETE") {
          if (id in box) return json({ code: "bad_param", param: "id", message: "Встроенный шаблон нельзя удалить.", hint: "Измените его или создайте свой шаблон." }, 422);
          if (!own.has(id)) return json(answer("template_not_found").body, 404);
          own.delete(id);
          return json(answer("template_delete").body);
        }
      }
      return json({ code: "not_found", message: "Нет", hint: route }, 404);
    }),
  );
  return { sent, force: (r, a) => void forced.set(r, a) };
}

let app: App;
let core: TplCore;
let unbind: () => void;
beforeEach(() => {
  ({ app, unbind } = newApp());
  core = installTemplates(); // after `newApp`: the stub of the window of the templates replaces the stub of the report
  resetTemplates();
  resourcesStore.set({ templates: [], templatesLoaded: false });
});
afterEach(() => teardown(unbind));

const calls = (method: string, re: RegExp) => core.sent.filter((x) => x.method === method && re.test(x.path));
const html = () => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, renderTemplatesWindow({ id: "w", kind: TEMPLATES_WINDOW } as never)));
const BODY_KEYS = ["name", "labels", "subtotals", "total", "key", "section", "columns", "balance", "units", "decimals"] as const;
const bodyOf = (t: Record<string, unknown>) => Object.fromEntries(BODY_KEYS.map((k) => [k, t[k]]));
const draft = () => templatesStore.get().draft as NonNullable<ReturnType<typeof templatesStore.get>["draft"]>;
const box = (key: "template_get" | "template_gkz") => answer(key).body.template as Record<string, unknown>;

async function answerDialog(ok: boolean, title?: string): Promise<void> {
  await vi.waitFor(() => expect(dialogStore.get().queue.length).toBe(1));
  const d = dialogStore.get().queue[0];
  expect(d.kind).toBe("confirm");
  if (d.kind === "confirm") {
    if (title) expect(d.options.title).toBe(title);
    d.resolve(ok);
  }
  closeDialog(d.id);
}

/** A template of the user in the stub: a copy of ГКЗ saved under a name. */
async function saveOwn(name = "Мой ГКЗ"): Promise<string> {
  await startEditor();
  await chooseTemplate("gkz");
  await copyTemplate();
  setDraft({ name });
  const saved = await saveTemplate();
  expect(saved).not.toBeNull();
  return (saved as { id: string }).id;
}

describe("the list and the template", () => {
  it("the list of the core: JORC and ГКЗ of the box, once read; the first one opens in the editor", async () => {
    const rows = await listTemplates();
    expect(rows).toEqual((answer("templates").body.templates as unknown[]).map((r) => expect.objectContaining(r as object)));
    await startEditor();
    expect(resourcesStore.get().templates.map((t) => t.id)).toEqual(["jorc", "gkz"]);
    expect(templatesStore.get().selected).toBe("jorc");
    expect(calls("GET", /^\/resources\/templates\/jorc$/)).toHaveLength(1);
  });

  it("the answer of the core → the template → the draft → the body of `PUT`: the same body, for JORC and for ГКЗ", () => {
    for (const key of ["template_get", "template_gkz"] as const) {
      const t = readTemplate({ template: box(key) });
      expect(t).not.toBeNull();
      expect(t?.builtin).toBe(true);
      const got = bodyOfDraft(draftOf(t as NonNullable<typeof t>));
      if (!got.ok) throw new Error(JSON.stringify(got.issue));
      expect(got.body).toEqual(bodyOf(box(key)));
    }
  });

  it("the draft of ГКЗ keeps what is special: the balance, the rows, the subtotal, the unit of the metal (кг for г/т)", () => {
    const d = draftOf(readTemplate({ template: box("template_gkz") }) as NonNullable<ReturnType<typeof readTemplate>>);
    expect(d).toMatchObject({ balance: true, labels: ["A", "B", "C1", "C2"], hasTotal: true, total: "Итого", key: "class", section: "" });
    expect(d.subtotals).toEqual([{ label: "A + B + C1", members: ["A", "B", "C1"] }]);
    expect(d.units.metal).toEqual({ g: "kg", t: "t" });
    expect(d.order.filter((m) => d.on[m])).toEqual(["tonnes", "grade", "metal"]);
  });
});

describe("the box is read-only; the copy", () => {
  it("JORC in the window: locked, nothing is sent on «Сохранить» and «Удалить»", async () => {
    await startEditor();
    expect(templatesStore.get().draft?.builtin).toBe(true);
    const page = html();
    expect(page).toContain("открыт только для просмотра");
    expect(page).toContain("Измеренные");
    expect(page).toMatch(/data-testid="tpl-save"[^>]*disabled|disabled=""[^>]*data-testid="tpl-save"/);
    expect(await saveTemplate()).toBeNull();
    expect(await removeTemplate()).toBe(false);
    expect(core.sent.some((x) => x.method === "PUT" || x.method === "DELETE")).toBe(false);
  });

  it("«Создать копию»: a new template of the user with the same rows, units and decimals and the name «… (копия)»", async () => {
    await startEditor();
    expect(await copyTemplate()).toBe(true);
    const d = templatesStore.get().draft;
    expect(d).toMatchObject({ id: null, version: null, builtin: false, name: "JORC (копия)", labels: ["Измеренные", "Выявленные", "Предполагаемые"] });
    expect(d?.units).toEqual(draftOf(readTemplate({ template: box("template_get") }) as never).units);
    // a name that is taken goes on counting
    const jorc = readTemplate({ template: box("template_get") }) as NonNullable<ReturnType<typeof readTemplate>>;
    expect(copyDraft(jorc, ["JORC (копия)"]).name).toBe("JORC (копия 2)");
    expect(newTemplateId("JORC (копия)", ["jorc", "gkz"])).toBe("jorc-kopiya");
    expect(newTemplateId("ГКЗ", ["gkz"])).toBe("gkz-2"); // a code of the box is never made
  });
});

describe("the save", () => {
  it("a new template: `PUT` of the body without a version, the code from the name; the template is read again and the list too", async () => {
    const id = await saveOwn("Мой ГКЗ");
    expect(id).toBe("moy-gkz");
    const put = calls("PUT", /templates\/moy-gkz$/);
    expect(put).toHaveLength(1);
    expect(put[0].body).toEqual({ template: { ...bodyOf(box("template_gkz")), name: "Мой ГКЗ" } });
    expect("expected_version" in put[0].body).toBe(false);
    const s = templatesStore.get();
    expect(s).toMatchObject({ selected: "moy-gkz", busy: false });
    expect(s.base).toMatchObject({ id: "moy-gkz", version: 1, builtin: false });
    expect(s.note).toContain("сохранён");
    await waitFor(() => resourcesStore.get().templates.some((t) => t.id === "moy-gkz"));
  });

  it("a template of the user: `PUT` with `expected_version` of the version read; the version grows", async () => {
    await saveOwn();
    const d = templatesStore.get().draft;
    putDraft({ ...(d as NonNullable<typeof d>), labels: ["C2", "C1", "B", "A"], subtotals: [] });
    const saved = await saveTemplate();
    expect(saved).toEqual({ id: "moy-gkz", version: 2 });
    const put = calls("PUT", /templates\/moy-gkz$/)[1];
    expect(put.body.expected_version).toBe(1);
    expect((put.body.template as { labels: string[] }).labels).toEqual(["C2", "C1", "B", "A"]);
    expect(templatesStore.get().base?.version).toBe(2);
  });

  it("409 `version_conflict` (the template was saved in another window): told in Russian; «Перечитать» takes the stored one, the edits go", async () => {
    await saveOwn();
    setDraft({ name: "Правка" });
    core.force("PUT /resources/templates/moy-gkz", answer("version_conflict"));
    const run = saveTemplate();
    await answerDialog(true, "Шаблон изменён в другом окне");
    expect(await run).toBeNull();
    expect(calls("GET", /templates\/moy-gkz$/).length).toBeGreaterThanOrEqual(2); // read again
    expect(templatesStore.get().draft?.name).toBe("Мой ГКЗ");
    expect(templatesStore.get().busy).toBe(false);
  });

  it("409 answered «Оставить»: the words of the core stay in the window and the edits are not lost", async () => {
    await saveOwn();
    setDraft({ name: "Правка" });
    core.force("PUT /resources/templates/moy-gkz", answer("version_conflict"));
    const run = saveTemplate();
    await answerDialog(false);
    expect(await run).toBeNull();
    const v = answer("version_conflict").body as { message: string; hint: string };
    expect(templatesStore.get().issue).toMatchObject({ place: "general", message: v.message, hint: v.hint });
    expect(templatesStore.get().draft?.name).toBe("Правка");
  });

  it("422 `bad_param {param}` of the core: told in the place of the editor (`labels.1` — the row 2) with the hint, nothing is lost", async () => {
    await saveOwn();
    core.force("PUT /resources/templates/moy-gkz", { status: 422, body: { param: "labels.1", code: "bad_param", message: "Подпись «B» повторяется.", hint: "Подписи строк не повторяются." } });
    expect(await saveTemplate()).toBeNull();
    expect(templatesStore.get().issue).toEqual({ place: "labels", index: 1, message: "Подпись «B» повторяется.", hint: "Подписи строк не повторяются." });
    expect(html()).toContain("Подписи строк не повторяются.");
  });

  it("423 (the project is open for reading): the words of the core in the window", async () => {
    await saveOwn();
    core.force("PUT /resources/templates/moy-gkz", answer("read_only"));
    expect(await saveTemplate()).toBeNull();
    expect(templatesStore.get().issue?.message).toContain("только для чтения");
  });

  it("a code taken meanwhile in another window (409 for a new template): the list is read and the next free code is tried once", async () => {
    await saveOwn("Свой");
    await waitFor(() => resourcesStore.get().templates.some((t) => t.id === "svoy"));
    await newTemplate();
    putDraft({ ...draft(), name: "Свой", labels: ["A", "B"] });
    resourcesStore.set({ templates: resourcesStore.get().templates.filter((t) => t.id !== "svoy") }); // the list of this window is behind
    const saved = await saveTemplate();
    expect(saved?.id).toBe("svoy-2");
    expect(calls("PUT", /templates\/svoy$/).length).toBeGreaterThanOrEqual(2); // the first, new, then the refused one
    expect(calls("PUT", /templates\/svoy-2$/)).toHaveLength(1);
  });

  it("a wrong template is told by the window before anything is sent (the same words as the core)", async () => {
    await startEditor();
    await newTemplate();
    const d = draft;
    expect(await saveTemplate()).toBeNull();
    expect(templatesStore.get().issue).toMatchObject({ place: "name" });
    setDraft({ name: "Свой" });
    expect(await saveTemplate()).toBeNull();
    expect(templatesStore.get().issue).toMatchObject({ place: "labels", message: "В шаблоне нет строк.", hint: "Добавьте хотя бы одну подпись группы." });
    putDraft({ ...d(), labels: ["A", "a"] });
    expect(bodyOfDraft(d())).toMatchObject({ ok: false, issue: { place: "labels", index: 1, message: "Подпись «a» повторяется.", hint: "Подписи строк не повторяются." } });
    putDraft({ ...d(), labels: ["A", "Прочие"] });
    expect(bodyOfDraft(d())).toMatchObject({ ok: false, issue: { place: "labels", index: 1 } });
    putDraft({ ...d(), labels: ["A", "B"], subtotals: [{ label: "A + B", members: ["A"] }] });
    expect(bodyOfDraft(d())).toMatchObject({ ok: false, issue: { place: "subtotals", index: 0, message: "Подытог «A + B» объединяет меньше двух строк." } });
    putDraft({ ...d(), subtotals: [], section: "class" });
    expect(bodyOfDraft(d())).toMatchObject({ ok: false, issue: { place: "section", message: "Разделы и строки не могут идти по одному ключу." } });
    putDraft({ ...d(), section: "", on: { tonnes: false, volume: false, grade: false, metal: false } });
    expect(bodyOfDraft(d())).toMatchObject({ ok: false, issue: { place: "columns" } });
    expect(core.sent.some((x) => x.method === "PUT")).toBe(false);
  });
});

describe("the deletion", () => {
  it("the question in our own window: «Отмена» leaves the template; «Удалить» sends `DELETE` with the version read, the list is read again", async () => {
    await saveOwn();
    const no = removeTemplate();
    await answerDialog(false, "Удалить шаблон");
    expect(await no).toBe(false);
    expect(calls("DELETE", /./)).toHaveLength(0);
    const yes = removeTemplate();
    await answerDialog(true);
    expect(await yes).toBe(true);
    const del = calls("DELETE", /templates\/moy-gkz$/);
    expect(del).toHaveLength(1);
    expect(del[0].query).toBe("?expected_version=1");
    expect(del[0].path).toBe("/resources/templates/moy-gkz");
    expect(templatesStore.get().note).toContain("удалён");
    expect(resourcesStore.get().templates.map((t) => t.id)).toEqual(["jorc", "gkz"]);
    expect(templatesStore.get().selected).toBe("jorc");
  });

  it("409 on the deletion: the words of the core, the template stays", async () => {
    await saveOwn();
    core.force("DELETE /resources/templates/moy-gkz", answer("version_conflict"));
    const run = removeTemplate();
    await answerDialog(true);
    expect(await run).toBe(false);
    expect(templatesStore.get().issue?.message).toBe((answer("version_conflict").body as { message: string }).message);
    expect(templatesStore.get().base?.id).toBe("moy-gkz");
  });
});

describe("the changes are not dropped by a click", () => {
  it("a template with unsaved edits: another one is chosen only after the question; «Отбросить» goes on, «Отмена» stays", async () => {
    await startEditor();
    await copyTemplate();
    setDraft({ name: "Что-то" });
    const stay = chooseTemplate("gkz");
    await answerDialog(false, "Шаблон изменён");
    expect(await stay).toBe(false);
    expect(templatesStore.get().draft?.name).toBe("Что-то");
    const go = chooseTemplate("gkz");
    await answerDialog(true);
    expect(await go).toBe(true);
    expect(templatesStore.get().selected).toBe("gkz");
  });
});

describe("the editor works on the draft", () => {
  it("rows: ↑ ↓ and the dragging (`moveItem`); a row renamed or taken out follows into the subtotals", () => {
    expect(moveItem(["A", "B", "C"], 0, 2)).toEqual(["B", "C", "A"]);
    expect(moveItem(["A", "B", "C"], 1, 0)).toEqual(["B", "A", "C"]);
    expect(moveItem(["A", "B"], 0, 5)).toEqual(["A", "B"]);
    const d = draftOf(readTemplate({ template: box("template_gkz") }) as NonNullable<ReturnType<typeof readTemplate>>);
    const renamed = renameLabel(d, 0, "A1");
    expect(renamed.labels[0]).toBe("A1");
    expect(renamed.subtotals[0].members).toEqual(["A1", "B", "C1"]);
    const taken = removeLabel(removeLabel(renamed, 0), 0);
    expect(taken.subtotals[0].members).toEqual(["C1"]);
    expect(bodyOfDraft(taken)).toMatchObject({ ok: false, issue: { place: "subtotals" } }); // one member is not a subtotal
    expect(toggleMember(taken, 0, "C2", true).subtotals[0].members).toEqual(["C1", "C2"]);
  });

  it("the columns in the order of the draft; the sections by the domain; units and decimals go into the body", async () => {
    await startEditor();
    await copyTemplate();
    const d = draft();
    putDraft({ ...d, order: ["metal", "grade", "tonnes", "volume"], on: { tonnes: true, volume: true, grade: true, metal: true }, section: "domain", units: { tonnes: "mt", volume: "m3", metal: { g: "kg", t: "g" } }, decimals: { tonnes: 3, volume: 2, grade: 4, metal: 0 } });
    const got = bodyOfDraft(templatesStore.get().draft as never);
    if (!got.ok) throw new Error(JSON.stringify(got.issue));
    expect(got.body).toMatchObject({ columns: ["metal", "grade", "tonnes", "volume"], section: "domain", units: { tonnes: "mt", volume: "m3", metal: { g: "kg", t: "g" } }, decimals: { tonnes: 3, volume: 2, grade: 4, metal: 0 } });
  });
});

describe("the sample of the form (a template → a form, nothing is recalculated)", () => {
  const form = (answer("report_get").body as { form: { columns: { title: string; unit: string; decimals: number; group: string | null }[]; sections: { rows: { label: string; type: string }[] }[] } }).form;

  it("JORC: the columns, the units, the decimals and the rows are those of the form of the core", () => {
    const t = readTemplate({ template: box("template_get") }) as NonNullable<ReturnType<typeof readTemplate>>;
    const p = previewForm(t, [{ name: "AU", unit: "г/т" }]);
    expect(p.columns.map((c) => ({ title: c.title, unit: c.unit, decimals: c.decimals, group: c.group }))).toEqual(form.columns.map((c) => ({ title: c.title, unit: c.unit, decimals: c.decimals, group: c.group })));
    const core = form.sections[0].rows.map((r) => [r.label, r.type]);
    // the core shows the rows that the result has; the sample shows all the rows of the template in the same order
    expect(p.sections[0].rows.map((r) => [r.label, r.type]).filter(([l]) => core.some(([c]) => c === l))).toEqual(core);
    expect(p.sections[0].rows.map((r) => r.label)).toEqual(["Измеренные", "Выявленные", "Изм. + Выяв.", "Предполагаемые", "Итого"]);
  });

  it("the subtotal and the total are SUMS (ḡ = Σ t·g / Σ t, the metal in the unit of the template: тыс. унц)", () => {
    const t = readTemplate({ template: box("template_get") }) as NonNullable<ReturnType<typeof readTemplate>>;
    const rows = previewForm(t, [{ name: "Au", unit: "г/т" }]).sections[0].rows;
    const plain = (s: string) => s.replace(/\s/g, "");
    expect(rows.map((r) => plain(r.cells[0]))).toEqual(["1250", "2500", "3750", "3750", "7500"]); // тыс. т: 1250 + 2500 = 3750
    expect(rows[2].cells[1]).toBe("1,10"); // (1,25·0,8 + 2,5·1,25) / 3,75
    expect(plain(rows[2].cells[2])).toBe((((1.25e6 * 0.8 + 2.5e6 * 1.25) * 1e-3) / 31.1034768).toFixed(1).replace(".", ","));
  });

  it("ГКЗ: «Балансовые» and «Забалансовые» columns, кг for г/т; % — тонны of metal", () => {
    const t = readTemplate({ template: box("template_gkz") }) as NonNullable<ReturnType<typeof readTemplate>>;
    const p = previewForm(t, [{ name: "Au", unit: "г/т" }]);
    expect(p.columns.map((c) => c.group)).toEqual(["Балансовые", "Балансовые", "Балансовые", "Забалансовые", "Забалансовые", "Забалансовые"]);
    expect(p.columns[2].title).toBe("Металл Au, кг");
    const cu = previewForm(t, [{ name: "Cu", unit: "%" }]);
    expect(cu.columns[2].title).toBe("Металл Cu, т");
    expect(cu.sections[0].rows[0].cells[2].replace(/\s/g, "")).toBe("4375"); // 1,25 млн т · 0,35 % = 4375 т
    expect(p.sections[0].rows.at(-1)?.label).toBe("Итого");
  });

  it("sections: the form is cut into tables; the template without a total has no total row; a subtotal with one row left is not shown", () => {
    const t = readTemplate({ template: box("template_gkz") }) as NonNullable<ReturnType<typeof readTemplate>>;
    const cut = previewForm({ ...t, section: "domain", total: null, subtotals: [{ label: "S", members: ["A", "Нет такой"] }] });
    expect(cut.sections).toHaveLength(2);
    expect(cut.sections[0].title).toBe("Домен (тело) 1");
    expect(cut.sections[0].rows.map((r) => r.label)).toEqual(["A", "B", "C1", "C2"]);
  });

  it("the window shows the sample of the chosen template, only Russian words", async () => {
    await startEditor();
    const page = html();
    expect(page).toContain("Пример формы");
    expect(page).toContain("Руда, тыс. т");
    expect(page).toContain("Изм. + Выяв.");
    expect(page).toContain("Отчёты не пересчитываются");
    await chooseTemplate("gkz");
    expect(html()).toContain("Балансовые");
  });
});

describe("the command and the window", () => {
  it("`resources.templates` opens the window once (a second call raises it) and reads the templates; the window renders for the shell", async () => {
    const r = await app.registry.execute("resources.templates", {}, "user");
    expect(r.ok).toBe(true);
    expect(r.result).toContain("Шаблоны форм отчёта");
    expect(windowStore.get().windows.filter((w) => w.kind === TEMPLATES_WINDOW)).toHaveLength(1);
    await app.registry.execute("resources.templates", {}, "user");
    expect(windowStore.get().windows.filter((w) => w.kind === TEMPLATES_WINDOW)).toHaveLength(1);
    expect(windowOfTemplates()).not.toBeNull();
    await waitFor(() => resourcesStore.get().templatesLoaded);
    expect(openTemplatesWindow()).toBe(windowOfTemplates());
    expect(renderTemplatesWindow({ id: "x", kind: "other" } as never)).toBeNull();
    const page = html();
    for (const word of ["Шаблоны", "Создать копию", "Сохранить", "Закрыть", "встроенный"]) expect(page).toContain(word);
  });

  it("another project: a clean editor", async () => {
    await startEditor();
    expect(templatesStore.get().draft).not.toBeNull();
    const { projectStore } = await import("../../src/modules/projects/state");
    projectStore.set({ current: { uid: "p2", name: "Д" } as never, version: 2 });
    await startEditor();
    expect(templatesStore.get().uid).toBe("p2");
    expect(templatesStore.get().selected).toBe("jorc"); // read again for the new project
    expect(FILE.templates.status).toBe(200);
  });
});
