/**
 * The panel «Собрать из контуров» over the 3D window (ТЗ 7.23; ADR 0015 §6; BF `viz/AssembleBar.tsx`): how many
 * contours and tie lines are picked, «Убрать последний контур», «Замкнуть торцы» (flat caps — a closed body), the
 * triangles and the volume of the preview (or why there is none), «Создать каркас» and «Отмена» (Esc). The
 * contours are clicked in the 3D window (`EditSession.assemble`); the preview is `assemble.ts`.
 */
import { useState } from "react";
import type { EditSession } from "../../edit/session";
import { useApp } from "../../shell/AppContext";
import type { App } from "../../shell/app";
import { Icon } from "../../shell/Icon";
import { useStore } from "../../shell/useStore";
import { useSession } from "../lines/EditToolbar";
import { editStore } from "../lines/editState";
import { assembleStore, cancelAssembly, createAssembled, type AssembleState } from "./assemble";
import css from "./ModelBars.module.css";

const INT = new Intl.NumberFormat("ru-RU");
const VOL = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });

/** The line of the panel about the preview. */
export function previewText(st: AssembleState): string {
  if (st.contours < 2) return st.contours ? "Щёлкните следующий контур" : "Щёлкните первый контур";
  if (st.busy) return "Каркас строится…";
  if (st.problem) return st.problem;
  const p = st.preview;
  if (!p) return "";
  const volume = p.volume !== null ? `объём ${VOL.format(p.volume)} м³` : p.closed ? "объём не определён" : "торцы открыты";
  return `треугольников ${INT.format(p.triangles)} · ${volume}`;
}

/** Why «Создать каркас» is not available now; null — it is. */
export function createReason(st: AssembleState): string | null {
  if (st.creating) return "Каркас уже создаётся";
  if (st.contours < 2) return "Выберите хотя бы два замкнутых контура";
  if (st.problem) return "Контуры не сшиваются: см. причину на панели";
  return null;
}

/** The panel while an assembly is under way; nothing otherwise. */
export function AssembleHud() {
  const app = useApp();
  const session = useStore(editStore, (s) => s.session);
  const active = useStore(assembleStore, (s) => s.active);
  if (!session || !active) return null;
  return <AssembleBar app={app} session={session} />;
}

function AssembleBar({ app, session }: { app: App; session: EditSession }) {
  useSession(session);
  const st = useStore(assembleStore, (s) => s);
  const [error, setError] = useState<string | null>(null);
  const a = session.assemble;
  if (!a) return null;
  const order = session.assembled();
  const reason = createReason(st);
  const create = () => {
    if (reason) return;
    setError(null);
    createAssembled(app).then(
      (text) => app.store.set({ statusNote: text }),
      (e: unknown) => {
        const text = e instanceof Error ? e.message : String(e);
        setError(text);
        app.store.set({ statusNote: text });
      },
    );
  };
  const info = previewText(st);
  const warnings = st.preview?.warnings ?? [];
  return (
    <div className={css.bar} data-edit-panel data-testid="assemble-panel" role="toolbar" aria-label="Собрать из контуров">
      <div className={css.row}>
        <span className={css.title} title={`Контуры слоя «${session.layerName}»: щёлкайте замкнутые контуры по порядку — каркас растёт от контура к контуру`}>
          Сборка каркаса
        </span>
        <span className={css.info} data-testid="assemble-count">
          контуров <b>{order.length}</b>
          {st.ties ? (
            <>
              {" "}
              · тай-линий <b>{st.ties}</b>
            </>
          ) : null}
        </span>
        <span className={css.group} role="group" aria-label="Сборка">
          <button
            type="button"
            className={css.tool}
            data-testid="assemble-drop"
            aria-label="Убрать последний контур"
            aria-disabled={!order.length}
            title={order.length ? "Убрать последний контур: каркас становится на контур короче (повторный щелчок по контуру тоже убирает его)" : "Убрать последний контур — недоступно: контуры ещё не выбраны"}
            onClick={() => order.length && session.dropLastContour()}
          >
            <Icon name="edUndo" />
          </button>
          <button
            type="button"
            className={css.tool}
            data-testid="assemble-close"
            aria-label="Замкнуть торцы"
            aria-pressed={a.closed}
            title={a.closed ? "Торцы замкнуты плоскими крышками по первому и последнему контуру; щелчок — открыть торцы" : "Замкнуть торцы: плоские крышки по первому и последнему контуру — получится замкнутое тело с объёмом"}
            onClick={() => session.setAssembleClosed(!a.closed)}
          >
            <Icon name="edClose" />
          </button>
        </span>
        <span className={`${css.info} ${st.problem && !st.busy ? css.error : ""}`} data-testid="assemble-info" title={warnings.join("; ") || info}>
          {info}
        </span>
        <span className={css.grow} />
        <button type="button" className={css.primary} data-testid="assemble-create" aria-disabled={!!reason} title={reason ? `Создать каркас — недоступно: ${reason}` : "Создать каркас: слой сохраняется, каркас строится операцией «Сшить контуры» в фоне"} onClick={create}>
          Создать каркас
        </button>
        <button type="button" className={css.btn} data-testid="assemble-cancel" title="Выйти из сборки (Esc); контуры не меняются" onClick={() => cancelAssembly()}>
          Отмена
        </button>
      </div>
      {(warnings.length > 0 || error) && (
        <div className={css.row}>
          {error ? (
            <span className={css.error} data-testid="assemble-error">
              {error}
            </span>
          ) : (
            <span className={css.warn}>{warnings.join("; ")}</span>
          )}
        </div>
      )}
      <div className={css.hint}>Тай-линии (инструмент «Тай-линия», Y) между вершинами соседних контуров направляют сшивку; затем вернитесь к «Выбору» (V) и щёлкайте контуры.</div>
    </div>
  );
}
