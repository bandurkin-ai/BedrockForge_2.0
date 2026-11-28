/**
 * The refusal «Модель не ложится в сетку» in the step «Блочная модель» of «Импорт файлов» (ТЗ 7.31, 8.7.2; ADR 0030
 * решения 5–7; Д-45 А6): why the blocks do not lie — the table of the axes (the parent, the sizes of the blocks, the
 * fractions of the parent, the division found, where the blocks start inside their parents, the quantum of the
 * columns), the causes in words, the grids that hold most of the blocks — and what the user may do: «Задать сетку
 * вручную» (the fields of the grid below are filled with the best candidate and the file is read again) or «Не
 * импортировать этот файл» (it is taken out of the selection, the other files go on). The panel is the window of the
 * step: no native dialog. The texts and numbers — `model.ts`.
 *
 * The third way out (ADR 0030 decision 14, the customer's requirement А6): «Импортировать списком блоков» — the model that
 * lies in no grid is kept as the centre and the size of every block (`blocks.layout = "list"`); the file is read again
 * and the step shows what the list holds. The same panel is shown for the refusal `rotation_mismatch` of a turned model
 * (the core's `choices`, no table: the rows of the three centres and how far they are).
 */
import type { FileOk, FileOptions, ObjectsWizard } from "../../importing/objects";
import {
  axisRows,
  candidateGrid,
  candidateRows,
  CAUSE_TITLES,
  gridPatch,
  mismatchText,
  patchDraft,
  rowsText,
  type BlocksDraft,
  type Unaligned,
  type UnalignedGrid,
} from "./model";
import css from "../../importing/ImportWizard.module.css";

interface Props {
  wizard: ObjectsWizard;
  file: FileOk;
  options: FileOptions;
  un: Unaligned;
}

export function UnalignedChoice({ wizard, file, options, un }: Props) {
  const draft: BlocksDraft | null = options.blocks ?? null;
  const { error, diag } = un;
  const axes = un.detailed ? axisRows(diag) : [];
  const candidates = un.detailed ? candidateRows(diag) : [];
  const rotation = un.grid?.grid?.rotation ?? 0;
  const apply = (grid: UnalignedGrid) => void wizard.setOptions(file.path, { blocks: patchDraft(draft, gridPatch(grid)) });
  const asList = () => void wizard.setOptions(file.path, { blocks: patchDraft(draft, { layout: "list" }) });
  const best = un.grid?.grid ?? null;
  const rest = un.detailed ? diag.text.slice(1) : [];
  const turned = error.code === "rotation_mismatch";
  return (
    <div className={css.error} role="alert" data-testid="blocks-error" data-code={error.code}>
      <div>
        <b>{turned ? "Центры блоков не совпали с сеткой" : "Модель не ложится в сетку"}</b>
      </div>
      <div data-testid="unaligned-message">{error.message}</div>
      {turned ? <div data-testid="unaligned-mismatch">{mismatchText(error)}</div> : <div data-testid="blocks-error-rows">{rowsText(error)}</div>}
      {rest.length > 0 && (
        <ul className={css.issues} data-testid="unaligned-text">
          {rest.map((t, i) => (
            <li key={i}>{t}</li>
          ))}
        </ul>
      )}
      {un.detailed && diag.causes.length > 0 && (
        <div className={css.note} data-testid="unaligned-causes">
          Возможные причины: {diag.causes.map((c) => CAUSE_TITLES[c] ?? c).join("; ")}.
        </div>
      )}
      {un.detailed && (
        <table className={css.layers} aria-label={`Оси и деление ${file.file}`} data-testid="unaligned-axes">
          <thead>
            <tr>
              <th>Ось</th>
              <th>Родитель, м</th>
              <th>Размеры блоков, м (блоков)</th>
              <th>Дроби родителя</th>
              <th>Деление</th>
              <th>Начала блоков внутри родителя, м (блоков)</th>
              <th>Столбцы</th>
              <th>Допуск, м</th>
            </tr>
          </thead>
          <tbody>
            {axes.map((a) => (
              <tr key={a.axis} data-axis={a.axis}>
                <td>{a.axis}</td>
                <td>{a.parent}</td>
                <td>{a.sizes}</td>
                <td>{a.fractions}</td>
                <td data-testid={`unaligned-div-${a.axis}`}>{a.div}</td>
                <td>{a.residues}</td>
                <td className={css.note}>{a.columns}</td>
                <td>{a.quantum}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {un.detailed && candidates.length > 0 && (
        <table className={css.layers} aria-label={`Сетки, в которые ложится больше всего блоков ${file.file}`} data-testid="unaligned-candidates">
          <thead>
            <tr>
              <th>Родитель, м</th>
              <th>Начало, м</th>
              <th>Деление</th>
              <th>Допуск, м</th>
              <th>Блоков ложится</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {candidates.map((c, i) => (
              <tr key={i} data-current={c.current ? "yes" : "no"}>
                <td>{c.size}</td>
                <td>{c.corner}</td>
                <td>{c.div}</td>
                <td>{c.tol}</td>
                <td data-testid={`unaligned-share-${i}`}>{c.aligned}</td>
                <td>
                  {c.current ? (
                    <span className={css.note}>определена по файлу</span>
                  ) : (
                    <button type="button" className={css.linkBtn} aria-label={`Взять сетку ${i + 1}`} data-testid={`unaligned-take-${i}`} onClick={() => apply(candidateGrid(diag.candidates[i], rotation))}>
                      Взять
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className={css.options}>
        <button type="button" disabled={!best} data-testid="unaligned-grid" onClick={() => best && apply(best)}>
          {un.grid?.label ?? "Задать сетку вручную"}
        </button>
        {un.list && (
          <button type="button" data-testid="unaligned-list" title={un.list.note} onClick={asList}>
            {un.list.label}
          </button>
        )}
        <button type="button" data-testid="unaligned-cancel" onClick={() => wizard.remove(file.path)}>
          {un.cancel.label}
        </button>
      </div>
      <div className={css.note}>
        {turned
          ? "«Задать сетку вручную» заполняет поля «Сетка модели» ниже сеткой, которую нашли по файлу: поправьте угол поворота, начало или оси центров — файл будет прочитан заново. "
          : "«Задать сетку вручную» заполняет поля «Сетка модели» ниже лучшей сеткой из таблицы; поправьте начало, размер родителя, деление, поворот или допуск — файл будет прочитан заново. "}
        {un.list ? `«${un.list.label}» хранит модель как список блоков (центр и размер каждого): ${un.list.note ?? "показ, фильтры, отчёт, экспорт"}. ` : ""}
        «Не импортировать» убирает файл из выбора: остальные файлы загрузятся.
      </div>
    </div>
  );
}
