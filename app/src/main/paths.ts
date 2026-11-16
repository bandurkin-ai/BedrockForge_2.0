// Where the interface and the core live in dev and packaged builds.
import path from "node:path";
import fs from "node:fs";
import type { CoreCommand } from "./core";

export function uiRoot(isPackaged: boolean, resourcesPath: string, appDir: string): string {
  return process.env.BF_UI_DIR ?? (isPackaged ? path.join(resourcesPath, "ui") : path.join(appDir, "..", "ui", "dist"));
}

export function coreCommand(isPackaged: boolean, resourcesPath: string, appDir: string): CoreCommand {
  const exe = process.platform === "win32" ? "bf-core.exe" : "bf-core";
  const override = process.env.BF_CORE_EXE;
  if (override) return { file: override, args: [], cwd: path.dirname(override) };
  if (isPackaged) {
    const dir = path.join(resourcesPath, "bf-core");
    return { file: path.join(dir, exe), args: [], cwd: dir };
  }
  const coreDir = path.join(appDir, "..", "core");
  const venvPy = process.platform === "win32" ? path.join(coreDir, ".venv", "Scripts", "python.exe") : path.join(coreDir, ".venv", "bin", "python");
  const python = process.env.BF_CORE_PYTHON ?? (fs.existsSync(venvPy) ? venvPy : "python");
  return { file: python, args: ["-m", "bf_core"], cwd: coreDir };
}
