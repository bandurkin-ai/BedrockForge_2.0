/** The window of a display form (window kind "downhole"): the form, its draft in the view, the title, the close guard. */
import { useCallback, useEffect, useMemo, useRef } from "react";
import { FORM_TITLES, type DisplayForm, type StripLogSettings } from "../../charts/striplog/model";
import { askConfirm } from "../../shell/dialogs";
import { useStore } from "../../shell/useStore";
import { setCloseGuard, updateWindow, windowStore } from "../../shell/windows";
import { projectStore } from "../projects/state";
import { tablesStore } from "../tables/state";
import { DownholeForm } from "./DownholeForm";
import { draftStore, setDraft } from "./drafts";
import { libraryStore, type ChartRow } from "./library";
import { openDisplay } from "./open";
import "./downhole.css";

export function DownholeWindow({ winId, setId, form, chartId, table, field, focus }: {
  winId: string;
  setId: number;
  form: DisplayForm;
  chartId: number | null;
  table?: string | null;
  field?: string | null;
  /** a layer to select when the form opens */
  focus?: string | null;
}) {
  const charts = useStore(libraryStore, (s) => s.charts);
  const loaded = useStore(libraryStore, (s) => s.loaded);
  const allSets = useStore(tablesStore, (s) => s.sets);
  const refreshKey = useStore(projectStore, (s) => s.version);
  const sets = useMemo(() => allSets.map((s) => ({ id: s.id, name: s.name })), [allSets]);
  const chart = chartId !== null ? (charts.find((c) => c.id === chartId) ?? null) : null;
  const setName = allSets.find((s) => s.id === setId)?.name ?? `набор ${setId}`;
  const hint = useMemo(() => (field ? { table, field } : undefined), [table, field]);
  const saved = useRef<number | null>(chartId);

  useEffect(() => {
    updateWindow(winId, { title: `${chart?.name ?? `${FORM_TITLES[form]} (новое)`} · ${setName}` });
  }, [winId, chart?.name, form, setName]);

  // the draft leaves the view with the window
  useEffect(() => () => {
    setDraft(winId, null);
    setCloseGuard(winId, null);
  }, [winId]);

  const onPreview = useCallback((settings: StripLogSettings | null) => {
    setDraft(winId, settings ? { setId, chartId: saved.current, settings } : null);
  }, [winId, setId]);
  const onSaved = useCallback((c: ChartRow) => {
    saved.current = c.id;
    const d = draftStore.get().drafts[winId];
    if (d) setDraft(winId, { ...d, chartId: c.id }); // drawn instead of the saved one, not beside it
    // the window now edits the saved display: a double click in the tree finds it
    const w = windowStore.get().windows.find((x) => x.id === winId);
    updateWindow(winId, { props: { ...(w?.props ?? {}), chartId: c.id, table: null, field: null } });
  }, [winId]);
  const onDirty = useCallback((dirty: boolean) => {
    setCloseGuard(winId, dirty ? () => askConfirm({ title: "Изменения не сохранены", message: "Отображение изменено, но не сохранено. Закрыть форму без сохранения?", okLabel: "Закрыть без сохранения", danger: true }) : null);
  }, [winId]);

  if (chartId !== null && !chart) {
    return <div className="dh-root"><p className="muted small form-note">{loaded ? "Отображение удалено или недоступно." : "Загрузка отображения…"}</p></div>;
  }
  return (
    <DownholeForm
      setId={setId} form={form} chart={chart} hint={hint} focus={focus} refreshKey={refreshKey} sets={sets}
      onPreview={onPreview} onSaved={onSaved} onOpenDisplay={(id) => void openDisplay(id)} onDirty={onDirty}
    />
  );
}
