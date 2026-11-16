import { describe, expect, it } from "vitest";
import { moduleCommands } from "../src/shell/catalog";

describe("command catalog (ТЗ 7.2)", () => {
  const all = moduleCommands();
  const ids = all.map((c) => c.id);

  it("has unique ids and all 90 commands of 7.2 plus shell extras", () => {
    expect(new Set(ids).size).toBe(ids.length);
    const shellExtras = ["license.open"];
    expect(all.filter((c) => !shellExtras.includes(c.id)).length).toBeGreaterThanOrEqual(90);
    expect(all.filter((c) => c.id.startsWith("model.") && c.uiOnly).length).toBe(15);
  });

  it("silent commands are exactly those listed in 7.2", () => {
    const silent = all.filter((c) => c.silent).map((c) => c.id).sort();
    expect(silent).toEqual(
      ["assistant.ask", "assistant.new", "palette.open", "ribbon.toggle", "section.draw", "workspace.state"].sort(),
    );
  });

  it("every command has a Russian title and a description for the model", () => {
    for (const c of all) {
      if (c.id !== "stats.swath") expect(c.title, c.id).toMatch(/[А-Яа-яЁё]/); // «SWATH PLOT» — as in ТЗ 7.0.4
      expect(c.description.length, c.id).toBeGreaterThan(5);
    }
  });
});
