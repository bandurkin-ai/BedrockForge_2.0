/**
 * The tab «Фильтр» of the window «Блочная модель · …» (ТЗ 7.35 Д-02, 8.7.6; ADR 0023 §8, §11): the filter of the library of
 * the project for the model (the blocks it passes are shown — together with the quick restrictor, both at once), «Фильтры…»
 * (the window of the library, with the chosen filter on edit) and «Сохранить быстрый ограничитель как фильтр» (`filter.save
 * {name, rules}`: the restrictor goes into the library under a name). The choice is the style of the model in the view of
 * the project; a filter that was deleted since is told, not hidden.
 */
import { useState } from "react";
import { useApp } from "../../../shell/AppContext";
import css from "../../../shell/PropsParts.module.css";
import { quickIssue, restrictOf } from "../display";
import { filtersByName, filtersStore } from "../filters/state";
import mod from "./Window.module.css";
import { saveQuickAsFilter } from "./actions";
import { setFilter } from "./edits";
import { useInfo, useModel, useRegistryVersion, useStore, useStyle } from "./hooks";
import { Issue } from "./parts";

export function FilterPanel({ id }: { id: number }) {
  const app = useApp();
  useRegistryVersion(app.registry);
  const style = useStyle(id);
  const { model } = useModel(id);
  const info = useInfo(id);
  const rows = useStore(filtersStore, (s) => s.rows);
  const loaded = useStore(filtersStore, (s) => s.loaded);
  const sorted = filtersByName(rows);
  const chosen = style.filter_id === null ? undefined : rows.find((f) => f.id === style.filter_id);
  const lost = style.filter_id !== null && loaded && !chosen;
  const [name, setName] = useState<string | null>(null);
  const [note, setNote] = useState<{ text: string; error: boolean } | null>(null);
  const [issue, setIssue] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const proposed = name ?? `Ограничитель · ${model?.name ?? `№${id}`}`;
  const saveReason = app.registry.reason("filter.save");
  const manageReason = app.registry.reason("filters.manage");
  const rule = restrictOf(style.quick);
  const quickBad = quickIssue(style.quick);
  const cannotSave = saveReason ?? (!rule ? "Быстрый ограничитель пуст: добавьте условие на вкладке «Быстрый ограничитель»." : quickBad);

  const choose = (value: string) => {
    const r = setFilter(id, value ? Number(value) : null);
    setIssue(r ? r.text : null);
  };
  const open = () => void app.run("filters.manage", chosen ? { filter_id: chosen.id } : {});
  const save = async () => {
    setBusy(true);
    setNote(null);
    try {
      const r = await saveQuickAsFilter(app, id, proposed);
      setNote({ text: r.text, error: !r.ok });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={mod.panel} data-testid="filter-panel">
      <fieldset className={css.group}>
        <legend>Фильтр из библиотеки</legend>
        <label className={css.field}>
          <span>Показывать блоки, которые проходят фильтр</span>
          <select className={css.input} value={style.filter_id ?? ""} aria-label="Фильтр модели" data-testid="filter-select" onChange={(e) => choose(e.target.value)}>
            <option value="">— без фильтра —</option>
            {lost && style.filter_id !== null && <option value={style.filter_id}>№{style.filter_id} (нет в проекте)</option>}
            {sorted.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>
        </label>
        {chosen && (
          <p className={css.note} data-testid="filter-title" title={chosen.note ?? undefined}>
            {chosen.title}
            {chosen.fields.length ? ` — поля: ${chosen.fields.join(", ")}` : ""}
          </p>
        )}
        {!chosen && !lost && <p className={css.note}>{loaded && !sorted.length ? "В библиотеке проекта пока нет фильтров: «Фильтры…» создаёт первый." : "Фильтр не выбран: показаны все блоки (кроме отсечённых быстрым ограничителем)."}</p>}
        {lost && (
          <p className={css.warn} role="alert" data-testid="filter-lost">
            Фильтр №{style.filter_id} удалён из проекта. Выберите другой фильтр или «— без фильтра —».
          </p>
        )}
        <Issue text={issue} testId="filter-issue" />
        {info?.header && (
          <p className={css.note} data-testid="filter-passed">
            Сейчас прошло {info.header.passed.toLocaleString("ru-RU")} из {info.header.rows.toLocaleString("ru-RU")} блоков.
          </p>
        )}
        <div className={css.row}>
          <button type="button" className={css.btn} disabled={manageReason !== null} title={manageReason ?? "Окно библиотеки фильтров: создать, изменить, проверить на модели"} data-testid="filter-manage" onClick={open}>
            Фильтры…
          </button>
        </div>
      </fieldset>

      <fieldset className={css.group}>
        <legend>Быстрый ограничитель → фильтр</legend>
        <p className={css.note}>Условия вкладки «Быстрый ограничитель» можно сохранить в библиотеку под именем и использовать в других окнах и отчётах.</p>
        <label className={css.field}>
          <span>Имя фильтра</span>
          <input className={css.input} value={proposed} aria-label="Имя нового фильтра" data-testid="filter-name" onChange={(e) => setName(e.target.value)} />
        </label>
        <div className={css.row}>
          <button type="button" className={css.btn} disabled={busy || cannotSave !== null} title={cannotSave ?? "Сохранить быстрый ограничитель как фильтр библиотеки"} data-testid="filter-save-quick" onClick={() => void save()}>
            Сохранить быстрый ограничитель как фильтр
          </button>
        </div>
        {cannotSave && <p className={css.note}>{cannotSave}</p>}
        {note && (
          <p className={note.error ? css.error : css.note} role={note.error ? "alert" : "status"} data-testid="filter-note">
            {note.text}
          </p>
        )}
      </fieldset>
    </div>
  );
}
