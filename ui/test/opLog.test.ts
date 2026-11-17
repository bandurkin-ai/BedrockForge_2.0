import { describe, expect, it } from "vitest";
import { call, setCoreTarget } from "../src/shell/api";
import { LOG_LIMIT, logError, logFileName, logText, opLogStore, startOpLog, stopOpLog } from "../src/shell/opLog";

describe("operation logs (ТЗ 7.9)", () => {
  it("records environment, requests while active, hides passwords, keeps detail and trace", async () => {
    const log = startOpLog("Импорт скважин", "import", { uiVersion: "0.1.0", coreVersion: "0.1.0", project: "Демо" });
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    const f = (async () =>
      new Response(JSON.stringify({ code: "internal", message: "Внутренняя ошибка ядра.", hint: "", detail: "KeyError: 'x'", trace: "Traceback…" }), { status: 500 })) as unknown as typeof fetch;
    const err = (await call("POST", "/projects/import", { path: "a.bfggis", password: "секрет" }, f).catch((e: unknown) => e)) as { detail?: string; trace?: string; message: string };
    logError(log, err.message, err);
    stopOpLog(log);
    await call("GET", "/health", undefined, (async () => new Response("{}")) as unknown as typeof fetch);
    setCoreTarget(null);
    const text = logText(log);
    expect(text).toContain("Версии: интерфейс 0.1.0, ядро 0.1.0");
    expect(text).toContain("POST /projects/import");
    expect(text).toContain("→ 500");
    expect(text).toContain("detail: KeyError: 'x'");
    expect(text).not.toContain("секрет");
    expect(text).not.toContain("/health"); // after stop the window's log records nothing
    expect(log.failed).toBe(true);
  });

  it("file name by the template and only the last 30 logs", () => {
    const log = startOpLog("Экспорт", "export");
    log.started = new Date(2026, 10, 17, 9, 5, 7);
    expect(logFileName(log)).toBe("bedrockforge-export-2026-11-17_09-05-07.txt");
    for (let i = 0; i < 40; i++) stopOpLog(startOpLog(`№${i}`, "import"));
    expect(opLogStore.get().logs).toHaveLength(LOG_LIMIT);
    expect(opLogStore.get().logs[0].title).toBe("№39");
  });
});
