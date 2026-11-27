// The link «Claude: …» of the status bar and the mark of the account in the title bar (Э23, 23.17; ТЗ 7.0.3, 7.0.5; docs/progress/e23-api.md §13): the text for every state
// of the connection, the colour of the dot (`--ok` / `--warn` / `--text-faint`), the click opens the window.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { AccountBadge } from "../../src/modules/assistant/AccountBadge";
import { statusLabel, StatusLink, statusTone } from "../../src/modules/assistant/StatusLink";
import { statusStore } from "../../src/modules/assistant/state";
import type { AssistantStatus } from "../../src/modules/assistant/wireTypes";

// `useSyncExternalStore` of the shell hooks has no server snapshot: the string renderer reads the store once instead
vi.mock("../../src/shell/useStore", () => ({
  useStore: (store: { get: () => unknown }, select: (s: unknown) => unknown) => select(store.get()),
  useRegistryVersion: () => 0,
}));

const STATUS = (over: Partial<AssistantStatus> = {}): AssistantStatus => ({
  state: "connected",
  kind: "api_key",
  mask: "sk-ant-…a1B2",
  organization: null,
  workspace: null,
  online: true,
  checked_at: null,
  model: "claude-opus-5-5",
  model_source: "default",
  effort: "medium",
  effort_source: "default",
  ant_available: true,
  secure_storage: true,
  seal_needed: false,
  subscription_note: "",
  models: [
    { id: "claude-opus-5-5", title: "Opus 5.5", default: true, note: "", price: { input: 4, output: 20, cache_read: 0.2, cache_write: 5 }, long: null },
    { id: "claude-sonnet-5-5", title: "Sonnet 5.5", note: "", price: { input: 2, output: 10, cache_read: 0.1, cache_write: 2.5 }, long: null },
  ],
  efforts: [{ id: "medium", title: "Обычно", default: true }],
  prices_version: "2026-11",
  ...over,
});

describe("the text of the link (§13)", () => {
  it("every state of the connection", () => {
    expect(statusLabel(STATUS({ state: "none", kind: null, mask: null, online: null }))).toBe("Claude: не подключён");
    expect(statusLabel(STATUS({ state: "login", kind: null, mask: null }))).toBe("Claude: вход…");
    expect(statusLabel(STATUS({ kind: "account", mask: null }))).toBe("Claude: аккаунт · Opus 5.5");
    expect(statusLabel(STATUS({ kind: "api_key" }))).toBe("Claude: ключ · Opus 5.5");
    expect(statusLabel(STATUS({ kind: "env", mask: null }))).toBe("Claude: подключён · Opus 5.5");
  });

  it("the model of the status (another model, an id the list does not know)", () => {
    expect(statusLabel(STATUS({ model: "claude-sonnet-5-5" }))).toBe("Claude: ключ · Sonnet 5.5");
    expect(statusLabel(STATUS({ model: "claude-x-9" }))).toBe("Claude: ключ · claude-x-9");
  });

  it("no connection to Anthropic adds « · нет связи»; «ещё не проверялась» (null) does not", () => {
    expect(statusLabel(STATUS({ kind: "account", mask: null, online: false }))).toBe("Claude: аккаунт · Opus 5.5 · нет связи");
    expect(statusLabel(STATUS({ kind: "env", mask: null, online: false }))).toBe("Claude: подключён · Opus 5.5 · нет связи");
    expect(statusLabel(STATUS({ online: null }))).toBe("Claude: ключ · Opus 5.5");
  });

  it("the status is not known yet: «не подключён»", () => {
    expect(statusLabel(null)).toBe("Claude: не подключён");
  });
});

describe("the colour of the dot (§13)", () => {
  it("--ok: connected and the net is not known to be down; --warn: a login or no connection; --text-faint: not connected", () => {
    expect(statusTone(STATUS())).toBe("ok");
    expect(statusTone(STATUS({ online: null }))).toBe("ok");
    expect(statusTone(STATUS({ online: false }))).toBe("warn");
    expect(statusTone(STATUS({ state: "login", kind: null }))).toBe("warn");
    expect(statusTone(STATUS({ state: "none", kind: null, online: null }))).toBe("faint");
    expect(statusTone(null)).toBe("faint");
  });
});

describe("the link and the mark on the screen", () => {
  it("the link of the status bar shows the text and the tone", () => {
    statusStore.set({ status: STATUS({ kind: "account", mask: null, online: false }), error: null });
    const out = renderToStaticMarkup(createElement(StatusLink, { onOpen: () => undefined }));
    expect(out).toContain("Claude: аккаунт · Opus 5.5 · нет связи");
    expect(out).toContain('data-tone="warn"');
    expect(out).toContain('data-testid="claude-status"');
  });

  it("the mark of the title bar: the dot of the tone, the hint is the same text", () => {
    statusStore.set({ status: STATUS(), error: null });
    let out = renderToStaticMarkup(createElement(AccountBadge, { onOpen: () => undefined }));
    expect(out).toContain('data-tone="ok"');
    expect(out).toContain('title="Claude: ключ · Opus 5.5"');
    statusStore.set({ status: STATUS({ state: "none", kind: null, mask: null, online: null }), error: null });
    out = renderToStaticMarkup(createElement(AccountBadge, { onOpen: () => undefined }));
    expect(out).toContain('data-tone="faint"');
    expect(out).toContain('title="Claude: не подключён"');
    statusStore.set({ status: STATUS({ state: "login", kind: null, mask: null }), error: null });
    out = renderToStaticMarkup(createElement(AccountBadge, { onOpen: () => undefined }));
    expect(out).toContain('data-tone="warn"');
    expect(out).toContain('title="Claude: вход…"');
  });
});
