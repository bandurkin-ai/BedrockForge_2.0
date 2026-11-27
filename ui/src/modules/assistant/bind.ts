/**
 * The assistant of the window joined to the application (Э23, 23.15; ADR 0029 решения 22, 26, 35): `bindAssistant(app, opts)` is called once by `createApp`. It
 *
 * - binds the handlers of the commands `assistant.*` (`commands.ts`) to the actions of the chat (`state.ts`) and to the windows of the assistant;
 * - replaces `registry.hooks.confirm` (the confirmation of Claude is a card of the chat, `confirm.ts`; the panel «Claude» is opened to show it);
 * - tells the state of the chat what a request carries: the context of the screen (`context.ts`), the tools of the registry (`commandTools` — a command added to the registry
 *   is a tool of Claude without other changes, ТЗ 7.2), the titles of the cards;
 * - makes `pending.ts` run the commands a turn ended with;
 * - starts the events of the core (`assistant.connection`, `assistant.dialogs`).
 *
 * The window «Claude: подключение» (23.17) is opened through `opts.openWindow`: until the application gives it, the command answers that the window is not ready. The history of the
 * dialogs (23.16) is a part of the panel: `assistant.history` turns the panel to it and back (`panelViewStore`); the last dialog of the project is opened by `history.ts`. No React.
 */
import type { App } from "../../shell/app";
import { CoreError } from "../../shell/api";
import { commandTools, findByToolName, type Command } from "../../shell/commands";
import { setPanel } from "../../shell/workspace";
import { deleteDialog, renameDialog } from "./api";
import { CONNECT_SECTIONS } from "./commands";
import { contextOf } from "./context";
import { declineAllConfirms, hasOpenConfirm, installConfirm } from "./confirm";
import { PendingRunner, pendingActive, setPendingRunner, stopPending } from "./pending";
import { startHistoryWatch } from "./history";
import { ask, chatStore, configureAssistant, newDialog, openDialog, panelViewStore, refreshDialogs, setPendingHandler, showChat, showHistory, startAssistantEvents, statusStore, stopTurn } from "./state";

export type AssistantWindowId = "assistant.connect";

export interface BindOptions {
  /** opens a window of the assistant (23.17 «Claude: подключение»); `props.section` — a section of `CONNECT_SECTIONS` */
  openWindow?: (id: AssistantWindowId, props?: Record<string, unknown>) => void;
}

export const FROM_CLAUDE = "Нельзя вызвать из ассистента";
export const NO_WINDOW = "Окно ассистента ещё не готово";

const errText = (e: unknown): string => (e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e));
const needCore = (ctx: { coreOnline: boolean }): string | null => (ctx.coreOnline ? null : "Ядро не запущено");

/** Why a question cannot be asked now (the status of the connection of the core); null — it can, or it is not known yet (the core then answers itself). */
export function askUnavailable(ctx: { coreOnline: boolean }): string | null {
  const core = needCore(ctx);
  if (core) return core;
  const st = statusStore.get().status;
  if (!st) return null;
  if (st.state === "none") return "Claude не подключён: откройте окно «Подключение Claude»";
  if (st.state === "login") return "Идёт вход в аккаунт Anthropic";
  if (st.online === false) return "Ассистент недоступен без интернета";
  return null;
}

/** The right dock on «Claude», whatever it shows (the chat or the history). */
function openDock(app: Pick<App, "store">): void {
  const w = app.store.get().workspace;
  if (w.right && w.rightTab === "assistant") return;
  app.store.set((s) => ({ workspace: setPanel(s.workspace, "assistant", true) }));
}

/** The right dock on «Claude» with the chat (ТЗ 7.0.3): a question, a confirmation or an opened dialog must be in sight (the history, if it was shown, gives way). */
export function showPanel(app: Pick<App, "store">): void {
  openDock(app);
  showChat();
}

export function bindAssistant(app: App, opts: BindOptions = {}): () => void {
  const registry = app.registry;
  const runner = new PendingRunner(registry);
  setPendingRunner(runner);

  const bind = (id: string, run: Command["run"], unavailable?: Command["unavailable"]) => registry.bind(id, run, unavailable);
  const open = (id: AssistantWindowId, props?: Record<string, unknown>): void => {
    if (!opts.openWindow) throw new Error(NO_WINDOW);
    opts.openWindow(id, props);
  };
  const leaveDialog = (): void => {
    stopPending();
    declineAllConfirms();
  };

  bind(
    "assistant.ask",
    async (a, source) => {
      if (source === "claude") throw new Error(FROM_CLAUDE);
      showPanel(app);
      const r = await ask(String(a.prompt));
      if (!r.ok) throw new Error([r.message, r.hint].filter(Boolean).join(" "));
      return "Вопрос отправлен";
    },
    askUnavailable,
  );
  bind("assistant.new", () => {
    leaveDialog();
    newDialog();
    showPanel(app);
    return "Начат новый диалог";
  });
  bind(
    "assistant.stop",
    async () => {
      const commands = pendingActive();
      stopPending();
      await stopTurn();
      return commands ? "Выполнение команд остановлено" : "Ответ остановлен";
    },
    () => (pendingActive() || chatStore.get().status === "running" ? null : "Ответ не идёт"),
  );
  bind(
    "assistant.connect",
    (a) => {
      const section = typeof a.section === "string" && (CONNECT_SECTIONS as readonly string[]).includes(a.section) ? a.section : undefined;
      open("assistant.connect", section ? { section } : undefined);
      return "Открыто окно «Claude: подключение»";
    },
    needCore,
  );
  bind(
    "assistant.history",
    () => {
      // the history is a part of the panel, not a window (23.16): the command turns the panel to it and back
      if (panelViewStore.get().view === "history") {
        showChat();
        return "Открыт диалог";
      }
      openDock(app);
      showHistory();
      void refreshDialogs();
      return "Открыта история диалогов";
    },
    needCore,
  );
  bind(
    "assistant.open",
    async (a) => {
      const id = String(a.dialog_id);
      leaveDialog();
      await openDialog(id);
      const dialog = chatStore.get().dialog;
      if (!dialog || dialog.id !== id) throw new Error("Диалог не открыт: причина — в панели «Claude»");
      showPanel(app);
      return `Открыт диалог «${dialog.title}»`;
    },
    needCore,
  );
  bind(
    "assistant.rename",
    async (a) => {
      const title = String(a.title).trim();
      if (!title) throw new Error("Название диалога не должно быть пустым");
      const id = String(a.dialog_id);
      try {
        const head = await renameDialog(id, title);
        const chat = chatStore.get();
        if (chat.dialog?.id === id) chatStore.set({ dialog: { ...chat.dialog, title: head.title } });
        void refreshDialogs();
        return `Диалог переименован: «${head.title}»`;
      } catch (e) {
        throw new Error(errText(e));
      }
    },
    needCore,
  );
  bind(
    "assistant.delete",
    async (a) => {
      const id = String(a.dialog_id);
      try {
        await deleteDialog(id);
      } catch (e) {
        throw new Error(errText(e));
      }
      if (chatStore.get().dialog?.id === id) {
        leaveDialog();
        newDialog();
      }
      void refreshDialogs();
      return "Диалог удалён";
    },
    needCore,
  );

  const uninstallConfirm = installConfirm(registry, () => showPanel(app));
  // closing the panel (or switching the dock to «Свойства») while Claude waits for an answer is «Отклонить» (решение 26)
  const stopWatching = app.store.subscribe(() => {
    const w = app.store.get().workspace;
    if (hasOpenConfirm() && !(w.right && w.rightTab === "assistant")) leaveDialog();
  });

  configureAssistant({
    context: () => contextOf(app),
    tools: () => commandTools(registry.list()),
    titleOf: (tool) => findByToolName(registry.list(), tool.name)?.title ?? null,
  });
  setPendingHandler((pending) => void runner.run(pending));
  const stopEvents = startAssistantEvents();
  const stopHistory = startHistoryWatch(app);

  return () => {
    stopHistory();
    stopEvents();
    setPendingHandler(null);
    configureAssistant(null);
    stopWatching();
    uninstallConfirm();
    setPendingRunner(null);
  };
}
