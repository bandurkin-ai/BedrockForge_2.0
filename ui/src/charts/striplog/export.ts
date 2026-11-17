/**
 * Saving a chart (ТЗ 7.17): SVG and PNG with the theme's colours baked in and the brand fonts embedded
 * (Montserrat, Inter as data URLs — ADR 0006), so the file looks the same on a PC without them.
 */
import interUrl from "@fontsource/inter/files/inter-cyrillic-400-normal.woff2?url";
import interLatinUrl from "@fontsource/inter/files/inter-latin-400-normal.woff2?url";
import montUrl from "@fontsource/montserrat/files/montserrat-cyrillic-600-normal.woff2?url";
import montLatinUrl from "@fontsource/montserrat/files/montserrat-latin-600-normal.woff2?url";

const STYLE_PROPS = ["fill", "stroke", "stroke-width", "stroke-dasharray", "opacity", "font-size", "font-family", "font-weight"];

export interface FontFace {
  family: string;
  weight: number;
  /** data:font/woff2;base64,… */
  src: string;
}

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

/** The fonts as `@font-face` rules inside the SVG (a <style> right after the opening tag). */
export function embedFonts(svgText: string, fonts: FontFace[]): string {
  if (!fonts.length) return svgText;
  const rules = fonts.map((f) => `@font-face{font-family:"${f.family}";font-weight:${f.weight};src:url(${f.src}) format("woff2");}`).join("");
  const at = svgText.indexOf(">", svgText.indexOf("<svg"));
  if (at < 0) return svgText;
  return `${svgText.slice(0, at + 1)}<defs><style>${rules}</style></defs>${svgText.slice(at + 1)}`;
}

let fontCache: Promise<FontFace[]> | null = null;

async function dataUrl(url: string): Promise<string> {
  const buf = new Uint8Array(await (await fetch(url)).arrayBuffer());
  let bin = "";
  for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  return `data:font/woff2;base64,${btoa(bin)}`;
}

export function brandFonts(): Promise<FontFace[]> {
  fontCache ??= Promise.all(
    [
      ["Inter", 400, interUrl],
      ["Inter", 400, interLatinUrl],
      ["Montserrat", 600, montUrl],
      ["Montserrat", 600, montLatinUrl],
    ].map(async ([family, weight, url]) => ({ family: family as string, weight: weight as number, src: await dataUrl(url as string) })),
  ).catch(() => []);
  return fontCache;
}

export async function svgText(svg: SVGSVGElement): Promise<string> {
  return embedFonts(standaloneSvg(svg), await brandFonts());
}

/** PNG at twice the chart's size (print quality). */
export async function pngBytes(svg: SVGSVGElement, scale = 2): Promise<Uint8Array> {
  const box = svg.viewBox.baseVal;
  const img = new Image();
  const url = URL.createObjectURL(new Blob([await svgText(svg)], { type: "image/svg+xml" }));
  try {
    await new Promise<void>((resolve, reject) => {
      img.onload = () => resolve();
      img.onerror = () => reject(new Error("Не удалось подготовить изображение"));
      img.src = url;
    });
    const canvas = document.createElement("canvas");
    canvas.width = box.width * scale;
    canvas.height = box.height * scale;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Не удалось подготовить изображение");
    ctx.scale(scale, scale);
    ctx.drawImage(img, 0, 0);
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
    if (!blob) throw new Error("Не удалось подготовить изображение");
    return new Uint8Array(await blob.arrayBuffer());
  } finally {
    URL.revokeObjectURL(url);
  }
}
