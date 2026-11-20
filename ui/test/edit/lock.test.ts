// Э10 10.21 (ADR 0016 §7): «текущий разрез / все» — on a section of a series the lines off it are seen but not
// picked, selected or edited; «все» picks every line again.
import { describe, expect, it } from "vitest";
import { asLoaded, fromStored } from "../../src/edit/io";
import { SectionLock } from "../../src/edit/lock";
import type { EditLayer } from "../../src/edit/model";
import { EditSession } from "../../src/edit/session";
import type { EditPointer } from "../../src/edit/tools";
import { seriesGeometry, type SeriesGeometry } from "../../src/viz/section/series";
import { ortho } from "./testUtil";

const X0 = 500000;
const Y0 = 7000000;
// a plan series: v̂ = (0, 0, −1), sections are the levels Z = 100, 90, 80 (windows ±5 m)
const g = seriesGeometry({ type: "plan", p1: [X0, Y0, 100], az: 0, s0: 0, s1: 20, step: 10, towards: 5, away: 5 }) as SeriesGeometry;

/** Two squares in plan: one at Z = 90 (section 1), one crossing from Z = 100 to 90 (no section). */
function layer(): EditLayer {
  const sq = (x: number, z0: number, z1: number) => [x, Y0, z0, x + 10, Y0, z0, x + 10, Y0 + 10, z1, x, Y0 + 10, z1];
  return fromStored(Float64Array.from([...sq(X0 + 10, 90, 90), ...sq(X0 + 40, 100, 90)]), [0, 4, 8], [1, 1], []);
}

function session(): EditSession {
  const s = new EditSession(asLoaded(1, 1, layer()));
  const m = ortho(X0, X0 + 100, Y0 - 50, Y0 + 50, -1000, 1000); // 4 px per metre
  s.view = { transform: () => ({ matrix: m, width: 400, height: 400 }), ray: (cx, cy) => ({ origin: [X0 + cx / 4, Y0 + 50 - cy / 4, 1000], dir: [0, 0, -1] }) };
  s.plane = g.plane(1);
  s.clip = true;
  s.lock.setTest((pts) => g.lineSection(pts) === 1);
  s.viewChanged();
  return s;
}
const px = (x: number, y: number) => ({ x: (x - X0) * 4, y: (Y0 + 50 - y) * 4 });
const click = (x: number, y: number, extra: Partial<EditPointer> = {}): EditPointer => {
  const p = px(x, y);
  return { type: "click", x: p.x, y: p.y, clientX: p.x, clientY: p.y, button: 0, shift: false, ctrl: false, ...extra };
};

describe("SectionLock", () => {
  it("only the lines on the current section are picked; «все» picks the others again", () => {
    const s = session();
    const [onIt, off] = s.layer.lines;
    s.pointer(click(X0 + 10, Y0)); // a vertex of the square on the section
    expect(s.selection.lines).toEqual([onIt.id]);
    s.pointer(click(X0 + 40, Y0)); // a vertex of the crossing square: not picked, the selection goes
    expect(s.selection.lines).toEqual([]);
    s.lock.setScope("all");
    s.pointer(click(X0 + 40, Y0));
    expect(s.selection.lines).toEqual([off.id]);
  });

  it("a box takes only the lines on the section; without a test of a series everything is editable", () => {
    const s = session();
    const a = px(X0, Y0 + 20);
    const b = px(X0 + 60, Y0 - 10);
    s.pointer({ type: "down", x: a.x, y: a.y, clientX: a.x, clientY: a.y, button: 0, shift: false, ctrl: true });
    s.pointer({ type: "move", x: b.x, y: b.y, clientX: b.x, clientY: b.y, button: 0, shift: false, ctrl: true });
    s.pointer({ type: "up", x: b.x, y: b.y, clientX: b.x, clientY: b.y, button: 0, shift: false, ctrl: true });
    expect(s.selection.lines).toEqual([s.layer.lines[0].id]);
    const lock = new SectionLock();
    const l = layer();
    expect(lock.pickable(l)).toBe(l);
    lock.setTest(() => false);
    expect(lock.pickable(l).lines).toEqual([]);
    expect(lock.pickable(l)).toBe(lock.pickable(l));
  });
});
