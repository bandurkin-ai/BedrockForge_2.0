/**
 * The results of the jobs of the building as the windows show them: the codes of a fill / a coding (the blocks and the
 * volume of each, the warnings of the core — «Каркас не пересекает сетку в плане» among them, the fields that «Перестроить»
 * took away, the seconds of the stages) and the totals before / after a regularization (a gap is a gap; a difference of
 * more than 1e-9 is marked and explained). A model is named with its number: it is in the tree, «Блочные модели».
 */
import css from "../../../shell/PropsParts.module.css";
import type { FillResult, RegularizeResult } from "./api";
import mod from "./Build.module.css";
import { anyDiffers, dec, int, plural, totalRows, volumeText } from "./format";
import { Warnings } from "./Parts";

const STAGE_TITLES: Record<string, string> = { wireframes: "Каркасы", columns: "Столбцы", boundary: "Граничные родители", split: "Дробление", merge: "Слияние", write: "Запись", read: "Чтение", overlap: "Перекрытия", extent: "Охват" };

/** «Каркасы 0,4 с · Запись 1,2 с» — the seconds the core spent (the total is the last). */
export function stagesText(stages: Record<string, number>): string {
  return Object.entries(stages)
    .filter(([k]) => k !== "total")
    .map(([k, v]) => `${STAGE_TITLES[k] ?? k} ${dec(v, 1)} с`)
    .join(" · ");
}

export function FillResultView({ r, kind }: { r: FillResult; kind: "fill" | "code" }) {
  const centre = kind === "code" && r.fields.length > 0;
  return (
    <div data-testid={`build-${kind}-result`}>
      <h4 className={mod.head}>
        {centre ? `Модель «${r.name}»: записано ${r.fields.join(", ")}` : `Новая модель «${r.name}» (№${r.model_id})`}
      </h4>
      <p className={css.note} data-testid={`build-${kind}-rows`}>
        {int(r.rows)} {plural(r.rows, "строка", "строки", "строк")}, родителей {int(r.parents)}
        {centre && r.version !== null ? `, версия ${r.version}` : ""}.
      </p>
      <div className={mod.tableWrap}>
        <table className={mod.table} data-testid={`build-${kind}-codes`}>
          <thead>
            <tr>
              <th className={mod.left}>Код</th>
              <th>Строк</th>
              <th>Объём</th>
            </tr>
          </thead>
          <tbody>
            {Object.keys(r.codes).map((c) => (
              <tr key={c} data-testid={`build-${kind}-code-row-${c}`}>
                <td className={mod.left}>{c}</td>
                <td>{int(r.codes[c])}</td>
                <td>{volumeText(r.volume[c] ?? 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Warnings list={r.warnings} testId={`build-${kind}-warnings`} />
      {r.dropped.length > 0 && (
        <p className={css.note} data-testid={`build-${kind}-dropped`}>
          Удалены поля других операций: {r.dropped.join(", ")}.
        </p>
      )}
      {Object.keys(r.stages).length > 0 && <p className={css.note}>{stagesText(r.stages)}</p>}
    </div>
  );
}

export function RegularizeResultView({ r }: { r: RegularizeResult }) {
  const rows = totalRows(r);
  return (
    <div data-testid="build-regularize-result">
      <h4 className={mod.head}>
        Новая модель «{r.name}» (№{r.model_id})
      </h4>
      <p className={css.note}>
        {int(r.rows)} {plural(r.rows, "блок", "блока", "блоков")}.
      </p>
      <div className={mod.tableWrap}>
        <table className={mod.table} data-testid="build-regularize-totals">
          <thead>
            <tr>
              <th className={mod.left}>Итог</th>
              <th>До</th>
              <th>После</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((x) => (
              <tr key={x.label} className={x.differs ? mod.differs : undefined} data-differs={x.differs} data-testid={`build-regularize-total-${x.label}`}>
                <td className={mod.left}>{x.label}</td>
                <td>{x.before}</td>
                <td>{x.after}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {anyDiffers(r) && (
        <p className={css.note} data-testid="build-regularize-differs">
          Итоги до и после расходятся. Если у части блоков нет значения поля, пропуск не считается нулём: среднее и металл считаются только по блокам со значением.
        </p>
      )}
      <Warnings list={r.warnings} testId="build-regularize-warnings" />
      {Object.keys(r.stages).length > 0 && <p className={css.note}>{stagesText(r.stages)}</p>}
    </div>
  );
}
