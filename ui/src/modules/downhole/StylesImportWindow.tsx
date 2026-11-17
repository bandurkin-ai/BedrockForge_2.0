/** Window «Импорт стилей» (command styles.import): the conflicts of a .bfstyle.json and the summary. */
import { useEffect, useState } from "react";
import { closeWindow } from "../../shell/windows";
import { ImportDialog } from "./ImportDialog";
import { forgetImport, importSource } from "./open";
import "./downhole.css";

export function StylesImportWindow({ winId, token }: { winId: string; token: string }) {
  const [source] = useState(() => importSource(token));
  useEffect(() => () => forgetImport(token), [token]);
  if (!source) return <div className="dh-root"><p className="muted small form-note">Файл больше недоступен: выберите «Импорт стилей» ещё раз.</p></div>;
  return (
    <div className="dh-root dh-window">
      <ImportDialog source={source} onClose={() => closeWindow(winId)} />
    </div>
  );
}
