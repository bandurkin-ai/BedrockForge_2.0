/**
 * Modelling actions of the interface: the string editor's session (open, save, leave), the
 * journal of versions, export, and the dialogs of operations on lines and wireframes.
 * No React here: App gives the state it needs through `ModelingContext`.
 */
import {
  createLineLayer, getLineArrays, getLineVersions, getSelfIntersections, lineExportUrl, moveLinesToLayer, rebuildWireframe, restoreLineVersion,
  saveLineGeometry, startOperation, downloadFile, wireframeExportUrl, type Dataset, type JobInfo, type LineLayerInfo, type PickResult, type WireframeInfo,
} from "../api";
import { fromStored } from "../edit/io";
import type { Vec3 } from "../edit/model";
import { EditSession } from "../edit/session";
import type { SectionLine } from "../viz/geometry";
import { askConfirm, askText, showMessage } from "./dialogs";
import { askForm, type FormField, type FormValue } from "./FormDialog";

/** Triangles of a wireframe picked for a manual fix: some to delete, some to add. */
export interface TriangleEdit {
  wireframeId: number;
  name: string;
  mode: "remove" | "add";
  /** index -> corners (project coordinates) of triangles to delete */
  remove: Map<number, number[][]>;
  add: number[][][];
  /** corners clicked so far for the next new triangle */
  pending: number[][];
}

export interface ModelingContext {
  projectId: number;
  datasets: () => Dataset[];
  reload: () => void;
  notify: (text: string, error?: boolean) => void;
  edit: () => EditSession | null;
  setEdit: (session: EditSession | null) => void;
  section: () => SectionLine | null;
  selected: () => { kind: string; id: number } | null;
  select: (object: { kind: "wireframe" | "lines"; id: number } | null) => void;
  show: (kind: "wireframe" | "lines", id: number) => void;
  onJobStarted: (job: JobInfo) => void;
  triangles: () => TriangleEdit | null;
  setTriangles: (edit: TriangleEdit | null) => void;
  /** Marks triangles in the view (corners in project coordinates, 9 numbers each); null removes. */
  mark: (key: string, corners: number[] | null, color?: string) => void;
}

const num = (v: FormValue | undefined) => Number(v);
const fmt = (v: unknown, digits = 2) => (typeof v === "number" ? v.toLocaleString("ru-RU", { maximumFractionDigits: digits }) : "—");

const CAPS = [
  { value: "flat", label: "плоская крышка" }, { value: "point", label: "выклинивание в точку" }, { value: "line", label: "выклинивание в линию" },
  { value: "extend", label: "продолжение и крышка" }, { value: "none", label: "не замыкать" },
];

export function createModeling(ctx: ModelingContext) {
  const wireframes = (): WireframeInfo[] => ctx.datasets().flatMap((d) => d.wireframes ?? []);
  const layers = (): LineLayerInfo[] => ctx.datasets().flatMap((d) => d.lines ?? []);
  const wf = (id: number) => wireframes().find((w) => w.id === id);
  const layer = (id: number) => layers().find((l) => l.id === id);
  const wfOptions = (filter?: (w: WireframeInfo) => boolean) => wireframes().filter(filter ?? (() => true)).map((w) => ({ value: String(w.id), label: w.name }));
  const layerOptions = (filter?: (l: LineLayerInfo) => boolean) => layers().filter(filter ?? (() => true)).map((l) => ({ value: String(l.id), label: `${l.name} (${l.line_count})` }));
  const closedLayers = () => layerOptions((l) => l.stats.closed > 0);
  const selectedId = (kind: string) => (ctx.selected()?.kind === kind ? ctx.selected()!.id : null);
  const isClosed = (w: WireframeInfo) => Boolean(w.stats?.closed);

  async function run(op: string, params: Record<string, unknown>, name?: string): Promise<string> {
    const job = await startOperation(ctx.projectId, op, params, name);
    ctx.onJobStarted(job);
    return `Задание запущено: ${job.title}`;
  }

  /** Picks an object in a dialog when it is not given. */
  async function choose(title: string, label: string, options: { value: string; label: string }[], preset: number | null, empty: string, extra: FormField[] = [],
    message?: string): Promise<Record<string, FormValue> | null> {
    if (!options.length) {
      await showMessage({ title, message: empty });
      return null;
    }
    const value = preset !== null && options.some((o) => o.value === String(preset)) ? String(preset) : options[0].value;
    return askForm({ title, message, fields: [{ key: "id", label, type: "select", options, value }, ...extra] });
  }

  // ---------------------------------------------------------------- the string editor

  async function newLayer(name?: string, datasetId?: number): Promise<string | undefined> {
    const given = name ?? (await askText({ title: "Новый слой линий", message: "Название слоя — код стринга при экспорте в STR", placeholder: "Например, РУДА", value: "" }));
    if (!given) return undefined;
    const dataset = await createLineLayer(ctx.projectId, { name: given, dataset_id: datasetId });
    ctx.reload();
    const made = dataset.lines?.[dataset.lines.length - 1];
    if (made) await startEdit(made.id, made);
    return `Создан слой «${given}»: режим правки включён`;
  }

  async function leave(): Promise<boolean> {
    const session = ctx.edit();
    if (!session) return true;
    if (session.dirty) {
      const keep = await askConfirm({ title: "Несохранённые изменения", message: `Сохранить изменения слоя «${session.layerName}»?`, ok: "Сохранить", cancel: "Не сохранять" });
      if (keep && !(await save())) return false;
    }
    ctx.setEdit(null);
    return true;
  }

  async function startEdit(layerId: number, info?: LineLayerInfo): Promise<string> {
    const target = info ?? layer(layerId);
    if (!target) throw new Error(`Слоя линий ${layerId} нет в проекте`);
    if (ctx.edit()?.layerId === layerId) return `Слой «${target.name}» уже редактируется`;
    if (!(await leave())) return "Правка не начата: прежний слой не сохранён";
    const a = await getLineArrays(layerId);
    const session = new EditSession(layerId, target.name, target.color, a.version, fromStored(a.vertices, a.offsets, a.closed, a.attributes));
    ctx.show("lines", layerId);
    ctx.setEdit(session);
    ctx.select({ kind: "lines", id: layerId });
    return `Правка слоя «${target.name}» (версия ${a.version})`;
  }

  /** Saves the working copy as the next version. False when it was not saved. */
  async function save(): Promise<boolean> {
    const session = ctx.edit();
    if (!session) return false;
    if (!session.dirty) {
      session.say("Изменений нет");
      return true;
    }
    session.finishDrawing();
    session.saving = true;
    session.touch();
    try {
      const saved = await saveLineGeometry(session.layerId, session.baseVersion, session.stored());
      session.saved(saved.version);
      ctx.reload();
      ctx.notify(`Слой «${session.layerName}» сохранён: версия ${saved.version}, линий ${saved.line_count}, вершин ${saved.point_count}`);
      return true;
    } catch (e) {
      // 409: another tab saved the layer meanwhile — nothing is overwritten.
      await showMessage({ title: "Слой не сохранён", message: (e as Error).message, error: true });
      return false;
    } finally {
      session.saving = false;
      session.touch();
    }
  }

  async function versions(layerId: number): Promise<string | undefined> {
    const target = layer(layerId);
    if (!target) throw new Error("Слой не найден");
    const rows = await getLineVersions(layerId);
    const titles: Record<string, string> = { import: "импорт", create: "создание", save: "сохранение", restore: "возврат", move: "перенос линий", operation: "операция" };
    const options = rows.map((r) => ({
      value: String(r.version),
      label: `Версия ${r.version}${r.current ? " (текущая)" : ""} · ${new Date(r.created_at).toLocaleString("ru-RU")} · ${titles[r.action] ?? r.action}`
        + ` · линий ${r.prev_line_count}→${r.line_count}, вершин ${r.prev_point_count}→${r.point_count}${r.author ? ` · ${r.author}` : ""}${r.note ? ` · ${r.note}` : ""}`,
    }));
    const v = await askForm({
      title: `Версии слоя «${target.name}»`, ok: "Вернуть версию",
      message: "Возврат делает выбранную версию текущей новой записью журнала: ничего не стирается.",
      fields: [{ key: "version", label: "Версия", type: "select", options, value: options[Math.min(1, options.length - 1)]?.value }],
      check: (x) => (rows.find((r) => String(r.version) === x.version)?.current ? "Это текущая версия" : null),
    });
    if (!v) return undefined;
    if (ctx.edit()?.layerId === layerId) {
      if (ctx.edit()!.dirty && !(await askConfirm({ title: "Возврат версии", message: "В редакторе есть несохранённые изменения: они будут потеряны. Продолжить?", ok: "Вернуть версию", danger: true }))) return undefined;
      ctx.setEdit(null);
    }
    const restored = await restoreLineVersion(layerId, num(v.version));
    ctx.reload();
    return `Слой «${target.name}»: возвращена версия ${v.version} (теперь версия ${restored.version})`;
  }

  /** Moves the selected lines of the edited layer to another layer (both get a new version). */
  async function moveSelected(): Promise<string | undefined> {
    const session = ctx.edit();
    if (!session) throw new Error("Режим правки не включён");
    const ids = session.targetLines();
    if (!ids.length) throw new Error("Выберите линии для переноса");
    const v = await choose("Перенести линии в другой слой", "Слой", layerOptions((l) => l.id !== session.layerId), null, "В проекте нет другого слоя линий: создайте его («+» у группы «Линии»)");
    if (!v) return undefined;
    if (session.dirty && !(await save())) return undefined;
    const indices = ids.map((id) => session.layer.lines.findIndex((l) => l.id === id)).filter((i) => i >= 0);
    const [source] = await moveLinesToLayer(session.layerId, indices, num(v.id), session.baseVersion);
    ctx.setEdit(null);
    ctx.reload();
    await startEdit(source.id, source);
    return `Перенесено линий: ${indices.length}`;
  }

  /** Downloads an export; a refusal is shown in the application's window (not a browser page). */
  async function download(url: string, fallback: string, what: string): Promise<string> {
    try {
      const name = await downloadFile(url, fallback);
      return `${what}: файл «${name}» сохранён в загрузки`;
    } catch (e) {
      void showMessage({ title: `${what} не выполнен`, message: (e as Error).message, error: true });
      throw e;
    }
  }
  async function exportLines(ids: number[], format: "str" | "dxf"): Promise<string> {
    if (!ids.length) throw new Error("Не выбран слой линий");
    // The file is written from the saved layer: unsaved edits are saved first (or left out on request).
    const session = ctx.edit();
    if (session && ids.includes(session.layerId) && session.dirty) {
      const go = await askConfirm({
        title: "Экспорт линий", ok: "Сохранить и выгрузить",
        message: `В слое «${session.layerName}» есть несохранённые изменения. В файл попадает сохранённая версия слоя — сохранить изменения перед выгрузкой?`,
      });
      if (!go) return "Экспорт отменён";
      if (!(await save())) return "Экспорт отменён: слой не сохранён";
    }
    const ext = format === "str" ? "STR" : "dxf";
    return download(lineExportUrl(ids, format), `Линии.${ext}`, `Экспорт линий в ${format.toUpperCase()}`);
  }
  function exportWireframes(ids: number[], format: "dxf" | "obj"): Promise<string> {
    if (!ids.length) throw new Error("Не выбран каркас");
    return download(wireframeExportUrl(ids, format), `Каркас.${format}`, `Экспорт каркасов в ${format.toUpperCase()}`);
  }

  // ---------------------------------------------------------------- operations

  /** Contours of the operation: the selected lines of the edited layer, else the whole layer. */
  function contoursOf(layerId: number): { layer_id: number; lines: number[] | null }[] {
    const session = ctx.edit();
    if (session?.layerId === layerId && session.shownSelection.lines.length >= 2) {
      const picked = session.shownSelection.lines.map((id) => session.layer.lines.findIndex((l) => l.id === id)).filter((i) => i >= 0);
      return [{ layer_id: layerId, lines: picked }];
    }
    return [{ layer_id: layerId, lines: null }];
  }
  async function savedFirst(layerId: number): Promise<boolean> {
    const session = ctx.edit();
    return !(session?.layerId === layerId && session.dirty) || save();
  }

  async function stitch(layerId?: number): Promise<string | undefined> {
    const preset = layerId ?? ctx.edit()?.layerId ?? selectedId("lines");
    const session = ctx.edit();
    const picked = session && session.shownSelection.lines.length >= 2 ? session.shownSelection.lines.length : 0;
    const v = await choose("Сшивка контуров в тело", "Слой с контурами", closedLayers().length ? closedLayers() : layerOptions(), preset,
      "В проекте нет слоя с замкнутыми контурами: нарисуйте контуры на разрезах инструментом «Полигон»", [
        { key: "start_cap", label: "Торец первого разреза", type: "select", options: CAPS, value: "flat" },
        { key: "end_cap", label: "Торец последнего разреза", type: "select", options: CAPS, value: "flat" },
        { key: "cap_distance", label: "Вынос торца, доля расстояния между разрезами", type: "number", value: 0.5, min: 0.01,
          show: (x) => [x.start_cap, x.end_cap].some((c) => c === "point" || c === "line" || c === "extend") },
        { key: "name", label: "Название тела", type: "text", value: "" },
      ], picked
        ? `Сшиваются выбранные в редакторе контуры (${picked}), если выбран редактируемый слой; тай-линии — линии из двух вершин.`
        : "Сшиваются все замкнутые контуры слоя по порядку разрезов; линии из двух вершин — тай-линии. Разветвления (один контур против нескольких) строятся автоматически.");
    if (!v || !(await savedFirst(num(v.id)))) return undefined;
    return run("stitch", { contours: contoursOf(num(v.id)), start_cap: v.start_cap, end_cap: v.end_cap, cap_distance: v.cap_distance ?? 0.5 }, String(v.name || ""));
  }

  async function extrude(layerId?: number): Promise<string | undefined> {
    const v = await choose("Выдавливание полигона", "Слой с полигоном", closedLayers(), layerId ?? ctx.edit()?.layerId ?? selectedId("lines"),
      "В проекте нет замкнутых полигонов", [
        { key: "distance", label: "Расстояние, м", type: "number", value: 10 },
        { key: "direction", label: "Направление", type: "select", value: "normal",
          options: [{ value: "normal", label: "по нормали к полигону" }, { value: "z", label: "по вертикали" }, { value: "vector", label: "по вектору" }] },
        { key: "vx", label: "Вектор X", type: "number", value: 0, show: (x) => x.direction === "vector" },
        { key: "vy", label: "Вектор Y", type: "number", value: 0, show: (x) => x.direction === "vector" },
        { key: "vz", label: "Вектор Z", type: "number", value: 1, show: (x) => x.direction === "vector" },
        { key: "both_sides", label: "в обе стороны (по половине расстояния)", type: "checkbox", value: false },
      ], "Каждый замкнутый полигон слоя (или выбранные в редакторе) становится телом.");
    if (!v || !(await savedFirst(num(v.id)))) return undefined;
    const vector = v.direction === "z" ? [0, 0, 1] : v.direction === "vector" ? [num(v.vx), num(v.vy), num(v.vz)] : null;
    const session = ctx.edit();
    const contours = session?.layerId === num(v.id) && session.shownSelection.lines.length
      ? [{ layer_id: num(v.id), lines: session.shownSelection.lines.map((id) => session.layer.lines.findIndex((l) => l.id === id)).filter((i) => i >= 0) }]
      : [{ layer_id: num(v.id), lines: null }];
    return run("extrude", { contours, distance: v.distance, vector, both_sides: v.both_sides });
  }

  async function repair(id?: number): Promise<string | undefined> {
    const v = await choose("Исправление каркаса", "Каркас", wfOptions(), id ?? selectedId("wireframe"), "В проекте нет каркасов", [
      { key: "weld_on", label: "сшить совпадающие вершины", type: "checkbox", value: true },
      { key: "weld", label: "Допуск сшивки, м", type: "number", value: 0.000001, min: 0, show: (x) => Boolean(x.weld_on) },
      { key: "drop_degenerate", label: "удалить вырожденные треугольники", type: "checkbox", value: true },
      { key: "drop_duplicate", label: "удалить повторные треугольники", type: "checkbox", value: true },
      { key: "fix_nonmanifold", label: "убрать рёбра с тремя и более треугольниками", type: "checkbox", value: true },
      { key: "fill_holes", label: "закрыть отверстия", type: "checkbox", value: true },
      { key: "orient", label: "согласовать нормали и развернуть наружу", type: "checkbox", value: true },
    ], "Результат — новый каркас; исходный не меняется. Самопересечения не исправляются автоматически: их покажет команда «Показать самопересечения».");
    if (!v) return undefined;
    const { id: wid, ...flags } = v;
    return run("repair", { wireframe_id: num(wid), ...flags });
  }

  async function simplify(id?: number): Promise<string | undefined> {
    const v = await choose("Упрощение каркаса", "Каркас", wfOptions(), id ?? selectedId("wireframe"), "В проекте нет каркасов", [
      { key: "by", label: "Упростить", type: "select", value: "tolerance", options: [{ value: "tolerance", label: "до допуска по отклонению" }, { value: "count", label: "до числа треугольников" }] },
      { key: "tolerance", label: "Наибольшее отклонение, м", type: "number", value: 0.5, min: 1e-6, show: (x) => x.by === "tolerance" },
      { key: "target", label: "Число треугольников", type: "number", value: 10000, min: 4, show: (x) => x.by === "count" },
    ]);
    if (!v) return undefined;
    return run("simplify", v.by === "count" ? { wireframe_id: num(v.id), target_triangles: Math.round(num(v.target)) } : { wireframe_id: num(v.id), tolerance: v.tolerance });
  }

  async function smooth(id?: number): Promise<string | undefined> {
    const v = await choose("Сглаживание каркаса", "Каркас", wfOptions(), id ?? selectedId("wireframe"), "В проекте нет каркасов", [
      { key: "iterations", label: "Проходов", type: "number", value: 10, min: 1 },
      { key: "strength", label: "Сила, 0–1", type: "number", value: 0.5, min: 0.01 },
      { key: "keep_boundary", label: "не трогать край незамкнутой поверхности", type: "checkbox", value: true },
    ], "Сглаживание без усадки: объём замкнутого тела сохраняется. В отчёте — наибольшее отклонение от исходной поверхности.");
    if (!v) return undefined;
    return run("smooth", { wireframe_id: num(v.id), iterations: Math.round(num(v.iterations)), strength: v.strength, keep_boundary: v.keep_boundary });
  }

  async function split(id?: number): Promise<string | undefined> {
    const v = await choose("Разделение каркаса на связные части", "Каркас", wfOptions(), id ?? selectedId("wireframe"), "В проекте нет каркасов");
    return v ? run("split", { wireframe_id: num(v.id) }) : undefined;
  }

  async function two(title: string, labels: [string, string], options: { value: string; label: string }[], first: number | null, extra: FormField[] = [], message?: string) {
    if (options.length < 2) {
      await showMessage({ title, message: "Для операции нужны два подходящих каркаса в проекте" });
      return null;
    }
    const a = first !== null && options.some((o) => o.value === String(first)) ? String(first) : options[0].value;
    return askForm({
      title, message,
      fields: [{ key: "a", label: labels[0], type: "select", options, value: a }, { key: "b", label: labels[1], type: "select", options, value: options.find((o) => o.value !== a)!.value }, ...extra],
      check: (x) => (x.a === x.b ? "Выберите два разных каркаса" : null),
    });
  }

  async function merge(ids?: number[]): Promise<string | undefined> {
    if (ids && ids.length >= 2) return run("merge", { wireframe_ids: ids });
    const v = await two("Объединение каркасов в один объект", ["Первый каркас", "Второй каркас"], wfOptions(), selectedId("wireframe"), [],
      "Каркасы складываются в один объект без изменения геометрии (не булево объединение).");
    return v ? run("merge", { wireframe_ids: [num(v.a), num(v.b)] }) : undefined;
  }

  async function boolean(op?: string, a?: number, b?: number): Promise<string | undefined> {
    if (op && a && b) return run("boolean", { op, a, b });
    const closed = wfOptions(isClosed);
    if (closed.length < 2) {
      const open = wireframes().filter((w) => !isClosed(w)).map((w) => `«${w.name}»`).slice(0, 5).join(", ");
      await showMessage({ title: "Булева операция", message: `Нужны два замкнутых тела.${open ? ` Не замкнуты: ${open} — примените к ним «Исправить каркас».` : ""}` });
      return undefined;
    }
    const v = await two("Булева операция", ["Тело A", "Тело B"], closed, selectedId("wireframe"), [
      { key: "op", label: "Операция", type: "select", value: op ?? "difference",
        options: [{ value: "union", label: "объединение A ∪ B" }, { value: "intersection", label: "пересечение A ∩ B" }, { value: "difference", label: "вычитание A − B" }] },
    ], "Только замкнутые тела; результат — новое замкнутое тело, исходные не меняются.");
    return v ? run("boolean", { op: v.op, a: num(v.a), b: num(v.b) }) : undefined;
  }

  async function clip(id?: number): Promise<string | undefined> {
    const section = ctx.section();
    const surfaces = wfOptions((w) => !isClosed(w));
    const polygons = closedLayers();
    const by = [
      { value: "level", label: "уровнем Z" },
      ...(section ? [{ value: "section", label: "плоскостью текущего разреза" }] : []),
      ...(polygons.length ? [{ value: "polygon", label: "полигоном в плане" }] : []),
      ...(surfaces.length ? [{ value: "surface", label: "поверхностью (ЦМР, разлом)" }] : []),
    ];
    const v = await choose("Обрезка каркаса", "Каркас", wfOptions(), id ?? selectedId("wireframe"), "В проекте нет каркасов", [
      { key: "by", label: "Обрезать", type: "select", options: by, value: by[0].value },
      { key: "level", label: "Уровень Z, м", type: "number", value: 0, show: (x) => x.by === "level" },
      { key: "polygon", label: "Слой с полигоном", type: "select", options: polygons, value: polygons[0]?.value, show: (x) => x.by === "polygon" },
      { key: "surface", label: "Секущая поверхность", type: "select", options: surfaces, value: surfaces[0]?.value, show: (x) => x.by === "surface" },
      { key: "keep", label: "Оставить", type: "select", value: "both",
        options: [{ value: "both", label: "обе части" }, { value: "first", label: "выше / перед плоскостью / внутри полигона" }, { value: "second", label: "ниже / за плоскостью / вне полигона" }] },
    ]);
    if (!v) return undefined;
    const wid = num(v.id);
    const side = (first: string, second: string) => (v.keep === "first" ? first : v.keep === "second" ? second : "both");
    if (v.by === "polygon") return run("clip_polygon", { wireframe_id: wid, polygon: { layer_id: num(v.polygon) }, keep: side("inside", "outside") });
    if (v.by === "surface") return run("clip_surface", { wireframe_id: wid, surface_id: num(v.surface), keep: side("above", "below") });
    if (v.by === "section" && section) {
      const a = (section.az * Math.PI) / 180;
      return run("clip_plane", { wireframe_id: wid, point: [section.x0, section.y0, 0], normal: [Math.cos(a), -Math.sin(a), 0], keep: side("positive", "negative") });
    }
    return run("clip_plane", { wireframe_id: wid, point: [0, 0, num(v.level)], normal: [0, 0, 1], keep: side("positive", "negative") });
  }

  async function intersectionLines(): Promise<string | undefined> {
    const v = await two("Линии пересечения каркасов", ["Первый каркас", "Второй каркас"], wfOptions(), selectedId("wireframe"), [], "Результат — слой линий.");
    return v ? run("intersection_lines", { a: num(v.a), b: num(v.b) }) : undefined;
  }

  async function dtm(layerId?: number): Promise<string | undefined> {
    const sets = ctx.datasets().filter((d) => d.kind === "drillholes" && d.counts?.collar).map((d) => ({ value: String(d.id), label: `устья: ${d.name}` }));
    const sources = [...layerOptions((l) => l.point_count > 0).map((o) => ({ value: `l${o.value}`, label: o.label })), ...sets.map((o) => ({ value: `s${o.value}`, label: o.label }))];
    const preset = layerId ?? selectedId("lines");
    const none = [{ value: "", label: "— нет —" }];
    if (!sources.length) {
      await showMessage({ title: "ЦМР", message: "Не из чего строить поверхность: нужен слой линий или точек, или устья скважин" });
      return undefined;
    }
    const v = await askForm({
      title: "ЦМР из точек и линий", message: "Триангуляция Делоне в плане. Линии — структурные: рёбра поверхности их не пересекают.",
      fields: [
        { key: "source", label: "Точки и линии", type: "select", options: sources, value: preset !== null ? `l${preset}` : sources[0].value },
        { key: "more", label: "Ещё источник", type: "select", options: [...none, ...sources], value: "" },
        { key: "breaklines", label: "линии — структурные (иначе только их вершины)", type: "checkbox", value: true },
        { key: "boundary", label: "Граница — полигон слоя", type: "select", options: [...none, ...closedLayers()], value: "", hint: "Пусто — выпуклая оболочка точек" },
        { key: "max_edge", label: "Отбросить треугольники с ребром длиннее, м", type: "number", value: "", optional: true, min: 0 },
        { key: "name", label: "Название", type: "text", value: "" },
      ],
    });
    if (!v) return undefined;
    const picked = [String(v.source), String(v.more)].filter(Boolean);
    return run("dtm", {
      layers: picked.filter((s) => s[0] === "l").map((s) => Number(s.slice(1))), set_ids: picked.filter((s) => s[0] === "s").map((s) => Number(s.slice(1))),
      breaklines: v.breaklines, boundary: v.boundary ? { layer_id: num(v.boundary) } : null, max_edge: v.max_edge === "" ? null : v.max_edge,
    }, String(v.name || ""));
  }

  async function contours(id?: number): Promise<string | undefined> {
    const v = await choose("Изолинии", "Поверхность", wfOptions(), id ?? selectedId("wireframe"), "В проекте нет каркасов", [
      { key: "interval", label: "Шаг, м", type: "number", value: 5, min: 1e-6 },
      { key: "base", label: "Опорная отметка, м", type: "number", value: 0 },
      { key: "major_every", label: "Основная — каждая N-я", type: "number", value: 5, min: 1 },
    ], "Результат — слой основных и слой дополнительных горизонталей с атрибутом отметки ELEV.");
    return v ? run("contours", { wireframe_id: num(v.id), interval: v.interval, base: v.base, major_every: Math.round(num(v.major_every)) }) : undefined;
  }

  async function volume(): Promise<string | undefined> {
    const surfaces = wfOptions((w) => !isClosed(w));
    if (!surfaces.length) {
      await showMessage({ title: "Объём между поверхностями", message: "Нужна незамкнутая поверхность (ЦМР). Постройте её: «ЦМР из точек и линий»" });
      return undefined;
    }
    const none = [{ value: "", label: "— нет —" }];
    const v = await askForm({
      title: "Объём между поверхностями", message: "Выемка — где первая поверхность выше второй, насыпь — где ниже; в пределах общего контура или полигона.",
      fields: [
        { key: "top", label: "Первая поверхность", type: "select", options: surfaces, value: surfaces[0].value },
        { key: "against", label: "Сравнить", type: "select", value: surfaces.length > 1 ? "surface" : "level", options: [{ value: "surface", label: "со второй поверхностью" }, { value: "level", label: "с уровнем Z" }] },
        { key: "bottom", label: "Вторая поверхность", type: "select", options: surfaces, value: surfaces[1]?.value ?? surfaces[0].value, show: (x) => x.against === "surface" },
        { key: "level", label: "Уровень Z, м", type: "number", value: 0, show: (x) => x.against === "level" },
        { key: "polygon", label: "Ограничить полигоном слоя", type: "select", options: [...none, ...closedLayers()], value: "" },
      ],
      check: (x) => (x.against === "surface" && x.top === x.bottom ? "Выберите две разные поверхности" : null),
    });
    if (!v) return undefined;
    const polygon = v.polygon ? { layer_id: num(v.polygon) } : null;
    return run("volume_between", v.against === "level" ? { top: num(v.top), level: v.level, polygon } : { top: num(v.top), bottom: num(v.bottom), polygon });
  }

  async function rebuild(id: number): Promise<string> {
    const job = await rebuildWireframe(id);
    ctx.onJobStarted(job);
    return `Перестроение запущено: ${job.title}`;
  }

  async function showSelfIntersections(id?: number): Promise<string | undefined> {
    const wid = id ?? selectedId("wireframe");
    const w = wid !== null ? wf(wid) : undefined;
    if (!w) throw new Error("Выберите каркас");
    const r = await getSelfIntersections(w.id);
    if (!r.triangles.length) {
      ctx.mark("intersections", null);
      return `У каркаса «${w.name}» самопересечений нет`;
    }
    const corners = r.corners.map((c, i) => c + r.origin[i % 3]);
    ctx.mark("intersections", corners, "#ff3b30");
    return `«${w.name}»: пересекающихся треугольников ${r.triangles.length} — отмечены красным. Исправьте их ручной правкой треугольников`;
  }

  // ---- manual triangle edit
  function startTriangles(id?: number): string {
    const wid = id ?? selectedId("wireframe");
    const w = wid !== null ? wf(wid) : undefined;
    if (!w) throw new Error("Выберите каркас для правки треугольников");
    ctx.setTriangles({ wireframeId: w.id, name: w.name, mode: "remove", remove: new Map(), add: [], pending: [] });
    return `Правка треугольников «${w.name}»: щёлкайте по треугольникам, чтобы отметить их к удалению`;
  }
  function markTriangles(t: TriangleEdit | null): void {
    ctx.mark("tri-remove", t && t.remove.size ? [...t.remove.values()].flat(2) : null, "#ff3b30");
    ctx.mark("tri-add", t && t.add.length ? t.add.flat(2) : null, "#34c759");
    ctx.mark("tri-pending", t && t.pending.length ? [...t.pending, ...Array(3 - t.pending.length).fill(t.pending[t.pending.length - 1])].flat() : null, "#ffcc00");
  }
  /** A click on the wireframe while triangles are being edited. */
  function triangleClick(result: PickResult): boolean {
    const t = ctx.triangles();
    if (!t || !result.hit || result.id !== t.wireframeId) return false;
    const hit = result.hit;
    const next: TriangleEdit = { ...t, remove: new Map(t.remove), add: [...t.add], pending: [...t.pending] };
    if (t.mode === "remove") {
      if (next.remove.has(hit.triangle)) next.remove.delete(hit.triangle);
      else next.remove.set(hit.triangle, hit.vertices);
    } else {
      // The corner of the hit triangle nearest to the click: new triangles use existing vertices.
      const d = hit.vertices.map((c) => Math.hypot(c[0] - hit.point[0], c[1] - hit.point[1], c[2] - hit.point[2]));
      const corner = hit.vertices[d.indexOf(Math.min(...d))];
      if (!next.pending.some((p) => p.every((x, i) => x === corner[i]))) next.pending.push(corner);
      if (next.pending.length === 3) {
        next.add.push(next.pending);
        next.pending = [];
      }
    }
    ctx.setTriangles(next);
    markTriangles(next);
    return true;
  }
  function setTriangleMode(mode: "remove" | "add"): void {
    const t = ctx.triangles();
    if (t) ctx.setTriangles({ ...t, mode, pending: [] });
  }
  async function applyTriangles(apply: boolean): Promise<string | undefined> {
    const t = ctx.triangles();
    ctx.setTriangles(null);
    markTriangles(null);
    if (!t || !apply) return undefined;
    if (!t.remove.size && !t.add.length) return "Правка пуста";
    return run("edit_triangles", { wireframe_id: t.wireframeId, remove: [...t.remove.keys()], add: t.add });
  }

  /** A finished operation: show the object, tell about warnings; a volume report as a dialog. */
  function jobDone(job: JobInfo): void {
    const r = job.result;
    if (!r || job.kind !== "geometry.operation") return;
    for (const id of r.wireframe_ids ?? []) ctx.show("wireframe", id);
    for (const id of r.layer_ids ?? []) ctx.show("lines", id);
    if (r.wireframe_ids?.length) ctx.select({ kind: "wireframe", id: r.wireframe_ids[0] });
    else if (r.layer_ids?.length) ctx.select({ kind: "lines", id: r.layer_ids[0] });
    const rep = (r.report ?? {}) as Record<string, unknown>;
    const lines: string[] = [];
    if (r.op === "volume_between") {
      lines.push(`Выемка: ${fmt(rep.cut)} м³`, `Насыпь: ${fmt(rep.fill)} м³`, `Разность: ${fmt(rep.net)} м³`, `Площадь общего контура: ${fmt(rep.area)} м²`);
    } else if (r.op === "boolean") {
      lines.push(`Объём A: ${fmt(rep.volume_a ?? (rep.volumes as number[] | undefined)?.[0])} м³`, `Объём B: ${fmt(rep.volume_b ?? (rep.volumes as number[] | undefined)?.[1])} м³`, `Объём результата: ${fmt(rep.volume)} м³`);
    } else if (r.op === "simplify" || r.op === "smooth") {
      lines.push(`Объём: ${fmt(rep.volume_before)} → ${fmt(rep.volume_after)} м³ (${fmt(rep.volume_change_percent, 4)} %)`, `Наибольшее отклонение: ${fmt(rep.max_deviation, 4)} м`);
      if (rep.triangles_after !== undefined) lines.push(`Треугольников: ${fmt(rep.triangles_before, 0)} → ${fmt(rep.triangles_after, 0)}`);
    } else if (r.op === "repair") {
      const before = (rep.before ?? {}) as Record<string, unknown>;
      const after = (rep.after ?? {}) as Record<string, unknown>;
      const row = (title: string, key: string) => `${title}: ${String(before[key] ?? "—")} → ${String(after[key] ?? "—")}`;
      lines.push(`Замкнут: ${before.closed ? "да" : "нет"} → ${after.closed ? "да" : "нет"}`, row("Открытых рёбер", "open_edges"), row("Рёбер с 3+ треугольниками", "nonmanifold_edges"),
        row("Вырожденных треугольников", "degenerate"), row("Повторных треугольников", "duplicate"), row("Рёбер с несогласованными нормалями", "orientation_conflicts"),
        row("Самопересечений", "self_intersections"), `Объём: ${fmt(before.volume)} → ${fmt(after.volume)} м³`);
    } else if (r.op?.startsWith("clip") && rep.volume !== undefined) {
      lines.push(`Объём исходного тела: ${fmt(rep.volume)} м³`);
    }
    const warnings = r.warnings ?? [];
    if (lines.length || warnings.length) {
      void showMessage({ title: r.title ?? job.title, message: [...lines, ...warnings.map((w) => `⚠ ${w}`)].join("\n") });
    }
  }

  return {
    newLayer, startEdit, save, leave, versions, moveSelected, exportLines, exportWireframes, run,
    stitch, extrude, repair, simplify, smooth, split, merge, boolean, clip, intersectionLines, dtm, contours, volume, rebuild,
    showSelfIntersections, startTriangles, triangleClick, setTriangleMode, applyTriangles, jobDone,
    /** Adds a line to the edited layer from coordinates (commands, Claude). */
    addLine(points: Vec3[], closed: boolean): string {
      const session = ctx.edit();
      if (!session) throw new Error("Режим правки не включён: сначала lines.edit");
      session.addLine(points, closed);
      return `Добавлена ${closed ? "замкнутая " : ""}линия из ${points.length} вершин (не сохранено)`;
    },
  };
}

export type Modeling = ReturnType<typeof createModeling>;
