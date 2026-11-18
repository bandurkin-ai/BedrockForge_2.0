/**
 * The exact pick of a wireframe (ТЗ 7.21, ADR 0013 §5–§6): the ray of a click in project coordinates, the
 * planes of the section window, the request and the answer of `POST /wireframes/pick`, only the latest click
 * answered, and what «Свойства» say about the picked triangle.
 */
import { describe, expect, it } from "vitest";
import { VizError } from "../src/viz/error";
import { absoluteRay, attitudeText, coordText, degreesText, ExactPicker, pickRequest, readExactPick, windowPlanes, type ExactPick, type PickRequest } from "../src/viz/mesh/exact";
import { inCorridor, type SectionLine } from "../src/viz/section/math";
import { pickedFacts, pickedNote, pickedTriangle } from "../src/modules/wireframes/picked";

const HIT = {
  hit: {
    wireframe_id: 7, name: "Разлом", triangle: 41, distance: 471.85, point: [512_321.978_901, 6_234_599.023_456, 440.495_678],
    vertices: [[512_315.678_901, 6_234_597.123_456, 437.345_678], [512_325.678_901, 6_234_597.123_456, 442.345_678], [512_325.678_901, 6_234_607.123_456, 442.345_678]],
    indices: [3, 4, 23], normal: [-0.447_213_595_499_957_9, 0, 0.894_427_190_999_915_9], area: 55.901_699_437_494_74, dip: 26.565_051_177_077_99, dip_direction: 270,
  },
  tested: 1,
};

describe("the ray of a click in project coordinates", () => {
  it("rendered space → absolute: the scene origin back, the exaggeration of Z out", () => {
    // the layer origin (500 050, 7 000 050, 300) stands 1000 m west of the scene origin; Z is drawn × 2
    const ray = { origin: [-980, 10, 400] as [number, number, number], dir: [0.6, 0, -0.8] as [number, number, number] };
    const abs = absoluteRay(ray, [-1000, 0, 0], { x: 500_050, y: 7_000_050, z: 300 }, 2);
    expect(abs.origin).toEqual([500_070, 7_000_060, 500]);
    expect(abs.dir).toEqual([0.6, 0, -0.4]);
    // a point of the ray in rendered space is the same point of the absolute ray
    const t = 250;
    const rendered = [ray.origin[0] + t * ray.dir[0], ray.origin[1] + t * ray.dir[1], ray.origin[2] + t * ray.dir[2]];
    expect(abs.origin[0] + t * abs.dir[0]).toBeCloseTo(rendered[0] + 1000 + 500_050, 9);
    expect(abs.origin[2] + t * abs.dir[2]).toBeCloseTo(rendered[2] / 2 + 300, 9);
    // no exaggeration given: 1
    expect(absoluteRay(ray, [0, 0, 0], { x: 0, y: 0, z: 0 }, 0).dir).toEqual([0.6, 0, -0.8]);
  });

  it("the planes of the section window are absolute and keep exactly what the window keeps", () => {
    const line: SectionLine = { x0: 512_345.5, y0: 6_234_567.25, az: 37, length: 400, towards: 25, away: 60 };
    expect(windowPlanes(line, false)).toBeNull();
    expect(windowPlanes(null, true)).toBeNull();
    const planes = windowPlanes(line, true)!;
    expect(planes).toHaveLength(4);
    expect(windowPlanes({ ...line, length: null }, true)).toHaveLength(2);
    const inside = (x: number, y: number) => planes.every((p) => p[0] * x + p[1] * y + p[3] >= 0);
    const s = Math.sin((37 * Math.PI) / 180);
    const c = Math.cos((37 * Math.PI) / 180);
    for (const [u, d] of [[10, 0], [390, 24.9], [200, -59.9], [-1, 0], [401, 0], [200, 25.1], [200, -60.1]]) {
      const x = line.x0 + s * u + c * d;
      const y = line.y0 + c * u - s * d;
      expect(inside(x, y)).toBe(inCorridor(x, y, line));
    }
    expect(planes.every((p) => p[2] === 0)).toBe(true); // vertical: the exaggeration changes nothing
  });

  it("the request names every shown wireframe once", () => {
    const ray = { origin: [1, 2, 3] as [number, number, number], dir: [0, 0, -1] as [number, number, number] };
    const body = pickRequest(ray, [9, 3, 9, 5], null, false);
    expect(body).toEqual({ origin: [1, 2, 3], direction: [0, 0, -1], wireframe_ids: [3, 5, 9], clip: null });
    expect(pickRequest(ray, [], { x0: 0, y0: 0, az: 90, length: null, towards: 5, away: 5 }, true).clip).toHaveLength(2);
  });
});

describe("the answer of the core", () => {
  it("is the triangle, its point and how it lies; a level triangle has no dip direction", () => {
    const p = readExactPick(HIT)!;
    expect(p).toMatchObject({ wireframeId: 7, name: "Разлом", triangle: 41, distance: 471.85, dip: 26.565_051_177_077_99, dipDirection: 270 });
    expect(p.point).toEqual(HIT.hit.point); // float64 as sent
    expect(p.vertices[2]).toEqual(HIT.hit.vertices[2]);
    expect(readExactPick({ hit: null, tested: 3 })).toBeNull();
    const level = readExactPick({ hit: { ...HIT.hit, dip: 0, dip_direction: null } })!;
    expect(level.dip).toBe(0);
    expect(level.dipDirection).toBeNull(); // not 0: a missing value stays missing
    const flat = readExactPick({ hit: { ...HIT.hit, normal: null, area: 0, dip: null, dip_direction: null } })!;
    expect([flat.normal, flat.dip, flat.dipDirection]).toEqual([null, null, null]);
  });

  it("a damaged answer is an error in Russian with a hint, never a wrong point", () => {
    for (const bad of [null, "x", { hit: 5 }, { hit: { ...HIT.hit, point: [1, 2] } }, { hit: { ...HIT.hit, point: [1, 2, NaN] } }, { hit: { ...HIT.hit, triangle: -1 } }, { hit: { ...HIT.hit, vertices: [] } }, { hit: { ...HIT.hit, dip: "45" } }]) {
      let err: unknown;
      try {
        readExactPick(bad);
      } catch (e) {
        err = e;
      }
      expect(err).toBeInstanceOf(VizError);
      expect((err as VizError).code).toBe("pick_answer");
      expect((err as VizError).hint).not.toBe("");
    }
  });

  it("only the latest click is answered", async () => {
    const waiting: { body: PickRequest; resolve: (v: unknown) => void; reject: (e: unknown) => void }[] = [];
    const picker = new ExactPicker((body) => new Promise((resolve, reject) => waiting.push({ body, resolve, reject })));
    const body = (x: number): PickRequest => ({ origin: [x, 0, 0], direction: [0, 0, -1], wireframe_ids: [7], clip: null });
    const first = picker.pick(body(1));
    const second = picker.pick(body(2));
    waiting[1].resolve(HIT);
    waiting[0].resolve({ hit: { ...HIT.hit, wireframe_id: 99 } }); // the older answer comes last
    expect(await first).toBeUndefined();
    expect((await second)?.wireframeId).toBe(7);
    // a late failure of an old click is nobody's; a failure of the latest one is thrown
    const third = picker.pick(body(3));
    const fourth = picker.pick(body(4));
    waiting[2].reject(new Error("old"));
    waiting[3].reject(new Error("ядро недоступно"));
    expect(await third).toBeUndefined();
    await expect(fourth).rejects.toThrow("ядро недоступно");
    // cancelled (the selection was dropped): the answer is not delivered
    const fifth = picker.pick(body(5));
    picker.cancel();
    waiting[4].resolve(HIT);
    expect(await fifth).toBeUndefined();
    // nothing under the ray
    const sixth = picker.pick(body(6));
    waiting[5].resolve({ hit: null });
    expect(await sixth).toBeNull();
  });
});

describe("«Треугольник по клику» of «Свойства»", () => {
  const exact = readExactPick(HIT) as ExactPick;

  it("numbers are written as the user reads them", () => {
    expect(coordText(512_321.978_901).replace(/\s/g, " ")).toBe("512 321,979");
    expect(coordText(-0.0004)).toBe("−0,000");
    expect(degreesText(26.565_051)).toBe("26,6°");
    expect(attitudeText(exact)).toBe("падение 26,6° по азимуту 270,0°");
    expect(attitudeText({ dip: 0, dipDirection: null })).toBe("горизонтальный");
    expect(attitudeText({ dip: null, dipDirection: null })).toBe("площадь нулевая");
  });

  it("the place of the level in view first, then the exact point with the dip and the dip direction", () => {
    const picked = { kind: "wireframe" as const, id: 7, xyz: [512_322.1, 6_234_599.0, 440.6] as [number, number, number] };
    expect(pickedTriangle(picked, 8)).toBeNull();
    expect(pickedTriangle({ kind: "raster", id: 7, xyz: [0, 0, 0] }, 7)).toBeNull();
    const coarse = pickedTriangle(picked, 7)!;
    expect(pickedFacts(coarse).map((f) => f.label)).toEqual(["X", "Y", "Z"]);
    expect(pickedNote(coarse)).toContain("уточняется");
    expect(pickedNote({ exact: null })).toContain("нет");
    const full = pickedTriangle({ ...picked, xyz: exact.point, exact }, 7)!;
    const facts = Object.fromEntries(pickedFacts(full).map((f) => [f.label, f.value.replace(/\s/g, " ")]));
    expect(facts).toEqual({ X: "512 321,979", Y: "6 234 599,023", Z: "440,496", "Угол падения": "26,6°", "Азимут падения": "270,0°", "Площадь треугольника": "55,9 м²", "Треугольник №": "42" });
    expect(pickedNote(full)).toBeNull();
    const level = Object.fromEntries(pickedFacts({ xyz: exact.point, exact: { ...exact, dip: 0, dipDirection: null } }).map((f) => [f.label, f.value]));
    expect(level["Угол падения"]).toBe("0,0°");
    expect(level["Азимут падения"]).toContain("горизонтален"); // never «0°»
  });
});
