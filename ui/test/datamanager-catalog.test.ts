// The model of «Менеджер данных» (Д-20; ADR 0017 §6): folders, types, sorting, search, choosing several rows
// (Shift, Ctrl, Ctrl+A), drop targets, the reread on `objects.changed` (an overtaken read waits for the newest).
// The catalog of the core (`/catalog`, another track) is a fetch stub.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { catalogPath, catalogStore, fetchAllRows, loadCatalog, moveToFolder, parseFolders, parsePage, parseRow } from "../src/modules/datamanager/api";
import {
  ALL,
  DEFAULT_QUERY,
  dropFolder,
  dropReason,
  folderKey,
  folderTree,
  kindKey,
  moveIds,
  normFolder,
  sortBy,
  viewRows,
  visibleNodes,
  type CatalogRow,
} from "../src/modules/datamanager/catalog";
import { clickRow, contextRow, EMPTY_SELECTION, moveFocus, pruneSelection, selectAll } from "../src/modules/datamanager/selection";
import { projectStore, type ProjectInfo } from "../src/modules/projects/state";
import { setCoreTarget } from "../src/shell/api";

const row = (id: number, p: Partial<CatalogRow> = {}): CatalogRow => ({ id, kind: "wireframe", name: `Объект ${id}`, folder: null, format: null, size: null, rows: null, modified: null, crs: null, state: "ready", ...p });

const rows: CatalogRow[] = [
  row(1, { kind: "drillhole_set", name: "Скважины 2025", rows: 12_400, modified: "2026-11-02T10:00:00Z" }),
  row(2, { name: "Рудное тело 10", folder: "Участок 1", format: "TRIDB", size: 2_000_000, modified: "2026-11-05T10:00:00Z", crs: "МСК-63" }),
  row(3, { name: "Рудное тело 2", folder: "Участок 1/Карьер", format: "DXF", size: 50_000 }),
  row(4, { kind: "line_file", name: "Контуры.str", folder: "Участок 2", format: "STR", size: 4_000 }),
  row(5, { kind: "dtm", name: "Рельеф", size: null }),
  row(6, { kind: "raster", name: "ортофото", folder: "Участок 1", format: "GeoTIFF", size: 90_000_000, state: "building" }),
];

describe("the folders on the left", () => {
  it("«Все данные», the types in the ADR order, the folders of the user as a tree with counts under them", () => {
    const tree = folderTree(rows, [{ folder: "Пустая", count: 0 }, { folder: "Участок 1", count: 2 }]);
    expect(tree.map((n) => [n.key, n.title, n.count])).toEqual([
      [ALL, "Все данные", 6],
      ["kind:drillhole_set", "Наборы скважин", 1],
      ["kind:wireframe", "Каркасы", 2],
      ["kind:dtm", "ЦТМ", 1],
      ["kind:line_file", "Линии", 1],
      ["kind:raster", "Растры", 1],
      ["folder:Пустая", "Пустая", 0],
      ["folder:Участок 1", "Участок 1", 3],
      ["folder:Участок 2", "Участок 2", 1],
    ]);
    const u1 = tree.find((n) => n.key === folderKey("Участок 1"))!;
    expect(u1.children.map((n) => [n.key, n.title, n.count, n.depth])).toEqual([["folder:Участок 1/Карьер", "Карьер", 1, 1]]);
    expect(visibleNodes(tree, new Set()).length).toBe(9);
    expect(visibleNodes(tree, new Set([u1.key])).map((n) => n.title).slice(7, 9)).toEqual(["Участок 1", "Карьер"]);
  });

  it("folders are normalised: «/» at the ends and doubled are dropped, empty is the root", () => {
    expect(normFolder(" /a//b / ")).toBe("a/b");
    expect(normFolder("")).toBeNull();
    expect(normFolder(null)).toBeNull();
    expect(folderTree([row(1, { folder: "a/b/c" })]).filter((n) => n.key.startsWith("folder:")).map((n) => n.count)).toEqual([1]);
  });
});

describe("the table: the node, the search, the sorting", () => {
  it("a folder shows its rows and those of its folders; a type — its rows", () => {
    expect(viewRows(rows, { ...DEFAULT_QUERY, node: folderKey("Участок 1") }).map((r) => r.id)).toEqual([6, 3, 2]) // by name;
    expect(viewRows(rows, { ...DEFAULT_QUERY, node: folderKey("Участок 1/Карьер") }).map((r) => r.id)).toEqual([3]);
    expect(viewRows(rows, { ...DEFAULT_QUERY, node: folderKey("Участок") }).map((r) => r.id)).toEqual([]); // not a prefix of a name
    expect(viewRows(rows, { ...DEFAULT_QUERY, node: kindKey("wireframe") }).map((r) => r.id)).toEqual([3, 2]);
  });

  it("names sort as people read them: numbers by value, case ignored", () => {
    expect(viewRows(rows, DEFAULT_QUERY).map((r) => r.name)).toEqual(["Контуры.str", "ортофото", "Рельеф", "Рудное тело 2", "Рудное тело 10", "Скважины 2025"]);
  });

  it("empty values go last both ways — a missing size is not 0", () => {
    const asc = viewRows(rows, { ...DEFAULT_QUERY, sort: "size" }).map((r) => r.id);
    expect(asc).toEqual([4, 3, 2, 6, 5, 1]); // the empty ones by name: «Рельеф», «Скважины 2025»
    const desc = viewRows(rows, sortBy({ ...DEFAULT_QUERY, sort: "size" }, "size")).map((r) => r.id);
    expect(desc).toEqual([6, 2, 3, 4, 5, 1]);
    expect(viewRows(rows, { ...DEFAULT_QUERY, sort: "kind" }).map((r) => r.kind)).toEqual(["drillhole_set", "wireframe", "wireframe", "dtm", "line_file", "raster"]);
    expect(viewRows(rows, { ...DEFAULT_QUERY, sort: "modified", desc: true }).map((r) => r.id).slice(0, 2)).toEqual([2, 1]);
  });

  it("a header click: the same column turns the order, another sorts ascending", () => {
    const q = sortBy(DEFAULT_QUERY, "name");
    expect([q.sort, q.desc]).toEqual(["name", true]);
    expect(sortBy(q, "format")).toEqual({ ...DEFAULT_QUERY, sort: "format", desc: false });
  });

  it("the search: every word, in the name, the folder, the format, the type, the CRS; any case", () => {
    expect(viewRows(rows, { ...DEFAULT_QUERY, q: "рудное 10" }).map((r) => r.id)).toEqual([2]);
    expect(viewRows(rows, { ...DEFAULT_QUERY, q: "участок 1" }).map((r) => r.id)).toEqual([6, 3, 2]);
    expect(viewRows(rows, { ...DEFAULT_QUERY, q: "tridb" }).map((r) => r.id)).toEqual([2]);
    expect(viewRows(rows, { ...DEFAULT_QUERY, q: "цтм" }).map((r) => r.id)).toEqual([5]);
    expect(viewRows(rows, { ...DEFAULT_QUERY, q: "мск" }).map((r) => r.id)).toEqual([2]);
    expect(viewRows(rows, { ...DEFAULT_QUERY, q: "  " }).length).toBe(6);
  });
});

describe("drop targets", () => {
  it("a folder of the user and «Все данные» (the root) take rows; a type does not", () => {
    expect(dropFolder(folderKey("Участок 1/Карьер"))).toBe("Участок 1/Карьер");
    expect(dropFolder(ALL)).toBe("");
    expect(dropFolder(kindKey("wireframe"))).toBeNull();
  });

  it("only the rows a move changes; the reason when none", () => {
    expect(moveIds(rows, [2, 3, 4], "Участок 1")).toEqual([3, 4]);
    expect(moveIds(rows, [2, 5], "")).toEqual([2]);
    expect(dropReason(rows, [2, 3], folderKey("Участок 2"))).toBeNull();
    expect(dropReason(rows, [2], folderKey("Участок 1"))).toBe("Уже в папке «Участок 1»");
    expect(dropReason(rows, [5], ALL)).toBe("Уже в корне");
    expect(dropReason(rows, [2], kindKey("dtm"))).toBe("Перенос — на папку или на «Все данные»");
    expect(dropReason(rows, [6], folderKey("Участок 2"))).toBe("Объект ещё строится");
    expect(dropReason(rows, [], ALL)).toBe("Нечего переносить");
  });
});

describe("choosing several rows", () => {
  const order = [10, 11, 12, 13, 14];

  it("a click, Ctrl, Shift from the anchor, Ctrl+Shift adds the range", () => {
    let s = clickRow(EMPTY_SELECTION, order, 11);
    expect(s).toEqual({ ids: [11], anchor: 11, focus: 11 });
    s = clickRow(s, order, 13, { shift: true });
    expect(s.ids).toEqual([11, 12, 13]);
    s = clickRow(s, order, 10, { shift: true }); // the anchor stays: the range turns
    expect(s.ids).toEqual([11, 10]);
    s = clickRow(s, order, 14, { ctrl: true });
    expect(s).toEqual({ ids: [11, 10, 14], anchor: 14, focus: 14 });
    s = clickRow(s, order, 10, { ctrl: true });
    expect(s.ids).toEqual([11, 14]);
    s = clickRow(s, order, 12, { ctrl: true, shift: true });
    expect(s.ids).toEqual([11, 14, 10, 12]) // the Ctrl click moved the anchor to 10;
    expect(clickRow(s, order, 99)).toBe(s); // not shown
  });

  it("Ctrl+A, the right click keeps the chosen rows, the keys move the focus", () => {
    const all = selectAll(order);
    expect(all.ids).toEqual(order);
    expect(contextRow(all, order, 12)).toEqual({ ...all, focus: 12 });
    expect(contextRow(clickRow(EMPTY_SELECTION, order, 10), order, 12).ids).toEqual([12]);
    let s = moveFocus(EMPTY_SELECTION, order, "down");
    expect(s.ids).toEqual([10]);
    s = moveFocus(s, order, "down", { shift: true });
    s = moveFocus(s, order, "down", { shift: true });
    expect(s.ids).toEqual([10, 11, 12]);
    expect(moveFocus(s, order, "end").ids).toEqual([14]);
    expect(moveFocus(s, order, "home", { shift: true }).ids).toEqual([10]);
    expect(moveFocus(s, order, "pageDown", {}, 10).ids).toEqual([14]);
    expect(moveFocus(s, [], "down")).toBe(s);
  });

  it("after a reload or a search only the shown rows stay chosen", () => {
    const s = selectAll(order);
    expect(pruneSelection(s, order)).toBe(s);
    expect(pruneSelection(s, [12, 14])).toEqual({ ids: [12, 14], anchor: null, focus: null });
  });
});

describe("1 000 rows (Д-20: the list in the window ≤ 200 ms)", () => {
  it("the tree, the view, the search and Ctrl+A — the model ≤ 20 ms", () => {
    const kinds = ["drillhole_set", "wireframe", "dtm", "line_file", "cloud", "raster", "block_model"];
    const many = Array.from({ length: 1000 }, (_, i) =>
      row(i + 1, { kind: kinds[i % 7], name: `Объект ${(i * 7919) % 1000}`, folder: i % 3 ? `Участок ${i % 10}/Блок ${i % 4}` : null, size: i % 5 ? i * 1000 : null, modified: `2026-11-${String((i % 28) + 1).padStart(2, "0")}` }),
    );
    const once = () => {
      const tree = folderTree(many);
      visibleNodes(tree, new Set(tree.map((n) => n.key)));
      const view = viewRows(many, { ...DEFAULT_QUERY, sort: "size", desc: true });
      viewRows(many, { ...DEFAULT_QUERY, node: folderKey("Участок 3"), q: "объект 1" });
      const order = view.map((r) => r.id);
      const s = clickRow(clickRow(EMPTY_SELECTION, order, order[10]), order, order[900], { shift: true });
      pruneSelection(selectAll(order, s), order.slice(0, 500));
      return view.length;
    };
    expect(once()).toBe(1000); // warm-up
    let best = Infinity;
    for (let i = 0; i < 5; i++) {
      const t0 = performance.now();
      once();
      best = Math.min(best, performance.now() - t0);
    }
    expect(best).toBeLessThan(20);
  });
});

describe("the answers of the core", () => {
  it("rows, pages and folders are read leniently", () => {
    expect(parseRow({ id: 3, type: "cloud", name: "Съёмка", folder: "/a/", source_format: "LAS", size: 10, updated_at: "t", crs_name: "WGS 84" })).toEqual({
      id: 3, kind: "cloud", name: "Съёмка", folder: "a", format: "LAS", size: 10, rows: null, modified: "t", crs: "WGS 84", state: "ready",
    });
    expect(parseRow({ id: "3", kind: "x" })).toBeNull();
    expect(parseRow({ id: 3 })).toBeNull();
    expect(parseRow({ id: 4, kind: "raster" })!.name).toBe("№4");
    expect(parsePage({ total: 9, rows: [{ id: 1, kind: "dtm" }, 5] })).toMatchObject({ total: 9, rows: [{ id: 1 }] });
    expect(parsePage(null)).toEqual({ total: 0, rows: [] });
    expect(parseFolders({ folders: [{ folder: "a", count: 2 }, { path: "b/" }, "c", "", 7] })).toEqual([{ folder: "a", count: 2 }, { folder: "b", count: 0 }, { folder: "c", count: 0 }]);
    expect(parseFolders([{ name: "d", count: 1 }])).toEqual([{ folder: "d", count: 1 }]);
  });

  it("the query of `GET /catalog`", () => {
    expect(catalogPath()).toBe("/catalog");
    expect(catalogPath({ folder: "Участок 1", kind: "wireframe", q: "", sort: "size", desc: true, offset: 0, limit: 50 })).toBe("/catalog?folder=%D0%A3%D1%87%D0%B0%D1%81%D1%82%D0%BE%D0%BA+1&kind=wireframe&sort=size&desc=1&offset=0&limit=50");
  });

  it("every page is read; a broken total does not loop for ever", async () => {
    const pages = [{ total: 5, rows: [{ id: 1, kind: "dtm" }, { id: 2, kind: "dtm" }] }, { total: 5, rows: [{ id: 3, kind: "dtm" }, { id: 4, kind: "dtm" }] }, { total: 5, rows: [{ id: 5, kind: "dtm" }] }];
    const asked: string[] = [];
    const got = await fetchAllRows(async (p) => {
      asked.push(p);
      return pages[asked.length - 1];
    });
    expect(got.map((r) => r.id)).toEqual([1, 2, 3, 4, 5]);
    expect(asked[1]).toBe("/catalog?offset=2&limit=2000");
    let n = 0;
    const endless = await fetchAllRows(async () => ({ total: 1e9, rows: [{ id: ++n, kind: "dtm" }] }));
    expect(endless.length).toBe(100);
    expect((await fetchAllRows(async () => ({ total: 3, rows: [] }))).length).toBe(0);
  });
});

describe("the store: reread, overtaken reads, move", () => {
  let answers: ((v: Response) => void)[] = [];
  const sent: { method: string; path: string; body: unknown }[] = [];
  const json = (v: unknown) => new Response(JSON.stringify(v), { status: 200 });

  beforeEach(() => {
    answers = [];
    sent.length = 0;
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      const p = new URL(url).pathname.replace(/^\/api\/v1/, "");
      sent.push({ method: init?.method ?? "GET", path: p, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (p === "/catalog/folders") return Promise.resolve(json({ folders: [] }));
      if (p === "/catalog/move") return Promise.resolve(json({ moved: 2 }));
      return new Promise<Response>((resolve) => answers.push(resolve)); // /catalog: answered by the test
    });
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    projectStore.set({ current: { uid: "p1", name: "П" } as ProjectInfo });
    catalogStore.set({ rows: [], folders: [], loaded: false, error: null });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setCoreTarget(null);
    projectStore.set({ current: null });
  });

  it("an overtaken read is dropped and its caller waits for the newest", async () => {
    const first = loadCatalog();
    const second = loadCatalog();
    await vi.waitFor(() => expect(answers.length).toBe(2));
    let firstDone = false;
    void first.then(() => (firstDone = true));
    answers[0](json({ total: 1, rows: [{ id: 1, kind: "dtm", name: "старое" }] }));
    await new Promise((r) => setTimeout(r, 10));
    expect(firstDone).toBe(false); // waits for the newest
    expect(catalogStore.get().loaded).toBe(false); // the old answer is not shown
    answers[1](json({ total: 1, rows: [{ id: 1, kind: "dtm", name: "новое" }] }));
    await first;
    await second;
    expect(catalogStore.get().rows.map((r) => r.name)).toEqual(["новое"]);
  });

  it("an unchanged answer keeps the lists; an error is told in Russian", async () => {
    const a = loadCatalog();
    await vi.waitFor(() => expect(answers.length).toBe(1));
    answers[0](json({ total: 1, rows: [{ id: 1, kind: "dtm" }] }));
    await a;
    const before = catalogStore.get().rows;
    const b = loadCatalog();
    await vi.waitFor(() => expect(answers.length).toBe(2));
    answers[1](json({ total: 1, rows: [{ id: 1, kind: "dtm" }] }));
    await b;
    expect(catalogStore.get().rows).toBe(before);
    const c = loadCatalog();
    await vi.waitFor(() => expect(answers.length).toBe(3));
    answers[2](new Response(JSON.stringify({ code: "catalog_failed", message: "Каталог не прочитан.", hint: "Повторите." }), { status: 500 }));
    await c;
    expect(catalogStore.get().error).toBe("Каталог не прочитан. Повторите.");
  });

  it("without a project the catalog is empty and nothing is asked", async () => {
    projectStore.set({ current: null });
    await loadCatalog();
    expect(sent).toEqual([]);
    expect(catalogStore.get()).toEqual({ rows: [], folders: [], loaded: false, error: null });
  });

  it("a move is one request with the normalised folder, then the catalog is reread", async () => {
    const m = moveToFolder([2, 3], "/Участок 2/");
    await vi.waitFor(() => expect(answers.length).toBe(1));
    answers[0](json({ total: 0, rows: [] }));
    expect(await m).toBe(2);
    expect(sent[0]).toEqual({ method: "POST", path: "/catalog/move", body: { ids: [2, 3], folder: "Участок 2" } });
    expect(sent.filter((s) => s.path === "/catalog").length).toBe(1);
  });
});
