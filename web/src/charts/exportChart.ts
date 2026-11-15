/** Saving charts: SVG and PNG with the theme's colours baked in, CSV of the bins. */

const STYLE_PROPS = ["fill", "stroke", "stroke-width", "stroke-dasharray", "opacity", "font-size", "font-family", "font-weight"];

/** A standalone copy of an on-screen SVG: CSS classes and variables resolved into attributes. */
export function standaloneSvg(svg: SVGSVGElement): string {
  const clone = svg.cloneNode(true) as SVGSVGElement;
  const src = svg.querySelectorAll("*");
  const dst = clone.querySelectorAll("*");
  src.forEach((el, i) => {
    const cs = getComputedStyle(el);
    const target = dst[i] as SVGElement;
    for (const p of STYLE_PROPS) {
      const v = cs.getPropertyValue(p);
      if (v) target.setAttribute(p, v);
    }
    target.removeAttribute("class");
  });
  const box = svg.viewBox.baseVal;
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  clone.setAttribute("width", String(box.width));
  clone.setAttribute("height", String(box.height));
  return new XMLSerializer().serializeToString(clone);
}

export function download(name: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  // A detached link may lose its file name in some browsers.
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function saveSvg(svg: SVGSVGElement, name: string): void {
  download(`${name}.svg`, new Blob([standaloneSvg(svg)], { type: "image/svg+xml" }));
}

/** PNG at twice the chart's size (print quality). */
export async function savePng(svg: SVGSVGElement, name: string, scale = 2): Promise<void> {
  const box = svg.viewBox.baseVal;
  const img = new Image();
  const url = URL.createObjectURL(new Blob([standaloneSvg(svg)], { type: "image/svg+xml" }));
  try {
    await new Promise<void>((resolve, reject) => {
      img.onload = () => resolve();
      img.onerror = () => reject(new Error("Не удалось подготовить изображение"));
      img.src = url;
    });
    const canvas = document.createElement("canvas");
    canvas.width = box.width * scale;
    canvas.height = box.height * scale;
    const ctx = canvas.getContext("2d")!;
    ctx.scale(scale, scale);
    ctx.drawImage(img, 0, 0);
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
    if (blob) download(`${name}.png`, blob);
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** CSV with a semicolon separator and decimal points (opens in Excel with Russian settings). */
export function toCsv(header: string[], rows: (string | number | null)[][]): string {
  const cell = (v: string | number | null) => (v === null ? "" : typeof v === "number" ? String(v) : `"${v.replace(/"/g, '""')}"`);
  return "﻿" + [header.map(cell), ...rows.map((r) => r.map(cell))].map((r) => r.join(";")).join("\r\n");
}
