import { afterEach, describe, expect, it, vi } from "vitest";
import { setCoreTarget } from "../src/shell/api";
import { createApp } from "../src/shell/app";
import { moduleCommands } from "../src/shell/catalog";
import { findOda, odaLines, pushOdaPath, setOdaPath, syncOda, type OdaStatus } from "../src/shell/odaTool";
import { normalizeOdaPath, normalizeSettings } from "../src/shell/settings";
import { INITIAL_STATE, Store, type AppState } from "../src/shell/store";
import { closeWindow, windowStore } from "../src/shell/windows";

const EXE = "C:\\Program Files\\ODA\\ODAFileConverter 26.10.0\\ODAFileConverter.exe";
const INSTALL = "ODA File Converter — бесплатная программа Open Design Alliance.";

const status = (over: Partial<OdaStatus> = {}): OdaStatus => ({
  configured: null,
  found: false,
  path: null,
  source: null,
  auto: null,
  version: null,
  message: "ODA File Converter не найден.",
  warning: "",
  hint: `${INSTALL} Без неё чертежи DWG не читаются и не записываются: сохраните чертёж в DXF.`,
  install: INSTALL,
  ...over,
});
const found = (over: Partial<OdaStatus> = {}) =>
  status({ configured: EXE, found: true, path: EXE, source: "configured", version: "26.10.0", message: "ODA File Converter найден, версия 26.10.0.", hint: "", ...over });

/** The core in place of `fetch`: every request is recorded; the answer is what the core would say for the path. */
function core(answer: (method: string, path: string | null | undefined) => Response | OdaStatus) {
  const calls: { method: string; url: string; body: unknown }[] = [];
  const f = vi.fn(async (url: string, init: RequestInit) => {
    const body = init.body ? (JSON.parse(String(init.body)) as { path?: string | null }) : undefined;
    calls.push({ method: String(init.method), url, body });
    const a = answer(String(init.method), body?.path);
    return a instanceof Response ? a : new Response(JSON.stringify(a), { status: 200 });
  });
  vi.stubGlobal("fetch", f);
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  return calls;
}

const store = (odaConverter: string | null = null) => new Store<AppState>({ ...INITIAL_STATE, odaConverter });

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
  for (const w of windowStore.get().windows) closeWindow(w.id);
});

describe("«Настройка › Внешние программы»: ODA File Converter (ADR 0013 §5–§6, ТЗ 7.35 Д-13)", () => {
  it("the path of the settings: spaces and quotes around it go, junk is no path", () => {
    expect(normalizeOdaPath(`  "${EXE}"  `)).toBe(EXE);
    expect(normalizeOdaPath("/opt/ODA File Converter/ODAFileConverter")).toBe("/opt/ODA File Converter/ODAFileConverter");
    expect(normalizeOdaPath("D:\\Программы\\ODA")).toBe("D:\\Программы\\ODA");
    for (const junk of ["", "   ", '""', null, undefined, 5, {}, ["C:\\x"], "C:\\a\nb", "C:\\a\u0000b", "x".repeat(2001)]) expect(normalizeOdaPath(junk)).toBeNull();
    expect(normalizeOdaPath("x".repeat(2000))).toHaveLength(2000);
  });

  it("the settings file keeps the path; a file of an older version has none", () => {
    expect(normalizeSettings({ odaConverter: ` ${EXE} ` }).odaConverter).toBe(EXE);
    expect(normalizeSettings({ heavyWorkers: 2 })).toMatchObject({ odaConverter: null, heavyWorkers: 2 });
    expect(normalizeSettings({ odaConverter: 12 }).odaConverter).toBeNull();
    expect(normalizeSettings("damaged").odaConverter).toBeNull();
  });

  it("the lines of the state: found, found by the search, a stale path, nothing", () => {
    expect(odaLines(found())).toEqual([
      { tone: "ok", text: "ODA File Converter найден, версия 26.10.0." },
      { tone: "plain", text: `Программа: ${EXE}` },
    ]);
    expect(odaLines(found({ configured: null, source: "auto", auto: EXE }))[1]).toEqual({ tone: "plain", text: `Найдена автоматически: ${EXE}` });
    const stale = found({ configured: "D:\\old\\ODAFileConverter.exe", source: "auto", auto: EXE, warning: `По указанному пути программы нет. Будет использована найденная: ${EXE}` });
    expect(odaLines(stale).map((l) => l.tone)).toEqual(["warn", "plain", "warn"]);
    expect(odaLines(stale)[2].text).toContain("По указанному пути программы нет");
    // nothing found: the state only — how to install is the block under it
    expect(odaLines(status())).toEqual([{ tone: "bad", text: "ODA File Converter не найден." }]);
  });

  it("«Проверить»: the path goes to the core and, once taken, into the settings", async () => {
    const calls = core((_m, path) => found({ configured: path ?? null, path: path ?? null }));
    const s = store();
    const r = await setOdaPath(s, `"${EXE}"`);
    expect(calls).toEqual([{ method: "PUT", url: "http://127.0.0.1:1/api/v1/tools/oda", body: { path: EXE } }]);
    expect(r.found).toBe(true);
    expect(s.get().odaConverter).toBe(EXE);
  });

  it("a path the core refuses stays out of the settings; the error keeps the Russian hint", async () => {
    core(() => new Response(JSON.stringify({ code: "bad_path", message: "Путь к ODA File Converter указан неверно.", hint: "Укажите полный путь." }), { status: 422 }));
    const s = store(EXE);
    const err = await setOdaPath(s, "ODAFileConverter.exe").catch((e: unknown) => e);
    expect((err as { full: string }).full).toBe("Путь к ODA File Converter указан неверно. Укажите полный путь.");
    expect(s.get().odaConverter).toBe(EXE);
  });

  it("«Очистить»: no path in the core and in the settings", async () => {
    const calls = core(() => status());
    const s = store(EXE);
    expect((await setOdaPath(s, "  ")).configured).toBeNull();
    expect(calls[0].body).toEqual({ path: null });
    expect(s.get().odaConverter).toBeNull();
  });

  it("«Найти»: what the search of the core finds becomes the path; nothing found changes nothing", async () => {
    let calls = core((m, path) => (m === "GET" ? status({ auto: EXE, found: true, path: EXE, source: "auto" }) : found({ configured: path ?? null })));
    const s = store();
    expect((await findOda(s)).configured).toBe(EXE);
    expect(calls.map((c) => c.method)).toEqual(["GET", "PUT"]);
    expect(s.get().odaConverter).toBe(EXE);

    calls = core(() => status({ configured: "D:\\old" }));
    const kept = store("D:\\old");
    const none = await findOda(kept);
    expect(none.found).toBe(false);
    expect(calls.map((c) => c.method)).toEqual(["GET"]);
    expect(kept.get().odaConverter).toBe("D:\\old");
  });

  it("the tab opens with the core holding what the settings hold; at the start of the core only a set path is sent", async () => {
    let calls = core((_m, path) => status({ configured: path ?? null }));
    await syncOda(store());
    expect(calls).toEqual([{ method: "PUT", url: "http://127.0.0.1:1/api/v1/tools/oda", body: { path: null } }]);

    calls = core((_m, path) => found({ configured: path ?? null }));
    expect(await pushOdaPath(store())).toBeNull();
    expect(calls).toEqual([]);
    expect((await pushOdaPath(store(EXE)))?.found).toBe(true);
    expect(calls).toEqual([{ method: "PUT", url: "http://127.0.0.1:1/api/v1/tools/oda", body: { path: EXE } }]);
    // the core is down or refuses: the start goes on
    core(() => new Response("{}", { status: 500 }));
    expect(await pushOdaPath(store(EXE))).toBeNull();
  });

  it("the command `settings.open {tab: \"tools\"}` is in the registry and brings the one window to the tab", async () => {
    const cmd = moduleCommands().find((c) => c.id === "settings.open")!;
    expect(cmd.params?.tab).toMatchObject({ enum: ["shortcuts", "general", "tools"] });
    expect(cmd.description).toContain("внешние программы");
    const app = createApp({ persist: false });
    await app.run("settings.open");
    await app.run("settings.open", { tab: "tools" });
    const wins = windowStore.get().windows.filter((w) => w.kind === "settings");
    expect(wins).toHaveLength(1);
    expect(wins[0].props?.tab).toBe("tools");
    const asked = wins[0].props?.asked;
    await app.run("settings.open"); // the ribbon button again: the window comes to the front, the tab stays
    expect(windowStore.get().windows.filter((w) => w.kind === "settings")[0].props).toMatchObject({ tab: "tools", asked });
    expect((await app.registry.execute("settings.open", { tab: "nope" })).ok).toBe(false);
  });
});
