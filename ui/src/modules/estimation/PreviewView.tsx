/**
 * The preview of one block in step 6 of the wizard «Оценка содержаний» (ТЗ 8.8.4; docs/progress/e18-api.md §7): the estimate and the
 * other output values of the block, the status, the passes tried (which one gave the estimate, why the others did not), the number of
 * samples and their weights λ (the heaviest first; the sum of the weights, the negative ones in red). A gap is a dash, never 0.
 */
import css from "../../shell/PropsParts.module.css";
import { STATUS_LABELS } from "./api";
import { FIELD_TITLES, OUTPUT_KEYS } from "./formSearch";
import { PASS_REASONS, REASON_LABELS, type PreviewAnswer } from "./previewApi";
import { g, nf } from "./Parts";
import mod from "./Wizard.module.css";

const SHOWN = 12;

/** The weights of the samples chosen: the heaviest first. */
export const weighted = (a: PreviewAnswer) => a.samples.filter((s) => s.lambda !== null).sort((x, y) => Math.abs(y.lambda!) - Math.abs(x.lambda!));

export function PreviewView({ data, unit }: { data: PreviewAnswer; unit: string | null }) {
  const chosen = weighted(data);
  const sum = chosen.reduce((t, s) => t + (s.lambda ?? 0), 0);
  const keys = OUTPUT_KEYS.filter((k) => k in data.values);
  const otherKeys = Object.keys(data.values).filter((k) => !(OUTPUT_KEYS as readonly string[]).includes(k));
  const status = STATUS_LABELS[data.status] ?? String(data.status);
  return (
    <div className={mod.preview} data-testid="est-preview-result">
      <p className={mod.verdict} data-status={data.status} data-testid="est-preview-status">
        Блок {data.at.ijk ? data.at.ijk.join("; ") : `№${data.at.row}`}
        {data.label ? `, домен «${data.label}»` : ""}: <strong>{status}</strong>
        {data.pass !== null && data.status === 0 ? `, проход ${data.pass}` : ""}.
      </p>
      {data.status === 0 && (
        <dl className={mod.facts} data-testid="est-preview-values">
          {[...keys, ...otherKeys].map((k) => (
            <div key={k} style={{ display: "contents" }}>
              <dt title={FIELD_TITLES[k]}>{k}</dt>
              <dd data-testid={`est-preview-${k}`}>
                {g(data.values[k])}
                {k === "EST" && unit ? ` ${unit}` : ""}
              </dd>
            </div>
          ))}
          {data.mu !== null && (
            <div style={{ display: "contents" }}>
              <dt title="Множитель Лагранжа">μ</dt>
              <dd data-testid="est-preview-mu">{g(data.mu)}</dd>
            </div>
          )}
        </dl>
      )}
      <div className={mod.tableWrap}>
        <table className={mod.table} data-testid="est-preview-passes" aria-label="Проходы поиска у блока">
          <thead>
            <tr>
              <th className={mod.left}>Проход</th>
              <th className={mod.left}>Итог</th>
              <th>Проб</th>
            </tr>
          </thead>
          <tbody>
            {data.passes.map((p) => (
              <tr key={p.pass} data-ok={p.ok}>
                <td className={mod.left}>{p.pass}</td>
                <td className={mod.left}>{p.ok ? "оценка получена" : p.reason ? PASS_REASONS[p.reason] : "—"}</td>
                <td>{nf(p.n)}</td>
              </tr>
            ))}
            {data.passes.length === 0 && (
              <tr>
                <td className={mod.left} colSpan={3}>
                  ни один проход не нашёл проб
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <p className={css.note} data-testid="est-preview-counts">
        Кандидатов в эллипсоиде {nf(data.counts.candidates)}, выбрано {nf(data.counts.selected)}
        {data.ellipsoid ? `; эллипсоид прохода ${data.ellipsoid.pass}: радиусы ${[data.ellipsoid.r1, data.ellipsoid.r2, data.ellipsoid.r3].map((x) => g(x)).join(" × ")} м` : ""}.
      </p>
      {chosen.length > 0 && (
        <div className={mod.tableWrap}>
          <table className={mod.table} data-testid="est-preview-samples" aria-label="Пробы и их веса">
            <thead>
              <tr>
                <th className={mod.left}>Скважина</th>
                <th className={mod.left}>От–до, м</th>
                <th>Расстояние, м</th>
                <th>Значение{unit ? `, ${unit}` : ""}</th>
                <th>Вес λ</th>
                <th className={mod.left}>Причина</th>
              </tr>
            </thead>
            <tbody>
              {chosen.slice(0, SHOWN).map((s) => (
                <tr key={s.i} data-testid={`est-preview-sample-${s.i}`}>
                  <td className={mod.left}>{s.hole}</td>
                  <td className={mod.left}>
                    {g(s.from)}–{g(s.to)}
                  </td>
                  <td>{g(s.dist)}</td>
                  <td>{g(s.z)}</td>
                  <td style={s.lambda! < 0 ? { color: "var(--danger)" } : undefined}>{s.lambda!.toLocaleString("ru-RU", { maximumFractionDigits: 4 })}</td>
                  <td className={mod.left}>{REASON_LABELS[s.reason] ?? s.reason}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td className={mod.left} colSpan={4}>
                  {chosen.length > SHOWN ? `Показаны ${SHOWN} из ${chosen.length}; сумма весов` : "Сумма весов"}
                </td>
                <td data-testid="est-preview-lambda-sum">{sum.toLocaleString("ru-RU", { maximumFractionDigits: 4 })}</td>
                <td />
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </div>
  );
}
