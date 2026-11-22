// A stored model in the window «Вариография» (16.29, the review of the interface) on the REAL answer of the core
// `GET models/{id}` (`core/tests/data/variography/window_answers.json` → `model_read`): the data of `meta.source`
// (domain, weights), `meta.transform`, the values of `meta.calc.params`, `meta.fit`, `meta.back`; its points shown
// until the body is edited (a preview of a new body does not hide them); «Рассчитать» → «Сохранить» keeps the
// source; the replacement does not erase the fit without an edit; an answer of `/fit` or `/back` for a model changed
// during the request is dropped; the format of the import is sent only when known.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadModel, readCalc, readFit } from "../src/modules/variography/api";
import { backFits } from "../src/modules/variography/BackPanel";
import { bindVariography, modelWindow } from "../src/modules/variography/bind";
import { axesOf, pickShown } from "../src/modules/variography/calc";
import { modelBody } from "../src/modules/variography/model";
import { fitWork, saveBody } from "../src/modules/variography/save";
import { experimentalBody, newWork, setModel, setWork, storedData, storedWork, variographyStore, workOf } from "../src/modules/variography/state";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { windowStore } from "../src/shell/windows";

const A = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/variography/window_answers.json"), "utf-8"));
const READ = A.model_read;
const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const sent: { method: string; path: string; body: Record<string, unknown> }[] = [];
let app: App;

beforeEach(() => {
  sent.length = 0;
  windowStore.set({ windows: [] });
  variographyStore.set({ works: {}, models: [], modelsLoaded: false });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url).pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      sent.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : {} });
      const V = "/geostat/variography";
      if (path === `${V}/models/3` && method === "GET") return json(READ);
      if (path === `${V}/models/import`) return json({ model: READ.meta });
      if (path === "/jobs") return json({ jobs: [] });
      return json({ code: "not_found", message: "Нет", hint: "" }, 404);
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  app = createApp({ persist: false });
  bindVariography(app);
  app.store.set({ projectName: "Проект", projectReadOnly: false, setName: "Набор" });
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
});

describe("the window of a stored model", () => {
  it("the data, the values, the fit, the back-transform of `meta`; the body restored = the body of its calculation", async () => {
    const { win } = await modelWindow(3);
    const w = workOf(win)!;
    expect(w.data).toEqual({ setId: 1, table: "1", field: "AU", domain: null, weights: { kind: "none" }, transform: "none" });
    const p = READ.meta.calc.params;
    for (const k of ["lag", "nlag", "lag_tol", "az_tol", "min_pairs", "dh_lag", "seed"]) expect(w.values[k]).toBe(String(p[k]).replace(".", ","));
    expect(w.fitInfo).toEqual({ weights: "n_h", residual: READ.meta.fit.residual, n: READ.meta.fit.n, fixed: READ.meta.fit.fixed });
    expect(w.fix).toEqual(READ.meta.fit.fixed);
    expect(w.back).toBeUndefined(); // `back: null` stored — none
    expect(w.modelVersion).toBe(READ.version);
    expect(w.dirty).toBe(false);
    // the body the window builds is the body of the stored calculation (the source and the parameters kept)
    const body = experimentalBody(w.data, w.values, { axes: axesOf(w.model) }).body!;
    for (const k of ["set_id", "table", "field", "domain", "transform", "lag", "nlag", "lag_tol", "az_tol", "dip_tol", "band_h", "band_v", "min_pairs", "downhole", "dh_lag", "dh_nlag", "dh_lag_tol", "seed"]) expect(body[k]).toEqual(p[k]);
    expect(body.weights).toBe(false); // stored without weights: «нет», never the automatic choice
    expect(body.axes).toEqual(p.axes);
    expect(w.resultFor).toBe(JSON.stringify(body));
    // its points shown, a preview of that body does not hide them; an edit of the body — the preview of the new one
    const preview = readCalc(A.preview);
    expect(pickShown(w.resultFor!, w.result, w.resultFor!, preview, w.resultFor!)).toMatchObject({ kind: "job", calc: w.result });
    const edited = JSON.stringify({ ...body, nlag: 7 });
    expect(pickShown(edited, w.result, w.resultFor!, preview, edited)).toMatchObject({ kind: "preview" });
    expect(pickShown(edited, w.result, w.resultFor!, null, null)).toMatchObject({ kind: "stale" });
  });

  it("the domain of another table, the weights of a field, normal scores", () => {
    const meta = { ...READ.meta, transform: "nscore" };
    const source = { set_id: 1, table: "1", field: "AU", domain: { table: "2", field: "ZONE", values: ["1", "3"] }, weights: "DECL_W" };
    expect(storedData({ meta, source })).toEqual({ setId: 1, table: "1", field: "AU", domain: { table: "2", field: "ZONE", values: ["1", "3"] }, weights: { kind: "field", field: "DECL_W" }, transform: "nscore" });
    expect(storedData({ meta, source: { ...source, domain: { table: "1", field: "ZONE", values: [1] } } }).domain).toEqual({ table: "", field: "ZONE", values: ["1"] });
  });

  it("the replacement without an edit keeps the stored fit (sent as read); an edit resets fit and back", async () => {
    const m = await loadModel(3);
    const w = newWork(storedWork(m));
    expect(saveBody(w, "replace").fit).toEqual(READ.meta.fit);
    expect("back" in saveBody(w, "replace")).toBe(false); // not sent: the stored one kept
    setWork("vw", w);
    setModel("vw", { ...w.model, c0: 0.5 });
    expect(saveBody(workOf("vw")!, "replace")).toMatchObject({ fit: null, back: null });
  });
});

describe("answers for a model changed during the request", () => {
  const FIT = readFit(A.fit);

  it("«Подобрать»: the model or the calculation edited meanwhile — the answer dropped", async () => {
    const m = await loadModel(3);
    setWork("vf", newWork(storedWork(m)));
    await expect(
      fitWork("vf", {}, async () => {
        setModel("vf", { ...workOf("vf")!.model, c0: 0.2 }); // edited while asked
        return FIT;
      }),
    ).rejects.toMatchObject({ code: "stale_answer" });
    expect(workOf("vf")!.model.c0).toBe(0.2);
    await expect(
      fitWork("vf", {}, async () => {
        setWork("vf", { expKey: "other" });
        return FIT;
      }),
    ).rejects.toMatchObject({ code: "stale_answer" });
    setWork("vf", { expKey: m.points!.key });
    expect((await fitWork("vf", {}, async () => FIT)).model).toEqual(FIT.model);
    expect(workOf("vf")!.model).toEqual(FIT.model);
  });

  it("«Преобразовать»: the answer applies to the model and the key it was asked for only", async () => {
    const m = await loadModel(3);
    const w = newWork(storedWork(m));
    const asked = JSON.stringify(modelBody(w.model));
    expect(backFits(w, w.expKey!, asked)).toBe(true);
    expect(backFits({ ...w, expKey: "other" }, w.expKey!, asked)).toBe(false);
    expect(backFits({ ...w, model: { ...w.model, c0: 9 } }, w.expKey!, asked)).toBe(false);
    expect(backFits(null, w.expKey!, asked)).toBe(false); // the window closed
  });
});

describe("variography.import", () => {
  it("`format` only when given or the extension is .json / .par; else the core tells it by the content", async () => {
    for (const [path, format] of [["C:\\м\\a.json", "json"], ["C:\\м\\a.PAR", "gslib"], ["C:\\м\\a.txt", undefined], ["C:\\м\\vario", undefined]] as const) {
      await app.registry.execute("variography.import", { path }, "claude");
      expect(sent.filter((r) => r.path.endsWith("/import")).at(-1)!.body).toEqual(format ? { format, path } : { path });
    }
    await app.registry.execute("variography.import", { path: "C:\\м\\a.txt", format: "gslib" }, "claude");
    expect(sent.at(-1)!.body).toEqual({ format: "gslib", path: "C:\\м\\a.txt" });
  });
});
