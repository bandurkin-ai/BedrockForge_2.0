/**
 * The parts of the window «Сравнить сценарии карьера» (ТЗ 8.11.5; ADR 0028 решение 27; docs/progress/e21-api.md §11): the notes of the core («Сетки различаются» is a banner),
 * the heads of both scenarios, the parameters that differ as a list, the shells by RF side by side with the difference A − B (a shell only one scenario has is marked and
 * has «—», not 0), NPV of the best and the worst case of the last shell, the blocks only A / only B has. A value that is not known is «—». Presentation only.
 */
import { fmtCount } from "./format";
import type { CompareAnswer, CompareSide } from "./resultsAnswers";
import { compareRows, DASH, metricInfo, paramLabel, paramValue, rfText, STATUS_TEXT, val } from "./resultsModel";
import type { ShellKey } from "./resultsAnswers";
import mod from "./Results.module.css";
import { warningText } from "./format";

/** The words of the core and the one banner of the window: «Сетки различаются». */
export function Notes({ answer }: { answer: CompareAnswer }) {
  const rest = answer.warnings.filter((w) => w.code !== "grids_differ");
  const differ = !answer.same_grid || answer.warnings.some((w) => w.code === "grids_differ");
  return (
    <>
      {differ && (
        <p className={mod.banner} role="alert" data-testid="pit-cmp-grids-differ">
          Сетки различаются: у сценариев разные модели или блоки. Сравниваются только оболочки по RF, NPV и параметры; блоки конечного карьера — нет.
        </p>
      )}
      {rest.length > 0 && (
        <ul className={mod.notes} data-testid="pit-cmp-warnings">
          {rest.map((w, i) => (
            <li key={`${w.code}-${i}`} data-code={w.code}>
              {w.side ? `${w.side.toUpperCase()}: ` : ""}
              {warningText(w)}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function SideCard({ id, side }: { id: "A" | "B"; side: CompareSide }) {
  return (
    <div className={mod.card} data-testid={`pit-cmp-card-${id}`}>
      <h4>
        {id} — {side.name || `Сценарий №${side.id}`}
      </h4>
      <p>
        Сценарий №{side.id}
        {side.version !== null ? `, версия ${side.version}` : ""}
        {side.solver ? `, решатель ${side.solver.toUpperCase()}` : ""}
      </p>
      <p>
        Модель «{side.model.name || DASH}»{side.model.cells_sha ? `, сетка ${side.model.cells_sha.slice(0, 8)}` : ""}
      </p>
    </div>
  );
}

export function Heads({ answer }: { answer: CompareAnswer }) {
  return (
    <div className={mod.heads} data-testid="pit-cmp-head">
      <SideCard id="A" side={answer.a} />
      <SideCard id="B" side={answer.b} />
    </div>
  );
}

/** The parameters of the two results that differ, as a list; a parameter only one side has shows «—» on the other. */
export function ParamsDiff({ answer }: { answer: CompareAnswer }) {
  if (!answer.params_diff.length) {
    return (
      <p className={mod.empty} data-testid="pit-cmp-params-same">
        Параметры сценариев не различаются.
      </p>
    );
  }
  return (
    <div data-testid="pit-cmp-params">
      <h5 className={mod.section}>Различия параметров: {answer.params_diff.length}</h5>
      <table className={mod.grid}>
        <thead>
          <tr>
            <th>Параметр</th>
            <th>A</th>
            <th>B</th>
          </tr>
        </thead>
        <tbody>
          {answer.params_diff.map((d, i) => (
            <tr key={`${d.param}-${i}`} data-param={d.param}>
              <td title={d.param}>{paramLabel(d.param)}</td>
              <td>{paramValue(d.a)}</td>
              <td>{paramValue(d.b)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The shells by RF: the shell of A, of B, the quantity of both and A − B. */
export function ShellsTable({ answer, metric }: { answer: CompareAnswer; metric: ShellKey }) {
  const info = metricInfo(metric);
  const rows = compareRows(answer, metric);
  const num = (v: number | null) => (metric === "blocks" && v !== null ? fmtCount(v) : val(v));
  if (!rows.length) {
    return (
      <p className={mod.empty} data-testid="pit-cmp-no-shells">
        В сценариях нет оболочек.
      </p>
    );
  }
  return (
    <div data-testid="pit-cmp-shells">
      <h5 className={mod.section}>
        Оболочки по RF · {info.label}, {info.unit}
      </h5>
      <table className={mod.grid}>
        <thead>
          <tr>
            <th>RF</th>
            <th className={mod.num}>№ в A</th>
            <th className={mod.num}>№ в B</th>
            <th className={mod.num}>A</th>
            <th className={mod.num}>B</th>
            <th className={`${mod.num} ${mod.delta}`}>A − B</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.rf} data-testid={`pit-cmp-row-${r.rf}`} data-status={r.status}>
              <td>
                {rfText(r.rf)}
                {r.status !== "both" && <span className={mod.only}>{STATUS_TEXT[r.status]}</span>}
              </td>
              <td className={mod.num}>{r.k_a ?? DASH}</td>
              <td className={mod.num}>{r.k_b ?? DASH}</td>
              <td className={mod.num}>{num(r.a)}</td>
              <td className={mod.num}>{num(r.b)}</td>
              <td className={`${mod.num} ${mod.delta}`}>{num(r.diff)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** NPV of the best and the worst case (and CF) of the last shell of each side — the totals of the core. */
export function Totals({ answer }: { answer: CompareAnswer }) {
  const t = answer.totals;
  if (!t) return null;
  const lines: { key: ShellKey; label: string }[] = [
    { key: "npv_best", label: "NPV лучшего случая, $" },
    { key: "npv_worst", label: "NPV худшего случая, $" },
    { key: "cf", label: "CF, $" },
    { key: "ore_t", label: "Руда, т" },
    { key: "waste_t", label: "Пустая порода, т" },
  ];
  return (
    <div data-testid="pit-cmp-totals">
      <h5 className={mod.section}>Итог по последней оболочке</h5>
      <table className={mod.grid}>
        <thead>
          <tr>
            <th>Показатель</th>
            <th className={mod.num}>A</th>
            <th className={mod.num}>B</th>
            <th className={`${mod.num} ${mod.delta}`}>A − B</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((l) => (
            <tr key={l.key} data-testid={`pit-cmp-total-${l.key}`}>
              <td>{l.label}</td>
              <td className={mod.num}>{val(t.a?.[l.key])}</td>
              <td className={mod.num}>{val(t.b?.[l.key])}</td>
              <td className={`${mod.num} ${mod.delta}`}>{val(t.diff?.[l.key])}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The blocks of the final pit that only A has and that only B has (the same grid only); otherwise «—» and the reason. */
export function OnlyBlocks({ answer }: { answer: CompareAnswer }) {
  const b = answer.blocks;
  return (
    <div data-testid="pit-cmp-blocks">
      <h5 className={mod.section}>Блоки конечного карьера</h5>
      {b ? (
        <table className={mod.grid}>
          <thead>
            <tr>
              <th>Где</th>
              <th className={mod.num}>Блоков</th>
              <th className={mod.num}>Тонн</th>
              <th className={mod.num}>Без тоннажа</th>
            </tr>
          </thead>
          <tbody>
            {(
              [
                ["Только в A", b.only_a],
                ["Только в B", b.only_b],
              ] as const
            ).map(([title, x]) => (
              <tr key={title} data-testid={`pit-cmp-only-${title === "Только в A" ? "a" : "b"}`}>
                <td>{title}</td>
                <td className={mod.num}>{x.blocks === null ? DASH : fmtCount(x.blocks)}</td>
                <td className={mod.num}>{val(x.tonnes)}</td>
                <td className={mod.num} title="Блоки без плотности: посчитаны, но не вошли в тонны">
                  {x.no_tonnes === null ? DASH : fmtCount(x.no_tonnes)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className={mod.empty} data-testid="pit-cmp-blocks-none">
          — Блоки не сравниваются: нужна одна модель и одна сетка у обоих сценариев, и поле PIT каждого сценария должно быть в модели.
        </p>
      )}
    </div>
  );
}
