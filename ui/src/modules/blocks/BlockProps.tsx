/**
 * «Свойства» of a block model (ADR 0017 §3; ADR 0023; ТЗ 7.31): the name with «Переименовать…» and «Удалить…»
 * (`object.rename`, `object.delete` — own windows), «Экспорт…» (`blocks.export`), «Показать в 3D» / «Скрыть в 3D»
 * (`blocks.show`, Э17), what the display shows now («упрощено, LOD»), the block of the last click («Выбранный блок»),
 * the grid with the turn and the sub-blocks, the checks of the import (duplicates, overlaps, empty and constant
 * fields, the rows of the examples; «Перепроверить» — `blocks.check`), the fields with their statistics (`GET /blocks/{id}`), the source and what the
 * import did not carry over.
 */
import { useEffect } from "react";
import { useApp } from "../../shell/AppContext";
import { Facts, useRun } from "../../shell/PropsParts";
import { useRegistryVersion, useStore } from "../../shell/useStore";
import css from "../../shell/PropsParts.module.css";
import { viewStore } from "../scene/bind";
import { blocksShowStore, pickedBlockStore, pickedFacts, showText, styleIn } from "./display";
import { checkRows, exampleText, fieldRow, gridFacts, MAX_EXAMPLES, sourceFacts, v2 } from "./props";
import { CheckControl } from "./checkButton";
import { blocksStore, loadBlockDetails } from "./state";

export function BlockProps({ id }: { id: number }) {
  const app = useApp();
  useRegistryVersion(app.registry);
  const brief = useStore(blocksStore, (s) => s.list.find((b) => b.id === id));
  const details = useStore(blocksStore, (s) => s.details[id]);
  const detailError = useStore(blocksStore, (s) => s.detailErrors[id]);
  const { run, error } = useRun();
  const shownNow = useStore(viewStore, (k) => styleIn(k, id).visible);
  const shownInfo = useStore(blocksShowStore, (s) => s.models[id]);
  const picked = useStore(pickedBlockStore, (s) => s.picked);
  const pickedHere = picked?.model === id ? picked : null;
  const version = brief?.version;
  const fresh = details !== undefined && details.version === version;
  useEffect(() => {
    if (version !== undefined && !fresh) void loadBlockDetails(id);
  }, [id, version, fresh]);
  if (!brief) return null;
  const b = fresh ? details : brief; // the list has the grid and the fields without stats: shown at once
  const show = app.registry.reason("blocks.show");
  const exportReason = app.registry.reason("omf.export");
  const fileReason = app.registry.reason("blocks.export");
  const checks = v2(b).checks;
  const rows = checkRows(checks);
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
        <button
          type="button"
          className={css.btn}
          disabled={show !== null}
          title={show ? `${shownNow ? "Скрыть в 3D" : "Показать в 3D"}\nНедоступно: ${show}` : shownNow ? "Убрать модель из 3D" : "Показать модель в 3D: оболочка, раскраска, фильтр"}
          onClick={() => void run("blocks.show", { model_id: id, visible: !shownNow })}
          data-testid="props-block-show"
          aria-pressed={shownNow}
        >
          {shownNow ? "Скрыть в 3D" : "Показать в 3D"}
        </button>
        <button type="button" className={css.btn} disabled={fileReason !== null} title={fileReason ? `Экспорт в файл\nНедоступно: ${fileReason}` : "Записать модель в DAT, CSV, Parquet или OMF"} data-testid="props-block-export" onClick={() => void run("blocks.export", { model_id: id })}>
          Экспорт…
        </button>
        <button type="button" className={css.btn} disabled={exportReason !== null} title={exportReason ? `Экспорт OMF\nНедоступно: ${exportReason}` : "Записать модель в файл OMF"} onClick={() => void run("omf.export", { dataset_ids: String(id) })}>
          Экспорт OMF…
        </button>
        <button type="button" className={css.btn} onClick={() => void run("object.delete", { kind: "blocks", id })}>
          Удалить…
        </button>
      </div>
      {show && <p className={css.note}>Показ в 3D недоступен: {show}.</p>}
      <fieldset className={css.group} data-testid="props-block-grid">
        <legend>Сетка</legend>
        <Facts facts={gridFacts(b)} />
      </fieldset>
      <fieldset className={css.group} data-testid="props-block-checks" data-checked={rows ? "yes" : "no"}>
        <legend>Проверки</legend>
        <CheckControl id={id} />
        {rows ? (
          <>
            <dl className={css.facts}>
              {rows.map((r) => (
                <div key={r.label} style={{ display: "contents" }}>
                  <dt>{r.label}</dt>
                  <dd className={r.found ? css.warn : undefined} data-found={r.found ? "yes" : "no"}>
                    {r.value}
                  </dd>
                </div>
              ))}
            </dl>
            {checks && checks.examples.length > 0 && (
              <ul className={css.list} data-testid="props-block-examples">
                {checks.examples.slice(0, MAX_EXAMPLES).map((e, i) => (
                  <li key={i}>{exampleText(e)}</li>
                ))}
                {checks.examples.length > MAX_EXAMPLES && <li className={css.note}>…и ещё примеров: {checks.examples.length - MAX_EXAMPLES}</li>}
              </ul>
            )}
          </>
        ) : fresh ? (
          <p className={css.note}>Проверки не выполнялись: модель создана до проверок при импорте. Нажмите «Перепроверить».</p>
        ) : (
          <p className={css.note}>Проверки читаются…</p>
        )}
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
      {shownInfo && (
        <fieldset className={css.group} data-testid="props-block-display" data-state={shownInfo.status}>
          <legend>Показ в 3D</legend>
          <p className={shownInfo.status === "error" ? css.error : css.note}>{showText(shownInfo, b.name)}</p>
          {shownInfo.warnings.map((w) => (
            <p key={w} className={css.warn}>
              {w}
            </p>
          ))}
        </fieldset>
      )}
      {pickedHere && (
        <fieldset className={css.group} data-testid="props-block-picked" data-state={pickedHere.status}>
          <legend>Выбранный блок</legend>
          <Facts facts={pickedFacts(pickedHere, b)} />
        </fieldset>
      )}
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
