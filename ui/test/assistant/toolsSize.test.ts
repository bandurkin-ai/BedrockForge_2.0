// Early measuring point of Э23 (ADR 0029 decision 17, подшаг 23.9): how big are the tools that the window sends with
// every turn - `commandTools(createRegistry().list())`, all commands of the registry that are not `uiOnly`. The measure
// of 23.9: 341 779 bytes, 270 tools, ~79.5 thousand tokens - decision 23.9б: the core accepts up to 512 KB and sends the
// API tools shortened to ~290 KB (`build_api_tools`). The test prints the figures
// and checks the threshold, the contract limits of e23-api.md §5.1 and the names.
import { describe, expect, it } from "vitest";
import { createRegistry } from "../../src/shell/catalog";
import { commandTools } from "../../src/shell/commands";

const LIMIT_BYTES = 512 * 1024; // decision 17 as changed by 23.9б: the full set up to 512 KB (to the API ~290 KB after shortening)
const CONTRACT_BYTES = 512 * 1024; // §5.1: the core refuses more
const CONTRACT_COUNT = 400;
const CONTRACT_DESCRIPTION = 2000;
const SHORTEN_ABOVE = 600; // 23.9б: the descriptions longer than this are the ones to shorten
const enc = new TextEncoder();

describe("size of commandTools(createRegistry().list()) (ADR 0029 decision 17)", () => {
  const commands = createRegistry().list();
  const tools = commandTools(commands);
  const json = JSON.stringify(tools);
  const bytes = enc.encode(json).length;
  const tokens = Math.ceil(json.length / 3); // the estimate of window.py: characters / 3
  const sizes = tools.map((t) => ({ name: t.name, chars: t.description.length, bytes: enc.encode(JSON.stringify(t)).length }));
  const byDescription = [...sizes].sort((a, b) => b.chars - a.chars);

  it("prints the figures: commands, bytes, tokens, the ten longest descriptions", () => {
    const long = sizes.filter((s) => s.chars > SHORTEN_ABOVE);
    const saved = tools.reduce((sum, t) => sum + enc.encode(t.description.slice(SHORTEN_ABOVE)).length, 0);
    const lines = [
      `commands in the registry: ${commands.length}, tools for Claude (not uiOnly): ${tools.length}`,
      `commandTools JSON: ${bytes} bytes (${(bytes / 1024).toFixed(1)} KB), ${json.length} characters, ~${tokens} tokens (characters / 3)`,
      `limit ${LIMIT_BYTES} bytes (512 KB): ${bytes <= LIMIT_BYTES ? "within" : "EXCEEDED"}; contract maximum ${CONTRACT_BYTES}`,
      `descriptions > ${SHORTEN_ABOVE} characters: ${long.length}, bytes beyond ${SHORTEN_ABOVE} characters (what 23.9б could save): ${saved}`,
      "ten longest descriptions (name, characters):",
      ...byDescription.slice(0, 10).map((s, i) => `  ${i + 1}. ${s.name} - ${s.chars}`),
    ];
    console.log(`\n[toolsSize]\n${lines.join("\n")}\n`);
    expect(tools.length).toBeGreaterThan(0);
  });

  it("does not grow past the figure of the measure without a decision (guard: 400 KB; the core accepts 512 KB)", () => {
    expect(bytes).toBeLessThanOrEqual(400 * 1024);
  });

  it("fits the other limits of the core (§5.1): count, names, description", () => {
    expect(tools.length).toBeLessThanOrEqual(CONTRACT_COUNT);
    const names = tools.map((t) => t.name);
    expect(new Set(names).size).toBe(names.length);
    for (const t of tools) {
      expect(t.name, t.name).toMatch(/^ui_[a-z0-9_]{1,60}$/);
      expect(t.description.length, t.name).toBeLessThanOrEqual(CONTRACT_DESCRIPTION);
    }
  });

  it("holds exactly the commands that are not uiOnly", () => {
    expect(tools.length).toBe(commands.filter((c) => !c.uiOnly).length);
  });
});
