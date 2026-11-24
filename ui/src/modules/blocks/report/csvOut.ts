/**
 * «Копировать CSV» / «Сохранить CSV…» of the report «Содержание–тоннаж» (ТЗ 7.31): the table as the core wrote it
 * («;» between the cells, the decimal comma, CRLF) goes to the clipboard or to a file through the save dialog of the
 * shell (a BOM for a spreadsheet is added by the shell). No window.alert/confirm/open: a failure is a Russian text
 * with a hint. No React.
 */
import { bridge, saveFile } from "../../../bridge";
import { refusal } from "../../../shell/objectRun";
import { csvName } from "./format";
import { currentRun } from "./state";

function csvOfRun(): { text: string; name: string } {
  const run = currentRun();
  if (!run.result?.csv) throw refusal("no_csv", "В отчёте нет таблицы CSV.", "Постройте отчёт из окна «Содержание–тоннаж».");
  return { text: run.result.csv, name: csvName(run.modelName) };
}

/** «Копировать CSV»: the table on the clipboard (the cells part by «;» and the decimal comma, as the file has them). */
export async function copyCsv(): Promise<string> {
  const { text } = csvOfRun();
  const b = bridge();
  if (b) b.copyText(text);
  else if (typeof navigator !== "undefined" && navigator.clipboard) await navigator.clipboard.writeText(text);
  else throw refusal("clipboard", "Буфер обмена недоступен.", "Сохраните таблицу в файл: «Сохранить CSV…».");
  return "Таблица CSV скопирована";
}

/** «Сохранить CSV…»: the save dialog of the shell and the file (a BOM for a spreadsheet is added by the shell); the path or null. */
export async function saveCsv(): Promise<string | null> {
  const { text, name } = csvOfRun();
  return saveFile(`${name}.csv`, "csv", text);
}

