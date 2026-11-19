/** «Фильтр земли» and «Построить ЦТМ из облака» (ADR 0014 §4–§6, подшаг 7.1.16): the profiles and their numbers,
 * the ranges, the body of the request and the arguments of `ground.classify`, the cloud as a source of
 * «Построение ЦТМ» (classes, step of the thinning, `from-points`), the commands with their buttons. */
import { describe, expect, it } from "vitest";
import { buildBody, EMPTY_FORM, initialForm, sourcesText } from "../src/modules/dtm/build";
import { classOptions, cloudBodies, fromPointsBody, parseCell, parseClasses, requestOf } from "../src/modules/dtm/cloud";
import { commands } from "../src/modules/dtm/commands";
import {
  bodyOfArgs, cloudLayers, countsText, groundArgs, groundBody, initialGround, lastGround, paramsText, pickCloud, PROFILES, profileFields, withProfile, type GroundForm,
} from "../src/modules/dtm/ground";
import type { CloudSummary } from "../src/modules/lines/classes";
import { layerMenu } from "../src/modules/lines/tree";
import type { LineFileInfo, LineLayerInfo } from "../src/modules/scene/objects";
import { moduleCommands } from "../src/shell/catalog";
import { buildRibbon, flattenRibbon } from "../src/shell/ribbonConfig";
import { DEFAULT_VIEW, DEFAULT_WORKSPACE } from "../src/shell/workspace";

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
const form = (p: Partial<GroundForm> = {}): GroundForm => ({ ...initialGround(files, 7), ...p });

describe("the profiles of «Фильтр земли» (ADR 0014 §4)", () => {
  it("«Карьер» and «Равнина» have the numbers of the table; «Карьер» is the default", () => {
    expect(PROFILES.quarry).toEqual({ cr: 1, ri: 2, ct: 0.5, r_b: 5, noise: { r: 2, k_min: 3 } });
    expect(PROFILES.plain).toEqual({ cr: 1, ri: 3, ct: 0.5, r_b: null, noise: { r: 2, k_min: 3 } });
    expect(form()).toMatchObject({ cloudId: 7, profile: "quarry", base: "quarry", cr: "1", ri: "2", ct: "0,5", faces: true, rb: "5", noise: true });
    expect(profileFields("plain")).toMatchObject({ ri: "3", faces: false });
  });

  it("a profile sends only its name; «Свой» keeps the fields and sends the numbers", () => {
    expect(groundBody(withProfile(form(), "plain"))).toEqual({ body: { profile: "plain" }, error: null });
    const own = withProfile({ ...withProfile(form(), "plain"), cr: "0,5" }, "custom");
    expect(own).toMatchObject({ profile: "custom", base: "plain", cr: "0,5", ri: "3", faces: false });
    expect(groundBody(own).body).toEqual({ profile: "plain", cr: 0.5, ri: 3, ct: 0.5, r_b: false, noise: { r: 2, k_min: 3 } });
    // an empty field is the profile's value, never 0; faces switched on over «Равнина» with no R_б — 5 m
    expect(groundBody({ ...own, cr: "", faces: true, rb: "", noise: false }).body).toEqual({ profile: "plain", cr: null, ri: 3, ct: 0.5, r_b: 5, noise: false });
  });

  it("the ranges of ТЗ 7.35 Д-22", () => {
    const own = withProfile(form(), "custom");
    expect(groundBody({ ...own, cr: "0,05" }).error).toBe("«Шаг ткани CR» — число от 0,1 до 50 м, например 1.");
    expect(groundBody({ ...own, ri: "2,5" }).error).toBe("«Жёсткость ткани RI» — целое от 1 до 3.");
    expect(groundBody({ ...own, ct: "сто" }).error).toContain("«Порог земли CT» — число от 0,05 до 5 м");
    expect(groundBody({ ...own, rb: "25" }).error).toContain("от 1 до 20 м");
    expect(groundBody({ ...own, rb: "25", faces: false }).error).toBeNull(); // faces off: R_б is not read
    expect(groundBody({ ...own, noiseK: "0" }).error).toBe("«Число соседей для шума» — целое от 1 до 20.");
    expect(groundBody({ ...own, noiseR: "0,4" }).error).toContain("«Радиус шума» — число от 0,5 до 20 м");
  });

  it("the arguments of ground.classify and back", () => {
    const body = groundBody({ ...withProfile(form(), "custom"), cr: "2", faces: false, noise: false }).body!;
    const args = groundArgs(7, body);
    expect(args).toEqual({ cloud_id: 7, profile: "quarry", cr: 2, ri: 2, ct: 0.5, r_b: null, noise: false });
    expect(bodyOfArgs(args)).toEqual({ profile: "quarry", cr: 2, ri: 2, ct: 0.5, r_b: false, noise: false });
    expect(bodyOfArgs({ profile: "plain", noise_r: 3 })).toEqual({ profile: "plain", cr: null, ri: null, ct: null, noise: { r: 3, k_min: null } });
    expect(groundArgs(7, { profile: "plain" })).toEqual({ cloud_id: 7, profile: "plain" });
  });

  it("the cloud of the window and the last filter", () => {
    expect(cloudLayers(files).map((l) => l.id)).toEqual([7, 8]);
    expect(pickCloud(files, 8, 7)).toBe(8);
    expect(pickCloud(files, null, 7)).toBe(7);
    expect(pickCloud(files, null, 21)).toBeNull(); // two clouds, none selected
    expect(pickCloud([file(1, [raw])], null, null)).toBe(7); // the only cloud
    expect(lastGround(raw)).toBeNull();
    const last = lastGround(filtered)!;
    expect(initialGround(files, 8)).toMatchObject({ profile: "plain", base: "plain", ri: "3", faces: false });
    expect(plain(paramsText(last))).toBe("профиль «Равнина»: CR 1 м, RI 3, CT 0,5 м, грани выключены, шум: радиус 2 м, соседей меньше 3");
    expect(plain(countsText(last.counts))).toBe("не классифицирована — 10, земля — 2 627, высокая растительность — 281, низкая точка (шум) — 82");
    const tuned = cloud(9, { ground: { params: { profile: "quarry", cr: 2, ri: 2, ct: 0.5, r_b: 5, noise_r: null, noise_k: 3 } } });
    expect(initialGround([file(1, [tuned])], 9)).toMatchObject({ profile: "custom", base: "quarry", cr: "2", noise: false });
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
