/**
 * Planes, rays and keyboard input of vertices for the editor (ADR 0015 §3; BF `edit/coords.ts`).
 * float64 project coordinates; the plane keeps a point and a normal (not a constant), so a section plane far
 * from the origin (UTM) loses nothing.
 */
import type { Ray, SectionLine } from "../viz/section/math";
import { sinCosDeg } from "../viz/section/math";
import type { Vec3 } from "./model";

export type { Ray };

/** `normal` is a unit vector. */
export interface Plane {
  point: Vec3;
  normal: Vec3;
}

export function horizontalPlane(z: number): Plane {
  return { point: [0, 0, z], normal: [0, 0, 1] };
}

/**
 * The vertical plane in the MIDDLE of the section window: the section line shifted by
 * (towards − away) / 2 along its right-hand normal n = (cos az, −sin az). The normal looks at the viewer.
 */
export function sectionPlane(section: SectionLine): Plane {
  const [s, c] = sinCosDeg(section.az);
  const nx = c;
  const ny = s === 0 ? 0 : -s;
  const shift = (section.towards - section.away) / 2;
  return { point: [section.x0 + nx * shift, section.y0 + ny * shift, 0], normal: [nx, ny, 0] };
}

/** Signed distance from the plane, positive on the side of the normal. */
export function planeDistance(p: Vec3, plane: Plane): number {
  const n = plane.normal;
  const o = plane.point;
  return (p[0] - o[0]) * n[0] + (p[1] - o[1]) * n[1] + (p[2] - o[2]) * n[2];
}

/** The foot of the point on the plane. A missing Z stays missing on a vertical plane. */
export function projectToPlane(p: Vec3, plane: Plane): Vec3 {
  const n = plane.normal;
  let x = p[0];
  let y = p[1];
  let z = p[2];
  const vertical = n[2] === 0;
  // The second pass removes the rounding left by the first one: with UTM-size numbers
  // the result lies in the plane to about one unit in the last place.
  for (let pass = 0; pass < 2; pass++) {
    const d = vertical
      ? (x - plane.point[0]) * n[0] + (y - plane.point[1]) * n[1]
      : (x - plane.point[0]) * n[0] + (y - plane.point[1]) * n[1] + (z - plane.point[2]) * n[2];
    if (d === 0 || !Number.isFinite(d)) break;
    x -= d * n[0];
    y -= d * n[1];
    if (!vertical) z -= d * n[2];
  }
  return [x, y, z];
}

/** Where the ray meets the plane; null when it is parallel or the plane is behind the origin. */
export function rayPlane(ray: Ray, plane: Plane): Vec3 | null {
  const n = plane.normal;
  const d = ray.dir;
  const o = ray.origin;
  const len = Math.hypot(d[0], d[1], d[2]);
  if (!(len > 0)) return null;
  const denom = d[0] * n[0] + d[1] * n[1] + d[2] * n[2];
  if (Math.abs(denom) <= 1e-12 * len) return null;
  const dist = (plane.point[0] - o[0]) * n[0] + (plane.point[1] - o[1]) * n[1] + (plane.point[2] - o[2]) * n[2];
  const t = dist / denom;
  // A tiny negative t is rounding: the origin is in the plane.
  if (!Number.isFinite(t) || t * len < -1e-6 * (1 + Math.abs(dist))) return null;
  return projectToPlane([o[0] + d[0] * t, o[1] + d[1] * t, o[2] + d[2] * t], plane);
}

const NUMBER = /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/;

function splitNumbers(text: string): number[] | string {
  let tokens: string[];
  const hasComma = text.includes(",");
  if (text.includes(";")) {
    // Semicolons separate, so a comma can only be the decimal mark.
    tokens = text.split(/[;\s]+/).map((t) => t.replace(",", "."));
  } else if (hasComma && !text.includes(".")) {
    const words = text.split(/\s+/);
    const decimal = words.length >= 2 && words.every((w) => /^[^,]*(\d,\d[^,]*)?$/.test(w));
    tokens = decimal ? words.map((w) => w.replace(",", ".")) : text.split(/[,\s]+/);
  } else {
    tokens = text.split(/[,\s]+/);
  }
  tokens = tokens.filter((t) => t !== "");
  const out: number[] = [];
  for (const t of tokens) {
    if (!NUMBER.test(t)) return t;
    const v = Number(t);
    if (!Number.isFinite(v)) return t;
    out.push(v);
  }
  return out;
}

export type CoordResult = { point: Vec3 } | { error: string };

/**
 * Keyboard input of a vertex:
 *  - absolute "X Y Z" or "X Y" (keeps the Z of the previous point, or `planZ` — the Z of the panel — for the
 *    first one); separators are spaces, semicolons, or commas when the decimal mark is a point; a decimal comma
 *    works with spaces and semicolons;
 *  - relative polar "@azimuth distance [dip]": azimuth in degrees clockwise from north, distance along
 *    the slope, dip in degrees from the horizontal, positive up;
 *  - relative cartesian "@@dx dy [dz]".
 */
export function parseCoordInput(text: string, previous: Vec3 | null, planZ?: number): CoordResult {
  let s = text.trim();
  if (s === "") return { error: "Введите координаты" };
  let mode: "abs" | "polar" | "delta" = "abs";
  if (s.startsWith("@@")) {
    mode = "delta";
    s = s.slice(2).trim();
  } else if (s.startsWith("@")) {
    mode = "polar";
    s = s.slice(1).trim();
  }
  const nums = splitNumbers(s);
  if (typeof nums === "string") return { error: `Не число: «${nums}»` };

  if (mode === "abs") {
    if (nums.length === 3) return { point: [nums[0], nums[1], nums[2]] };
    if (nums.length === 2) {
      const z = previous ? previous[2] : planZ;
      if (z === undefined) return { error: "Нужны три координаты X Y Z: предыдущей точки нет, взять Z неоткуда" };
      return { point: [nums[0], nums[1], z] };
    }
    return { error: "Ожидаются координаты X Y Z или X Y" };
  }

  if (!previous) return { error: "Относительный ввод возможен только от предыдущей точки" };
  if (mode === "delta") {
    if (nums.length < 2 || nums.length > 3) return { error: "Ожидаются приращения: @@dX dY [dZ]" };
    return { point: [previous[0] + nums[0], previous[1] + nums[1], previous[2] + (nums[2] ?? 0)] };
  }
  if (nums.length < 2 || nums.length > 3) return { error: "Ожидаются азимут, расстояние и угол наклона: @азимут расстояние [угол]" };
  const [az, dist] = nums;
  const dip = nums[2] ?? 0;
  if (dist < 0) return { error: "Расстояние не может быть отрицательным" };
  if (dip < -90 || dip > 90) return { error: "Угол наклона должен быть от −90 до 90°" };
  // Exact on the multiples of 90°: "@90 100" from (0, 0, 0) is (100, 0, 0), not (100, 6e−15, 0).
  const [sa, ca] = sinCosDeg(az);
  const [sb, cb] = sinCosDeg(dip);
  const horizontal = dist * cb;
  return {
    point: [previous[0] + horizontal * sa, previous[1] + horizontal * ca, previous[2] + dist * sb],
  };
}

/** "X Y Z" with `digits` decimals; a missing value is shown as "—". */
export function formatCoord(p: Vec3, digits = 2): string {
  return p.map((v) => (Number.isFinite(v) ? v.toFixed(digits) : "—")).join(" ");
}
