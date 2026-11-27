/**
 * The right panel «Claude» (Э23, 23.14; ТЗ 7.32 «Панель чата», 7.0.3–7.0.5; ADR 0029 решение 33; docs/progress/e23-api.md §2, §7, §13): the head (the model and the
 * depth → the connection window, «Новый диалог», «История»), the feed (`MessageList`), the quick requests of an empty dialog, the input (`Composer`).
 * «История» turns the panel to the list of the dialogs (`HistoryList.tsx`, `panelViewStore`; 23.16) and back. The states: not connected («Подключить Claude» → `assistant.connect`), a login that goes on, no internet («Ассистент недоступен без интернета» — the
 * history is still read, nothing can be asked), a temporary dialog (the reason is `dialog.note` of the core). The buttons are commands of the registry
 * (`assistant.connect`, `assistant.new`, `assistant.history`, `assistant.ask`), their unavailability is shown with the reason, as everywhere.
 * `ChatView` draws from what is known (the tests); `ChatPanel` is it connected to the stores and to the registry. `renderToolActions` and `tail` are the
 * slots of 23.15 (the buttons of a card that waits for the window, the confirmation card after the last item).
 */
import { useEffect, useState, type ReactNode } from "react";
import { useApp } from "../../shell/AppContext";
import type { App } from "../../shell/app";
import { commandContext } from "../../shell/store";
import { useRegistryVersion, useStore } from "../../shell/useStore";
import { projectStore } from "../projects/state";
import { Composer } from "./Composer";
import { confirmStore } from "./confirm";
import { restoreLastDialog } from "./history";
import { HistoryPanel } from "./HistoryList";
import { MessageList } from "./MessageList";
import { askPrompt, QuickPromptsView, quickItems, type QuickItem } from "./QuickPrompts";
import type { ChatState } from "./reduce";
import { checkConnection, chatStore, panelViewStore, refreshStatus, statusStore } from "./state";
import type { AssistantStatus, DialogHead, ToolItem } from "./wireTypes";
import css from "./ChatPanel.module.css";

export const OFFLINE_TEXT = "Ассистент недоступен без интернета";
export const NOT_CONNECTED_TEXT = "Claude не подключён";
export const LOGIN_TEXT = "Идёт вход в аккаунт Anthropic";
export const CORE_UNREACHABLE_TEXT = "Нет связи с ядром";

export type PanelMode = "loading" | "unreachable" | "none" | "login" | "offline" | "ready";

export interface PanelState {
  mode: PanelMode;
  /** why a question cannot be sent now; null — it can */
  blocked: string | null;
}

/**
 * The state of the panel from the status of the connection (`GET /status`); `online: null` — no call yet, the panel does not guess. «Нет интернета» (`online: false`)
 * does not block the input: the core learns the net is back only from a call, so a question is the way to find out (it answers `assistant_offline` itself), and the banner
 * has «Проверить связь».
 */
export function panelState(status: AssistantStatus | null, error: string | null): PanelState {
  if (!status) return error ? { mode: "unreachable", blocked: CORE_UNREACHABLE_TEXT } : { mode: "loading", blocked: "Проверка подключения…" };
  if (status.state === "none") return { mode: "none", blocked: NOT_CONNECTED_TEXT };
  if (status.state === "login") return { mode: "login", blocked: LOGIN_TEXT };
  if (status.online === false) return { mode: "offline", blocked: null };
  return { mode: "ready", blocked: null };
}

/** «Opus 5.5 · средняя» of the head; the ids stay when the lists of the status do not know them. */
export function modelLine(status: AssistantStatus | null): string {
  if (!status) return "Модель не определена";
  const model = status.models.find((m) => m.id === status.model)?.title ?? status.model;
  const effort = status.efforts.find((e) => e.id === status.effort)?.title ?? status.effort;
  return `${model} · ${effort}`;
}

/** `Вопросов: 3 · ≈ $0,0412`; the price of an unknown model is not 0 — «стоимость не определена». */
export function dialogMeta(head: DialogHead | null): string | null {
  if (!head || head.questions <= 0) return null;
  const cost = head.cost_usd === null ? "стоимость не определена" : `≈ $${head.cost_usd.toFixed(head.cost_usd < 1 ? 4 : 2).replace(".", ",")}`;
  return `Вопросов: ${head.questions} · ${cost}`;
}

export interface HeadReasons {
  connect: string | null;
  newDialog: string | null;
  history: string | null;
}

export interface ChatActions {
  connect: () => void;
  newDialog: () => void;
  history: () => void;
  quick: (prompt: string) => void;
  /** «Стоп» of the input: the command `assistant.stop` */
  stop: () => void;
  /** «Проверить связь» of the banner «нет интернета» */
  recheck: () => Promise<void>;
}

export interface ChatSlots {
  /** the buttons of a card of a command that waits for the window (23.15) */
  renderToolActions?: (item: ToolItem) => ReactNode;
  /** what follows the last item of the feed (the confirmation card, 23.15) */
  tail?: ReactNode;
}

export interface ChatViewProps extends ChatSlots {
  chat: ChatState;
  status: AssistantStatus | null;
  statusError: string | null;
  quick: QuickItem[];
  reasons: HeadReasons;
  actions: ChatActions;
}

function HeadButton({ label, reason, onClick, testId }: { label: string; reason: string | null; onClick: () => void; testId: string }) {
  return (
    <button
      type="button"
      className={css.headBtn}
      data-testid={testId}
      aria-disabled={reason ? true : undefined}
      title={reason ? `Недоступно: ${reason}` : label}
      onClick={() => {
        if (!reason) onClick();
      }}
    >
      {label}
    </button>
  );
}

function OfflineBanner({ recheck }: { recheck: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  return (
    <div className={css.offline} role="status" data-testid="state-offline">
      <div className={css.cardTitle}>{OFFLINE_TEXT}</div>
      <p className={css.dim}>Остальные функции программы работают. Прежние диалоги можно читать.</p>
      <button
        type="button"
        className={css.secondary}
        data-testid="recheck"
        disabled={busy}
        onClick={() => {
          setBusy(true);
          void recheck().finally(() => setBusy(false));
        }}
      >
        {busy ? "Проверка…" : "Проверить связь"}
      </button>
    </div>
  );
}

function Banners({ view, status, chat, actions, reasons, error }: { view: PanelState; status: AssistantStatus | null; chat: ChatState; actions: ChatActions; reasons: HeadReasons; error: string | null }) {
  const temporary = chat.dialog?.temporary === true;
  return (
    <>
      {view.mode === "none" && (
        <div className={css.card} data-testid="state-none">
          <div className={css.cardTitle}>{NOT_CONNECTED_TEXT}</div>
          <p className={css.text}>Подключите ключ API или войдите аккаунтом Anthropic Console — тогда можно задавать вопросы о проекте и просить программу выполнять команды.</p>
          {status?.subscription_note ? <p className={css.dim}>{status.subscription_note}</p> : null}
          <button type="button" className={css.primary} data-testid="connect" aria-disabled={reasons.connect ? true : undefined} title={reasons.connect ? `Недоступно: ${reasons.connect}` : undefined} onClick={() => !reasons.connect && actions.connect()}>
            Подключить Claude
          </button>
        </div>
      )}
      {view.mode === "login" && (
        <div className={css.card} data-testid="state-login">
          <div className={css.cardTitle}>{LOGIN_TEXT}</div>
          <p className={css.text}>Завершите вход в окне подключения: вставьте код из браузера.</p>
          <button type="button" className={css.secondary} data-testid="connect" aria-disabled={reasons.connect ? true : undefined} onClick={() => !reasons.connect && actions.connect()}>
            Окно подключения
          </button>
        </div>
      )}
      {view.mode === "offline" && <OfflineBanner recheck={actions.recheck} />}
      {view.mode === "unreachable" && (
        <div className={css.offline} role="alert" data-testid="state-unreachable">
          <div className={css.cardTitle}>{CORE_UNREACHABLE_TEXT}</div>
          {error ? <p className={css.dim}>{error}</p> : null}
        </div>
      )}
      {(temporary || chat.note) && (
        <div className={css.temporary} role="status" data-testid="state-temporary">
          {temporary ? <strong>Временный диалог. </strong> : null}
          {chat.note}
        </div>
      )}
    </>
  );
}

export function ChatView({ chat, status, statusError, quick, reasons, actions, renderToolActions, tail }: ChatViewProps) {
  const view = panelState(status, statusError);
  const empty = chat.items.length === 0;
  const meta = dialogMeta(chat.dialog);
  const running = chat.status === "running";
  return (
    <div className={css.panel} data-testid="chat-panel" data-mode={view.mode}>
      <div className={css.head}>
        <button
          type="button"
          className={css.model}
          data-testid="chat-model"
          aria-disabled={reasons.connect ? true : undefined}
          title={reasons.connect ? `Недоступно: ${reasons.connect}` : "Модель и глубина рассуждений — в окне подключения"}
          onClick={() => !reasons.connect && actions.connect()}
        >
          {modelLine(status)}
        </button>
        <span className={css.grow} />
        <HeadButton label="Новый диалог" reason={reasons.newDialog} onClick={actions.newDialog} testId="chat-new" />
        <HeadButton label="История" reason={reasons.history} onClick={actions.history} testId="chat-history" />
      </div>
      {meta ? (
        <div className={css.meta} data-testid="chat-meta">
          {meta}
        </div>
      ) : null}
      <div className={css.body}>
        <Banners view={view} status={status} chat={chat} actions={actions} reasons={reasons} error={statusError} />
        {empty ? (
          view.mode === "ready" || view.mode === "offline" ? (
            <div className={css.empty} data-testid="chat-empty">
              <p className={css.text}>Спросите о данных проекта или попросите выполнить команду: «Покажи скважину DH-012 на разрезе».</p>
              <QuickPromptsView items={quick} onPick={actions.quick} />
            </div>
          ) : null
        ) : (
          <MessageList items={chat.items} status={chat.status} thinking={chat.thinking} renderToolActions={renderToolActions} tail={tail} />
        )}
      </div>
      <Composer running={running} blocked={view.blocked} onStop={actions.stop} />
    </div>
  );
}

/** The buttons of the panel are commands of the registry (the log, the hotkeys and the availability are the registry's). */
export const chatActions = (app: Pick<App, "run">): ChatActions => ({
  connect: () => void app.run("assistant.connect"),
  newDialog: () => void app.run("assistant.new"),
  history: () => void app.run("assistant.history"),
  quick: (prompt) => askPrompt(app, prompt),
  stop: () => void app.run("assistant.stop"),
  recheck: checkConnection,
});

/** The panel in the right dock: `<ChatPanel />`. */
export function ChatPanel(slots: ChatSlots) {
  const app = useApp();
  useRegistryVersion(app.registry);
  const chat = useStore(chatStore, (s) => s);
  const st = useStore(statusStore, (s) => s);
  const state = useStore(app.store, (s) => s);
  const ctx = commandContext(state);
  const panelView = useStore(panelViewStore, (s) => s.view);
  const projectUid = useStore(projectStore, (s) => s.current?.uid ?? null);
  useStore(confirmStore, (s) => s.queue.length); // «История» is unavailable while a confirmation waits
  // the status is read when the panel is shown and whenever the core comes up (a read that failed while it started must not stay «не подключён»)
  useEffect(() => {
    if (state.core === "online" || statusStore.get().status === null) void refreshStatus();
  }, [state.core]);
  // the last dialog of the project is opened when the panel is shown (after a restart too); `history.ts` does it once per project and never over a dialog already started
  useEffect(() => {
    if (state.core === "online") void restoreLastDialog(projectUid);
  }, [state.core, projectUid]);
  const view = panelState(st.status, st.error);
  const blocked = view.blocked ?? (state.core === "online" ? null : "Ядро не запущено");
  const quick = quickItems(ctx, app.registry.reason("assistant.ask", ctx), blocked);
  const reasons: HeadReasons = {
    connect: app.registry.reason("assistant.connect", ctx),
    newDialog: app.registry.reason("assistant.new", ctx),
    history: app.registry.reason("assistant.history", ctx),
  };
  if (panelView === "history") return <HistoryPanel />;
  return <ChatView chat={chat} status={st.status} statusError={st.error} quick={quick} reasons={reasons} actions={chatActions(app)} {...slots} />;
}
