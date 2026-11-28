/** Quick choice of a colour (Э27, 27.6, Д-43 А4): ten standard colours, the five colours of the user chosen last (the
 * same for all windows, newest first, without repeats), the palette drawn from them. */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ColorPalette } from "../src/shell/ColorPalette";
import {
  BRAND, colorTitle, normalizeHex, normalizeRecent, pushRecent, recentColors, rememberColor, resetRecentColors, standardOf, STANDARD_COLORS, subscribeRecent,
} from "../src/shell/quickColors";

beforeEach(() => resetRecentColors());

describe("the standard colours", () => {
  it("ten, all different, in brand and geological order; the brand colours are the ones of docs/brand", () => {
    expect(STANDARD_COLORS).toHaveLength(10);
    expect(new Set(STANDARD_COLORS.map((c) => c.hex.toLowerCase())).size).toBe(10);
    expect(STANDARD_COLORS.map((c) => c.name)).toEqual(["Красный", "Оранжевый", "Жёлтый", "Зелёный", "Голубой", "Синий", "Фиолетовый", "Коричневый", "Серый", "Чёрный"]);
    for (const c of STANDARD_COLORS) expect(c.hex, c.id).toMatch(/^#[0-9A-F]{6}$/);
    const hex = (id: string) => STANDARD_COLORS.find((c) => c.id === id)?.hex;
    expect(hex("orange")).toBe("#F46A1A"); // Copper Orange
    expect(hex("blue")).toBe("#1F6BFF"); // Cobalt
    expect(hex("gray")).toBe("#8A99A8"); // Slate
    expect(hex("black")).toBe("#1B222B"); // Anthracite
  });

  it("the brand colours are not copied by hand: they are the ones of the brand book and of the tokens of the interface", () => {
    const book = readFileSync(resolve(__dirname, "../../docs/brand/README.md"), "utf-8");
    const tokens = readFileSync(resolve(__dirname, "../src/styles/tokens.css"), "utf-8").toLowerCase();
    const inBook = (name: string) => new RegExp(`\\|\\s*${name}\\s*\\|\\s*\`(#[0-9A-Fa-f]{6})\``).exec(book)?.[1];
    expect(BRAND.copper).toBe(inBook("Copper Orange"));
    expect(BRAND.cobalt).toBe(inBook("Cobalt"));
    expect(BRAND.slate).toBe(inBook("Slate"));
    expect(BRAND.anthracite).toBe(inBook("Anthracite"));
    for (const hex of Object.values(BRAND)) expect(tokens, hex).toContain(hex.toLowerCase());
  });

  it("the hint of a swatch carries the name and the code", () => {
    expect(colorTitle("#D32F2F")).toBe("Красный #D32F2F");
    expect(colorTitle("#d32f2f")).toBe("Красный #D32F2F");
    expect(colorTitle("#a1b2c3")).toBe("Свой цвет #A1B2C3");
    expect(standardOf("#1f6bff")?.name).toBe("Синий");
    expect(standardOf("#123456")).toBeNull();
    expect(normalizeHex(" #ABCDEF ")).toBe("#abcdef");
    expect(normalizeHex("red")).toBeNull();
  });
});

describe("the history of the colours of the user", () => {
  it("the newest first, no repeats (a repeat comes to the front), five at most", () => {
    let h: string[] = [];
    for (const c of ["#111111", "#222222", "#333333", "#444444", "#555555"]) h = pushRecent(h, c);
    expect(h).toEqual(["#555555", "#444444", "#333333", "#222222", "#111111"]);
    expect(pushRecent(h, "#333333")).toEqual(["#333333", "#555555", "#444444", "#222222", "#111111"]);
    expect(pushRecent(h, "#666666")).toEqual(["#666666", "#555555", "#444444", "#333333", "#222222"]);
    expect(pushRecent(h, "#ABCDEF")[0]).toBe("#abcdef");
  });

  it("a standard colour and a value that is not a colour are not kept", () => {
    expect(pushRecent(["#111111"], "#D32F2F")).toEqual(["#111111"]);
    expect(pushRecent(["#111111"], "#f46a1a")).toEqual(["#111111"]);
    expect(pushRecent(["#111111"], "not a colour")).toEqual(["#111111"]);
    expect(pushRecent(["#111111"], undefined)).toEqual(["#111111"]);
  });

  it("what was stored is cleaned: colours only, without repeats, five at most", () => {
    expect(normalizeRecent(["#AAAAAA", "x", "#aaaaaa", 7, "#bbbbbb", "#cccccc", "#dddddd", "#eeeeee", "#ffffff"])).toEqual(["#aaaaaa", "#bbbbbb", "#cccccc", "#dddddd", "#eeeeee"]);
    expect(normalizeRecent({ a: 1 })).toEqual([]);
    expect(normalizeRecent(null)).toEqual([]);
  });

  it("shared by the windows: one history, every subscriber is told once per change", () => {
    const told = vi.fn();
    const off = subscribeRecent(told);
    rememberColor("#112233");
    rememberColor("#445566");
    rememberColor("#445566"); // the same colour at the front: nothing changes, nobody is told
    rememberColor("#D32F2F"); // a standard one is not kept
    expect(recentColors()).toEqual(["#445566", "#112233"]);
    expect(told).toHaveBeenCalledTimes(2);
    off();
    rememberColor("#778899");
    expect(told).toHaveBeenCalledTimes(2);
    expect(recentColors()[0]).toBe("#778899");
  });
});

describe("the palette", () => {
  const draw = (props: { value?: string; disabled?: boolean; extra?: string[] } = {}) => renderToStaticMarkup(createElement(ColorPalette, { onPick: () => undefined, ...props }));

  it("ten standard swatches with Russian hints, five empty plates before anything was chosen", () => {
    const html = draw();
    for (const c of STANDARD_COLORS) expect(html).toContain(`title="${c.name} ${c.hex}"`);
    expect(html.match(/<button/g)).toHaveLength(10);
    expect(html.match(/Здесь появятся последние выбранные цвета/g)).toHaveLength(5);
    expect(html).toContain('aria-label="Быстрый выбор цвета"');
  });

  it("the colours chosen last take the plates, the newest first; the colour of the field is marked", () => {
    for (const c of ["#111111", "#222222", "#333333", "#444444", "#555555", "#666666"]) rememberColor(c);
    const html = draw({ value: "#1F6BFF" });
    const recent = html.slice(html.indexOf('data-testid="color-recent"'));
    expect(recent.match(/Свой цвет #[0-9A-F]{6}/g)?.filter((_, i) => i % 2 === 0)).toEqual(["Свой цвет #666666", "Свой цвет #555555", "Свой цвет #444444", "Свой цвет #333333", "Свой цвет #222222"]);
    expect(html.match(/<button/g)).toHaveLength(15);
    expect(html).not.toContain("Здесь появятся");
    expect(html.match(/aria-pressed="true"/g)).toHaveLength(1); // Cobalt
    expect(html).toMatch(/title="Синий #1F6BFF"[^>]*aria-pressed="true"|aria-pressed="true"[^>]*title="Синий #1F6BFF"/);
  });

  it("the colours already used in the display make a row of their own, standard ones are not repeated", () => {
    const html = draw({ extra: ["#abcdef", "#ABCDEF", "#D32F2F"] });
    expect(html).toContain('data-testid="color-used"');
    expect(html.match(/уже в показе/g)).toHaveLength(1); // one swatch: a repeat and a standard colour are left out
    expect(html.match(/<button/g)).toHaveLength(11);
    expect(draw()).not.toContain("color-used");
  });

  it("disabled: every swatch is disabled", () => {
    expect(draw({ disabled: true }).match(/disabled=""/g)).toHaveLength(10);
  });
});
