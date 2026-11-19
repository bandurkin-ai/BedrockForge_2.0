/** «Фильтр земли» and «Построить ЦТМ из облака» (ADR 0014 §4–§6, подшаги 7.1.16, 7.1.18): the profiles, their
 * numbers and hints and the ranges as the core answers them (`core/tests/data/ground/profiles.json` — the answer
 * of `GET /lines/ground/profiles`, held to the kernel by pytest), the spare values until it answers, the body of
 * the request and the arguments of `ground.classify` (null — the profile's, false — off), the cloud as a source
 * of «Построение ЦТМ» (classes, step of the thinning, `from-points`), the commands with their buttons. */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import { buildBody, EMPTY_FORM, initialForm, sourcesText } from "../src/modules/dtm/build";
import { classOptions, cloudBodies, fromPointsBody, parseCell, parseClasses, requestOf } from "../src/modules/dtm/cloud";
import { commands } from "../src/modules/dtm/commands";
import {
  bodyOfArgs, cloudLayers, countsText, groundArgs, groundBody, initialGround, lastGround, paramsText, parseProfiles, pickCloud, profileFields, profileHint, profileTitle, rangeText, sameAsProfile, SPARE, withProfile,
  type GroundForm, type GroundProfiles,
} from "../src/modules/dtm/ground";
import { groundProfiles, loadGroundProfiles, resetGroundProfiles } from "../src/modules/dtm/groundProfiles";
import type { CloudSummary } from "../src/modules/lines/classes";
import { layerMenu } from "../src/modules/lines/tree";
import type { LineFileInfo, LineLayerInfo } from "../src/modules/scene/objects";
import { moduleCommands } from "../src/shell/catalog";
import { buildRibbon, flattenRibbon } from "../src/shell/ribbonConfig";
import { validateArgs } from "../src/shell/commands";
import { DEFAULT_VIEW, DEFAULT_WORKSPACE } from "../src/shell/workspace";

/** the answer of the core */
const answer = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/ground/profiles.json"), "utf-8")) as Record<string, unknown>;
const T = parseProfiles(answer)!;

const plain = (s: string): string => s.replace(/[  ]/g, " ");

const summary = (p: Partial<CloudSummary> = {}): CloudSummary => ({
  parts: 1, levels: [0], bbox: [0, 0, 0, 100, 100, 10], classes: { "1": 100 }, modes: ["class"], source: null, ground: null, dz: null, ...p,
});
const layer = (id: number, p: Partial<LineLayerInfo> = {}): LineLayerInfo => ({
  id, file_id: id, name: `Слой ${id}`, version: 1, color: null, points_only: false, line_count: 3, point_count: 30, length: 10, closed_count: 1, closed: "some",
  flat: false, extent: null, attr_schema: [], ...p,
});
const cloud = (id: number, s: Partial<CloudSummary> = {}, p: Partial<LineLayerInfo> = {}) => layer(id, { name: `survey${id}`, points_only: true, line_count: 0, point_count: 3000, closed_count: 0, closed: "none", cloud: summary(s), ...p });
const file = (id: number, layers: LineLayerInfo[]): LineFileInfo => ({ id, name: `f${id}`, folder: null, version: 1, format: null, label: null, filename: null, crs_name: null, warnings: [], layers });

const raw = cloud(7);
const filtered = cloud(8, {
  classes: { "1": 10, "2": 2627, "5": 281, "7": 82 }, modes: ["class", "h"],
  ground: { params: { profile: "plain", cr: 1, ri: 3, ct: 0.5, r_b: null, noise_r: 2, noise_k: 3, alpha_deg: 30 }, counts: { "1": 10, "2": 2627, "5": 281, "7": 82 }, at: "2026-11-19T10:00:00" },
});
const outline = layer(21, { name: "Контур" });
const files = [file(1, [raw]), file(2, [filtered]), file(3, [outline])];
const form = (p: Partial<GroundForm> = {}): GroundForm => ({ ...initialGround(T, files, 7), ...p });

describe("the profiles of «Фильтр земли» are those of the core (ADR 0014 §4, 7.1.18)", () => {
  beforeEach(resetGroundProfiles);

  it("the answer of the core: «Карьер» and «Равнина», their numbers, hints and the ranges", () => {
    expect(T.loaded).toBe(true);
    expect(T.profiles.quarry).toEqual({ title: "Карьер", hint: expect.stringContaining("рост по склону до 55°"), cr: 1, ri: 2, ct: 0.5, r_b: 5, noise: { r: 2, k_min: 3 } });
    expect(T.profiles.plain).toEqual({ title: "Равнина", hint: expect.stringContaining("рост по склону до 30°"), cr: 1, ri: 3, ct: 0.5, r_b: false, noise: { r: 2, k_min: 3 } });
    expect([T.default, T.defaults]).toEqual(["quarry", { r_b: 5, noise: { r: 2, k_min: 3 } }]);
    expect(Object.keys(T.ranges).sort()).toEqual(["cr", "ct", "noise_k", "noise_r", "r_b", "ri"]);
    expect([rangeText(T.ranges.cr), rangeText(T.ranges.ri), rangeText(T.ranges.noise_r)].map(plain)).toEqual(["0,1–50 м", "1–3", "0,5–20 м"]);
    expect(form()).toMatchObject({ cloudId: 7, profile: "quarry", base: "quarry", cr: "1", ri: "2", ct: "0,5", faces: true, rb: "5", noise: true });
    expect(profileFields(T, "plain")).toMatchObject({ ri: "3", faces: false, rb: "5" }); // faces off: the field shows the default
    expect([profileTitle(T, "plain"), profileTitle(T, "custom")]).toEqual(["Равнина", "Свой"]);
    expect(profileHint(T, "quarry")).toBe((answer.profiles as { hint: string }[])[0].hint);
  });

  it("the spare values are the answer of the core — but for the constants of the kernel in the hints", () => {
    const { profiles, loaded, ...rest } = SPARE;
    expect(loaded).toBe(false);
    expect(rest).toEqual({ default: T.default, defaults: T.defaults, ranges: T.ranges });
    for (const id of ["quarry", "plain"] as const) {
      expect({ ...profiles[id], hint: "" }).toEqual({ ...T.profiles[id], hint: "" });
      expect(profiles[id].hint).not.toMatch(/\d/); // no slope of its own: 55° and 30° come with the answer
      expect(T.profiles[id].hint.startsWith(profiles[id].hint.split(":")[0])).toBe(true);
    }
  });

  it("the numbers follow the core: another answer — other fields, ranges and titles", () => {
    const other = structuredClone(answer) as { profiles: Record<string, unknown>[]; ranges: Record<string, Record<string, unknown>>; defaults: Record<string, unknown> };
    Object.assign(other.profiles[0], { cr: 2, ri: 1, r_b: 7.5, noise: false, title: "Карьер 2", hint: "рост по склону до 60°" });
    Object.assign(other.profiles[1], { r_b: false });
    other.ranges.cr.hi = 10;
    other.defaults.r_b = 6;
    const t = parseProfiles(other)!;
    expect(initialGround(t, files, 7)).toMatchObject({ profile: "quarry", cr: "2", ri: "1", rb: "7,5", faces: true, noise: false, noiseR: "2", noiseK: "3" });
    expect(profileFields(t, "plain")).toMatchObject({ faces: false, rb: "6" });
    expect([profileTitle(t, "quarry"), profileHint(t, "quarry")]).toEqual(["Карьер 2", "рост по склону до 60°"]);
    const own = withProfile(t, initialGround(t, files, 7), "custom");
    expect(plain(groundBody(t, { ...own, cr: "20" }).error ?? "")).toBe("«Шаг ткани CR» — число от 0,1 до 10 м, например 1.");
    expect(groundBody(T, { ...own, cr: "20" }).error).toBeNull();
    // the last filter of a cloud is «Карьер» only while its numbers are those of the profile of the core
    const last = lastGround(cloud(9, { ground: { params: { profile: "quarry", cr: 1, ri: 2, ct: 0.5, r_b: 5, noise_r: 2, noise_k: 3 } } }))!;
    expect([sameAsProfile(T.profiles.quarry, last), sameAsProfile(t.profiles.quarry, last)]).toEqual([true, false]);
    expect(initialGround(t, [file(1, [cloud(9, { ground: { params: { ...last } } })])], 9)).toMatchObject({ profile: "custom", base: "quarry", cr: "1", ri: "2" });
  });

  it("an answer the window cannot show is not taken", () => {
    const broken = (change: (a: Record<string, any>) => void): GroundProfiles | null => {
      const a = structuredClone(answer) as Record<string, any>;
      change(a);
      return parseProfiles(a);
    };
    expect(parseProfiles(null)).toBeNull();
    expect(parseProfiles({ profiles: [] })).toBeNull();
    expect(broken((a) => a.profiles.pop())).toBeNull(); // no «Равнина»
    expect(broken((a) => (a.profiles[0].cr = "1"))).toBeNull();
    expect(broken((a) => (a.profiles[1].r_b = null))).toBeNull(); // off is false; null is not a profile's value
    expect(broken((a) => delete a.ranges.noise_k)).toBeNull();
    expect(broken((a) => (a.ranges.cr.lo = 60))).toBeNull();
    expect(broken((a) => (a.defaults.noise = false))).toBeNull();
    expect(broken((a) => (a.profiles[0].alpha_deg = 70))).not.toBeNull(); // what the window does not use is not checked
  });

  it("the window has the spare values until the core answers; the answer is asked once", async () => {
    expect(groundProfiles.get()).toEqual(SPARE);
    let calls = 0;
    let give: (v: unknown) => void = () => undefined;
    const get = (path: string): Promise<unknown> => {
      calls += 1;
      expect(path).toBe("/lines/ground/profiles");
      return new Promise((done) => (give = done));
    };
    const first = loadGroundProfiles(get);
    const second = loadGroundProfiles(get);
    expect(groundProfiles.get().loaded).toBe(false); // not loaded yet: the spare values are shown
    give(answer);
    expect([await first, await second]).toEqual([T, T]);
    expect([calls, groundProfiles.get().loaded, groundProfiles.get().profiles.quarry.hint]).toEqual([1, true, T.profiles.quarry.hint]);
    await loadGroundProfiles(() => Promise.reject(new Error("not asked again")));
    expect(calls).toBe(1);
  });

  it("the core is away or answers something else: the spare values stay and the next call asks again", async () => {
    expect(await loadGroundProfiles(() => Promise.reject(new Error("ядро недоступно")))).toEqual(SPARE);
    expect(await loadGroundProfiles(() => Promise.resolve({ profiles: "?" }))).toEqual(SPARE);
    expect(groundProfiles.get().loaded).toBe(false);
    expect((await loadGroundProfiles(() => Promise.resolve(answer))).loaded).toBe(true);
  });

  it("a profile sends only its name; «Свой» keeps the fields and sends the numbers", () => {
    expect(groundBody(T, withProfile(T, form(), "plain"))).toEqual({ body: { profile: "plain" }, error: null });
    const own = withProfile(T, { ...withProfile(T, form(), "plain"), cr: "0,5" }, "custom");
    expect(own).toMatchObject({ profile: "custom", base: "plain", cr: "0,5", ri: "3", faces: false });
    expect(groundBody(T, own).body).toEqual({ profile: "plain", cr: 0.5, ri: 3, ct: 0.5, r_b: false, noise: { r: 2, k_min: 3 } });
    // an empty field is the profile's value, never 0; faces switched on with no R_б — true: the core takes the
    // R_б of the profile or, over «Равнина», its default; null would be «as the profile» — off over «Равнина»
    expect(groundBody(T, { ...own, cr: "", faces: true, rb: "", noise: false }).body).toEqual({ profile: "plain", cr: null, ri: 3, ct: 0.5, r_b: true, noise: false });
    expect(groundBody(T, { ...own, noiseR: "", noiseK: "" }).body?.noise).toBe(true);
    expect(groundBody(T, { ...own, noiseR: "", noiseK: "4" }).body?.noise).toEqual({ r: null, k_min: 4 });
  });

  it("the ranges of ТЗ 7.35 Д-22", () => {
    const own = withProfile(T, form(), "custom");
    expect(groundBody(T, { ...own, cr: "0,05" }).error).toBe("«Шаг ткани CR» — число от 0,1 до 50 м, например 1.");
    expect(groundBody(T, { ...own, ri: "2,5" }).error).toBe("«Жёсткость ткани RI» — целое от 1 до 3.");
    expect(groundBody(T, { ...own, ct: "сто" }).error).toContain("«Порог земли CT» — число от 0,05 до 5 м");
    expect(groundBody(T, { ...own, rb: "25" }).error).toContain("от 1 до 20 м");
    expect(groundBody(T, { ...own, rb: "25", faces: false }).error).toBeNull(); // faces off: R_б is not read
    expect(groundBody(T, { ...own, noiseK: "0" }).error).toBe("«Число соседей для шума» — целое от 1 до 20.");
    expect(groundBody(T, { ...own, noiseR: "0,4" }).error).toContain("«Радиус шума» — число от 0,5 до 20 м");
  });

  it("the arguments of ground.classify and back: null — the profile's, false — off, in the command as in the body", () => {
    const body = groundBody(T, { ...withProfile(T, form(), "custom"), cr: "2", faces: false, noise: false }).body!;
    const args = groundArgs(7, body);
    expect(args).toEqual({ cloud_id: 7, profile: "quarry", cr: 2, ri: 2, ct: 0.5, faces: false, noise: false });
    expect(bodyOfArgs(args)).toEqual({ profile: "quarry", cr: 2, ri: 2, ct: 0.5, r_b: false, noise: false });
    expect(bodyOfArgs({ profile: "plain", noise_r: 3 })).toEqual({ profile: "plain", cr: null, ri: null, ct: null, noise: { r: 3, k_min: null } });
    expect(groundArgs(7, { profile: "plain" })).toEqual({ cloud_id: 7, profile: "plain" });
    // r_b: null is «as the profile» (it meant «off» in the command and «the profile» in the body): nothing is sent
    expect(bodyOfArgs({ profile: "quarry", r_b: null })).toEqual({ profile: "quarry", cr: null, ri: null, ct: null });
    expect(bodyOfArgs({ profile: "plain", r_b: 7.5 }).r_b).toBe(7.5); // a number switches the faces on
    expect(bodyOfArgs({ profile: "plain", faces: true }).r_b).toBe(true);
    expect(bodyOfArgs({ profile: "quarry", faces: false, r_b: 7.5 }).r_b).toBe(false); // the switch wins
    expect(bodyOfArgs({ profile: "quarry", noise: true }).noise).toBe(true);
    expect(bodyOfArgs({ profile: "quarry", noise: false, noise_r: 3 }).noise).toBe(false);
    for (const b of [{ profile: "plain", r_b: true, noise: true }, { profile: "quarry", r_b: 7.5, noise: { r: 3, k_min: null } }, { profile: "plain", r_b: false, noise: false }] as const) {
      const again = bodyOfArgs(groundArgs(7, b));
      expect([again.r_b, again.noise]).toEqual([b.r_b, b.noise]);
    }
    // the registry takes them: a switch is a boolean, a number may be null
    const command = commands.find((c) => c.id === "ground.classify")!;
    expect(validateArgs(command, args)).toBeNull();
    expect(validateArgs(command, { cloud_id: 7, r_b: null, faces: true, noise: true })).toBeNull();
    expect(validateArgs(command, { cloud_id: 7, r_b: false })).toContain("r_b"); // off is `faces: false`
    expect(command.description).toContain("faces — false: грани не искать");
    expect(command.description).toContain("не заданное число или null — значение профиля");
  });

  it("the cloud of the window and the last filter", () => {
    expect(cloudLayers(files).map((l) => l.id)).toEqual([7, 8]);
    expect(pickCloud(files, 8, 7)).toBe(8);
    expect(pickCloud(files, null, 7)).toBe(7);
    expect(pickCloud(files, null, 21)).toBeNull(); // two clouds, none selected
    expect(pickCloud([file(1, [raw])], null, null)).toBe(7); // the only cloud
    expect(lastGround(raw)).toBeNull();
    const last = lastGround(filtered)!;
    expect(initialGround(T, files, 8)).toMatchObject({ profile: "plain", base: "plain", ri: "3", faces: false });
    expect(plain(paramsText(T, last))).toBe("профиль «Равнина»: CR 1 м, RI 3, CT 0,5 м, грани выключены, шум: радиус 2 м, соседей меньше 3");
    expect(plain(countsText(last.counts))).toBe("не классифицирована — 10, земля — 2 627, высокая растительность — 281, низкая точка (шум) — 82");
    const tuned = cloud(9, { ground: { params: { profile: "quarry", cr: 2, ri: 2, ct: 0.5, r_b: 5, noise_r: null, noise_k: 3 } } });
    expect(initialGround(T, [file(1, [tuned])], 9)).toMatchObject({ profile: "custom", base: "quarry", cr: "2", noise: false });
  });
});

describe("a cloud as a source of «Построение ЦТМ» (ADR 0014 §5)", () => {
  it("the selected cloud gives its ground; the classes offered", () => {
    expect(initialForm(files, 8)).toMatchObject({ layerIds: [], clouds: [{ layerId: 8, classes: [2], cell: "" }] });
    expect(initialForm(files, 21).layerIds).toEqual([21]);
    expect(classOptions(raw).map((c) => c.code)).toEqual([1, 2]); // the ground always, even before a filter
    expect(classOptions(filtered).find((c) => c.code === 2)?.points).toBe(2627);
  });

  it("the body: the clouds, the step with a comma; a single cloud goes to from-points", () => {
    const built = buildBody({ ...EMPTY_FORM, clouds: [{ layerId: 8, classes: [2], cell: "0,5" }] }, files);
    expect(built.body?.clouds).toEqual([{ layer_id: 8, classes: [2], cell: 0.5 }]);
    expect(requestOf(built.body!)).toEqual({ path: "/wireframes/from-points", body: { cloud_id: 8, classes: [2], l_max: null, boundary_id: null, cell: 0.5, name: null, to_dtm: true } });
    const both = buildBody({ ...EMPTY_FORM, layerIds: [21], clouds: [{ layerId: 8, classes: [5, 2], cell: "" }] }, files).body!;
    expect(requestOf(both)).toMatchObject({ path: "/wireframes/build-dtm", body: { layer_ids: [21], clouds: [{ layer_id: 8, classes: [2, 5], cell: null }] } });
    const line = buildBody({ ...EMPTY_FORM, clouds: [{ layerId: 8, classes: [2], cell: "" }], boundaryLayer: 21, boundaryLine: "1" }, files).body!;
    expect(requestOf(line).path).toBe("/wireframes/build-dtm"); // a numbered line of the boundary — only build-dtm takes it
    expect(buildBody({ ...EMPTY_FORM, layerIds: [21] }, files).body).not.toHaveProperty("clouds");
    expect(plain(sourcesText({ layerIds: [], setIds: [], clouds: [{ layerId: 8, classes: [2], cell: "" }] }, files, []))).toBe("1 облако (2 627 точек отмеченных классов)");
  });

  it("tells why a cloud cannot be used", () => {
    expect(cloudBodies([{ layerId: 7, classes: [2], cell: "" }], files).error).toBe("В облаке «survey7» нет точек земли (класс 2): запустите «Фильтр земли».");
    expect(cloudBodies([{ layerId: 8, classes: [], cell: "" }], files).error).toContain("отметьте классы");
    expect(cloudBodies([{ layerId: 8, classes: [2], cell: "0" }], files).error).toContain("от 0,01 до 1000 м");
    expect(cloudBodies([{ layerId: 99, classes: [2], cell: "" }], files).error).toContain("не найдено");
    expect([parseCell(""), parseCell("1000"), parseCell("1001")]).toEqual([null, 1000, undefined]);
  });

  it("the arguments of terrain.from_points", () => {
    expect(parseClasses("2; 9,2")).toEqual([2, 9]);
    expect(parseClasses("земля")).toBeNull();
    expect(fromPointsBody(8, { l_max: 12.5 }).body).toEqual({ cloud_id: 8, classes: [2], l_max: 12.5, boundary_id: null, cell: null });
    expect(fromPointsBody(8, { classes: "2, 3", cell: 0.5, boundary_id: 21 }).body).toMatchObject({ classes: [2, 3], cell: 0.5, boundary_id: 21 });
    expect(fromPointsBody(8, { classes: "x" }).error).toContain("коды классов");
    expect(fromPointsBody(8, { cell: 0 }).error).toContain("от 0,01 до 1000 м");
  });
});

describe("the commands (ТЗ 7.35.4): a button, a tree menu, the registry", () => {
  const items = flattenRibbon(buildRibbon(DEFAULT_WORKSPACE, DEFAULT_VIEW));

  it("every command of the module has a button of «Моделирование › Поверхности»", () => {
    for (const id of ["ground.classify", "terrain.from_points"]) {
      expect(moduleCommands().some((c) => c.id === id), id).toBe(true);
      expect(items.find((i) => i.command === id)?.group, id).toBe("Моделирование › Поверхности");
    }
    expect(commands.find((c) => c.id === "ground.classify")?.confirm).toBe(true); // the classes of the cloud are replaced
  });

  it("the menu of a cloud in the tree; an ordinary layer has none of it", () => {
    const menu = layerMenu(filtered, true).flatMap((a) => (a.command ? [[a.label, a.command, a.args]] : []));
    expect(menu).toContainEqual(["Фильтр земли…", "ground.classify", { cloud_id: 8 }]);
    expect(menu).toContainEqual(["Построить ЦТМ из облака…", "terrain.from_points", { cloud_id: 8 }]);
    expect(layerMenu(outline, true).some((a) => a.command === "ground.classify")).toBe(false);
  });
});
