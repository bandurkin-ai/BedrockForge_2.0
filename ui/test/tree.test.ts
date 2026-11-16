import { describe, expect, it } from "vitest";
import { buildFolders, detectImportKind, normalizeFolder } from "../src/shell/tree";

describe("data tree (ТЗ 7.1)", () => {
  it("an object in folder A/B is shown in nested folders A → B", () => {
    const root = buildFolders([
      { key: "wf:1", name: "Рудное тело", folder: "A/B" },
      { key: "wf:2", name: "Разлом", folder: "A" },
      { key: "wf:3", name: "Топо", folder: "" },
    ]);
    expect(root.objects.map((o) => o.name)).toEqual(["Топо"]);
    const a = root.folders[0];
    expect([a.name, a.objects[0].name, a.folders[0].path, a.folders[0].objects[0].name]).toEqual(["A", "Разлом", "A/B", "Рудное тело"]);
  });

  it("folder paths are normalised; empty — root", () => {
    expect(normalizeFolder(" A / B/ ")).toBe("A/B");
    expect(normalizeFolder("///")).toBe("");
  });

  it("dropping a .tridb opens the wizard with type «каркасы»", () => {
    expect(detectImportKind(["ORE SEP 2025.tridb"])).toBe("wireframes");
    expect(detectImportKind(["Восточный.STR"])).toBe("lines");
    expect(detectImportKind(["план.jpg", "план.jgw"])).toBe("rasters");
    expect(detectImportKind(["collar.dat", "assay.csv"])).toBe("holes");
    expect(detectImportKind(["readme.md"])).toBeNull();
  });
});
