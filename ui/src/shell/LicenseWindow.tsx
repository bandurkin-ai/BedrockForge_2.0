import { useEffect, useState } from "react";
import { bridge } from "../bridge";
import { apiUrl, authHeaders } from "./coreClient";
import css from "./LicenseWindow.module.css";

interface LicenseInfo {
  hwid: string;
  mode: "trial" | "licensed" | "readonly";
  trial: { first_run: string; expires: string; days_left: number; clock_back: boolean; active: boolean };
  message: string;
}

/** «Лицензия» (ТЗ 5.7): PC code with «Скопировать», trial period; licence files — Э24. */
export function LicenseWindow() {
  const [info, setInfo] = useState<LicenseInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const core = bridge()?.core();
    if (!core) {
      setError("Ядро недоступно");
      return;
    }
    fetch(apiUrl(core, "/license"), { headers: authHeaders(core) })
      .then(async (r) => (r.ok ? setInfo((await r.json()) as LicenseInfo) : setError(`Ядро ответило ${r.status}`)))
      .catch((e: unknown) => setError(String(e)));
  }, []);

  if (error) return <div className={css.wrap}>Не удалось получить сведения о лицензии: {error}</div>;
  if (!info) return <div className={css.wrap}>Загрузка…</div>;
  const date = new Intl.DateTimeFormat("ru-RU", { dateStyle: "long" });
  return (
    <div className={css.wrap}>
      <div className={css.row}>
        <span className={css.label}>Код ПК</span>
        <span className={css.code}>
          <span data-testid="hwid">{info.hwid}</span>
          <button
            type="button"
            className={css.btn}
            onClick={() => {
              bridge()?.copyText(info.hwid);
              setCopied(true);
            }}
          >
            {copied ? "Скопировано" : "Скопировать"}
          </button>
        </span>
      </div>
      <div className={css.row}>
        <span className={css.label}>Режим</span>
        <span className={info.trial.active ? css.ok : css.bad} data-testid="license-message">
          {info.message}
        </span>
      </div>
      <div className={css.row}>
        <span className={css.label}>Первый запуск</span>
        <span>{date.format(new Date(info.trial.first_run))}</span>
      </div>
      <div className={css.row}>
        <span className={css.label}>Пробный срок до</span>
        <span>{date.format(new Date(info.trial.expires))}</span>
      </div>
      <div className={css.row}>
        <span className={css.label}>Файл лицензии</span>
        <span>
          <button type="button" className={css.btn} aria-disabled="true" title="Недоступно: загрузка файла лицензии появится на этапе Э24">
            Загрузить файл лицензии…
          </button>
        </span>
      </div>
      <div className={css.note}>Отправьте код ПК владельцу программы, чтобы получить файл лицензии для этого компьютера.</div>
    </div>
  );
}
