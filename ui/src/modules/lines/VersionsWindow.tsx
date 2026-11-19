/**
 * «Версии» of a line layer (ТЗ 7.22): the journal of saves — version, when, who, the action, lines and vertices
 * «было → стало», the note; «Вернуть» makes a new version with the geometry of that one (`lines.restore`,
 * asked in an own dialog). The journal is read again after every change of the layer.
 */
import { useEffect, useState } from "react";
import { useApp } from "../../shell/AppContext";
import { errorText } from "../../shell/props";
import { useStore } from "../../shell/useStore";
import css from "../../shell/PropsParts.module.css";
import { lineLayers, sceneObjects } from "../scene/objects";
import { getVersions, type Journal } from "./editApi";
import { versionLines } from "./versions";

export function VersionsWindow({ layerId }: { layerId: number }) {
  const app = useApp();
  const layer = useStore(sceneObjects, (o) => lineLayers(o).find((l) => l.id === layerId) ?? null);
  const [journal, setJournal] = useState<Journal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    getVersions(layerId).then(
      (j) => live && (setJournal(j), setError(null)),
      (e: unknown) => live && setError(errorText(e)),
    );
    return () => {
      live = false;
    };
  }, [layerId, layer?.version]);
  const restore = async (version: number) => {
    setBusy(true);
    const r = await app.registry.execute("lines.restore", { layer_id: layerId, version });
    setBusy(false);
    if (!r.ok) setError(r.result);
    else setJournal(await getVersions(layerId).catch(() => journal));
  };
  const rows = journal ? versionLines(journal) : [];
  return (
    <section className={css.panel} data-testid="lines-versions" aria-label={`Версии слоя ${layer?.name ?? layerId}`}>
      <p className={css.note}>
        {layer ? `Слой «${layer.name}»` : `Слой №${layerId}`}
        {journal ? ` · текущая версия ${journal.version}` : ""}. Возврат создаёт новую версию с геометрией выбранной; журнал не стирается.
      </p>
      {error && (
        <p className={css.error} role="alert">
          {error}
        </p>
      )}
      {!journal && !error && <p className={css.note}>Загрузка…</p>}
      {journal && (
        <div className={css.tableWrap} style={{ maxHeight: "none" }}>
          <table className={css.table}>
            <thead>
              <tr>
                <th scope="col">Версия</th>
                <th scope="col">Когда</th>
                <th scope="col">Кто</th>
                <th scope="col">Действие</th>
                <th scope="col">Линии</th>
                <th scope="col">Вершины</th>
                <th scope="col">Заметка</th>
                <th scope="col" aria-label="Возврат" />
              </tr>
            </thead>
            <tbody>
              {rows.map((v) => (
                <tr key={v.version} className={v.current ? css.picked : undefined} data-testid={`version-row-${v.version}`}>
                  <td className={css.rowNo}>{v.version}</td>
                  <td>{v.when}</td>
                  <td>{v.who}</td>
                  <td>{v.action}</td>
                  <td>{v.lines}</td>
                  <td>{v.vertices}</td>
                  <td title={v.note}>{v.note}</td>
                  <td>
                    {v.current ? (
                      <span className={css.note}>текущая</span>
                    ) : (
                      <button type="button" className={css.btn} data-testid={`version-restore-${v.version}`} disabled={busy} title={`Создать версию ${journal.version + 1} с геометрией версии ${v.version}`} onClick={() => void restore(v.version)}>
                        Вернуть
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
