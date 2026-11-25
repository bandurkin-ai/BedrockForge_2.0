// The post-processing of the realizations (Э18б, 18.35) on the real answers of the core (`post_result`, `sis_post_result`, `sis_cat_post_result`,
// `post_field_exists`, `post_version_conflict`, `sis_cat_post_thresholds`): the prefix, the names of the fields (the same as the core's), the body
// of `POST /simulations/{id}/post` (the version of the BLOCK MODEL), the thresholds of the probability, the categories (probabilities and the mode),
// the question «Заменить поля?», the command `simulate.post` (the window without the parameters, the run with them), the window drawn.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { closeDialog, dialogStore } from "../../src/shell/dialogs";
import { windowStore } from "../../src/shell/windows";
import { blocksStore } from "../../src/modules/blocks/state";
import { readPost } from "../../src/modules/simulation/api";
import { renderSimulationWindow } from "../../src/modules/simulation/bind";
import { postArgsOf } from "../../src/modules/simulation/PostWindow";
import { choosePost, defaultPostPrefix, effectiveStats, existingFields, fieldNames, postBodyOf, postStore, postThresholds, POST_WINDOW, setPost, sourceOf, statsFor, thresholdName } from "../../src/modules/simulation/postState";
import { loadDetails, loadRows } from "../../src/modules/simulation/state";
import { flat, INFOS, installSimCore, MODEL_VERSION, newApp, requests, S, SID, SIM_MODEL, teardown, waitFor, type SimCore } from "./harness";

let app: App;
let core: SimCore;
beforeEach(async () => {
  core = installSimCore();
  app = newApp();
  await loadRows();
});
afterEach(teardown);

const src = (id: number) => sourceOf(INFOS[id].meta);
const POSTS = (id: number) => requests(core, `POST /geostat/simulations/${id}/post`);
const render = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));
const ALL = ["mean", "std", "p10", "p50", "p90", "prob"] as const;

describe("the names of the fields are the core's", () => {
  it("SGS: «AU_SGS_» and MEAN, STD, P10, P50, P90, PROB_GT_1", () => {
    const s = src(SID.sgs);
    expect(defaultPostPrefix(s)).toBe(S.post_result.body.prefix);
    expect(fieldNames(defaultPostPrefix(s), ALL, [1], null)).toEqual(S.post_result.body.fields);
  });
  it("SIS by thresholds: «AU_SIS_»; co-SGS: «AU_COSGS_»", () => {
    expect(defaultPostPrefix(src(SID.sis))).toBe(S.sis_post_result.body.prefix);
    expect(fieldNames(defaultPostPrefix(src(SID.sis)), ALL, [1], null)).toEqual(S.sis_post_result.body.fields);
    expect(defaultPostPrefix(src(SID.co))).toBe("AU_COSGS_");
  });
  it("the categories: PROB_<code> of every category and MODE, as the core names them", () => {
    const s = src(SID.cat);
    expect(s.categories).toEqual({ codes: [1, 2, 3], labels: ["Окисленные", "Переходные", "Первичные"] });
    expect(defaultPostPrefix(s)).toBe(S.sis_cat_post_result.body.prefix);
    expect(fieldNames(defaultPostPrefix(s), ["prob", "mode"], [], s.categories)).toEqual(S.sis_cat_post_result.body.fields);
    expect(statsFor(s)).toEqual(["prob", "mode"]);
    expect(statsFor(src(SID.sgs))).toEqual(ALL);
  });
  it("the name of a threshold: six significant digits, no dot", () => {
    expect(thresholdName(1)).toBe("1");
    expect(thresholdName(0.5)).toBe("0_5");
    expect(thresholdName(2.5)).toBe("2_5");
    expect(thresholdName(1.23456789)).toBe("1_23457");
  });
  it("the fields the model has already are told before the run", () => {
    expect(existingFields(["AU", "AU_SGS_MEAN"], ["AU_SGS_MEAN", "AU_SGS_STD"])).toEqual(["AU_SGS_MEAN"]);
  });
});

describe("the body of the post-processing", () => {
  it("nothing typed: the version of the block model only — the core's defaults (MEAN, STD, P10, P50, P90)", () => {
    expect(postBodyOf({ prefix: "", stats: null, thresholds: "", replace: false }, src(SID.sgs), MODEL_VERSION)).toEqual({ expected_version: MODEL_VERSION });
    expect(effectiveStats(src(SID.sgs), null)).toEqual(["mean", "std", "p10", "p50", "p90"]);
    expect(effectiveStats(src(SID.cat), null)).toEqual(["prob", "mode"]);
  });
  it("the probability needs the thresholds; they go as numbers, in the order typed, without repeats", () => {
    const p = { prefix: "", stats: [...ALL], thresholds: "1; 2,5", replace: false };
    expect(postBodyOf(p, src(SID.sgs), 3)).toEqual({ expected_version: 3, stats: [...ALL], thresholds: [1, 2.5] });
    expect(postBodyOf({ ...p, thresholds: "" }, src(SID.sgs), 3)).toMatchObject({ param: "thresholds", message: "Для вероятности превышения не заданы пороги." });
    expect(postBodyOf({ ...p, thresholds: "1; abc" }, src(SID.sgs), 3)).toMatchObject({ param: "thresholds.1" });
    expect(postBodyOf({ ...p, thresholds: "1; 1" }, src(SID.sgs), 3)).toMatchObject({ param: "thresholds", code: "bad_param" });
    expect(postThresholds(Array.from({ length: 21 }, (_, i) => i).join("; "))).toMatchObject({ param: "thresholds" });
    // the thresholds without the probability are not sent
    expect(postBodyOf({ ...p, stats: ["mean"], thresholds: "1" }, src(SID.sgs), 3)).toEqual({ expected_version: 3, stats: ["mean"] });
  });
  it("the prefix: letters, digits and «_», up to 40; the replace flag", () => {
    expect(postBodyOf({ prefix: " AU_E_ ", stats: null, thresholds: "", replace: true }, src(SID.sgs), 3)).toEqual({ expected_version: 3, prefix: "AU_E_", replace: true });
    expect(postBodyOf({ prefix: "AU-E", stats: null, thresholds: "", replace: false }, src(SID.sgs), 3)).toMatchObject({ param: "prefix", message: "Префикс полей: допустимы буквы, цифры и «_»." });
    expect(postBodyOf({ prefix: "я".repeat(41), stats: null, thresholds: "", replace: false }, src(SID.sgs), 3)).toMatchObject({ param: "prefix", message: expect.stringContaining("длиннее 40") });
  });
  it("the categories have no thresholds (the core's words); a statistic must be chosen", () => {
    const r = postBodyOf({ prefix: "", stats: null, thresholds: "1", replace: false }, src(SID.cat), 3);
    expect(r).toMatchObject({ param: "thresholds", code: "bad_param", message: S.sis_cat_post_thresholds.body.message, hint: S.sis_cat_post_thresholds.body.hint });
    expect(postBodyOf({ prefix: "", stats: ["mode"], thresholds: "", replace: false }, src(SID.cat), 3)).toEqual({ expected_version: 3, stats: ["mode"] });
    expect(postBodyOf({ prefix: "", stats: [], thresholds: "", replace: false }, src(SID.sgs), 3)).toMatchObject({ param: "stats", message: "Не выбрано ни одной статистики." });
  });
  it("the arguments of the command are what the form stands for; the untouched is absent", () => {
    expect(postArgsOf(8, { prefix: "", stats: null, thresholds: "", replace: false })).toEqual({ id: 8, run: true });
    expect(postArgsOf(8, { prefix: " X_ ", stats: ["mean", "prob"], thresholds: "1; 2", replace: true })).toEqual({ id: 8, run: true, prefix: "X_", stats: "mean;prob", thresholds: "1; 2", replace: true });
  });
  it("the reader of the result", () => {
    const r = readPost(S.post_result.body)!;
    expect(r).toMatchObject({ simulation_id: SID.sgs, model_id: SIM_MODEL.id, version: S.post_result.body.version, prefix: "AU_SGS_", nodes: 200, realizations: 5 });
    expect(r.fields).toEqual(S.post_result.body.fields);
    expect(readPost({})).toBeNull();
  });
});

describe("simulate.post", () => {
  it("with the parameters it runs: the version of the block model goes, the answer tells the fields and the version", async () => {
    const r = await app.registry.execute("simulate.post", { id: SID.sgs, stats: "mean;std;p10;p50;p90;prob", thresholds: "1" }, "user");
    expect(POSTS(SID.sgs)[0].body).toEqual({ expected_version: MODEL_VERSION, stats: ["mean", "std", "p10", "p50", "p90", "prob"], thresholds: [1] });
    expect(r.ok).toBe(true);
    expect(r.result).toBe(`Постобработка записана в модель «${SIM_MODEL.name}» (версия ${S.post_result.body.version}): ${S.post_result.body.fields.join(", ")}.`);
    expect(postStore.get()).toMatchObject({ phase: "done", result: { prefix: "AU_SGS_", fields: S.post_result.body.fields } });
  });
  it("`run: true` runs it with the defaults without the window; without both the window opens", async () => {
    const r = await app.registry.execute("simulate.post", { id: SID.sgs, run: true }, "user");
    expect(r.ok).toBe(true);
    expect(POSTS(SID.sgs)[0].body).toEqual({ expected_version: MODEL_VERSION });
    expect(windowStore.get().windows.filter((w) => w.kind === POST_WINDOW)).toHaveLength(0);
    const w = await app.registry.execute("simulate.post", { id: SID.sis }, "user");
    expect(w).toEqual({ ok: true, result: "Открыто окно «Постобработка реализаций»" });
    expect(windowStore.get().windows.filter((x) => x.kind === POST_WINDOW)).toHaveLength(1);
    expect(postStore.get().simId).toBe(SID.sis);
    await app.registry.execute("simulate.post", {}, "user");
    expect(windowStore.get().windows.filter((x) => x.kind === POST_WINDOW)).toHaveLength(1);
    expect(postStore.get().simId).toBe(SID.sis);
  });
  it("the categories: the probabilities and the mode, no thresholds", async () => {
    const bad = await app.registry.execute("simulate.post", { id: SID.cat, thresholds: "1", stats: "prob" }, "user");
    expect(bad).toMatchObject({ ok: false, result: expect.stringContaining(S.sis_cat_post_thresholds.body.message) });
    expect(postStore.get().issue).toMatchObject({ param: "thresholds" });
    expect(POSTS(SID.cat)).toHaveLength(0);
    core.postJob = { status: "done", result: S.sis_cat_post_result.body, error: null };
    const ok = await app.registry.execute("simulate.post", { id: SID.cat, run: true }, "user");
    expect(ok.ok).toBe(true);
    expect(ok.result).toContain("LITH_SIS_MODE");
    expect(postStore.get().result?.fields).toEqual(S.sis_cat_post_result.body.fields);
  });
  it("a wrong statistic, a wrong threshold and a wrong prefix are told before the core is asked", async () => {
    const stat = await app.registry.execute("simulate.post", { id: SID.sgs, stats: "mean;max" }, "user");
    expect(stat).toMatchObject({ ok: false, result: expect.stringContaining("«max»") });
    const t = await app.registry.execute("simulate.post", { id: SID.sgs, stats: "prob" }, "user");
    expect(t).toMatchObject({ ok: false, result: expect.stringContaining("не заданы пороги") });
    expect(postStore.get().issue).toMatchObject({ param: "thresholds" });
    const p = await app.registry.execute("simulate.post", { id: SID.sgs, prefix: "A-B" }, "user");
    expect(p.ok).toBe(false);
    expect(postStore.get().issue).toMatchObject({ param: "prefix" });
    expect(POSTS(SID.sgs)).toHaveLength(0);
  });
  it("the fields are in the model already: «Заменить поля», «Заменить» sends `replace: true`; «Нет» writes nothing", async () => {
    core.force(`POST /geostat/simulations/${SID.sgs}/post`, { status: 409, body: S.post_field_exists.body });
    const run = app.registry.execute("simulate.post", { id: SID.sgs, run: true }, "user");
    await waitFor(() => dialogStore.get().queue.length > 0);
    const d = dialogStore.get().queue[0];
    expect(d.kind === "confirm" && d.options.title).toBe("Заменить поля");
    expect(d.kind === "confirm" && d.options.message).toContain(S.post_field_exists.body.message);
    if (d.kind === "confirm") d.resolve(true);
    closeDialog(d.id);
    expect((await run).ok).toBe(true);
    expect(POSTS(SID.sgs).map((p) => p.body.replace)).toEqual([undefined, true]);

    core.force(`POST /geostat/simulations/${SID.sgs}/post`, { status: 409, body: S.post_field_exists.body });
    const no = app.registry.execute("simulate.post", { id: SID.sgs, run: true }, "user");
    await waitFor(() => dialogStore.get().queue.length > 0);
    const d2 = dialogStore.get().queue[0];
    if (d2.kind === "confirm") d2.resolve(false);
    closeDialog(d2.id);
    const r = await no;
    expect(r).toMatchObject({ ok: false, result: expect.stringContaining("Постобработка не запущена") });
    expect(postStore.get().phase).toBe("idle");
    expect(POSTS(SID.sgs)).toHaveLength(3);
  });
  it("`replace: true` goes without the question; a refusal of the core is told with its hint and stays in the window", async () => {
    await app.registry.execute("simulate.post", { id: SID.sgs, replace: true }, "user");
    expect(POSTS(SID.sgs)[0].body).toMatchObject({ replace: true });
    expect(dialogStore.get().queue).toHaveLength(0);
    core.force(`POST /geostat/simulations/${SID.sgs}/post`, { status: 409, body: S.post_version_conflict.body });
    const r = await app.registry.execute("simulate.post", { id: SID.sgs, run: true }, "user");
    expect(r).toMatchObject({ ok: false });
    expect(postStore.get()).toMatchObject({ phase: "failed", error: { code: "version_conflict", message: S.post_version_conflict.body.message, hint: S.post_version_conflict.body.hint } });
  });
  it("a failed job tells its words; a cancelled one writes nothing", async () => {
    core.postJob = { status: "failed", result: null, error: { code: "no_realizations", message: "Нет реализаций.", hint: "Запустите моделирование." } };
    const r = await app.registry.execute("simulate.post", { id: SID.sgs, run: true }, "user");
    expect(r).toMatchObject({ ok: false });
    expect(postStore.get().error).toMatchObject({ code: "no_realizations", hint: "Запустите моделирование." });
    core.postJob = { status: "cancelled", result: null, error: null };
    const c = await app.registry.execute("simulate.post", { id: SID.sgs, run: true }, "user");
    expect(c.result).toContain("отменена");
    expect(postStore.get().phase).toBe("idle");
  });
  it("«только просмотр»: no post-processing (it writes to the model); the block model that is not there is refused", async () => {
    app.store.set({ projectReadOnly: true });
    expect(await app.registry.execute("simulate.post", { id: SID.sgs, run: true }, "user")).toEqual({ ok: false, result: "Проект открыт только для чтения" });
    app.store.set({ projectReadOnly: false });
    blocksStore.set({ list: [] });
    const r = await app.registry.execute("simulate.post", { id: SID.sgs, run: true }, "user");
    expect(r).toMatchObject({ ok: false, result: expect.stringContaining("Блочная модель №") });
    expect(POSTS(SID.sgs)).toHaveLength(0);
  });
});

describe("the window", () => {
  const html = async (id: number) => {
    await loadDetails(id);
    await app.registry.execute("simulate.post", { id }, "user");
    const w = windowStore.get().windows.find((x) => x.kind === POST_WINDOW)!;
    return flat(render(renderSimulationWindow(w) as ReturnType<typeof createElement>));
  };
  it("a continuous variable: the six statistics, the prefix by default, the names to be written, the thresholds when the probability is on", async () => {
    let h = await html(SID.sgs);
    for (const k of ALL) expect(h).toContain(`data-testid="post-stat-${k}"`);
    expect(h).toContain('placeholder="AU_SGS_"');
    expect(h).toContain("AU_SGS_MEAN, AU_SGS_STD, AU_SGS_P10, AU_SGS_P50, AU_SGS_P90.");
    expect(h).not.toContain('data-testid="post-thresholds"');
    setPost({ stats: [...ALL], thresholds: "1; 2,5" });
    h = await html(SID.sgs);
    expect(h).toContain('data-testid="post-thresholds"');
    expect(h).toContain("AU_SGS_PROB_GT_1, AU_SGS_PROB_GT_2_5");
    setPost({ prefix: "E_" });
    expect(await html(SID.sgs)).toContain("E_MEAN");
  });
  it("the fields the model has are told; the categories have their two statistics and the names of the fields of the codes", async () => {
    blocksStore.set({ details: { [SIM_MODEL.id]: { ...SIM_MODEL, fields: [...SIM_MODEL.fields, { name: "AU_SGS_MEAN", kind: "number", unit: null }] } } });
    let h = await html(SID.sgs);
    expect(h).toContain("В модели уже есть: AU_SGS_MEAN.");
    choosePost(null);
    h = await html(SID.cat);
    expect(h).toContain('data-testid="post-stat-prob"');
    expect(h).toContain('data-testid="post-stat-mode"');
    expect(h).not.toContain('data-testid="post-stat-mean"');
    expect(h).toContain("LITH_SIS_PROB_1, LITH_SIS_PROB_2, LITH_SIS_PROB_3, LITH_SIS_MODE");
  });
  it("the words under the field and the result written", async () => {
    await app.registry.execute("simulate.post", { id: SID.sgs, stats: "prob" }, "user");
    let h = await html(SID.sgs);
    expect(h).toContain('data-testid="post-issue-thresholds"');
    postStore.set({ issue: null });
    await app.registry.execute("simulate.post", { id: SID.sgs, run: true }, "user");
    h = await html(SID.sgs);
    expect(h).toContain('data-testid="post-done"');
    expect(h).toContain("AU_SGS_PROB_GT_1");
  });
  it("a row without realizations: the run is off, with the reason", async () => {
    core.list = { simulations: [{ ...S.simulations.body.simulations[0], last_run: null }] };
    await loadRows();
    const h = await html(SID.sgs);
    expect(h).toContain('data-testid="post-empty"');
    expect(h).toMatch(/<button[^>]*disabled[^>]*data-testid="post-go"/);
  });
});
