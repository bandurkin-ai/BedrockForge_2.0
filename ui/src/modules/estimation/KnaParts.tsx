/**
 * The parts of the window «Анализ окрестности»: the choice of the test blocks (three picked by the core as «много / средне / мало», or
 * the person's own up to ten) and the table of the variants of one block — every indicator by the values of the parameter, the
 * recommended `n_max` marked, the row as it stands in the parameters first.
 */
import css from "../../shell/PropsParts.module.css";
import { type KnaBlock, type KnaParam, type KnaResult, knaValueText } from "./checksApi";
import mod from "./Checks.module.css";
import { useSel } from "./hooks";
import { addPick, blockTitle, BLOCKS_MAX, knaStore, num, pickText, PICK_TITLES, removePick, setDraftKind, setDraftText, setMode, statusWord, type PickKind } from "./knaState";

export function BlockPicker({ disabled }: { disabled: boolean }) {
  const s = useSel(knaStore, (x) => x);
  return (
    <div className={mod.blocks} data-testid="kna-blocks">
      <div className={mod.checks}>
        <span className={mod.legendTitle}>Тестовые блоки</span>
        <span className={mod.seg} role="group" aria-label="Выбор тестовых блоков">
          <button type="button" aria-pressed={s.mode === "auto"} disabled={disabled} data-testid="kna-mode-auto" title="Три блока строки: много, средне и мало проб в радиусе первого прохода" onClick={() => setMode("auto")}>
            Автоматически: много, средне, мало
          </button>
          <button type="button" aria-pressed={s.mode === "own"} disabled={disabled} data-testid="kna-mode-own" title="До десяти блоков, которые вы укажете" onClick={() => setMode("own")}>
            Свои блоки ({s.picks.length})
          </button>
        </span>
      </div>
      {s.mode === "own" && (
        <>
          <div className={mod.draft}>
            <select className={mod.select} value={s.draftKind} disabled={disabled} aria-label="Как задан блок" data-testid="kna-draft-kind" onChange={(e) => setDraftKind(e.target.value as PickKind)}>
              {(Object.keys(PICK_TITLES) as PickKind[]).map((k) => (
                <option key={k} value={k}>
                  {PICK_TITLES[k]}
                </option>
              ))}
            </select>
            <input
              value={s.draftText}
              disabled={disabled}
              spellCheck={false}
              placeholder={s.draftKind === "ijk" ? "4; 4; 7" : "500040; 6700040; 297,5"}
              aria-label="Блок"
              aria-invalid={s.draftIssue !== null}
              data-testid="kna-draft-text"
              onChange={(e) => setDraftText(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && addPick()}
            />
            <button type="button" className={css.btn} disabled={disabled || s.picks.length >= BLOCKS_MAX} data-testid="kna-add" title={`Добавить блок (до ${BLOCKS_MAX})`} onClick={() => addPick()}>
              Добавить
            </button>
          </div>
          {s.draftIssue && (
            <p className={css.error} role="alert" data-testid="kna-draft-issue">
              {s.draftIssue}
            </p>
          )}
          {s.picks.length > 0 ? (
            <ul>
              {s.picks.map((p, i) => (
                <li key={i} data-testid={`kna-pick-${i}`}>
                  {pickText(p)}
                  <button type="button" className={css.iconBtn} disabled={disabled} aria-label={`Убрать блок ${i + 1}`} data-testid={`kna-unpick-${i}`} onClick={() => removePick(i)}>
                    ×
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className={css.note}>Добавьте хотя бы один блок: индексы родителя или точку в координатах проекта.</p>
          )}
        </>
      )}
    </div>
  );
}

/** The table of the variants of one block by the values of a parameter. */
export function VariantTable({ result, block, param }: { result: KnaResult; block: KnaBlock; param: KnaParam }) {
  const list = block.vary[param] ?? [];
  const recommended = param === "n_max" ? block.recommend : null;
  const cells = (v: { KE: number | null; SLOPE: number | null; KV: number | null; SNW: number | null; w_far: number | null; LAGR: number | null; NS: number | null; status: number }) =>
    v.status !== 0 ? (
      <td colSpan={7}>{statusWord(v.status)}</td>
    ) : (
      <>
        <td>{num(v.KE)}</td>
        <td>{num(v.SLOPE)}</td>
        <td>{num(v.KV)}</td>
        <td>{num(v.SNW)}</td>
        <td>{num(v.w_far)}</td>
        <td>{num(v.LAGR)}</td>
        <td>{num(v.NS, 0)}</td>
      </>
    );
  return (
    <div className={mod.tableWrap}>
      <table className={mod.table} data-testid="kna-table" data-param={param} data-block={result.blocks.indexOf(block)}>
        <thead>
          <tr>
            <th>{param === "n_max" ? "n_max" : param === "disc" ? "Дискретизация" : "Множитель"}</th>
            <th title="Эффективность кригинга">KE</th>
            <th title="Наклон регрессии">SLOPE</th>
            <th title="Дисперсия кригинга">KV</th>
            <th title="Сумма отрицательных весов">Σ отр. весов</th>
            <th title="Вес самой дальней выбранной пробы">Вес дальней</th>
            <th title="Множитель Лагранжа">μ</th>
            <th title="Проб в оценке">Проб</th>
          </tr>
        </thead>
        <tbody>
          <tr data-base="true" data-testid="kna-base">
            <td>Как в строке</td>
            {cells(block.base)}
          </tr>
          {list.map((v) => (
            <tr key={v.value} data-testid={`kna-variant-${v.value}`} data-recommended={recommended === v.value} data-empty={v.status !== 0}>
              <td>
                {knaValueText(param, v.value)}
                {recommended === v.value ? " ← рекомендация" : ""}
              </td>
              {cells(v)}
            </tr>
          ))}
        </tbody>
      </table>
      <p className={css.note} style={{ margin: "4px 8px" }}>
        {blockTitle(block, result.blocks.indexOf(block))}
      </p>
    </div>
  );
}
