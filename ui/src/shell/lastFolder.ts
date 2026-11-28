/**
 * «Последняя папка» диалога выбора файлов (Э27, 27.5, Д-42 А3). The system dialog opened where it chose each time; now
 * the next opening of a wizard starts in the folder of the file chosen last in that wizard, and a wizard of the same
 * kind of files (a new one, or another window) — in the folder chosen last for that kind. The memory is the session's:
 * it lives in the window of the interface, nothing is written to the settings file.
 */
import { bridge, type PickKind } from "../bridge";

const byScope = new Map<string, string>();
const byKind = new Map<string, string>();

/** The folder of a file: both separators are known (a path of Windows comes from the shell as it is); null — no folder in it. */
export function folderOf(file: string): string | null {
  const i = Math.max(file.lastIndexOf("/"), file.lastIndexOf("\\"));
  if (i < 0) return null;
  const head = file.slice(0, i);
  if (i === 0) return file.slice(0, 1); // a file in the root of a disk of unix
  return /^[A-Za-z]:$/.test(head) ? file.slice(0, i + 1) : head; // `C:\file` — `C:\`, not `C:` (that is the current folder of a disk)
}

/** The folder to open: the last one of this window and kind, else the last one of the kind; undefined — the system chooses. */
export function lastFolder(kind: string, scope?: string): string | undefined {
  return (scope ? byScope.get(`${scope}\u0000${kind}`) : undefined) ?? byKind.get(kind);
}

/** The files chosen: the folder of the last of them is remembered. An empty choice (cancelled) changes nothing. */
export function rememberFolder(kind: string, files: readonly string[], scope?: string): void {
  const dir = files.length ? folderOf(files[files.length - 1]) : null;
  if (!dir) return;
  byKind.set(kind, dir);
  if (scope) byScope.set(`${scope}\u0000${kind}`, dir);
}

/** For tests and «new project»: forgets all the folders. */
export function resetLastFolders(): void {
  byScope.clear();
  byKind.clear();
}

/** The open dialog of the shell that starts in the last folder. `scope` — a window (a wizard), when the folder is to be
 * kept per window. Without the shell (a browser) — []. The third argument is not sent until a folder is known. */
export async function pickFilesIn(kind: PickKind, multi: boolean, scope?: string): Promise<string[]> {
  const b = bridge();
  if (!b) return [];
  const dir = lastFolder(kind, scope);
  const files = (dir ? await b.pickFiles(kind, multi, dir) : await b.pickFiles(kind, multi)) ?? [];
  rememberFolder(kind, files, scope);
  return files;
}
