import { afterEach, describe, expect, it, vi } from "vitest";
import { createLog, logForJob, recentLogs, renderJobOnly, resetTrace, tracedFetch } from "./opLog";

afterEach(() => {
  vi.unstubAllGlobals();
  resetTrace();
});

describe("journal of an import or export", () => {
  it("joins the session list with its first entry only", () => {
    const before = recentLogs().length;
    const log = createLog("import", "Пустой");
    expect(recentLogs().length).toBe(before);
    log.open({ a: 1 });
    log.open({ a: 1 });
    expect(recentLogs()[0]).toBe(log);
    expect(log.entries.filter((e) => e.text.startsWith("Окно открыто")).length).toBe(1);
  });

  it("takes the requests made while open, with the failure's answer", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => (url.includes("bad")
      ? new Response(JSON.stringify({ detail: "Колонка X не найдена" }), { status: 422, headers: { "content-type": "application/json" } })
      : new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "content-type": "application/json" } }))));
    await tracedFetch("/api/before");
    const log = createLog("import", "Импорт скважин", { "Проект": 7 });
    log.info("шаг 1");
    await tracedFetch("/api/import/validate", { method: "POST", body: JSON.stringify({ file_id: "f1" }) });
    await tracedFetch("/api/import/bad", { method: "POST" });
    log.error("не записан", new Error("Колонка X не найдена"));
    await new Promise((r) => setTimeout(r, 0));
    log.close();
    await tracedFetch("/api/after");

    const text = log.text();
    expect(text).toContain("Проект: 7");
    expect(text).toContain("POST /api/import/validate → 200");
    expect(text).toContain('запрос: {"file_id":"f1"}');
    expect(text).toContain("POST /api/import/bad → 422");
    expect(text).toContain("Колонка X не найдена");
    expect(text).toContain("-- Запросы с ошибкой");
    expect(text).not.toContain("/api/before");
    expect(text).not.toContain("/api/after");
    expect(log.hasErrors).toBe(true);
    expect(log.fileName()).toMatch(/^bedrockforge-import-\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d\.txt$/);
  });

  it("records a network failure", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    const log = createLog("export", "Экспорт");
    log.info("старт");
    await expect(tracedFetch("/api/lines/export?ids=1")).rejects.toThrow();
    expect(log.text()).toContain("сбой сети (TypeError: Failed to fetch)");
  });

  it("adds the server journals of its jobs", () => {
    const log = createLog("import", "Импорт файлов");
    log.attachJobs([41]);
    expect(logForJob(41)).toBe(log);
    const job = {
      id: 41, kind: "import.wireframes", title: "Каркасы", status: "failed", error: "DXF повреждён", params: { uploads: ["u1"] },
      log: [{ t: new Date().toISOString(), level: "error", text: "Traceback\n  line 1" }],
    };
    const text = log.text([job]);
    expect(text).toContain("Фоновое задание №41 (журнал сервера)");
    expect(text).toContain("Ошибка: DXF повреждён");
    expect(text).toContain("Traceback");
    expect(renderJobOnly(job)).toContain("журнал фонового задания №41");
  });
});
