/**
 * The axes gizmo in the lower left corner of the 3D view (ТЗ 7.13; BF `AxesGizmo` 2897–2987): an SVG of
 * 92 × 92 px, 10 px from the left and 26 px from the bottom, turned with the camera after every frame. The
 * geometry comes from the pure `gizmo/math.ts`; a click on an axis head (r = 8) asks to look along the axis
 * from its positive side, on the small circle opposite (r = 4) — from its negative side. Not clipped.
 */
import { AXIS_COLORS, GIZMO, gizmoAxes, gizmoDirection, gizmoTitles, type AxisKey } from "./math";
import type { CameraState } from "../core/views";
import type { Vec3 } from "../core/vec3";

const NS = "http://www.w3.org/2000/svg";

interface Item {
  key: AxisKey;
  g: SVGGElement;
  line: SVGLineElement;
  head: SVGCircleElement;
  back: SVGCircleElement;
  label: SVGTextElement;
}

function el<K extends keyof SVGElementTagNameMap>(name: K, attrs: Record<string, string> = {}): SVGElementTagNameMap[K] {
  const e = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}

export class AxesGizmo {
  readonly svg: SVGSVGElement;
  private readonly items: Item[] = [];
  private flipZ = false;

  constructor(host: HTMLElement, onTurn: (from: Vec3) => void) {
    const half = GIZMO.size / 2;
    this.svg = el("svg", { viewBox: `${-half} ${-half} ${GIZMO.size} ${GIZMO.size}`, "data-testid": "axes-gizmo", role: "group", "aria-label": "Оси координат" });
    this.svg.style.cssText = `position:absolute;left:${GIZMO.left}px;bottom:${GIZMO.bottom}px;width:${GIZMO.size}px;height:${GIZMO.size}px;pointer-events:none;z-index:2;overflow:visible`;
    const ring = el("circle", { r: String(GIZMO.ring) });
    ring.style.cssText = "fill:color-mix(in srgb, var(--bg) 70%, transparent);stroke:color-mix(in srgb, var(--text) 14%, transparent);stroke-width:1";
    this.svg.appendChild(ring);
    for (const key of ["x", "y", "z"] as AxisKey[]) {
      const color = AXIS_COLORS[key];
      const titles = gizmoTitles(key);
      const g = el("g", { "data-axis": key });
      const back = el("circle", { r: String(GIZMO.back), "data-sign": "-1", "aria-label": titles.back });
      back.style.cssText = `fill:var(--scene-bg);fill-opacity:0.01;stroke:${color};stroke-width:1.5;opacity:0.55;pointer-events:all;cursor:pointer`;
      const line = el("line", { x1: "0", y1: "0" });
      line.style.cssText = `stroke:${color};stroke-width:2.5;stroke-linecap:round`;
      const head = el("circle", { r: String(GIZMO.head), "data-sign": "1", "aria-label": titles.head });
      head.style.cssText = `fill:${color};stroke:var(--scene-bg);stroke-width:1.5;pointer-events:all;cursor:pointer`;
      const label = el("text", { "text-anchor": "middle", "dominant-baseline": "central" });
      label.textContent = key.toUpperCase();
      label.style.cssText = "font:700 10px/1 var(--font-brand);fill:#fff;pointer-events:none";
      for (const [circle, text] of [[head, titles.head], [back, titles.back]] as const) {
        const t = el("title");
        t.textContent = text;
        circle.appendChild(t);
      }
      const pick = (sign: 1 | -1) => (e: MouseEvent) => {
        e.stopPropagation();
        onTurn(gizmoDirection({ key, sign }, this.flipZ));
      };
      head.addEventListener("click", pick(1));
      back.addEventListener("click", pick(-1));
      // the view below must not take the press as the start of a rotation
      for (const c of [head, back]) c.addEventListener("pointerdown", (e) => e.stopPropagation());
      g.append(back, line, head, label);
      this.svg.appendChild(g);
      this.items.push({ key, g, line, head, back, label });
    }
    host.appendChild(this.svg);
  }

  /** Turns the axes with the camera; the nearest axis is drawn last, axes away from the viewer are dimmed. */
  update(cam: CameraState, flipZ: boolean): void {
    this.flipZ = flipZ;
    const axes = gizmoAxes(cam, flipZ);
    for (const a of axes) {
      const it = this.items.find((i) => i.key === a.key)!;
      const x = a.x.toFixed(2);
      const y = a.y.toFixed(2);
      it.line.setAttribute("x2", x);
      it.line.setAttribute("y2", y);
      it.head.setAttribute("cx", x);
      it.head.setAttribute("cy", y);
      it.label.setAttribute("x", x);
      it.label.setAttribute("y", y);
      it.back.setAttribute("cx", a.backX.toFixed(2));
      it.back.setAttribute("cy", a.backY.toFixed(2));
      it.g.style.opacity = String(a.opacity);
      it.g.dataset.away = a.away ? "1" : "0";
      this.svg.appendChild(it.g); // far to near
    }
  }

  dispose(): void {
    this.svg.remove();
  }
}
