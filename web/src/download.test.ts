import { afterEach, describe, expect, it, vi } from "vitest";
import { downloadFile } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("downloadFile", () => {
  it("turns a server refusal into an error with its text (no browser page opens)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "В выбранных слоях нет линий" }), { status: 422 })));
    await expect(downloadFile("/api/export/lines?format=str&ids=1", "x.STR")).rejects.toThrow("В выбранных слоях нет линий");
  });

  it("takes the file name from Content-Disposition", async () => {
    const clicked: string[] = [];
    const a = { href: "", download: "", click: () => clicked.push(a.download), remove: () => undefined };
    vi.stubGlobal("fetch", vi.fn(async () => new Response("x", { status: 200, headers: { "content-disposition": "attachment; filename*=UTF-8''%D0%9F%D1%80%D0%BE%D0%B1%D0%B0.STR" } })));
    vi.stubGlobal("document", { createElement: () => a, body: { appendChild: () => undefined } });
    vi.stubGlobal("URL", { createObjectURL: () => "blob:x", revokeObjectURL: () => undefined });
    expect(await downloadFile("/api/export/lines?format=str&ids=1", "x.STR")).toBe("Проба.STR");
    expect(clicked).toEqual(["Проба.STR"]);
  });
});
