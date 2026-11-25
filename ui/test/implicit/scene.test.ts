// The implicit models in the main 3D (Э19, 19.23): the points of the groups (the REAL answer `preview` of the core: the columns, the kinds) as a layer of the scene,
// float32 from an integer origin, a gap never a point at 0; the ellipsoid of the anisotropy (`viz/ellipsoid` as it is) of the numbers the core stores; the layers go with
// the choice, the model, the project; a lost context. A fake scene.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { readPreview } from "../../src/modules/implicit/api";
import { ImplicitScene } from "../../src/modules/implicit/scene";
import { implicitStore, loadDetails, loadRows } from "../../src/modules/implicit/state";
import { colorsOf, forgetView, implicitViewStore, KIND_COLORS, pointsOf, setEllipsoid, setPoints } from "../../src/modules/implicit/viewState";
import { fakeScene, type Log } from "../estimation/previewKit";
import { installReal, MODEL_ID, PARAMS, R, realApp, teardown, type RealCore } from "./real";

let core: RealCore;
let log: Log;
let sc: ImplicitScene;
beforeEach(async () => {
  core = installReal();
  realApp();
  await loadRows();
  const f = fakeScene();
  log = f.log;
  sc = new ImplicitScene(f.scene);
});
afterEach(() => {
  sc.dispose();
  teardown();
});

const ANSWER = readPreview(R.preview.body);
const VEIN = PARAMS.cases.vein_expand.canonical as { anisotropy: Record<string, unknown> };
const keys = () => [...log.layers.keys()].sort();
const settle = () => new Promise((r) => setTimeout(r, 10));

describe("the points of the real answer", () => {
  it("the columns of the core become the places and the kinds; nothing is lost or made up", () => {
    const e = pointsOf(ANSWER, 7);
    expect(e).toMatchObject({ status: "ready", version: 7, total: 1241, taken: 12, drawn: 12, truncated: true });
    expect(Array.from(e.xyz.slice(0, 3))).toEqual([ANSWER.points.x[0], ANSWER.points.y[0], ANSWER.points.z[0]]); // float64 as the core wrote it
    expect(Array.from(e.kinds)).toEqual(ANSWER.points.kind);
  });
  it("a gap in a coordinate is a point not drawn — never a point at 0", () => {
    const gap = { ...ANSWER, points: { ...ANSWER.points, y: ANSWER.points.y.map((v, i) => (i === 3 ? null : v)) } };
    const e = pointsOf(gap, 1);
    expect(e).toMatchObject({ taken: 12, drawn: 11 });
    expect(Array.from(e.xyz).some((v, i) => i % 3 === 1 && v === 0)).toBe(false);
  });
  it("the colours by kind: the contact is Copper Orange, the data inside Cobalt, outside Slate; an unknown kind is outside", () => {
    const c = colorsOf(new Uint8Array([0, 1, 2, 3, 4, 77]));
    const hex = (i: number) => "#" + [0, 1, 2].map((k) => Math.round(c[3 * i + k] * 255).toString(16).padStart(2, "0")).join("");
    expect([0, 1, 2, 3, 4].map(hex)).toEqual([KIND_COLORS.contact, KIND_COLORS.inside, KIND_COLORS.outside, KIND_COLORS.manual, KIND_COLORS.value]);
    expect(hex(5)).toBe(KIND_COLORS.outside);
  });
});

describe("the layers follow the choice", () => {
  it("a group switched on gets a layer of its points from an integer origin; off — the layer goes", async () => {
    expect(keys()).toEqual([]);
    await setPoints(MODEL_ID, 1, true);
    expect(keys()).toEqual(["implicit:points:6:1"]);
    const s = sc.stats().points[0];
    expect(s.count).toBe(12);
    expect(Number.isInteger(s.origin.x) && Number.isInteger(s.origin.y) && Number.isInteger(s.origin.z)).toBe(true);
    const lo = Math.min(...(ANSWER.points.x as number[]));
    expect(s.bbox![0]).toBeCloseTo(lo, 9);
    await setPoints(MODEL_ID, 1, false);
    expect(keys()).toEqual([]);
  });
  it("a model that is gone takes its layers; so does another project", async () => {
    await setPoints(MODEL_ID, 1, true);
    await setPoints(MODEL_ID, 3, true);
    expect(keys()).toHaveLength(2);
    core.list = [];
    await loadRows();
    expect(keys()).toEqual([]);
    core.list = [];
    await setPoints(MODEL_ID, 1, true);
    forgetView();
    expect(keys()).toEqual([]);
  });
  it("the model changed (its version moved): the points are read again and the layer is the new one", async () => {
    await setPoints(MODEL_ID, 1, true);
    const before = log.layers.get("implicit:points:6:1");
    core.list = [{ ...core.list[0], version: 8 }];
    await loadRows();
    await settle();
    await settle();
    expect(log.layers.get("implicit:points:6:1")).toBeDefined();
    expect(log.layers.get("implicit:points:6:1")).not.toBe(before);
    expect(implicitViewStore.get().entries["6:1"].version).toBe(8);
  });
  it("a lost context: the layers come again from the stores", async () => {
    await setPoints(MODEL_ID, 1, true);
    log.layers.clear();
    log.restore();
    expect(keys()).toEqual(["implicit:points:6:1"]);
  });
});

describe("the ellipsoid of the anisotropy", () => {
  const withAnisotropy = async () => {
    core.details.set(MODEL_ID, { ...R.model.body, meta: { ...(R.model.body.meta as object), anisotropy: VEIN.anisotropy } });
    await loadDetails(MODEL_ID);
  };
  it("the numbers of the core make the matrix of the layer: a1 a quarter of the model, a2 and a3 by the ratios, the angles as stored", async () => {
    await withAnisotropy();
    setEllipsoid(MODEL_ID, true);
    expect(keys()).toEqual(["implicit:ellipsoid:6"]);
    const e = sc.stats().ellipsoids[0];
    expect(e.ranges).toEqual([105, 52.5, 26.25]); // the extent 420 × 420 × 210 → 105
    expect(e.angles).toEqual([45, -30, 0]);
    expect(e.centre).toEqual([210, 210, 225]);
    setEllipsoid(MODEL_ID, false);
    expect(keys()).toEqual([]);
  });
  it("a model without anisotropy has none to draw (the command refuses first; the layer is not made if it is asked anyway)", async () => {
    await loadDetails(MODEL_ID);
    setEllipsoid(MODEL_ID, true);
    expect(keys()).toEqual([]);
    expect(implicitStore.get().details[MODEL_ID].meta.anisotropy).toBeNull();
  });
});
