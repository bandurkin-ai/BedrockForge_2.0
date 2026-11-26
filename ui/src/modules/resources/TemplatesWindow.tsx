/**
 * The window «Шаблоны форм отчёта» (ТЗ 8.10 «Формы»; ADR 0027 решения 16–19, 29; вопросы № 105, 106): on the left the list (JORC and ГКЗ of the box — to
 * view and to copy; the user's own — to change and delete), in the middle the editor (the order of the rows, the subtotals, the total, the keys,
 * the columns, the units, the decimals), on the right the sample of the form. «Сохранить» writes the template (`PUT` with the version read; a
 * template changed in another window is asked about), «Удалить» asks in our own window. A template is the show of a report: saving it does not
 * recalculate any report. Floating, not modal.
 */
import { useEffect } from "react";
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { closeWindow } from "../../shell/windows";
import { useSel } from "./ReportParts";
import { resourcesStore } from "./state";
import { ColumnsPart, DecimalsPart, Issue, KeysPart, LabelsPart, NamePart, PreviewPart, SubtotalsPart, TotalPart, UnitsPart } from "./TemplateParts";
import { chooseTemplate, copyTemplate, newTemplate, removeTemplate, saveTemplate, startEditor } from "./templatesActions";
import { templatesStore } from "./templatesState";
import mod from "./Templates.module.css";

export function TemplatesWindow({ winId }: { winId: string }) {
  const app = useApp();
  const list = useSel(resourcesStore, (s) => s.templates);
  const loaded = useSel(resourcesStore, (s) => s.templatesLoaded);
  const st = useSel(templatesStore, (s) => s);
  const readOnly = useSel(app.store, (s) => s.projectReadOnly);
  useEffect(() => {
    void startEditor();
  }, []);

  const d = st.draft;
  const off = !d || d.builtin || st.busy;
  const own = !!st.base && !st.base.builtin;
  return (
    <div className={mod.window} data-testid="tpl-window">
      <div className={mod.layout}>
        <div className={mod.side}>
          <h4 className={mod.head}>Шаблоны</h4>
          <div className={mod.list} role="listbox" aria-label="Шаблоны форм" data-testid="tpl-list">
            {list.map((t) => (
              <button key={t.id} type="button" role="option" aria-selected={st.selected === t.id} aria-current={st.selected === t.id} className={mod.item} data-testid={`tpl-item-${t.id}`} onClick={() => void chooseTemplate(t.id)}>
                <span>{t.name}</span>
                <span className={mod.badge}>{t.builtin ? "встроенный" : ""}</span>
              </button>
            ))}
            {d && d.id === null && (
              <div className={mod.item} aria-current="true" data-testid="tpl-item-new">
                <span>{d.name.trim() || "Новый шаблон"}</span>
                <span className={mod.badge}>не сохранён</span>
              </div>
            )}
            {loaded && !list.length && <p className={css.note}>Шаблонов нет.</p>}
          </div>
          <div className={mod.sideButtons}>
            <button type="button" className={css.btn} disabled={st.busy || readOnly} title={readOnly ? "Проект открыт только для чтения" : "Новый шаблон с пустым списком строк"} data-testid="tpl-new" onClick={() => void newTemplate()}>
              Создать
            </button>
            <button type="button" className={css.btn} disabled={st.busy || !st.base || readOnly} title={readOnly ? "Проект открыт только для чтения" : "Новый шаблон с теми же строками, единицами и знаками"} data-testid="tpl-copy" onClick={() => void copyTemplate()}>
              Создать копию
            </button>
            <button type="button" className={css.btn} disabled={st.busy || !own || readOnly} title={st.base?.builtin ? "Встроенные шаблоны не удаляются: создайте копию" : "Удалить шаблон"} data-testid="tpl-delete" onClick={() => void removeTemplate()}>
              Удалить
            </button>
          </div>
          <p className={css.note}>JORC и ГКЗ — встроенные: их можно смотреть и копировать. Форма — только показ: отчёты не пересчитываются.</p>
        </div>

        <div className={mod.editor}>
          {!d && (
            <p className={mod.empty} data-testid="tpl-empty">
              {st.loading ? "Чтение шаблона…" : "Выберите шаблон в списке или создайте свой."}
            </p>
          )}
          {d && (
            <>
              {d.builtin && (
                <p className={css.note} data-testid="tpl-builtin">
                  Встроенный шаблон «{d.name}» открыт только для просмотра. Нажмите «Создать копию», чтобы изменить порядок, подытоги, единицы и знаки в своём шаблоне.
                </p>
              )}
              <NamePart d={d} off={off} />
              <LabelsPart d={d} off={off} />
              <SubtotalsPart d={d} off={off} />
              <TotalPart d={d} off={off} />
              <KeysPart d={d} off={off} />
              <ColumnsPart d={d} off={off} />
              <UnitsPart d={d} off={off} />
              <DecimalsPart d={d} off={off} />
            </>
          )}
        </div>

        <div className={mod.sample}>{d ? <PreviewPart d={d} /> : <p className={mod.empty}>Пример формы появится здесь.</p>}</div>
      </div>
      <Issue place="general" />
      {st.note && (
        <p className={mod.ok} style={{ margin: "0 12px" }} role="status" data-testid="tpl-note">
          {st.note}
        </p>
      )}
      <div className={mod.footer}>
        <button type="button" className={css.primary} disabled={off || readOnly} title={readOnly ? "Проект открыт только для чтения: шаблон не записать" : d?.builtin ? "Встроенный шаблон не меняется: создайте копию" : "Записать шаблон в проект"} data-testid="tpl-save" onClick={() => void saveTemplate()}>
          Сохранить
        </button>
        <span className={mod.grow} />
        <button type="button" className={css.btn} data-testid="tpl-close" onClick={() => closeWindow(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
