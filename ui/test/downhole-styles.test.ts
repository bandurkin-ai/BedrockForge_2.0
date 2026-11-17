import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { DEFAULT_STRIPLOG, withDefaults, type StripLogSettings, type Track } from "../src/charts/striplog/model";
import {
  applyLayerStyle, applyPalette, applyTemplate, BUILTIN_STYLES, defaultStyleFor, fileConflicts, layerBody, matchFields, nameMatches, paletteBody,
  parseStyleFile, resolveTracks, templateBody, toStyleFile, unlinkStyle, usedStyles, type StyleRec,
} from "../src/viz/downhole/styles";

const track = (patch: Partial<Track> = {}): Track => ({
  id: "t1", table: "5", field: "AU", type: "bars", width: 90, color: "#ff0000", min: "", max: "", log: true, boundaries: false, ...patch,
});
const display = (tracks: Track[], extra: Partial<StripLogSettings> = {}): StripLogSettings => ({ ...DEFAULT_STRIPLOG, allHoles: true, tracks, ...extra });
const classes = [{ from: null, color: "#111111" }, { from: 1, color: "#ff0000" }];
const layerStyle: StyleRec = { id: 7, uid: "s7", name: "Au столбцы", level: "layer", data_kind: "number", default_for: ["AU*"], body: { type: "steps", colorBy: "classes", classes, side: "left", log: true, width: 60 } };
const palette: StyleRec = { id: 9, uid: "s9", name: "Au борта", level: "palette", data_kind: "number", default_for: [], body: { kind: "classes", classes } };

describe("styles of downhole displays", () => {
  it("opens old settings without the new fields", () => {
    const old = withDefaults({ tracks: [{ table: "5", field: "AU" } as Track] });
    expect(resolveTracks(old, [layerStyle])).toBe(old); // nothing linked: untouched
    expect(old.tracks[0].style).toBeUndefined();
  });

  it("takes a style as a copy, leaving out the binding to data", () => {
    const body = layerBody(track({ visible: false, style: { uid: "s1", name: "x", linked: false } }));
    expect(body).not.toHaveProperty("id");
    expect(body).not.toHaveProperty("table");
    expect(body).not.toHaveProperty("field");
    expect(body).not.toHaveProperty("style");
    expect(body).not.toHaveProperty("visible");
    const t = applyLayerStyle(track({ field: "AU_PPM", id: "x" }), layerStyle);
    expect(t).toMatchObject({ id: "x", field: "AU_PPM", table: "5", type: "steps", side: "left", style: { uid: "s7", name: "Au столбцы", linked: false } });
  });

  it("draws a linked layer with the settings of its style, whatever copy it keeps", () => {
    const linked = track({ type: "bars", side: "right", style: { uid: "s7", name: "Au столбцы", linked: true } });
    const r = resolveTracks(display([linked]), [layerStyle, palette]);
    expect(r.tracks[0]).toMatchObject({ type: "steps", side: "left", id: "t1", field: "AU", style: { linked: true } });
    const changed = { ...layerStyle, body: { ...layerStyle.body, type: "line" } };
    expect(resolveTracks(display([linked]), [changed]).tracks[0].type).toBe("line");
  });

  it("falls back to the layer's own copy when the style is gone", () => {
    const linked = track({ type: "points", style: { uid: "s404", name: "нет", linked: true }, palette: { uid: "s405", name: "нет", linked: true } });
    const r = resolveTracks(display([linked]), [layerStyle]);
    expect(r.tracks[0]).toMatchObject({ type: "points", style: { linked: false }, palette: { linked: false } });
  });

  it("applies and links a colouring", () => {
    const t = applyPalette(track(), palette, true);
    expect(t).toMatchObject({ colorBy: "classes", classes, palette: { uid: "s9", linked: true } });
    const newer = { ...palette, body: { kind: "classes", classes: [{ from: null, color: "#00ff00" }] } };
    expect(resolveTracks(display([t]), [newer]).tracks[0].classes).toEqual([{ from: null, color: "#00ff00" }]);
    expect(paletteBody(t, true)).toEqual({ kind: "classes", classes });
    expect(paletteBody(track({ colorBy: "single" }), true)).toBeNull();
    expect(paletteBody(track({ catColors: { A: "#000000" }, hidden: ["B"] }), false)).toEqual({ kind: "categories", catColors: { A: "#000000" }, hidden: ["B"] });
  });

  it("finds the default style of a field by name and wildcard", () => {
    expect(nameMatches("AU*", "au_ppm")).toBe(true);
    expect(nameMatches("AU", "AU_PPM")).toBe(false);
    expect(nameMatches("*_PPM", "Cu_ppm")).toBe(true);
    expect(nameMatches("A.U", "AXU")).toBe(false);
    expect(defaultStyleFor("Au_ppm", "number", [palette, layerStyle])?.id).toBe(7);
    expect(defaultStyleFor("CU", "number", [layerStyle])).toBeNull();
    expect(defaultStyleFor("AU", "text", [layerStyle])).toBeNull(); // a style of numbers does not go to a text field
    expect(defaultStyleFor("AU", "number", BUILTIN_STYLES)).toBeNull();
  });

  it("maps the layers of a template onto the fields of another table by name", () => {
    const body = templateBody(display([track({ field: "AU" }), track({ id: "t2", field: "Cu", type: "color" }), track({ id: "t3", field: "LITH" })], { tube: 6 }));
    expect(body.tube).toBe(6);
    const matches = matchFields(body, [{ name: "au" }, { name: "CU" }, { name: "AS" }]);
    expect(matches.map((m) => m.field)).toEqual(["au", "CU", null]);
    let n = 0;
    const tracks = applyTemplate(body, "8", matches, () => `n${n++}`);
    expect(tracks.map((t) => [t.id, t.table, t.field, t.type])).toEqual([["n0", "8", "au", "bars"], ["n1", "8", "CU", "color"]]);
    matches[2].field = "AS"; // chosen by hand
    expect(applyTemplate(body, "8", matches, () => "x")).toHaveLength(3);
  });

  it("draws a display linked to a template with the template's layers", () => {
    const tpl: StyleRec = { id: 20, uid: "s20", name: "Шаблон", level: "display", data_kind: "mixed", default_for: [], body: { tracks: [{ field: "AU", type: "line", color: "#00ff00" }] } };
    const s = display([track(), track({ id: "t2", field: "CU" })], { template: { uid: "s20", name: "Шаблон", linked: true } });
    const r = resolveTracks(s, [tpl]);
    expect(r.tracks[0]).toMatchObject({ id: "t1", field: "AU", type: "line", color: "#00ff00" });
    expect(r.tracks[1].type).toBe("bars"); // no layer of the template for CU
  });

  it("lists the styles a display uses and unlinks one before it is deleted", () => {
    const s = display([track({ style: { uid: "s7", name: "a", linked: true }, palette: { uid: "s9", name: "p", linked: false } })]);
    expect(usedStyles(s)).toEqual([{ uid: "s7", linked: true }, { uid: "s9", linked: false }]);
    const u = unlinkStyle(s, "s7", [layerStyle]);
    expect(u.tracks[0].style).toBeUndefined();
    expect(u.tracks[0].type).toBe("steps"); // keeps the style's settings as its own
  });

  it("writes a file of styles and reads it back the same", () => {
    const file = toStyleFile([layerStyle, palette]);
    const back = parseStyleFile(JSON.stringify(file));
    expect(back).toEqual(file);
    expect(back.styles[0]).not.toHaveProperty("id");
    expect(back.styles[0].uid).toBe("s7"); // BF2: links survive the file
    expect(fileConflicts(back, [palette, ...BUILTIN_STYLES]).map((s) => s.name)).toEqual(["Au борта"]);
  });

  it("rejects a file of another format or version with a clear message", () => {
    expect(() => parseStyleFile("{")).toThrow(/не JSON/);
    expect(() => parseStyleFile(JSON.stringify({ format: "x", version: 1, styles: [] }))).toThrow(/не файл стилей/);
    expect(() => parseStyleFile(JSON.stringify({ format: "bedrockforge-styles", version: 2, styles: [] }))).toThrow(/Неизвестная версия файла стилей: 2/);
    expect(() => parseStyleFile(JSON.stringify({ format: "bedrockforge-styles", version: 1, styles: [{ name: "", level: "layer", body: {} }] }))).toThrow(/записан неверно/);
  });

  it("unlinks a deleted style by the same rule as the core (shared fixtures)", () => {
    const dir = fileURLToPath(new URL("../../core/tests/data/styles/", import.meta.url));
    const files = readdirSync(dir).filter((f) => f.endsWith(".json"));
    expect(files.length).toBeGreaterThanOrEqual(3);
    for (const f of files) {
      const c = JSON.parse(readFileSync(dir + f, "utf8")) as { style: { uid: string; level: StyleRec["level"]; body: Record<string, unknown> }; settings: StripLogSettings; expected: StripLogSettings };
      const rec: StyleRec = { id: 1, uid: c.style.uid, name: "x", level: c.style.level, data_kind: "mixed", default_for: [], body: c.style.body };
      const strip = (x: unknown) => JSON.parse(JSON.stringify(x)); // undefined fields are not in the JSON of the core
      expect(strip(unlinkStyle(c.settings, c.style.uid, [rec])), f).toEqual(c.expected);
    }
  });
});
