/**
 * Marker textures of the collars (ТЗ 7.15; BF `makeMarkerTexture` / `makeCircleTexture` 2846–2895): a white
 * shape with a dark outline, tinted by the colour of each point — circle, square, diamond, triangle, cross,
 * star. One texture per shape, shared by all sets; rebuilt after a lost context.
 */
import * as THREE from "three";
import type { MarkerShape } from "../../charts/striplog/model";

function draw(shape: MarkerShape): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 64;
  const ctx = canvas.getContext("2d");
  if (!ctx) return canvas;
  ctx.fillStyle = "#ffffff";
  ctx.strokeStyle = "rgba(0,0,0,0.6)";
  ctx.lineWidth = 5;
  ctx.lineJoin = "round";
  const poly = (pts: number[][]) => {
    ctx.beginPath();
    pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  };
  if (shape === "square") poly([[8, 8], [56, 8], [56, 56], [8, 56]]);
  else if (shape === "diamond") poly([[32, 4], [60, 32], [32, 60], [4, 32]]);
  else if (shape === "triangle") poly([[32, 5], [59, 56], [5, 56]]);
  else if (shape === "cross") poly([[22, 4], [42, 4], [42, 22], [60, 22], [60, 42], [42, 42], [42, 60], [22, 60], [22, 42], [4, 42], [4, 22], [22, 22]]);
  else if (shape === "star") {
    const pts: number[][] = [];
    for (let i = 0; i < 10; i++) {
      const r = i % 2 ? 13 : 29;
      const a = -Math.PI / 2 + (i * Math.PI) / 5;
      pts.push([32 + r * Math.cos(a), 32 + r * Math.sin(a)]);
    }
    poly(pts);
  } else {
    ctx.beginPath();
    ctx.arc(32, 32, 28, 0, Math.PI * 2);
    ctx.fill();
    ctx.lineWidth = 5;
    ctx.strokeStyle = "rgba(0,0,0,0.55)";
    ctx.stroke();
  }
  return canvas;
}

export class MarkerTextures {
  private readonly items = new Map<MarkerShape, THREE.CanvasTexture>();

  get(shape: MarkerShape): THREE.CanvasTexture {
    let t = this.items.get(shape);
    if (!t) {
      t = new THREE.CanvasTexture(draw(shape));
      t.colorSpace = THREE.SRGBColorSpace;
      this.items.set(shape, t);
    }
    return t;
  }

  dispose(): void {
    for (const t of this.items.values()) t.dispose();
    this.items.clear();
  }
}
