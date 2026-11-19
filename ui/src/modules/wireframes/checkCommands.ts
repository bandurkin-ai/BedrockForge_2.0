/**
 * The checks of a wireframe as registry commands (ТЗ 7.21 «Проверить»; ADR 0013 §6): `wireframe.check` starts
 * the job «Проверка и копии для показа» of the selected wireframe (or `wireframe_id`) — once: a wireframe that
 * is being checked is not queued again; `model.self_intersections` shows what the check found — the wireframe
 * is made visible and selected with «Свойства», its crossing triangles are red in the 3D window — and checks
 * a wireframe that was not checked yet. The data of the wireframe do not change, so neither asks to confirm;
 * «Проверить» writes its results, so it is unavailable in a project open only for reading.
 */
import type { App } from "../../shell/app";
import { needProject, needWritable, noted, refusal } from "../../shell/objectRun";
import { selectWithProperties } from "../../shell/treeRun";
import { jobsStore, refresh as refreshJobs } from "../jobs/state";
import { loadSceneObjects, sceneObjects } from "../scene/objects";
import { activeCheck, checkSummary, doneChecks, startCheck } from "./checks";
import { wireframeOf } from "./named";
import type { WireframeInfo } from "./state";
import { wireframeKey } from "./tree";

export function bindCheckCommands(app: App): void {
  const r = app.registry;

  /** Starts the job unless one is checking the wireframe already. */
  const check = async (w: WireframeInfo): Promise<string> => {
    if (!(w.triangle_count > 0)) throw refusal("no_triangles", `В каркасе «${w.name}» нет треугольников: проверять нечего.`, "Выберите в дереве каркас с треугольниками.");
    let job = activeCheck(jobsStore.get().jobs, w.id);
    if (!job) {
      await refreshJobs(); // a job started elsewhere (an import, another window) may not be in the list yet
      job = activeCheck(jobsStore.get().jobs, w.id);
    }
    if (job) return `«${w.name}» уже проверяется: задание №${job.id}. Итог появится в «Свойствах»`;
    const started = await startCheck(w.id);
    void refreshJobs();
    return `Проверка «${w.name}» запущена: задание №${started.id}. Итог появится в «Свойствах» каркаса, пересекающиеся треугольники — красным в 3D-окне`;
  };

  r.bind(
    "wireframe.check",
    noted(app, async (a) => {
      if (!sceneObjects.get().loaded) await loadSceneObjects();
      return check(wireframeOf(app, a));
    }),
    needWritable(app), // the job writes the results of the checks into the project
  );

  r.bind(
    "model.self_intersections",
    noted(app, async (a) => {
      if (!sceneObjects.get().loaded) await loadSceneObjects();
      const w = wireframeOf(app, a);
      await selectWithProperties(app, "wireframe", w.id);
      if (!doneChecks(w.stats)) return check(w);
      // the red triangles follow their wireframe: a hidden one is shown
      const hidden = app.store.get().view.hidden;
      if (hidden.includes(wireframeKey(w.id)) && r.reason("layer.show") === null) await app.run("layer.show", { layer: "wireframe", id: w.id, visible: true });
      return checkSummary(w.name, w.stats);
    }),
    needProject,
  );
}
