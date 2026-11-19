/**
 * The panel «Править треугольники» over the 3D window (ТЗ 7.23; ADR 0015 §6): the wireframe, the mode —
 * «Отметить к удалению» or «Добавить по трём вершинам», how many triangles are marked and added (and the vertices
 * of the next one), «Убрать последнее» (also a right click), «Применить» — the operation `edit_triangles`, a new
 * wireframe — and «Отмена». The clicks are taken in the 3D window (`triangles.ts`).
 */
import { useState } from "react";
import { useApp } from "../../shell/AppContext";
import type { App } from "../../shell/app";
import { Icon } from "../../shell/Icon";
import { useStore } from "../../shell/useStore";
import { applyTriangles, cancelTriangles, isEmpty, trianglesController, trianglesStore, type TriEdit } from "./triangles";
import css from "./ModelBars.module.css";

const INT = new Intl.NumberFormat("ru-RU");

/** «к удалению 3 · новых 1 · вершин 2 из 3» */
export function countText(t: TriEdit): string {
  const parts = [`к удалению ${INT.format(t.remove.size)}`, `новых ${INT.format(t.add.length)}`];
  if (t.pending.length) parts.push(`вершин ${t.pending.length} из 3`);
  return parts.join(" · ");
}

/** Why «Применить» is not available now; null — it is. */
export function applyReason(t: TriEdit, creating: boolean): string | null {
  if (creating) return "Правка уже применяется";
  if (isEmpty(t)) return "Отметьте треугольники к удалению или добавьте новые";
  return null;
}

/** The panel while a fix is under way; nothing otherwise. */
export function TrianglesHud() {
  const app = useApp();
  const edit = useStore(trianglesStore, (s) => s.edit);
  if (!edit) return null;
  return <TrianglesBar app={app} edit={edit} />;
}

function TrianglesBar({ app, edit }: { app: App; edit: TriEdit }) {
  const st = useStore(trianglesStore, (s) => s);
  const [error, setError] = useState<string | null>(null);
  const c = trianglesController();
  const reason = applyReason(edit, st.creating);
  const canDrop = edit.pending.length > 0 || !isEmpty(edit);
  const apply = () => {
    if (reason) return;
    setError(null);
    applyTriangles(app).then(
      (text) => app.store.set({ statusNote: text }),
      (e: unknown) => {
        const text = e instanceof Error ? e.message : String(e);
        setError(text);
        app.store.set({ statusNote: text });
      },
    );
  };
  const mode = (m: TriEdit["mode"], label: string, testid: string, hint: string) => (
    <button type="button" className={css.mode} data-testid={testid} aria-pressed={edit.mode === m} title={hint} onClick={() => c?.setMode(m)}>
      {label}
    </button>
  );
  return (
    <div className={css.bar} data-testid="triangles-panel" role="toolbar" aria-label="Править треугольники">
      <div className={css.row}>
        <span className={css.title} title={`Каркас «${edit.name}»: результат правки — новый каркас, этот не меняется`}>
          Треугольники: {edit.name}
        </span>
        <span className={css.group} role="group" aria-label="Режим">
          {mode("remove", "Отметить к удалению", "triangles-mode-remove", "Щелчок по треугольнику (точный выбор) отмечает его к удалению; повторный щелчок снимает отметку")}
          {mode("add", "Добавить по трём вершинам", "triangles-mode-add", "Три щелчка — три вершины нового треугольника: щелчок берёт ближайшую вершину треугольника под курсором")}
        </span>
        <span className={css.group} role="group" aria-label="Шаг">
          <button
            type="button"
            className={css.tool}
            data-testid="triangles-drop"
            aria-label="Убрать последнее"
            aria-disabled={!canDrop}
            title={canDrop ? "Убрать последнее: выбранную вершину, новый треугольник или отметку (правый щелчок — то же)" : "Убрать последнее — недоступно: ничего не выбрано"}
            onClick={() => canDrop && c?.dropLast()}
          >
            <Icon name="edUndo" />
          </button>
        </span>
        <span className={css.info} data-testid="triangles-count">
          {countText(edit)}
        </span>
        <span className={css.grow} />
        <button type="button" className={css.primary} data-testid="triangles-apply" aria-disabled={!!reason} title={reason ? `Применить — недоступно: ${reason}` : "Применить: операция «Править треугольники» строит новый каркас в фоне"} onClick={apply}>
          Применить
        </button>
        <button type="button" className={css.btn} data-testid="triangles-cancel" title="Выйти из правки треугольников: каркас не меняется" onClick={() => cancelTriangles()}>
          Отмена
        </button>
      </div>
      <div className={css.row}>
        <span className={error ? css.error : css.hint} data-testid="triangles-status">
          {error ?? (st.busy ? "Точный выбор…" : st.message ?? "")}
        </span>
      </div>
    </div>
  );
}
