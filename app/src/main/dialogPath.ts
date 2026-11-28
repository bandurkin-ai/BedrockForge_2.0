// The folder a system dialog opens in (Э27, 27.5): the interface may offer the folder of the last chosen file;
// the main process trusts nothing it is sent, so the path is checked here, and a folder that is gone is dropped silently.
import fs from "node:fs";
import path from "node:path";

export const DIALOG_PATH_MAX = 1000;

/** True when the path is an existing directory. */
export type IsDir = (p: string) => boolean;

const fsIsDir: IsDir = (p) => {
  try {
    return fs.statSync(p).isDirectory();
  } catch {
    return false;
  }
};

/**
 * A path with a drive letter (Windows) or a root slash (POSIX) and nothing else: a network path (`\\host\share`, `//host`)
 * or a device path (`\\?\`, `\\.\`) is not one — `fs.statSync` on it would reach out to the network or open a device.
 */
export function isLocalAbsolute(raw: string, platform: NodeJS.Platform = process.platform): boolean {
  if (platform === "win32") return /^[A-Za-z]:[\\/]/.test(raw);
  return raw.startsWith("/") && !raw.startsWith("//");
}

/** `defaultPath` of a dialog: an absolute local path of an existing folder with no control characters; otherwise
 * `undefined` (the dialog opens where the system chooses — no error, nothing to tell the user). The shape of the path is
 * checked before the disk is touched. */
export function safeDefaultPath(raw: unknown, isDir: IsDir = fsIsDir, platform: NodeJS.Platform = process.platform): string | undefined {
  if (typeof raw !== "string" || !raw || raw.length > DIALOG_PATH_MAX) return undefined;
  if (/[\u0000-\u001f\u007f]/.test(raw)) return undefined;
  if (!isLocalAbsolute(raw, platform)) return undefined;
  return isDir(raw) ? raw : undefined;
}

/** The folder of the file a save or folder dialog returned, per kind of file — in memory for the session. */
export class LastFolders {
  private readonly dirs = new Map<string, string>();

  constructor(private readonly isDir: IsDir = fsIsDir) {}

  /** The remembered folder of a kind, when it still exists. */
  get(kind: string): string | undefined {
    const dir = this.dirs.get(kind);
    return dir !== undefined && this.isDir(dir) ? dir : undefined;
  }

  remember(kind: string, dir: string): void {
    if (safeDefaultPath(dir, () => true)) this.dirs.set(kind, dir);
  }

  /** A file was chosen: its folder is remembered for the kind. */
  rememberFile(kind: string, file: string): void {
    this.remember(kind, path.dirname(file));
  }
}
