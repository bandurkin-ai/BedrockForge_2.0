/**
 * The parts of the window «Сравнить отчёты» (ТЗ 7.35 Д-37; ADR 0027 решение 26): the notes of the core («сетки различаются» is a banner), the head with the
 * versions of the inputs of both reports (name, fingerprint, date — Д-35), the table «A | B | разность» of T, ḡ and M of one element. A row that is only in
 * one report is marked and has no difference («—»); a value no block has is «нет значения»; nothing is 0 that is not. Presentation only.
 */
import type { CompareAnswer, CompareSide } from "./answers";
import { dateText, fpText, headLines, kindText, notes, sideState, tableRows, GAP, type CompareCell, type Trio } from "./compareModel";
import mod from "./Compare.module.css";
import panel from "./Panel.module.css";

/** The words of the core and the one banner of the window: «Сетки различаются». */
export function Notes({ answer }: { answer: CompareAnswer }) {
  const rest = notes(answer).filter((n) => n.code !== "grids_differ");
  return (
    <>
      {answer.grids_differ && (
        <p className={mod.banner} role="alert" data-testid="cmp-grids-differ">
          Сетки различаются: модели или их блоки разные. Сравниваются только отчёты (числа по строкам), не блоки.
        </p>
      )}
      {rest.length > 0 && (
        <ul className={mod.notes} data-testid="cmp-warnings">
          {rest.map((n, i) => (
            <li key={`${n.code}-${i}`} data-code={n.code}>
              {n.text}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function SideCard({ id, side }: { id: "A" | "B"; side: CompareSide }) {
  const date = dateText(side.created_at);
  return (
    <div className={mod.card} data-testid={`cmp-card-${id}`}>
      <h4>
        {id} — {side.name || `Отчёт №${side.id}`}
      </h4>
      <p>
        Отчёт №{side.id}
        {side.version !== null ? `, версия ${side.version}` : ""}
        {date ? `, ${date}` : ""}
      </p>
      <p>
        Модель «{side.model.name || "—"}»{side.model.cells_sha ? `, сетка ${fpText(side.model.cells_sha)}` : ""}
      </p>
      <p className={side.stale ? mod.stale : undefined} data-testid={`cmp-state-${id}`}>
        Состояние: {sideState(side)}
      </p>
    </div>
  );
}

/** The versions of the inputs of both reports side by side: what changed between the two reports shows. */
export function Head({ answer }: { answer: CompareAnswer }) {
  const lines = headLines(answer);
  return (
    <div data-testid="cmp-head">
      <div className={mod.heads}>
        <SideCard id="A" side={answer.a} />
        <SideCard id="B" side={answer.b} />
      </div>
      {lines.length > 0 && (
        <>
          <h5 className={mod.section}>Версии входов отчётов</h5>
          <table className={mod.inputs} data-testid="cmp-inputs">
            <thead>
              <tr>
                <th>Вход</th>
                <th>A: имя · отпечаток</th>
                <th>B: имя · отпечаток</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((l) => (
                <tr key={l.key} data-changed={l.a?.changed === true || undefined}>
                  <td>{kindText((l.a ?? l.b)?.kind ?? "")}</td>
                  {[l.a, l.b].map((x, i) => (
                    <td key={i}>
                      {x ? (
                        <>
                          {x.name} · <span className={mod.fp}>{x.fp}</span>
                        </>
                      ) : (
                        <span className={panel.gap} title={`Входа нет в отчёте ${i === 0 ? "A" : "B"}`}>
                          {GAP}
                        </span>
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {lines.some((l) => l.a?.changed) && (
            <p className={panel.note} data-testid="cmp-inputs-changed">
              Отпечаток входа различается: этот вход изменился между отчётами.
            </p>
          )}
        </>
      )}
    </div>
  );
}

const hint = (c: CompareCell, side: "A" | "B" | "Δ"): string | undefined => {
  if (c.kind === "absent") return side === "Δ" ? "Строка есть только в одном отчёте: разности нет (это пропуск, не 0)" : `Строки нет в отчёте ${side}`;
  if (c.kind === "none") return "В блоках строки нет значения этого содержания: это не 0, а пропуск";
  return undefined;
};

function Cells({ t }: { t: Trio }) {
  const td = (c: CompareCell, side: "A" | "B" | "Δ", sep = false) => (
    <td className={`${c.kind === "value" ? "" : panel.gap}${sep ? ` ${mod.delta}` : ""}`.trim() || undefined} title={hint(c, side)} data-kind={c.kind}>
      {c.text}
    </td>
  );
  return (
    <>
      {td(t.a, "A", true)}
      {td(t.b, "B")}
      {td(t.d, "Δ")}
    </>
  );
}

/** The table of the comparison: the group and the cut-off, then T, ḡ and M of the element — A, B and the difference. */
export function CompareTable({ answer, element, onlyDiff }: { answer: CompareAnswer; element: string | null; onlyDiff: boolean }) {
  const rows = tableRows(answer, element, onlyDiff);
  const key = answer.keys.length ? answer.keys.join(" / ") : "Группа";
  const span = (title: string) => (
    <th className={panel.span} colSpan={3}>
      {title}
    </th>
  );
  const sub = () => ["A", "B", "Разность"].map((x, i) => <th key={x} className={i === 0 ? panel.sep : undefined}>{x}</th>);
  return (
    <div className={panel.tableWrap} data-testid="cmp-table-wrap">
      <table className={panel.table} data-testid="cmp-table" data-rows={rows.length}>
        <thead>
          <tr>
            <th className={panel.left} rowSpan={2}>
              {key}
            </th>
            <th className={panel.left} rowSpan={2}>
              Борт
            </th>
            {span("Тоннаж T, т")}
            {span(`Содержание ḡ${element ? `, ${element}` : ""}`)}
            {span(`Металл M${element ? `, ${element}` : ""}`)}
          </tr>
          <tr>
            {sub()}
            {sub()}
            {sub()}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.index} className={r.total ? panel.total : undefined} data-only={r.only ?? undefined} data-testid="cmp-row">
              <td className={panel.left}>
                {r.group}
                {r.note && (
                  <span className={mod.only} data-testid="cmp-only">
                    {r.note}
                  </span>
                )}
              </td>
              <td className={panel.left}>{r.title}</td>
              <Cells t={r.tonnes} />
              <Cells t={r.grade} />
              <Cells t={r.metal} />
            </tr>
          ))}
          {!rows.length && (
            <tr>
              <td className={panel.left} colSpan={11} data-testid="cmp-no-rows">
                {onlyDiff ? "Различий нет: во всех строках разность равна нулю." : "В отчётах нет строк."}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
