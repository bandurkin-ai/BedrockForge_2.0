// The history of the dialogs in the panel «Claude» (Э23, 23.16; ТЗ 7.32 «История диалогов — в проекте: список с датой, числом вопросов и стоимостью»; ADR 0029 решения 30, 31): the rows are made
// from the REAL answers of the core (`core/tests/data/assistant/contract_answers.json`, 23.11: `dialogs_list`, `dialog_temporary`); the view is drawn with `renderToStaticMarkup`.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { BUSY_REASON, EMPTY_TEXT, OFFLINE_NOTE, HistoryView, historyActions, type HistoryActions, type HistoryReasons, type HistoryViewProps } from "../../src/modules/assistant/HistoryList";
import { formatCost, formatWhen, questionsText, rowOf, TEMPORARY_REASON } from "../../src/modules/assistant/history";
import type { Dialog, DialogHead, DialogList } from "../../src/modules/assistant/wireTypes";

const ANSWERS = JSON.parse(readFileSync(resolve(__dirname, "../../../core/tests/data/assistant/contract_answers.json"), "utf8")) as Record<string, { status: number; body: unknown }>;
const LIST = ANSWERS.dialogs_list.body as DialogList;
const TEMPORARY = (ANSWERS.dialog_temporary.body as Dialog).head;

const noReasons: HistoryReasons = { open: null, rename: null, remove: null, newDialog: null, back: null };
const noop = (): void => undefined;
const actions = (over: Partial<HistoryActions> = {}): HistoryActions => ({ open: noop, rename: noop, remove: noop, more: noop, back: noop, newDialog: noop, ...over });

function view(over: Partial<HistoryViewProps> = {}): string {
  return renderToStaticMarkup(
    createElement(HistoryView, { items: LIST.items, total: LIST.total, error: null, actionError: null, currentId: null, offline: false, reasons: noReasons, actions: actions(), timeZone: "UTC", ...over }),
  );
}

const rowsOf = (html: string): string[] => html.split('data-testid="history-row"').slice(1);
const head = (over: Partial<DialogHead>): DialogHead => ({ ...LIST.items[1], ...over });

describe("the rows of the list (the real answer `dialogs_list`)", () => {
  it("every dialog is a row with the date of the last question, the number of questions and the cost", () => {
    const rows = rowsOf(view());
    expect(rows).toHaveLength(LIST.items.length);
    for (const [i, h] of LIST.items.entries()) {
      expect(rows[i]).toContain(`data-id="${h.id}"`);
      expect(rows[i]).toContain(formatWhen(h.last_at, "UTC"));
      expect(rows[i]).toContain(questionsText(h.questions));
      expect(rows[i]).toContain(formatCost(h.cost_usd));
    }
    expect(formatWhen("2026-11-26T10:00:12Z", "UTC")).toMatch(/2026.*10:00/);
    // The numbers come from the real answer of the core (they change when the samples are regenerated): the row shows them as they are.
    const first = LIST.items[0];
    expect(rowsOf(view())[0]).toContain(questionsText(first.questions));
    expect(rowsOf(view())[0]).toContain(formatCost(first.cost_usd));
    expect(rowsOf(view())[0]).toContain("≈ $");
    expect(questionsText(1)).toBe("1 вопрос");
    expect(questionsText(5)).toBe("5 вопросов");
  });

  it("the order of the core is kept (new first); the titles are texts, never markup", () => {
    const html = view({ items: [head({ id: "9", title: "<b>Жирный</b> & <script>x()</script>" }), ...LIST.items] });
    expect(html).not.toContain("<b>Жирный");
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;b&gt;Жирный");
    expect(rowsOf(html)[0]).toContain('data-id="9"');
  });

  it("the state: «ждёт окна» for a dialog with a command that waited, nothing for a quiet one, «идёт ответ» and «ошибка»", () => {
    const html = view({ items: [...LIST.items, head({ id: "50", state: "running" }), head({ id: "51", state: "error" })] });
    const rows = rowsOf(html);
    expect(rows[0]).toContain('data-testid="history-state" data-state="pending"');
    expect(rows[0]).toContain("ждёт окна");
    expect(rows[1]).not.toContain("history-state");
    expect(rows[2]).toContain("идёт ответ");
    expect(rows[3]).toContain("ошибка");
  });

  it("a cost nobody knows is «—», not 0 (a dialog with a model of an unknown price)", () => {
    const html = view({ items: [head({ cost_usd: null })] });
    expect(html).toContain('data-testid="history-cost"');
    expect(html).toMatch(/data-testid="history-cost"[^>]*>—</);
    expect(html).not.toContain("$0");
    expect(formatCost(null)).toBe("—");
    expect(formatCost(0)).toBe("≈ $0,0000"); // a real zero is a zero
    expect(formatCost(12.3456)).toBe("≈ $12,35");
  });

  it("a temporary dialog is marked, the reason of the core («проект не открыт») is its hint", () => {
    expect(TEMPORARY.temporary).toBe(true);
    expect(TEMPORARY.id.startsWith("t-")).toBe(true);
    const html = view({ items: [TEMPORARY, ...LIST.items] });
    const rows = rowsOf(html);
    expect(rows[0]).toContain('data-temporary="true"');
    expect(rows[0]).toContain("временный");
    expect(rows[0]).toContain(`title="${TEMPORARY_REASON}"`);
    expect(TEMPORARY_REASON).toBe("Диалог не сохранится: проект не открыт");
    expect(rows[1]).toContain('data-temporary="false"');
    expect(rows[1]).not.toContain("history-temporary");
  });

  it("the open dialog is marked", () => {
    const rows = rowsOf(view({ currentId: "41" }));
    expect(rows[0]).toContain('data-current="false"');
    expect(rows[1]).toContain('data-current="true"');
    expect(rows[1]).toContain("history-current");
  });

  it("a date nobody can read is «—»; a title that is empty has a name", () => {
    expect(formatWhen("не дата")).toBe("—");
    expect(rowOf(head({ title: "   " }), null).title).toBe("Без названия");
  });

  it("the plural of «вопрос»", () => {
    expect([0, 1, 2, 4, 5, 11, 12, 14, 21, 22, 25, 101, 111].map(questionsText)).toEqual([
      "0 вопросов",
      "1 вопрос",
      "2 вопроса",
      "4 вопроса",
      "5 вопросов",
      "11 вопросов",
      "12 вопросов",
      "14 вопросов",
      "21 вопрос",
      "22 вопроса",
      "25 вопросов",
      "101 вопрос",
      "111 вопросов",
    ]);
  });
});

describe("the states of the list", () => {
  it("an empty list says what to do", () => {
    const html = view({ items: [], total: 0 });
    expect(html).toContain('data-testid="history-empty"');
    expect(html).toContain(EMPTY_TEXT);
    expect(html).not.toContain("history-list");
  });

  it("without the network the history is read: the rows stay, a note says only questions are unavailable", () => {
    const html = view({ offline: true });
    expect(html).toContain('data-testid="history-offline"');
    expect(html).toContain(OFFLINE_NOTE);
    expect(rowsOf(html)).toHaveLength(2);
    expect(html).not.toContain('aria-disabled="true"');
  });

  it("a failed read shows the reason; the rows read before stay under a warning", () => {
    const none = view({ items: [], total: 0, error: "Ядро недоступно." });
    expect(none).toContain("Не удалось прочитать историю: Ядро недоступно.");
    expect(none).not.toContain("history-empty");
    const some = view({ error: "Ядро недоступно." });
    expect(some).toContain("Список не обновился: Ядро недоступно.");
    expect(rowsOf(some)).toHaveLength(2);
  });

  it("the refusal of an action is shown", () => {
    expect(view({ actionError: "Идёт ответ. Дождитесь конца." })).toContain("Идёт ответ. Дождитесь конца.");
    expect(view()).not.toContain("history-action-error");
  });

  it("pages of 50: «Показано 50 из 120» and «Показать ещё» until the list is whole", () => {
    const many = Array.from({ length: 50 }, (_, i) => head({ id: String(100 + i) }));
    const part = view({ items: many, total: 120 });
    expect(part).toContain("Показано 50 из 120");
    expect(part).toContain('data-testid="history-more"');
    const whole = view();
    expect(whole).toContain("Показано 2 из 2");
    expect(whole).not.toContain("history-more");
  });

  it("an action that is unavailable says why; a dialog with an answer that goes on cannot be renamed or deleted", () => {
    const html = view({ items: [head({ id: "60", state: "running" })], reasons: { ...noReasons, open: "Ядро не запущено" } });
    expect(html).toContain(`title="Недоступно: ${BUSY_REASON}"`);
    expect(html).toContain('title="Недоступно: Ядро не запущено"');
    expect((html.match(/aria-disabled="true"/g) ?? []).length).toBe(3);
  });
});

describe("the buttons call the actions (and only when they are available)", () => {
  type Props = { onClick?: () => void; "data-testid"?: string };
  /** The tree of elements of `HistoryView`, walked without a DOM: the buttons by `data-testid`. */
  function buttons(props: HistoryViewProps): Map<string, () => void> {
    const found = new Map<string, () => void>();
    const walk = (node: unknown): void => {
      if (Array.isArray(node)) return void node.forEach(walk);
      if (!node || typeof node !== "object") return;
      const el = node as { type?: unknown; props?: Props & { children?: unknown } };
      if (typeof el.type === "function") return walk((el.type as (p: unknown) => unknown)(el.props));
      const p = el.props;
      if (p?.["data-testid"] && p.onClick && !found.has(p["data-testid"])) found.set(p["data-testid"], p.onClick);
      walk(p?.children);
    };
    walk(HistoryView(props));
    return found;
  }

  it("open, rename, delete get the head of the row; «Показать ещё», «К диалогу» and «Новый диалог» are the panel's", () => {
    const calls: string[] = [];
    const a = actions({
      open: (h) => calls.push(`open ${h.id}`),
      rename: (h) => calls.push(`rename ${h.id}`),
      remove: (h) => calls.push(`delete ${h.id}`),
      more: () => calls.push("more"),
      back: () => calls.push("back"),
      newDialog: () => calls.push("new"),
    });
    const b = buttons({ items: LIST.items, total: 120, error: null, actionError: null, currentId: null, offline: false, reasons: noReasons, actions: a });
    for (const id of ["history-open", "history-rename", "history-delete", "history-more", "history-back", "history-new"]) b.get(id)?.();
    expect(calls).toEqual(["open 48", "rename 48", "delete 48", "more", "back", "new"]);
  });

  it("a button with a reason does nothing", () => {
    const calls: string[] = [];
    const a = actions({ open: () => calls.push("open"), rename: () => calls.push("rename"), remove: () => calls.push("delete"), back: () => calls.push("back") });
    const reasons: HistoryReasons = { open: "Ядро не запущено", rename: "Ядро не запущено", remove: "Ядро не запущено", newDialog: null, back: "Ядро не запущено" };
    const b = buttons({ items: LIST.items, total: 2, error: null, actionError: null, currentId: null, offline: false, reasons, actions: a });
    for (const id of ["history-open", "history-rename", "history-delete", "history-back"]) b.get(id)?.();
    expect(calls).toEqual([]);
  });

  it("the actions of the panel are commands of the registry", () => {
    const run = vi.fn(async () => undefined);
    const execute = vi.fn(async () => ({ ok: true, result: "" }));
    const a = historyActions({ run, registry: { execute } as never });
    a.back();
    a.newDialog();
    expect(run.mock.calls).toEqual([["assistant.history"], ["assistant.new"]]);
    a.open(LIST.items[0]);
    expect(execute).toHaveBeenCalledWith("assistant.open", { dialog_id: "48" }, "user");
  });
});
