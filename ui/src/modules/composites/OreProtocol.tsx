/**
 * The protocol of оконтуривание of one hole (ТЗ 7.35 Д-26 «Результат» (4); ADR 0018 §9, решение 19): the form
 * И1, сл. 17 — samples as rows (№, от, до, l, C), the elementary units N / IN₀, per stage the прирезки C_s and
 * the units IN_s, the прослои of step 3, the сближенные of step 4, the final intervals; a cell spans its samples,
 * its tooltip is the line of the long form. «Список событий» — the long form itself. `ProtocolExport` — the
 * export of a fresh result («Сохранить как…» → the job `composite.ore_protocol`: XLSX or CSV).
 */
import { useMemo, useState, type CSSProperties } from "react";
import { bridge } from "../../bridge";
import { CLASS_STYLE, mix, BRAND, textOn } from "../../charts/column/classes";
import { askConfirm } from "../../shell/dialogs";
import { errorText } from "../../shell/props";
import css from "../../shell/PropsParts.module.css";
import { refresh as refreshJobs } from "../jobs/state";
import { overwriteQuestion, startWithOverwrite } from "../rasters/export";
import own from "./Composites.module.css";
import type { ResultRow } from "./api";
import { eventText, num, protocolExportBody, protocolFileName, protocolGrid, startProtocolExport, type OreAnswer, type ProtocolFormat, type Tone } from "./tools/ore";

const CELL: CSSProperties = { padding: "1px 4px", border: "1px solid var(--border)", whiteSpace: "nowrap", fontSize: 11, verticalAlign: "middle" };

function toneStyle(tone: Tone): CSSProperties {
  if (tone === "none") return { color: "var(--text-dim)" };
  if (tone === "reject") return { color: "var(--danger)" };
  const bg = tone === "pass" ? mix(BRAND.cobalt, BRAND.stone, 0.6) : CLASS_STYLE[tone].color;
  return { background: bg, color: textOn(bg) };
}

export function OreProtocol({ answer, field }: { answer: OreAnswer; field: string | null }) {
  const [long, setLong] = useState(false);
  const n = answer.samples.from.length;
  const grid = useMemo(() => protocolGrid(answer.events, n, answer.unit), [answer, n]);
  const grade = field ? (answer.samples.values[field] ?? []) : [];
  if (!n) return <p className={css.note}>У скважины {answer.hole} нет проб.</p>;
  if (!answer.events.length) return <p className={css.note}>Протокола нет: ядро не вернуло событий для скважины {answer.hole}.</p>;
  return (
    <div data-testid="ore-protocol">
      <label className={css.check}>
        <input type="checkbox" checked={long} onChange={(e) => setLong(e.target.checked)} />
        Список событий (длинная форма)
      </label>
      {long ? (
        <ol className={own.notes} style={{ color: "var(--text)" }} data-testid="ore-protocol-long">
          {answer.events.map((e, i) => (
            <li key={i}>{eventText(e)}</li>
          ))}
        </ol>
      ) : (
        <div style={{ overflow: "auto", maxHeight: 380 }}>
          <table style={{ borderCollapse: "collapse", fontVariantNumeric: "tabular-nums" }} data-testid="ore-protocol-grid">
            <caption className={css.note} style={{ textAlign: "left" }}>
              Скважина {answer.hole}: форма И1, сл. 17
            </caption>
            <thead>
              <tr>
                {["№", "от", "до", "l, м", `C${answer.unit ? `, ${answer.unit}` : ""}`].map((h) => (
                  <th key={h} style={CELL} scope="col">
                    {h}
                  </th>
                ))}
                {grid.heads.map((h) => (
                  <th key={h.key} colSpan={h.lanes} style={CELL} scope="col" title={h.hint}>
                    {h.title}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {grid.rows.map((row, r) => {
                const a = answer.samples.from[r];
                const b = answer.samples.to[r];
                const l = typeof a === "number" && typeof b === "number" ? b - a : null;
                return (
                  <tr key={r}>
                    <td style={CELL}>{r + 1}</td>
                    <td style={CELL}>{num(a, 2)}</td>
                    <td style={CELL}>{num(b, 2)}</td>
                    <td style={CELL}>{num(l, 2)}</td>
                    <td style={CELL}>{grade[r] === null || grade[r] === undefined ? "нет значения" : num(grade[r], 3)}</td>
                    {row.map((cell, k) =>
                      cell === "covered" ? null : cell === null ? (
                        <td key={k} style={CELL} />
                      ) : (
                        <td key={k} rowSpan={cell.rowSpan} title={cell.title} style={{ ...CELL, ...toneStyle(cell.tone) }} data-tone={cell.tone}>
                          {cell.text}
                        </td>
                      ),
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** «Сохранить как…» of the protocol of a result: the format, the holes of the sheets И1, the file → the job. */
export function ProtocolExport({ result, hole, onStarted }: { result: ResultRow; hole: string | null; onStarted?: (jobId: number) => void }) {
  const [format, setFormat] = useState<ProtocolFormat>("xlsx");
  const [holes, setHoles] = useState(hole ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const stale = result.stale ? `Результат устарел${result.reason ? ` (${result.reason})` : ""}: перестройте результат` : null;

  const save = async () => {
    if (stale || busy) return;
    setError(null);
    setDone(null);
    const path = await bridge()?.pickSave(protocolFileName(result.table ?? result.name, format), format);
    if (!path) return;
    const list = holes.split(/[,;]+/);
    const first = protocolExportBody(format, path, list, false);
    if (typeof first === "string") {
      setError(first);
      return;
    }
    setBusy(true);
    try {
      const s = await startWithOverwrite(
        (overwrite) => startProtocolExport(result.id, protocolExportBody(format, path, list, overwrite) as Record<string, unknown>),
        (files) => askConfirm(overwriteQuestion(files)),
      );
      if (s) {
        setDone(`Протокол записывается: задание №${s.id}`);
        onStarted?.(s.id);
        await refreshJobs().catch(() => undefined);
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <fieldset className={css.group} data-testid="ore-protocol-export">
      <legend>Экспорт протокола</legend>
      <label className={css.formRow}>
        <span>Формат</span>
        <select className={css.input} aria-label="Формат протокола" value={format} onChange={(e) => setFormat(e.target.value === "csv" ? "csv" : "xlsx")}>
          <option value="xlsx">Книга XLSX (параметры, интервалы, сводка, протокол, листы И1)</option>
          <option value="csv">CSV — длинная форма («;», UTF-8)</option>
        </select>
      </label>
      {format === "xlsx" && (
        <label className={css.formRow} title="Листы «Скв. ‹имя›» в форме И1, не больше 50">
          <span>Листы скважин</span>
          <input className={css.input} aria-label="Скважины для листов И1" value={holes} placeholder="имена через «,»" onChange={(e) => setHoles(e.target.value)} />
        </label>
      )}
      <div className={css.inline}>
        <span className={`${css.note} ${css.grow}`}>{stale ?? `Результат «${result.name}»`}</span>
        <button type="button" className={css.btn} disabled={stale !== null || busy} onClick={() => void save()} data-testid="ore-protocol-save">
          Сохранить как…
        </button>
      </div>
      {done && <p className={css.note}>{done}</p>}
      {error && (
        <p className={css.error} role="alert">
          {error}
        </p>
      )}
    </fieldset>
  );
}
