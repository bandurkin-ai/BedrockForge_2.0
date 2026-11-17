import { describe, expect, it } from "vitest";
import { clearReason, finishedJobs } from "../src/modules/jobs/state";
import { BOTTOM_TITLES, TREE_SECTIONS } from "../src/shell/workspace";

describe("«Очистить выполненные» (ТЗ 7.35 Д-10)", () => {
  const jobs = [{ status: "done" }, { status: "cancelled" }, { status: "failed" }, { status: "queued" }, { status: "running" }] as const;

  it("counts done and cancelled, failed only by the flag", () => {
    expect(finishedJobs([...jobs], false)).toBe(2);
    expect(finishedJobs([...jobs], true)).toBe(3);
  });

  it("is unavailable with a reason when nothing is finished", () => {
    expect(clearReason([{ status: "queued" }, { status: "running" }], false)).toBe("Нет выполненных заданий");
    expect(clearReason([{ status: "failed" }], false)).toBe("Нет выполненных заданий");
    expect(clearReason([{ status: "failed" }], true)).toBeNull();
    expect(clearReason([], true)).toBe("Нет выполненных заданий");
  });
});

describe("«Аудит и статистика» (ТЗ 7.35 Д-06)", () => {
  it("tree section and bottom tab keep the id «audit»", () => {
    expect(TREE_SECTIONS.audit).toBe("Аудит и статистика");
    expect(BOTTOM_TITLES.audit).toBe("Аудит и статистика");
  });
});
