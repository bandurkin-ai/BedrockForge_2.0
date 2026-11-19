/**
 * The composition of an OMF v1 file in the card of «Импорт файлов» (ТЗ 7.24; ADR 0017 §1–§2): the header of the
 * file, every element with what it becomes or why it will not load (the error of the core with its hint), the
 * data it carries over and what not, and the block models with their grids. The data — `omf.ts`.
 */
import { blockEntryText, elementBecomes, elementData, elementNotes, elementSize, omfHeader, type OmfBlockEntry, type OmfInfo } from "./omf";
import css from "./ImportWizard.module.css";

export function OmfPart({ file, info, blocks }: { file: string; info: OmfInfo | undefined; blocks: readonly OmfBlockEntry[] }) {
  if (!info) return null;
  return (
    <div data-testid="objects-omf">
      <div className={css.note}>{omfHeader(info)}</div>
      {info.elements.length > 0 && (
        <table className={css.layers} aria-label={`Элементы ${file}`}>
          <thead>
            <tr>
              <th>Элемент</th>
              <th>Станет</th>
              <th>Размер</th>
              <th>Данные</th>
              <th>Не перенесётся</th>
            </tr>
          </thead>
          <tbody>
            {info.elements.map((e) => {
              const notes = elementNotes(e);
              return (
                <tr key={e.key} data-testid="objects-omf-element" data-loads={e.as ? "yes" : "no"} data-code={e.error?.code}>
                  <td>
                    {e.title} «{e.name}»
                  </td>
                  <td className={e.as ? undefined : e.error ? css.error : css.warn}>{elementBecomes(e)}</td>
                  <td className={css.num}>{elementSize(e)}</td>
                  <td className={css.note}>{elementData(e)}</td>
                  <td className={e.error ? css.error : css.note}>{notes.join("; ")}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {blocks.length > 0 && (
        <ul className={css.issues} aria-label={`Блочные модели ${file}`}>
          {blocks.map((b) => (
            <li key={b.name}>Блочная модель {blockEntryText(b)}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
