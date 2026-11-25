/**
 * The section «Неявные модели» of the data tree as a flat list of rows (ТЗ 8.9.4; ADR 0026 «Команды реестра»): a model, under it three branches —
 * «Данные» (the groups: their points can be shown in 3D), «Поверхности» and «Тела» (the wireframes the build wrote: ordinary wireframes of the
 * project, children of the model) — and «Линии» (Э19б) when the model has some. The list is made from `GET /models` (`ModelRow`): groups and outputs come
 * with it, so a row costs nothing more. Pure: the tree draws the list, the tests read it (100 models — a few ms).
 */
import type { MenuItem } from "../../shell/ContextMenu";
import type { GroupBrief, ModelRow, OutputInfo } from "./api";
import { staleLine } from "./run";

export type Branch = "data" | "surface" | "body" | "line";

export const BRANCH_TITLES: Record<Branch, string> = { data: "Данные", surface: "Поверхности", body: "Тела", line: "Линии" };
const BRANCH_ORDER: Branch[] = ["data", "surface", "body", "line"];

/** The kinds of groups of the core (§4) in words. */
export const GROUP_TITLES: Record<string, string> = {
  domain: "домен",
  shells: "оболочки содержаний",
  vein: "жила",
  expand: "расширение каркаса",
  system: "система жил",
};
export const groupTitle = (type: string): string => GROUP_TITLES[type] ?? type;

export type TreeItem =
  | { kind: "model"; key: string; level: number; row: ModelRow; expanded: boolean }
  | { kind: "branch"; key: string; level: number; model: number; branch: Branch; count: number; expanded: boolean }
  | { kind: "group"; key: string; level: number; model: number; group: GroupBrief }
  | { kind: "output"; key: string; level: number; model: number; output: OutputInfo; branch: Branch };

/** The models by name (the order of the tree), the same names — by number. */
export const modelsByName = (rows: readonly ModelRow[]): ModelRow[] => [...rows].sort((a, b) => a.name.localeCompare(b.name, "ru") || a.id - b.id);

/** Which kind of output goes under which branch (a kind the core may add later goes to the bodies: they are all wireframes). */
export const branchOfOutput = (o: Pick<OutputInfo, "kind">): Branch => (o.kind === "surface" ? "surface" : o.kind === "line" ? "line" : "body");

export interface OpenState {
  /** a model or a branch the person opened (`true`) or closed (`false`); a missing key — the default */
  [key: string]: boolean | undefined;
}

export const modelKey = (id: number): string => `m${id}`;
export const branchKey = (id: number, b: Branch): string => `m${id}:${b}`;

/** Up to this number of models the models are open at the start; with more the person opens what they need (the list stays short). */
export const OPEN_UP_TO = 8;

/** The rows of the tree in the order they are drawn. */
export function treeItems(rows: readonly ModelRow[], open: OpenState): TreeItem[] {
  const out: TreeItem[] = [];
  const dflt = rows.length <= OPEN_UP_TO;
  for (const row of modelsByName(rows)) {
    const mk = modelKey(row.id);
    const expanded = open[mk] ?? dflt;
    out.push({ kind: "model", key: mk, level: 2, row, expanded });
    if (!expanded) continue;
    const outputs = new Map<Branch, OutputInfo[]>();
    for (const o of row.output_list) {
      const b = branchOfOutput(o);
      outputs.set(b, [...(outputs.get(b) ?? []), o]);
    }
    for (const b of BRANCH_ORDER) {
      const items = b === "data" ? row.group_list.length : (outputs.get(b)?.length ?? 0);
      if (b === "line" && !items) continue;
      const bk = branchKey(row.id, b);
      const branchOpen = open[bk] ?? true;
      out.push({ kind: "branch", key: bk, level: 3, model: row.id, branch: b, count: items, expanded: branchOpen });
      if (!branchOpen) continue;
      if (b === "data") for (const g of row.group_list) out.push({ kind: "group", key: `${bk}:${g.id}`, level: 4, model: row.id, group: g });
      else for (const o of outputs.get(b) ?? []) out.push({ kind: "output", key: `${bk}:${o.wireframe_id ?? o.name}`, level: 4, model: row.id, output: o, branch: b });
    }
  }
  return out;
}

/** The state of a row in a word for the tree: the checks failed, being built, the data changed. Empty — nothing to say. */
export function stateText(r: Pick<ModelRow, "state" | "stale" | "stale_reason" | "source_lost">): string {
  if (r.state === "building") return "считается";
  if (r.state === "error") return "ошибка построения";
  if (r.source_lost) return "источник удалён";
  if (r.stale) return r.stale_reason ? `устарела: ${r.stale_reason}` : "устарела";
  return "";
}

const n = (v: number) => v.toLocaleString("ru-RU", { maximumFractionDigits: 0 });
const s1 = (v: number) => v.toLocaleString("ru-RU", { maximumFractionDigits: 1 });

/** «3 316 точек · 16 участков · 4,2 с» of the last build; «не построена» before it. */
export function statsText(r: Pick<ModelRow, "stats">): string {
  const s = r.stats;
  if (!s) return "не построена";
  return [s.points !== null ? `${n(s.points)} точек` : null, s.patches !== null ? `${n(s.patches)} участков` : null, s.seconds !== null ? `${s1(s.seconds)} с` : null].filter(Boolean).join(" · ") || "построена";
}

/** The tooltip of a model row. */
export function modelTitleText(r: ModelRow): string {
  const parts = [`Модель «${r.name}», версия ${r.version}`, `Групп ${r.groups}, каркасов-выходов ${r.outputs}`, statsText(r)];
  const st = stateText(r);
  if (st) parts.push(st[0].toUpperCase() + st.slice(1));
  const line = staleLine(r);
  if (line && !st) parts.push(line);
  parts.push("Двойной щелчок — «Свойства»; правая кнопка — команды модели");
  return parts.join("\n");
}

type Run = (id: string, args: Record<string, unknown>) => void | Promise<unknown>;
type Reason = (id: string) => string | null;

/** The menu of a model: each item is a command of the registry (unavailable — with the reason); «Обновить список» reads the list again. */
export function modelMenu(r: Pick<ModelRow, "id" | "stats" | "state" | "source_lost">, reason: Reason, run: Run, refresh: () => void): MenuItem[] {
  const args = { model_id: r.id };
  const cmd = (label: string, id: string, extra: Record<string, unknown> = {}, lock: string | null = null): MenuItem => ({ label, reason: reason(id) ?? lock, run: () => void run(id, { ...args, ...extra }) });
  const built = r.stats !== null && r.state !== "building";
  const needBuilt = built ? null : "Модель ещё не построена: сначала «Обновить модель»";
  return [
    cmd("Домены литологии…", "implicit.domains"),
    cmd("Оболочки содержаний…", "implicit.shells"),
    cmd("Жила…", "implicit.vein"),
    cmd("Расширение каркаса…", "implicit.expand"),
    { separator: true, label: "" },
    cmd("Обновить модель", "implicit.update", {}, r.state === "building" ? "Модель сейчас строится" : r.source_lost ? "Источник данных удалён: выберите другие данные в мастере" : null),
    cmd("В каркас…", "implicit.to_wireframe", {}, needBuilt),
    cmd("Проверка по данным…", "implicit.check", {}, needBuilt),
    cmd("Опора данных…", "implicit.support", {}, needBuilt),
    cmd("Сверка со скважинами…", "implicit.compare_holes", {}, needBuilt),
    { separator: true, label: "" },
    { label: "Обновить список", run: refresh },
    cmd("Удалить…", "implicit.delete"),
  ];
}

/** The menu of a branch of the data: the commands that add a group of that kind. */
export function branchMenu(model: number, branch: Branch, reason: Reason, run: Run): MenuItem[] {
  const item = (label: string, id: string): MenuItem => ({ label, reason: reason(id), run: () => void run(id, { model_id: model }) });
  if (branch === "data") return [item("Домены литологии…", "implicit.domains"), item("Оболочки содержаний…", "implicit.shells"), item("Жила…", "implicit.vein"), item("Расширение каркаса…", "implicit.expand")];
  return [item("В каркас…", "implicit.to_wireframe")];
}
