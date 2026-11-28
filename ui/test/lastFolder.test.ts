/** «Последняя папка» диалога выбора файлов (Э27, 27.5, Д-42 А3): the next opening of a wizard starts in the folder of
 * the file chosen last in it; another wizard of the same kind of files — in the folder chosen last for that kind. */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createApp } from "../src/shell/app";
import { folderOf, lastFolder, pickFilesIn, rememberFolder, resetLastFolders } from "../src/shell/lastFolder";
import { windowStore } from "../src/shell/windows";
import { projectStore, type ProjectInfo } from "../src/modules/projects/state";

beforeEach(() => {
  resetLastFolders();
  vi.unstubAllGlobals();
});

describe("folderOf", () => {
  it("both separators; a disk root keeps its slash; no folder — null", () => {
    expect(folderOf("/data/geo/holes.csv")).toBe("/data/geo");
    expect(folderOf("D:\\Проекты\\Карьер\\holes.csv")).toBe("D:\\Проекты\\Карьер");
    expect(folderOf("C:\\holes.csv")).toBe("C:\\");
    expect(folderOf("/holes.csv")).toBe("/");
    expect(folderOf("\\\\srv\\share\\a.csv")).toBe("\\\\srv\\share");
    expect(folderOf("holes.csv")).toBeNull();
  });
});

describe("the memory of folders", () => {
  it("a window first, then the kind of files; a cancelled dialog changes nothing", () => {
    expect(lastFolder("tables", "w1")).toBeUndefined();
    rememberFolder("tables", ["/a/one.csv", "/a/b/two.csv"], "w1");
    expect(lastFolder("tables", "w1")).toBe("/a/b"); // the last chosen file
    expect(lastFolder("tables", "w2")).toBe("/a/b"); // another wizard: by the kind of files
    expect(lastFolder("las", "w1")).toBeUndefined(); // another kind: nothing
    rememberFolder("tables", ["/c/x.csv"], "w2");
    expect(lastFolder("tables", "w1")).toBe("/a/b"); // within w1 its own folder stays
    expect(lastFolder("tables", "w2")).toBe("/c");
    expect(lastFolder("tables")).toBe("/c"); // without a window: the last one of the kind
    rememberFolder("tables", [], "w1");
    expect(lastFolder("tables", "w1")).toBe("/a/b");
  });
});

describe("pickFilesIn", () => {
  it("the first opening is without a folder, every next one — in the folder of the last chosen file", async () => {
    const pickFiles = vi.fn(async (_kind: string, _multi: boolean, _dir?: string) => ["/d/one/a.csv", "/d/two/b.csv"]);
    vi.stubGlobal("window", { bf: { pickFiles } });
    expect(await pickFilesIn("tables", true, "w1")).toEqual(["/d/one/a.csv", "/d/two/b.csv"]);
    expect(pickFiles).toHaveBeenLastCalledWith("tables", true);
    await pickFilesIn("tables", true, "w1");
    expect(pickFiles).toHaveBeenLastCalledWith("tables", true, "/d/two");
    await pickFilesIn("tables", true, "w2"); // another wizard — the folder of the kind
    expect(pickFiles).toHaveBeenLastCalledWith("tables", true, "/d/two");
    await pickFilesIn("prj", false, "w1"); // another kind — the system decides
    expect(pickFiles).toHaveBeenLastCalledWith("prj", false);
    pickFiles.mockResolvedValueOnce([]); // cancelled: nothing changes
    await pickFilesIn("tables", true, "w1");
    pickFiles.mockResolvedValueOnce(["/e/c.csv"]);
    await pickFilesIn("tables", true, "w3"); // another wizard moves the kind, not w1
    await pickFilesIn("tables", true, "w1");
    expect(pickFiles).toHaveBeenLastCalledWith("tables", true, "/d/two");
  });

  it("without the shell: no files", async () => {
    expect(await pickFilesIn("tables", true)).toEqual([]);
  });
});

describe("the commands of import open the dialog in the last folder", () => {
  it("points.import: the second opening starts where the first file was chosen", async () => {
    const pickFiles = vi.fn(async (_kind: string, _multi: boolean, _dir?: string) => ["/survey/2026/cloud.las"]);
    const app = createApp({ persist: false });
    vi.stubGlobal("window", { bf: { pickFiles }, innerWidth: 1280, innerHeight: 800 });
    projectStore.set({ current: { uid: "p1" } as ProjectInfo });
    app.store.set({ projectName: "Проект" });
    expect((await app.registry.execute("points.import", { mode: "las" })).ok).toBe(true);
    expect(pickFiles).toHaveBeenLastCalledWith("las", true);
    expect((await app.registry.execute("points.import", { mode: "las" })).ok).toBe(true);
    expect(pickFiles).toHaveBeenLastCalledWith("las", true, "/survey/2026");
    expect(windowStore.get().windows.length).toBeGreaterThan(0);
  });
});
