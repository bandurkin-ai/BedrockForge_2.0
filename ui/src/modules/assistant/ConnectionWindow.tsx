/**
 * The window «Claude: подключение» (Э23, 23.17; ТЗ 7.32; ADR 0029 решение 35; docs/progress/e23-api.md §2–§4): «Профиль» (способ, организация, рабочая область,
 * «Проверить связь», «Выйти»), «Вход» (ключ с маской, аккаунт Anthropic, текст о подписке), «Модель», «Глубина рассуждений», «Расход». The window is a plain
 * floating window of the shell (`kind: "claude-connection"`, one at a time); `section` scrolls to a part of it (the command `assistant.connect {section}`).
 *
 * `ConnectionView` is pure (state + callbacks → markup); `ConnectionWindow` keeps the state of the forms and calls the core (`api.ts`). The key is held
 * in the field only until it is saved; a login that is still open is cancelled when the window closes.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { bridge } from "../../bridge";
import { useStore } from "../../shell/useStore";
import { loginCancel, putPrefs, testConnection } from "./api";
import { AccountForm, beginLogin, failureOf, INSECURE_TEXT, KeyForm, ProfileSection, Section, sealIfNeeded, signOut, storageSecure, submitCode, submitKey, SubscriptionNote, type ConnectionSection, type Failure, type LoginView } from "./ConnectionParts";
import { EffortSection, ModelSection } from "./ModelParts";
import { refreshStatus, refreshUsage, startAssistantEvents, statusStore, usageStore } from "./state";
import { UsageTable } from "./UsageTable";
import type { AssistantStatus, TestResult, UsageReport } from "./wireTypes";
import css from "./Connection.module.css";

/** The state of the forms of the window (kept by `ConnectionWindow`, given to the view as it is). */
export interface ConnectionForms {
  key: { value: string; busy: boolean; failure: Failure | null };
  login: { view: LoginView; code: string; busy: boolean; failure: Failure | null };
  profile: { busy: boolean; test: TestResult | null; failure: Failure | null; notice: string | null };
  model: { busy: boolean; failure: Failure | null };
  effort: { busy: boolean; failure: Failure | null };
}

export const EMPTY_FORMS: ConnectionForms = {
  key: { value: "", busy: false, failure: null },
  login: { view: { phase: "idle" }, code: "", busy: false, failure: null },
  profile: { busy: false, test: null, failure: null, notice: null },
  model: { busy: false, failure: null },
  effort: { busy: false, failure: null },
};

export interface ConnectionActions {
  onKey: (v: string) => void;
  onSaveKey: () => void;
  onTest: () => void;
  onLogout: () => void;
  onStartLogin: () => void;
  onCode: (v: string) => void;
  onFinishLogin: () => void;
  onCancelLogin: () => void;
  onCopy: (url: string) => void;
  onPickModel: (id: string) => void;
  onPickEffort: (id: string) => void;
  onRefreshUsage: () => void;
}

export interface ConnectionViewProps {
  status: AssistantStatus | null;
  statusError: string | null;
  usage: UsageReport | null;
  usageError: string | null;
  forms: ConnectionForms;
  actions: ConnectionActions;
  section?: ConnectionSection;
}

export function ConnectionView({ status, statusError, usage, usageError, forms, actions, section }: ConnectionViewProps) {
  if (!status) {
    return (
      <div className={css.wrap} data-testid="connection-window">
        <p className={statusError ? css.error : css.hint} role={statusError ? "alert" : undefined} data-testid="connection-loading">
          {statusError ?? "Проверка подключения…"}
        </p>
      </div>
    );
  }
  const insecure = !storageSecure(status);
  return (
    <div className={css.wrap} data-testid="connection-window" data-state={status.state}>
      <Section id="profile" title="Профиль" focus={section === "profile"}>
        <ProfileSection status={status} busy={forms.profile.busy} test={forms.profile.test} failure={forms.profile.failure} notice={forms.profile.notice} onTest={actions.onTest} onLogout={actions.onLogout} />
      </Section>
      <Section id="login" title="Вход" focus={section === "login"}>
        <SubscriptionNote status={status} />
        {insecure ? (
          <p className={css.warn} data-testid="insecure-note">
            {INSECURE_TEXT}
          </p>
        ) : null}
        <KeyForm status={status} value={forms.key.value} busy={forms.key.busy} failure={forms.key.failure} onChange={actions.onKey} onSubmit={actions.onSaveKey} />
        <AccountForm
          status={status}
          login={forms.login.view}
          code={forms.login.code}
          busy={forms.login.busy}
          failure={forms.login.failure}
          onStart={actions.onStartLogin}
          onCode={actions.onCode}
          onFinish={actions.onFinishLogin}
          onCancel={actions.onCancelLogin}
          onCopy={actions.onCopy}
        />
      </Section>
      <Section id="model" title="Модель" focus={section === "model"}>
        <ModelSection status={status} busy={forms.model.busy} onPick={actions.onPickModel} />
        {forms.model.failure ? <p className={css.error} role="alert" data-testid="model-error">{forms.model.failure.message} {forms.model.failure.hint}</p> : null}
      </Section>
      <Section id="effort" title="Глубина рассуждений" focus={section === "effort"}>
        <EffortSection status={status} busy={forms.effort.busy} supported={forms.profile.test?.effort_supported ?? null} onPick={actions.onPickEffort} />
        {forms.effort.failure ? <p className={css.error} role="alert" data-testid="effort-error">{forms.effort.failure.message} {forms.effort.failure.hint}</p> : null}
      </Section>
      <Section id="usage" title="Расход" focus={section === "usage"}>
        <UsageTable usage={usage} error={usageError} />
        <div className={css.buttons}>
          <button type="button" className={css.btn} data-testid="usage-refresh" onClick={actions.onRefreshUsage}>
            Обновить
          </button>
        </div>
      </Section>
    </div>
  );
}

export function ConnectionWindow({ section }: { section?: ConnectionSection }) {
  const st = useStore(statusStore, (s) => s);
  const us = useStore(usageStore, (s) => s);
  const [forms, setForms] = useState<ConnectionForms>(EMPTY_FORMS);
  const loginId = useRef<string | null>(null);
  const patch = useCallback(<K extends keyof ConnectionForms>(k: K, p: Partial<ConnectionForms[K]>) => setForms((f) => ({ ...f, [k]: { ...f[k], ...p } })), []);

  useEffect(() => {
    void refreshStatus();
    void refreshUsage();
    const stop = startAssistantEvents();
    return () => {
      stop();
      if (loginId.current) void loginCancel(loginId.current).catch(() => undefined);
    };
  }, []);
  useEffect(() => {
    if (section) document.getElementById(`claude-${section}`)?.scrollIntoView?.({ block: "start" });
  }, [section]);
  // a new connection is kept on the disk by the main process (also when the event of the bus came first)
  useEffect(() => {
    const status = st.status;
    if (!status?.seal_needed) return;
    let live = true;
    void sealIfNeeded(status).then((n) => live && patch("profile", { notice: n }));
    return () => {
      live = false;
    };
  }, [st.status, patch]);

  /** A new status of the core replaces the old one (a connection that is new is then sealed by the effect above). */
  const apply = useCallback((status: AssistantStatus) => statusStore.set({ status, error: null }), []);

  const actions: ConnectionActions = {
    onKey: (v) => patch("key", { value: v }),
    onSaveKey: async () => {
      patch("key", { busy: true });
      const r = await submitKey(forms.key.value);
      if (r.ok) {
        // the key leaves the field; the window keeps only the mask of the status
        patch("key", { value: "", busy: false, failure: null });
        patch("profile", { test: null, failure: null });
        apply(r.status);
      } else patch("key", { busy: false, failure: r.failure });
    },
    onTest: async () => {
      patch("profile", { busy: true, failure: null });
      try {
        patch("profile", { test: await testConnection(), busy: false });
      } catch (e) {
        patch("profile", { test: null, busy: false, failure: failureOf(e) });
      }
      void refreshStatus();
    },
    onLogout: async () => {
      patch("profile", { busy: true, failure: null });
      try {
        const status = await signOut();
        patch("profile", { busy: false, test: null });
        apply(status);
      } catch (e) {
        patch("profile", { busy: false, failure: failureOf(e) });
      }
    },
    onStartLogin: async () => {
      patch("login", { view: { phase: "starting" }, failure: null, code: "" });
      const r = await beginLogin();
      if (r.ok) {
        loginId.current = r.login.id;
        patch("login", { view: r.login });
      } else patch("login", { view: { phase: "idle" }, failure: r.failure });
    },
    onCode: (v) => patch("login", { code: v }),
    onFinishLogin: async () => {
      const id = loginId.current;
      if (!id) return;
      patch("login", { busy: true });
      const r = await submitCode(id, forms.login.code);
      if (r.ok) {
        loginId.current = null;
        patch("login", { view: { phase: "idle" }, code: "", busy: false, failure: null });
        patch("profile", { test: null, failure: null });
        apply(r.status);
      } else if (r.failure.code === "assistant_login_gone") {
        loginId.current = null;
        patch("login", { view: { phase: "idle" }, code: "", busy: false, failure: r.failure });
      } else patch("login", { busy: false, failure: r.failure });
    },
    onCancelLogin: () => {
      const id = loginId.current;
      loginId.current = null;
      if (id) void loginCancel(id).catch(() => undefined);
      patch("login", { view: { phase: "idle" }, code: "", busy: false, failure: null });
      void refreshStatus();
    },
    onCopy: (url) => bridge()?.copyText(url),
    onPickModel: async (id) => {
      patch("model", { busy: true, failure: null });
      try {
        apply(await putPrefs({ model: id }));
        patch("model", { busy: false });
        patch("profile", { test: null });
      } catch (e) {
        patch("model", { busy: false, failure: failureOf(e) });
      }
    },
    onPickEffort: async (id) => {
      patch("effort", { busy: true, failure: null });
      try {
        apply(await putPrefs({ effort: id }));
        patch("effort", { busy: false });
      } catch (e) {
        patch("effort", { busy: false, failure: failureOf(e) });
      }
    },
    onRefreshUsage: () => void refreshUsage(),
  };

  return <ConnectionView status={st.status} statusError={st.error} usage={us.usage} usageError={us.error} forms={forms} actions={actions} section={section} />;
}

/** The window layer of the shell calls it for every window; only `kind: "claude-connection"` is drawn (the others — `null`). */
export const CONNECTION_WINDOW_KIND = "claude-connection";
export const CONNECTION_WINDOW_TITLE = "Claude: подключение";

export function renderConnectionWindow(w: { kind: string; props?: Record<string, unknown> }) {
  if (w.kind !== CONNECTION_WINDOW_KIND) return null;
  return <ConnectionWindow section={w.props?.section as ConnectionSection | undefined} />;
}
