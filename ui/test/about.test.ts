import { describe, expect, it } from "vitest";
import { ABOUT, aboutLines, aboutStore, aboutText, toggleAbout } from "../src/shell/about";

describe("«О производителе» (ТЗ 7.35 Д-12)", () => {
  it("shows the product, the maker (answer № 5) and both versions", () => {
    expect(aboutLines("0.2.0", "0.2.0")).toEqual(["Производитель: Bandurkin Mikhail", "Версия программы: 0.2.0", "Версия ядра: 0.2.0"]);
    expect(aboutLines(null, undefined)).toEqual(["Производитель: Bandurkin Mikhail", "Версия программы: —", "Версия ядра: —"]);
    const text = aboutText("0.2.0", "0.2.1");
    expect(text.split("\n")[0]).toBe("BedrockForge by Mikhail Bandurkin");
    expect(text).toContain(ABOUT.slogan);
  });

  it("no contacts and no links yet", () => {
    expect(aboutText("1", "1")).not.toMatch(/@|https?:|www\./);
  });

  it("the toggle opens and closes the plate (Esc and a click elsewhere close it)", () => {
    expect(toggleAbout()).toBe(true);
    expect(aboutStore.get().open).toBe(true);
    expect(toggleAbout()).toBe(false);
    toggleAbout(true);
    toggleAbout(false);
    expect(aboutStore.get().open).toBe(false);
  });
});
