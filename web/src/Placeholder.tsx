import type { Summary } from "./api";

const TEXT: Record<string, { title: string; text: string }> = {
  plan: {
    title: "План",
    text: "Карта устьев скважин в плане с подписями и раскраской по статусу и результатам аудита.",
  },
  section: {
    title: "Разрез",
    text: "Вертикальный разрез по профилю: стволы скважин, интервалы и содержания вдоль ствола.",
  },
  view3d: {
    title: "3D",
    text: "Трёхмерные стволы по минимальной кривизне с раскраской интервалов по содержаниям.",
  },
};

export function Placeholder({ view, summary, selectedHole }: { view: string; summary: Summary | null; selectedHole: string | null }) {
  const info = TEXT[view];
  return (
    <main className="page">
      <h1>{info.title}</h1>
      <div className="placeholder">
        <p>{info.text}</p>
        <p className="muted">Раздел появится на этапе «Визуализация». Данные для него уже загружены:</p>
        {summary && (
          <ul>
            <li>устья: {summary.counts.collar.toLocaleString("ru-RU")}</li>
            <li>замеры инклинометрии: {summary.counts.survey.toLocaleString("ru-RU")}</li>
            <li>интервалы проб: {summary.counts.assay.toLocaleString("ru-RU")}</li>
          </ul>
        )}
        {selectedHole && <p>Выбранная скважина <strong>{selectedHole}</strong> будет подсвечена на графике.</p>}
      </div>
    </main>
  );
}
