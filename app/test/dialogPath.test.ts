import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { LastFolders, safeDefaultPath } from "../src/main/dialogPath";

describe("defaultPath of a dialog (Э27, 27.5)", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-dialog-"));
  const file = path.join(dir, "a.csv");
  fs.writeFileSync(file, "x");

  it("an absolute path of an existing folder passes", () => {
    expect(safeDefaultPath(dir)).toBe(dir);
  });
  it("anything else is dropped silently", () => {
    expect(safeDefaultPath(undefined)).toBeUndefined();
    expect(safeDefaultPath(42)).toBeUndefined();
    expect(safeDefaultPath("")).toBeUndefined();
    expect(safeDefaultPath("relative/dir")).toBeUndefined();
    expect(safeDefaultPath(path.join(dir, "gone"))).toBeUndefined(); // the folder is not there
    expect(safeDefaultPath(file)).toBeUndefined(); // a file is not a folder
    expect(safeDefaultPath(dir + "\u0000x")).toBeUndefined();
    expect(safeDefaultPath(dir + "\nx")).toBeUndefined();
    expect(safeDefaultPath("/" + "a".repeat(1100))).toBeUndefined();
  });
  it("a network path or a device is dropped before the disk is touched (Windows and POSIX)", () => {
    let touched = 0;
    const isDir = () => (touched++, true);
    for (const raw of ["\\\\server\\share", "//server/share", "\\\\?\\C:\\x", "\\\\.\\PhysicalDrive0", "//?/C:/x", "\\x", "C:", "C:x", "relative\\dir"]) {
      expect(safeDefaultPath(raw, isDir, "win32"), raw).toBeUndefined();
    }
    expect(safeDefaultPath("//server/share", isDir, "linux")).toBeUndefined();
    expect(safeDefaultPath("\\\\server\\share", isDir, "linux")).toBeUndefined();
    expect(touched).toBe(0);
    expect(safeDefaultPath("C:\\Data\\Проект", isDir, "win32")).toBe("C:\\Data\\Проект");
    expect(safeDefaultPath("d:/data", isDir, "win32")).toBe("d:/data");
    expect(safeDefaultPath("/home/u", isDir, "linux")).toBe("/home/u");
    expect(touched).toBe(3);
    expect(safeDefaultPath("/home/u", isDir, "win32")).toBeUndefined(); // a root slash is not a path of Windows here
  });
  it("the check of the folder can be replaced", () => {
    expect(safeDefaultPath("/x/y", () => true)).toBe("/x/y");
    expect(safeDefaultPath("/x/y", () => false)).toBeUndefined();
  });
});

describe("LastFolders (save and folder dialogs)", () => {
  it("remembers the folder of a file per kind, forgets a folder that is gone", () => {
    const alive = new Set(["/out/a", "/out/b"]);
    const last = new LastFolders((p) => alive.has(p));
    expect(last.get("save:xlsx")).toBeUndefined();
    last.rememberFile("save:xlsx", "/out/a/report.xlsx");
    last.rememberFile("save:dat", "/out/b/t.dat");
    expect(last.get("save:xlsx")).toBe("/out/a");
    expect(last.get("save:dat")).toBe("/out/b");
    alive.delete("/out/a");
    expect(last.get("save:xlsx")).toBeUndefined();
    last.remember("folder", "relative");
    expect(last.get("folder")).toBeUndefined();
  });
});
