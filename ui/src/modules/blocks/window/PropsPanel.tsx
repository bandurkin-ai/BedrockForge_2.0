/**
 * The tab «Свойства» of the window «Блочная модель · …» (ТЗ 7.31, 8.7.6; ADR 0023 §2, §5): the grid (cells, step, corner,
 * extent), the turn about +Z, the sub-blocks (the division of a parent, the levels), the rows and the parents, the fields
 * with their roles and statistics (a missing value is «—», never 0), the findings of the checks of the import, a model that
 * is out of date, the source, what the import did not carry over, the block of the last click («Выбранный блок») and the
 * way to the window «Свойства полей» (`blocks.field.props`, when the command exists). Read from `GET /blocks/{id}`.
 */
import { useApp } from "../../../shell/AppContext";
import { Facts } from "../../../shell/PropsParts";
import css from "../../../shell/PropsParts.module.css";
import { pickedFacts } from "../display";
import { fieldRow, sourceFacts } from "../props";
import mod from "./Window.module.css";
import { checkLines, modelFacts, readDetails, roleText } from "./details";
import { useModel, usePicked, useRegistryVersion } from "./hooks";

/** The command of the window «Свойства полей» (17.36); until it is registered the button says why it cannot be pressed. */
export const FIELD_PROPS_COMMAND = "blocks.field.props";

export function PropsPanel({ id }: { id: number }) {
  const app = useApp();
  useRegistryVersion(app.registry);
  const { model, details, fresh, error } = useModel(id);
  const picked = usePicked(id);
  if (!model) return <p className={css.note}>Блочная модель не найдена: обновите дерево проекта.</p>;
  const d = readDetails(details);
  const checks = checkLines(d.checks);
  const meta = new Map(d.fields.map((f) => [f.name, f]));
  const known = app.registry.get(FIELD_PROPS_COMMAND) !== undefined;
  const reason = known ? app.registry.reason(FIELD_PROPS_COMMAND) : "Окно «Свойства полей» ещё не подключено.";
  return (
    <div className={mod.panel} data-testid="props-panel" data-state={fresh ? "ready" : error ? "failed" : "loading"}>
      {d.stale && (
        <p className={css.warn} role="alert" data-testid="props-stale">
          Модель устарела{d.staleReason ? `: ${d.staleReason}` : ""}. Пересчитайте её или исходные поля.
        </p>
      )}
      <fieldset className={css.group} data-testid="props-grid">
        <legend>Сетка</legend>
        <Facts facts={modelFacts(model, d)} />
        {!fresh && !error && <p className={css.note}>Свойства читаются…</p>}
        {error && (
          <p className={css.error} role="alert">
            {error}
          </p>
        )}
      </fieldset>

      <fieldset className={css.group} data-testid="props-fields" data-fields={model.fields.length}>
        <legend>Поля</legend>
        {model.fields.length ? (
          <div className={css.tableWrap}>
            <table className={css.table}>
              <thead>
                <tr>
                  <th>Поле</th>
                  <th>Тип</th>
                  <th>Ед.</th>
                  <th title="Содержание, плотность, доля блока, код, класс">Роль</th>
                  <th title="Занятые ячейки со значением">Значений</th>
                  <th title="Занятые ячейки без значения">Пусто</th>
                  <th>Мин.</th>
                  <th>Макс.</th>
                  <th>Среднее</th>
                </tr>
              </thead>
              <tbody>
                {model.fields.map(fieldRow).map((f) => {
                  const m = meta.get(f.name);
                  return (
                    <tr key={f.name} title={m?.description ?? (f.labels || undefined)}>
                      <td>{f.name}</td>
                      <td>{f.kind}</td>
                      <td>{f.unit}</td>
                      <td data-testid={`props-role-${f.name}`}>{roleText(m?.role ?? null)}</td>
                      <td>{f.count}</td>
                      <td>{f.missing}</td>
                      {f.labels ? (
                        <td colSpan={3}>{f.labels}</td>
                      ) : (
                        <>
                          <td>{f.min}</td>
                          <td>{f.max}</td>
                          <td>{f.mean}</td>
                        </>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p className={css.note}>У модели нет полей: только занятые ячейки.</p>
        )}
        <div className={css.row}>
          <button type="button" className={css.btn} disabled={reason !== null} title={reason ?? "Единицы, описание и роль полей; пересчёт и удаление"} data-testid="props-field-props" onClick={() => void app.run(FIELD_PROPS_COMMAND, { model_id: id })}>
            Свойства полей…
          </button>
        </div>
        {reason && (
          <p className={css.note} data-testid="props-field-props-reason">
            Недоступно: {reason}
          </p>
        )}
      </fieldset>

      <fieldset className={css.group} data-testid="props-checks">
        <legend>Проверки</legend>
        {!checks && <p className={css.note}>Проверки ещё не выполнялись.</p>}
        {checks?.clean && <p className={css.note}>Замечаний нет: повторов, перекрытий, пустых и постоянных полей не найдено.</p>}
        {checks && !checks.clean && (
          <ul className={css.list}>
            {checks.lines.map((l) => (
              <li key={l} className={css.warn}>
                {l}
              </li>
            ))}
          </ul>
        )}
      </fieldset>

      {picked && (
        <fieldset className={css.group} data-testid="props-picked" data-state={picked.status}>
          <legend>Выбранный блок</legend>
          <Facts facts={pickedFacts(picked, model)} />
        </fieldset>
      )}

      {sourceFacts(model.source).length > 0 && (
        <fieldset className={css.group} data-testid="props-source">
          <legend>Источник</legend>
          <Facts facts={sourceFacts(model.source)} />
        </fieldset>
      )}
      {model.warnings.length > 0 && (
        <fieldset className={css.group} data-testid="props-warnings">
          <legend>Не перенесено при импорте</legend>
          <ul className={css.list}>
            {model.warnings.map((w) => (
              <li key={w} className={css.warn}>
                {w}
              </li>
            ))}
          </ul>
        </fieldset>
      )}
    </div>
  );
}
