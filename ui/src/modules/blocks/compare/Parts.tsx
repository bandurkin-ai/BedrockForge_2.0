/**
 * The parts of the window «Сравнить оценки» (ТЗ 7.35 Д-37; ADR 0027 решение 25): the statistics of Δ, the table by the domains (the means of A, B and Δ weighted by the volume
 * of the blocks, the blocks whose `STATUS` changed — «—» when the fields are not of an estimate), the words of the core. A gap is «—», never 0. Presentation only.
 */
import css from "../../../shell/PropsParts.module.css";
import panel from "../../resources/Panel.module.css";
import type { EstimateCompare } from "./api";
import { DOMAIN_HEAD, domainCells, statRows } from "./model";
import mod from "./Compare.module.css";

export function Stats({ result }: { result: EstimateCompare }) {
  return (
    <dl className={mod.facts} data-testid="ecmp-stats">
      {statRows(result.stats).map(([k, v]) => (
        <div key={k} className={mod.fact}>
          <dt>{k}</dt>
          <dd data-stat={k}>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Domains({ result }: { result: EstimateCompare }) {
  return (
    <div className={panel.tableWrap} data-testid="ecmp-domains-wrap">
      <table className={panel.table} data-testid="ecmp-domains" data-rows={result.domains.length}>
        <thead>
          <tr>
            {DOMAIN_HEAD.map((h, i) => (
              <th key={h} className={i === 0 ? panel.left : undefined}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.domains.map((d, r) => (
            <tr key={`${d.domain}-${r}`} data-testid="ecmp-domain">
              {domainCells(d).map((t, i) => (
                <td key={i} className={i === 0 ? panel.left : t === "—" ? panel.gap : undefined} title={t === "—" && i > 0 ? (i === 7 ? "Поля не из оценки: у них нет STATUS" : "Нет значения: у блоков домена нет и A, и B") : undefined}>
                  {t}
                </td>
              ))}
            </tr>
          ))}
          {!result.domains.length && (
            <tr>
              <td className={panel.left} colSpan={DOMAIN_HEAD.length}>
                Доменов нет.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

export function Words({ result }: { result: EstimateCompare }) {
  return (
    <>
      {!result.field_written && (
        <p className={css.warn} data-testid="ecmp-not-written">
          Поле Δ «{result.field}» в модель не записано: раскрасить его в 3D нельзя.
        </p>
      )}
      {result.warnings.length > 0 && (
        <ul className={mod.notes} data-testid="ecmp-warnings">
          {result.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      )}
    </>
  );
}
