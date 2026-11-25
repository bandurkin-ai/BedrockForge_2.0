/**
 * The table of the probes of the window «Блок · i, j, k» (ТЗ 7.35 Д-30): every candidate of the ellipsoid of the last pass — the hole, the interval,
 * h_s, the distance, the octant, the value of the table and the one used, the weight λ and the reason; the order by a click on a header, the filter
 * by the reason. A row lights the probe in 3D; «К интервалу» opens 7.17 on it. A negative weight is Copper Orange, a probe not taken is dimmed.
 */
import css from "../../../shell/PropsParts.module.css";
import type { Candidate, InspectResult } from "./api";
import mod from "./Inspect.module.css";
import { pick, setReason, sortBy, type InspectState } from "./state";
import { COLUMNS, countsText, dash, intervalText, isSelected, numText, reasonOptions, shownCandidates, sortMark, weightClass } from "./view";

const cell = (c: Candidate, key: (typeof COLUMNS)[number]["key"]): string => {
  switch (key) {
    case "hole":
      return c.hole;
    case "from":
      return intervalText(c);
    case "h":
      return numText(c.h, 3);
    case "dist":
      return numText(c.dist, 2);
    case "octant":
      return c.octant === null ? dash : String(c.octant);
    case "z_raw":
      return numText(c.z_raw, 4);
    case "z":
      return numText(c.z, 4);
    case "lambda":
      return numText(c.lambda, 5);
    default:
      return c.merged > 1 ? `${c.reason_label} (проб слито: ${c.merged})` : c.reason_label;
  }
};

export function Candidates({ result, state, onInterval }: { result: InspectResult; state: Pick<InspectState, "sort" | "reason" | "picked">; onInterval: (c: Candidate) => void }) {
  const options = reasonOptions(result.candidates);
  // a filter of a reason this row does not have is no filter (the tab changed)
  const reason = state.reason !== null && options.some((o) => o.code === state.reason) ? state.reason : null;
  const rows = shownCandidates(result.candidates, reason, state.sort);
  return (
    <section className={mod.block} data-testid="inspect-candidates">
      <h3>Пробы в эллипсоиде последнего прохода</h3>
      <div className={mod.filter}>
        <label>
          Причина{" "}
          <select className={mod.select} value={reason ?? ""} aria-label="Фильтр по причине" data-testid="inspect-reason" onChange={(e) => setReason(e.target.value || null)}>
            <option value="">Все ({result.candidates.length})</option>
            {options.map((o) => (
              <option key={o.code} value={o.code}>
                {o.label} ({o.count})
              </option>
            ))}
          </select>
        </label>
        <span className={mod.note} data-testid="inspect-counts">
          {countsText(result, rows.length)}
        </span>
      </div>
      {result.candidates.length === 0 ? (
        <p className={mod.empty} data-testid="inspect-no-candidates">
          {result.active ? "В эллипсоиде нет проб." : "Блок вне домена строки: пробы для него не отбирались."}
        </p>
      ) : (
        <div className={mod.tableWrap}>
          <table className={mod.table} data-testid="inspect-table">
            <thead>
              <tr>
                {COLUMNS.map((c) => (
                  <th key={c.key} className={c.numeric ? mod.num : undefined} aria-sort={sortMark(state.sort, c.key)} title={c.hint}>
                    <button type="button" data-testid={`inspect-sort-${c.key}`} onClick={() => sortBy(c.key)}>
                      {c.title}
                      {state.sort?.key === c.key ? (state.sort.dir === "asc" ? " ▲" : " ▼") : ""}
                    </button>
                  </th>
                ))}
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.i} data-testid={`inspect-row-${c.i}`} data-reason={c.reason} data-selected={isSelected(c)} data-picked={state.picked === c.i} tabIndex={0} onClick={() => pick(c.i)} onDoubleClick={() => onInterval(c)} onKeyDown={(e) => e.key === "Enter" && pick(c.i)}>
                  {COLUMNS.map((col) => {
                    const w = col.key === "lambda" ? weightClass(c.lambda) : null;
                    return (
                      <td key={col.key} className={`${col.numeric ? mod.num : ""} ${w === "negative" ? mod.negative : ""}`.trim() || undefined} data-weight={w ?? undefined}>
                        {cell(c, col.key)}
                      </td>
                    );
                  })}
                  <td>
                    <button
                      type="button"
                      className={css.btn}
                      data-testid={`inspect-interval-${c.i}`}
                      title="Открыть «Данные по скважинам» на интервале этой пробы"
                      onClick={(e) => {
                        e.stopPropagation();
                        onInterval(c);
                      }}
                    >
                      К интервалу
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
