// The window «Claude: подключение» (Э23, 23.17; ТЗ 7.32; ADR 0029 решение 35; docs/progress/e23-api.md §2–§3): the sections, the key that is never shown after it is saved,
// a wrong key (the text of the core, the field stays), the login by account, the models with their prices, the five levels of the depth, the text about the
// subscription. The view is pure (`ConnectionView`), the actions are plain functions with their calls injected: no jsdom, `renderToStaticMarkup` only.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { CoreError } from "../../src/shell/api";
import { ConnectionView, EMPTY_FORMS, type ConnectionActions, type ConnectionForms } from "../../src/modules/assistant/ConnectionWindow";
import { beginLogin, sealIfNeeded, signOut, storageSecure, submitCode, submitKey, testLines } from "../../src/modules/assistant/ConnectionParts";
import { fmtPrice, priceLine } from "../../src/modules/assistant/ModelParts";
import type { AssistantStatus, UsageReport } from "../../src/modules/assistant/wireTypes";

const ZERO = { input: 0, output: 0, cache_read: 0, cache_write: 0 };
const TEST_KEY = "sk-ant-test-0000000000000000000000";

const STATUS = (over: Partial<AssistantStatus> = {}): AssistantStatus => ({
  state: "connected",
  kind: "api_key",
  mask: "sk-ant-…a1B2",
  organization: { id: "org_1", name: "ООО Рудник" },
  workspace: { id: "wrkspc_1", name: "Геология" },
  online: true,
  checked_at: "2026-11-26T10:00:00Z",
  model: "claude-opus-5-5",
  model_source: "default",
  effort: "medium",
  effort_source: "default",
  ant_available: true,
  secure_storage: true,
  seal_needed: false,
  subscription_note: "Подписка Claude (Pro, Max, Team) панелью не используется: нужен ключ API или вход аккаунтом Anthropic Console.",
  models: [
    { id: "claude-opus-5-5", title: "Opus 5.5", default: true, note: "Самая сильная", price: { input: 4, output: 20, cache_read: 0.2, cache_write: 5 }, long: null },
    { id: "claude-sonnet-5-5", title: "Sonnet 5.5", note: "", price: { input: 2, output: 10, cache_read: 0.1, cache_write: 2.5 }, long: null },
    { id: "claude-haiku-5-5", title: "Haiku 5.5", note: "", price: { input: 0.1, output: 0.5, cache_read: 0.01, cache_write: 0.125 }, long: { threshold: 100000, input: 0.5, output: 2.5, cache_read: 0.05, cache_write: 0.625 } },
    { id: "claude-fable-5-2", title: "Fable 5.2", note: "", price: { input: 8, output: 40, cache_read: 0.2, cache_write: 10 }, long: null },
  ],
  efforts: [
    { id: "low", title: "Быстро" },
    { id: "medium", title: "Обычно", default: true },
    { id: "high", title: "Тщательно" },
    { id: "xhigh", title: "Очень тщательно" },
    { id: "max", title: "Максимум" },
  ],
  prices_version: "2026-11",
  ...over,
});

const NONE = (over: Partial<AssistantStatus> = {}) => STATUS({ state: "none", kind: null, mask: null, organization: null, workspace: null, online: null, ...over });

const PERIOD = { requests: 0, tokens: ZERO, cost_usd: null, by_model: [] };
const USAGE: UsageReport = { today: PERIOD, days30: PERIOD, all: PERIOD, prices_version: "2026-11", since: null };

const NO_ACTIONS: ConnectionActions = {
  onKey: () => undefined,
  onSaveKey: () => undefined,
  onTest: () => undefined,
  onLogout: () => undefined,
  onStartLogin: () => undefined,
  onCode: () => undefined,
  onFinishLogin: () => undefined,
  onCancelLogin: () => undefined,
  onCopy: () => undefined,
  onPickModel: () => undefined,
  onPickEffort: () => undefined,
  onRefreshUsage: () => undefined,
};

const draw = (status: AssistantStatus | null, forms: ConnectionForms = EMPTY_FORMS, over: { statusError?: string | null; section?: never } = {}) =>
  renderToStaticMarkup(createElement(ConnectionView, { status, statusError: over.statusError ?? null, usage: USAGE, usageError: null, forms, actions: NO_ACTIONS }));
const withForms = (p: Partial<ConnectionForms>): ConnectionForms => ({ ...EMPTY_FORMS, ...p });
const text = (html: string) => html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");

describe("the sections of the window (ТЗ 7.32)", () => {
  it("«Профиль», «Вход», «Модель», «Глубина рассуждений», «Расход» are all there", () => {
    const out = draw(STATUS());
    for (const id of ["profile", "login", "model", "effort", "usage"]) expect(out).toContain(`data-testid="section-${id}"`);
    const t = text(out);
    for (const title of ["Профиль", "Вход", "Модель", "Глубина рассуждений", "Расход"]) expect(t).toContain(title);
  });

  it("the profile: method, organization, workspace, mask, «Проверить связь», «Выйти»", () => {
    const out = draw(STATUS());
    const t = text(out);
    expect(t).toContain("API-ключ");
    expect(t).toContain("ООО Рудник");
    expect(t).toContain("Геология");
    expect(out).toContain("sk-ant-…a1B2");
    expect(t).toContain("Проверить связь");
    expect(t).toContain("Выйти");
    expect(draw(STATUS({ kind: "account", mask: null }))).toContain("Аккаунт Anthropic");
    expect(text(draw(STATUS({ kind: "env", mask: null })))).toContain("переменной окружения ANTHROPIC_API_KEY");
  });

  it("not connected: «Проверить связь» and «Выйти» say why they are off", () => {
    const out = draw(NONE());
    expect(out).toMatch(/data-testid="connection-test"[^>]*aria-disabled="true"[^>]*title="Недоступно: Claude не подключён"/);
    expect(out).toMatch(/data-testid="connection-logout"[^>]*aria-disabled="true"/);
  });

  it("without a status the window waits (or tells why it cannot)", () => {
    expect(draw(null)).toContain("Проверка подключения…");
    const bad = draw(null, EMPTY_FORMS, { statusError: "Ядро недоступно. Подождите перезапуска ядра." });
    expect(bad).toContain("Ядро недоступно");
    expect(bad).toContain('role="alert"');
  });

  it("the text about the subscription (Pro, Max, Team are not used)", () => {
    const t = text(draw(NONE()));
    expect(t).toContain("Подписка Claude (Pro, Max, Team) панелью не используется");
    // the window says it even when the core sent no text
    expect(text(draw(NONE({ subscription_note: "" })))).toContain("Подписка Claude (Pro, Max, Team) панелью не используется");
  });

  it("the result of «Проверить связь» and the levels the model does not support", () => {
    const test = { ok: true, latency_ms: 412, model: "claude-opus-5-5", model_available: true, effort_supported: ["low", "medium", "high"], organization_id: "org_1", message: "Связь с Anthropic в порядке." };
    const out = draw(STATUS(), withForms({ profile: { ...EMPTY_FORMS.profile, test } }));
    const t = text(out);
    expect(t).toContain("Связь есть · 412 мс");
    expect(t).toContain("Связь с Anthropic в порядке.");
    expect(t).toContain("Глубина рассуждений, которую поддерживает модель: Быстро, Обычно, Тщательно.");
    expect(text(draw(STATUS(), withForms({ profile: { ...EMPTY_FORMS.profile, test } })))).toContain("Выбранная модель не поддерживает: Очень тщательно, Максимум");
    expect(testLines({ ...test, ok: false, latency_ms: 0, message: "" }, STATUS().efforts)[0]).toBe("Связи нет · 0 мс");
  });
});

describe("the key (ТЗ 7.32: проверяется до сохранения, в окне — только маска)", () => {
  it("a wrong key: the text of the core under the field, the field is not cleared", () => {
    const failure = { code: "assistant_bad_key", message: "Ключ не принят Anthropic.", hint: "Проверьте ключ в console.anthropic.com → API Keys." };
    const out = draw(NONE(), withForms({ key: { value: TEST_KEY, busy: false, failure } }));
    expect(out).toContain('data-testid="key-error"');
    expect(out).toContain('data-code="assistant_bad_key"');
    expect(text(out)).toContain("Ключ не принят Anthropic.");
    expect(text(out)).toContain("Проверьте ключ в console.anthropic.com");
    // what the user typed stays in the (password) field for him to fix
    expect(out).toMatch(new RegExp(`data-testid="key-input"[^>]*value="${TEST_KEY}"`));
    expect(out).toContain('type="password"');
    expect(out).toContain('aria-invalid="true"');
  });

  it("submitKey: a refusal of the core is returned as it is and nothing is kept", async () => {
    const save = vi.fn(async () => {
      throw new CoreError(422, "assistant_bad_key", "Ключ не принят Anthropic.", "Проверьте ключ.");
    });
    const r = await submitKey(`  ${TEST_KEY} `, save);
    expect(save).toHaveBeenCalledWith(TEST_KEY);
    expect(r).toEqual({ ok: false, failure: { code: "assistant_bad_key", message: "Ключ не принят Anthropic.", hint: "Проверьте ключ." } });
  });

  it("submitKey: an empty field is not sent", async () => {
    const save = vi.fn();
    const r = await submitKey("   ", save as never);
    expect(save).not.toHaveBeenCalled();
    expect(r.ok).toBe(false);
  });

  it("a saved key: the window has the mask and never the key (neither in the field nor anywhere)", async () => {
    const saved = STATUS({ mask: "sk-ant-…0000" });
    const r = await submitKey(TEST_KEY, async () => saved);
    expect(r).toEqual({ ok: true, status: saved });
    // the window clears the field when `ok`: the markup of the saved state
    const out = draw(saved, withForms({ key: { value: "", busy: false, failure: null } }));
    expect(out).not.toContain(TEST_KEY);
    expect(out).not.toContain("sk-ant-test");
    expect(out).toContain("sk-ant-…0000");
    expect(out).toContain('data-testid="key-saved"');
    expect(out).toMatch(/data-testid="key-input"[^>]*placeholder="sk-ant-…0000"/);
    expect(JSON.stringify(saved)).not.toContain(TEST_KEY);
  });

  it("the hint names the console; the save button is off while the field is empty", () => {
    const out = draw(NONE());
    expect(text(out)).toContain("console.anthropic.com");
    expect(out).toMatch(/data-testid="key-save"[^>]*aria-disabled="true"/);
    expect(draw(NONE(), withForms({ key: { value: TEST_KEY, busy: false, failure: null } }))).not.toMatch(/data-testid="key-save"[^>]*aria-disabled/);
  });
});

describe("the login by account (ant)", () => {
  const waiting = { phase: "waiting" as const, id: "L1", url: "https://console.anthropic.com/oauth/authorize?code_challenge=abc", expiresAt: "2026-11-26T10:10:00Z" };

  it("«Войти через Anthropic» until the login starts; no `ant` — the text and the button is off", () => {
    expect(text(draw(NONE()))).toContain("Войти через Anthropic");
    const out = draw(NONE({ ant_available: false }));
    expect(text(out)).toContain("Вход аккаунтом недоступен в этой сборке. Введите API-ключ.");
    expect(out).toMatch(/data-testid="login-start"[^>]*aria-disabled="true"/);
  });

  it("the address, «Открыть в браузере» (a link of the shell, not window.open), the field of the code, «Подтвердить», «Отмена»", () => {
    const out = draw(NONE({ state: "login" }), withForms({ login: { view: waiting, code: "", busy: false, failure: null } }));
    expect(out).toContain("https://console.anthropic.com/oauth/authorize?code_challenge=abc");
    expect(out).toMatch(/<a [^>]*href="https:\/\/console\.anthropic\.com\/oauth\/authorize\?code_challenge=abc"[^>]*rel="noopener noreferrer"[^>]*data-testid="login-open"/);
    const t = text(out);
    for (const s of ["Открыть в браузере", "Скопировать адрес", "Подтвердить", "Отмена"]) expect(t).toContain(s);
    expect(out).toContain('data-testid="login-code"');
    expect(out).toMatch(/data-testid="login-finish"[^>]*aria-disabled="true"/);
    expect(out).not.toContain("window.open");
  });

  it("an address of a foreign host is not made a link", () => {
    const out = draw(NONE({ state: "login" }), withForms({ login: { view: { ...waiting, url: "https://evil.example/login" }, code: "", busy: false, failure: null } }));
    expect(out).not.toContain('href="https://evil.example');
    expect(out).toMatch(/data-testid="login-open"/);
    expect(out).toContain('aria-disabled="true"');
  });

  it("a wrong code: the text of the core, the field of the code stays", async () => {
    const finish = vi.fn(async () => {
      throw new CoreError(422, "assistant_login_code", "Код не подошёл.", "Скопируйте код со страницы входа ещё раз.");
    });
    const r = await submitCode("L1", " 4/abc ", finish);
    expect(finish).toHaveBeenCalledWith("L1", "4/abc");
    expect(r.ok).toBe(false);
    if (r.ok) return;
    const out = draw(NONE({ state: "login" }), withForms({ login: { view: waiting, code: "4/abc", busy: false, failure: r.failure } }));
    expect(text(out)).toContain("Код не подошёл.");
    expect(out).toMatch(/data-testid="login-code"[^>]*value="4\/abc"/);
  });

  it("beginLogin: the address of the core; a refusal is shown as it is", async () => {
    const ok = await beginLogin(async () => ({ login_id: "L2", url: "https://console.anthropic.com/x", expires_at: "2026-11-26T10:10:00Z" }));
    expect(ok).toEqual({ ok: true, login: { phase: "waiting", id: "L2", url: "https://console.anthropic.com/x", expiresAt: "2026-11-26T10:10:00Z" } });
    const bad = await beginLogin(async () => {
      throw new CoreError(501, "assistant_no_ant", "Вход аккаунтом недоступен в этой сборке.", "Введите API-ключ.");
    });
    expect(bad).toEqual({ ok: false, failure: { code: "assistant_no_ant", message: "Вход аккаунтом недоступен в этой сборке.", hint: "Введите API-ключ." } });
  });
});

describe("the models and the depth (ТЗ 7.32)", () => {
  it("4 models with the prices of the core: input / output / cache read", () => {
    const out = draw(STATUS());
    const t = text(out);
    for (const m of ["Opus 5.5", "Sonnet 5.5", "Haiku 5.5", "Fable 5.2"]) expect(t).toContain(m);
    expect(text(out.match(/data-testid="price-claude-opus-5-5">(.*?)<\/span>/)?.[1] ?? "")).toBe("вход $4 · выход $20 · чтение кеша $0,20 · запись кеша $5");
    expect(text(out.match(/data-testid="price-claude-haiku-5-5">(.*?)<\/span>/)?.[1] ?? "")).toContain("вход $0,10 · выход $0,50 · чтение кеша $0,01");
    expect(t).toContain("Запрос длиннее 100 тыс. токенов: вход $0,50 · выход $2,50 · чтение кеша $0,05");
    expect(out).toMatch(/data-testid="model-claude-opus-5-5"[^>]*aria-checked="true"|aria-checked="true"[^>]*data-testid="model-claude-opus-5-5"/);
    expect(fmtPrice(8)).toBe("8");
    expect(priceLine({ input: 2, output: 10, cache_read: 0.1, cache_write: 2.5 })).toBe("вход $2 · выход $10 · чтение кеша $0,10 · запись кеша $2,50");
  });

  it("five levels of the depth with their names; the chosen one is marked", () => {
    const out = draw(STATUS({ effort: "high" }));
    const t = text(out);
    for (const e of ["Быстро", "Обычно", "Тщательно", "Очень тщательно", "Максимум"]) expect(t).toContain(e);
    expect(out).toMatch(/aria-checked="true"[^>]*data-testid="effort-high"|data-testid="effort-high"[^>]*aria-checked="true"/);
    expect(out.match(/data-testid="effort-/g)).toHaveLength(5);
  });

  it("a choice goes through the callbacks of the window (the click of a model other than the chosen one)", () => {
    // the pick handlers are invoked by the radios; the chosen model does not call (nothing to change)
    const out = draw(STATUS({ model: "claude-sonnet-5-5" }));
    expect(out).toMatch(/aria-checked="true"[^>]*data-testid="model-claude-sonnet-5-5"|data-testid="model-claude-sonnet-5-5"[^>]*aria-checked="true"/);
  });

  it("the source of the value is said (the environment, the default)", () => {
    expect(text(draw(STATUS({ model_source: "env" })))).toContain("задано переменной окружения");
    expect(text(draw(STATUS()))).toContain("по умолчанию");
  });
});

describe("the shell: sealing, forgetting, secure storage", () => {
  it("a new connection is sealed by the main process; a refusal text is shown", async () => {
    const seal = vi.fn(async () => ({ ok: true }));
    expect(await sealIfNeeded(STATUS({ seal_needed: true }), { claudeSeal: seal })).toBeNull();
    expect(seal).toHaveBeenCalledTimes(1);
    expect(await sealIfNeeded(STATUS({ seal_needed: false }), { claudeSeal: seal })).toBeNull();
    expect(seal).toHaveBeenCalledTimes(1);
    expect(await sealIfNeeded(STATUS({ seal_needed: true }), { claudeSeal: async () => ({ ok: false, message: "Хранилище ключей ОС недоступно: задайте ANTHROPIC_API_KEY" }) })).toBe("Хранилище ключей ОС недоступно: задайте ANTHROPIC_API_KEY");
    // no bridge (a browser, an old shell) — nothing to do, nothing to say
    expect(await sealIfNeeded(STATUS({ seal_needed: true }), null)).toBeNull();
  });

  it("«Выйти»: the core first, then the copy of the shell", async () => {
    const order: string[] = [];
    const status = NONE();
    const out = await signOut(
      async () => {
        order.push("core");
        return status;
      },
      {
        claudeForget: async () => {
          order.push("shell");
        },
      },
    );
    expect(out).toBe(status);
    expect(order).toEqual(["core", "shell"]);
  });

  it("insecure storage is told in the window", () => {
    expect(storageSecure(STATUS({ secure_storage: false }), null)).toBe(false);
    expect(storageSecure(STATUS(), { claudeStorage: () => ({ secure: false, backend: "basic_text" }) })).toBe(false);
    expect(storageSecure(STATUS(), null)).toBe(true);
    expect(text(draw(STATUS({ secure_storage: false })))).toContain("Хранилище ключей Windows недоступно");
    expect(draw(STATUS())).not.toContain("insecure-note");
  });
});
