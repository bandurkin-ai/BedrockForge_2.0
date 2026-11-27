// The history of the dialogs on the REAL answers of the core (Э23, 23.16; ТЗ 7.32; ADR 0029 решения 23, 30, 31; docs/progress/e23-api.md §7): the last dialog of the project after a restart,
// a dialog opened with `pending` runs nothing, a temporary dialog and its reason, a change of the project, the history read without the network, rename and delete
// (the confirmation is the window of the application). The routes of the core are a stub of `fetch` that answers with `contract_answers.json` (23.11).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setCoreTarget } from "../../src/shell/api";
import { createApp, type App } from "../../src/shell/app";
import { dialogStore } from "../../src/shell/dialogs";
import { askUnavailable, bindAssistant } from "../../src/modules/assistant/bind";
import { declineAllConfirms } from "../../src/modules/assistant/confirm";
import { chatUntouched, deleteFlow, openFlow, renameFlow, resetRestore, restoreLastDialog, TEMPORARY_REASON, TITLE_MAX } from "../../src/modules/assistant/history";
import { pendingActive, pendingStore } from "../../src/modules/assistant/pending";
import { chatStore, dialogsStore, loadMoreDialogs, newDialog, openDialog, panelViewStore, refreshDialogs, showChat, statusStore } from "../../src/modules/assistant/state";
import { projectStore, type ProjectInfo } from "../../src/modules/projects/state";
import type { Dialog, DialogHead, DialogList } from "../../src/modules/assistant/wireTypes";

const ANSWERS = JSON.parse(readFileSync(resolve(__dirname, "../../../core/tests/data/assistant/contract_answers.json"), "utf8")) as Record<string, { status: number; body: unknown }>;
const LIST = ANSWERS.dialogs_list.body as DialogList;
const PENDING = ANSWERS.dialog_full_sample.body as Dialog; // id 48, state «pending», two cards `waiting`
const TEMPORARY = ANSWERS.dialog_temporary.body as Dialog; // id t-…, `note` «Диалог не сохранится: проект не открыт»
const PROJECT_A = PENDING.head.project_uid as string;
const PROJECT_B = "b8c2d0e1-0000-4000-8000-000000000002";
const json = (body: unknown, status = 200): Response => new Response(JSON.stringify(body), { status });

interface Call {
  method: string;
  path: string;
  body: Record<string, unknown> | undefined;
}
const calls: Call[] = [];
let route: (c: Call) => Response | Promise<Response> = () => json({});

function stubCore(): void {
  calls.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const c: Call = { method: init.method ?? "GET", path: String(url).replace("http://127.0.0.1:5555/api/v1/assistant", ""), body: init.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined };
      // the routes of the other modules (a project that is opened makes them read their data) are not the subject: «not found»
      if (!String(url).includes("/api/v1/assistant")) return json({ code: "not_found", message: "Нет.", hint: "" }, 404);
      calls.push(c);
      return route(c);
    }),
  );
}
const called = (method: string, path: string): Call[] => calls.filter((c) => c.method === method && c.path === path);

const project = (uid: string): ProjectInfo => ({ uid, name: `Проект ${uid.slice(0, 2)}`, description: { deposit: "", license: "", stage: "" }, created_at: "", updated_at: "", version: 1, summary: {}, path: "", read_only: false, lock_owner: {} });
const openProject = (uid: string | null): void => projectStore.set({ current: uid ? project(uid) : null });
const headOf = (over: Partial<DialogHead>): DialogHead => ({ ...PENDING.head, ...over });
/** the core of the project: its list, the last dialog and the dialogs by id */
function projectRoutes(opts: { last: DialogHead | null; dialogs: Dialog[]; list?: DialogHead[] }): void {
  route = (c) => {
    if (c.method === "GET" && c.path === "/dialogs/last") return opts.last ? json(opts.last) : new Response(null, { status: 204 });
    const one = /^\/dialogs\/([^/?]+)$/.exec(c.path);
    if (c.method === "GET" && one) {
      const d = opts.dialogs.find((x) => x.head.id === decodeURIComponent(one[1]));
      return d ? json(d) : json({ code: "assistant_dialog_not_found", message: "Диалог не найден.", hint: "Обновите список." }, 404);
    }
    if (c.method === "GET" && c.path.startsWith("/dialogs?")) {
      const items = opts.list ?? LIST.items;
      return json({ total: items.length, items });
    }
    return json({});
  };
}

let app: App;
let dispose: () => void;

beforeEach(() => {
  setCoreTarget({ baseUrl: "http://127.0.0.1:5555", token: "tok" });
  stubCore();
  projectRoutes({ last: null, dialogs: [] });
  resetRestore();
  openProject(null);
  newDialog();
  showChat();
  dialogsStore.set({ items: [], total: 0, error: null });
  statusStore.set({ status: null, error: null });
  dialogStore.set({ queue: [] });
  app = createApp({ persist: false });
  app.store.set({ core: "online" });
  dispose = bindAssistant(app, {});
});
afterEach(() => {
  dispose();
  declineAllConfirms();
  vi.unstubAllGlobals();
  setCoreTarget(null);
  openProject(null);
  newDialog();
  showChat();
  dialogStore.set({ queue: [] });
  statusStore.set({ status: null, error: null });
});

describe("the list is read from the core", () => {
  it("`refreshDialogs` takes the real `dialogs_list`; an error keeps the rows read before (the history is read without the network)", async () => {
    await refreshDialogs();
    expect(called("GET", "/dialogs?offset=0&limit=50")).toHaveLength(1);
    expect(dialogsStore.get()).toMatchObject({ total: 2, error: null });
    expect(dialogsStore.get().items.map((d) => d.id)).toEqual(["48", "41"]);
    route = () => {
      throw new TypeError("network down");
    };
    await refreshDialogs();
    expect(dialogsStore.get().items).toHaveLength(2);
    expect(dialogsStore.get().error).toContain("Ядро недоступно");
  });

  it("pages of 50: the next page goes by the offset and does not double a row; a refresh keeps the pages that were loaded", async () => {
    const page = (from: number, n: number): DialogHead[] => Array.from({ length: n }, (_, i) => headOf({ id: String(from + i) }));
    route = (c) => {
      const m = /offset=(\d+)&limit=(\d+)/.exec(c.path);
      if (!m) return json({});
      const [offset, limit] = [Number(m[1]), Number(m[2])];
      const all = page(1, 120);
      return json({ total: 120, items: all.slice(offset, offset + limit) });
    };
    await refreshDialogs();
    expect(dialogsStore.get().items).toHaveLength(50);
    await loadMoreDialogs();
    expect(called("GET", "/dialogs?offset=50&limit=50")).toHaveLength(1);
    expect(dialogsStore.get().items).toHaveLength(100);
    await refreshDialogs(); // an event of the bus: the list is read again as far as it was shown
    expect(called("GET", "/dialogs?offset=0&limit=100")).toHaveLength(1);
    expect(dialogsStore.get().items).toHaveLength(100);
    await loadMoreDialogs();
    expect(dialogsStore.get().items).toHaveLength(120);
    expect(new Set(dialogsStore.get().items.map((d) => d.id)).size).toBe(120);
  });
});

describe("the last dialog of the project (ТЗ 7.32: «После перезапуска открывается последний диалог проекта»)", () => {
  it("a project appears: the last dialog is opened with its feed; the dialog with `pending` runs nothing — the cards wait for «Выполнить» / «Отклонить»", async () => {
    projectRoutes({ last: PENDING.head, dialogs: [PENDING] });
    openProject(PROJECT_A);
    await vi.waitFor(() => expect(chatStore.get().dialog?.id).toBe("48"));
    const chat = chatStore.get();
    expect(chat.items).toHaveLength(PENDING.items.length);
    expect(chat.status).toBe("pending");
    expect(chat.pending?.id).toBe("p-3");
    expect(chat.items.filter((i) => i.kind === "tool" && i.status === "waiting")).toHaveLength(2);
    expect(called("GET", "/dialogs/last")).toHaveLength(1);
    expect(calls.filter((c) => c.method === "POST")).toEqual([]); // nothing was sent: no turn, no results
    expect(pendingActive()).toBe(false);
    expect(pendingStore.get().running).toBeNull();
    expect(app.registry.entries()).toEqual([]); // no command of Claude ran
  });

  it("the project of the first call: the panel mounted (or the core came up) after the project was opened", async () => {
    app.store.set({ core: "starting" });
    openProject(PROJECT_A);
    projectRoutes({ last: PENDING.head, dialogs: [PENDING] });
    expect(await restoreLastDialog(PROJECT_A)).toBe("opened");
    expect(chatStore.get().dialog?.id).toBe("48");
    expect(await restoreLastDialog(PROJECT_A)).toBe("skipped"); // once per project: the panel, the binding and the event may all ask
    expect(called("GET", "/dialogs/last")).toHaveLength(1);
  });

  it("a project without dialogs (204) leaves the empty chat; no project asks nothing", async () => {
    app.store.set({ core: "starting" });
    openProject(PROJECT_A);
    expect(await restoreLastDialog(PROJECT_A)).toBe("none");
    expect(chatStore.get().dialog).toBeNull();
    expect(await restoreLastDialog(null)).toBe("skipped");
    expect(called("GET", "/dialogs/last")).toHaveLength(1);
  });

  it("the core is not ready: nothing is lost, the next call tries again", async () => {
    app.store.set({ core: "starting" });
    openProject(PROJECT_A);
    route = () => {
      throw new TypeError("network down");
    };
    expect(await restoreLastDialog(PROJECT_A)).toBe("failed");
    projectRoutes({ last: PENDING.head, dialogs: [PENDING] });
    expect(await restoreLastDialog(PROJECT_A)).toBe("opened");
  });

  it("a dialog the user has already started is not replaced (neither a question that goes on nor a feed in hand)", async () => {
    app.store.set({ core: "starting" });
    openProject(PROJECT_A);
    projectRoutes({ last: PENDING.head, dialogs: [PENDING] });
    chatStore.set({ items: [{ kind: "user", id: "local-1", at: "2026-11-26T10:00:00Z", text: "Вопрос" }], status: "running" });
    expect(chatUntouched()).toBe(false);
    expect(await restoreLastDialog(PROJECT_A)).toBe("skipped");
    expect(chatStore.get().dialog).toBeNull();
    expect(chatStore.get().items).toHaveLength(1);
    expect(called("GET", "/dialogs/last")).toEqual([]);
  });

  it("«Новый диалог» pressed while the core was answering wins: the answer of the core is dropped", async () => {
    app.store.set({ core: "starting" });
    openProject(PROJECT_A);
    let release: (r: Response) => void = () => undefined;
    route = (c) => (c.path === "/dialogs/last" ? new Promise<Response>((r) => (release = r)) : json({}));
    const pending = restoreLastDialog(PROJECT_A);
    await vi.waitFor(() => expect(called("GET", "/dialogs/last")).toHaveLength(1));
    newDialog(); // the user starts anew
    release(json(PENDING.head));
    expect(await pending).toBe("none");
    expect(chatStore.get().dialog).toBeNull();
    expect(called("GET", "/dialogs/48")).toEqual([]);
  });

  it("a dialog opened by the user from the history is not overwritten by the late answer for the last one", async () => {
    app.store.set({ core: "starting" });
    openProject(PROJECT_A);
    projectRoutes({ last: PENDING.head, dialogs: [PENDING] });
    let release: (r: Response) => void = () => undefined;
    const inner = route;
    route = (c) => (c.path === "/dialogs/last" ? new Promise<Response>((r) => (release = r)) : inner(c));
    const pending = restoreLastDialog(PROJECT_A);
    await vi.waitFor(() => expect(called("GET", "/dialogs/last")).toHaveLength(1));
    await openDialog("48");
    release(json(headOf({ id: "41" })));
    expect(await pending).toBe("none");
    expect(chatStore.get().dialog?.id).toBe("48");
  });
});

describe("a change of the project (ADR 0029 решение 31)", () => {
  it("a saved dialog of the old project leaves the panel; the new project opens its own last dialog", async () => {
    projectRoutes({ last: PENDING.head, dialogs: [PENDING] });
    openProject(PROJECT_A);
    await vi.waitFor(() => expect(chatStore.get().dialog?.id).toBe("48"));
    const other = { ...PENDING, head: headOf({ id: "7", title: "Другой проект", project_uid: PROJECT_B, state: "idle" }), pending: null };
    projectRoutes({ last: other.head, dialogs: [other] });
    openProject(PROJECT_B);
    await vi.waitFor(() => expect(chatStore.get().dialog?.id).toBe("7"));
    expect(chatStore.get().dialog?.project_uid).toBe(PROJECT_B);
    expect(chatStore.get().status).toBe("idle");
    expect(called("GET", "/dialogs/last")).toHaveLength(2);
  });

  it("a project without dialogs: the dialog of the old project is gone, the feed is empty", async () => {
    projectRoutes({ last: PENDING.head, dialogs: [PENDING] });
    openProject(PROJECT_A);
    await vi.waitFor(() => expect(chatStore.get().dialog?.id).toBe("48"));
    projectRoutes({ last: null, dialogs: [] });
    openProject(PROJECT_B);
    await vi.waitFor(() => expect(called("GET", "/dialogs/last")).toHaveLength(2));
    expect(chatStore.get().dialog).toBeNull();
    expect(chatStore.get().items).toEqual([]);
  });

  it("the project is closed: the dialog of it does not stay in the panel", async () => {
    projectRoutes({ last: PENDING.head, dialogs: [PENDING] });
    openProject(PROJECT_A);
    await vi.waitFor(() => expect(chatStore.get().dialog?.id).toBe("48"));
    openProject(null);
    expect(chatStore.get().dialog).toBeNull();
    expect(chatStore.get().items).toEqual([]);
    expect(called("GET", "/dialogs/last")).toHaveLength(1);
  });

  it("a temporary dialog goes with the user into the project that is opened (the core writes it there at the end of its segment): the last dialog does not replace it", async () => {
    projectRoutes({ last: PENDING.head, dialogs: [PENDING, TEMPORARY] });
    await openFlow(app, TEMPORARY.head);
    expect(chatStore.get().dialog).toMatchObject({ id: TEMPORARY.head.id, temporary: true });
    openProject(PROJECT_A);
    await new Promise((r) => setTimeout(r, 20)); // the watch had its turn: it asked nothing, the temporary dialog is in hand
    expect(called("GET", "/dialogs/last")).toEqual([]);
    expect(chatStore.get().dialog?.id).toBe(TEMPORARY.head.id);
    expect(chatStore.get().items).toHaveLength(TEMPORARY.items.length);
    expect(called("GET", "/dialogs/48")).toEqual([]);
    // and into the next project too
    openProject(PROJECT_B);
    expect(chatStore.get().dialog?.id).toBe(TEMPORARY.head.id);
  });

  it("a dialog with an answer that goes on is not taken from the user by a change of the project", async () => {
    projectRoutes({ last: PENDING.head, dialogs: [PENDING] });
    openProject(PROJECT_A);
    await vi.waitFor(() => expect(chatStore.get().dialog?.id).toBe("48"));
    chatStore.set({ status: "running" });
    projectRoutes({ last: null, dialogs: [] });
    openProject(PROJECT_B);
    expect(chatStore.get().dialog?.id).toBe("48");
  });
});

describe("open: a temporary dialog and a dialog with `pending`", () => {
  it("a temporary dialog opens with the reason of the core and the mark", async () => {
    projectRoutes({ last: null, dialogs: [TEMPORARY] });
    const r = await openFlow(app, TEMPORARY.head);
    expect(r.ok).toBe(true);
    const chat = chatStore.get();
    expect(chat.dialog?.temporary).toBe(true);
    expect(chat.note).toBe(TEMPORARY_REASON);
    expect(chat.note).toBe(TEMPORARY.note);
    expect(chat.pending).toBeNull();
    expect(app.store.get().workspace.right && app.store.get().workspace.rightTab).toBe("assistant"); // the dock shows it
    expect(panelViewStore.get().view).toBe("chat");
  });

  it("the dialog with `pending` from the history: the cards wait, nothing is run or sent; «Отклонить» is the user's", async () => {
    projectRoutes({ last: null, dialogs: [PENDING] });
    const r = await openFlow(app, PENDING.head);
    expect(r).toEqual({ ok: true, result: `Открыт диалог «${PENDING.head.title}»` });
    expect(chatStore.get().status).toBe("pending");
    expect(chatStore.get().pending?.tools.map((t) => t.name)).toEqual(["ui_hole_select", "ui_section_through_hole"]);
    expect(calls.filter((c) => c.method !== "GET")).toEqual([]);
    expect(pendingActive()).toBe(false);
  });

  it("a dialog that is not there (deleted elsewhere) is a refusal in Russian, shown over the list", async () => {
    projectRoutes({ last: null, dialogs: [] });
    const r = await openFlow(app, { id: "999" });
    expect(r.ok).toBe(false);
    expect(panelViewStore.get().error).toBe(r.result);
    expect(r.result).toMatch(/[А-Яа-яЁё]/);
  });
});

describe("the history in the panel", () => {
  it("`assistant.history` turns the panel to the list and back (no window); a question or an opened dialog turns it to the chat", async () => {
    expect((await app.registry.execute("assistant.history", {})).result).toBe("Открыта история диалогов");
    expect(panelViewStore.get().view).toBe("history");
    expect(app.store.get().workspace.right && app.store.get().workspace.rightTab).toBe("assistant");
    await vi.waitFor(() => expect(called("GET", "/dialogs?offset=0&limit=50")).toHaveLength(1));
    expect((await app.registry.execute("assistant.history", {})).result).toBe("Открыт диалог");
    expect(panelViewStore.get().view).toBe("chat");
    await app.registry.execute("assistant.history", {});
    await app.registry.execute("assistant.new", {});
    expect(panelViewStore.get().view).toBe("chat");
    await app.registry.execute("assistant.history", {});
    route = (c) => (c.path === "/turns" ? new Response("", { status: 200 }) : json({}));
    await app.registry.execute("assistant.ask", { prompt: "Привет" });
    expect(panelViewStore.get().view).toBe("chat");
  });
});

describe("offline: the history is read, only a question is not", () => {
  it("without the internet (`online: false`) the list and a dialog open; a question is refused with the reason", async () => {
    statusStore.set({ status: { state: "connected", online: false } as never });
    projectRoutes({ last: null, dialogs: [PENDING] });
    await refreshDialogs();
    expect(dialogsStore.get().items).toHaveLength(2);
    expect((await openFlow(app, PENDING.head)).ok).toBe(true);
    expect(chatStore.get().dialog?.id).toBe("48");
    expect(askUnavailable({ coreOnline: true })).toBe("Ассистент недоступен без интернета");
  });
});

describe("rename", () => {
  const nextWindow = async () => {
    await vi.waitFor(() => expect(dialogStore.get().queue).toHaveLength(1));
    return dialogStore.get().queue[0];
  };

  it("the name is asked in the own window with the old one; the answer runs `assistant.rename` and the list is read again", async () => {
    route = (c) => (c.method === "PATCH" ? json(headOf({ id: "41", title: "Новое имя" })) : json({ total: 0, items: [] }));
    const done = renameFlow(app, LIST.items[1]);
    const w = await nextWindow();
    expect(w.kind).toBe("text");
    if (w.kind === "text") {
      expect(w.options).toMatchObject({ title: "Переименовать диалог", initial: "Сводка по проекту" });
      w.resolve("  Новое имя  ");
    }
    const r = await done;
    expect(r).toEqual({ ok: true, result: "Диалог переименован: «Новое имя»" });
    expect(called("PATCH", "/dialogs/41")[0].body).toEqual({ title: "Новое имя" });
    await vi.waitFor(() => expect(called("GET", "/dialogs?offset=0&limit=50").length).toBeGreaterThan(0));
  });

  it("the open dialog takes the new title at once", async () => {
    projectRoutes({ last: null, dialogs: [PENDING] });
    await openFlow(app, PENDING.head);
    route = () => json(headOf({ title: "Переименован" }));
    const done = renameFlow(app, PENDING.head);
    const w = await nextWindow();
    if (w.kind === "text") w.resolve("Переименован");
    await done;
    expect(chatStore.get().dialog?.title).toBe("Переименован");
  });

  it("cancel, an empty name and the same name change nothing; a name over 200 characters is refused before the call", async () => {
    for (const answer of [null, "   ", "Сводка по проекту"]) {
      const done = renameFlow(app, LIST.items[1]);
      const w = await nextWindow();
      if (w.kind === "text") w.resolve(answer);
      expect(await done).toBeNull();
      dialogStore.set({ queue: [] });
    }
    const done = renameFlow(app, LIST.items[1]);
    const w = await nextWindow();
    if (w.kind === "text") w.resolve("я".repeat(TITLE_MAX + 1));
    expect(await done).toBeNull();
    expect(panelViewStore.get().error).toBe(`Название длиннее ${TITLE_MAX} знаков`);
    expect(calls.filter((c) => c.method === "PATCH")).toEqual([]);
  });

  it("the core refuses a dialog with an answer that goes on (409): the reason is shown", async () => {
    route = () => json({ code: "assistant_busy", message: "Идёт ответ.", hint: "Дождитесь конца ответа." }, 409);
    const done = renameFlow(app, LIST.items[1]);
    const w = await nextWindow();
    if (w.kind === "text") w.resolve("Другое имя");
    const r = await done;
    expect(r?.ok).toBe(false);
    expect(panelViewStore.get().error).toBe("Идёт ответ. Дождитесь конца ответа.");
  });
});

describe("delete", () => {
  const confirmWindow = async () => {
    await vi.waitFor(() => expect(dialogStore.get().queue).toHaveLength(1));
    return dialogStore.get().queue[0];
  };

  it("the confirmation is the own window of the application with the title of the dialog; a refusal leaves the dialog whole", async () => {
    const head = headOf({ id: "41", title: "Модель рудного тела" });
    const done = deleteFlow(app, head);
    const w = await confirmWindow();
    expect(w.kind).toBe("confirm");
    if (w.kind === "confirm") {
      expect(w.options.message).toContain("«Модель рудного тела»");
      expect(w.options.okLabel).toBe("Удалить");
      expect(w.options.danger).toBe(true);
      w.resolve(false);
    }
    expect(await done).toBeNull();
    expect(calls.filter((c) => c.method === "DELETE")).toEqual([]);
  });

  it("«Удалить» runs `assistant.delete`: the dialog is deleted and the list is read again", async () => {
    route = (c) => (c.method === "DELETE" ? new Response(null, { status: 204 }) : json({ total: 1, items: [LIST.items[0]] }));
    const done = deleteFlow(app, LIST.items[1]);
    const w = await confirmWindow();
    if (w.kind === "confirm") w.resolve(true);
    expect(await done).toEqual({ ok: true, result: "Диалог удалён" });
    expect(called("DELETE", "/dialogs/41")).toHaveLength(1);
    await vi.waitFor(() => expect(dialogsStore.get().items.map((d) => d.id)).toEqual(["48"]));
  });

  it("the open dialog that is deleted leaves the panel (a new empty dialog)", async () => {
    projectRoutes({ last: null, dialogs: [PENDING] });
    await openFlow(app, PENDING.head);
    route = (c) => (c.method === "DELETE" ? new Response(null, { status: 204 }) : json({ total: 0, items: [] }));
    const done = deleteFlow(app, PENDING.head);
    const w = await confirmWindow();
    if (w.kind === "confirm") w.resolve(true);
    await done;
    expect(chatStore.get().dialog).toBeNull();
    expect(chatStore.get().items).toEqual([]);
  });

  it("a temporary dialog says it is not saved; a refusal of the core (an answer goes on) is shown", async () => {
    route = () => json({ code: "assistant_busy", message: "Идёт ответ.", hint: "Дождитесь конца ответа." }, 409);
    const done = deleteFlow(app, TEMPORARY.head);
    const w = await confirmWindow();
    if (w.kind === "confirm") {
      expect(w.options.message).toContain("временный");
      w.resolve(true);
    }
    expect((await done)?.ok).toBe(false);
    expect(panelViewStore.get().error).toBe("Идёт ответ. Дождитесь конца ответа.");
  });
});
