import { describe, expect, it } from "vitest";
import { addPoint, DRAW_OFF, drawHint, startDraw } from "../src/viz/section/draw";
import { buildOverview, MINI_PLAN_POINTS, overviewBounds, planFrame } from "../src/viz/section/miniPlan";

describe("drawing a section by two clicks", () => {
  it("first click marks the start, the second gives an unlimited line with the window kept", () => {
    let s = startDraw();
    expect(drawHint(s)).toMatch(/начало профиля/);
    let r = addPoint(s, { x: 100, y: 200 }, null);
    expect(r.line).toBeNull();
    s = r.state;
    expect(s.stage).toBe("second");
    expect(drawHint(s)).toMatch(/конец профиля/);
    // the same point again: still waiting
    r = addPoint(s, { x: 100, y: 200 }, null);
    expect(r.line).toBeNull();
    r = addPoint(s, { x: 100, y: 300 }, { x0: 0, y0: 0, az: 10, length: 40, towards: 7, away: 9 });
    expect(r.line).toEqual({ x0: 100, y0: 200, az: 0, length: null, towards: 7, away: 9 });
    expect(r.state).toEqual(DRAW_OFF);
    expect(drawHint(r.state)).toBeNull();
  });

  it("eastward line: azimuth 90; clicks while off change nothing", () => {
    const r = addPoint(addPoint(startDraw(), { x: 0, y: 0 }, null).state, { x: 10, y: 0 }, null);
    expect(r.line?.az).toBe(90);
    expect(r.line?.towards).toBe(25);
    expect(addPoint(DRAW_OFF, { x: 1, y: 1 }, null)).toEqual({ state: DRAW_OFF, line: null });
  });
});

describe("mini-plan", () => {
  const part = (n: number, per: number) => {
    const collars = new Float64Array(n * 3);
    const offsets = new Uint32Array(n + 1);
    const xyz = new Float32Array(n * per * 3);
    for (let h = 0; h < n; h++) {
      collars.set([500_000 + h, 7_000_000 + h, 100], h * 3);
      offsets[h + 1] = (h + 1) * per;
      for (let p = 0; p < per; p++) xyz.set([h, h + p * 0.1, -p], (h * per + p) * 3);
    }
    return { origin: [500_000, 7_000_000, 100] as const, collars, offsets, xyz };
  };

  it("keeps everything under the limit and adds the origin of the part in float64", () => {
    const o = buildOverview([part(3, 4)]);
    expect(o.simplified).toBe(false);
    expect(o.collars).toHaveLength(3);
    expect(o.holes[1][0]).toEqual([500_001, 7_000_001]);
    expect(o.holes[1]).toHaveLength(4);
  });

  it("over 20 000 points: thinned evenly to the limit, the ends kept, marked simplified", () => {
    const o = buildOverview([part(1000, 40)]); // 1000 collars + 40 000 points
    expect(o.simplified).toBe(true);
    const shown = o.collars.length + o.holes.reduce((s, h) => s + h.length, 0);
    expect(shown).toBeLessThanOrEqual(MINI_PLAN_POINTS + 1000); // + the kept ends of the traces
    expect(o.holes[0][o.holes[0].length - 1][1]).toBeCloseTo(7_000_000 + 3.9, 4);
  });

  it("frame: the viewer's triangle on the «towards» side, clicks map back to project coordinates", () => {
    const o = buildOverview([part(3, 4)]);
    expect(overviewBounds(o, [])).not.toBeNull();
    const line = { x0: 500_001, y0: 7_000_000, az: 0, length: null, towards: 10, away: 5 };
    const f = planFrame(o, line);
    expect(f.eye!.x).toBeGreaterThan(line.x0); // north line → viewer to the east
    expect(f.eye!.rotate).toBe(-90);
    const p = f.toWorld(f.sx(500_002.5), f.sy(7_000_001.25));
    expect(p.x).toBeCloseTo(500_002.5, 6);
    expect(p.y).toBeCloseTo(7_000_001.25, 6);
    expect(f.corners).toHaveLength(4);
  });
});

describe("section of the project view and the layers of a set", async () => {
  const { normalizeLine, sectionOf, describeLine } = await import("../src/modules/section/state");
  const { setStyleOf, setVisibilityOf } = await import("../src/modules/scene/loaders");

  it("reads the line as the core keeps it: azimuth in [0; 360), window defaults, no clipping without a line", () => {
    expect(normalizeLine({ x0: 1, y0: 2, az: -90, length: 0, towards: -1 })).toEqual({ x0: 1, y0: 2, az: 270, length: null, towards: 25, away: 25 });
    expect(normalizeLine({ x0: 1, y0: "2", az: 0 })).toBeNull();
    expect(sectionOf({ section: { line: null, clip: true } })).toEqual({ line: null, clip: false });
    const s = sectionOf({ section: { line: { x0: 5, y0: 6, az: 45, length: 100, towards: 10, away: 20 }, clip: true } });
    expect(s.clip).toBe(true);
    expect(describeLine(s.line!, s.clip)).toMatch(/азимут 45°, длина 100 м, окно от 10 до 20 м, отсечение вкл/);
  });

  it("hidden keys and styles of a set", () => {
    expect(setVisibilityOf(["set:3:paths", "set:4"], 3)).toEqual({ set: true, collars: true, paths: false });
    expect(setVisibilityOf(["set:3:paths", "set:4"], 4).set).toBe(false);
    const st = setStyleOf({ collar_styles: { "3": { shape: "star", color: "#ff0000" } }, trace_styles: { "3": { width: 3 } } }, 3);
    expect(st.collar.shape).toBe("star");
    expect(st.trace.width).toBe(3);
    expect(setStyleOf({}, 9).collar.color).toBe("audit");
  });
});
