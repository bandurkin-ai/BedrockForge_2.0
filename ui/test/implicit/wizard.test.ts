// The wizard «Новая модель» (Э19, 19.22): the defaults of the core, the save (`POST` / `PUT /models`, the refusals under their field, 409
// «Перечитать» / «Сохранить как новую»), the four steps drawn (the number of the cells by the defaults of the core, the groups, «Прочее», the
// kernel, the smoothing, the trend), the editing of the groups.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { EMPTY_FORM, formOfMeta, type GroupForm, type ModelForm } from "../../src/modules/implicit/form";
import { Wizard } from "../../src/modules/implicit/Wizard";
import { WizardBoundary } from "../../src/modules/implicit/WizardBoundary";
import { WizardDomains } from "../../src/modules/implicit/WizardDomains";
import { WizardInterp } from "../../src/modules/implicit/WizardInterp";
import { WizardResult } from "../../src/modules/implicit/WizardResult";
import { implicitStore, loadRows } from "../../src/modules/implicit/state";
import { ensureDefaults, putForm, rereadModel, saveWizard, setForm, setIssue, setStep, startEditModel, startNew, wizardStore } from "../../src/modules/implicit/wizardState";
import { A, CANONICAL, FIELD, ID, installCore, NAME, newApp, teardown, waitFor, type Core } from "./harness";

let app: App;
let core: Core;
beforeEach(() => {
  core = installCore();
  app = newApp();
});
afterEach(teardown);

const render = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));
const flat = (s: string): string => s.replace(/[  ]/g, " ");
const requests = (route: string) => core.sent.filter((r) => `${r.method} ${r.path}` === route);
const domain = (id: number, name: string, codes: string[]): GroupForm => ({ id, name, type: "domain", codes, raw: null });
const filled = (patch: Partial<ModelForm> = {}): ModelForm => ({ ...EMPTY_FORM, name: "Новая", box: ["0", "0", "-300", "1000", "800", "100"], setId: ID.set, table: ID.table, field: FIELD, groups: [domain(1, "Дайка", ["DY"]), domain(2, "Песчаник", ["SS", "SSc"])], ...patch });

describe("the defaults of the core", () => {
  it("are asked once; the answer is kept for the ranges and the placeholders", async () => {
    await ensureDefaults();
    await ensureDefaults();
    expect(requests("GET /implicit/defaults")).toHaveLength(1);
    expect(wizardStore.get().defaults.phase).toBe("ok");
    expect(wizardStore.get().defaults.data?.params.h_divisor).toBe(200);
  });
  it("a refusal is the words on the page, not a made-up default", async () => {
    core.force("GET /implicit/defaults", { status: 404, body: { code: "not_found", message: "Нет такого маршрута", hint: "Обновите программу." } });
    await ensureDefaults();
    expect(wizardStore.get().defaults).toMatchObject({ phase: "error", data: null });
    putForm(filled());
    setStep(1);
    expect(render(createElement(Wizard, { winId: "w" }))).toContain("Нет такого маршрута");
  });
});

describe("the save", () => {
  it("a new model: POST /models with the body of the form; the wizard goes on editing it", async () => {
    await ensureDefaults();
    putForm(filled());
    const saved = await saveWizard();
    expect(saved).toEqual({ id: ID.model2, name: "Новая", version: 1 });
    const sent = requests("POST /implicit/models")[0].body;
    expect(sent.boundary).toEqual({ box: [0, 0, -300, 1000, 800, 100] });
    expect(sent.data).toEqual({ set_id: ID.set, table: ID.table, field: FIELD });
    expect(sent.groups).toEqual([
      { id: 1, name: "Дайка", type: "domain", codes: ["DY"] },
      { id: 2, name: "Песчаник", type: "domain", codes: ["SS", "SSc"] },
    ]);
    expect(wizardStore.get()).toMatchObject({ editing: { id: ID.model2, version: 1 }, dirty: false, issue: null, saving: false });
    expect(requests("GET /implicit/models").length).toBeGreaterThan(0);
  });
  it("a stored model: PUT with the version read; the version of the answer is the next one", async () => {
    await loadRows();
    await startEditModel(ID.model);
    expect(wizardStore.get().editing).toEqual({ id: ID.model, version: core.version });
    expect(wizardStore.get().form.name).toBe(NAME);
    setForm({ kernel: "cubic" });
    const saved = await saveWizard();
    const put = requests(`PUT /implicit/models/${ID.model}`)[0].body;
    expect(put.expected_version).toBe(3);
    expect(put.kernel).toBe("cubic");
    expect(put.groups).toEqual(CANONICAL.groups);
    expect(saved.version).toBe(4);
    expect(wizardStore.get().editing?.version).toBe(4);
  });
  it("a wrong form is not sent: the words stand under the field, the step is the field's", async () => {
    putForm(filled({ nMin: "10" }));
    await expect(saveWizard()).rejects.toMatchObject({ code: "bad_param" });
    expect(requests("POST /implicit/models")).toHaveLength(0);
    expect(wizardStore.get().issue).toMatchObject({ param: "pou.n_min" });
    expect(wizardStore.get().step).toBe(3);
    expect(flat(render(createElement(WizardInterp)))).toContain("N_min");
    expect(render(createElement(WizardInterp))).toContain('data-testid="imp-issue-nmin"');
  });
  it("a refusal of the core stands under its field with its hint (the draft key `bad_param` of the core: pou.n_min)", async () => {
    putForm(filled());
    core.force("POST /implicit/models", A.bad_param);
    await expect(saveWizard()).rejects.toMatchObject({ code: "bad_param", message: A.bad_param.body.message, hint: A.bad_param.body.hint });
    expect(wizardStore.get().issue).toMatchObject({ param: "pou.n_min", message: A.bad_param.body.message, hint: A.bad_param.body.hint });
    expect(wizardStore.get().step).toBe(3);
    const html = render(createElement(WizardInterp));
    expect(html).toContain('data-testid="imp-issue-nmin"');
    expect(html).toContain("Наименьшее число точек участка N_min");
    expect(html).toContain("Задайте от 20 до 500.");
  });
  it("the refusals of the core (§7) in Russian: an open wireframe under the wireframe, a trend that is gone under the trend", async () => {
    putForm(filled({ boundary: "wireframe", wireframeId: ID.openWire }));
    core.force("POST /implicit/models", { status: 422, body: { ...A.refusals.wireframe_open.body, param: "boundary.wireframe_id" } });
    await expect(saveWizard()).rejects.toMatchObject({ code: "wireframe_open" });
    setStep(1);
    const html = render(createElement(WizardBoundary));
    expect(html).toContain('data-testid="imp-issue-wireframe"');
    expect(html).toContain("Каркас не замкнут");
    expect(html).toContain("Запустите «Исправить каркас»");
    putForm(filled({ trend: "list", trendId: 77 }));
    core.force("POST /implicit/models", A.refusals.trend_not_found);
    await expect(saveWizard()).rejects.toMatchObject({ code: "trend_not_found" });
    expect(wizardStore.get().issue).toMatchObject({ param: "trend_id" });
    expect(wizardStore.get().step).toBe(3);
  });
  it("a refusal without a place is the words over the footer; a name that is taken is the name's", async () => {
    putForm(filled());
    core.force("POST /implicit/models", { status: 409, body: { code: "name_taken", message: "Модель с таким названием уже есть.", hint: "Выберите другое название." } });
    await expect(saveWizard()).rejects.toMatchObject({ code: "name_taken" });
    expect(wizardStore.get().issue).toMatchObject({ param: "name" });
    core.force("POST /implicit/models", A.refusals.too_many_points);
    await expect(saveWizard()).rejects.toMatchObject({ code: "too_many_points" });
    setStep(1);
    expect(render(createElement(Wizard, { winId: "w" }))).toContain('data-testid="imp-issue-general"');
  });
  it("409 version_conflict: «Перечитать» takes the stored model, «Сохранить как новую» writes another", async () => {
    await loadRows();
    await startEditModel(ID.model);
    core.version = 9;
    await expect(saveWizard()).rejects.toMatchObject({ code: "version_conflict" });
    expect(wizardStore.get().issue).toMatchObject({ code: "version_conflict", reread: true });
    expect(render(createElement(Wizard, { winId: "w" }))).toContain('data-testid="imp-conflict"');
    await rereadModel();
    expect(wizardStore.get().editing?.version).toBe(9);
    const again = await saveWizard({ asNew: true });
    expect(again.id).toBe(ID.model2);
    expect(requests("POST /implicit/models")).toHaveLength(1);
  });
  it("the project is read-only: the buttons say so", () => {
    app.store.set({ projectReadOnly: true });
    const html = render(createElement(Wizard, { winId: "w" }));
    expect(html).toMatch(/data-testid="imp-save"[^>]*disabled|disabled=""[^>]*data-testid="imp-save"/);
    expect(html).toContain("Проект открыт только для чтения");
  });
  it("the first wrong thing jumps to its step (setIssue)", () => {
    setIssue({ param: "groups.0.codes", code: "bad_param", message: "m", hint: "h" });
    expect(wizardStore.get().step).toBe(2);
    setIssue({ param: "h", code: "bad_param", message: "m", hint: "h" });
    expect(wizardStore.get().step).toBe(1);
    setIssue(null);
    expect(wizardStore.get().step).toBe(1);
  });
});

describe("step 1: the boundary and the step", () => {
  it("the grid by the defaults of the core: the cells along the axes, the step, the levels", async () => {
    await ensureDefaults();
    putForm(filled());
    const html = flat(render(createElement(WizardBoundary)));
    expect(html).toContain("Сетка 152 × 120 × 60 = 1 094 400 ячеек");
    expect(html).toContain("по умолчанию: диагональ охвата / 200");
    expect(html).toContain("уровней 2");
    expect(html).toContain("Диапазон: 0,1–100 м.");
  });
  it("a typed step is the step used; too many cells along an axis is told", async () => {
    await ensureDefaults();
    putForm(filled({ box: ["0", "0", "0", "1000000", "10", "10"], h: "0,1" }));
    const html = flat(render(createElement(WizardBoundary)));
    expect(html).not.toContain("по умолчанию");
    expect(html).toContain('data-testid="imp-grid-many"');
  });
  it("without a boundary there is no count (never 0)", () => {
    putForm({ ...EMPTY_FORM });
    expect(render(createElement(WizardBoundary))).toContain("Число ячеек будет известно");
  });
  it("a wireframe: the closed ones are offered, the open one is marked and cannot be chosen; the DTMs go under «Сверху ограничить»", () => {
    putForm({ ...EMPTY_FORM, boundary: "wireframe", wireframeId: ID.wire, dtmId: ID.dtm });
    const html = render(createElement(WizardBoundary));
    expect(html).toContain("Рудное тело");
    expect(html).toMatch(/<option value="51" disabled="">Дыра \(не замкнут\)/);
    expect(html).toContain("ЦТМ «Рельеф»");
    expect(flat(html)).toContain("Сетка");
    expect(html).not.toContain('data-testid="imp-box-0"');
  });
});

describe("step 2: the domains of the lithology", () => {
  it("the data, the groups by the age with their codes, «Прочее», the manual layers", () => {
    putForm(filled({ manual: [{ layerId: ID.layer, role: "inside", group: 2 }] }));
    const html = render(createElement(WizardDomains));
    expect(html).toContain('data-testid="imp-group-0"');
    expect(html).toContain('data-testid="imp-group-1"');
    expect(html).toContain('data-testid="imp-code-1-SSc"');
    expect(html).toContain("Прочее — всё остальное в границе");
    expect(html).toContain("Первая группа — самая молодая");
    expect(html).toContain('placeholder="30"');
    expect(html).toContain('placeholder="5"');
    expect(html).toContain("Контакт дайки");
    expect(html).toContain('data-testid="imp-manual-0"');
  });
  it("a group that is not a domain is only carried: the kind in words, no codes to edit", () => {
    putForm({ ...filled(), groups: [{ id: 5, name: "Au", type: "shell", codes: [], raw: null }, domain(6, "Дайка", ["DY"])] });
    const html = render(createElement(WizardDomains));
    expect(html).toContain("оболочка");
    expect(html).not.toContain('data-testid="imp-group-name-0"><input');
    expect(html).toContain('data-testid="imp-code-add-1"');
    expect(html).not.toContain('data-testid="imp-code-add-0"');
  });
  it("the refusal of a group stands under the group", () => {
    putForm(filled());
    setIssue({ param: "groups.1.codes.0", code: "bad_param", message: "Код «SS» уже есть в группе «Дайка».", hint: "Код входит в одну группу модели." });
    const html = render(createElement(WizardDomains));
    expect(html).toContain('data-testid="imp-issue-group-codes-1"');
    expect(html).toContain("Код входит в одну группу модели.");
  });
});

describe("step 3: the interpolation", () => {
  it("the kernel, the smoothing «Вручную» with the default of the core, the anisotropy by hand", () => {
    putForm(filled());
    const html = render(createElement(WizardInterp));
    for (const t of ["Линейное (−r)", "Кубическое (r³)", "Конечный радиус (Вендланд)", "Вручную: s задаёт человек", "Авто: ядро подбирает η"]) expect(html).toContain(t);
    expect(html).not.toContain('data-testid="imp-kernel-radius"');
    expect(html).not.toContain('data-testid="imp-cond"');
    expect(html).toContain('data-testid="imp-s"');
    expect(html).toContain("Анизотропия вручную");
    expect(html).toContain("Тренд из списка");
  });
  it("«Авто» asks the target condition number with the default of the core and its range; the finite radius asks a", async () => {
    await ensureDefaults();
    putForm(filled({ smoothing: "auto", kernel: "wendland" }));
    const html = flat(render(createElement(WizardInterp)));
    expect(html).toContain('data-testid="imp-cond"');
    expect(html).toContain('placeholder="100000000"');
    expect(html).toContain("Диапазон: 10000–1000000000000.");
    expect(html).toContain('data-testid="imp-kernel-radius"');
  });
  it("the trend of the list: the global and the structural ones with the kind in words; the anisotropy by hand: the angles and the ratios", () => {
    putForm(filled({ trend: "manual" }));
    const html = render(createElement(WizardInterp));
    expect(html).toContain('data-testid="imp-ang-2"');
    expect(html).toContain('data-testid="imp-ratio-2"');
    expect(html).toContain("a2 / a1");
    putForm(filled({ trend: "list" }));
    const list = render(createElement(WizardInterp));
    expect(list).toContain('data-testid="imp-trend-id"');
  });
});

describe("step 4: the result", () => {
  it("the summary of the form and the clipping", async () => {
    await ensureDefaults();
    putForm(filled({ clip: "wireframe", clipId: ID.wire, kernel: "cubic", smoothing: "auto" }));
    const html = flat(render(createElement(WizardResult)));
    expect(html).toContain('data-testid="imp-sum-name">Новая<');
    expect(html).toContain("охват 0; 0; -300; 1000; 800; 100");
    expect(html).toContain("152 × 120 × 60 = 1 094 400 ячеек");
    expect(html).toContain("Дайка (домен: DY); Песчаник (домен: SS, SSc)");
    expect(html).toContain("Кубическое (r³)");
    expect(html).toContain("авто, cond по умолчанию");
    expect(html).toContain('data-testid="imp-clip-id"');
    expect(html).toContain("Рудное тело");
  });
  it("an empty list of groups is told: only «Прочее»", () => {
    putForm({ ...EMPTY_FORM });
    expect(render(createElement(WizardResult))).toContain("нет (только «Прочее»)");
  });
});

describe("the window", () => {
  it("four steps by name, the first page; the footer has the save, the save with the build, close", () => {
    startNew();
    const html = render(createElement(Wizard, { winId: "w" }));
    for (const t of ["Граница и шаг", "Домены литологии", "Интерполяция", "Итог", "Сохранить и построить", "Назад", "Далее", "Закрыть"]) expect(html).toContain(t);
    expect(html).toContain('data-testid="imp-step-1"');
    expect(html).not.toContain("window.confirm");
  });
  it("another project gives a clean wizard", async () => {
    putForm(filled());
    setStep(3);
    expect(wizardStore.get().step).toBe(3);
    const { resetWizard } = await import("../../src/modules/implicit/wizardState");
    resetWizard();
    expect(wizardStore.get()).toMatchObject({ step: 1, editing: null, dirty: false });
    expect(wizardStore.get().form).toEqual(EMPTY_FORM);
  });
  it("the stored model fills the form from `meta`", async () => {
    await loadRows();
    await startEditModel(ID.model, 2);
    expect(wizardStore.get().step).toBe(2);
    expect(wizardStore.get().form.groups).toHaveLength(3);
    expect(wizardStore.get().form).toEqual(formOfMeta(NAME, CANONICAL));
    expect(implicitStore.get().details[ID.model].version).toBe(core.version);
    await waitFor(() => true);
  });
});
