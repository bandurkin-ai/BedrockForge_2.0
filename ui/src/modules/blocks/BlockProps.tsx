/**
 * «Свойства» of a block model (ADR 0017 §3): the name with «Переименовать…» and «Удалить…» (`object.rename`,
 * `object.delete` — own windows), «Показать в 3D» disabled with the reason of its stage (Э17), the grid, the
 * fields with their statistics (`GET /blocks/{id}`), the source and what the import did not carry over.
 */
import { useEffect } from "react";
import { useApp } from "../../shell/AppContext";
import { Facts, useRun } from "../../shell/PropsParts";
import { useRegistryVersion, useStore } from "../../shell/useStore";
import css from "../../shell/PropsParts.module.css";
import { fieldRow, gridFacts, sourceFacts } from "./props";
import { blocksStore, loadBlockDetails } from "./state";

export function BlockProps({ id }: { id: number }) {
  const app = useApp();
  useRegistryVersion(app.registry);
  const brief = useStore(blocksStore, (s) => s.list.find((b) => b.id === id));
  const details = useStore(blocksStore, (s) => s.details[id]);
  const detailError = useStore(blocksStore, (s) => s.detailErrors[id]);
  const { run, error } = useRun();
  const version = brief?.version;
  const fresh = details !== undefined && details.version === version;
  useEffect(() => {
    if (version !== undefined && !fresh) void loadBlockDetails(id);
  }, [id, version, fresh]);
  if (!brief) return null;
  const b = fresh ? details : brief; // the list has the grid and the fields without stats: shown at once
  const show = app.registry.reason("blocks.show");
  const exportReason = app.registry.reason("omf.export");
  return (
    <section className={css.panel} aria-label={`Свойства: ${b.name}`} data-testid="props-block" data-state={fresh ? "ready" : detailError ? "failed" : "loading"}>
      <header className={css.head}>
        <span className={css.kind}>Блочная модель</span>
        <h4 className={css.name} title={b.name}>
          {b.name}
        </h4>
        <button type="button" className={css.btn} onClick={() => void run("object.rename", { kind: "blocks", id })}>
          Переименовать…
        </button>
      </header>
      <div className={css.row}>
        <button type="button" className={css.btn} disabled={show !== null} title={show ? `Показать в 3D\nНедоступно: ${show}` : "Показать в 3D"} data-testid="props-block-show">
          Показать в 3D
        </button>
        <button type="button" className={css.btn} disabled={exportReason !== null} title={exportReason ? `Экспорт OMF\nНедоступно: ${exportReason}` : "Записать модель в файл OMF"} onClick={() => void run("omf.export", { dataset_ids: String(id) })}>
          Экспорт OMF…
        </button>
        <button type="button" className={css.btn} onClick={() => void run("object.delete", { kind: "blocks", id })}>
          Удалить…
        </button>
      </div>
      {show && <p className={css.note}>{show}: ячейки, раскраска по полю, фильтр.</p>}
      <fieldset className={css.group} data-testid="props-block-grid">
        <legend>Сетка</legend>
        <Facts facts={gridFacts(b)} />
      </fieldset>
      <fieldset className={css.group} data-testid="props-block-fields" data-fields={b.fields.length}>
        <legend>Поля</legend>
        {b.fields.length ? (
          <div className={css.tableWrap}>
            <table className={css.table}>
              <thead>
                <tr>
                  <th>Поле</th>
                  <th>Тип</th>
                  <th>Ед.</th>
                  <th title="Занятые ячейки со значением">Значений</th>
                  <th title="Занятые ячейки без значения">Пусто</th>
                  <th>Мин.</th>
                  <th>Макс.</th>
                  <th>Среднее</th>
                  <th title="Стандартное отклонение по всем значениям">Ст. откл.</th>
                </tr>
              </thead>
              <tbody>
                {b.fields.map(fieldRow).map((f) => (
                  <tr key={f.name} title={f.labels || undefined}>
                    <td>{f.name}</td>
                    <td>{f.kind}</td>
                    <td>{f.unit}</td>
                    <td>{f.count}</td>
                    <td>{f.missing}</td>
                    {f.labels ? (
                      <td colSpan={4}>{f.labels}</td>
                    ) : (
                      <>
                        <td>{f.min}</td>
                        <td>{f.max}</td>
                        <td>{f.mean}</td>
                        <td>{f.std}</td>
                      </>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className={css.note}>У модели нет полей: только занятые ячейки.</p>
        )}
        {!fresh && !detailError && <p className={css.note}>Статистика полей читается…</p>}
        {detailError && (
          <p className={css.error} role="alert">
            {detailError}
          </p>
        )}
      </fieldset>
      {sourceFacts(b.source).length > 0 && (
        <fieldset className={css.group} data-testid="props-block-source">
          <legend>Источник</legend>
          <Facts facts={sourceFacts(b.source)} />
        </fieldset>
      )}
      {b.warnings.length > 0 && (
        <fieldset className={css.group} data-testid="props-block-warnings">
          <legend>Не перенесено при импорте</legend>
          <ul className={css.list}>
            {b.warnings.map((w) => (
              <li key={w} className={css.warn}>
                {w}
              </li>
            ))}
          </ul>
        </fieldset>
      )}
      {error && (
        <p className={css.error} role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
