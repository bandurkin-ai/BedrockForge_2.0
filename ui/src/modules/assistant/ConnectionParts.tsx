/**
 * The sections «Профиль» and «Вход» of the window «Claude: подключение» (Э23, 23.17; ТЗ 7.32; ADR 0029 решения 9–10, 35; docs/progress/e23-api.md §3) and the
 * actions behind them. The views are pure (props → markup); the actions are plain async functions with their calls injected, so the window and the tests
 * run the same code.
 *
 * The secret: the key lives in the field only until it is saved. A refusal of the core (a wrong key) leaves the field as it is, with the text of the core
 * under it; a saved key clears the field, and the window shows only the mask of the status (`sk-ant-…a1B2`). Neither the key nor the code of a login is
 * written into any log or state outside the field.
 */
import type { ReactNode } from "react";
import { bridge } from "../../bridge";
import { CoreError } from "../../shell/api";
import { disconnect, loginFinish, loginStart, saveKey } from "./api";
import { allowedLink } from "./markdown";
import type { AssistantStatus, EffortInfo, TestResult } from "./wireTypes";
import css from "./Connection.module.css";

// ---- refusals ----

export interface Failure {
  code: string;
  message: string;
  hint: string;
}

/** The refusal of the core as it is (`{code, message, hint}`, Russian); anything else — a general text. */
export function failureOf(e: unknown): Failure {
  if (e instanceof CoreError) return { code: e.code, message: e.message, hint: e.hint };
  return { code: "error", message: e instanceof Error ? e.message : String(e), hint: "" };
}

export function ErrorLine({ failure, testId }: { failure: Failure | null; testId?: string }) {
  if (!failure) return null;
  return (
    <p className={css.error} role="alert" data-testid={testId ?? "connection-error"} data-code={failure.code}>
      {failure.message}
      {failure.hint ? <span className={css.errorHint}> {failure.hint}</span> : null}
    </p>
  );
}

// ---- the shell bridge (`window.bf.claude*`, 23.12): it may be missing (a browser, an old shell) ----

export interface ClaudeBridge {
  claudeSeal?: () => Promise<{ ok: boolean; message?: string }>;
  claudeForget?: () => Promise<void>;
  claudeStorage?: () => { secure: boolean; backend: string };
}

export const claudeBridge = (): ClaudeBridge | null => bridge() as unknown as ClaudeBridge | null;

export const INSECURE_TEXT =
  "Хранилище ключей Windows недоступно: подключение действует до закрытия программы. Чтобы не вводить ключ каждый раз, задайте переменную окружения ANTHROPIC_API_KEY.";

/** Whether the shell keeps the secret on the disk: the status of the core and the bridge both agree; unknown — yes (no false alarm). */
export function storageSecure(status: AssistantStatus | null, b: ClaudeBridge | null = claudeBridge()): boolean {
  if (status && status.secure_storage === false) return false;
  try {
    return b?.claudeStorage?.().secure !== false;
  } catch {
    return true;
  }
}

/** A new connection is kept on the disk by the main process (`claudeSeal`); the text to show when it did not, or null. */
export async function sealIfNeeded(status: AssistantStatus | null, b: ClaudeBridge | null = claudeBridge()): Promise<string | null> {
  if (!status?.seal_needed || !b?.claudeSeal) return null;
  try {
    const r = await b.claudeSeal();
    return r.ok ? null : (r.message ?? INSECURE_TEXT);
  } catch {
    return INSECURE_TEXT;
  }
}

// ---- the actions ----

export type KeyResult = { ok: true; status: AssistantStatus } | { ok: false; failure: Failure };

/** «Проверить и сохранить»: the core checks the key before it keeps it; on a refusal nothing is kept. */
export async function submitKey(value: string, save: (key: string) => Promise<AssistantStatus> = saveKey): Promise<KeyResult> {
  const key = value.trim();
  if (!key) return { ok: false, failure: { code: "key_empty", message: "Введите ключ.", hint: "Ключ вида sk-ant-… — в console.anthropic.com → API Keys." } };
  try {
    return { ok: true, status: await save(key) };
  } catch (e) {
    return { ok: false, failure: failureOf(e) };
  }
}

export type LoginView = { phase: "idle" } | { phase: "starting" } | { phase: "waiting"; id: string; url: string; expiresAt: string };
export type LoginResult = { ok: true; login: Extract<LoginView, { phase: "waiting" }> } | { ok: false; failure: Failure };

export async function beginLogin(start: typeof loginStart = loginStart): Promise<LoginResult> {
  try {
    const r = await start();
    return { ok: true, login: { phase: "waiting", id: r.login_id, url: r.url, expiresAt: r.expires_at } };
  } catch (e) {
    return { ok: false, failure: failureOf(e) };
  }
}

/** «Подтвердить»: a wrong code keeps the login open (the user may retry); an expired login ends it (`assistant_login_gone`). */
export async function submitCode(id: string, value: string, finish: typeof loginFinish = loginFinish): Promise<KeyResult> {
  const code = value.trim();
  if (!code) return { ok: false, failure: { code: "code_empty", message: "Вставьте код со страницы входа.", hint: "Страница Anthropic показывает его после входа." } };
  try {
    return { ok: true, status: await finish(id, code) };
  } catch (e) {
    return { ok: false, failure: failureOf(e) };
  }
}

/** «Выйти»: the core drops the secret, then the main process drops its encrypted copy. */
export async function signOut(drop: typeof disconnect = disconnect, b: ClaudeBridge | null = claudeBridge()): Promise<AssistantStatus> {
  const status = await drop();
  try {
    await b?.claudeForget?.();
  } catch {
    // the copy of the shell is removed at the next start of the core anyway (no secret is left in the core)
  }
  return status;
}

// ---- the views ----

export type ConnectionSection = "profile" | "login" | "model" | "effort" | "usage";

export function Section({ id, title, focus, children }: { id: ConnectionSection; title: string; focus?: boolean; children: ReactNode }) {
  return (
    <section id={`claude-${id}`} className={css.section} data-testid={`section-${id}`} data-focus={focus ? "true" : undefined} aria-label={title}>
      <h3 className={css.title}>{title}</h3>
      {children}
    </section>
  );
}

const METHOD_TEXT = { account: "Аккаунт Anthropic", api_key: "API-ключ", env: "Ключ из переменной окружения ANTHROPIC_API_KEY" } as const;

export function methodText(status: AssistantStatus): string {
  if (status.state === "login") return "Идёт вход аккаунтом…";
  return status.kind ? METHOD_TEXT[status.kind] : "Не подключён";
}

function connectionText(status: AssistantStatus): { text: string; tone: string | undefined } {
  if (status.state !== "connected") return { text: "—", tone: undefined };
  if (status.online === true) return { text: "Есть", tone: css.ok };
  if (status.online === false) return { text: "Нет связи с Anthropic", tone: css.bad };
  return { text: "Ещё не проверялась", tone: undefined };
}

/** «Связь есть · 412 мс» and what the check found out about the model and the depth. */
export function testLines(r: TestResult, efforts: EffortInfo[]): string[] {
  const lines = [`${r.ok ? "Связь есть" : "Связи нет"} · ${r.latency_ms} мс`];
  if (r.ok && !r.model_available) lines.push(`Модель ${r.model} недоступна для этого подключения: выберите другую в разделе «Модель».`);
  if (r.effort_supported) {
    const names = r.effort_supported.map((id) => efforts.find((e) => e.id === id)?.title ?? id);
    lines.push(`Глубина рассуждений, которую поддерживает модель: ${names.join(", ") || "—"}.`);
  }
  if (r.message) lines.push(r.message);
  return lines;
}

export interface ProfileProps {
  status: AssistantStatus;
  busy: boolean;
  test: TestResult | null;
  failure: Failure | null;
  notice: string | null;
  onTest: () => void;
  onLogout: () => void;
}

export function ProfileSection({ status, busy, test, failure, notice, onTest, onLogout }: ProfileProps) {
  const connected = status.state === "connected";
  const org = status.organization ? (status.organization.name ?? status.organization.id) : "—";
  const ws = status.workspace ? (status.workspace.name ?? status.workspace.id) : "—";
  const link = connectionText(status);
  const noOut = status.kind === "env" ? "Ключ задан переменной окружения ANTHROPIC_API_KEY: чтобы отключить Claude, уберите переменную и перезапустите программу." : null;
  return (
    <>
      <div className={css.rows} data-testid="profile">
        <span className={css.label}>Способ</span>
        <span className={css.value} data-testid="profile-method">
          {methodText(status)}
        </span>
        {status.mask ? (
          <>
            <span className={css.label}>Ключ</span>
            <span className={`${css.value} ${css.mask}`} data-testid="profile-mask">
              {status.mask}
            </span>
          </>
        ) : null}
        <span className={css.label}>Организация</span>
        <span className={css.value}>{org}</span>
        <span className={css.label}>Рабочая область</span>
        <span className={css.value}>{ws}</span>
        <span className={css.label}>Связь</span>
        <span className={`${css.value} ${link.tone ?? ""}`} data-testid="profile-link">
          {link.text}
        </span>
      </div>
      <div className={css.buttons}>
        <button type="button" className={css.btn} data-testid="connection-test" aria-disabled={!connected || busy ? true : undefined} title={connected ? undefined : "Недоступно: Claude не подключён"} onClick={() => connected && !busy && onTest()}>
          Проверить связь
        </button>
        <button type="button" className={css.btn} data-testid="connection-logout" aria-disabled={!connected || busy || status.kind === "env" ? true : undefined} title={noOut ? `Недоступно: ${noOut}` : connected ? undefined : "Недоступно: Claude не подключён"} onClick={() => connected && !busy && status.kind !== "env" && onLogout()}>
          Выйти
        </button>
      </div>
      {noOut ? <p className={css.hint}>{noOut}</p> : null}
      {test ? (
        <div className={test.ok ? css.note : css.warn} data-testid="connection-test-result">
          {testLines(test, status.efforts).map((l) => (
            <div key={l}>{l}</div>
          ))}
        </div>
      ) : null}
      <ErrorLine failure={failure} testId="profile-error" />
      {notice ? (
        <p className={css.warn} role="status" data-testid="connection-notice">
          {notice}
        </p>
      ) : null}
    </>
  );
}

export interface KeyProps {
  status: AssistantStatus;
  value: string;
  busy: boolean;
  failure: Failure | null;
  onChange: (v: string) => void;
  onSubmit: () => void;
}

/** The key: a masked field (`type="password"`); a saved key is shown as the mask of the status only. */
export function KeyForm({ status, value, busy, failure, onChange, onSubmit }: KeyProps) {
  const saved = status.kind === "api_key" && status.mask;
  return (
    <div className={css.field} data-testid="key-form">
      <h4 className={css.sub}>API-ключ</h4>
      <p className={css.hint}>
        Ключ вида <code>sk-ant-…</code> — в <a href="https://console.anthropic.com" rel="noopener noreferrer">console.anthropic.com</a> → API Keys. Он проверяется до сохранения и не показывается целиком: в окне остаётся только маска.
      </p>
      {saved ? (
        <p className={css.hint} data-testid="key-saved">
          Сохранён ключ: <span className={css.mask}>{status.mask}</span>. Новый ключ заменит его.
        </p>
      ) : null}
      <div className={css.fieldRow}>
        <input
          type="password"
          className={css.input}
          data-testid="key-input"
          data-bad={failure ? "true" : undefined}
          autoComplete="off"
          spellCheck={false}
          placeholder={saved ? (status.mask ?? "") : "sk-ant-…"}
          aria-label="API-ключ Anthropic"
          aria-invalid={failure ? true : undefined}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !busy) onSubmit();
          }}
        />
        <button type="button" className={css.primary} data-testid="key-save" aria-disabled={busy || !value.trim() ? true : undefined} title={value.trim() ? undefined : "Недоступно: введите ключ"} onClick={() => !busy && value.trim() && onSubmit()}>
          {busy ? "Проверка…" : "Проверить и сохранить"}
        </button>
      </div>
      <ErrorLine failure={failure} testId="key-error" />
    </div>
  );
}

export interface AccountProps {
  status: AssistantStatus;
  login: LoginView;
  code: string;
  busy: boolean;
  failure: Failure | null;
  onStart: () => void;
  onCode: (v: string) => void;
  onFinish: () => void;
  onCancel: () => void;
  onCopy: (url: string) => void;
}

export const NO_ANT_TEXT = "Вход аккаунтом недоступен в этой сборке. Введите API-ключ.";

/** The account: «Войти через Anthropic» → the address (opened by the shell in the system browser: a link, never `window.open`) → the code → «Подтвердить». */
export function AccountForm({ status, login, code, busy, failure, onStart, onCode, onFinish, onCancel, onCopy }: AccountProps) {
  const available = status.ant_available;
  const open = login.phase === "waiting" ? allowedLink(login.url) : null;
  return (
    <div className={css.field} data-testid="account-form">
      <h4 className={css.sub}>Аккаунт Anthropic</h4>
      <p className={css.hint}>Вход Anthropic Console откроется в системном браузере; код со страницы входа вставляется сюда. Доступ обновляется сам.</p>
      {!available ? (
        <p className={css.warn} data-testid="account-unavailable">
          {NO_ANT_TEXT}
        </p>
      ) : null}
      {login.phase === "waiting" ? (
        <>
          <div className={css.url} data-testid="login-url">
            {login.url}
          </div>
          <div className={css.buttons}>
            {open ? (
              <a className={css.linkBtn} href={open} rel="noopener noreferrer" data-testid="login-open">
                Открыть в браузере
              </a>
            ) : (
              <span className={css.linkBtn} aria-disabled="true" title="Недоступно: адрес не из списка разрешённых, скопируйте его вручную" data-testid="login-open">
                Открыть в браузере
              </span>
            )}
            <button type="button" className={css.btn} data-testid="login-copy" onClick={() => onCopy(login.url)}>
              Скопировать адрес
            </button>
          </div>
          <div className={css.fieldRow}>
            <input
              type="password"
              className={css.input}
              data-testid="login-code"
              data-bad={failure ? "true" : undefined}
              autoComplete="off"
              spellCheck={false}
              placeholder="Код со страницы входа"
              aria-label="Код со страницы входа"
              value={code}
              onChange={(e) => onCode(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !busy) onFinish();
              }}
            />
            <button type="button" className={css.primary} data-testid="login-finish" aria-disabled={busy || !code.trim() ? true : undefined} title={code.trim() ? undefined : "Недоступно: вставьте код"} onClick={() => !busy && code.trim() && onFinish()}>
              {busy ? "Проверка…" : "Подтвердить"}
            </button>
            <button type="button" className={css.btn} data-testid="login-cancel" onClick={onCancel}>
              Отмена
            </button>
          </div>
          <p className={css.hint}>Код действует 10 минут.</p>
        </>
      ) : (
        <div className={css.buttons}>
          <button type="button" className={css.primary} data-testid="login-start" aria-disabled={!available || login.phase === "starting" || busy ? true : undefined} title={available ? undefined : `Недоступно: ${NO_ANT_TEXT}`} onClick={() => available && login.phase === "idle" && !busy && onStart()}>
            {login.phase === "starting" ? "Запуск входа…" : "Войти через Anthropic"}
          </button>
        </div>
      )}
      <ErrorLine failure={failure} testId="login-error" />
    </div>
  );
}

export function SubscriptionNote({ status }: { status: AssistantStatus }) {
  return (
    <p className={css.note} data-testid="subscription-note">
      {status.subscription_note || "Подписка Claude (Pro, Max, Team) панелью не используется: нужен ключ API или вход аккаунтом Anthropic Console."}
    </p>
  );
}
