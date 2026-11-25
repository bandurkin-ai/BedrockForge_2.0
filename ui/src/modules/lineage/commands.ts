/**
 * The commands of the graph of dependencies (Д-35; ТЗ 7.35.4; ADR 0025 §7–10): «Настройки проекта › Данные › Зависимости» and
 * «Свойства» of an object. The ribbon, Ctrl+K, the keys of the user and the assistant run the same commands. The object is
 * named by `kind`, `id` (and `sub` — the field of a block model); without them — the object chosen in the tree (a wireframe, a
 * block model). The handlers are `bind.ts`.
 *
 * `kind`: `dh_set` (набор скважин), `dh_table` (таблица), `dh_result` (результат обработки проб), `wireframe`, `bm_field`
 * (поле блочной модели: `id` — модель, `sub` — имя поля), `object:variogram_model`, `object:block_model`,
 * `object:qaqc_data`, `object:qaqc_report`, `object:trend`, `object:estimation`.
 */
import type { Command, ParamSchema } from "../../shell/commands";
import type { IconName } from "../../shell/Icon";

const P = (type: ParamSchema["type"], description: string, extra: Partial<ParamSchema> = {}): ParamSchema => ({ type, description, ...extra });

const NODE = {
  kind: P("string", "вид объекта: dh_set, dh_table, dh_result, wireframe, bm_field, object:variogram_model, object:block_model, object:qaqc_data, object:qaqc_report, object:trend, object:estimation"),
  id: P("integer", "номер объекта (для bm_field — номер блочной модели)"),
  sub: P("string", "имя поля блочной модели (только для kind=bm_field)"),
};

export const commands: Command[] = [
  {
    id: "lineage.status",
    title: "Состояние объекта",
    description:
      "Актуален ли расчётный объект: «актуален», «устарел» (с причиной по цепочке: какая таблица, модель или каркас изменились), «источник удалён», «закреплён», «считается». " +
      "Объект задаётся kind и id (без них — выбранный в дереве). Без объекта — сводка по тому, что показано в дереве: сколько объектов устарело.",
    params: NODE,
    stage: "Э18 (18.Г3)",
  },
  {
    id: "lineage.impact",
    title: "Влияние изменения",
    description:
      "Что построено из объекта, прямо или через другие (наглядно: «таблица → композиты → модель вариограммы → оценка → отчёт»): число по видам и список. " +
      "Если зависимые есть, открывает окно «Влияние изменения» с кнопкой «Перестроить с зависимыми»; если нет — только сообщает. Ничего не меняет и не блокирует.",
    params: NODE,
    stage: "Э18 (18.Г3)",
  },
  {
    id: "lineage.rebuild",
    title: "Перестроить с зависимыми",
    description:
      "Перестраивает объект и устаревшие объекты, построенные из него, по порядку построения (одно задание, ход — вкладка «Задания»); готовые и закреплённые объекты не трогает. " +
      "with_dependents=false — только сам объект. Если перестроение теряет результаты или колонки, нужен drop_lost=true. Меняет данные: после подтверждения. Автоматически объекты не перестраиваются.",
    params: {
      ...NODE,
      with_dependents: P("boolean", "перестроить и зависимые (по умолчанию да)"),
      drop_lost: P("boolean", "согласиться с потерей результатов и колонок, которые перестроение не восстанавливает"),
      confirmed: P("boolean", "план уже показан и подтверждён в окне «Влияние изменения»: без повторного вопроса о плане (служебный параметр окна)"),
    },
    confirm: true,
    stage: "Э18 (18.Г3)",
  },
  {
    id: "lineage.pin",
    title: "Закрепить объект",
    description:
      "Закреплённый объект не помечается «устарел»: он нужен для сравнения версий и выпущенных отчётов; объекты, построенные из него, считаются актуальными. " +
      "pinned=false снимает закрепление; без pinned состояние переключается.",
    params: { ...NODE, pinned: P("boolean", "закрепить (true) или снять закрепление (false); без параметра — переключить") },
    stage: "Э18 (18.Г3)",
  },
];

/** A button of the group «Зависимости» (the ribbon puts it in «Настройки проекта › Данные»; the object — the chosen one). */
export interface LineageButton {
  command: string;
  args?: Record<string, unknown>;
  label: string;
  icon: IconName;
  hint: string;
  small?: boolean;
}

export const LINEAGE_GROUP_TITLE = "Зависимости";

export const LINEAGE_BUTTONS: readonly LineageButton[] = [
  { command: "lineage.status", label: "Состояние", icon: "info", hint: "Актуален ли выбранный объект и почему устарел; без выбора — сколько объектов дерева устарело", small: true },
  { command: "lineage.impact", label: "Влияние", icon: "dendrogram", hint: "Что построено из выбранного объекта: список и число по видам", small: true },
  { command: "lineage.rebuild", label: "Перестроить с зависимыми", icon: "refresh", hint: "Перестроить выбранный объект и устаревшие объекты, построенные из него" },
  { command: "lineage.pin", label: "Закрепить", icon: "lock", hint: "Не помечать объект «устарел» (сравнение версий, выпущенные отчёты)", small: true },
];
