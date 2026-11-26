// The quick requests of an empty dialog (Э23, 23.14; ТЗ 7.0.4): the three of `QUICK_PROMPTS` of the ribbon, each runs the command `assistant.ask {prompt}`;
// «Проверить скважину» without a chosen hole is unavailable with the reason; none can be sent while the assistant cannot be asked.
import { createElement, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { EMPTY_CONTEXT } from "../../src/shell/commands";
import { CommandRegistry } from "../../src/shell/registry";
import { QUICK_PROMPTS } from "../../src/shell/ribbonConfig";
import { INITIAL_STATE, Store, type AppState } from "../../src/shell/store";
import { askPrompt, NO_HOLE_REASON, QuickPrompts, quickItems, QuickPromptsView } from "../../src/modules/assistant/QuickPrompts";

// `useSyncExternalStore` of the shell hooks has no server snapshot: the string renderer reads the stores once instead
vi.mock("../../src/shell/useStore", () => ({
  useStore: (store: { get: () => unknown }, select: (s: unknown) => unknown) => select(store.get()),
  useRegistryVersion: () => 0,
}));

type El = ReactElement<{ children?: ReactNode; onClick?: () => void; "data-testid"?: string }>;
/** Every host element of a tree of elements (the tree of a component without hooks, called as a function). */
function all(node: ReactNode, out: El[] = []): El[] {
  if (Array.isArray(node)) node.forEach((n) => all(n, out));
  else if (isValidElement(node)) {
    out.push(node as El);
    all((node as El).props.children, out);
  }
  return out;
}
const buttons = (node: ReactNode): El[] => all(node).filter((e) => e.props["data-testid"] === "quick-prompt");

const WITH_HOLE = { ...EMPTY_CONTEXT, hasProject: true, hasHole: true, coreOnline: true };
const NO_HOLE = { ...EMPTY_CONTEXT, hasProject: true, hasHole: false, coreOnline: true };
const HOLE_PROMPT = QUICK_PROMPTS.find((q) => q.needsHole)!;

describe("the reasons", () => {
  it("three requests of the ribbon, in its order", () => {
    const items = quickItems(WITH_HOLE, null, null);
    expect(items.map((i) => i.label)).toEqual(["Сводка по проекту", "Разобрать аудит", "Проверить скважину"]);
    expect(items.map((i) => i.prompt)).toEqual(QUICK_PROMPTS.map((q) => q.prompt));
    expect(items.every((i) => i.reason === null)).toBe(true);
  });

  it("«Проверить скважину» without a chosen hole is unavailable with the reason, the others are not", () => {
    const items = quickItems(NO_HOLE, null, null);
    expect(items.find((i) => i.prompt === HOLE_PROMPT.prompt)?.reason).toBe(NO_HOLE_REASON);
    expect(items.filter((i) => i.reason === null)).toHaveLength(2);
  });

  it("the state of the assistant comes first, then the command, then the hole", () => {
    expect(quickItems(NO_HOLE, "Функция появится на этапе Э23", "Ассистент недоступен без интернета").every((i) => i.reason === "Ассистент недоступен без интернета")).toBe(true);
    expect(quickItems(WITH_HOLE, "Функция появится на этапе Э23", null).every((i) => i.reason === "Функция появится на этапе Э23")).toBe(true);
    expect(quickItems(NO_HOLE, "Функция появится на этапе Э23", null).find((i) => i.prompt === HOLE_PROMPT.prompt)?.reason).toBe("Функция появится на этапе Э23");
  });
});

describe("the buttons", () => {
  it("a click runs the prompt; an unavailable button runs nothing", () => {
    const onPick = vi.fn();
    const tree = QuickPromptsView({ items: quickItems(NO_HOLE, null, null), onPick });
    const bs = buttons(tree);
    expect(bs).toHaveLength(3);
    bs[0].props.onClick?.();
    expect(onPick).toHaveBeenCalledTimes(1);
    expect(onPick).toHaveBeenCalledWith(QUICK_PROMPTS[0].prompt);
    bs[2].props.onClick?.();
    expect(onPick).toHaveBeenCalledTimes(1);
  });

  it("the reason is on the screen and in the tooltip; the button says it is disabled", () => {
    const out = renderToStaticMarkup(createElement(QuickPromptsView, { items: quickItems(NO_HOLE, null, null), onPick: () => undefined }));
    expect(out).toContain(NO_HOLE_REASON);
    expect(out).toContain(`Недоступно: ${NO_HOLE_REASON}`);
    expect(out.match(/aria-disabled="true"/g)).toHaveLength(1);
    expect(out).toContain("Сводка по проекту");
  });

  it("a quick request is the command assistant.ask with the prompt", () => {
    const run = vi.fn(async () => undefined);
    askPrompt({ run }, "Дай сводку");
    expect(run).toHaveBeenCalledWith("assistant.ask", { prompt: "Дай сводку" });
  });
});

describe("connected to the registry and to the state of the window", () => {
  function app(state: Partial<AppState>, bound: boolean): App {
    const registry = new CommandRegistry();
    registry.register([{ id: "assistant.ask", title: "Спросить Claude", description: "d", params: { prompt: { type: "string" } }, required: ["prompt"], silent: true, ...(bound ? { run: () => undefined } : { stage: "Э23" }) }]);
    registry.hooks.context = () => ({ ...EMPTY_CONTEXT, hasHole: state.holeId != null });
    return { registry, store: new Store<AppState>({ ...INITIAL_STATE, ...state }), run: async () => undefined } as unknown as App;
  }
  const draw = (a: App, blocked: string | null = null): string => renderToStaticMarkup(createElement(AppContext.Provider, { value: a }, createElement(QuickPrompts, { blocked })));

  it("a chosen hole makes the third request available", () => {
    expect(draw(app({ holeId: "DH-012" }, true))).not.toContain("aria-disabled");
    expect(draw(app({ holeId: null }, true))).toContain(NO_HOLE_REASON);
  });

  it("the command that is not bound yet makes them all unavailable with its reason", () => {
    const out = draw(app({ holeId: "DH-012" }, false));
    expect(out.match(/aria-disabled="true"/g)).toHaveLength(3);
    expect(out).toContain("Функция появится на этапе Э23");
  });

  it("no internet: all three are unavailable with the words of the assistant", () => {
    const out = draw(app({ holeId: "DH-012" }, true), "Ассистент недоступен без интернета");
    expect(out.match(/aria-disabled="true"/g)).toHaveLength(3);
    expect(out).toContain("Ассистент недоступен без интернета");
  });
});
