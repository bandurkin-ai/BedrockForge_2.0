// The confirmation card of the chat (Э23, 23.15; ADR 0029 решения 25–26): the hook of the registry is replaced; the card shows the title, the description and ALL the arguments as they are;
// «Разрешить» / «Отклонить» decide the promise; «Стоп», «Новый диалог» and a closed panel decline. The views are called as functions and rendered to markup (no jsdom in the repository).
import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Command } from "../../src/shell/commands";
import { CommandRegistry } from "../../src/shell/registry";
import { ConfirmCard, PendingButtons, RunNotice, CONFIRM_TITLE, RESTORED_HINT } from "../../src/modules/assistant/ConfirmCard";
import { answerConfirm, argText, askClaudeConfirm, confirmArgs, confirmStore, declineAllConfirms, hasOpenConfirm, installConfirm } from "../../src/modules/assistant/confirm";

const DROP: Command = { id: "project.delete", title: "Удалить проект", description: "Удаляет проект целиком. Отменить нельзя.", confirm: true, run: () => "Удалён" };

afterEach(() => declineAllConfirms());

/** The elements of a tree of React elements (a view called as a function) by `data-testid`. */
function find(node: ReactNode, testId: string): ReactElement<Record<string, unknown>>[] {
  const out: ReactElement<Record<string, unknown>>[] = [];
  const walk = (n: ReactNode): void => {
    if (Array.isArray(n)) return n.forEach(walk);
    if (!isValidElement(n)) return;
    const el = n as ReactElement<Record<string, unknown>>;
    if (el.props["data-testid"] === testId) out.push(el);
    walk(el.props.children as ReactNode);
  };
  walk(node);
  return out;
}

describe("the arguments of the card", () => {
  it("show every argument as it is: a string raw, the rest as JSON, a gap is null", () => {
    expect(confirmArgs({ name: "Демо", n: 3, ok: true, list: [1, "a"], gap: null })).toEqual(["name = Демо", "n = 3", "ok = true", 'list = [1,"a"]', "gap = null"]);
    expect(argText(undefined)).toBe("undefined");
    expect(confirmArgs({})).toEqual([]);
  });

  it("do not cut a long value (a long text cannot hide what is asked for)", () => {
    const long = "я".repeat(5000);
    expect(confirmArgs({ ids: long })[0]).toBe(`ids = ${long}`);
  });
});

describe("the queue of questions", () => {
  it("a question waits for its answer; «Разрешить» is true, «Отклонить» is false, the card is gone", async () => {
    const yes = askClaudeConfirm(DROP, { uid: "p-1" });
    const no = askClaudeConfirm(DROP, { uid: "p-2" });
    const [a, b] = confirmStore.get().queue;
    expect(confirmStore.get().queue.map((v) => v.args)).toEqual([["uid = p-1"], ["uid = p-2"]]);
    expect(a).toMatchObject({ commandId: "project.delete", title: "Удалить проект", description: DROP.description });
    expect(answerConfirm(a.id, true)).toBe(true);
    expect(answerConfirm(b.id, false)).toBe(true);
    expect(await yes).toBe(true);
    expect(await no).toBe(false);
    expect(confirmStore.get().queue).toEqual([]);
    expect(answerConfirm(a.id, true)).toBe(false); // the second answer of the same card does nothing
  });

  it("«Стоп» / «Новый диалог» / the closed panel decline every open question", async () => {
    const p = [askClaudeConfirm(DROP, {}), askClaudeConfirm(DROP, {})];
    expect(hasOpenConfirm()).toBe(true);
    expect(declineAllConfirms()).toBe(2);
    expect(await Promise.all(p)).toEqual([false, false]);
    expect(hasOpenConfirm()).toBe(false);
    expect(declineAllConfirms()).toBe(0);
  });
});

describe("the hook of the registry", () => {
  it("replaces `hooks.confirm`, opens the panel first, keeps `hooks.context`, puts the old hook back", async () => {
    const r = new CommandRegistry();
    const old = vi.fn(async () => true);
    const context = vi.fn(() => ({ hasProject: true, hasSet: false, hasHole: false, hasObject: false, hasSection: false, editing: false, coreOnline: true }));
    r.hooks = { context, confirm: old };
    r.register([DROP]);
    const onAsk = vi.fn();
    const restore = installConfirm(r, onAsk);
    expect(r.hooks.context).toBe(context);

    const res = r.execute("project.delete", {}, "claude");
    await vi.waitFor(() => expect(confirmStore.get().queue).toHaveLength(1));
    expect(onAsk).toHaveBeenCalledTimes(1);
    expect(old).not.toHaveBeenCalled(); // the modal window is not asked
    answerConfirm(confirmStore.get().queue[0].id, true);
    expect((await res).ok).toBe(true);

    const pending = r.execute("project.delete", {}, "claude");
    await vi.waitFor(() => expect(confirmStore.get().queue).toHaveLength(1));
    restore(); // taking the hook back declines what is still asked
    expect((await pending).result).toBe("Пользователь отклонил действие");
    expect(r.hooks.confirm).toBe(old);
  });
});

describe("ConfirmCard", () => {
  const view = { id: 9, commandId: "project.delete", title: "Удалить проект", description: "Удаляет проект целиком. Отменить нельзя.", args: ["uid = p-1", "force = true"] };

  it("shows the title, the description and all the arguments", () => {
    const html = renderToStaticMarkup(ConfirmCard({ view, onAnswer: () => undefined }));
    expect(html).toContain(CONFIRM_TITLE);
    expect(html).toContain("Удалить проект");
    expect(html).toContain("Удаляет проект целиком. Отменить нельзя.");
    expect(html).toContain("uid = p-1\nforce = true");
    expect(html).toContain("Разрешить");
    expect(html).toContain("Отклонить");
    expect(html).toContain('data-command="project.delete"');
  });

  it("a text is a text: markup in the title or in an argument is escaped", () => {
    const html = renderToStaticMarkup(ConfirmCard({ view: { ...view, title: "<b>x</b>", args: ['a = <img src=x onerror="1">'] }, onAnswer: () => undefined }));
    expect(html).not.toContain("<b>x</b>");
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });

  it("without arguments it says so", () => {
    expect(renderToStaticMarkup(ConfirmCard({ view: { ...view, args: [] }, onAnswer: () => undefined }))).toContain("Без аргументов");
  });

  it("the buttons answer: «Разрешить» true, «Отклонить» false", () => {
    const onAnswer = vi.fn();
    const tree = ConfirmCard({ view, onAnswer });
    (find(tree, "confirm-allow")[0].props.onClick as () => void)();
    (find(tree, "confirm-deny")[0].props.onClick as () => void)();
    expect(onAnswer.mock.calls).toEqual([[true], [false]]);
  });
});

describe("the buttons of a restored card and the notice of a run", () => {
  it("«Выполнить» and «Отклонить» call their handlers; the hint says nothing runs by itself", () => {
    const onRun = vi.fn();
    const onDecline = vi.fn();
    const tree = PendingButtons({ onRun, onDecline });
    (find(tree, "pending-run")[0].props.onClick as () => void)();
    (find(tree, "pending-decline")[0].props.onClick as () => void)();
    expect(onRun).toHaveBeenCalledTimes(1);
    expect(onDecline).toHaveBeenCalledTimes(1);
    const html = renderToStaticMarkup(PendingButtons({ onRun, onDecline }));
    expect(html).toContain("Выполнить");
    expect(html).toContain(RESTORED_HINT);
  });

  it("the notice has «Стоп»", () => {
    const onStop = vi.fn();
    (find(RunNotice({ onStop }), "run-stop")[0].props.onClick as () => void)();
    expect(onStop).toHaveBeenCalledTimes(1);
    expect(renderToStaticMarkup(RunNotice({ onStop }))).toContain("Claude выполняет команды");
  });
});
