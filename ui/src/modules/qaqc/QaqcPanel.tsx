/**
 * The bottom tab «QAQC» (ТЗ 8.4.3; ADR 0020 §11): on the left the data of control samples, the element and the
 * filters (with «записей N из M» and «Сбросить»), on the right the subtabs «Стандарты», «Бланки», «Дубликаты»,
 * «Внешний контроль», «Партии», «Отчёт». The choice of the data, of the element and of a subtab runs `qaqc.open`
 * (the command of the ribbon, Ctrl+K and the assistant). A subtab is a component of its own file in `SUBTABS`;
 * the interface Б adds its lines (duplicates, external) on acceptance.
 */
import { useEffect, useSyncExternalStore, type ComponentType } from "react";
import { useApp } from "../../shell/AppContext";
import { QAQC_SECTIONS, SECTION_TITLES, type QaqcSection } from "./commands";
import { FiltersPanel } from "./FiltersPanel";
import { activeData, activeElement, loadDatas, qaqcStore } from "./state";
import css from "./Qaqc.module.css";

/** The subtabs drawn by their own components; a section without one says that it is not ready. */
export const SUBTABS: Partial<Record<QaqcSection, ComponentType>> = {};

function Pending({ section }: { section: QaqcSection }) {
  return <p className={css.note}>Раздел «{SECTION_TITLES[section]}» появится в одной из следующих сборок этапа Э14.</p>;
}

export function QaqcPanel() {
  const app = useApp();
  const s = useSyncExternalStore(qaqcStore.subscribe, qaqcStore.get, qaqcStore.get);
  const projectOpen = () => app.store.get().projectName !== null;
  const hasProject = useSyncExternalStore(app.store.subscribe, projectOpen, projectOpen);
  useEffect(() => {
    if (hasProject && s.datas === null && !s.dataError) void loadDatas();
  }, [hasProject, s.datas, s.dataError]);
  const open = (args: Record<string, unknown>) => void app.run("qaqc.open", { section: s.section, ...args });
  if (!hasProject) return <div className={css.empty}>Откройте проект: контрольные пробы QAQC хранятся в проекте.</div>;
  if (s.datas === null)
    return (
      <div className={css.empty} data-testid="qaqc-tab">
        {s.dataError ? (
          <>
            <p className={css.error}>{s.dataError}</p>
            <button type="button" className={css.btn} onClick={() => void loadDatas()}>
              Повторить
            </button>
          </>
        ) : (
          "Чтение данных QAQC…"
        )}
      </div>
    );
  const data = activeData(s);
  if (!data)
    return (
      <div className={css.empty} data-testid="qaqc-tab">
        <p>В проекте нет данных контрольных проб. Загрузите файл или таблицу набора с колонками SAMPLE_ID, TYPE, CRM_ID, LAB, BATCH, DATE и элементами.</p>
        <button type="button" className={css.primary} onClick={() => void app.run("qaqc.setup", { tab: "data" })}>
          Настройка QAQC…
        </button>
      </div>
    );
  const element = activeElement(s);
  const Sub = SUBTABS[s.section];
  return (
    <div className={css.tab} data-testid="qaqc-tab">
      <aside className={css.side}>
        <label className={css.field}>
          <span>Данные QAQC</span>
          <select aria-label="Данные QAQC" value={data.id} onChange={(e) => open({ data_id: Number(e.target.value) })}>
            {s.datas.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name} ({d.rows.toLocaleString("ru-RU")})
              </option>
            ))}
          </select>
          {data.stale && <span className={css.stale}>Таблица набора изменилась после загрузки: обновите данные в «Настройка QAQC»</span>}
        </label>
        <label className={css.field}>
          <span>Элемент</span>
          <select aria-label="Элемент" value={element ?? ""} disabled={!data.elements.length} onChange={(e) => open({ element: e.target.value })}>
            {data.elements.map((el) => (
              <option key={el.name} value={el.name}>
                {el.name}
                {el.unit ? `, ${el.unit}` : ""}
              </option>
            ))}
          </select>
        </label>
        <FiltersPanel data={data} />
      </aside>
      <section className={css.main}>
        <div className={css.subtabs} role="tablist" aria-label="Разделы QAQC">
          {QAQC_SECTIONS.map((section) => (
            <button key={section} type="button" role="tab" aria-selected={section === s.section} className={section === s.section ? css.subtabOn : css.subtab} onClick={() => open({ section })}>
              {SECTION_TITLES[section]}
            </button>
          ))}
        </div>
        <div className={css.body} role="tabpanel" aria-label={SECTION_TITLES[s.section]}>
          {!element ? <p className={css.note}>В данных нет элементов: загрузите данные с колонками элементов.</p> : Sub ? <Sub /> : <Pending section={s.section} />}
        </div>
      </section>
    </div>
  );
}
