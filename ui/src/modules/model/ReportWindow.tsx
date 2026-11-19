/**
 * The window «Отчёт операции» (ТЗ 7.23): what was made (the wireframes with their volumes, the layers of lines),
 * «было → стало» (`report.ts`: «Исправить» — the checks of the wireframe, «Упростить», «Сгладить» — the volume
 * and the triangles), the numbers of the operation (the change of the volume and the deviation, cut and fill),
 * what every step changed and the warnings; «Скопировать» puts it as text on the clipboard.
 */
import { useState } from "react";
import { Facts } from "../../shell/PropsParts";
import { useStore } from "../../shell/useStore";
import { closeWindow } from "../../shell/windows";
import css from "../../shell/PropsParts.module.css";
import { sceneObjects } from "../scene/objects";
import { reportText, reportView } from "./report";
import type { ReportProps } from "./run";

const volumeText = (v: number | null | undefined): string => (typeof v === "number" && Number.isFinite(v) ? `объём ${v.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} м³` : "не замкнут");

export function ReportWindow({ winId, ...p }: { winId: string } & ReportProps) {
  const o = useStore(sceneObjects, (s) => s);
  const [copied, setCopied] = useState<string | null>(null);
  const view = reportView(p.op, p.report, p.warnings);
  const layers = o.files.flatMap((f) => f.layers);
  const made = [
    ...p.wireframeIds.map((id, k) => `«${o.wireframes.find((w) => w.id === id)?.name ?? `каркас №${id}`}» (${volumeText(p.volumes[k])})`),
    ...p.layerIds.map((id) => `слой «${layers.find((l) => l.id === id)?.name ?? `№${id}`}»`),
  ];
  const title = `${p.title}${p.name ? `: ${p.name}` : ""}`;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(reportText(title, view, made));
      setCopied("Отчёт скопирован.");
    } catch {
      setCopied("Буфер обмена недоступен.");
    }
  };
  return (
    <div className={css.win} data-testid="op-report" data-op={p.op}>
      <div className={css.winBody}>
        <h4 className={css.name}>{title}</h4>
        {p.rebuildOf !== null && <p className={css.note}>Перестроено: прежний объект №{p.rebuildOf} не изменён.</p>}
        {made.length > 0 ? (
          <div className={css.group}>
            <span className={css.note}>Создано</span>
            <ul className={css.list} data-testid="op-report-made">
              {made.map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          </div>
        ) : (
          <p className={css.note}>Объекты не созданы: только отчёт.</p>
        )}
        {view.compare && view.compare.length > 0 && (
          <div className={css.tableWrap}>
            <table className={css.table} data-testid="op-report-compare">
              <thead>
                <tr>
                  <th>Показатель</th>
                  <th>Было</th>
                  <th>Стало</th>
                </tr>
              </thead>
              <tbody>
                {view.compare.map((r) => (
                  <tr key={r.label} data-changed={r.changed ? "yes" : "no"} style={r.changed ? { fontWeight: 600, color: "var(--highlight)" } : undefined}>
                    <td>{r.label}</td>
                    <td>{r.before}</td>
                    <td>{r.after}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div data-testid="op-report-facts">
          <Facts facts={view.facts} />
        </div>
        {view.steps.length > 0 && (
          <ul className={css.list} data-testid="op-report-steps">
            {view.steps.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        )}
        {view.warnings.map((w) => (
          <p key={w} className={css.warn}>
            {w}
          </p>
        ))}
      </div>
      <div className={css.footer}>
        <span className={`${css.note} ${css.grow}`}>{copied ?? ""}</span>
        <button type="button" className={css.btn} onClick={() => void copy()}>
          Скопировать
        </button>
        <button type="button" className={css.primary} onClick={() => closeWindow(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
