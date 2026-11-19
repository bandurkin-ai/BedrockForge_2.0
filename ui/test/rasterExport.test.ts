/** The window «Экспорт растра»: files next to the chosen one are replaced only after our own question
 * (Э7 review; the core answers 409 `files_exist` with their names). */
import { describe, expect, it, vi } from "vitest";
import { existingFiles, overwriteQuestion, startWithOverwrite } from "../src/modules/rasters/export";
import { startRasterExport } from "../src/modules/rasters/actions";
import { CoreError, setCoreTarget } from "../src/shell/api";

function refusal(files: unknown): CoreError {
  const e = new CoreError(409, "files_exist", "В папке уже есть файлы, которые экспорт заменит: «карта.jgw».", "Подтвердите замену…");
  e.body = { code: "files_exist", files };
  return e;
}

describe("«Экспорт растра»: files that are there already", () => {
  it("reads the names of a 409 files_exist and nothing of other errors", () => {
    expect(existingFiles(refusal(["карта.jgw", "карта.prj"]))).toEqual(["карта.jgw", "карта.prj"]);
    expect(existingFiles(refusal(["a.tab", 7, "", null]))).toEqual(["a.tab"]);
    expect(existingFiles(refusal([]))).toBeNull();
    expect(existingFiles(refusal(undefined))).toBeNull();
    expect(existingFiles(new CoreError(409, "version_conflict", "…", ""))).toBeNull();
    expect(existingFiles(new Error("сеть"))).toBeNull();
    expect(existingFiles(null)).toBeNull();
  });

  it("asks in Russian with the list of the files", () => {
    const one = overwriteQuestion(["план.tif.jpg"]);
    expect(one).toEqual({ title: "Заменить файлы?", message: "В папке уже есть файл с таким именем. Экспорт заменит его:\n• план.tif.jpg", okLabel: "Заменить", danger: true });
    const two = overwriteQuestion(["карта.jgw", "карта.prj"]);
    expect(two.message).toBe("В папке уже есть файлы с такими именами. Экспорт заменит их:\n• карта.jgw\n• карта.prj");
    const many = overwriteQuestion(Array.from({ length: 15 }, (_, i) => `f${i}.tab`));
    expect(many.message.split("\n")).toHaveLength(14); // the head, twelve names, «и ещё»
    expect(many.message.endsWith("… и ещё 3")).toBe(true);
  });

  it("starts at once when nothing is in the way", async () => {
    const start = vi.fn(async (overwrite: boolean) => ({ id: 5, overwrite }));
    const ask = vi.fn(async () => true);
    expect(await startWithOverwrite(start, ask)).toEqual({ id: 5, overwrite: false });
    expect(start.mock.calls).toEqual([[false]]);
    expect(ask).not.toHaveBeenCalled();
  });

  it("asks once and starts again with the permission", async () => {
    const start = vi.fn(async (overwrite: boolean) => {
      if (!overwrite) throw refusal(["карта.jgw", "карта.prj"]);
      return { id: 6 };
    });
    const ask = vi.fn(async (_files: string[]) => true);
    expect(await startWithOverwrite(start, ask)).toEqual({ id: 6 });
    expect(start.mock.calls).toEqual([[false], [true]]);
    expect(ask.mock.calls).toEqual([[["карта.jgw", "карта.prj"]]]);
  });

  it("starts nothing when the user says no", async () => {
    const start = vi.fn(async (_overwrite: boolean): Promise<{ id: number }> => {
      throw refusal(["карта.jgw"]);
    });
    expect(await startWithOverwrite(start, async () => false)).toBeNull();
    expect(start.mock.calls).toEqual([[false]]);
  });

  it("passes other errors on, the second refusal too", async () => {
    const offline = new CoreError(0, "core_offline", "Ядро недоступно.", "Подождите перезапуска ядра.");
    await expect(startWithOverwrite(async () => Promise.reject(offline), async () => true)).rejects.toBe(offline);
    const busy = new CoreError(409, "file_unwritable", "Файл не записан.", "Закройте файл.");
    const start = vi.fn(async (overwrite: boolean): Promise<never> => {
      throw overwrite ? busy : refusal(["a.jgw"]);
    });
    await expect(startWithOverwrite(start, async () => true)).rejects.toBe(busy);
  });

  it("sends `overwrite` to the core only as asked", async () => {
    const sent: unknown[] = [];
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      sent.push(JSON.parse(String(init?.body)));
      return new Response(JSON.stringify({ source: "project", id: 9 }), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    setCoreTarget({ baseUrl: "http://127.0.0.1:5555", token: "t" });
    try {
      await startRasterExport(3, "C:\\out\\карта.jpg", "source");
      await startRasterExport(3, "C:\\out\\карта.jpg", "source", true);
    } finally {
      setCoreTarget(null);
      vi.unstubAllGlobals();
    }
    expect(sent).toEqual([
      { path: "C:\\out\\карта.jpg", format: "source", overwrite: false },
      { path: "C:\\out\\карта.jpg", format: "source", overwrite: true },
    ]);
    expect(String(fetchMock.mock.calls[0][0])).toContain("/rasters/3/export");
  });
});
