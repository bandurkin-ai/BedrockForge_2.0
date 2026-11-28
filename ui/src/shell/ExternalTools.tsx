import { useEffect, useRef, useState } from "react";
import { bridge } from "../bridge";
import { CoreError } from "./api";
import { useApp } from "./AppContext";
import { pickFilesIn } from "./lastFolder";
import { findOda, odaLines, pickOdaFile, setOdaPath, syncOda, type OdaStatus, type OdaTone } from "./odaTool";
import { normalizeOdaPath } from "./settings";
import { useStore } from "./useStore";
import css from "./SettingsWindow.module.css";

const TONE: Record<OdaTone, string> = { ok: css.stateOk, warn: css.stateWarn, bad: css.stateBad, plain: "" };

/**
 * «Настройка → Внешние программы» (ADR 0013 §6; ТЗ 7.35 Д-13, Д-17): the path of ODA File Converter, through
 * which DWG is read and written — «Выбрать…» (the file dialog of the system), «Найти» (the search of the core),
 * «Проверить» (the typed path goes to the core and into the settings), the state and how to install the program.
 */
export function ExternalTools() {
  const app = useApp();
  const saved = useStore(app.store, (s) => s.odaConverter);
  const [draft, setDraft] = useState(saved ?? "");
  const [status, setStatus] = useState<OdaStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const turn = useRef(0);

  /** One request at a time is shown: the answer of an older one is dropped. */
  const ask = async (work: () => Promise<OdaStatus>) => {
    const mine = ++turn.current;
    setBusy(true);
    try {
      const s = await work();
      if (mine !== turn.current) return;
      setStatus(s);
      setError(null);
      setDraft(s.configured ?? "");
    } catch (e) {
      if (mine !== turn.current) return;
      setError(e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e));
    } finally {
      if (mine === turn.current) setBusy(false);
    }
  };

  useEffect(() => {
    void ask(() => syncOda(app.store));
    return () => {
      turn.current++; // the tab is closed: a late answer is not shown
    };
  }, [app]);

  const check = () => void ask(() => setOdaPath(app.store, draft));
  /** A closed dialog changes nothing (an answer on its way is still shown); one that did not open is told as any other error. */
  const pick = async () => {
    const b = bridge();
    const file = await pickOdaFile(b ? () => pickFilesIn("program", false) : undefined).catch((e: unknown) => ask(() => Promise.reject(e)));
    if (file) await ask(() => setOdaPath(app.store, file));
  };
  const changed = normalizeOdaPath(draft) !== saved;
  const how = status ? (status.found ? status.install : status.hint) : "";

  return (
    <div className={css.general}>
      <div className={css.heading}>ODA File Converter — чтение и запись чертежей DWG</div>
      <label className={css.field}>
        <span>Путь к программе ODAFileConverter.exe или к её папке</span>
        <span className={css.fieldRow}>
          <input
            value={draft}
            spellCheck={false}
            placeholder="Не задан — программа ищется в стандартных местах"
            aria-label="Путь к ODA File Converter"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") check();
            }}
          />
          <button type="button" disabled={!bridge()} title={bridge() ? undefined : "Выбор файла доступен только в приложении"} onClick={() => void pick()}>
            Выбрать…
          </button>
        </span>
      </label>
      <div className={css.fieldRow}>
        <button type="button" disabled={busy} title="Искать установленную программу: переменная BF_ODA_CONVERTER, PATH, папка Program Files\ODA" onClick={() => void ask(() => findOda(app.store))}>
          Найти
        </button>
        <button type="button" disabled={busy} title="Проверить путь и запомнить его" onClick={check}>
          Проверить
        </button>
        {(saved !== null || draft !== "") && (
          <button type="button" disabled={busy} title="Убрать путь: программа будет искаться в стандартных местах" onClick={() => void ask(() => setOdaPath(app.store, null))}>
            Очистить
          </button>
        )}
      </div>
      {changed && <div className={css.stateWarn}>Путь изменён — нажмите «Проверить», чтобы запомнить его.</div>}
      <div className={css.state} role="status" aria-live="polite" data-testid="oda-status" data-found={status ? String(status.found) : ""}>
        {error ? (
          <div className={css.stateBad}>{error}</div>
        ) : status ? (
          odaLines(status).map((l, i) => (
            <div key={i} className={TONE[l.tone]}>
              {l.text}
            </div>
          ))
        ) : (
          <div>Проверка…</div>
        )}
      </div>
      {how && (
        <div className={css.how}>
          <b>Как установить.</b> {how}
        </div>
      )}
      <div className={css.how}>BedrockForge не распространяет эту программу и запускает её только для преобразования чертежей DWG в DXF и обратно.</div>
    </div>
  );
}
