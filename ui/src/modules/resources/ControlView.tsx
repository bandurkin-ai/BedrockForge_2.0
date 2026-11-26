/**
 * The block «Контроль» of the result panel (ТЗ 8.10 «Вывод»; ADR 0027 решение 13; Д-27, ТД27-2): the sum of the groups against the total (tonnes, volume, metal; the
 * norm is 1e-12), the blocks that are not counted (by the restrictor, without volume, without density — the tonnes of the blocks on the density by default), the blocks
 * without grade of each element, the statuses of the estimate (blocks, volume and the share of the volume of the restrictor by `<префикс>STATUS`: 1, 2, 3), the check of
 * the volume of each closed wireframe of the restrictor against its signed volume, and the warnings of the core. A gap is «нет значения», never 0. No state.
 */
import type { Control, ReportResult, Warning } from "./answers";
import { fmt, fmtCount, relText, warningText } from "./format";
import css from "./Panel.module.css";

export { relText };

export const shareText = (share: number | null): string => (share === null ? "нет значения" : `${fmt(share * 100)} %`);

/** The warnings of the whole result in one list: the report, the control, the form (a text twice is told once). */
export function allWarnings(r: ReportResult): Warning[] {
  const seen = new Set<string>();
  return [...r.warnings, ...(r.control?.warnings ?? []), ...(r.form?.warnings ?? [])].filter((w) => {
    const key = `${w.code}|${w.message}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function SumGroups({ c, elements }: { c: NonNullable<Control["sum_groups"]>; elements: ReportResult["elements"] }) {
  const rows: { name: string; d: { sum: number | null; total: number | null; rel: number | null } }[] = [
    { name: "Тоннаж, т", d: c.tonnes },
    { name: "Объём, м³", d: c.volume },
    ...Object.entries(c.metal).map(([f, d]) => ({ name: `Металл ${f}, ${elements.find((e) => e.field === f)?.metal_base ?? "ед."}`, d })),
  ];
  return (
    <div className={css.tableWrap} style={{ maxHeight: "none" }}>
      <table className={css.table} data-testid="res-control-sum">
        <thead>
          <tr>
            <th className={css.left}>Величина</th>
            <th>Сумма групп</th>
            <th>Итог</th>
            <th>Относительная разность</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name}>
              <td className={css.left}>{r.name}</td>
              <td>{fmt(r.d.sum)}</td>
              <td>{fmt(r.d.total)}</td>
              <td>{relText(r.d.rel)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ControlView({ result }: { result: ReportResult }) {
  const c = result.control;
  const ex = result.excluded;
  const warnings = allWarnings(result);
  if (!c) return <p className={css.note}>У отчёта нет контроля: он записывается вместе с таблицей.</p>;
  const grade = Object.entries(c.no_grade);
  const status = Object.entries(c.status);
  return (
    <div className={css.control} data-testid="res-control">
      <section>
        <h5>Сумма групп и итог</h5>
        {c.sum_groups ? (
          <>
            <p className={c.sum_groups.ok ? css.ok : css.warn} data-testid="res-control-sum-ok">
              {c.sum_groups.ok ? `Сумма групп равна итогу (норма ${relText(c.tolerance)}).` : `Сумма групп не равна итогу: отклонение больше нормы ${relText(c.tolerance)}.`}
            </p>
            <SumGroups c={c.sum_groups} elements={result.elements} />
          </>
        ) : (
          <p className={css.note}>Без групп итог — единственная строка: сравнивать не с чем.</p>
        )}
      </section>

      <section>
        <h5>Блоки вне расчёта и без данных</h5>
        <dl className={css.facts2} data-testid="res-control-blocks">
          <dt>Отсеяны ограничителем</dt>
          <dd>{fmtCount(ex.filtered)}</dd>
          <dt>Без объёма (пустая доля FRAC)</dt>
          <dd>{fmtCount(ex.no_volume)}</dd>
          <dt>Без плотности</dt>
          <dd data-testid="res-control-no-density">
            {fmtCount(c.no_density.blocks)} бл.{c.no_density.blocks ? `, объём ${fmt(c.no_density.volume)} м³ (тоннаж неизвестен)` : ""}
          </dd>
          {c.default_density && (
            <>
              <dt>По плотности по умолчанию</dt>
              <dd data-testid="res-control-default-density">
                {fmtCount(c.default_density.blocks)} бл., тоннаж {fmt(c.default_density.tonnes)} т
              </dd>
            </>
          )}
          {grade.map(([f, g]) => (
            <ElementGap key={f} field={f} blocks={g.blocks} tonnes={g.tonnes} />
          ))}
        </dl>
      </section>

      {status.length > 0 && (
        <section data-testid="res-control-status">
          <h5>Статусы оценки</h5>
          {status.map(([f, s]) => (
            <div key={f} className={css.tableWrap} style={{ maxHeight: "none", marginBottom: 4 }}>
              <table className={css.table} data-testid={`res-control-status-${f}`}>
                <thead>
                  <tr>
                    <th className={css.left}>
                      {f} · {s.prefix}STATUS
                    </th>
                    <th>Блоков</th>
                    <th>Объём, м³</th>
                    <th>Доля объёма ограничителя</th>
                  </tr>
                </thead>
                <tbody>
                  {s.items.map((i) => (
                    <tr key={i.code}>
                      <td className={css.left}>
                        {i.code} — {i.label}
                      </td>
                      <td>{fmtCount(i.blocks)}</td>
                      <td>{fmt(i.volume)}</td>
                      <td>{shareText(i.share)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </section>
      )}

      {c.wireframes.length > 0 && (
        <section data-testid="res-control-wireframes">
          <h5>Объём каркасов ограничителя</h5>
          <div className={css.tableWrap} style={{ maxHeight: "none" }}>
            <table className={css.table}>
              <thead>
                <tr>
                  <th className={css.left}>Каркас</th>
                  <th>Объём по блокам, м³</th>
                  <th>Объём каркаса, м³</th>
                  <th>Расхождение</th>
                </tr>
              </thead>
              <tbody>
                {c.wireframes.map((w, i) => (
                  <tr key={w.wireframe_id ?? i} className={w.warning ? css.warn : undefined} data-warning={w.warning || undefined}>
                    <td className={css.left}>
                      {w.name}
                      {w.outside_extent ? " — каркас за охватом модели" : ""}
                    </td>
                    <td>{fmt(w.volume_restrictor)}</td>
                    <td>{fmt(w.volume_wireframe)}</td>
                    <td>{w.diff_pct === null ? "нет значения" : `${fmt(w.diff_pct)} %`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {warnings.length > 0 && (
        <section>
          <h5>Предупреждения</h5>
          <ul className={css.warns} data-testid="res-control-warnings">
            {warnings.map((w, i) => (
              <li key={i}>{warningText(w)}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function ElementGap({ field, blocks, tonnes }: { field: string; blocks: number; tonnes: number | null }) {
  return (
    <>
      <dt>Без содержания {field}</dt>
      <dd data-testid={`res-control-no-grade-${field}`}>
        {fmtCount(blocks)} бл.{blocks ? `, ${fmt(tonnes)} т (в среднее и металл не входят)` : ""}
      </dd>
    </>
  );
}
