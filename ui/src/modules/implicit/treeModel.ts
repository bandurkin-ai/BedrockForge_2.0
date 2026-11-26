/**
 * The section «Неявные модели» of the data tree as a flat list of rows (ТЗ 8.9.4; ADR 0026 «Команды реестра»): a model, under it three branches —
 * «Данные» (the groups: their points can be shown in 3D), «Разломы» and «Стек» (Э19б: the faults, the domains by age with their relations; each only
 * when the model has them), «Поверхности» and «Тела» (the wireframes the build wrote: ordinary wireframes of the project, children of the model; the
 * outputs of the two wings of a group are under one row «крылья»; the bodies of the wings of a fault are marked «область» — a region of space, not a
 * rock — and the common body of a system of veins is a row of its own) — and «Линии» (the lines of intersection of the veins: layers of strings) when
 * the model has some. The list is made from `GET /models` (`ModelRow`): groups and outputs come with it, so a row costs nothing more; the model whose
 * details are read (`GET /models/{id}`) adds the relations, the radii and the lines the list does not carry. Pure: the tree draws the list, the tests
 * read it (100 models — a few ms).
 */
import type { MenuItem } from "../../shell/ContextMenu";
import type { GroupBrief, ModelInfo, ModelRow, OutputInfo } from "./api";
import { staleLine } from "./run";
import { RELATION_TITLES, relationOf } from "./stackApi";
import { domainsOfMeta, faultsOfMeta, radiusOf, radiusText } from "./stackModel";

export type Branch = "data" | "faults" | "stack" | "surface" | "body" | "line";

export const BRANCH_TITLES: Record<Branch, string> = { data: "Данные", faults: "Разломы", stack: "Стек", surface: "Поверхности", body: "Тела", line: "Линии" };
const BRANCH_ORDER: Branch[] = ["data", "faults", "stack", "surface", "body", "line"];

/** The kinds of groups of the core (§4) in words. */
export const GROUP_TITLES: Record<string, string> = {
  domain: "домен",
  shell: "оболочки содержаний",
  shells: "оболочки содержаний",
  vein: "жила",
  expand: "расширение каркаса",
  system: "система жил",
  vein_system: "система жил",
  fault: "разлом",
};
export const groupTitle = (type: string): string => GROUP_TITLES[type] ?? type;

export type TreeItem =
  | { kind: "model"; key: string; level: number; row: ModelRow; expanded: boolean }
  | { kind: "branch"; key: string; level: number; model: number; branch: Branch; count: number; expanded: boolean }
  | { kind: "group"; key: string; level: number; model: number; group: GroupBrief }
  /** a fault of «Разломы»: the radius and the domains it cuts (known when the details of the model are read) */
  | { kind: "fault"; key: string; level: number; model: number; group: GroupBrief; text: string }
  /** a domain of «Стек» in the order of age (the youngest first) with its relation to the older ones (null — the oldest, or not read yet) */
  | { kind: "stackgroup"; key: string; level: number; model: number; group: GroupBrief; position: number; relation: string | null }
  /** the outputs of the wings of one group under one row (the region of a fault is not a rock) */
  | { kind: "wings"; key: string; level: number; model: number; group: number | null; name: string; count: number; region: boolean; expanded: boolean }
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

/** Does the tree need the details of the model (`GET /models/{id}`) to draw it fully: faults, systems of veins, a stack of two domains. */
export const needsDetails = (r: Pick<ModelRow, "group_list">): boolean => r.group_list.some((g) => g.type === "fault" || g.type === "vein_system") || r.group_list.filter((g) => g.type === "domain").length >= 2;

/** The outputs of a model: the details when they are of this version (they carry the lines, the common body, the codes), else the list with the regions found by the group. */
export function outputsOf(row: ModelRow, info?: Pick<ModelInfo, "version" | "outputs">): OutputInfo[] {
  if (info && info.version === row.version && info.outputs.length) return info.outputs;
  const faults = new Set(row.group_list.filter((g) => g.type === "fault").map((g) => g.id));
  return row.output_list.map((o) => (o.kind === "body" && o.group !== null && faults.has(o.group) ? { ...o, region: true } : o));
}

const groupNameOf = (row: ModelRow, id: number | null): string => (id === null ? "Прочее" : (row.group_list.find((g) => g.id === id)?.name ?? `группа ${id}`));

/** The rows of the tree in the order they are drawn. `details` — the models whose details are read (the relations and the lines come from them). */
export function treeItems(rows: readonly ModelRow[], open: OpenState, details: Readonly<Record<number, ModelInfo | undefined>> = {}): TreeItem[] {
  const out: TreeItem[] = [];
  const dflt = rows.length <= OPEN_UP_TO;
  for (const row of modelsByName(rows)) {
    const mk = modelKey(row.id);
    const expanded = open[mk] ?? dflt;
    out.push({ kind: "model", key: mk, level: 2, row, expanded });
    if (!expanded) continue;
    const info = details[row.id]?.version === row.version ? details[row.id] : undefined;
    const meta = info?.meta ?? {};
    const outputs = new Map<Branch, OutputInfo[]>();
    for (const o of outputsOf(row, info)) {
      const b = branchOfOutput(o);
      outputs.set(b, [...(outputs.get(b) ?? []), o]);
    }
    const domains = row.group_list.filter((g) => g.type === "domain");
    const faults = row.group_list.filter((g) => g.type === "fault");
    for (const b of BRANCH_ORDER) {
      const items = b === "data" ? row.group_list.length : b === "faults" ? faults.length : b === "stack" ? domains.length : (outputs.get(b)?.length ?? 0);
      if ((b === "line" || b === "faults") && !items) continue;
      if (b === "stack" && items < 2) continue; // a stack of one domain has no relations
      const bk = branchKey(row.id, b);
      const branchOpen = open[bk] ?? true;
      out.push({ kind: "branch", key: bk, level: 3, model: row.id, branch: b, count: items, expanded: branchOpen });
      if (!branchOpen) continue;
      if (b === "data") for (const g of row.group_list) out.push({ kind: "group", key: `${bk}:${g.id}`, level: 4, model: row.id, group: g });
      else if (b === "faults") {
        const known = faultsOfMeta(meta);
        for (const g of faults) {
          const f = known.find((x) => x.id === g.id);
          const cutting = domainsOfMeta(meta).filter((d) => g.id !== null && d.faults.includes(g.id)).length;
          out.push({ kind: "fault", key: `${bk}:${g.id}`, level: 4, model: row.id, group: g, text: f ? `${radiusText(radiusOf(f))}${cutting ? `, рассекает доменов: ${cutting}` : ""}` : "" });
        }
      } else if (b === "stack") {
        const read = info ? domainsOfMeta(meta) : [];
        domains.forEach((g, i) => {
          const d = read.find((x) => x.id === g.id);
          out.push({ kind: "stackgroup", key: `${bk}:${g.id}`, level: 4, model: row.id, group: g, position: i + 1, relation: d && i < domains.length - 1 ? RELATION_TITLES[relationOf(d.relation)] : null });
        });
      } else pushOutputs(out, outputs.get(b) ?? [], row, b, bk, open);
    }
  }
  return out;
}

/** The outputs of a branch: the outputs of the two wings of a group go under one row; the rest are rows of their own. */
function pushOutputs(out: TreeItem[], os: readonly OutputInfo[], row: ModelRow, branch: Branch, bk: string, open: OpenState): void {
  const key = (o: OutputInfo) => `${bk}:${o.wireframe_id ?? (o.line_layer_id !== null ? `l${o.line_layer_id}` : o.name)}`;
  for (let i = 0; i < os.length; ) {
    const o = os[i];
    if (o.wing === null) {
      out.push({ kind: "output", key: key(o), level: 4, model: row.id, output: o, branch });
      i++;
      continue;
    }
    let j = i;
    while (j < os.length && os[j].wing !== null && os[j].group === o.group) j++;
    const wk = `${bk}:w${o.group ?? "rest"}`;
    const expanded = open[wk] ?? true;
    out.push({ kind: "wings", key: wk, level: 4, model: row.id, group: o.group, name: groupNameOf(row, o.group), count: j - i, region: os.slice(i, j).every((x) => x.region), expanded });
    if (expanded) for (const x of os.slice(i, j)) out.push({ kind: "output", key: key(x), level: 5, model: row.id, output: x, branch });
    i = j;
  }
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
    cmd("Разлом…", "implicit.fault"),
    cmd("Система жил…", "implicit.vein_system"),
    cmd("Стек отношений…", "implicit.relations"),
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
  if (branch === "data") return [item("Домены литологии…", "implicit.domains"), item("Оболочки содержаний…", "implicit.shells"), item("Жила…", "implicit.vein"), item("Расширение каркаса…", "implicit.expand"), item("Разлом…", "implicit.fault"), item("Система жил…", "implicit.vein_system")];
  if (branch === "faults") return [item("Новый разлом…", "implicit.fault"), item("Стек отношений…", "implicit.relations")];
  if (branch === "stack") return [item("Стек отношений…", "implicit.relations")];
  return [item("В каркас…", "implicit.to_wireframe")];
}

/** The command whose window edits a group of a kind (a domain is edited in the wizard). */
const GROUP_COMMANDS: Record<string, string> = { shell: "implicit.shells", vein: "implicit.vein", expand: "implicit.expand", fault: "implicit.fault", vein_system: "implicit.vein_system" };

/** The menu of a group row: «Изменить…» (the window of its kind with this group); a domain — the wizard and the stack. */
export function groupMenu(model: number, group: GroupBrief, reason: Reason, run: Run): MenuItem[] {
  const id = GROUP_COMMANDS[group.type];
  const items: MenuItem[] = [];
  if (id && group.id !== null) items.push({ label: `Изменить «${group.name}»…`, reason: reason(id), run: () => void run(id, { model_id: model, group_id: group.id }) });
  if (group.type === "domain") {
    items.push({ label: "Домены литологии…", reason: reason("implicit.domains"), run: () => void run("implicit.domains", { model_id: model }) });
    items.push({ label: "Стек отношений…", reason: reason("implicit.relations"), run: () => void run("implicit.relations", { model_id: model }) });
  }
  return items;
}
