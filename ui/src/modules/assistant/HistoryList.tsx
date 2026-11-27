/**
 * The history of the dialogs in the panel «Claude» (Э23, 23.16; ТЗ 7.32 «История диалогов — в проекте»; ADR 0029 решения 30, 31): a list with the date, the number of questions, the cost
 * and the state; open (the dialog goes on where it stopped), rename, delete. Pages of 50. The history is read without the network: only a new question needs it.
 * `HistoryView` draws from what is known (the tests); `HistoryPanel` is it connected to the stores and to the registry. The buttons are commands of the registry (`assistant.open`,
 * `assistant.rename`, `assistant.delete`, `assistant.new`, `assistant.history`); the texts are text nodes, never markup.
 */
import { useEffect } from "react";
import { useApp } from "../../shell/AppContext";
import type { App } from "../../shell/app";
import { commandContext } from "../../shell/store";
import { useRegistryVersion, useStore } from "../../shell/useStore";
import { deleteFlow, openFlow, renameFlow, rowOf, TEMPORARY_REASON, type HistoryRow } from "./history";
import { chatStore, dialogsStore, loadMoreDialogs, panelViewStore, refreshDialogs, statusStore } from "./state";
import type { DialogHead } from "./wireTypes";
import css from "./HistoryList.module.css";

export const EMPTY_TEXT = "Диалогов в этом проекте пока нет. Задайте вопрос Claude — диалог появится здесь.";
export const OFFLINE_NOTE = "Нет связи с интернетом: диалоги можно читать, новые вопросы недоступны.";
export const BUSY_REASON = "Идёт ответ";

export interface HistoryReasons {
  open: string | null;
  rename: string | null;
  remove: string | null;
  newDialog: string | null;
  back: string | null;
}

export interface HistoryActions {
  open: (head: DialogHead) => void;
  rename: (head: DialogHead) => void;
  remove: (head: DialogHead) => void;
  more: () => void;
  back: () => void;
  newDialog: () => void;
}

export interface HistoryViewProps {
  items: DialogHead[];
  total: number;
  /** the last error of the list (the rows read before stay) */
  error: string | null;
  /** the last refusal of an action of a row */
  actionError: string | null;
  currentId: string | null;
  offline: boolean;
  reasons: HistoryReasons;
  actions: HistoryActions;
  timeZone?: string;
}

function Btn({ label, reason, onClick, testId }: { label: string; reason: string | null; onClick: () => void; testId: string }) {
  return (
    <button
      type="button"
      className={css.btn}
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

function Row({ row, head, reasons, actions }: { row: HistoryRow; head: DialogHead; reasons: HistoryReasons; actions: HistoryActions }) {
  const busy = row.busy ? BUSY_REASON : null;
  return (
    <li className={css.row} data-testid="history-row" data-id={row.id} data-state={row.state} data-temporary={row.temporary ? "true" : "false"} data-current={row.current ? "true" : "false"}>
      <button
        type="button"
        className={css.open}
        data-testid="history-open"
        aria-disabled={reasons.open ? true : undefined}
        title={reasons.open ? `Недоступно: ${reasons.open}` : "Открыть и продолжить с места остановки"}
        onClick={() => {
          if (!reasons.open) actions.open(head);
        }}
      >
        {row.title}
      </button>
      <div className={css.meta}>
        <span data-testid="history-when">{row.when}</span>
        <span data-testid="history-questions">{row.questions}</span>
        <span data-testid="history-cost" title={head.cost_usd === null ? "Цена модели неизвестна: стоимость не определена" : undefined}>
          {row.cost}
        </span>
        {row.stateLabel ? (
          <span className={css.state} data-testid="history-state" data-state={row.state}>
            {row.stateLabel}
          </span>
        ) : null}
        {row.temporary ? (
          <span className={css.temp} data-testid="history-temporary" title={TEMPORARY_REASON}>
            временный
          </span>
        ) : null}
        {row.current ? (
          <span className={css.cur} data-testid="history-current">
            открыт
          </span>
        ) : null}
      </div>
      <div className={css.actions}>
        <Btn label="Переименовать…" reason={busy ?? reasons.rename} onClick={() => actions.rename(head)} testId="history-rename" />
        <Btn label="Удалить…" reason={busy ?? reasons.remove} onClick={() => actions.remove(head)} testId="history-delete" />
      </div>
    </li>
  );
}

export function HistoryView({ items, total, error, actionError, currentId, offline, reasons, actions, timeZone }: HistoryViewProps) {
  return (
    <div className={css.panel} data-testid="history-panel">
      <div className={css.head}>
        <span className={css.title}>История диалогов</span>
        <span className={css.grow} />
        <Btn label="Новый диалог" reason={reasons.newDialog} onClick={actions.newDialog} testId="history-new" />
        <Btn label="К диалогу" reason={reasons.back} onClick={actions.back} testId="history-back" />
      </div>
      {offline ? (
        <div className={css.note} role="status" data-testid="history-offline">
          {OFFLINE_NOTE}
        </div>
      ) : null}
      {actionError ? (
        <div className={css.error} role="alert" data-testid="history-action-error">
          {actionError}
        </div>
      ) : null}
      <div className={css.body}>
        {items.length === 0 ? (
          error ? (
            <div className={css.error} role="alert" data-testid="history-error">
              Не удалось прочитать историю: {error}
            </div>
          ) : (
            <p className={css.empty} data-testid="history-empty">
              {EMPTY_TEXT}
            </p>
          )
        ) : (
          <>
            {error ? (
              <div className={css.error} role="alert" data-testid="history-error">
                Список не обновился: {error}
              </div>
            ) : null}
            <ul className={css.list} data-testid="history-list">
              {items.map((h) => (
                <Row key={h.id} row={rowOf(h, currentId, timeZone)} head={h} reasons={reasons} actions={actions} />
              ))}
            </ul>
            <div className={css.foot} data-testid="history-foot">
              <span>
                Показано {items.length} из {Math.max(total, items.length)}
              </span>
              {items.length < total ? <Btn label="Показать ещё" reason={null} onClick={actions.more} testId="history-more" /> : null}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export const historyActions = (app: Pick<App, "registry" | "run">): HistoryActions => ({
  open: (head) => void openFlow(app, head),
  rename: (head) => void renameFlow(app, head),
  remove: (head) => void deleteFlow(app, head),
  more: () => void loadMoreDialogs(),
  back: () => void app.run("assistant.history"),
  newDialog: () => void app.run("assistant.new"),
});

/** The history in the panel «Claude»: `<HistoryPanel />`. */
export function HistoryPanel() {
  const app = useApp();
  useRegistryVersion(app.registry);
  const list = useStore(dialogsStore, (s) => s);
  const chat = useStore(chatStore, (s) => s);
  const status = useStore(statusStore, (s) => s.status);
  const view = useStore(panelViewStore, (s) => s);
  const state = useStore(app.store, (s) => s);
  const ctx = commandContext(state);
  useEffect(() => {
    void refreshDialogs();
  }, []);
  const reasons: HistoryReasons = {
    open: app.registry.reason("assistant.open", ctx),
    rename: app.registry.reason("assistant.rename", ctx),
    remove: app.registry.reason("assistant.delete", ctx),
    newDialog: app.registry.reason("assistant.new", ctx),
    back: app.registry.reason("assistant.history", ctx),
  };
  return (
    <HistoryView
      items={list.items}
      total={list.total}
      error={list.error}
      actionError={view.error}
      currentId={chat.dialog?.id ?? null}
      offline={status?.online === false}
      reasons={reasons}
      actions={historyActions(app)}
    />
  );
}
