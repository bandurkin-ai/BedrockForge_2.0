/**
 * The parts of «Свойства» of an implicit model that belong to Э19б (ТЗ 7.35 Д-24, Д-31, Д-39; ADR 0026 решения 19–22): «Стек» (the order and the relations of the
 * domains), «Разломы» (the radius, the data, the regions of the wings — a region is the space on a side of the fault, not a rock), «Смещение» (the result of
 * the build, read only) and «Система жил» (the pairs of the veins, the lines of intersection that can be shown in 3D). Every command is the registry's.
 */
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { useSel } from "../estimation/hooks";
import type { OutputInfo } from "./api";
import { faultRows, offsetRows, pairRows, pairsOf, stackSummary } from "./stackFacts";
import { domainsOfMeta, faultsOfMeta, groupsOfMeta } from "./stackModel";
import type { VeinPair } from "./stackApi";

type Run = (id: string, args: Record<string, unknown>) => unknown;

/** The table of the pairs of veins: the pair, the length of the line of intersection, the volume of the common zone BEFORE the younger vein cut the older one. */
export function VeinPairsTable({ pairs, names }: { pairs: readonly VeinPair[]; names?: ReadonlyMap<string, string> }) {
  const rows = pairRows(pairs, names);
  if (!rows.length) return <p className={css.note} data-testid="vsys-no-pairs">Пересечения жил появятся после построения модели («Обновить»).</p>;
  return (
    <div className={css.tableWrap}>
      <table className={css.table} data-testid="vsys-pairs">
        <thead>
          <tr>
            <th>Пара жил</th>
            <th title="Длина линии пересечения, м">Линия, м</th>
            <th title="Объём общей зоны до того, как молодая жила вырезала её из древней, м³">Общая зона до обрезки, м³</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.pair}>
              <td>{r.pair}</td>
              <td>{r.length}</td>
              <td>{r.common}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The lines of intersection of the veins: a layer of strings each; the check box shows it in 3D (`layer.show {layer: "lines"}`). */
export function LineOutputs({ lines, run }: { lines: readonly OutputInfo[]; run: Run }) {
  const app = useApp();
  const hidden = useSel(app.store, (s) => s.view.hidden);
  if (!lines.length) return null;
  return (
    <ul className={css.list} data-testid="vsys-lines" style={{ listStyle: "none", paddingLeft: 0 }}>
      {lines.map((o) => {
        const id = o.line_layer_id;
        const on = id !== null && !hidden.includes(`ln:${id}`);
        return (
          <li key={id ?? o.name}>
            <label className={css.check} title={`${o.name}: слой линий проекта (редактор стрингов), перезаписывается командой «Обновить модель»`}>
              <input type="checkbox" checked={on} disabled={id === null} data-testid={`vsys-line-${id}`} onChange={(e) => id !== null && void run("layer.show", { layer: "lines", id, visible: e.target.checked })} />
              {o.name}
              {o.length !== null ? ` — ${o.length.toLocaleString("ru-RU", { maximumFractionDigits: 1 })} м` : ""}
            </label>
          </li>
        );
      })}
    </ul>
  );
}

export interface StackPropsArgs {
  id: number;
  meta: Record<string, unknown>;
  outputs: readonly OutputInfo[];
  run: Run;
  /** why a command is unavailable (the registry's words); null — it is */
  reason: (command: string) => string | null;
}

/** The panels of the stack, the faults, the offset and the systems of veins; each is drawn only when the model has something to say. */
export function StackProps({ id, meta, outputs, run, reason }: StackPropsArgs) {
  const domains = domainsOfMeta(meta);
  const faults = faultsOfMeta(meta);
  const systems = groupsOfMeta(meta).filter((g) => g.type === "vein_system");
  const faultInfo = faultRows(meta);
  const offsets = offsetRows(meta);
  const stack = stackSummary(meta);
  const btn = (label: string, command: string, args: Record<string, unknown> = {}, testId = command.split(".")[1]) => {
    const why = reason(command);
    return (
      <button type="button" className={css.btn} disabled={why !== null} title={why ? `${label}\nНедоступно: ${why}` : label} data-testid={`props-implicit-${testId}`} onClick={() => void run(command, { model_id: id, ...args })}>
        {label}
      </button>
    );
  };
  return (
    <>
      <fieldset className={css.group} data-testid="props-implicit-stack">
        <legend>Стек</legend>
        {domains.length ? (
          <ol className={css.list} data-testid="props-implicit-stack-lines">
            {stack.lines.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ol>
        ) : (
          <p className={css.note}>В модели нет доменов литологии.</p>
        )}
        {stack.dropped && (
          <p className={css.note} data-testid="props-implicit-stack-dropped">
            {stack.dropped}
          </p>
        )}
        <div className={css.row}>
          {btn("Стек отношений…", "implicit.relations")}
          {btn("Разлом…", "implicit.fault", {}, "fault")}
          {btn("Система жил…", "implicit.vein_system", {}, "vein_system")}
        </div>
      </fieldset>

      {faults.length > 0 && (
        <fieldset className={css.group} data-testid="props-implicit-faults">
          <legend>Разломы</legend>
          <div className={css.tableWrap}>
            <table className={css.table}>
              <thead>
                <tr>
                  <th>Разлом</th>
                  <th title="Радиус влияния R_F">Радиус</th>
                  <th>Точек</th>
                  <th title="Область крыла — пространство по одну сторону поверхности разлома, не порода: объёмы областей не складываются с объёмами пород">Области крыльев</th>
                  <th title="Невязка в данных разлома, м">Невязка</th>
                  <th>Рассекает</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {faultInfo.map((f) => (
                  <tr key={f.id} data-testid={`props-implicit-fault-${f.id}`}>
                    <td>{f.name}</td>
                    <td>{f.radius}</td>
                    <td>{f.points}</td>
                    <td title="Область, не порода">{f.regions.length ? f.regions.join("; ") : "—"}</td>
                    <td>{f.residual}</td>
                    <td>{f.cuts.length ? f.cuts.join(", ") : "—"}</td>
                    <td>
                      <button type="button" className={css.btn} disabled={reason("implicit.fault") !== null} title={reason("implicit.fault") ?? `Изменить разлом «${f.name}»`} onClick={() => void run("implicit.fault", { model_id: id, group_id: f.id })}>
                        Изменить…
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className={css.note}>Области крыльев — части пространства по разные стороны разлома, не породы: их объёмы в сумму объёмов пород не входят.</p>
        </fieldset>
      )}

      {offsets.length > 0 && (
        <fieldset className={css.group} data-testid="props-implicit-offset">
          <legend>Смещение по разлому (только чтение)</legend>
          <div className={css.tableWrap}>
            <table className={css.table}>
              <thead>
                <tr>
                  <th>Группа</th>
                  <th>Крыло</th>
                  <th>Разломы</th>
                  <th title="Среднее смещение, м">Среднее, м</th>
                  <th title="Наибольшее смещение, м">Наибольшее, м</th>
                </tr>
              </thead>
              <tbody>
                {offsets.map((o, i) => (
                  <tr key={i}>
                    <td>{o.group}</td>
                    <td>{o.wing}</td>
                    <td>{o.faults}</td>
                    <td>{o.mean}</td>
                    <td>{o.max}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className={css.note}>Смещение — результат разности полей двух крыльев, а не заданный вектор: изменить его можно только данными разлома.</p>
        </fieldset>
      )}

      {systems.map((g) => {
        const lines = outputs.filter((o) => o.kind === "line" && o.group === g.id);
        const names = new Map<string, string>();
        for (const o of outputs) if (o.group === g.id && o.code) names.set(o.code, o.name.split(" · ").pop() ?? o.code);
        return (
          <fieldset key={g.id} className={css.group} data-testid={`props-implicit-system-${g.id}`}>
            <legend>Система жил «{g.name}»</legend>
            <p className={css.note}>Жилы по возрасту (первая самая молодая и режет древнюю): {g.codes.join(", ") || "—"}.</p>
            <VeinPairsTable pairs={pairsOf(meta, g.id)} names={names} />
            <LineOutputs lines={lines} run={run} />
            <div className={css.row}>{btn("Изменить систему…", "implicit.vein_system", { group_id: g.id }, `system-edit-${g.id}`)}</div>
          </fieldset>
        );
      })}
    </>
  );
}
