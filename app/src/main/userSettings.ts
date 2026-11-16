// %APPDATA%\BedrockForge\settings.json: read once, written atomically (temp file + rename).
import fs from "node:fs";
import path from "node:path";

export const MAX_SETTINGS_BYTES = 2 * 1024 * 1024;

/** Parsed object, null when there is no file yet, "damaged" when it cannot be read as JSON. */
export function readSettings(file: string): unknown {
  let text: string;
  try {
    text = fs.readFileSync(file, "utf8");
  } catch {
    return null;
  }
  try {
    const data = JSON.parse(text) as unknown;
    return data && typeof data === "object" && !Array.isArray(data) ? data : "damaged";
  } catch {
    return "damaged";
  }
}

export function writeSettings(file: string, data: unknown): boolean {
  const text = JSON.stringify(data, null, 2);
  if (text === undefined || text.length > MAX_SETTINGS_BYTES) return false;
  const tmp = `${file}.${process.pid}.tmp`;
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(tmp, text, "utf8");
    fs.renameSync(tmp, file);
    return true;
  } catch {
    try {
      fs.rmSync(tmp, { force: true });
    } catch {
      /* ignore */
    }
    return false;
  }
}

/** Merges the window part (owned by the main process) into the file without touching the rest. */
export function mergeSettings(file: string, patch: Record<string, unknown>): void {
  const cur = readSettings(file);
  const base = cur && typeof cur === "object" ? (cur as Record<string, unknown>) : {};
  writeSettings(file, { ...base, ...patch });
}
