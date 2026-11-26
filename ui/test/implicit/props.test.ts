// «Свойства» of an implicit model (Э19, 19.23) on the REAL answers of the core: `model` (the parameters and the results of the last build as the core stores
// them), `model_stale`, `model_error`, `build_result` (the journal), `params.json` (`cases.*.canonical`: the anisotropy by hand, «Авто» smoothing, the cubic kernel).
// The facts, the groups of the last build, the anisotropy with its ellipsoid, the panel drawn, and the command `implicit.show` (the points and the ellipsoid in 3D).
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { lineageStore } from "../../src/modules/lineage/state";
import { readInfo } from "../../src/modules/implicit/api";
import { ImplicitProps } from "../../src/modules/implicit/ImplicitProps";
import { anisotropyFacts, anisotropyOf, drawable, ellipsoidFrame, groupStats, journalOfResult, kernelText, resultFacts, settingsFacts, smoothingText } from "../../src/modules/implicit/propsModel";
import { implicitStore, loadDetails, loadRows } from "../../src/modules/implicit/state";
import { implicitViewStore } from "../../src/modules/implicit/viewState";
import { ERROR_ID, installReal, MODEL_ID, PARAMS, R, realApp, teardown, type RealCore } from "./real";

let app: App;
let core: RealCore;
beforeEach(async () => {
  core = installReal();
  app = realApp();
  await loadRows();
});
afterEach(() => {
  lineageStore.set({ statuses: {}, version: 0 });
  teardown();
});

const render = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));
const flat = (s: string): string => s.replace(/[  ]/g, " ").replace(/[−‑]/g, "-").replace(/&#x27;/g, "'");
const text = (html: string): string => flat(html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " "));
const META = (R.model.body as { meta: Record<string, unknown> }).meta;
const VEIN = PARAMS.cases.vein_expand.canonical as Record<string, unknown>;
const AUTO = PARAMS.cases.shells_auto.canonical as Record<string, unknown>;
const fact = (facts: { label: string; value: string }[], label: string): string => flat(facts.find((f) => f.label === label)?.value ?? "<нет>");

describe("the facts of the model the core stores", () => {
  it("the settings: the kernel, η, the step h, the patches, the data, the clipping", () => {
    const f = settingsFacts(META);
    expect(fact(f, "Ядро")).toBe("Линейное (-r)");
    expect(fact(f, "Сглаживание (η)")).toBe("вручную, по типу группы (контакт 0, градиент 0,05)");
    expect(fact(f, "Шаг h")).toBe("8 м, уровней 2");
    expect(fact(f, "Участки")).toBe("до 1 000 точек, не меньше 100, перекрытие 0,25");
    expect(fact(f, "Данные")).toBe("поле «CODE», D_max 30 м, шаг по скважине 5 м");
    expect(fact(f, "Обрезка")).toBe("границей модели");
  });
  it("another kernel, the smoothing by hand and «Авто», the radius", () => {
    expect(kernelText(VEIN)).toBe("Кубическое (r³)");
    expect(smoothingText(VEIN)).toBe("вручную, η = 0,01");
    expect(smoothingText(AUTO)).toBe("авто: cond ≤ 1,00e8");
    expect(kernelText({ kernel: "wendland", kernel_radius: 150 })).toBe("Конечный радиус (Вендланд), радиус 150 м");
    expect(fact(settingsFacts(VEIN), "Обрезка")).toBe("каркасом, тела не меньше 100 м³");
  });
  it("the numbers of the last build: the points, the patches, the residual, cond, η, the time", () => {
    const f = resultFacts(META);
    expect(fact(f, "Точек")).toBe("3 316");
    expect(fact(f, "Участков")).toBe("16");
    expect(fact(f, "Невязка (макс.)")).toBe("2,65e-12 м");
    expect(fact(f, "cond (макс.)")).toBe("2,43e7");
    expect(fact(f, "η (макс.)")).toBe("0");
    expect(fact(f, "Время")).toMatch(/^\d+(,\d)? с$/);
    expect(fact(f, "Последний запуск")).toMatch(/^построение, \d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
    expect(f.find((x) => x.label === "Взято из прежнего")).toBeUndefined();
    expect(resultFacts({ stats: null })).toEqual([]);
    expect(resultFacts(VEIN)).toEqual([]); // the canonical of a model never built has no results
  });
  it("by groups: the names from the groups of the model, «Прочее» for a null", () => {
    const g = groupStats({ meta: META });
    expect(g.map((x) => [x.name, flat(x.points), x.patches, x.cond])).toEqual([
      ["INT", "1 241", "8", "1,14e5"],
      ["SS", "2 075", "8", "2,43e7"],
    ]);
    expect(groupStats({ meta: { ...META, stats: { by_group: [{ group: null, points: 5 }] } } })[0]).toMatchObject({ name: "Прочее", points: "5", resid: "—" });
  });
  it("the journal of the job: the lines the core wrote", () => {
    const j = journalOfResult(R.build_result.body);
    expect(j).toHaveLength(6);
    expect(j[0]).toContain("Группа «INT»: точек 1241, участков 8");
    expect(journalOfResult({ journal: [1, "a"] })).toEqual(["a"]);
    expect(journalOfResult(null)).toEqual([]);
  });
});

describe("the anisotropy and its ellipsoid", () => {
  it("none: a ball, nothing to draw", () => {
    const a = anisotropyOf(META);
    expect(a.source).toBe("none");
    expect(anisotropyFacts(a)).toEqual([{ label: "Анизотропия", value: "нет (шар)" }]);
    expect(drawable(a)).toBe(false);
  });
  it("by hand: the angles and the ratios as the core stores them; the ellipsoid of the same numbers", () => {
    const a = anisotropyOf(VEIN);
    expect(a).toMatchObject({ source: "manual", ang1: 45, ang2: -30, ang3: 0, ratios: [1, 0.5, 0.25], known: true });
    const f = anisotropyFacts(a);
    expect(fact(f, "Анизотропия")).toBe("задана вручную");
    expect(fact(f, "Углы ang1, ang2, ang3")).toBe("45°, -30°, 0°");
    expect(fact(f, "Отношения осей")).toBe("1 : 0,5 : 0,25");
    expect(drawable(a)).toBe(true);
    // the frame: the centre of the box, the major axis a quarter of the longest side
    expect(ellipsoidFrame(VEIN)).toEqual({ centre: [250, 250, 100], size: 125 });
    // the model built has `extent`
    expect(ellipsoidFrame(META)).toEqual({ centre: [210, 210, 225], size: 105 });
    expect(ellipsoidFrame({})).toBeNull();
  });
  it("a trend: the angles of the trend of the project; a trend that is not read (or a structural one) has no single ellipsoid", () => {
    const meta = { ...VEIN, anisotropy: null, trend_id: 9 };
    const a = anisotropyOf(meta, (id) => (id === 9 ? { ang1: 10, ang2: 20, ang3: 30, ratios: [1, 0.4, 0.2] } : null));
    expect(a).toMatchObject({ source: "trend", trendId: 9, known: true, ang1: 10 });
    expect(fact(anisotropyFacts(a, "Общий тренд"), "Анизотропия")).toBe("из тренда «Общий тренд»");
    expect(drawable(a)).toBe(true);
    const lost = anisotropyOf(meta);
    expect(lost).toMatchObject({ source: "trend", known: false });
    expect(drawable(lost)).toBe(false);
    expect(fact(anisotropyFacts(lost), "Оси")).toContain("локальные оси");
  });
});

describe("the panel drawn", () => {
  const open = async (id = MODEL_ID) => {
    await loadDetails(id);
    return render(createElement(ImplicitProps, { id }));
  };
  it("the model built: the parameters, the numbers, the groups, the outputs; no error, nothing stale", async () => {
    const t = text(await open());
    for (const s of ["Неявная модель", "Литология", "Линейное (-r)", "до 1 000 точек", "нет (шар)", "3 316", "2,65e-12 м", "2,43e7", "INT", "SS", "Литология · Прочее", "Точки данных в 3D", "Журнал последней сборки"]) expect(t).toContain(s);
    expect(t).not.toContain("устарела");
    expect(t).not.toContain("не построена");
    // the volume of a surface is a gap, not 0
    expect(t).toMatch(/Литология · SS · поверхность поверхность — 360 635/);
    const html = await open();
    expect(html).toContain('data-testid="props-implicit"');
    expect(html).toContain('data-state="ready"');
    expect(html).not.toContain('data-testid="props-implicit-error"');
    expect(html).not.toContain('data-testid="props-implicit-stale"');
  });
  it("the data changed after the build: «Модель устарела: причина»", async () => {
    core.list = [{ ...core.list[0], version: 8, stale: true, stale_reason: "изменены параметры" }];
    core.details.set(MODEL_ID, R.model_stale.body);
    await loadRows();
    const t = text(await open());
    expect(t).toContain("Устарела: изменены параметры. Модель устарела: изменены параметры. «Обновить» пересчитает только изменённые участки.");
  });
  it("the build failed: the words of the core with the group; nothing built is shown as built", async () => {
    core.list = [{ id: ERROR_ID, name: "Не построится", version: 1, state: "error", stale: false, stale_reason: null, source_lost: false, groups: [{ id: 1, name: "INT", type: "domain" }, { id: 2, name: "Призрак", type: "domain" }], outputs: [], stats: null }];
    await loadRows();
    const html = await open(ERROR_ID);
    expect(html).toContain('data-testid="props-implicit-error"');
    expect(text(html)).toContain("В данных группы нет контактов: поверхность не определена. Проверьте коды группы и таблицу или добавьте ручные точки и линии. (группа «Призрак»)");
    expect(text(html)).toContain("Модель ещё не построена");
    expect(text(html)).toContain("Сборки не было.");
  });
  it("the anisotropy by hand: the facts and the check box of the ellipsoid, on", async () => {
    core.details.set(MODEL_ID, { ...R.model.body, meta: { ...META, anisotropy: VEIN.anisotropy, kernel: "cubic" } });
    const html = await open();
    const t = text(html);
    expect(t).toContain("задана вручную");
    expect(t).toContain("45°, -30°, 0°");
    expect(t).toContain("1 : 0,5 : 0,25");
    expect(t).toContain("Кубическое (r³)");
    const box = /<input type="checkbox"([^>]*)\/>Эллипсоид в 3D/.exec(html);
    expect(box).not.toBeNull();
    expect(box![1]).not.toContain("disabled");
    // without anisotropy the box is off and says why
    core.details.set(MODEL_ID, R.model.body);
    await loadDetails(MODEL_ID);
    const ball = render(createElement(ImplicitProps, { id: MODEL_ID }));
    expect(/<input type="checkbox"([^>]*)\/>Эллипсоид в 3D/.exec(ball)![1]).toContain("disabled");
    expect(text(ball)).toContain("У модели нет анизотропии.")
  });
  it("the buttons are the commands of the registry; the build of a model never built is not offered a copy", async () => {
    const html = await open();
    for (const s of ["props-implicit-update", "props-implicit-domains", "props-implicit-to_wireframe", "props-implicit-delete"]) expect(html).toContain(`data-testid="${s}"`);
    core.list = [{ ...core.list[0], stats: null, outputs: [] }];
    await loadRows();
    expect(await open()).toMatch(/data-testid="props-implicit-to_wireframe"[^>]*disabled|disabled=""[^>]*data-testid="props-implicit-to_wireframe"/);
  });
  it("a model that is not in the list draws nothing", () => {
    expect(render(createElement(ImplicitProps, { id: 4242 }))).toBe("");
  });
});

describe("`implicit.show`: the points and the ellipsoid in 3D", () => {
  it("the points of a group: read once, the answer says how many and of what kind", async () => {
    const r = await app.registry.execute("implicit.show", { model_id: MODEL_ID, group_id: 1, what: "points" });
    expect(r.ok).toBe(true);
    expect(flat(r.result)).toBe("Литология: точки группы «INT»: показано 12 из 1 241 (в 3D первые по порядку ядра) (снаружи 12)");
    const sent = core.sent.filter((s) => s.path.endsWith("/points/preview"));
    expect(sent).toHaveLength(1);
    expect(sent[0].body).toEqual({ group_id: 1, limit: 50000 });
    expect(implicitViewStore.get().points).toEqual(["6:1"]);
    const hide = await app.registry.execute("implicit.show", { model_id: MODEL_ID, group_id: 1, what: "points", visible: false });
    expect(flat(hide.result)).toBe("Литология: точки группы «INT» скрыты");
    expect(implicitViewStore.get().points).toEqual([]);
    expect(implicitViewStore.get().entries).toEqual({});
  });
  it("without a group — every group of the model; a group the model has not got is refused", async () => {
    const all = await app.registry.execute("implicit.show", { what: "points" }); // the only model of the project
    expect(all.ok).toBe(true);
    expect(implicitViewStore.get().points).toEqual(["6:1", "6:3"]);
    const bad = await app.registry.execute("implicit.show", { model_id: MODEL_ID, group_id: 77, what: "points" });
    expect(bad).toMatchObject({ ok: false });
    expect(bad.result).toContain("В модели «Литология» нет группы №77");
  });
  it("a group the core could not read gives its words and is not left shown", async () => {
    core.force(`POST /implicit/models/${MODEL_ID}/points/preview`, R.preview_no_contacts as never);
    const r = await app.registry.execute("implicit.show", { model_id: MODEL_ID, group_id: 1, what: "points" });
    expect(r.ok).toBe(false);
    expect(r.result).toContain("не прочитаны");
    expect(implicitViewStore.get().points).toEqual([]);
  });
  it("the ellipsoid: a model without anisotropy is refused in Russian; one with it is shown; a model that is not there is refused", async () => {
    await loadDetails(MODEL_ID);
    const none = await app.registry.execute("implicit.show", { model_id: MODEL_ID, what: "ellipsoid" });
    expect(none.ok).toBe(false);
    expect(none.result).toContain("нет анизотропии");
    core.details.set(MODEL_ID, { ...R.model.body, meta: { ...META, anisotropy: VEIN.anisotropy } });
    const ok = await app.registry.execute("implicit.show", { model_id: MODEL_ID, what: "ellipsoid" });
    expect(ok).toEqual({ ok: true, result: "Эллипсоид анизотропии модели «Литология» показан в 3D" });
    expect(implicitViewStore.get().ellipsoids).toEqual([MODEL_ID]);
    await app.registry.execute("implicit.show", { model_id: MODEL_ID, what: "ellipsoid", visible: false });
    expect(implicitViewStore.get().ellipsoids).toEqual([]);
    expect((await app.registry.execute("implicit.show", { model_id: 999, what: "points" })).result).toContain("не найдена");
  });
  it("a model that leaves the list takes what was shown of it", async () => {
    await app.registry.execute("implicit.show", { model_id: MODEL_ID, group_id: 1, what: "points" });
    core.list = [];
    await loadRows();
    expect(implicitViewStore.get().points).toEqual([]);
  });
  it("the model is read again (its version moved): the points follow it", async () => {
    await app.registry.execute("implicit.show", { model_id: MODEL_ID, group_id: 1, what: "points" });
    core.list = [{ ...core.list[0], version: 8 }];
    await loadRows();
    await new Promise((r) => setTimeout(r, 30));
    expect(core.sent.filter((s) => s.path.endsWith("/points/preview"))).toHaveLength(2);
    expect(implicitViewStore.get().entries["6:1"]).toMatchObject({ status: "ready", version: 8 });
    expect(readInfo(R.model_stale.body).version).toBe(8);
    expect(implicitStore.get().rows[0].version).toBe(8);
  });
});
