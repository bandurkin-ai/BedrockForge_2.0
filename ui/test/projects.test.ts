import { describe, expect, it, vi } from "vitest";
import { applyEvent, jobsStore, parseSse, type Job } from "../src/modules/jobs/state";
import { exportFileName, objectCount } from "../src/modules/projects/state";
import { call, CoreError, setCoreTarget } from "../src/shell/api";
import { normalizeSettings } from "../src/shell/settings";

describe("projects and jobs (Э2)", () => {
  it("export file name: «<Название>_ГГГГ-ММ-ДД.bfggis» without forbidden characters", () => {
    expect(exportFileName("Участок: Северный/2", new Date(2026, 10, 6))).toBe("Участок_ Северный_2_2026-11-06.bfggis");
  });

  it("objects of a list row exclude holes and tables", () => {
    expect(objectCount({ holes: 48, assays: 900, wireframes: 2, lines: 3, rasters: 2 })).toBe(7);
  });

  it("the last project uid survives settings normalisation, junk is dropped", () => {
    expect(normalizeSettings({ lastProject: "a".repeat(32) }).lastProject).toBe("a".repeat(32));
    expect(normalizeSettings({ lastProject: "../../x" }).lastProject).toBeNull();
    expect(normalizeSettings({ heavyWorkers: 4, projectsDir: "D:\\Проекты" })).toMatchObject({ heavyWorkers: 4, projectsDir: "D:\\Проекты" });
    expect(normalizeSettings({ heavyWorkers: 0 }).heavyWorkers).toBe(1);
  });

  it("SSE: events split across chunks are kept until complete", () => {
    const a = parseSse('id: 1\nevent: job\ndata: {"id":1}\n\nid: 2\nevent: jo');
    expect(a.events).toEqual([{ event: "job", data: '{"id":1}' }]);
    const b = parseSse(a.rest + 'b\ndata: {"id":2}\n\n: keep-alive\n\n');
    expect(b.events).toEqual([{ event: "job", data: '{"id":2}' }]);
    expect(b.rest).toBe("");
  });

  it("live progress updates a running job; a status change asks for a reload", () => {
    const job = { id: 5, source: "p", status: "running", progress: 0.1, message: null } as Job;
    jobsStore.set({ jobs: [job] });
    expect(applyEvent({ source: "p", id: 5, progress: 0.5, message: "Шаг 5" })).toBe(true);
    expect(jobsStore.get().jobs[0]).toMatchObject({ progress: 0.5, message: "Шаг 5" });
    expect(applyEvent({ source: "p", id: 5, status: "done" })).toBe(false);
    expect(applyEvent({ source: "other", id: 5, progress: 1 })).toBe(false);
  });

  it("core errors keep code, message and hint", async () => {
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    const f = vi.fn(async () => new Response(JSON.stringify({ code: "name_taken", message: "Проект «А» уже есть.", hint: "Выберите другое название." }), { status: 409 }));
    const err = await call("POST", "/projects", { name: "А" }, f as unknown as typeof fetch).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(CoreError);
    expect((err as CoreError).code).toBe("name_taken");
    expect((err as CoreError).full).toBe("Проект «А» уже есть. Выберите другое название.");
    setCoreTarget(null);
  });
});
