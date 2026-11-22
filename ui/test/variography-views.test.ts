// The fan, the map, the normal scores, the exchange and the models in the tree (16.25; ТЗ 8.6.4; ADR 0022 §5, §8,
// §12–§15, decisions 8, 12, 22) on the REAL answers of the core (`core/tests/data/variography/window_answers.json`):
// `job.result` of `variography.map`, the mirror of `fan.turn` (a click on a ray → the angles, 1e−9°), `/curve` =
// the formulas of the window, `/back` (the model of Z, the share), the models (stale, rebuild, export, import).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";
import { commands } from "../src/modules/variography/commands";
import { readBack, readCalc, readCurveAnswer, readFit, readModelRow } from "../src/modules/variography/api";
import { backReason, backSeries, BackPanel, tailsBody, DEFAULT_TAILS } from "../src/modules/variography/BackPanel";
import { reloadPoints } from "../src/modules/variography/bind";
import { readJobCalc } from "../src/modules/variography/calc";
import { askExportPath, exchangeFileName, exportFlow, importFlow } from "../src/modules/variography/exchange";
import { FanView, fanHMax, fanSeries } from "../src/modules/variography/FanView";
import { planeText, readMapCalc, readRebuild, samePlane, thetaInPlane, turnAngles, turnModel } from "../src/modules/variography/mapCalc";
import { MapView, planeAxes, selectedTheta, type MapJob } from "../src/modules/variography/MapView";
import { axisOf, EMPTY_MODEL, gammaAlong, readModel, type VModel } from "../src/modules/variography/model";
import { modelMenu, modelsByName, propertiesText, rebuildReason, staleText } from "../src/modules/variography/ModelsTree";
import { dropWork, newWork, setWork, workOf } from "../src/modules/variography/state";

const A = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/variography/window_answers.json"), "utf-8"));
const MAP = readMapCalc(A.job_map);
const FIT = readFit(A.fit);
const JOB: MapJob = { live: null, error: null, busy: false, run: async () => undefined, cancel: () => undefined };
const WIN = "vario-views";
afterEach(() => dropWork(WIN));
const close = (a: number, b: number, tol = 1e-9) => Math.abs(a - b) <= tol * Math.max(1, Math.abs(b));

describe("the answer of /map", () => {
  it("the plane, the fan (θ, world az), the rose, the map, the stages", () => {
    expect(MAP.key).toMatch(/^[0-9a-f]{64}$/);
    expect(MAP.plane).toEqual({ ang1: 0, ang2: 0, ang3: 0 });
    expect(MAP.fresh).toBe(false);
    expect(Object.keys(MAP.stages).sort()).toEqual(["fan", "map", "read", "total", "trajectories", "write"]);
    expect(MAP.fan!.map((d) => [d.theta, d.az, d.plunge])).toEqual(A.job_map.fan.directions.map((d: { theta: number; az: number; plunge: number }) => [d.theta, d.az, d.plunge]));
    expect(MAP.fan![0].gamma[0]).toBeNull(); // a lag without pairs stays null
    expect(MAP.rose!.rays).toHaveLength(6);
    expect(MAP.map!.gamma).toHaveLength(9);
    expect(MAP.sigma2).toBe(A.job_map.sigma2);
    expect(planeText(MAP.plane)).toBe("Плоскость a1–a2: ang1 0° · ang2 0° · ang3 0°");
    // a1 north, a2 WEST (the mirror of the plane)
    expect(planeAxes(MAP.plane)).toEqual({ a1: "Аз 0° · Пог 0°", a2: "Аз 270° · Пог 0°" });
    expect(readMapCalc({ ...A.job_map, fan: null, rose: null, map: null })).toMatchObject({ fan: null, rose: null, map: null });
  });

  it("the series of the rays: the world direction of each ray, the model along it", () => {
    const s = fanSeries(MAP);
    expect(s.map((x) => x.label)[1]).toBe("θ 30° · Аз 330° · Пог 0°");
    expect(s[3].u!.map((v) => Number(v.toFixed(12)) + 0)).toEqual([-1, 0, 0]);
    expect(fanHMax(MAP)).toBe(60);
  });
});

describe("a click on a ray / the map: the angles (the mirror of fan.turn)", () => {
  it("= the core to 1e−9° for every plane of the fixture", () => {
    expect(A.turns.length).toBeGreaterThanOrEqual(6);
    for (const t of A.turns as { plane: number[]; theta: number; angles: number[] }[]) {
      const [ang1, ang2, ang3] = t.plane;
      const got = turnAngles({ ang1, ang2, ang3 }, t.theta);
      const want = { ang1: t.angles[0], ang2: t.angles[1], ang3: t.angles[2] };
      for (const k of ["ang1", "ang2", "ang3"] as const) expect(close(got[k], want[k]), `${JSON.stringify(t)} ${k}: ${got[k]}`).toBe(true);
    }
  });

  it("θ of the a1 of the model in the plane: back from the turn; off the plane — null", () => {
    const p = { ang1: 30, ang2: -20, ang3: 10 };
    for (const th of [0, 15, 45, 100, 170]) expect(thetaInPlane(p, turnAngles(p, th))).toBeCloseTo(th, 9);
    expect(thetaInPlane(p, turnAngles(p, 200))).toBeCloseTo(20, 9); // a direction has no sign
    expect(thetaInPlane({ ang1: 0, ang2: 0, ang3: 0 }, { ang1: 0, ang2: -45, ang3: 0 })).toBeNull();
    expect(samePlane(p, turnAngles(p, 60))).toBe(true);
    expect(samePlane(p, { ang1: 30, ang2: 0, ang3: 0 })).toBe(false);
  });

  it("the model turned: the first structure (with «общие оси» — all); an empty model gets one", () => {
    const m: VModel = readModel(A.fit.model);
    const t = turnModel(m, MAP.plane, 30);
    expect(t.structures[0]).toMatchObject({ ang1: 330, ang2: 0, ang3: 0, a1: m.structures[0].a1 });
    expect(selectedTheta({ model: t }, MAP)).toBeCloseTo(30, 9);
    const two: VModel = { ...m, common_axes: false, structures: [m.structures[0], { ...m.structures[0], ang1: 10 }] };
    expect(turnModel(two, MAP.plane, 90).structures.map((s) => s.ang1)).toEqual([270, 10]);
    const empty = turnModel(EMPTY_MODEL, MAP.plane, 60, { sigma2: 2, range: 30 });
    expect(empty.structures).toHaveLength(1);
    expect(empty.structures[0]).toMatchObject({ type: "spherical", ang1: 300, a1: 30, c: 1 });
    expect(selectedTheta({ model: EMPTY_MODEL }, MAP)).toBeNull();
  });

  it("the views draw the real answer: the rays, the rose with world labels, the map; nothing yet — a message", () => {
    const w = newWork({ model: readModel(A.fit.model), mapCalc: MAP, mapFor: null, data: { ...newWork().data, field: "AU" } });
    const fan = renderToStaticMarkup(createElement(FanView, { work: w, winId: WIN, job: JOB }));
    expect(fan).toContain("Лучи веера");
    expect(fan).toContain("Роза дальностей");
    expect(fan).toContain("a1 · Аз 0° · Пог 0°");
    // the a1 of the model at Аз 30° (ang1 = 30) is east of north: θ (counter-clockwise, towards the west) = −30° ≡ 150°
    expect(fan).toContain('data-selected="150"');
    expect(selectedTheta(w, MAP)).toBeCloseTo(150, 9);
    expect(fan).toContain("Рассчитать веер и карту");
    const map = renderToStaticMarkup(createElement(MapView, { work: w, winId: WIN, job: JOB }));
    expect(map).toContain("data-colorbar");
    expect(map).toContain('data-cells="81"');
    expect(map).not.toContain("показан прежний расчёт"); // no data: no body to compare with
    const data = { ...newWork().data, setId: 1, table: "1", field: "AU" };
    const same = renderToStaticMarkup(createElement(MapView, { work: { ...w, data }, winId: WIN, job: JOB }));
    expect(same).toContain("Данные или параметры изменены: показан прежний расчёт"); // turned within the plane
    const tilted = { ...w, data, model: { ...w.model, structures: w.model.structures.map((s) => ({ ...s, ang2: -30 })) } };
    expect(renderToStaticMarkup(createElement(MapView, { work: tilted, winId: WIN, job: JOB }))).toContain("Оси модели повернуты");
    const none = renderToStaticMarkup(createElement(MapView, { work: newWork(), winId: WIN, job: JOB }));
    expect(none).toContain("Карта не рассчитана");
  });
});

describe("/curve and the formulas of the window", () => {
  it("γ along the axes of the model = the core to 1e−12", () => {
    const got = readCurveAnswer(A.curve);
    const m = FIT.model;
    expect(got.directions.map((d) => d.axis)).toEqual([1, 2, 3]);
    for (const d of got.directions) {
      const h = d.h.map((x) => x as number);
      const mine = gammaAlong(m, h, axisOf(m, d.axis as 1 | 2 | 3));
      mine.forEach((g, i) => expect(close(g, d.gamma[i] as number, 1e-12)).toBe(true));
    }
    expect(readCurveAnswer(A.curve_directions).directions[0]).toMatchObject({ axis: null, az: 120, h: [0, 10, 60, 90] });
  });
});

describe("normal scores: the back-transform", () => {
  it("the answer: the model of Z, the share, the curves of three axes; its model curve = γ fitted by the core", () => {
    const b = readBack(A.back);
    expect(b.model.type).toBe("semivariogram");
    expect(b.share).toBeGreaterThan(0.95);
    expect(b.warning).toBeNull();
    expect(b.raw).toEqual(A.back);
    const s = backSeries(b);
    expect(s.map((x) => x.label)).toEqual(["Ось 1: γ(Z)", "Ось 2: γ(Z)", "Ось 3: γ(Z)"]);
    b.curves.forEach((c, k) => {
      const mine = gammaAlong(b.model, c.h as number[], s[k].u!);
      mine.forEach((g, i) => expect(close(g, c.gamma_fit[i] as number, 1e-9)).toBe(true));
    });
  });

  it("the tails; why it cannot go; the panel shows the share", () => {
    expect(tailsBody(DEFAULT_TAILS)).toEqual({ tails: {}, error: null });
    expect(tailsBody({ ltail: "power", utail: "hyperbolic", ltpar: "2,5", utpar: "1,5" }).tails).toEqual({ ltail: "power", utail: "hyperbolic", ltpar: 2.5, utpar: 1.5 });
    expect(tailsBody({ ...DEFAULT_TAILS, utail: "hyperbolic", utpar: "0,5" }).error).toContain("не меньше 1");
    expect(tailsBody({ ...DEFAULT_TAILS, ltail: "none" }).tails).toEqual({ ltail: null, utail: "linear" });
    const nsCalc = readJobCalc(A.job_nscore_axes);
    const base = newWork({ model: readModel(A.fit_nscore.model), expKey: nsCalc.key, result: nsCalc, data: { ...newWork().data, transform: "nscore" } });
    expect(backReason(base)).toBeNull();
    expect(backReason({ ...base, data: { ...base.data, transform: "none" } })).toContain("нормальными оценками");
    expect(backReason({ ...base, result: readCalc(A.job_fit_calc) })).toContain("Сначала рассчитайте");
    const html = renderToStaticMarkup(createElement(BackPanel, { work: { ...base, back: A.back }, winId: WIN }));
    expect(html).toContain("Доля Σφ² / Var(Z) = 99,96 %");
    expect(html).toContain("Модель в единицах Z");
  });
});

describe("the models in the tree", () => {
  it("stale with its reason; «Перестроить» only for a stale one with its source; the menu — the commands", () => {
    const stale = readModelRow(A.models_stale.models[0]);
    const fresh = readModelRow(A.models_rebuilt.models[0]);
    expect(staleText(stale)).toBe("устарел: изменена таблица «Пробы»");
    expect(staleText(fresh)).toBeNull();
    expect(rebuildReason(stale)).toBeNull();
    expect(rebuildReason(fresh)).toBe("Модель не устарела");
    expect(rebuildReason({ stale: true, source_lost: true })).toContain("Источник удалён");
    const ran: unknown[] = [];
    const items = modelMenu(stale, () => null, (id, args) => void ran.push([id, args]), (f) => void ran.push(["export", f]));
    expect(items.map((i) => i.label)).toEqual(["Открыть", "Показать эллипсоид", "Перестроить", "Экспорт JSON…", "Экспорт GSLIB…", "Свойства", "Удалить…"]);
    items[2].run!();
    items[4].run!();
    items[6].run!();
    expect(ran).toEqual([["variography.rebuild", { model_id: stale.id }], ["export", "gslib"], ["variography.delete", { model_id: stale.id }]]);
    expect(modelMenu(fresh, () => null, () => undefined, () => undefined)[2].reason).toBe("Модель не устарела");
    expect(propertiesText(stale)).toContain("Состояние: устарел: изменена таблица «Пробы»");
    expect(modelsByName([{ ...stale, name: "Б" }, { ...stale, name: "А" }]).map((m) => m.name)).toEqual(["А", "Б"]);
    expect(commands.find((c) => c.id === "variography.rebuild")?.required).toEqual(["model_id"]);
  });

  it("the result of «Перестроить»; the window of the model takes the new points", async () => {
    const r = readRebuild(A.job_rebuild);
    expect(r).toMatchObject({ modelId: A.model_created.id, fresh: false, back: null });
    expect(r.key).toMatch(/^[0-9a-f]{64}$/);
    expect(r.fit).toEqual(A.job_rebuild.fit);
    setWork(WIN, newWork({ modelId: A.model_created.id, expKey: "0".repeat(64), dirty: false }));
    const points = { ...readCalc(A.model_read.points), key: r.key };
    expect(await reloadPoints(A.model_created.id, async () => ({ points }) as never)).toBe(true);
    expect(workOf(WIN)).toMatchObject({ expKey: r.key, dirty: false });
    setWork(WIN, { dirty: true, expKey: "1".repeat(64) });
    expect(await reloadPoints(A.model_created.id, async () => ({ points }) as never)).toBe(false); // an edited window keeps its own
  });
});

describe("the exchange", () => {
  it("the file names of the core; the path chosen, then the commands of the registry", async () => {
    expect(exchangeFileName(A.model_created.name, "gslib")).toBe(A.export_gslib.filename);
    expect(exchangeFileName(A.model_created.name, "json")).toBe(A.export_json.filename);
    expect(exchangeFileName('a<b>:"c"/', "json")).toBe("a_b___c__.json");
    const asked: unknown[] = [];
    const ask = async (o: unknown) => {
      asked.push(o);
      return " C:\\Модели\\AU.par ";
    };
    expect(await askExportPath("AU · Пробы", "gslib", { ask: ask as never })).toBe("C:\\Модели\\AU.par");
    expect(asked[0]).toMatchObject({ initial: "AU · Пробы.par" });
    const ran: unknown[] = [];
    const run = (id: string, args: Record<string, unknown>) => void ran.push([id, args]);
    expect(await exportFlow(run, 3, "AU · Пробы", "json", { pickSave: async (n) => `/tmp/${n}` })).toBe(true);
    expect(await exportFlow(run, 3, "AU · Пробы", "json", { pickSave: async () => null })).toBe(false);
    expect(await importFlow(run, { pickOpen: async () => "/tmp/модель.par" })).toBe(true);
    expect(ran).toEqual([["variography.export", { model_id: 3, format: "json", path: "/tmp/AU · Пробы.json" }], ["variography.import", { path: "/tmp/модель.par" }]]);
  });

  it("an imported model (JSON, GSLIB, a file) is the model that was exported", () => {
    const m = readModel(A.fit.model);
    expect(readModel(A.import_json.model)).toEqual(m);
    expect(readModel(A.import_gslib.model)).toEqual(m);
    expect(readModel(A.import_path.model)).toEqual(m);
    expect(A.import_bad.body).toMatchObject({ code: "bad_file", line: 3 });
  });
});
