import { downloadFile } from "../api";
import { showMessage } from "./dialogs";
import { createLog } from "./opLog";

/**
 * Downloads an export with its journal: a refusal is shown in the application's window with the
 * journal icon (the request, the server's answer). Returns the saved file name.
 */
export async function downloadWithLog(url: string, fallback: string, what: string, context: Record<string, unknown> = {}): Promise<string> {
  const log = createLog("export", what, context);
  log.info(`Запрос файла: ${url}`);
  try {
    const name = await downloadFile(url, fallback);
    log.info(`Файл «${name}» сохранён в загрузки`);
    return name;
  } catch (e) {
    log.error(`${what} не выполнен`, e);
    void showMessage({ title: `${what} не выполнен`, message: (e as Error).message, error: true, log });
    throw e;
  } finally {
    log.close();
  }
}
