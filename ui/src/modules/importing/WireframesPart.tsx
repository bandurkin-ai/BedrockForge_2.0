/**
 * The wireframes of a file in the composition of «Импорт файлов» (ТЗ 7.21, 9.4, 9.5; ADR 0013 §5–§6): name,
 * triangles, vertices, colour and attributes of each, the ones that will not load with the reason; Datamine —
 * the field that divides the file into wireframes (or the layers of its strings) and «предварительно»; a DWG
 * without ODA File Converter — the text of the core and «Настройка › Внешние программы».
 */
import { useState } from "react";
import { CHOICE_TITLES, needsConverter, type FileEntry, type FileOk, type FileOptions, type ObjectsWizard } from "./objects";
import css from "./ImportWizard.module.css";

const SHOWN = 12;
const n = (v: number | null) => (v === null ? "—" : v.toLocaleString("ru-RU"));

/** The choices of the file (`choices` of the composition): changing one reads the composition again. */
export function FileChoices({ wizard, file, options }: { wizard: ObjectsWizard; file: FileOk; options: FileOptions }) {
  const entries = Object.entries(file.choices ?? {}).filter(([key, fields]) => key in CHOICE_TITLES && fields.length > 0);
  if (!entries.length) return null;
  return (
    <>
      {entries.map(([key, fields]) => {
        const t = CHOICE_TITLES[key];
        const value = (options as Record<string, string | undefined>)[key] ?? "";
        return (
          <label key={key}>
            {t.label}
            <select value={value} onChange={(e) => void wizard.setOptions(file.path, { [key]: e.target.value })} aria-label={`${t.label} ${file.file}`}>
              <option value="">{t.none}</option>
              {fields.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
          </label>
        );
      })}
    </>
  );
}

/** «Предварительно»: the reader was not checked on real files. */
export function Preliminary({ file }: { file: FileOk }) {
  if (!file.preliminary) return null;
  return (
    <div className={css.warn} data-testid="objects-preliminary">
      Предварительно: формат прочитан по описанию, без проверки на образцах. Сверьте число треугольников, положение и объём с исходной программой.
    </div>
  );
}

export function WireframesTable({ file }: { file: FileOk }) {
  const [all, setAll] = useState(false);
  const list = file.wireframes ?? [];
  if (!list.length) return null;
  const total = (file.totals.wireframes ?? 0) + (file.unread ?? 0);
  const shown = all ? list : list.slice(0, SHOWN);
  return (
    <>
      <table className={css.layers} aria-label={`Каркасы ${file.file}`} data-testid="objects-wireframes">
        <thead>
          <tr>
            <th>Каркас</th>
            <th>Треугольников</th>
            <th>Вершин</th>
            <th>Атрибуты</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((w) => (
            <tr key={String(w.key)}>
              <td>
                {w.color && <span aria-hidden className={css.swatch} style={{ background: w.color }} />}
                {w.name}
                {w.error && (
                  <div className={css.error}>
                    Не загрузится: {w.error.message} {w.error.hint}
                  </div>
                )}
                {!w.error && w.warnings.length > 0 && <div className={css.note}>{w.warnings.slice(0, 3).join("; ")}</div>}
              </td>
              <td className={css.num}>{n(w.triangles)}</td>
              <td className={css.num}>{n(w.points)}</td>
              <td className={css.note}>{w.attrs.map((a) => a.name).join(", ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {list.length > SHOWN && (
        <button type="button" className={css.linkBtn} style={{ alignSelf: "flex-start" }} onClick={() => setAll(!all)}>
          {all ? "Свернуть список каркасов" : `Все каркасы (${n(list.length)})`}
        </button>
      )}
      {all && total > list.length && <div className={css.note}>Показаны первые {n(list.length)} каркасов; загрузятся все, кроме непрочитанных.</div>}
    </>
  );
}

/** A file that failed: a DWG without the converter offers «Настройка» and «Прочитать снова». */
export function ConverterHelp({ wizard, file, onSettings }: { wizard: ObjectsWizard; file: FileEntry; onSettings: () => void }) {
  if (!needsConverter(file)) return null;
  return (
    <div className={css.options} data-testid="objects-oda">
      <button type="button" className={css.btn} onClick={onSettings}>
        Настройка › Внешние программы…
      </button>
      <button type="button" className={css.btn} onClick={() => void wizard.inspect()}>
        Прочитать снова
      </button>
    </div>
  );
}
