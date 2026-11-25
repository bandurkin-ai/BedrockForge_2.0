/**
 * The command `estimate.ellipsoid {id?, show?, pass?, block?}` (ТЗ 8.8.4; docs/progress/e18-api.md §12) and the layers of the preview in the main 3D
 * (`preview.ts`, `scene.ts`, a lazy chunk with three.js attached to every scene). Without `id` it is the form of the wizard: the ellipsoid of the
 * pass (`pass`; by default the one that gave the estimate) at the block of the last «Показать» of step 6, followed live while step 4 is edited;
 * with `id` it is a stored row: the core estimates the block (`block` — «i; j; k» of the parent; by default the block picked in 3D, else the block
 * of the wizard when it holds this row) and the answer stays in 3D until `show: false`. `show: false` takes the layers away (without `id` the
 * layer of the wizard is switched off until it is asked for again). `bindEstimation` (`bind.ts`) binds it with the rest.
 */
import type { App } from "../../shell/app";
import { needProject, noted, refusal } from "../../shell/objectRun";
import { pickedBlockStore } from "../blocks/display";
import { blockById, blocksStore } from "../blocks/state";
import { parseTriple } from "./inspect/bind";
import { projectStore } from "../projects/state";
import { currentScene, onScene } from "../scene/sceneHost";
import { refusalOf } from "./api";
import { previewSceneStore, resetPreviewScene, shownText, viewOf, type PreviewView } from "./preview";
import { loadRowPreview, type PreviewAt } from "./previewApi";
import { formWindowOpen } from "./previewWindows";
import { estimationStore, rowById } from "./state";
import { parseBlock, wizardStore } from "./wizardState";

const NOT_READY = () => refusal("scene_not_ready", "3D-окно ещё не готово: откройте проект и дождитесь первого кадра.", "Когда в 3D-окне появится сетка, повторите.");

/** The block of a stored row: the one named, else the one picked in 3D (of the model of the row), else the one of the wizard that holds this row. */
export function blockOf(a: Record<string, unknown>, id: number): PreviewAt {
  if (a.block !== undefined && a.block !== null && a.block !== "") {
    const t = parseTriple(a.block, true);
    if (!t || t.some((v) => v < 0)) throw refusal("bad_param", "Ячейка блока — три целых неотрицательных числа i; j; k.", "Индексы родителя с нуля, например 4; 4; 7.");
    return { ijk: t };
  }
  const picked = pickedBlockStore.get().picked;
  const model = rowById(id)?.model_id;
  if (picked?.status === "found" && picked.answer?.found && picked.model === model) return { row: picked.answer.row };
  const w = wizardStore.get();
  if (w.editing?.id === id) {
    const ijk = parseBlock(w.preview.at);
    if (Array.isArray(ijk)) return { ijk };
  }
  throw refusal("no_block", "Не выбран блок: эллипсоид ставится в центр блока.", "Щёлкните по блоку в 3D или передайте block, например 4; 4; 7.");
}

const currentView = (): PreviewView | null => viewOf(previewSceneStore.get(), wizardStore.get(), formWindowOpen());

export async function runEllipsoid(a: Record<string, unknown>): Promise<string> {
  const id = typeof a.id === "number" ? a.id : null;
  if (a.pass !== undefined && (typeof a.pass !== "number" || !Number.isInteger(a.pass) || a.pass < 1 || a.pass > 5)) throw refusal("bad_param", "Проход — целое число от 1 до 5.", "Проходы мастера — шаг «Поиск и проходы».");
  if (a.show === false) {
    const s = previewSceneStore.get();
    const had = s.row !== null || (id === null && currentView() !== null);
    previewSceneStore.set(id === null ? { row: null, wizard: false } : { row: s.row?.id === id ? null : s.row });
    return had ? "Эллипсоид поиска убран из 3D" : "Эллипсоид поиска в 3D не показывался";
  }
  if (!currentScene()) throw NOT_READY();
  if (id === null) {
    if (!formWindowOpen()) throw refusal("no_wizard", "Мастер «Оценка содержаний» не открыт: показывать нечего.", "Откройте мастер или передайте id строки «Параметры оценки».");
    if (!wizardStore.get().preview.data) throw refusal("no_preview", "Блок ещё не показан: эллипсоид встаёт в центр блока предпросмотра.", "На шаге «Предпросмотр и запуск» введите ячейку i; j; k и нажмите «Показать».");
    previewSceneStore.set((s) => ({ row: null, wizard: true, pass: typeof a.pass === "number" ? a.pass : s.pass, reveal: s.reveal + 1 }));
    const v = currentView();
    if (!v) throw refusal("no_preview", "У блока предпросмотра нет центра: показывать нечего.", "Покажите блок заново.");
    return shownText(v);
  }
  const row = rowById(id);
  if (!row && estimationStore.get().loaded) throw refusal("estimation_not_found", `Строка «Параметры оценки» №${id} не найдена.`, "Номера строк — в разделе «Геостатистика → Параметры оценки» дерева.");
  if (row && !blockById(row.model_id) && blocksStore.get().loaded) throw refusal("block_model_not_found", `Блочная модель №${row.model_id} не найдена.`, "Номера моделей — в разделе «Блочные модели» дерева.");
  const at = blockOf(a, id);
  const uid = projectStore.get().current?.uid ?? null;
  let answer;
  try {
    answer = await loadRowPreview(id, at);
  } catch (e) {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  }
  if ((projectStore.get().current?.uid ?? null) !== uid) return "Проект изменился: эллипсоид не показан";
  previewSceneStore.set((s) => ({ row: { id, name: row?.name ?? `№${id}`, answer }, reveal: s.reveal + 1 }));
  const v = currentView();
  if (!v) throw refusal("no_block_centre", "Ядро не назвало центр блока: показывать нечего.", "Выберите другой блок.");
  return `Строка «${row?.name ?? `№${id}`}»: ${shownText(v)}`;
}

/** The binding with the registry, the project and the scene; the functions that take it off. */
export function bindPreview(app: App): (() => void)[] {
  app.registry.bind("estimate.ellipsoid", noted(app, (a) => runEllipsoid(a)), needProject);
  let uid: string | null = projectStore.get().current?.uid ?? null;
  let generation = 0;
  let detach: (() => void) | null = null;
  return [
    // what was asked of one project does not belong to another
    projectStore.subscribe(() => {
      const cur = projectStore.get().current?.uid ?? null;
      if (cur === uid) return;
      uid = cur;
      resetPreviewScene();
    }),
    // the layers in the main 3D: a lazy chunk with three.js, attached to every new scene
    onScene((scene) => {
      const mine = ++generation;
      detach?.();
      detach = null;
      if (!scene) return;
      void import("./scene").then((m) => {
        if (mine === generation) detach = m.attachPreviewScene(scene);
      });
    }),
    () => {
      generation++;
      detach?.();
      detach = null;
    },
  ];
}
