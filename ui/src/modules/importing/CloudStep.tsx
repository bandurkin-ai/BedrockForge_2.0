/**
 * The card of a cloud file in «Импорт файлов» and «Облако точек» (ADR 0014 §6; ТЗ 7.35 Д-22): a LAS — version,
 * format of points, records, scale, offset, extent, CRS, the classes of a sample, what is not loaded, the
 * proposed thinning; a table XYZ — its layout, the columns X / Y / Z / class to choose and its first rows; both —
 * «каждая k-я запись» and the points the import takes. A change reads the composition again.
 */
import { useEffect, useState } from "react";
import { count } from "../../shell/treeActions";
import { classHex } from "../../viz/lines/cloud";
import { classLabel } from "../lines/classes";
import { everyProposal, lasFacts, parseEvery, pointsText, sampleClasses, sampleNote, xyzFacts, type CloudCard, type LasCard, type XyzCard } from "./cloud";
import type { FileOk, FileOptions, ObjectsWizard } from "./objects";
import css from "./ImportWizard.module.css";

const PREVIEW_ROWS = 20;

export function CloudStep({ wizard, file, card, options }: { wizard: ObjectsWizard; file: FileOk; card: CloudCard; options: FileOptions }) {
  const proposal = everyProposal(card);
  return (
    <div className={css.importStep} data-testid="objects-cloud">
      <div>
        <b>Облако точек</b> · {pointsText(card)}
        {file.crs?.name ? ` · СК: ${file.crs.name}` : ""}
      </div>
      <div className={css.options}>
        <EveryField file={file} value={options.every ?? ""} onApply={(every) => void wizard.setOptions(file.path, { every })} />
        {proposal && (
          <span className={css.note}>
            {proposal.text}{" "}
            <button type="button" className={css.linkBtn} onClick={() => void wizard.setOptions(file.path, { every: String(proposal.every) })}>
              Применить
            </button>
          </span>
        )}
      </div>
      {card.format === "las" ? <LasPart card={card} /> : <XyzPart wizard={wizard} file={file} card={card} />}
    </div>
  );
}

/** «Каждая k-я запись»: applied on Enter or leaving the field (every keystroke would read the file again). */
function EveryField({ file, value, onApply }: { file: FileOk; value: string; onApply: (every: string) => void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const error = parseEvery(draft).error;
  const apply = () => {
    if (draft.trim() !== value.trim()) onApply(draft.trim());
  };
  return (
    <>
      <label>
        Каждая k-я запись
        <input
          value={draft}
          placeholder="1"
          style={{ width: 90 }}
          inputMode="numeric"
          aria-label={`Каждая k-я запись ${file.file}`}
          aria-invalid={error ? true : undefined}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={apply}
          onKeyDown={(e) => e.key === "Enter" && apply()}
        />
      </label>
      {error && (
        <span className={css.error} role="alert">
          {error}
        </span>
      )}
    </>
  );
}

function Facts({ facts }: { facts: { label: string; value: string }[] }) {
  return (
    <table className={css.layers}>
      <tbody>
        {facts.map((f) => (
          <tr key={f.label}>
            <th scope="row">{f.label}</th>
            <td>{f.value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function LasPart({ card }: { card: LasCard }) {
  const classes = sampleClasses(card);
  return (
    <>
      <Facts facts={lasFacts(card)} />
      {classes.length > 0 && (
        <>
          <table className={css.layers} aria-label="Классы облака" data-testid="objects-cloud-classes">
            <thead>
              <tr>
                <th>Класс</th>
                <th>Записей</th>
              </tr>
            </thead>
            <tbody>
              {classes.map((c) => (
                <tr key={c.code}>
                  <td>
                    <span aria-hidden className={css.swatch} style={{ background: classHex(c.code) }} />
                    {classLabel(c.code)}
                  </td>
                  <td className={css.num}>{count(c.points)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className={css.note}>Классы — {sampleNote(card)}.</div>
        </>
      )}
    </>
  );
}

const FIELDS = [
  ["x", "x", "Колонка X"],
  ["y", "y", "Колонка Y"],
  ["z", "z", "Колонка Z"],
  ["class", "class_column", "Колонка класса"],
] as const;

/** The layout of a table XYZ, the columns X / Y / Z / class and the first rows. */
function XyzPart({ wizard, file, card }: { wizard: ObjectsWizard; file: FileOk; card: XyzCard }) {
  const rows = card.preview.slice(0, PREVIEW_ROWS);
  const role = (name: string): string | null => {
    const f = FIELDS.find(([key]) => card.fields[key] === name);
    return f ? f[2].replace("Колонка ", "") : null;
  };
  return (
    <>
      <Facts facts={xyzFacts(card)} />
      <div className={css.options}>
        {FIELDS.map(([key, option, label]) => (
          <label key={key}>
            {label}
            <select
              value={card.fields[key] ?? ""}
              aria-label={`${label} ${file.file}`}
              onChange={(e) => void wizard.setOptions(file.path, { [option]: e.target.value })}
            >
              {key === "class" && <option value="">нет</option>}
              {key !== "class" && card.fields[key] === null && <option value="">не найдена</option>}
              {card.columns.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
      {rows.length > 0 && (
        <div className={css.previewWrap}>
          <table className={css.preview} aria-label={`Первые строки ${file.file}`} data-testid="objects-cloud-preview">
            <thead>
              <tr>
                {card.columns.map((c) => (
                  <th key={c}>
                    {c}
                    {role(c) ? ` (${role(c)})` : ""}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  {card.columns.map((_, k) => (
                    <td key={k}>{r[k] ?? ""}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
