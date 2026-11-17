import { describe, expect, it, vi } from "vitest";

const listeners: ((job: unknown) => void)[] = [];
vi.mock("../src/modules/jobs/state", () => ({
  STATUS_TITLES: { done: "Готово", failed: "Ошибка", cancelled: "Отменено" },
  onJobFinished: (fn: (job: unknown) => void) => {
    listeners.push(fn);
    return () => listeners.splice(listeners.indexOf(fn), 1);
  },
  jobLog: async () => "12:00:01 [error] Traceback: ValueError",
}));

const { followJob } = await import("../src/modules/jobs/follow");
const { logOfJob, logText, startOpLog } = await import("../src/shell/opLog");

describe("a window log follows its job to the end (ТЗ 7.9)", () => {
  it("writes the error with the job log, turns red and is found from the job", async () => {
    const log = startOpLog("Импорт скважин", "import");
    followJob(log, { source: "p1", id: 5 });
    expect(logOfJob({ source: "p1", id: 5 })).toBe(log.id);
    expect(logOfJob({ source: "p1", id: 6 })).toBeUndefined();
    for (const fn of [...listeners]) fn({ source: "p1", id: 4, status: "done" }); // another job: ignored
    expect(log.failed).toBe(false);
    for (const fn of [...listeners]) fn({ source: "p1", id: 5, status: "failed", error: { code: "job_failed", message: "Задание завершилось с ошибкой.", hint: "Откройте журнал." }, result: null });
    await vi.waitFor(() => expect(log.failed).toBe(true));
    const text = logText(log);
    expect(text).toContain("Задание №5: Задание завершилось с ошибкой.");
    expect(text).toContain("detail: код job_failed");
    expect(text).toContain("Traceback: ValueError");
    expect(listeners).toHaveLength(0);
  });
});
