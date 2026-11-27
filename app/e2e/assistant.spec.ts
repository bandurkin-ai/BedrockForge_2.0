// Э23 acceptance on the real window (ТЗ 7.32, ADR 0029): the Claude assistant against the mock of the Anthropic API (nothing goes to the internet).
// The app is started with `BF_ASSISTANT_BASE_URL` of the mock, `BF_ANT_PATH` of the fake `ant` and `BF_E2E=1` (the test store of the key). One chain: one profile,
// the app is restarted inside the chain (the same `userData`) the way a user restarts it. The helpers — `assistantKit.ts`.
import { expect, test, type ElectronApplication, type Locator, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { close, coreApi, launch, openDemo, shot } from "./helpers";
import {
  ACCOUNT_TOKENS,
  LOGIN_CODE,
  TEST_KEY,
  WRONG_KEY,
  ask,
  answered,
  assistantEnv,
  chat,
  closeConnection,
  connectionWindow,
  filesWith,
  makeFakeAnt,
  money,
  newMock,
  num,
  online,
  openConnection,
  showChat,
  showLog,
  usage,
  walk,
  type MockAnthropic,
  type Usage,
} from "./assistantKit";

const userData = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-assistant-"));
const BIN = path.join(userData, "assistant", "connection.bin");
const OFFLINE_TEXT = "Ассистент недоступен без интернета";
/** The rows of the log of the commands made by Claude (the cell «Кто»; the title «Подключение Claude» is not one of them). */
const byClaude = (log: Locator) => log.getByRole("row").filter({ has: log.page().getByRole("cell", { name: "Claude", exact: true }) });
const SELECTOR = (page: Page) => page.getByTestId("project-selector");

let mock: MockAnthropic;
let env: Record<string, string>;
let app: ElectronApplication;
let page: Page;

async function start(): Promise<void> {
  ({ app, page } = await launch(env, userData));
  await online(page);
}

async function restart(): Promise<void> {
  await close(app);
  await start();
}

/** Every file of the profile (settings, logs of the shell and of the core, projects, the store of the assistant) except the test store of the key. */
const profileFiles = () => walk([userData]).filter((f) => f !== BIN);

test.describe.serial("Claude assistant: key, commands, confirmation, history, usage, offline, account, secret", () => {
  test.beforeAll(async () => {
    // all the scenarios of the spec in one list: the first step that fits answers (their questions differ)
    mock = await newMock("hole_section", "project_delete", "summary", "data_tools");
    await mock.start();
    env = assistantEnv(mock, makeFakeAnt(), { FAKE_ANT_CODE: LOGIN_CODE });
    await start();
  });

  test.afterAll(async () => {
    if (app) await close(app);
    await mock?.stop();
    fs.rmSync(userData, { recursive: true, force: true });
  });

  test("key: a wrong one is refused and not kept; the right one shows only its mask, is sealed to connection.bin, the status bar says «подключён»", async () => {
    test.setTimeout(120_000);
    await expect(page.getByTestId("claude-status")).toHaveText("Claude: не подключён");
    const win = await openConnection(page);
    await expect(win.getByTestId("profile-method")).toHaveText("Не подключён");
    await page.screenshot({ path: shot("assistant-1-none.png") });

    // a wrong key: the mock answers 401, the window shows the reason, nothing is kept
    await win.getByTestId("key-input").fill(WRONG_KEY);
    await win.getByTestId("key-save").click();
    const error = win.getByTestId("key-error");
    await expect(error).toBeVisible({ timeout: 30_000 });
    await expect(error).toHaveText(/[А-Яа-я]/); // the text is Russian (code, message, hint of the core)
    expect(await error.innerText()).not.toContain(WRONG_KEY);
    await expect(win.getByTestId("profile-method")).toHaveText("Не подключён");
    await expect(page.getByTestId("claude-status")).toHaveText("Claude: не подключён");
    const refused = (await mock.requests()).filter((r) => r.path.startsWith("/v1/models/"));
    expect(refused.length).toBeGreaterThan(0); // the key did go to the check ...
    expect(refused.every((r) => r.status === 401 && !r.auth.ok)).toBe(true); // ... and was refused
    expect(fs.existsSync(BIN)).toBe(false);
    expect((await coreApi<{ state: string }>(page, "GET", "/assistant/status")).body.state).toBe("none");
    await page.screenshot({ path: shot("assistant-1-wrong-key.png") });

    // the right key: the window keeps only the mask
    await win.getByTestId("key-input").fill(TEST_KEY);
    await win.getByTestId("key-save").click();
    const mask = win.getByTestId("profile-mask");
    await expect(mask).toBeVisible({ timeout: 30_000 });
    expect(await mask.innerText()).not.toBe(TEST_KEY);
    await expect(win.getByTestId("profile-method")).toHaveText("API-ключ");
    await expect(win.getByTestId("key-error")).toHaveCount(0);
    await expect(win.getByTestId("key-input")).toHaveValue("");
    const seen = [await win.innerText(), await page.locator("body").innerText(), await page.content(), JSON.stringify((await coreApi(page, "GET", "/assistant/status")).body)];
    for (const text of seen) expect(text).not.toContain(TEST_KEY);
    // the shell seals the key of the core to the profile (the test store under BF_E2E: no DPAPI on the runner)
    await expect.poll(() => fs.existsSync(BIN), { timeout: 30_000 }).toBe(true);
    // the status bar: «Claude: ключ · <модель>»
    await expect(page.getByTestId("claude-status")).toHaveText(/^Claude: ключ · \S+/);
    expect(await page.getByTestId("claude-status").getAttribute("data-tone")).toBe("ok");
    await page.screenshot({ path: shot("assistant-1-key.png") });
    await closeConnection(page);
  });

  test("«Покажи скважину DH-012 на разрезе» → hole.select and section.through_hole, both in the log as «Claude»", async () => {
    test.setTimeout(150_000);
    await openDemo(page, false);
    await showChat(page);
    const log = await showLog(page);
    await expect(chat(page).getByTestId("chat-empty")).toBeVisible();
    await ask(page, "Покажи скважину DH-012 на разрезе");
    await expect(chat(page).getByTestId("msg-user").filter({ hasText: "Покажи скважину DH-012 на разрезе" })).toBeVisible();
    await answered(page, "Готово: скважина DH-012 выбрана и показана на разрезе.");

    // the cards of the tools in the feed (both with a result), the text of the model before them
    const cards = chat(page).getByTestId("tool-card");
    await expect(cards).toHaveCount(2);
    expect(await cards.evaluateAll((els) => els.map((e) => e.getAttribute("data-name")))).toEqual(["ui_hole_select", "ui_section_through_hole"]);
    await expect(cards.first()).toContainText("Выбрать скважину");
    await expect(cards.nth(1)).toContainText("Разрез через скважину");
    await expect(chat(page).getByTestId("msg-assistant").first()).toContainText("Выбираю скважину и строю разрез.");
    for (let i = 0; i < 2; i++) expect(await cards.nth(i).getAttribute("data-status")).toBe("ok");

    // the log of the commands: two rows by «Claude» with the commands of the registry
    const mine = byClaude(log);
    await expect(mine).toHaveCount(2);
    await expect(mine.filter({ hasText: "Выбрать скважину" })).toContainText("DH-012");
    await expect(mine.filter({ hasText: "Разрез через скважину" })).toContainText("DH-012");
    expect(await log.getByRole("row").filter({ has: page.getByRole("cell", { name: "Вы", exact: true }) }).count()).toBeGreaterThan(0); // the demo project is the user's own command

    // the mock got two requests: the question and the results of both tools; the key is not in the bodies
    const messages = await mock.messages();
    expect(messages.map((m) => m.status)).toEqual([200, 200]);
    const last = JSON.stringify(messages[1].body.messages?.at(-1));
    expect(last).toContain("toolu_mock_select");
    expect(last).toContain("toolu_mock_section");
    expect(JSON.stringify(messages.map((m) => m.body))).not.toContain(TEST_KEY);
    await page.screenshot({ path: shot("assistant-2-hole-section.png") });
  });

  test("restart with the same profile: the last dialog of the project is open, the history shows its date, questions and cost", async () => {
    test.setTimeout(150_000);
    await restart();
    await expect(SELECTOR(page)).toContainText("Демо", { timeout: 60_000 }); // the last project opens at start
    // the key survived the restart through connection.bin (the shell hands it to the new core)
    await expect(page.getByTestId("claude-status")).toHaveText(/^Claude: ключ · \S+/, { timeout: 60_000 });
    await showChat(page);
    const feed = chat(page);
    await expect(feed.getByTestId("msg-user").filter({ hasText: "Покажи скважину DH-012 на разрезе" })).toBeVisible({ timeout: 60_000 });
    await expect(feed.getByTestId("msg-assistant").filter({ hasText: "Готово: скважина DH-012 выбрана и показана на разрезе." })).toBeVisible();
    await expect(feed.getByTestId("tool-card")).toHaveCount(2);

    const list = await coreApi<{ total: number; items: { title: string; questions: number; cost_usd: number | null; last_at: string; state: string }[] }>(page, "GET", "/assistant/dialogs");
    expect(list.status).toBe(200);
    expect(list.body.total).toBe(1);
    const head = list.body.items[0];
    expect(head.questions).toBe(1);
    expect(head.cost_usd).toBeGreaterThan(0);

    await page.getByTestId("chat-history").click();
    await expect(page.getByTestId("history-panel")).toBeVisible();
    const rows = page.getByTestId("history-row");
    await expect(rows).toHaveCount(1);
    await expect(rows.getByTestId("history-open")).toHaveText("Покажи скважину DH-012 на разрезе");
    await expect(rows.getByTestId("history-questions")).toHaveText("1 вопрос");
    await expect(rows.getByTestId("history-cost")).toHaveText(money(head.cost_usd));
    await expect(rows.getByTestId("history-when")).toHaveText(/\d{1,2}\D+\d{4}/); // «27 нояб. 2026 г., 10:30»
    await expect(rows.getByTestId("history-current")).toBeVisible();
    await page.screenshot({ path: shot("assistant-3-history.png") });
    await page.getByTestId("history-back").click();
    await expect(page.getByTestId("history-panel")).toHaveCount(0);
    await expect(feed.getByTestId("msg-user")).toHaveCount(1);
  });

  test("usage: the window sums the cost by models for today / 30 days / all time as the core does", async () => {
    test.setTimeout(150_000);
    const win = await openConnection(page);
    // a second model in the profile: Sonnet answers one more question
    await win.getByTestId("model-claude-sonnet-5-5").click();
    await expect(win.getByTestId("model-claude-sonnet-5-5")).toHaveAttribute("aria-checked", "true");
    await closeConnection(page);
    await ask(page, "Сводка по проекту");
    await answered(page, "В проекте «Демо»");
    const sonnet = (await mock.messages()).at(-1)!;
    expect(sonnet.body.model).toBe("claude-sonnet-5-5");

    const u: Usage = await usage(page);
    expect(u.all.requests).toBe(3); // two requests of the first question, one of the second
    expect(u.all.by_model.map((m) => m.model).sort()).toEqual(["claude-opus-5-5", "claude-sonnet-5-5"]);
    expect(u.all.by_model.map((m) => m.requests).sort()).toEqual([1, 2]);
    // the core counts what the mock received
    expect((await mock.messages()).filter((m) => m.status === 200)).toHaveLength(u.all.requests);
    for (const p of [u.today, u.days30, u.all]) {
      expect(p.cost_usd).toBeGreaterThan(0);
      expect(p.by_model.every((m) => m.cost_usd !== null && m.cost_usd > 0)).toBe(true);
      expect(p.by_model.reduce((s, m) => s + (m.cost_usd ?? 0), 0)).toBeCloseTo(p.cost_usd ?? 0, 9);
    }
    expect(u.today.requests).toBe(3);
    expect(u.days30.requests).toBe(3);

    await openConnection(page);
    const table = connectionWindow(page).getByTestId("usage-table");
    await connectionWindow(page).getByTestId("usage-refresh").click();
    for (const [id, p] of [["today", u.today], ["days30", u.days30], ["all", u.all]] as const) {
      const row = table.getByTestId(`usage-${id}`);
      await expect(row).toContainText(money(p.cost_usd));
      await expect(row).toContainText(num(p.tokens.input));
      await expect(row).toContainText(num(p.tokens.output));
      await expect(row).toContainText(num(p.tokens.cache_read));
      await expect(row).toContainText(num(p.tokens.cache_write));
      expect((await row.locator("td").nth(1).innerText()).trim()).toBe(num(p.requests));
      for (const m of p.by_model) {
        const mr = table.getByTestId(`usage-${id}-${m.model}`);
        await expect(mr).toContainText(m.title);
        await expect(mr).toContainText(money(m.cost_usd));
        expect((await mr.locator("td").nth(1).innerText()).trim()).toBe(num(m.requests));
      }
    }
    await connectionWindow(page).getByTestId("section-usage").scrollIntoViewIfNeeded();
    await page.screenshot({ path: shot("assistant-4-usage.png") });
    await closeConnection(page);
  });

  test("the secret: the test key is nowhere in the profile, the projects and the logs except the test store connection.bin", async () => {
    test.setTimeout(120_000);
    expect(fs.existsSync(BIN)).toBe(true);
    // the test store (BF_E2E, no DPAPI on Linux) keeps the secret as JSON behind a «BFTEST1» mark: the only file that may hold it
    expect(fs.readFileSync(BIN, "latin1").startsWith("BFTEST1\n")).toBe(true);
    expect(filesWith([userData], TEST_KEY)).toEqual([BIN]);
    expect(filesWith([userData], WRONG_KEY)).toEqual([]);
    const files = profileFiles();
    expect(files.some((f) => f.endsWith("shell.log")) && files.some((f) => f.endsWith("core.log"))).toBe(true); // the logs were part of the search
    expect(files.some((f) => f.includes(`${path.sep}assistant${path.sep}`) && f.endsWith("usage.jsonl"))).toBe(true);
    // the journal of the mock holds no key: the headers of the secrets are hidden by the mock, the bodies are checked
    expect(JSON.stringify((await mock.requests()).map((r) => r.body))).not.toContain(TEST_KEY);
  });

  test("offline: the mock is stopped → a question gets «Ассистент недоступен без интернета», the history is still read; the link comes back", async () => {
    test.setTimeout(150_000);
    await mock.stop();
    await ask(page, "Расскажи подробно про бурение");
    await expect(chat(page).getByTestId("state-offline")).toContainText(OFFLINE_TEXT, { timeout: 90_000 });
    await expect(chat(page).getByTestId("msg-user").filter({ hasText: "Расскажи подробно про бурение" })).toBeVisible();
    await expect(page.getByTestId("claude-status")).toContainText("нет связи");
    expect(await page.getByTestId("claude-status").getAttribute("data-tone")).toBe("warn");
    await expect(page.getByTestId("composer-input")).toBeEnabled(); // no internet does not lock the input: a question is the way to find out the net is back
    await page.screenshot({ path: shot("assistant-5-offline.png") });

    // the history is read without the network
    await page.getByTestId("chat-history").click();
    await expect(page.getByTestId("history-row")).toHaveCount(1);
    await expect(page.getByTestId("history-row").getByTestId("history-questions")).toContainText("вопрос");
    await page.getByTestId("history-back").click();

    // the network is back: «Проверить связь» of the banner finds it, the question can be asked again
    await mock.start();
    await chat(page).getByTestId("recheck").click();
    await expect(chat(page).getByTestId("state-offline")).toHaveCount(0, { timeout: 30_000 });
    await expect(page.getByTestId("claude-status")).not.toContainText("нет связи");
    const win = await openConnection(page);
    await win.getByTestId("connection-test").click();
    await expect(win.getByTestId("connection-test-result")).toContainText("Связь есть", { timeout: 30_000 });
    await expect(win.getByTestId("profile-link")).toHaveText("Есть");
    await closeConnection(page);
    await expect(page.getByTestId("claude-status")).not.toContainText("нет связи");
    await expect(chat(page).getByTestId("state-offline")).toHaveCount(0);
  });

  test("deleting the project at the request of Claude needs a card in the chat: «Отклонить» keeps it, «Разрешить» deletes it", async () => {
    test.setTimeout(150_000);
    await expect(SELECTOR(page)).toContainText("Демо");
    const log = await showLog(page);
    const before = (await mock.messages()).length;
    const errorsBefore = await chat(page).getByTestId("msg-error").count();

    await ask(page, "Удали проект");
    const card = chat(page).getByTestId("confirm-card");
    await expect(card).toBeVisible({ timeout: 45_000 });
    await expect(card.getByTestId("confirm-title")).toHaveText("Удалить проект");
    await expect(card.getByTestId("confirm-description")).toHaveText("Удалить текущий проект со всеми данными.");
    expect(await card.getAttribute("data-command")).toBe("project.delete");
    await expect(SELECTOR(page)).toContainText("Демо"); // nothing is deleted while the card waits
    await page.screenshot({ path: shot("assistant-6-confirm.png") });
    await card.getByTestId("confirm-deny").click();
    await expect(card).toHaveCount(0);
    await answered(page, "Хорошо, проект не удалён.");
    await expect(SELECTOR(page)).toContainText("Демо");
    expect(await coreApi<{ project: { name: string } | null }>(page, "GET", "/projects/current").then((r) => r.body.project?.name)).toContain("Демо");
    // Claude got the refusal as the result of the tool
    const declined = (await mock.messages()).slice(before);
    expect(declined).toHaveLength(2);
    const result = JSON.stringify(declined[1].body.messages?.at(-1));
    expect(result).toContain("Пользователь отклонил действие");
    expect(result).toContain("toolu_mock_delete");
    expect(result).toContain('"is_error":true');
    const refusedRow = byClaude(log).filter({ hasText: "Удалить проект" });
    await expect(refusedRow).toHaveCount(1); // a refusal is logged as a failed command; nothing was run
    await expect(refusedRow).toContainText("Пользователь отклонил действие");

    // the same request again, this time allowed
    await ask(page, "Удали проект");
    await expect(card).toBeVisible({ timeout: 45_000 });
    await card.getByTestId("confirm-allow").click();
    await expect(card).toHaveCount(0);
    await expect(SELECTOR(page)).toContainText("Проект не открыт", { timeout: 30_000 });
    await expect(page.getByTestId("composer-stop")).toHaveCount(0, { timeout: 45_000 });
    const rows = byClaude(log).filter({ hasText: "Удалить проект" });
    await expect(rows).toHaveCount(2); // the refused call and the allowed one
    await expect(rows.first()).toContainText("удалён"); // «Проект «Демо: участок Северный» удалён» (the newest row is the first)
    expect(fs.readdirSync(path.join(userData, "Проекты")).filter((n) => n.startsWith("Демо"))).toEqual([]);
    const cards = chat(page).getByTestId("tool-card").filter({ hasText: "Удалить проект" });
    await expect(cards.last()).toContainText("Проект «Демо: участок Северный» удалён");
    expect(await cards.last().getAttribute("data-status")).toBe("ok");
    // The dialog lived in the project that is gone (ADR 0029 решение 32), so the result of the tool is not sent: the chat says so instead of a raw «Диалог не найден».
    const notice = chat(page).getByTestId("msg-notice").last();
    await expect(notice).toHaveText("Диалог закрыт вместе с проектом: ответ Claude получить нельзя.");
    expect(await notice.getAttribute("data-code")).toBe("dialog_closed");
    await expect(chat(page).getByTestId("msg-error")).toHaveCount(errorsBefore); // no new error (the one of the offline test stays in the feed)
    expect(await chat(page).getByTestId("msg-error").evaluateAll((els) => els.map((e) => e.getAttribute("data-code")))).not.toContain("assistant_dialog_not_found");
    await expect(chat(page).getByTestId("msg-assistant").filter({ hasText: "Проект удалён." })).toHaveCount(0); // the mock's answer to the result never comes
    expect((await mock.messages()).slice(before + 2)).toHaveLength(1); // the allowed request reached Claude once; its result was not sent back
    await page.screenshot({ path: shot("assistant-6-deleted.png") });
  });

  test("account: «Войти через Anthropic» on the fake ant → the address, a wrong code is refused, the right one connects the account", async () => {
    test.setTimeout(150_000);
    expect(JSON.parse(fs.readFileSync(BIN, "latin1").slice("BFTEST1\n".length)).kind).toBe("api_key");
    const win = await openConnection(page);
    await expect(win.getByTestId("account-unavailable")).toHaveCount(0); // BF_ANT_PATH is set
    await win.getByTestId("login-start").click();
    const url = win.getByTestId("login-url");
    await expect(url).toBeVisible({ timeout: 30_000 });
    expect(await url.innerText()).toMatch(/^https:\/\/console\.anthropic\.com\//); // shown, never opened by the test
    await expect(win.getByTestId("login-open")).toHaveAttribute("href", /^https:\/\/console\.anthropic\.com\//);
    expect((await coreApi<{ state: string }>(page, "GET", "/assistant/status")).body.state).toBe("login");
    await expect(page.getByTestId("claude-status")).toHaveText("Claude: вход…");

    await win.getByTestId("login-code").fill("WRONG-CODE-000000");
    await win.getByTestId("login-finish").click();
    await expect(win.getByTestId("login-error")).toBeVisible({ timeout: 30_000 });
    await expect(win.getByTestId("login-error")).toHaveText(/[А-Яа-я]/);
    await page.screenshot({ path: shot("assistant-7-wrong-code.png") });
    // a wrong code ends that login: a new one is started
    await win.getByTestId("login-cancel").click();
    await win.getByTestId("login-start").click();
    await expect(win.getByTestId("login-url")).toBeVisible({ timeout: 30_000 });
    await win.getByTestId("login-code").fill(LOGIN_CODE);
    await win.getByTestId("login-finish").click();
    await expect(win.getByTestId("profile-method")).toHaveText("Аккаунт Anthropic", { timeout: 60_000 });
    await expect(win.getByTestId("profile-mask")).toHaveCount(0); // an account has no key to mask
    await expect(page.getByTestId("claude-status")).toHaveText(/^Claude: аккаунт · \S+/);
    // the shell seals the account in place of the key
    await expect.poll(() => (fs.existsSync(BIN) ? (JSON.parse(fs.readFileSync(BIN, "latin1").slice("BFTEST1\n".length)) as { kind: string }).kind : ""), { timeout: 30_000 }).toBe("account");
    await page.screenshot({ path: shot("assistant-7-account.png") });
    // the account works: a question goes with its token to the mock (a Bearer token of OAuth, not the key)
    await closeConnection(page);
    await page.getByTestId("chat-new").click(); // an empty feed: the answer below is a new one
    await expect(chat(page).getByTestId("chat-empty")).toBeVisible();
    await ask(page, "Сводка по проекту");
    await answered(page, "В проекте «Демо»");
    const sent = (await mock.messages()).at(-1)!;
    expect(sent.status).toBe(200);
    expect(sent.auth).toEqual({ kind: "bearer", ok: true });
    // the temporary folder of the login (the profile files of ant) is gone
    const tmp = path.join(userData, "local", "tmp");
    expect(fs.existsSync(tmp) ? fs.readdirSync(tmp).filter((n) => n.startsWith("ant-")) : []).toEqual([]);
  });

  test("the secret at the end: neither keys, nor the tokens of the account are in the files, the logs and the usage journal", async () => {
    test.setTimeout(120_000);
    await close(app); // the logs are flushed and the files are closed
    expect(filesWith([userData], TEST_KEY)).toEqual([]); // the key was replaced by the account in the test store
    expect(filesWith([userData], WRONG_KEY)).toEqual([]);
    for (const token of ACCOUNT_TOKENS) {
      const hits = filesWith([userData], token);
      expect(hits, `${token} outside connection.bin`).toEqual(hits.filter((f) => f === BIN));
    }
    const files = profileFiles();
    expect(files.some((f) => f.endsWith("shell.log"))).toBe(true);
    // a launch of the same profile after the test: the account is still there (the sealed store is read at start)
    await start();
    await expect(page.getByTestId("claude-status")).toHaveText(/^Claude: аккаунт · \S+/, { timeout: 60_000 });
  });
});
