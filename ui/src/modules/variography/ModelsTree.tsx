/**
 * The models of variograms in the data tree (ТЗ 8.6.4; ADR 0022 §15 «Дерево»: the section «Геостатистика», the
 * subsection «Модели вариограмм»): the stored models by name, each with its colour, the number of structures and the
 * sill; a stale one is marked «устарел» with its reason (a changed table, changed trajectories, «источник удалён»).
 * A double click or Enter opens its window (`variography.open`); the menu: «Открыть», «Показать эллипсоид»,
 * «Перестроить» (only a stale model whose source is there), «Экспорт JSON…», «Экспорт GSLIB…» (the file chosen in a
 * dialog — `exchange.ts`), «Свойства», «Удалить…» — the commands of the registry. The list is read when a project
 * opens and after every `geostat.changed {kind: "variogram_model"}` (`bind.ts`). The section itself, its title and
 * «+» (import) — by the orchestrator (shell/treeSections.ts, shell/DataTree.tsx).
 */
import { useMemo, useState, useSyncExternalStore, type MouseEvent } from "react";
import { useApp } from "../../shell/AppContext";
import { openMenu, type MenuItem } from "../../shell/ContextMenu";
import { showMessage } from "../../shell/dialogs";
import type { IconName } from "../../shell/Icon";
import { Glyph } from "../../shell/ObjectRows";
import { TreeRow } from "../../shell/TreeRow";
import tree from "../../shell/DataTree.module.css";
import { INDICATOR, isIndicator, type ExchangeFormat, type ModelRow } from "./api";
import { exportFlow } from "./exchange";
import { MODEL_TITLES, type ModelType } from "./model";
import { variographyStore } from "./state";

const n = (v: number | null) => (v === null ? "—" : v.toLocaleString("ru-RU", { maximumSignificantDigits: 4 }));

/** The models by name (as the core lists them; the same order whatever the store got). */
export const modelsByName = (list: readonly ModelRow[]): ModelRow[] => [...list].sort((a, b) => a.name.localeCompare(b.name, "ru"));

/** «устарел: изменена таблица «Пробы»»; null — up to date. */
export const staleText = (m: Pick<ModelRow, "stale" | "stale_reason">): string | null => (m.stale ? `устарел${m.stale_reason ? `: ${m.stale_reason}` : ""}` : null);

/** Why «Перестроить» is not available; null — it is. */
export function rebuildReason(m: Pick<ModelRow, "stale" | "source_lost">): string | null {
  if (m.source_lost) return "Источник удалён: набор или таблица модели удалены";
  if (!m.stale) return "Модель не устарела";
  return null;
}

/** «нормальные оценки» / «индикатор, порог 0,5» with the brackets given; "" — no transform. */
function transformNote(t: string, open: string, close: string): string {
  if (t === "nscore") return `${open}нормальные оценки${close}`;
  return isIndicator(t) ? `${open}индикатор, порог ${n(Number(t.slice(INDICATOR.length)))}${close}` : "";
}

const typeTitle = (t: string) => MODEL_TITLES[t as ModelType] ?? t;

/** The tooltip of a row. */
export function modelTitleText(m: ModelRow): string {
  const parts = [`Модель «${m.name}»: ${typeTitle(m.type)}${transformNote(m.transform, " (", ")")}, ${m.field ?? "—"}`, `структур ${m.structures}, порог ${n(m.sill)}`];
  const st = staleText(m);
  if (st) parts.push(st[0].toUpperCase() + st.slice(1));
  parts.push("Двойной щелчок — открыть в окне «Вариография»");
  return parts.join("\n");
}

/** The text of «Свойства». */
export function propertiesText(m: ModelRow): string {
  const at = (s: string | null) => (s ? new Date(s).toLocaleString("ru-RU") : "—");
  return [
    `№${m.id} «${m.name}»`,
    `Тип: ${typeTitle(m.type)}${transformNote(m.transform, ", ", "")}`,
    `Данные: набор №${m.set_id ?? "—"}, таблица ${m.table ?? "—"}, поле ${m.field ?? "—"}`,
    `Структур: ${m.structures}; порог C0 + Σc = ${n(m.sill)}`,
    `Состояние: ${staleText(m) ?? "актуальна"}`,
    `Создана: ${at(m.created_at)}; изменена: ${at(m.updated_at)}`,
  ].join("\n");
}

type Run = (id: string, args: Record<string, unknown>) => void | Promise<unknown>;

/** The items of the menu of a model: each runs its command (unavailable — with the reason). */
export function modelMenu(m: ModelRow, reason: (id: string) => string | null, run: Run, exportAs: (format: ExchangeFormat) => void): MenuItem[] {
  return [
    { label: "Открыть", reason: reason("variography.open"), run: () => void run("variography.open", { model_id: m.id }) },
    { label: "Показать эллипсоид", reason: reason("variography.show") ?? (m.structures ? null : "В модели нет структур"), run: () => void run("variography.show", { model_id: m.id, visible: true }) },
    { label: "Перестроить", reason: reason("variography.rebuild") ?? rebuildReason(m), run: () => void run("variography.rebuild", { model_id: m.id }) },
    { label: "Экспорт JSON…", reason: reason("variography.export"), run: () => exportAs("json") },
    { label: "Экспорт GSLIB…", reason: reason("variography.export"), run: () => exportAs("gslib") },
    { label: "Свойства", run: () => void showMessage({ title: "Модель вариограммы", message: propertiesText(m) }) },
    { label: "Удалить…", reason: reason("variography.delete"), run: () => void run("variography.delete", { model_id: m.id }) },
  ];
}

export function ModelsTree() {
  const app = useApp();
  const getState = () => variographyStore.get();
  const { models, modelsLoaded } = useSyncExternalStore(variographyStore.subscribe, getState, getState);
  const [selected, setSelected] = useState<number | null>(null);
  const list = useMemo(() => modelsByName(models), [models]);
  const run: Run = (id, args) => app.run(id, args);
  if (!list.length) return <div className={tree.none}>{modelsLoaded ? "Нет моделей — «Сохранить» в окне «Вариография»" : "Нет объектов"}</div>;
  const menu = (m: ModelRow, e: MouseEvent) => {
    e.preventDefault();
    setSelected(m.id);
    openMenu(e.clientX, e.clientY, modelMenu(m, (id) => app.registry.reason(id), run, (format) => void exportFlow(run, m.id, m.name, format).catch(() => undefined)));
  };
  return (
    <div role="group" aria-label="Модели вариограмм">
      {list.map((m) => {
        const st = staleText(m);
        return (
          <TreeRow
            key={m.id}
            label={m.name}
            level={2}
            selected={selected === m.id}
            onSelect={() => setSelected(m.id)}
            onActivate={() => void run("variography.open", { model_id: m.id })}
            title={modelTitleText(m)}
            testId={`tree-vario-model-${m.id}`}
            glyph={
              <>
                {m.color && <span className={tree.swatch} style={{ background: m.color }} />}
                <Glyph icon={"variogram" as IconName} />
              </>
            }
            after={st ? <span className={`${tree.count} ${tree.error}`} data-stale title={st}>устарел</span> : <span className={tree.count}>{m.structures}</span>}
            onContextMenu={(e) => menu(m, e)}
          />
        );
      })}
    </div>
  );
}
