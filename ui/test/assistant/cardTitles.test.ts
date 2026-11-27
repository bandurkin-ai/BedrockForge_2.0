// The titles of the cards of the commands of the window (Э23, 23.21в): the core puts `title = name` into a waiting card it restores, the window shows the title of the registry,
// as it does for a live `pending`; a title is never empty.
import { describe, expect, it } from "vitest";
import { applyEvents, EMPTY_CHAT, fromDialog } from "../../src/modules/assistant/reduce";
import type { Dialog, DialogHead, Item, ToolItem, TurnEnvelope } from "../../src/modules/assistant/wireTypes";

const T0 = "2026-11-26T10:00:00Z";
const head = (over: Partial<DialogHead> = {}): DialogHead => ({
  id: "41",
  uid: "d-1",
  title: "Сводка",
  created_at: T0,
  last_at: T0,
  questions: 1,
  cost_usd: null,
  tokens: { input: 0, output: 0, cache_read: 0, cache_write: 0 },
  models: [],
  state: "pending",
  temporary: false,
  project_uid: "p-1",
  ...over,
});
const uiCard = (id: string, name: string, over: Partial<ToolItem> = {}): ToolItem => ({ kind: "tool", id, at: T0, tool_use_id: `toolu_${id}`, name, side: "ui", title: name, args: {}, status: "waiting", summary: "", ...over });
const pendingOf = (...tools: [string, string, string | null][]) => ({
  id: "p-1",
  created_at: T0,
  tools: tools.map(([item_id, name, command]) => ({ tool_use_id: `toolu_${item_id}`, name, command, args: {}, item_id })),
});
const read = (items: Item[], pending: ReturnType<typeof pendingOf>): Dialog => ({ head: head(), items, pending, note: null });
const titleOf = (t: { name: string }): string | null => (t.name === "ui_hole_select" ? "Выбрать скважину" : null);
const titles = (items: Item[]): string[] => items.map((i) => (i as ToolItem).title);

describe("a dialog read from the core", () => {
  it("a waiting card with the bare name of the tool for a title gets the title of the registry; else the command id; else the name — never empty", () => {
    const d = read([uiCard("c1", "ui_hole_select"), uiCard("c2", "ui_x_unknown"), uiCard("c3", "ui_y")], pendingOf(["c1", "ui_hole_select", "hole.select"], ["c2", "ui_x_unknown", null], ["c3", "ui_y", "y.run"]));
    expect(titles(fromDialog(d, { titleOf }).items)).toEqual(["Выбрать скважину", "ui_x_unknown", "y.run"]);
  });

  it("without `titleOf` the card still has a title", () => {
    expect(titles(fromDialog(read([uiCard("c1", "ui_hole_select")], pendingOf(["c1", "ui_hole_select", null]))).items)).toEqual(["ui_hole_select"]);
  });

  it("a card with its own title, a finished card and a card of a data tool are left as they are", () => {
    const own = uiCard("c1", "ui_hole_select", { title: "Свой заголовок" });
    const done = uiCard("c2", "ui_hole_select", { status: "ok" });
    const data = uiCard("c3", "data_tree", { side: "data" });
    const s = fromDialog(read([own, done, data], pendingOf(["c1", "ui_hole_select", "hole.select"])), { titleOf });
    expect(s.items[0]).toBe(own);
    expect(s.items[1]).toBe(done);
    expect(s.items[2]).toBe(data);
  });
});

describe("a live `pending`", () => {
  it("whose tool has no command and no title in the registry still has a title", () => {
    const ev = { event: "pending", data: pendingOf(["c1", "ui_z", null]), id: 1 } as unknown as TurnEnvelope;
    const s = applyEvents(EMPTY_CHAT, [ev], { titleOf });
    expect(titles(s.items)).toEqual(["ui_z"]);
  });
});
