/**
 * The exchange of variogram models (ТЗ 8.6.4; ADR 0022 §12 «Обмен», §13; e16-api.md `export`, `import`): JSON (the
 * fields of `meta` without `inputs`) and the parameters of GSLIB (`nst c0` / `it cc ang1 ang2 ang3` / `a1 a2 a3`).
 * The file is chosen in the dialogs of the application (`bridge().pickSave` / `pickFiles`, as the neighbouring
 * modules do); without the application (a browser, a test) — in our own window for the path (`askText`), never
 * window.prompt. Then the commands of the registry write and read it (`variography.export` with `path`,
 * `variography.import`): the core writes the file (UTF-8; GSLIB — ASCII, «\n») and reads it (≤ 1 МБ; `bad_file
 * {line}` with the hint). No React.
 */
import { bridge } from "../../bridge";
import { askText } from "../../shell/dialogs";
import type { ExchangeFormat } from "./api";

export const FORMAT_TITLES: Record<ExchangeFormat, string> = { json: "JSON", gslib: "GSLIB (.par)" };
export const FORMAT_EXT: Record<ExchangeFormat, string> = { json: ".json", gslib: ".par" };

/** The file name of a model: its name without the characters Windows forbids, and the extension of the format. */
export function exchangeFileName(name: string, format: ExchangeFormat): string {
  const base = name.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").replace(/[. ]+$/, "").trim() || "Модель вариограммы";
  return `${base}${FORMAT_EXT[format]}`;
}

export interface PathDeps {
  pickSave?: (name: string) => Promise<string | null>;
  pickOpen?: () => Promise<string | null>;
  ask?: typeof askText;
}

function appSave(): ((name: string) => Promise<string | null>) | null {
  const b = bridge();
  return b ? (name) => b.pickSave(name, "any") : null;
}

function appOpen(): (() => Promise<string | null>) | null {
  const b = bridge();
  return b ? async () => (await b.pickFiles("objects", false))[0] ?? null : null;
}

/** The path of the file to write (the dialog of the application; without it — our own window); null — cancelled. */
export async function askExportPath(name: string, format: ExchangeFormat, deps: PathDeps = {}): Promise<string | null> {
  const file = exchangeFileName(name, format);
  const pick = deps.pickSave ?? appSave();
  if (pick) return (await pick(file))?.trim() || null;
  const got = await (deps.ask ?? askText)({ title: `Экспорт модели: ${FORMAT_TITLES[format]}`, label: "Полный путь файла (кириллица допустима)", initial: file, okLabel: "Экспорт" });
  return got?.trim() || null;
}

/** The path of the file to read; null — cancelled. */
export async function askImportPath(deps: PathDeps = {}): Promise<string | null> {
  const pick = deps.pickOpen ?? appOpen();
  if (pick) return (await pick())?.trim() || null;
  const got = await (deps.ask ?? askText)({ title: "Импорт модели вариограммы", label: "Полный путь файла JSON или GSLIB (.par)", okLabel: "Импорт" });
  return got?.trim() || null;
}

type Run = (id: string, args: Record<string, unknown>) => unknown;

/** «Экспорт…» of a stored model: the file is chosen, then `variography.export` writes it. */
export async function exportFlow(run: Run, id: number, name: string, format: ExchangeFormat, deps: PathDeps = {}): Promise<boolean> {
  const path = await askExportPath(name, format, deps);
  if (!path) return false;
  await run("variography.export", { model_id: id, format, path });
  return true;
}

/** «Импорт…»: the file is chosen, then `variography.import` opens it as the working model of a new window. */
export async function importFlow(run: Run, deps: PathDeps = {}): Promise<boolean> {
  const path = await askImportPath(deps);
  if (!path) return false;
  await run("variography.import", { path });
  return true;
}
